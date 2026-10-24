"""目录浏览与 raw 下载测试：自然排序、类型校验、URL 编解码、越权防护。"""
from urllib.parse import quote

import pytest

from catalog.services.branches import create_branch
from tests.factories import add_dir, add_file

pytestmark = pytest.mark.django_db

PROJECT_URL = "/backend/device-platform"


def _url(path, branch, prefix="blob"):
    return f"{PROJECT_URL}/{prefix}/{quote(path, safe='/')}?branch={branch.pk}"


class TestDirectoryBrowsing:
    def test_dirs_first_natural_sort(self, client, project, branch):
        add_dir(branch, "zdir")
        add_dir(branch, "Adir")
        add_file(branch, "a10.py", b"1")
        add_file(branch, "a2.py", b"2")
        add_file(branch, "B.txt", b"3")
        response = client.get(f"{PROJECT_URL}/?branch={branch.pk}")
        html = response.content.decode()
        order = [html.index(n) for n in ["Adir", "zdir", "a2.py", "a10.py", "B.txt"]]
        assert order == sorted(order)

    def test_drill_down_and_parent_row(self, client, project, branch):
        add_dir(branch, "src")
        add_file(branch, "src/app.py", b"x = 1")
        response = client.get(_url("src", branch, prefix="tree"))
        assert response.status_code == 200
        html = response.content.decode()
        assert "app.py" in html
        # 父目录行与面包屑都能回到根
        assert f'href="{PROJECT_URL}/?branch={branch.pk}"' in html

    def test_empty_dir_state(self, client, project, branch):
        add_dir(branch, "empty-dir")
        response = client.get(_url("empty-dir", branch, prefix="tree"))
        assert "此目录为空" in response.content.decode()

    def test_tree_url_to_file_redirects_blob(self, client, project, branch):
        add_file(branch, "note.md", b"x")
        response = client.get(_url("note.md", branch, prefix="tree"))
        assert response.status_code == 302
        assert "/blob/" in response.url

    def test_blob_url_to_dir_redirects_tree(self, client, project, branch):
        add_dir(branch, "assets")
        response = client.get(_url("assets", branch, prefix="blob"))
        assert response.status_code == 302
        assert "/tree/" in response.url

    def test_branch_isolation_in_listing(self, client, project, branch):
        add_file(branch, "README.md", b"main only")
        other = create_branch(project_id=project.id, name="empty-dev")
        html_main = client.get(f"{PROJECT_URL}/?branch={branch.pk}").content.decode()
        html_other = client.get(f"{PROJECT_URL}/?branch={other.pk}").content.decode()
        assert "README.md" in html_main
        assert "README.md" not in html_other


class TestRawDownload:
    def test_text_raw_inline(self, client, project, branch):
        add_file(branch, "src/main.py", b"print('hi')\n")
        response = client.get(_url("src/main.py", branch, prefix="raw"))
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/plain")
        assert b"print" in b"".join(response.streaming_content)
        assert response["X-Content-Type-Options"] == "nosniff"

    def test_binary_raw_attachment(self, client, project, branch):
        add_file(branch, "logo.png", b"\x89PNG fake")
        response = client.get(_url("logo.png", branch, prefix="raw"))
        assert response["Content-Type"] == "application/octet-stream"
        assert "attachment" in response["Content-Disposition"]

    def test_download_param_forces_attachment(self, client, project, branch):
        add_file(branch, "a.py", b"x")
        response = client.get(_url("a.py", branch, prefix="raw") + "&dl=1")
        assert "attachment" in response["Content-Disposition"]

    def test_chinese_filename_disposition(self, client, project, branch):
        add_file(branch, "文档/说明.pdf", b"%PDF-1.4")
        response = client.get(_url("文档/说明.pdf", branch, prefix="raw"))
        assert "UTF-8''" in response["Content-Disposition"]

    def test_missing_branch_redirects_default(self, client, project, branch):
        add_file(branch, "a.py", b"x")
        response = client.get(f"{PROJECT_URL}/raw/a.py")
        assert response.status_code == 302
        assert f"branch={branch.pk}" in response.url

    def test_traversal_cannot_read_system_files(self, client, project, branch):
        for crafted in ["..%2F..%2Fetc%2Fpasswd", "%2E%2E%2Fconfig%2Fsettings.py"]:
            response = client.get(f"{PROJECT_URL}/raw/{crafted}?branch={branch.pk}")
            assert response.status_code == 404, crafted

    def test_other_branch_path_404(self, client, project, branch):
        add_file(branch, "secret.txt", b"s")
        other = create_branch(project_id=project.id, name="no-secret")
        response = client.get(_url("secret.txt", other, prefix="raw"))
        assert response.status_code == 404


