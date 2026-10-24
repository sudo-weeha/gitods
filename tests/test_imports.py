"""ZIP 导入测试：校验边界、两步发布、选项切换、并发与状态机。"""
import io
import stat
import threading
import zipfile
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from catalog.models import Entry, ImportDraft
from catalog.services.branches import create_branch
from catalog.services.exceptions import (
    CapacityExceeded,
    NotFound,
    RevisionConflict,
    ValidationError,
)
from catalog.services.imports import ImportBusy
from catalog.services.imports import (
    cancel_import,
    prepare_zip_import,
    preview_entries,
    publish_import,
    set_import_root_option,
)
from catalog.services.storage import read_object_bytes
from tests.factories import add_dir, add_file

pytestmark = pytest.mark.django_db


def make_zip(files: dict, dirs=(), compression=zipfile.ZIP_DEFLATED) -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression) as archive:
        for name in dirs:
            archive.writestr(zipfile.ZipInfo(name + "/"), "")
        for name, data in files.items():
            if not isinstance(data, bytes):
                data = data.encode("utf-8")
            archive.writestr(name, data)
    return payload.getvalue()


def upload(zip_bytes) -> SimpleUploadedFile:
    return SimpleUploadedFile("upload.zip", zip_bytes, content_type="application/zip")


def _prepare(branch, owner, zip_bytes, revision=None, strip_root=True):
    from catalog.models import Branch as BranchModel

    if revision is None:
        revision = BranchModel.objects.get(pk=branch.pk).revision
    return prepare_zip_import(
        project_id=branch.project_id,
        branch_id=branch.pk,
        expected_revision=revision,
        owner=owner,
        uploaded_file=upload(zip_bytes),
        strip_root=strip_root,
    )


@pytest.fixture
def owner(db, django_user_model):
    return django_user_model.objects.create_superuser("import-admin", password="Import-Only-42")


