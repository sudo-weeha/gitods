"""维护命令测试：check_storage 报告、cleanup dry-run/apply、restore_post 收尾。"""
import json
from pathlib import Path

import pytest
from django.core.management import call_command

from catalog.models import Blob, Entry, ImportDraft, LoginAttempt
from tests.factories import add_dir, add_file

pytestmark = pytest.mark.django_db


def _run(command, *args, **kwargs):
    from io import StringIO

    out = StringIO()
    call_command(command, *args, stdout=out, stderr=StringIO(), **kwargs)
    return out.getvalue()


class TestCheckStorage:
    def test_consistent(self, branch, isolated_data_root):
        add_dir(branch, "src")
        add_file(branch, "src/a.py", b"ok")
        output = _run("check_storage")
        assert "缺失 0" in output and "大小失配 0" in output

    def test_reports_orphan_and_missing(self, branch, isolated_data_root, settings):
        entry = add_file(branch, "a.py", b"data")
        # 造一个孤儿对象
        orphan = settings.DATA_ROOT / "objects" / "zz"
        orphan.mkdir(parents=True)
        (orphan / ("z" * 64)).write_bytes(b"orphan")
        # 删掉被引用对象 → 缺失
        from catalog.services.storage import object_path

        object_path(entry.blob.sha256).unlink()
        with pytest.raises(SystemExit):
            call_command("check_storage")

    def test_size_mismatch(self, branch, isolated_data_root):
        entry = add_file(branch, "a.py", b"data")
        from catalog.services.storage import object_path

        path = object_path(entry.blob.sha256)
        path.write_bytes(b"different-length!")
        with pytest.raises(SystemExit):
            call_command("check_storage")


class TestCleanupStorage:
    def test_apply_requires_stopped_web_flag(self, db):
        from django.core.management.base import CommandError

        with pytest.raises(CommandError, match="停止"):
            call_command("cleanup_storage", "--apply")

    def test_dry_run_then_apply(self, branch, isolated_data_root, settings, admin_user):
        from django.utils import timezone

        # 未引用对象：直接建 Blob 行与文件，但不建 Entry
        orphan_sha = "b" * 64
        orphan_path = settings.DATA_ROOT / "objects" / orphan_sha[:2] / orphan_sha
        orphan_path.parent.mkdir(parents=True)
        orphan_path.write_bytes(b"orphan")
        Blob.objects.create(sha256=orphan_sha, size=6)

        # 遗留临时文件
        stray = settings.DATA_ROOT / "tmp" / "export-deadbeef.zip"
        stray.write_bytes(b"stray")

        # 过期 ready 草稿（引用不存在的清单）
        from datetime import timedelta

        draft = ImportDraft.objects.create(
            project_id=branch.project_id, branch_id=branch.pk, owner=admin_user,
            expected_revision=1, status=ImportDraft.STATUS_READY,
            manifest_path=str(settings.DATA_ROOT / "tmp" / "import-x" / "manifest.json"),
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        LoginAttempt.objects.create(scope_key="k")
        from datetime import timedelta as _td

        LoginAttempt.objects.update(created_at=timezone.now() - _td(hours=1))

        # 被引用对象保留
        entry = add_file(branch, "keep.py", b"keep")

        dry = _run("cleanup_storage")
        assert "dry-run" in dry and "未删除" in dry
        assert orphan_path.exists()  # 未动

        _run("cleanup_storage", "--apply", "--yes-i-stopped-web")
        assert not orphan_path.exists()
        assert not stray.exists()
        draft.refresh_from_db()
        assert draft.status == ImportDraft.STATUS_EXPIRED
        assert draft.manifest_path == ""
        from catalog.services.storage import object_path

        assert object_path(entry.blob.sha256).exists()
        assert not LoginAttempt.objects.exists()