class TestUrlEncoding:
    """空文件、中文、空格、#/? 与百分号文件名的编解码各一例。"""

    def test_chinese_path_blob(self, client, project, branch):
        add_file(branch, "文档/架构说明.md", "# 架构\n\n分层说明".encode("utf-8"))
        response = client.get(_url("文档/架构说明.md", branch))
        assert response.status_code == 200
        assert "架构说明.md" in response.content.decode()

    def test_space_in_name(self, client, project, branch):
        add_file(branch, "my notes.txt", b"notes")
        response = client.get(_url("my notes.txt", branch))
        assert response.status_code == 200

    def test_hash_and_question_in_name(self, client, project, branch):
        add_file(branch, "hash#tag.txt", b"h")
        add_file(branch, "问?号.txt", b"q")
        for name in ["hash#tag.txt", "问?号.txt"]:
            response = client.get(_url(name, branch))
            assert response.status_code == 200, name

    def test_percent_in_name_not_double_decoded(self, client, project, branch):
        # 文件名内含字面 %23；URL 编码为 %2523，只解码一次
        add_file(branch, "百分%23号.txt", b"pct")
        response = client.get(_url("百分%23号.txt", branch))
        assert response.status_code == 200
        assert "百分%23号.txt" in response.content.decode()

    def test_branch_param_survives_special_chars(self, client, project, branch):
        add_file(branch, "a#b.py", b"x")
        response = client.get(_url("a#b.py", branch, prefix="raw") + "&dl=1")
        assert response.status_code == 200
        assert "attachment" in response["Content-Disposition"]

    def test_empty_file_state(self, client, project, branch):
        add_file(branch, "empty.txt", b"")
        response = client.get(_url("empty.txt", branch))
        assert "空文件" in response.content.decode()
        raw = client.get(_url("empty.txt", branch, prefix="raw"))
        assert raw.status_code == 200
        assert b"".join(raw.streaming_content) == b""


