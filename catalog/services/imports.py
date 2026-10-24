"""ZIP 导入：分块校验、服务器私有清单、两步预览与原子整分支替换。

- 校验在私有 flock（DATA_ROOT/locks/import.lock）内串行执行，忙时抛
  ImportBusy（视图映射 429）；异常路径释放锁并清理临时目录
- prepare 在耗时解压前固定 expected_revision；草稿 30 分钟有效
- 用户回传的清单参数一律不可信：预览与发布都从服务器保存的原始清单推导
- publish 在事务外持久化 Blob，短事务内锁 Project→Branch→草稿，
  校验后整分支替换 Entry；失败保持旧树（设计文档 5.3/5.4）
"""
import fcntl
import hashlib
import json
import os
import shutil
import stat
import unicodedata
import uuid
import zipfile
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from catalog.models import Branch, Entry, ImportDraft, Project
from catalog.services.exceptions import (
    CapacityExceeded,
    NotFound,
    RevisionConflict,
    ServiceError,
    ValidationError,
)
from catalog.services.paths import normalize_path
from catalog.services.storage import store_blob

EXCLUDED_DIRS = {".git", ".svn", "__MACOSX"}
EXCLUDED_FILES = {".DS_Store"}
REJECT_ENV_PATTERN = (".env",)  # 拒绝 .env 与 .env.*，白名单见 ALLOW_ENV_SUFFIX
ALLOW_ENV_NAMES = {".env.example", ".env.sample", ".env.template"}
ALLOWED_COMPRESS = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}

READ_CHUNK = 1024 * 1024


class ImportBusy(ServiceError):
    """同一主机已有 ZIP 校验进行中（flock 非阻塞失败）。"""


def _limits() -> dict:
    return settings.GITODS_LIMITS


def _tmp_root() -> Path:
    root = Path(settings.DATA_ROOT) / "tmp"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _draft_dir(manifest_path: str) -> Path:
    return Path(manifest_path).resolve().parent


class _ImportLock:
    """非阻塞主机内文件锁：同一时刻只允许一个 ZIP 校验。"""

    def __init__(self):
        lock_dir = Path(settings.DATA_ROOT) / "locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        self._path = lock_dir / "import.lock"
        self._fd = None

    def __enter__(self):
        self._fd = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self._fd)
            self._fd = None
            raise ImportBusy("已有导入正在校验，请稍后重试")
        return self

    def __exit__(self, *exc_info):
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None
        return False


def _reject_env_file(name: str) -> bool:
    if name in ALLOW_ENV_NAMES:
        return False
    if name == ".env" or name.startswith(".env."):
        return True
    return False


def _norm_zip_path(raw_name: str, exclusions: list) -> tuple[str, bool] | None:
    """规范化 ZIP 内路径；返回 (路径, 是否目录) 或 None（跳过记录）。

    返回特殊值 "INVALID" 由调用方转为拒绝——用异常更直接，这里直接抛。
    """
    if raw_name in ("", "/", "./", "."):
        return None
    name = raw_name.replace("\\", "/")
    if name.startswith("/"):
        raise ValidationError(f"压缩包内不允许绝对路径：{raw_name}")
    parts = name.split("/")
    is_dir = parts[-1] == ""
    if is_dir:
        parts = parts[:-1]
    kept = []
    for part in parts:
        if part == "":
            raise ValidationError(f"压缩包路径包含空段：{raw_name}")
        if part in (".", ".."):
            raise ValidationError(f"压缩包路径不允许 . 或 .. 段：{raw_name}")
        kept.append(unicodedata.normalize("NFC", part))
    joined = "/".join(kept)
    # 排除项：跳过但记录
    top = kept[0]
    if top in EXCLUDED_DIRS:
        exclusions.append({"path": joined, "reason": f"{top}/ 目录"})
        return None
    if not is_dir and Path(joined).name in EXCLUDED_FILES:
        exclusions.append({"path": joined, "reason": ".DS_Store"})
        return None
    # 拒绝项：真实运行环境密钥
    if not is_dir and _reject_env_file(Path(joined).name):
        raise ValidationError(
            f"压缩包包含疑似运行环境配置 {Path(joined).name}；"
            "请移除后重传（.env.example/.sample/.template 允许）"
        )
    try:
        normalized = normalize_path(joined)
    except ServiceError as exc:
        raise ValidationError(f"压缩包内路径非法（{raw_name}）：{exc}") from exc
    return normalized, is_dir


