"""逻辑路径规范化与树结构约束。

路径是无前导 “/” 的规范化相对路径，根目录用空字符串表示；
Unicode 统一 NFC。逻辑路径从不用于拼接物理磁盘路径——对象键由
Blob.sha256 派生（见 services.storage），此处只做字符串校验。
"""
import unicodedata

from django.conf import settings

from catalog.services.exceptions import ValidationError


def _limits() -> dict:
    return settings.GITODS_LIMITS


def normalize_path(value) -> str:
    """校验并返回规范化路径；根目录返回空字符串。

    拒绝：绝对路径、反斜杠、控制字符、空段（含尾随 /）、`.`、`..`、
    超过深度/长度/单段字节限制的路径。Markdown 相对链接解析先自行处理
    `../` 后再调用本函数，不能把允许内部 `../` 的文档链接误判。
    """
    if value is None:
        raise ValidationError("路径不能为空")
    if not isinstance(value, str):
        raise ValidationError("路径必须是字符串")
    if value == "":
        return ""
    limits = _limits()
    if value.startswith("/"):
        raise ValidationError("不允许绝对路径")
    if "\\" in value:
        raise ValidationError("路径不允许反斜杠")
    for ch in value:
        if ord(ch) < 0x20 or ord(ch) == 0x7F:
            raise ValidationError("路径不允许控制字符")

    normalized = unicodedata.normalize("NFC", value)
    parts = normalized.split("/")
    for part in parts:
        if part == "":
            raise ValidationError("路径包含空段")
        if part in (".", ".."):
            raise ValidationError("路径不允许 . 或 .. 段")
        if len(part.encode("utf-8")) > limits["MAX_SEGMENT_BYTES"]:
            raise ValidationError("单段名称过长（超过 255 字节）")
    if len(normalized) > limits["MAX_PATH_LENGTH"]:
        raise ValidationError(f"路径过长（超过 {limits['MAX_PATH_LENGTH']} 字符）")
    if len(parts) > limits["MAX_PATH_DEPTH"]:
        raise ValidationError(f"路径层级过深（超过 {limits['MAX_PATH_DEPTH']} 层）")
    return normalized


def normalize_name(value) -> str:
    """校验单个名称段（文件/文件夹名）并返回 NFC 形式。"""
    if value is None or value == "":
        raise ValidationError("名称不能为空")
    if "/" in value:
        raise ValidationError("名称不能包含 /")
    return normalize_path(value)


def parent_of(path: str) -> str:
    """父目录路径；根下的一级条目返回空字符串（根）。"""
    return path.rsplit("/", 1)[0] if "/" in path else ""


def name_of(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def join_path(parent: str, name: str) -> str:
    return f"{parent}/{name}" if parent else name
