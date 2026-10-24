"""分支 ZIP 导出测试：路径与字节一致、空目录保留、临时文件清理。"""
import io
import zipfile

import pytest

from catalog.services.branches import create_branch
from tests.factories import add_dir, add_file

pytestmark = pytest.mark.django_db

DOWNLOAD_URL = "/backend/device-platform/download/"


def _read_zip(response) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content)))


class TestBranchDownload:
    def test_download_matches_published_content(self, client, project, branch):
        add_dir(branch, "src")
        add_dir(branch, "docs")
        add_dir(branch, "docs/空目录")
        add_file(branch, "README.md", b"readme")
        add_file(branch, "src/app.py", b"print(1)")
        add_file(branch, "docs/架构说明.md", "# 架构".encode("utf-8"))

        response = client.get(f"{DOWNLOAD_URL}?branch={branch.pk}")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/zip"
        assert "device-platform-main.zip" in response["Content-Disposition"]

        archive = _read_zip(response)
        names = set(archive.namelist())
        assert {"src/", "docs/", "docs/空目录/", "README.md", "src/app.py", "docs/架构说明.md"} == names
        assert archive.read("README.md") == b"readme"
        assert archive.read("src/app.py") == b"print(1)"
        assert archive.read("docs/架构说明.md") == "# 架构".encode("utf-8")

    def test_download_only_current_branch(self, client, project, branch):
        add_file(branch, "main-only.txt", b"1")
        other = create_branch(project_id=project.id, name="dev", source_branch_id=branch.id)
        add_file_branch = None  # noqa: F841
        from tests.factories import add_file as _add

        _add(other, "dev-only.txt", b"2")
        response = client.get(f"{DOWNLOAD_URL}?branch={branch.pk}")
        names = set(_read_zip(response).namelist())
        assert names == {"main-only.txt"}
        assert "dev-only.txt" not in names

    def test_empty_branch_disabled_with_reason(self, client, project, branch):
        response = client.get(f"{DOWNLOAD_URL}?branch={branch.pk}")
        assert response.status_code == 409
        assert b"\xe6\xb2\xa1\xe6\x9c\x89\xe4\xbb\xbb\xe4\xbd\x95\xe6\x96\x87\xe4\xbb\xb6" in response.content  # “没有任何文件”

    def test_no_branch_redirects_default(self, client, project, branch):
        response = client.get(DOWNLOAD_URL)
        assert response.status_code == 302
        assert f"branch={branch.pk}" in response.url

    def test_bad_branch_404(self, client, project, branch):
        response = client.get(f"{DOWNLOAD_URL}?branch=not-uuid")
        assert response.status_code == 404

    def test_temp_zip_removed_after_send(self, client, project, branch, settings):
        from pathlib import Path

        add_file(branch, "README.md", b"x")
        tmp = Path(settings.DATA_ROOT) / "tmp"
        before = sorted(p.name for p in tmp.glob("*.zip"))
        response = client.get(f"{DOWNLOAD_URL}?branch={branch.pk}")
        assert response.status_code == 200
        b"".join(response.streaming_content)  # 完整消费触发 close
        after = sorted(p.name for p in tmp.glob("*.zip"))
        assert after == before  # 新生成的临时 zip 已删除

    def test_download_does_not_delete_objects(self, client, project, branch):
        from catalog.models import Blob

        entry = add_file(branch, "keep.txt", b"keep")
        blob_pk = entry.blob.pk
        response = client.get(f"{DOWNLOAD_URL}?branch={branch.pk}")
        assert response.status_code == 200
        b"".join(response.streaming_content)
        assert Blob.objects.filter(pk=blob_pk).exists()

    def test_requires_staff_only_for_writes_download_is_public(self, client, project, branch):
        # 下载是公开只读能力：访客可用
        add_file(branch, "public.txt", b"1")
        response = client.get(f"{DOWNLOAD_URL}?branch={branch.pk}")
        assert response.status_code == 200