def _validate_and_extract(zip_path: Path, work_dir: Path) -> dict:
    """打开 ZIP、逐项校验并解出到 work_dir/files；返回服务器原始清单。

    只用 ZipFile.open() 分块读写到 EOF（CRC 由 zipfile 校验），禁用 extractall。
    """
    limits = _limits()
    files_root = work_dir / "files"
    files_root.mkdir(parents=True)
    exclusions: list[dict] = []
    entries: dict[str, dict] = {}  # path -> {"kind","size"}
    total_uncompressed = 0
    declared_total = 0

    try:
        archive = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, NotImplementedError) as exc:
        raise ValidationError(f"不是有效的 ZIP 文件：{exc}") from exc

    with archive:
        infos = archive.infolist()
        if not infos:
            return {"entries": [], "excluded": exclusions, "common_root": None}

        for info in infos:
            mode = (info.external_attr >> 16) & 0xFFFF
            fmt = mode & stat.S_IFMT(0o170000)
            if fmt not in (0, stat.S_IFREG):  # 符号链接、套接字、设备等特殊文件
                raise ValidationError(
                    f"压缩包包含符号链接或特殊文件：{info.filename}"
                )
            if info.flag_bits & 0x1:
                raise ValidationError("不支持加密 ZIP；请提供未加密压缩包")
            if info.compress_type not in ALLOWED_COMPRESS:
                raise ValidationError(
                    f"仅支持 stored/deflate 压缩：{info.filename}"
                )

            parsed = _norm_zip_path(info.filename, exclusions)
            if parsed is None:
                continue
            path, is_dir = parsed

            if is_dir:
                if path in entries and entries[path]["kind"] == "file":
                    raise ValidationError(f"路径同时是文件和目录：{path}")
                entries.setdefault(path, {"kind": "dir", "size": 0})
                continue

            if path in entries:
                if entries[path]["kind"] == "dir":
                    raise ValidationError(f"路径同时是文件和目录：{path}")
                raise ValidationError(f"压缩包内重复路径：{path}")
            entries[path] = {"kind": "file", "size": 0}

            # 父目录补齐（父级若已被文件占用则类型冲突）
            parts = path.split("/")
            for i in range(1, len(parts)):
                parent = "/".join(parts[:i])
                if parent in entries and entries[parent]["kind"] == "file":
                    raise ValidationError(f"路径同时是文件和目录：{parent}")
                entries.setdefault(parent, {"kind": "dir", "size": 0})

            declared_total += info.file_size

        if declared_total > limits["MAX_ZIP_UNCOMPRESSED_BYTES"]:
            raise CapacityExceeded("解压后总大小超过上限")
        if len(entries) > limits["MAX_BRANCH_ENTRIES"]:
            raise CapacityExceeded("条目总数超过分支上限（目录计入条目数）")

        # 分块读出并写临时文件；读到 EOF 完成 CRC 校验，按实际字节数计数
        for path, meta in entries.items():
            if meta["kind"] != "file":
                (files_root / path).parent.mkdir(parents=True, exist_ok=True)
                continue
            target = files_root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with archive.open(path) as src, open(target, "wb") as out:
                    size = 0
                    while chunk := src.read(READ_CHUNK):
                        size += len(chunk)
                        out.write(chunk)
                    out.flush()
            except (zipfile.BadZipFile, RuntimeError, OSError, NotImplementedError) as exc:
                raise ValidationError(f"读取 {path} 失败（CRC 或编码问题）：{exc}") from exc
            total_uncompressed += size
            meta["size"] = size
            if size > limits["MAX_FILE_BYTES"]:
                raise CapacityExceeded(f"{path} 超过单文件大小上限")
            if total_uncompressed > limits["MAX_ZIP_UNCOMPRESSED_BYTES"]:
                raise CapacityExceeded("解压后总大小超过上限")

        if total_uncompressed > limits["MAX_BRANCH_BYTES"]:
            raise CapacityExceeded("导入内容超过分支逻辑总大小上限")

    # 防御：确认没有写出临时目录
    resolved_root = files_root.resolve()
    for path in entries:
        if not (files_root / path).resolve().is_relative_to(resolved_root):
            raise ValidationError("解压路径越界")  # 理论不可达，防御性校验

    common_root = _detect_common_root(entries)
    return {
        "entries": [{"path": p, "kind": m["kind"], "size": m["size"]} for p, m in entries.items()],
        "excluded": exclusions,
        "common_root": common_root,
        "total_uncompressed": total_uncompressed,
    }


def _detect_common_root(entries: dict) -> str | None:
    """所有顶层条目共享同一个外层目录（且没有根级文件）时返回该目录名。"""
    tops = set()
    for path, meta in entries.items():
        top = path.split("/", 1)[0]
        tops.add(top)
        if "/" not in path and meta["kind"] == "file":
            return None  # 根级文件：没有共同外层目录
    return tops.pop() if len(tops) == 1 else None