class TestUploadService:
    def _upload(self, branch, files, overwrite=False, revision=None):
        from catalog.models import Branch as BranchModel
        from catalog.services.entries import upload_files

        if revision is None:  # 读取数据库最新值，避免夹具对象过期
            revision = BranchModel.objects.get(pk=branch.pk).revision
        return upload_files(
            branch_id=branch.pk,
            expected_revision=revision,
            parent_path="",
            files=files,
            overwrite=overwrite,
        )

    def test_upload_creates_entries_and_bumps_revision(self, branch):
        from django.core.files.uploadedfile import SimpleUploadedFile

        result = self._upload(branch, [
            SimpleUploadedFile("README.md", b"hello readme"),
            SimpleUploadedFile("a.py", b"print(1)"),
        ])
        assert result.revision == 2
        assert {e.path for e in result.entries.all()} == {"README.md", "a.py"}

    def test_batch_all_or_nothing_on_conflict(self, branch):
        from catalog.services.entries import FileConflict
        from django.core.files.uploadedfile import SimpleUploadedFile

        self._upload(branch, [SimpleUploadedFile("README.md", b"first")])
        with pytest.raises(FileConflict) as exc:
            self._upload(branch, [
                SimpleUploadedFile("new.txt", b"fresh"),
                SimpleUploadedFile("README.md", b"second"),
            ])
        assert "README.md" in exc.value.conflicts
        # 冲突时整批回滚：new.txt 也没有写入
        paths = {e.path for e in branch.entries.all()}
        assert paths == {"README.md"}
        # 原内容未被改动
        from catalog.services.storage import read_object_bytes

        entry = branch.entries.get(path="README.md")
        assert read_object_bytes(entry.blob.sha256) == b"first"

    def test_overwrite_confirmed_replaces_content(self, branch):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self._upload(branch, [SimpleUploadedFile("README.md", b"v1")])
        self._upload(branch, [SimpleUploadedFile("README.md", b"v2-longer")], overwrite=True)
        from catalog.services.storage import read_object_bytes

        entry = branch.entries.get(path="README.md")
        assert read_object_bytes(entry.blob.sha256) == b"v2-longer"
        assert entry.size == len(b"v2-longer")

    def test_replacing_file_does_not_change_source_branch(self, project, branch):
        # 计划要求的分支隔离核心用例（真实上传 + 真实对象）
        from catalog.services.branches import create_branch
        from catalog.services.storage import open_blob
        from django.core.files.uploadedfile import SimpleUploadedFile

        self._upload(branch, [SimpleUploadedFile("README.md", b"main content")])
        branch.refresh_from_db()
        copied = create_branch(
            project_id=project.id, name="release-1.0",
            source_branch_id=branch.id, expected_revision=branch.revision,
        )
        self._upload(branch=copied, files=[SimpleUploadedFile("README.md", b"release content")], overwrite=True)
        from catalog.models import Entry

        main_entry = Entry.objects.get(branch=branch, path="README.md")
        copied_entry = Entry.objects.get(branch=copied, path="README.md")
        with open_blob(main_entry.blob) as stream:
            assert stream.read() == b"main content"
        with open_blob(copied_entry.blob) as stream:
            assert stream.read() == b"release content"
        assert main_entry.blob_id != copied_entry.blob_id

    def test_revision_conflict_rejects_and_keeps_content(self, branch):
        from catalog.services.entries import upload_files
        from catalog.services.exceptions import RevisionConflict
        from django.core.files.uploadedfile import SimpleUploadedFile

        upload_files(branch_id=branch.pk, expected_revision=branch.revision,
                     parent_path="", files=[SimpleUploadedFile("README.md", b"v1")])
        with pytest.raises(RevisionConflict):
            upload_files(branch_id=branch.pk, expected_revision=1,  # 过期基准
                         parent_path="", files=[SimpleUploadedFile("other.txt", b"x")])
        assert not branch.entries.filter(path="other.txt").exists()

    def test_per_file_capacity(self, branch, settings):
        settings.GITODS_LIMITS = {**settings.GITODS_LIMITS, "MAX_FILE_BYTES": 4}
        from catalog.services.exceptions import CapacityExceeded
        from django.core.files.uploadedfile import SimpleUploadedFile

        with pytest.raises(CapacityExceeded):
            self._upload(branch, [SimpleUploadedFile("big.bin", b"12345")])

    def test_actual_bytes_enforced_not_declared(self, branch, settings):
        # 声明 size 很小、实际内容超限：按实际读入字节拒绝
        from catalog.services.exceptions import CapacityExceeded

        settings.GITODS_LIMITS = {**settings.GITODS_LIMITS, "MAX_FILE_BYTES": 10}

        class LyingFile:
            name = "lie.bin"
            size = 3

            def __init__(self):
                self._sent = False

            def read(self, n=-1):
                if self._sent:
                    return b""
                self._sent = True
                return b"x" * 100

        with pytest.raises(CapacityExceeded):
            self._upload(branch, [LyingFile()])

    def test_upload_to_subdirectory(self, branch):
        from catalog.services.entries import upload_files
        from django.core.files.uploadedfile import SimpleUploadedFile

        add_dir(branch, "docs")
        upload_files(branch_id=branch.pk, expected_revision=branch.revision,
                     parent_path="docs",
                     files=[SimpleUploadedFile("guide.md", b"guide")])
        entry = branch.entries.get(path="docs/guide.md")
        assert entry.parent_path == "docs"

    def test_upload_missing_parent_rejected(self, branch):
        from catalog.services.entries import upload_files
        from catalog.services.exceptions import ValidationError
        from django.core.files.uploadedfile import SimpleUploadedFile

        with pytest.raises(ValidationError, match="不存在"):
            upload_files(branch_id=branch.pk, expected_revision=branch.revision,
                         parent_path="ghost", files=[SimpleUploadedFile("a", b"1")])


