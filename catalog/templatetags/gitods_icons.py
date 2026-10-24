"""内联 Lucide 线性图标：读取 static/icons 下同一套 SVG，按名缓存。"""
import re
from pathlib import Path

from django import template
from django.utils.safestring import mark_safe

register = template.Library()

_ICON_DIR = Path(__file__).resolve().parents[2] / "static" / "icons"
_CACHE: dict[str, str] = {}


@register.simple_tag
def icon(name: str, cls: str = "") -> str:
    key = f"{name}:{cls}"
    if key not in _CACHE:
        svg = (_ICON_DIR / f"{name}.svg").read_text(encoding="utf-8")
        svg = re.sub(
            r'class="[^"]*"',
            f'class="icon icon-{name} {cls}"'.replace("  ", " "),
            svg,
            count=1,
        )
        svg = svg.replace("\n", "")
        _CACHE[key] = svg
    return mark_safe(_CACHE[key])
