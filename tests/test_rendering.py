"""预览渲染与图片安全测试：编码、降级、转义、Markdown 重写、受控图片。"""
import io
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from catalog.services.rendering import render_markdown, render_source
from tests.factories import add_dir, add_file

pytestmark = pytest.mark.django_db


# ---- 源码渲染 ----


def test_source_is_escaped():
    rendered = render_source(b'<script>alert("x")</script>', filename="demo.html")
    assert "<script>" not in rendered.html
    assert "&lt;" in rendered.html


def test_utf8_and_non_utf8():
    assert render_source("中文源码".encode("utf-8"), "a.py").line_count == 1
    binary = render_source(b"\xff\xfe\x00bad", "data.bin")
    assert binary.binary is True
    assert binary.html == ""


def test_empty_file():
    rendered = render_source(b"", "empty.py")
    assert rendered.line_count == 0
    assert rendered.html == ""


def test_highlight_threshold_falls_back_to_plain(settings):
    settings.GITODS_LIMITS = {**settings.GITODS_LIMITS, "HIGHLIGHT_MAX_LINES": 3}
    code = "\n".join(f"line {i} <b>&amp;" for i in range(1, 11)).encode()
    rendered = render_source(code, "big.py")
    assert "hl-plain" in rendered.html
    assert 'id="L1"' in rendered.html and 'id="L10"' in rendered.html
    # 降级路径内容逐行转义
    assert "<b>" not in rendered.html
    assert "&lt;b&gt;" in rendered.html
    assert "&amp;amp;" in rendered.html


def test_line_truncation_flag(settings):
    settings.GITODS_LIMITS = {**settings.GITODS_LIMITS, "TEXT_PREVIEW_MAX_LINES": 5}
    rendered = render_source(b"a\nb\nc\nd\ne\nf\ng", "t.py")
    assert rendered.line_count == 5
    assert rendered.truncated is True
    assert "f" not in rendered.html


def test_too_large_only_download(settings):
    settings.GITODS_LIMITS = {**settings.GITODS_LIMITS, "TEXT_PREVIEW_MAX_BYTES": 4}
    rendered = render_source(b"1234567890", "big.txt")
    assert rendered.too_large is True
    assert rendered.html == ""


def test_unknown_type_uses_text_lexer():
    rendered = render_source("随便一段没有后缀的内容".encode(), "LICENSE")
    assert rendered.line_count == 1


def test_long_line_preserved():
    long_line = "x" * 500
    rendered = render_source(long_line.encode(), "a.py")
    assert "x" * 500 in rendered.html


def test_line_anchors_present():
    rendered = render_source(b"a\nb\nc", "a.py")
    assert 'id="L1"' in rendered.html or "#L1" in rendered.html
    assert 'id="L3"' in rendered.html or "#L3" in rendered.html


# ---- Markdown 渲染与重写 ----


def test_markdown_drops_script_and_dangerous_link(branch):
    rendered = render_markdown(
        '<script>alert(1)</script>\n[x](javascript:alert(1))', branch=branch, current_path="README.md"
    )
    assert "<script" not in rendered.html.lower()
    assert 'href="javascript:' not in rendered.html.lower()


def test_markdown_renders_table_headings_code(branch):
    rendered = render_markdown(
        "# 标题\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n\n- 列表\n\n```python\nx = 1\n```\n",
        branch=branch,
        current_path="README.md",
    )
    assert "<h1>" in rendered.html
    assert "<table>" in rendered.html
    assert "<li>" in rendered.html
    assert "hl" in rendered.html or "<pre>" in rendered.html


class TestMarkdownLinks:
    def _href(self, html, text):
        import re

        match = re.search(rf'<a href="([^"]*)"[^>]*>{text}</a>', html)
        return match.group(1) if match else None

    def test_same_dir_file_links_blob_with_branch(self, branch):
        add_file(branch, "docs/guide.md", b"x")
        add_dir(branch, "docs")
        rendered = render_markdown("[指南](guide.md)", branch, "docs/README.md")
        href = self._href(rendered.html, "指南")
        assert href is not None
        assert "/backend/device-platform/blob/docs/guide.md" in href
        assert f"branch={branch.pk}" in href

    def test_subdir_and_dotdot_links(self, branch):
        add_file(branch, "docs/deep/note.md", b"n")
        add_dir(branch, "docs/deep")
        rendered = render_markdown("[笔记](deep/note.md) [上溯](../intro.md)", branch, "docs/README.md")
        assert "/blob/docs/deep/note.md" in rendered.html
        assert "/blob/intro.md" in rendered.html  # docs/ 下 ../intro.md 解析到根级

    def test_escape_above_root_rejected(self, branch):
        rendered = render_markdown("[越界](../../outside.md)", branch, "docs/README.md")
        # 越出根：href 被清空，不产生链接
        assert '<a href="">越界</a>' in rendered.html or "outside.md" not in rendered.html

    def test_dir_link_targets_tree(self, branch):
        add_dir(branch, "assets")
        rendered = render_markdown("[资源](assets)", branch, "README.md")
        assert "/backend/device-platform/tree/assets" in rendered.html
        assert f"branch={branch.pk}" in rendered.html

    def test_anchor_kept_on_internal_link(self, branch):
        add_file(branch, "docs/guide.md", b"x")
        rendered = render_markdown("[跳](docs/guide.md#section)", branch, "README.md")
        assert "#section" in rendered.html

    def test_pure_anchor_preserved(self, branch):
        rendered = render_markdown("[目录](#toc)", branch, "README.md")
        assert 'href="#toc"' in rendered.html

    def test_external_http_kept_protocol_relative_rejected(self, branch):
        rendered = render_markdown(
            "[外链](https://example.com) [相对协议](//evil.com/x) [文件](file:///etc/passwd)",
            branch,
            "README.md",
        )
        assert 'href="https://example.com"' in rendered.html
        assert 'href="//evil' not in rendered.html
        assert 'href="file:' not in rendered.html

    def test_relative_image_uses_raw_with_branch(self, branch):
        add_file(branch, "assets/logo.png", b"png")
        add_dir(branch, "assets")
        rendered = render_markdown("![标志](assets/logo.png)", branch, "README.md")
        import re

        src = re.search(r'<img src="([^"]+)"', rendered.html)
        assert src is not None
        assert "/backend/device-platform/raw/assets/logo.png" in src.group(1)
        assert f"branch={branch.pk}" in src.group(1)

    def test_external_image_shows_stub_no_img(self, branch):
        rendered = render_markdown("![外图](https://cdn.example.com/a.png)", branch, "README.md")
        assert "<img" not in rendered.html
        assert "外部图片" in rendered.html
        assert "https://cdn.example.com/a.png" in rendered.html

    def test_html_disabled(self, branch):
        rendered = render_markdown("<b>加粗</b>", branch, "README.md")
        assert "<b>" not in rendered.html