def _manifest_entries(manifest: dict, strip_root: bool) -> list[dict]:
    root = manifest.get("common_root")
    if not strip_root or not root:
        return manifest["entries"]
    prefix = root + "/"
    stripped = []
    for e in manifest["entries"]:
        if e["path"] == root:
            continue  # 外层目录成为分支根，不再生成条目
        path = e["path"][len(prefix):] if e["path"].startswith(prefix) else e["path"]
        stripped.append({**e, "path": path})
    return stripped


def _owned_draft(draft_id, owner) -> ImportDraft:
    draft = ImportDraft.objects.filter(pk=draft_id).first()
    if draft is None or draft.owner_id != owner.pk:
        raise NotFound("导入草稿不存在")
    return draft


def prepare_zip_import(
    *, project_id, branch_id, expected_revision, owner, uploaded_file, strip_root=True
) -> ImportDraft:
    """两步导入第一步：上传→校验→生成 30 分钟草稿（不写公开分支）。"""
    if uploaded_file is None or not getattr(uploaded_file, "name", ""):
        raise ValidationError("请选择 ZIP 文件")
    limits = _limits()
    declared = getattr(uploaded_file, "size", None) or 0
    if declared > limits["MAX_ZIP_BYTES"]:
        raise CapacityExceeded("压缩包体积超过上限")

    branch = Branch.objects.select_related("project").filter(pk=branch_id).first()
    if branch is None or branch.project_id != project_id:
        raise NotFound("分支不存在")
    # 耗时处理前固定并发基准
    if expected_revision is None or branch.revision != expected_revision:
        raise RevisionConflict("分支内容已更新，请刷新后重试")

    work_dir = _tmp_root() / f"import-{uuid.uuid4().hex}"
    zip_path = work_dir / "source.zip"
    work_dir.mkdir(parents=True)
    digest = hashlib.sha256()
    try:
        # 落盘（按实际字节计数）
        size = 0
        with open(zip_path, "wb") as out:
            while chunk := uploaded_file.read(READ_CHUNK):
                size += len(chunk)
                if size > limits["MAX_ZIP_BYTES"]:
                    raise CapacityExceeded("压缩包体积超过上限")
                digest.update(chunk)
                out.write(chunk)

        with _ImportLock():
            manifest = _validate_and_extract(zip_path, work_dir)

        manifest["zip_sha256"] = digest.hexdigest()
        manifest["zip_size"] = size
        effective_strip = bool(strip_root and manifest["common_root"])
        manifest["strip_root"] = effective_strip
        manifest_path = work_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
        )

        with transaction.atomic():
            draft = ImportDraft.objects.create(
                project_id=branch.project_id,
                branch_id=branch.pk,
                owner_id=owner.pk,
                expected_revision=expected_revision,
                status=ImportDraft.STATUS_READY,
                strip_root=effective_strip,
                manifest_path=str(manifest_path),
                file_count=sum(1 for e in manifest["entries"] if e["kind"] == "file"),
                total_size=manifest["total_uncompressed"],
                expires_at=timezone.now()
                + timedelta(minutes=limits["IMPORT_DRAFT_TTL_MINUTES"]),
            )
        zip_path.unlink(missing_ok=True)  # 原始 zip 不再需要，保留解出内容
        return draft
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise


def draft_manifest(draft: ImportDraft) -> dict:
    """读取服务器保存的原始清单；文件缺失按过期处理。"""
    try:
        return json.loads(Path(draft.manifest_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise NotFound("导入草稿已失效（临时清单不存在），请重新上传")


def preview_entries(draft: ImportDraft) -> list[dict]:
    manifest = draft_manifest(draft)
    return _manifest_entries(manifest, draft.strip_root)


def set_import_root_option(*, draft_id, owner, strip_root) -> ImportDraft:
    """切换“移除最外层目录”：按服务器原始清单重新推导，不改 revision。"""
    with transaction.atomic():
        draft = ImportDraft.objects.select_for_update().get(pk=draft_id)
        if draft.owner_id != owner.pk:
            raise NotFound("导入草稿不存在")
        if draft.status != ImportDraft.STATUS_READY:
            raise ValidationError("草稿已结束，不能修改选项")
        if timezone.now() > draft.expires_at:
            _expire(draft)
            raise ValidationError("草稿已过期，请重新上传")
        manifest = draft_manifest(draft)
        if strip_root and not manifest.get("common_root"):
            raise ValidationError("该压缩包没有共同外层目录，无法移除")
        draft.strip_root = bool(strip_root) and bool(manifest.get("common_root"))
        draft.save(update_fields=["strip_root"])
        return draft


def publish_import(*, draft_id, owner) -> Branch:
    """两步导入第二步：校验归属/有效期/未消费/revision 后整分支替换。"""
    draft = ImportDraft.objects.select_related("branch__project").filter(pk=draft_id).first()
    if draft is None or draft.owner_id != owner.pk:
        raise NotFound("导入草稿不存在")
    if draft.status != ImportDraft.STATUS_READY:
        raise ValidationError(f"草稿状态为 {draft.get_status_display()}，不能重复发布")

    if timezone.now() > draft.expires_at:
        _expire(draft)
        raise ValidationError("草稿已过期，请重新上传")

    manifest = draft_manifest(draft)
    entries = _manifest_entries(manifest, draft.strip_root)
    work_files = _draft_dir(draft.manifest_path) / "files"

    # 事务外持久化全部 Blob；失败时公开分支未受影响
    blobs = {}
    for e in entries:
        if e["kind"] == "file":
            with open(work_files / e["path"], "rb") as stream:
                blobs[e["path"]] = store_blob(stream, max_bytes=_limits()["MAX_FILE_BYTES"])

    # 冲突时不允许在 atomic 块内抛异常（会连取消标记一起回滚），
    # 也不允许拆成两个事务（锁会释放、校验与替换不再原子）：
    # 块内只置位标记，正常退出提交取消状态，块外再转译为 RevisionConflict。
    conflict = False
    with transaction.atomic():
        project = Project.objects.select_for_update().get(pk=draft.project_id)
        branch = (
            Branch.objects.select_for_update()
            .select_related("project")
            .get(pk=draft.branch_id)
        )
        locked_draft = ImportDraft.objects.select_for_update().get(pk=draft.pk)
        # 归属与并发校验（锁内复核）
        if branch.project_id != project.pk:
            raise NotFound("分支不存在")
        if locked_draft.status != ImportDraft.STATUS_READY:
            raise ValidationError("草稿已被处理，不能重复发布")
        if timezone.now() > locked_draft.expires_at:
            _expire(locked_draft)
            raise ValidationError("草稿已过期，请重新上传")

        if branch.revision != locked_draft.expected_revision:
            locked_draft.status = ImportDraft.STATUS_CANCELLED
            locked_draft.result = "分支内容已更新，导入已取消，请重新上传"
            locked_draft.save(update_fields=["status", "result"])
            conflict = True
        else:
            # 整分支替换：同事务内删旧插新
            Entry.objects.filter(branch=branch).delete()
            rows = []
            for e in entries:
                rows.append(
                    Entry(
                        branch=branch,
                        path=e["path"],
                        parent_path=e["path"].rsplit("/", 1)[0] if "/" in e["path"] else "",
                        name=e["path"].rsplit("/", 1)[-1],
                        kind=e["kind"],
                        size=e["size"],
                        blob=blobs.get(e["path"]),
                        updated_at=timezone.now(),
                    )
                )
            Entry.objects.bulk_create(rows)
            branch.revision += 1
            branch.save(update_fields=["revision", "updated_at"])
            Project.objects.filter(pk=project.pk).update(updated_at=timezone.now())
            locked_draft.status = ImportDraft.STATUS_PUBLISHED
            locked_draft.published_at = timezone.now()
            locked_draft.result = (
                f"已发布 {len(rows)} 个条目（{sum(e['size'] for e in entries)} 字节）"
            )
            locked_draft.save(update_fields=["status", "result", "published_at"])

    if conflict:
        raise RevisionConflict("分支内容已更新，本次导入已取消；请重新上传") from None

    shutil.rmtree(_draft_dir(draft.manifest_path), ignore_errors=True)
    return branch


def cancel_import(*, draft_id, owner) -> None:
    with transaction.atomic():
        draft = ImportDraft.objects.select_for_update().filter(pk=draft_id).first()
        if draft is None or draft.owner_id != owner.pk:
            raise NotFound("导入草稿不存在")
        if draft.status != ImportDraft.STATUS_READY:
            return  # 已结束的草稿取消是幂等操作
        draft.status = ImportDraft.STATUS_CANCELLED
        draft.result = "已取消"
        draft.save(update_fields=["status", "result"])
    if draft.manifest_path:
        shutil.rmtree(_draft_dir(draft.manifest_path), ignore_errors=True)


def _expire(draft) -> None:
    if draft.status == ImportDraft.STATUS_READY:
        draft.status = ImportDraft.STATUS_EXPIRED
        draft.result = "已过期"
        draft.save(update_fields=["status", "result"])
    if draft.manifest_path:
        shutil.rmtree(_draft_dir(draft.manifest_path), ignore_errors=True)
