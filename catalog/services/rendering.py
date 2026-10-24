"""预览渲染：源码（Pygments 行号锚点）与 Markdown（链接/图片重写 + nh3 清洗）。

HTML 输出只来自三处：Pygments（自带转义）、逐行转义的纯文本、或
nh3 清洗后的 Markdown——模板侧仅对最后一者 mark_safe，渲染过程不
执行源码。相对链接以当前 Markdown 所在目录为基准，`../` 允许但
规范化后必须仍在分支根内；分支 ID 贯穿所有内部 URL（含图片 raw）。
"""
import html
import posixpath
import re
from dataclasses import dataclass

import nh3
from django.conf import settings
from django.urls import reverse
from markdown_it import MarkdownIt
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, guess_lexer_for_filename
from pygments.util import ClassNotFound

# ---- 源码 ----


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

    if (
        len(data) <= limits["HIGHLIGHT_MAX_BYTES"]
        and len(lines) <= limits["HIGHLIGHT_MAX_LINES"]
    ):
        try:
            lexer = guess_lexer_for_filename(filename, code)
        except ClassNotFound:
            lexer = TextLexer()
        formatter = HtmlFormatter(linenos="table", lineanchors="L", cssclass="hl")
        html_out = highlight(text, lexer, formatter)
        # Pygments 生成 L-N 形式锚点；统一为设计的 #LN 定位格式
        html_out = re.sub(r'(id|name|href)="L-(\d+)"', r'\1="L\2"', html_out)
        return Rendered(html=html_out, line_count=len(lines), truncated=truncated)
    return Rendered(html=_plain_lines(text), line_count=len(lines), truncated=truncated)


def _plain_lines(text: str) -> str:
    """超过高亮阈值：转义纯文本 + 行号，保持与 Pygments 一致的 L 锚点。"""
    rows = []
    for i, line in enumerate(text.splitlines(), 1):
        escaped = html.escape(line) or " "
        rows.append(f'<span id="L{i}"><span class="ln-no">{i}</span>{escaped}</span>')
    return '<div class="hl hl-plain"><pre><code>' + "".join(rows) + "</code></pre></div>"


# ---- Markdown ----

# 禁用原始 HTML；token 重写后再渲染，最终经 nh3 白名单清洗
_md = MarkdownIt("commonmark", {"html": False}).enable("table").enable("strikethrough")

NH3_TAGS = {
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "a", "img", "ul", "ol", "li",
    "blockquote", "code", "pre", "em", "strong", "del", "table", "thead",
    "tbody", "tr", "th", "td", "hr", "br", "span", "input",
}
NH3_ATTRS = {
    "*": {"class"},
    "a": {"href", "title", "rel", "target"},
    "img": {"src", "alt", "title", "width", "height", "loading"},
    "input": {"type", "checked", "disabled"},  # 任务列表复选框
}
NH3_URL_SCHEMES = {"http", "https", "mailto"}

_EXTERNAL_IMG = re.compile(r'<img src="(https?://[^"]+)"([^>]*)>')
_EXT_IMAGE_STUB = (
    r'<a href="\1" class="ext-image" target="_blank" rel="noopener nofollow">'
    r'外部图片：\1</a>'
)


def _split_anchor(target: str) -> tuple[str, str]:
    path, sep, anchor = target.partition("#")
    return path, ("#" + anchor if sep else "")


def _resolve_relative(base_dir: str, path: str) -> str | None:
    """以 Markdown 所在目录为基准合并并规范化；越出分支根返回 None。"""
    if not path:
        return None
    joined = posixpath.normpath(posixpath.join(base_dir, path))
    if joined in (".", "") or joined.startswith("..") or joined.startswith("/"):
        return None
    return joined


def _rewrite_link(target: str, branch, base_dir: str) -> str | None:
    """返回重写后的 href；None 表示拒绝（危险协议或越出根）。"""
    if not target:
        return ""
    if target.startswith("#"):
        return target  # 当前文档锚点：保留上下文
    if "://" in target or target.startswith("//"):
        return target if target.startswith(("http://", "https://")) else None
    if target.startswith("mailto:"):
        return target
    path, anchor = _split_anchor(target)
    rel = path.lstrip("/")
    resolved = _resolve_relative(base_dir, rel)
    if resolved is None:
        return None
    # 目录跳 tree、文件跳 blob；目标缺失时按 blob 链接落到缺失状态页
    from catalog.models import Entry

    kind = (
        "dir"
        if Entry.objects.filter(branch=branch, path=resolved, kind="dir").exists()
        else "file"
    )
    url = reverse(
        "project-tree" if kind == "dir" else "project-blob",
        args=[branch.project.group.slug, branch.project.slug, resolved],
    )
    return f"{url}?branch={branch.pk}{anchor}"


EXTERNAL_IMAGE = "EXTERNAL"  # 外链图片：token 阶段保留 src，渲染后替换为提示存根


def _rewrite_image(src: str, branch, base_dir: str) -> str | None:
    """相对图片改写为受控 raw URL；外部图片返回 EXTERNAL_IMAGE 交给存根替换。"""
    if not src:
        return None
    if "://" in src or src.startswith("//"):
        if src.startswith(("http://", "https://")):
            return EXTERNAL_IMAGE  # 不由服务器抓取或代理；显示外链提示
        return None  # 协议相对或危险 scheme：拒绝
    path, _anchor = _split_anchor(src)
    resolved = _resolve_relative(base_dir, path.lstrip("/"))
    if resolved is None:
        return None
    url = reverse(
        "project-raw", args=[branch.project.group.slug, branch.project.slug, resolved]
    )
    return f"{url}?branch={branch.pk}"


def _rewrite_tokens(tokens, branch, current_path: str) -> None:
    base_dir = current_path.rsplit("/", 1)[0] if "/" in current_path else ""

    def walk(items):
        for token in items:
            if token.children:
                walk(token.children)
            if token.type == "link_open":
                rewritten = _rewrite_link(token.attrGet("href") or "", branch, base_dir)
                token.attrSet("href", rewritten if rewritten is not None else "")
            elif token.type == "image":
                rewritten = _rewrite_image(token.attrGet("src") or "", branch, base_dir)
                if rewritten == EXTERNAL_IMAGE:
                    pass  # 保留外部 src：渲染后由外链存根替换
                elif rewritten is not None:
                    token.attrSet("src", rewritten)
                    token.attrSet("loading", "lazy")
                else:
                    token.attrs.pop("src", None)

    walk(tokens)


def render_markdown(text: str, branch, current_path: str) -> Rendered:
    """渲染 Markdown：token 重写相对链接/图片 → nh3 清洗 → mark_safe 候选。"""
    tokens = _md.parse(text)
    _rewrite_tokens(tokens, branch, current_path)
    rendered = _md.renderer.render(tokens, _md.options, {})
    # 外部图片：显示外链提示，不加载、不代理
    rendered = _EXTERNAL_IMG.sub(_EXT_IMAGE_STUB, rendered)
    cleaned = nh3.clean(
        rendered,
        tags=NH3_TAGS,
        attributes=NH3_ATTRS,
        url_schemes=NH3_URL_SCHEMES,
        link_rel=None,  # 外链图片存根需要自行管理 rel/target
    )
    # reverse() 已对路径做一次百分号编码，ammonia 重写 URL 时会把 “%” 再编码
    # 成 %25——统一还原为单次编码（含真实 % 的文件名经此变换仍正确）。
    cleaned = re.sub(r"%25([0-9A-Fa-f]{2})", r"%\1", cleaned)
    return Rendered(html=cleaned)
