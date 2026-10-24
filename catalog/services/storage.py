"""不可变对象存储：内容寻址（SHA-256）、分块写入、原子发布。

对象位于 DATA_ROOT/objects/<前2位>/<完整哈希>；对象创建后内容永不改写，
同哈希复用。数据库写入失败可能留下未引用对象，由停机清理命令回收
（设计文档 5.3/5.4），不能反向先提交数据库再写对象。
"""
import hashlib
import os
import uuid
from pathlib import Path

from django.conf import settings

from catalog.models import Blob

CHUNK_SIZE = 1024 * 1024


def _data_root() -> Path:
    return Path(settings.DATA_ROOT)


def object_path(sha256: str) -> Path:
    return _data_root() / "objects" / sha256[:2] / sha256


def store_blob(stream) -> Blob:
    """分块读取流，写入私有临时文件后原子发布到哈希对象路径。

    stream 必须是二进制文件对象；调用方负责限制读取总量。
    """
    digest = hashlib.sha256()
    size = 0
    tmp_dir = _data_root() / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / f"upload-{uuid.uuid4().hex}"
    try:
        with open(tmp_path, "wb") as out:
            while chunk := stream.read(CHUNK_SIZE):
                digest.update(chunk)
                size += len(chunk)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())

        sha256 = digest.hexdigest()
        existing = Blob.objects.filter(sha256=sha256).first()
        if existing is not None:
            return existing

        final = object_path(sha256)
        final.parent.mkdir(parents=True, exist_ok=True)
        if not final.exists():
            os.replace(tmp_path, final)

        blob, _ = Blob.objects.get_or_create(sha256=sha256, defaults={"size": size})
        return blob
    finally:
        tmp_path.unlink(missing_ok=True)


def store_bytes(data: bytes) -> Blob:
    import io

    return store_blob(io.BytesIO(data))


def open_blob(blob: Blob):
    return open(object_path(blob.sha256), "rb")


def read_object_bytes(sha256: str) -> bytes:
    """整读对象内容（预览用）；下载走 FileResponse 流式读取。"""
    with open(object_path(sha256), "rb") as stream:
        return stream.read()