class TestFolderRenameDeleteService:
    def _svc(self):
        from catalog.services import entries as entry_service

        return entry_service

    def test_create_folder_and_duplicate(self, branch):
        svc = self._svc()
        svc.create_folder(branch_id=branch.pk, expected_revision=branch.revision,
                          parent_path="", name="新目录")
        from catalog.models import Branch as BranchModel

        with pytest.raises(Exception, match="已存在"):
            svc.create_folder(branch_id=branch.pk,
                              expected_revision=BranchModel.objects.get(pk=branch.pk).revision,
                              parent_path="", name="新目录")

    def test_rename_dir_updates_descendants(self, branch):
        svc = self._svc()
        add_dir(branch, "a")
        add_dir(branch, "a/b")
        add_file(branch, "a/x.txt", b"1")
        add_file(branch, "a/b/y.txt", b"2")
        add_file(branch, "outside.txt", b"3")
        svc.rename_entry(branch_id=branch.pk, expected_revision=branch.revision,
                         path="a", new_name="renamed")
        paths = {e.path for e in branch.entries.all()}
        assert paths == {"renamed", "renamed/b", "renamed/x.txt", "renamed/b/y.txt", "outside.txt"}
        b = branch.entries.get(path="renamed/b")
        assert b.parent_path == "renamed"
        y = branch.entries.get(path="renamed/b/y.txt")
        assert y.parent_path == "renamed/b"

    def test_rename_conflict_rejected(self, branch):
        svc = self._svc()
        add_file(branch, "a.txt", b"1")
        add_file(branch, "b.txt", b"2")
        with pytest.raises(Exception, match="已存在"):
            svc.rename_entry(branch_id=branch.pk, expected_revision=branch.revision,
                             path="a.txt", new_name="b.txt")

    def test_rename_shared_blob_untouched(self, project, branch):
        from catalog.services.branches import create_branch

        add_file(branch, "shared.py", b"same")
        copied = create_branch(project_id=project.id, name="copy2",
                               source_branch_id=branch.id, expected_revision=branch.revision)
        svc = self._svc()
        svc.rename_entry(branch_id=branch.pk, expected_revision=branch.revision,
                         path="shared.py", new_name="moved.py")
        assert branch.entries.filter(path="moved.py").exists()
        assert copied.entries.filter(path="shared.py").exists()
        assert (copied.entries.get(path="shared.py").blob_id
                == branch.entries.get(path="moved.py").blob_id)

    def test_delete_nonempty_dir_cascades_and_keeps_blob(self, branch):
        from catalog.models import Blob

        svc = self._svc()
        add_dir(branch, "pkg")
        entry = add_file(branch, "pkg/data.json", b'{"k":1}')
        blob_pk = entry.blob.pk
        svc.delete_entry(branch_id=branch.pk, expected_revision=branch.revision, path="pkg")
        assert branch.entries.filter(path__startswith="pkg").count() == 0
        assert Blob.objects.filter(pk=blob_pk).exists()

    def test_delete_missing_raises(self, branch):
        from catalog.services.exceptions import NotFound

        with pytest.raises(NotFound):
            self._svc().delete_entry(branch_id=branch.pk,
                                     expected_revision=branch.revision, path="ghost")


class TestAdminFilesViews:
    def _manage(self, project, branch, path=""):
        from urllib.parse import quote

        url = f"/admin/catalog/project/{project.pk}/files/?branch={branch.pk}"
        if path:
            url += f"&path={quote(path)}"
        return url

    def test_manage_requires_staff(self, client, project, branch):
        response = client.get(self._manage(project, branch))
        assert response.status_code == 302
        assert "/admin/login/" in response.url

    def test_upload_via_admin(self, admin_client, project, branch):
        from django.core.files.uploadedfile import SimpleUploadedFile

        response = admin_client.post(
            f"/admin/catalog/project/{project.pk}/files/upload/?branch={branch.pk}",
            {"path": "", "expected_revision": str(branch.revision),
             "files": SimpleUploadedFile("admin.txt", b"from admin")},
        )
        assert response.status_code == 302
        assert branch.entries.filter(path="admin.txt").exists()

    def test_upload_conflict_shows_list_then_overwrite(self, admin_client, project, branch):
        from django.core.files.uploadedfile import SimpleUploadedFile

        add_file(branch, "README.md", b"original")
        url = f"/admin/catalog/project/{project.pk}/files/upload/?branch={branch.pk}"
        response = admin_client.post(url, {
            "path": "", "expected_revision": str(branch.revision),
            "files": SimpleUploadedFile("README.md", b"attempt"),
        })
        assert response.status_code == 200  # 冲突页，不覆盖
        assert "README.md" in response.context["conflict_files"]
        from catalog.services.storage import read_object_bytes

        assert read_object_bytes(branch.entries.get(path="README.md").blob.sha256) == b"original"

        branch.refresh_from_db()
        response = admin_client.post(url, {
            "path": "", "expected_revision": str(branch.revision), "overwrite": "1",
            "files": SimpleUploadedFile("README.md", b"replaced"),
        })
        assert response.status_code == 302
        assert read_object_bytes(branch.entries.get(path="README.md").blob.sha256) == b"replaced"

    def test_folder_via_admin(self, admin_client, project, branch):
        response = admin_client.post(
            f"/admin/catalog/project/{project.pk}/files/folder/?branch={branch.pk}",
            {"path": "", "expected_revision": str(branch.revision), "name": "新建目录"},
        )
        assert response.status_code == 302
        assert branch.entries.filter(path="新建目录", kind="dir").exists()

    def test_rename_get_no_side_effect_then_post(self, admin_client, project, branch):
        entry = add_file(branch, "old.py", b"x")
        url = f"/admin/catalog/project/{project.pk}/files/{entry.pk}/rename/?branch={branch.pk}"
        response = admin_client.get(url)
        assert response.status_code == 200
        assert branch.entries.filter(pk=entry.pk, name="old.py").exists()
        response = admin_client.post(url, {
            "path": "", "expected_revision": str(branch.revision), "new_name": "new.py",
        })
        assert response.status_code == 302
        assert not branch.entries.filter(name="old.py").exists()
        assert branch.entries.filter(path="new.py").exists()

    def test_delete_get_no_side_effect_then_post(self, admin_client, project, branch):
        add_dir(branch, "victim")
        add_file(branch, "victim/x.txt", b"1")
        dir_entry = branch.entries.get(path="victim")
        url = f"/admin/catalog/project/{project.pk}/files/{dir_entry.pk}/delete/?branch={branch.pk}"
        response = admin_client.get(url)
        assert response.status_code == 200
        assert response.context["descendant_count"] == 1
        assert branch.entries.filter(path="victim").exists()
        response = admin_client.post(url, {"path": "", "expected_revision": str(branch.revision)})
        assert response.status_code == 302
        assert branch.entries.filter(path__startswith="victim").count() == 0

    def test_delete_current_dir_redirects_to_nearest_parent(self, admin_client, project, branch):
        add_dir(branch, "p")
        add_dir(branch, "p/q")
        q_entry = branch.entries.get(path="p/q")
        url = (f"/admin/catalog/project/{project.pk}/files/{q_entry.pk}/delete/"
               f"?branch={branch.pk}&path=p/q")
        response = admin_client.post(url, {"path": "p/q", "expected_revision": str(branch.revision)})
        assert response.status_code == 302
        # q 删除后回到仍存在的父目录 p（随后 p 也被删则回根——这里 p 仍在）
        assert response.url.startswith("/backend/device-platform/tree/p?branch=")

    def test_stale_revision_shows_friendly_error(self, admin_client, project, branch):
        from django.core.files.uploadedfile import SimpleUploadedFile

        response = admin_client.post(
            f"/admin/catalog/project/{project.pk}/files/upload/?branch={branch.pk}",
            {"path": "", "expected_revision": "999",
             "files": SimpleUploadedFile("stale.txt", b"x")},
        )
        assert response.status_code == 200
        assert "刷新" in response.content.decode()
        assert not branch.entries.filter(path="stale.txt").exists()

    def test_upload_without_csrf_rejected(self, client, project, branch):
        from django.test import Client

        strict = Client(enforce_csrf_checks=True)
        response = strict.post(
            f"/admin/catalog/project/{project.pk}/files/upload/?branch={branch.pk}",
            {"path": "", "expected_revision": "1"},
        )
        assert response.status_code in (302, 403)  # 未登录先跳登录，或 CSRF 拦截


