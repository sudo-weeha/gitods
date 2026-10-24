"""catalog 模板过滤器：相对时间、文件大小、字母头像与文件图标。"""
import hashlib
import re
from datetime import timedelta

from django import template
from django.utils import timezone

register = template.Library()

AVATAR_COLORS = [
    "#F0A24B", "#5B8DEF", "#54B585", "#E06C75", "#9A7FD1",
    "#4CB3D9", "#D9824C", "#7A9E4F", "#C76BAF", "#6A7FDB",
]

_ICON_BY_EXT = {
    "md": "file-text", "markdown": "file-text", "rst": "file-text", "txt": "file-text",
    "py": "file-code", "js": "file-code", "ts": "file-code", "tsx": "file-code",
    "jsx": "file-code", "java": "file-code", "go": "file-code", "rs": "file-code",
    "kt": "file-code", "kts": "file-code", "c": "file-code", "h": "file-code",
    "cpp": "file-code", "hpp": "file-code", "cs": "file-code", "rb": "file-code",
    "php": "file-code", "swift": "file-code", "vue": "file-code", "svelte": "file-code",
    "css": "file-code", "scss": "file-code", "less": "file-code",
    "json": "file-code", "yml": "file-code", "yaml": "file-code", "toml": "file-code",
    "ini": "file-code", "cfg": "file-code", "conf": "file-code", "sh": "file-code",
    "bat": "file-code", "sql": "file-code", "xml": "file-code", "html": "file-code",
    "htm": "file-code", "proto": "file-code", "tf": "file-code",
    "png": "image", "jpg": "image", "jpeg": "image", "webp": "image",
    "gif": "image", "svg": "image", "ico": "image", "bmp": "image",
    "zip": "file-archive", "tar": "file-archive", "gz": "file-archive",
    "7z": "file-archive", "rar": "file-archive",
}


@register.filter
def reltime(value):
    """真实相对时间：刚刚 / N分钟前 / N小时前 / N天前 / 日期。"""
    if not value:
        return ""
    now = timezone.now()
    delta = now - value
    seconds = delta.total_seconds()
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{int(seconds // 60)}分钟前"
    if seconds < 86400:
        return f"{int(seconds // 3600)}小时前"
    if seconds < 86400 * 30:
        return f"{int(seconds // 86400)}天前"
    return timezone.localtime(value).strftime("%Y-%m-%d")


@register.filter
def fulltime(value):
    if not value:
        return ""
    return timezone.localtime(value).strftime("%Y-%m-%d %H:%M")


@register.filter
def filesize(value):
    if value is None:
        return "—"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


@register.filter
def avatar_initials(name):
    """字母头像：取名称前两个有意义的字符（拉丁词首字母或前两个字符）。"""
    cleaned = re.sub(r"[\s\-_/.]+", " ", name).strip()
    if not cleaned:
        return "?"
    words = [w for w in cleaned.split() if w]
    if len(words) >= 2 and all(w[0].isascii() and w[0].isalpha() for w in words[:2]):
        return (words[0][0] + words[1][0]).upper()
    return cleaned[:2].upper()


@register.filter
def avatar_color(name):
    index = int(hashlib.md5(name.encode("utf-8")).hexdigest()[:8], 16)
    return AVATAR_COLORS[index % len(AVATAR_COLORS)]


@register.filter
def file_icon(name):
    if not name or "." not in name:
        dotless = name.lower() if name else ""
        return "file-text" if dotless in {"license", "readme", "makefile", "dockerfile"} else "file"
    ext = name.rsplit(".", 1)[-1].lower()
    if not name.startswith("."):
        return _ICON_BY_EXT.get(ext, "file")
    return "file"  # .gitignore 等点文件


LANG_COLORS = {
    "python": "#3572A5", "java": "#B07219", "go": "#00ADD8", "typescript": "#3178C6",
    "javascript": "#F1E05A", "vue": "#41B883", "react": "#61DAFB", "rust": "#DEA584",
    "kotlin": "#A97BFF", "dart": "#00B4AB", "swift": "#F05138", "c": "#555555",
    "c++": "#F34B7D", "c#": "#178600", "php": "#4F5D95", "ruby": "#701516",
    "markdown": "#083FA1", "html": "#E34C26", "css": "#563D7C", "sql": "#E38C00",
    "shell": "#89E051", "yaml": "#CB171E",
}


@register.filter
def lang_color(language):
    return LANG_COLORS.get((language or "").strip().lower(), "#687386")


@register.filter
def dirname(path):
    """父目录路径；根下一级返回空字符串。"""
    return path.rsplit("/", 1)[0] if "/" in path else ""
