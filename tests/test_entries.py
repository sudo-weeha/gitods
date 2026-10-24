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
