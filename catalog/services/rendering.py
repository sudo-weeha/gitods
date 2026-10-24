"""预览渲染：源码（Pygments + 行号锚点）与 Markdown（markdown-it-py + nh3 清洗）。

当前为基础安全实现；任务 8 补齐相对链接重写、相对图片、行数与高亮阈值
的全部降级路径。HTML 输出只来自 Pygments（自带转义）、逐行转义的纯文本
或 nh3 清洗后的 Markdown，模板侧才允许 mark_safe。
"""
import html
from dataclasses import dataclass, field

import nh3
from django.conf import settings
from markdown_it import MarkdownIt
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, guess_lexer_for_filename
from pygments.util import ClassNotFound

# 禁用原始 HTML，交给 markdown-it 渲染，再经 nh3 白名单清洗
_md = MarkdownIt("commonmark", {"html": False}).enable("table").enable("strikethrough")

NH3_TAGS = {
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "a", "img", "ul", "ol", "li",
    "blockquote", "code", "pre", "em", "strong", "del", "table", "thead",
    "tbody", "tr", "th", "td", "hr", "br", "span", "input",
}
NH3_ATTRS = {
    "*": {"class"},
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height"},
    "input": {"type", "checked", "disabled"},  # 任务列表复选框
}
NH3_URL_SCHEMES = {"http", "https", "mailto"}


@dataclass
class Rendered:
    html: str = ""
    line_count: int = 0
    truncated: bool = False  # 行数超限截断
    too_large: bool = False  # 超过文本预览上限，仅提供下载
    binary: bool = False  # 非 UTF-8，不支持文本预览


def render_source(data: bytes, filename: str) -> Rendered:
    limits = settings.GITODS_LIMITS
    if len(data) > limits["TEXT_PREVIEW_MAX_BYTES"]:
        return Rendered(too_large=True)
    try:
        code = data.decode("utf-8")
    except UnicodeDecodeError:
        return Rendered(binary=True)
    if not code:
        return Rendered(line_count=0)

    lines = code.splitlines()
    truncated = False
    if len(lines) > limits["TEXT_PREVIEW_MAX_LINES"]:
        lines = lines[: limits["TEXT_PREVIEW_MAX_LINES"]]
        truncated = True
    text = "\n".join(lines)

    if len(data) <= limits["HIGHLIGHT_MAX_BYTES"] and len(lines) <= limits["HIGHLIGHT_MAX_LINES"]:
        try:
            lexer = guess_lexer_for_filename(filename, code)
        except ClassNotFound:
            lexer = TextLexer()
        formatter = HtmlFormatter(
            linenos="table", lineanchors="L", cssclass="hl", linespans="l"
        )
        return Rendered(html=highlight(text, lexer, formatter), line_count=len(lines), truncated=truncated)
    return Rendered(
        html=_plain_lines(text), line_count=len(lines), truncated=truncated
    )


def _plain_lines(text: str) -> str:
    """超过高亮阈值：转义后的纯文本 + 行号，结构与 Pygments 输出一致。"""
    rows = []
    for i, line in enumerate(text.splitlines(), 1):
        escaped = html.escape(line) or " "
        rows.append(f'<span id="L{i}"><span class="ln-no">{i}</span>{escaped}</span>')
    return (
        '<div class="hl hl-plain"><pre><code>'
        + "".join(rows)
        + "</code></pre></div>"
    )


def render_markdown(text: str, branch, current_path: str) -> Rendered:
    """渲染 Markdown；branch/current_path 用于任务 8 的相对链接重写。"""
    cleaned = nh3.clean(
        _md.render(text),
        tags=NH3_TAGS,
        attributes=NH3_ATTRS,
        url_schemes=NH3_URL_SCHEMES,
    )
    return Rendered(html=cleaned)