class TestPrepareValidation:
    def test_normal_zip_draft_ready(self, branch, owner):
        draft = _prepare(branch, owner, make_zip({"README.md": "# t", "src/app.py": "x=1"}, dirs=["docs"]))
        assert draft.status == ImportDraft.STATUS_READY
        assert draft.file_count == 2
        assert draft.total_size > 0
        assert draft.expected_revision == branch.revision
        # 未写公开分支
        assert branch.entries.count() == 0

    def test_outer_dir_detected_and_stripped(self, branch, owner):
        draft = _prepare(branch, owner, make_zip({"proj/README.md": "hi", "proj/src/a.py": "1"}))
        paths = {e["path"] for e in preview_entries(draft)}
        assert paths == {"README.md", "src", "src/a.py"}
        assert draft.strip_root is True

    def test_no_common_root_keeps_paths(self, branch, owner):
        draft = _prepare(branch, owner, make_zip({"README.md": "hi", "other/x.txt": "1"}))
        assert draft.strip_root is False
        paths = {e["path"] for e in preview_entries(draft)}
        assert "other/x.txt" in paths

    def test_zip_traversal_never_changes_published_branch(self, project, branch, owner):
        before = list(Entry.objects.filter(branch=branch).values_list("path", "blob_id"))
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("../outside.txt", "blocked")
        with pytest.raises(ValidationError):
            prepare_zip_import(
                project_id=project.id, branch_id=branch.id, owner=owner,
                expected_revision=branch.revision, uploaded_file=upload(payload.getvalue()),
            )
        assert list(Entry.objects.filter(branch=branch).values_list("path", "blob_id")) == before

    def test_symlink_rejected(self, branch, owner):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
            info = zipfile.ZipInfo("link.txt")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "/etc/passwd")
        with pytest.raises(ValidationError, match="符号链接"):
            _prepare(branch, owner, payload.getvalue())

    @pytest.mark.filterwarnings("ignore:Duplicate name:UserWarning")
    def test_duplicate_path_rejected(self, branch, owner):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("a.txt", "one")
            archive.writestr("a.txt", "two")  # 第二个同名条目
        with pytest.raises(ValidationError, match="重复路径"):
            _prepare(branch, owner, payload.getvalue())

    def test_dir_file_conflict_rejected(self, branch, owner):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(zipfile.ZipInfo("thing/"), "")
            archive.writestr("thing", "as file")
        with pytest.raises(ValidationError, match="文件和目录"):
            _prepare(branch, owner, payload.getvalue())

    def test_corrupted_crc_rejected(self, branch, owner):
        payload = bytearray(make_zip({"blob.bin": b"A" * 64}, compression=zipfile.ZIP_STORED))
        # 局部头 30 字节 + 文件名后即数据；翻转一个字节破坏内容/CRC
        with zipfile.ZipFile(io.BytesIO(bytes(payload))) as probe:
            info = probe.infolist()[0]
            data_offset = info.header_offset + 30 + len(info.filename.encode())
        payload[data_offset + 5] ^= 0xFF
        with pytest.raises(ValidationError):
            _prepare(branch, owner, bytes(payload))

    def test_encrypted_flag_rejected(self, branch, owner):
        payload = bytearray(make_zip({"secret.txt": "s"}))
        # 置位中央目录 general purpose bit 0（加密标志）
        idx = payload.find(b"PK\x01\x02")
        assert idx != -1
        payload[idx + 8] |= 0x01
        with pytest.raises(ValidationError, match="加密"):
            _prepare(branch, owner, bytes(payload))

    def test_env_rejected_but_example_allowed(self, branch, owner):
        with pytest.raises(ValidationError, match=".env"):
            _prepare(branch, owner, make_zip({".env": "SECRET=1"}))
        draft = _prepare(branch, owner, make_zip({".env.example": "SECRET="}))
        paths = {e["path"] for e in preview_entries(draft)}
        assert ".env.example" in paths

    def test_exclusions_listed_not_imported(self, branch, owner):
        draft = _prepare(branch, owner, make_zip({
            ".git/config": "[core]", ".DS_Store": "junk", "__MACOSX/._a": "meta", "README.md": "ok",
        }))
        from catalog.services.imports import draft_manifest

        manifest = draft_manifest(draft)
        excluded = {x["path"] for x in manifest["excluded"]}
        assert {".git/config", ".DS_Store", "__MACOSX/._a"} <= excluded
        assert {e["path"] for e in preview_entries(draft)} == {"README.md"}

    def test_parent_dirs_filled(self, branch, owner):
        draft = _prepare(branch, owner, make_zip({"a/b/c.txt": "deep"}), strip_root=False)
        kinds = {e["path"]: e["kind"] for e in preview_entries(draft)}
        assert kinds == {"a": "dir", "a/b": "dir", "a/b/c.txt": "file"}

    def test_uncompressed_limit(self, branch, owner, settings):
        settings.GITODS_LIMITS = {
            **settings.GITODS_LIMITS, "MAX_ZIP_UNCOMPRESSED_BYTES": 8
        }
        with pytest.raises(CapacityExceeded):
            _prepare(branch, owner, make_zip({"big.txt": "x" * 32}))

    def test_stale_revision_rejected_before_parse(self, branch, owner):
        with pytest.raises(RevisionConflict):
            _prepare(branch, owner, make_zip({"a": "1"}), revision=branch.revision + 9)

    def test_bad_zip_rejected(self, branch, owner):
        with pytest.raises(ValidationError):
            _prepare(branch, owner, b"not a zip at all")

    def test_busy_lock_returns_import_busy(self, branch, owner, settings):
        import fcntl
        import os
        from pathlib import Path

        lock_path = Path(settings.DATA_ROOT) / "locks" / "import.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            with pytest.raises(ImportBusy):
                _prepare(branch, owner, make_zip({"a": "1"}))
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