# ---- 视图状态与受控图片 ----


class TestBlobStates:
    def test_markdown_toggle_keeps_branch_and_file(self, client, project, branch):
        add_file(branch, "README.md", "# 你好".encode())
        url = f"/backend/device-platform/blob/README.md?branch={branch.pk}"
        rendered_page = client.get(url)
        assert "markdown-body" in rendered_page.content.decode()
        source_page = client.get(url + "&view=source")
        assert "code-content" in source_page.content.decode()

    def test_non_utf8_markdown_no_crash(self, client, project, branch):
        add_file(branch, "README.md", b"\xff\xfe\x00bad")
        response = client.get(f"/backend/device-platform/blob/README.md?branch={branch.pk}")
        assert response.status_code == 200
        assert "暂不支持文本预览" in response.content.decode()

    def test_image_blob_shows_preview(self, client, project, branch):
        png = _tiny_png()
        add_file(branch, "assets/logo.png", png)
        response = client.get(
            f"/backend/device-platform/blob/assets/logo.png?branch={branch.pk}"
        )
        assert response.status_code == 200
        assert "image-preview" in response.content.decode()


def _tiny_png() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), (255, 108, 0)).save(buffer, format="PNG")
    return buffer.getvalue()


class TestRawImages:
    def test_valid_png_inline(self, client, project, branch):
        add_file(branch, "logo.png", _tiny_png())
        response = client.get(f"/backend/device-platform/raw/logo.png?branch={branch.pk}")
        assert response.status_code == 200
        assert response["Content-Type"] == "image/png"
        assert "attachment" not in response.get("Content-Disposition", "")
        assert response["X-Content-Type-Options"] == "nosniff"

    def test_corrupted_png_becomes_attachment(self, client, project, branch):
        add_file(branch, "broken.png", b"\x89PNG\r\n\x1a\n garbage not an image")
        response = client.get(f"/backend/device-platform/raw/broken.png?branch={branch.pk}")
        assert response["Content-Type"] == "application/octet-stream"
        assert "attachment" in response["Content-Disposition"]

    def test_pixel_limit_attachment(self, client, project, branch, settings):
        settings.GITODS_LIMITS = {**settings.GITODS_LIMITS, "IMAGE_MAX_PIXELS": 1}
        add_file(branch, "big.png", _tiny_png())  # 4x4 = 16 像素 > 1
        response = client.get(f"/backend/device-platform/raw/big.png?branch={branch.pk}")
        assert "attachment" in response.get("Content-Disposition", "")

    def test_gif_is_attachment_not_active(self, client, project, branch):
        add_file(branch, "anim.gif", b"GIF89a....")
        response = client.get(f"/backend/device-platform/raw/anim.gif?branch={branch.pk}")
        assert response["Content-Type"] == "application/octet-stream"

    def test_svg_served_as_text_not_html(self, client, project, branch):
        svg = '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        add_file(branch, "icon.svg", svg.encode())
        response = client.get(f"/backend/device-platform/raw/icon.svg?branch={branch.pk}")
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/plain")
        assert response["X-Content-Type-Options"] == "nosniff"

    def test_readme_with_relative_image_renders(self, client, project, branch):
        from tests.factories import add_dir

        add_dir(branch, "assets")
        add_file(branch, "assets/logo.png", _tiny_png())
        add_file(branch, "README.md", "# 项\n\n![logo](assets/logo.png)\n".encode())
        response = client.get(f"/backend/device-platform/?branch={branch.pk}")
        html = response.content.decode()
        assert "/backend/device-platform/raw/assets/logo.png" in html
        assert f"branch={branch.pk}" in html
