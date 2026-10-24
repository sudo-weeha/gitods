"""测试数据工厂：直接走模型与服务，供各测试模块复用。"""
from catalog.models import Blob, Entry
from catalog.services.storage import store_bytes


def add_file(branch, path: str, content: bytes, updated_at=None) -> Entry:
    """在分支上放一个文件（写真实对象）。updated_at 为空时使用字段默认值。"""
    from catalog.models import Entry as _Entry

    blob = store_bytes(content)
    kwargs = {}
    if updated_at is not None:
        kwargs["updated_at"] = updated_at
    return _Entry.objects.create(
        branch=branch,
        path=path,
        parent_path=path.rsplit("/", 1)[0] if "/" in path else "",
        name=path.rsplit("/", 1)[-1],
        kind="file",
        blob=blob,
        size=blob.size,
        **kwargs,
    )


def add_dir(branch, path: str, updated_at=None) -> Entry:
    kwargs = {}
    if updated_at is not None:
        kwargs["updated_at"] = updated_at
    return Entry.objects.create(
        branch=branch,
        path=path,
        parent_path=path.rsplit("/", 1)[0] if "/" in path else "",
        name=path.rsplit("/", 1)[-1],
        kind="dir",
        size=0,
        **kwargs,
    )


def make_blob(content: bytes = b"demo") -> Blob:
    return store_bytes(content)