class TestPublishFlow:
    def test_publish_replaces_whole_branch(self, project, branch, owner):
        add_dir(branch, "old")
        old_entry = add_file(branch, "old/gone.txt", b"gone")
        add_file(branch, "README.md", b"old readme")

        draft = _prepare(branch, owner, make_zip(
            {"README.md": "new readme", "docs/architecture.md": "# architecture", "img/logo.bin": b"\x00\x01"},
        ))
        result = publish_import(draft_id=draft.pk, owner=owner)

        assert result.revision == branch.revision + 1
        paths = {e.path for e in result.entries.all()}
        assert paths == {"README.md", "docs", "docs/architecture.md", "img", "img/logo.bin"}
        entry = result.entries.get(path="README.md")
        assert read_object_bytes(entry.blob.sha256) == b"new readme"
        assert not Entry.objects.filter(pk=old_entry.pk).exists()

        draft.refresh_from_db()
        assert draft.status == ImportDraft.STATUS_PUBLISHED

    def test_publish_without_strip_keeps_outer(self, branch, owner):
        draft = _prepare(branch, owner, make_zip({"proj/README.md": "hi"}))
        set_import_root_option(draft_id=draft.pk, owner=owner, strip_root=False)
        result = publish_import(draft_id=draft.pk, owner=owner)
        assert {e.path for e in result.entries.all()} == {"proj", "proj/README.md"}

    def test_publish_after_branch_changed_cancels_draft(self, branch, owner):
        draft = _prepare(branch, owner, make_zip({"new.txt": "x"}))
        # 草稿创建后分支被更新
        from catalog.services.entries import upload_files

        upload_files(branch_id=branch.pk, expected_revision=branch.revision,
                     parent_path="", files=[SimpleUploadedFile("bump.txt", b"b")])
        with pytest.raises(RevisionConflict):
            publish_import(draft_id=draft.pk, owner=owner)
        draft.refresh_from_db()
        assert draft.status == ImportDraft.STATUS_CANCELLED
        # 分支保持导入前（更新后）的内容，未应用导入
        branch.refresh_from_db()
        assert {e.path for e in branch.entries.all()} == {"bump.txt"}

    def test_double_publish_rejected(self, branch, owner):
        draft = _prepare(branch, owner, make_zip({"a.txt": "1"}))
        publish_import(draft_id=draft.pk, owner=owner)
        with pytest.raises(ValidationError, match="重复"):
            publish_import(draft_id=draft.pk, owner=owner)

    def test_expired_draft_rejected_and_marked(self, branch, owner):
        draft = _prepare(branch, owner, make_zip({"a.txt": "1"}))
        ImportDraft.objects.filter(pk=draft.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        with pytest.raises(ValidationError, match="过期"):
            publish_import(draft_id=draft.pk, owner=owner)
        draft.refresh_from_db()
        assert draft.status == ImportDraft.STATUS_EXPIRED

    def test_cancel_keeps_branch_and_cleans_tmp(self, branch, owner, settings):
        from pathlib import Path

        draft = _prepare(branch, owner, make_zip({"a.txt": "1"}))
        tmp_dir = Path(draft.manifest_path).parent
        assert tmp_dir.exists()
        cancel_import(draft_id=draft.pk, owner=owner)
        draft.refresh_from_db()
        assert draft.status == ImportDraft.STATUS_CANCELLED
        assert branch.entries.count() == 0
        assert not tmp_dir.exists()
        # 幂等
        cancel_import(draft_id=draft.pk, owner=owner)

    def test_other_owner_rejected(self, branch, owner, django_user_model):
        other = django_user_model.objects.create_superuser("other-admin", password="Other-Admin-42")
        draft = _prepare(branch, owner, make_zip({"a.txt": "1"}))
        with pytest.raises(NotFound):
            set_import_root_option(draft_id=draft.pk, owner=other, strip_root=False)
        with pytest.raises(NotFound):
            publish_import(draft_id=draft.pk, owner=other)
        with pytest.raises(NotFound):
            cancel_import(draft_id=draft.pk, owner=other)
        assert draft.status == ImportDraft.STATUS_READY  # 未被他人影响

    def test_option_keeps_revision_and_no_publish(self, branch, owner):
        draft = _prepare(branch, owner, make_zip({"proj/a.txt": "1"}))
        before = draft.expected_revision
        set_import_root_option(draft_id=draft.pk, owner=owner, strip_root=False)
        draft.refresh_from_db()
        assert draft.expected_revision == before
        assert branch.entries.count() == 0
        paths = {e["path"] for e in preview_entries(draft)}
        assert paths == {"proj", "proj/a.txt"}

    def test_option_without_common_root_rejected(self, branch, owner):
        draft = _prepare(branch, owner, make_zip({"a.txt": "1", "b/c.txt": "2"}))
        with pytest.raises(ValidationError, match="没有共同外层目录"):
            set_import_root_option(draft_id=draft.pk, owner=owner, strip_root=True)


@pytest.mark.django_db(transaction=True)
class TestConcurrency:
    def test_same_revision_double_publish_only_one_wins(self, project, branch, owner):
        draft = _prepare(branch, owner, make_zip({"a.txt": "1", "b.txt": "2"}))
        from django.db import connections

        results = []

        def worker():
            try:
                publish_import(draft_id=draft.pk, owner=owner)
                results.append("ok")
            except Exception as exc:  # noqa: BLE001
                results.append(type(exc).__name__)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results.count("ok") == 1
        branch.refresh_from_db()
        assert branch.revision == draft.expected_revision + 1
        assert branch.entries.count() == 2  # 只应用一次

    def test_publish_and_delete_branch_no_deadlock(self, project, branch, owner):
        # 另一个分支被删除时并发发布导入：锁顺序一致，不死锁
        other = create_branch(project_id=project.id, name="victim")
        from catalog.services.branches import set_default_branch

        set_default_branch(project_id=project.id, branch_id=other.id)
        draft = _prepare(other, owner, make_zip({"x.txt": "1"}))
        errors = []

        def do_publish():
            try:
                publish_import(draft_id=draft.pk, owner=owner)
            except Exception as exc:  # noqa: BLE001
                errors.append(("publish", type(exc).__name__))

        def do_delete():
            try:
                from catalog.services.branches import delete_branch

                # 删除 main（非默认）：与发布无共享行，只验证锁顺序不互相饿死
                delete_branch(project_id=project.id, branch_id=branch.id)
            except Exception as exc:  # noqa: BLE001
                errors.append(("delete", type(exc).__name__))

        from django.db import connections

        def run(target):
            try:
                target()
            finally:
                connections.close_all()

        threads = [
            threading.Thread(target=run, args=(do_publish,)),
            threading.Thread(target=run, args=(do_delete,)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not any(t.is_alive() for t in threads), "出现死锁/挂起"
        # 两个操作都正常返回（成功或业务性失败），没有死锁异常
        assert not [e for e in errors if "Deadlock" in e[1] or "Timeout" in e[1]]


class TestImportViews:
    def _home(self, project, branch):
        return f"/admin/catalog/project/{project.pk}/imports/?branch={branch.pk}"

    def test_home_requires_staff(self, client, project, branch):
        response = client.get(self._home(project, branch))
        assert response.status_code == 302

    def test_upload_and_publish_via_views(self, admin_client, project, branch, owner):
        url = f"/admin/catalog/project/{project.pk}/imports/new/?branch={branch.pk}"
        response = admin_client.post(url, {
            "branch": str(branch.pk), "expected_revision": str(branch.revision),
            "zip": upload(make_zip({"README.md": "via view"})),
        })
        assert response.status_code == 302
        draft = ImportDraft.objects.get(project=project)
        preview = admin_client.get(f"/admin/catalog/project/{project.pk}/imports/{draft.pk}/")
        assert preview.status_code == 200
        assert "替换范围" in preview.content.decode()

        branch.refresh_from_db()
        publish = admin_client.post(
            f"/admin/catalog/project/{project.pk}/imports/{draft.pk}/publish/"
        )
        assert publish.status_code == 302
        assert branch.entries.filter(path="README.md").exists()

    def test_busy_maps_to_429(self, admin_client, project, branch, owner, settings):
        import fcntl
        import os
        from pathlib import Path

        lock_path = Path(settings.DATA_ROOT) / "locks" / "import.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            response = admin_client.post(
                f"/admin/catalog/project/{project.pk}/imports/new/?branch={branch.pk}",
                {"branch": str(branch.pk), "expected_revision": str(branch.revision),
                 "zip": upload(make_zip({"a": "1"}))},
            )
            assert response.status_code == 429
            assert "稍后重试" in response.content.decode()
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def test_capacity_maps_to_413(self, admin_client, project, branch, owner, settings):
        settings.GITODS_LIMITS = {**settings.GITODS_LIMITS, "MAX_ZIP_UNCOMPRESSED_BYTES": 4}
        response = admin_client.post(
            f"/admin/catalog/project/{project.pk}/imports/new/?branch={branch.pk}",
            {"branch": str(branch.pk), "expected_revision": str(branch.revision),
             "zip": upload(make_zip({"big.txt": "x" * 16}))},
        )
        assert response.status_code == 413

    def test_bad_zip_maps_to_400(self, admin_client, project, branch):
        response = admin_client.post(
            f"/admin/catalog/project/{project.pk}/imports/new/?branch={branch.pk}",
            {"branch": str(branch.pk), "expected_revision": str(branch.revision),
             "zip": upload(b"junk")},
        )
        assert response.status_code == 400

    def test_option_via_view(self, admin_client, project, branch, admin_user):
        draft = _prepare(branch, admin_user, make_zip({"proj/a.txt": "1"}))
        url = f"/admin/catalog/project/{project.pk}/imports/{draft.pk}/option/"
        response = admin_client.post(url, {"strip_root": "0"})
        assert response.status_code == 302
        draft.refresh_from_db()
        assert draft.strip_root is False