class TestFileTreeNavigation:
    """左树右内容联动布局（用户需求）：进入目录/文件后显示树，根页保持简介栏。"""

    def test_root_keeps_info_sidebar_no_tree(self, client, project, branch):
        add_file(branch, "README.md", b"readme")
        html = client.get(f"{PROJECT_URL}/?branch={branch.pk}").content.decode()
        assert "tree-panel" not in html
        assert "side-desc" in html  # 简介栏仍在

    def test_nested_dir_shows_tree_with_sibling_links(self, client, project, branch):
        add_dir(branch, "src")
        add_file(branch, "src/app.py", b"x")
        add_file(branch, "README.md", b"r")
        html = client.get(f"{PROJECT_URL}/tree/src?branch={branch.pk}").content.decode()
        assert 'aria-label="文件树"' in html
        assert "side-desc" not in html  # 简介栏让位
        assert "tree-dir is-current" in html  # 当前目录高亮
        # 兄弟文件链接携带分支参数
        assert f"/backend/device-platform/blob/src/app.py?branch={branch.pk}" in html
        assert f"/backend/device-platform/blob/README.md?branch={branch.pk}" in html

    def test_blob_page_expands_ancestors_and_highlights_file(self, client, project, branch):
        add_dir(branch, "docs")
        add_file(branch, "docs/note.md", b"# n")
        html = client.get(f"{PROJECT_URL}/blob/docs/note.md?branch={branch.pk}").content.decode()
        assert "tree-file is-current" in html
        assert "<details open" in html  # 祖先目录展开

    def test_tree_branch_isolation(self, client, project, branch):
        add_file(branch, "only-main.txt", b"1")
        empty = create_branch(project_id=project.id, name="tree-empty")
        html = client.get(f"{PROJECT_URL}/blob/only-main.txt?branch={branch.pk}").content.decode()
        assert "only-main.txt" in html
        html2 = client.get(f"{PROJECT_URL}/?branch={empty.pk}").content.decode()
        assert "only-main.txt" not in html2
