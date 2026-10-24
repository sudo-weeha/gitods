"""当前分支内的内容变更：上传、新建目录、重命名、删除。

统一流程：Blob 在事务外持久化；短事务内按 Project→Branch 加锁，
校验 expected_revision、父目录、冲突与分支容量后整体写入并递增
revision。失败整体回滚；事务外已写入的对象允许成为孤儿，由停机
清理回收（设计文档 5.3）。
"""
from django.conf import settings
from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone

from catalog.models import Branch, Entry, Project
from catalog.services.exceptions import (
    CapacityExceeded,
    NotFound,
    RevisionConflict,
    ValidationError,
)
from catalog.services.paths import join_path, normalize_name, normalize_path, parent_of
from catalog.services.storage import store_blob


class FileConflict(ValidationError):
    """同名冲突且未确认覆盖；携带冲突路径列表供表单展示。"""

    def __init__(self, conflicts):
        super().__init__("以下文件已存在，需勾选“覆盖同名文件”后重新上传")
        self.conflicts = conflicts


def _limits() -> dict:
    return settings.GITODS_LIMITS


def _lock_pair(branch_id):
    """锁顺序恒为 Project → Branch（设计文档 5.3）。"""
    owner_id = Branch.objects.filter(pk=branch_id).values_list("project_id", flat=True).first()
    if owner_id is None:
        raise NotFound("分支不存在")
    Project.objects.select_for_update().get(pk=owner_id)
    branch = (
        Branch.objects.select_for_update().select_related("project").get(pk=branch_id)
    )
    return branch.project, branch


def _check_revision(branch, expected_revision):
    if expected_revision is None or branch.revision != expected_revision:
        raise RevisionConflict("分支内容已更新，请刷新页面后重试")


def _touch(project, branch):
    branch.revision += 1
    branch.save(update_fields=["revision", "updated_at"])
    Project.objects.filter(pk=project.pk).update(updated_at=timezone.now())


def _require_parent_dir(branch, parent_path):
    if parent_path == "":
        return
    exists = Entry.objects.filter(branch=branch, path=parent_path, kind="dir").exists()
    if not exists:
        raise ValidationError(f"目标目录 “{parent_path}” 不存在或不是目录")


def _check_branch_capacity(branch, extra_bytes: int, extra_count: int):
    limits = _limits()
    stats = Entry.objects.filter(branch=branch).aggregate(
        total=Sum("size"), count=Count("id")
    )
    if (stats["total"] or 0) + extra_bytes > limits["MAX_BRANCH_BYTES"]:
        raise CapacityExceeded("分支逻辑总大小超过上限")
    if stats["count"] + extra_count > limits["MAX_BRANCH_ENTRIES"]:
        raise CapacityExceeded("分支条目总数超过上限（目录计入条目数）")


def upload_files(*, branch_id, expected_revision, parent_path, files, overwrite=False) -> Branch:
    """批量上传到当前目录；同名默认拒绝，确认覆盖后替换（全成或全败）。"""
    if not files:
        raise ValidationError("没有选择任何文件")
    parent_path = normalize_path(parent_path or "")
    limits = _limits()

    # ---- 事务外：名称规范化、批内去重、声明上限粗检、Blob 持久化 ----
    prepared = []
    batch_total = 0
    seen_paths = set()
    for f in files:
        raw_name = (getattr(f, "name", "") or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        name = normalize_name(raw_name)
        path = join_path(parent_path, name)
        if path in seen_paths:
            raise ValidationError(f"本次上传包含重复文件名：{name}")
        seen_paths.add(path)
        declared = getattr(f, "size", None) or 0
        if declared > limits["MAX_FILE_BYTES"]:
            raise CapacityExceeded(f"“{name}” 超过单文件大小上限")
        batch_total += declared
        if batch_total > limits["MAX_UPLOAD_BATCH_BYTES"]:
            raise CapacityExceeded("单次上传合计大小超过上限")
        prepared.append((name, path, f))

    branch_probe = Branch.objects.filter(pk=branch_id).first()
    if branch_probe is None:
        raise NotFound("分支不存在")
    _check_branch_capacity(branch_probe, batch_total, len(prepared))

    stored = [
        (name, path, store_blob(f, max_bytes=limits["MAX_FILE_BYTES"]))
        for name, path, f in prepared
    ]

    # ---- 短事务：锁、revision、父目录、冲突、容量复核、写入 ----
    with transaction.atomic():
        project, branch = _lock_pair(branch_id)
        _check_revision(branch, expected_revision)
        _require_parent_dir(branch, parent_path)
        _check_branch_capacity(
            branch, sum(b.size for _, _, b in stored), len(stored)
        )

        existing = {
            e.path: e
            for e in Entry.objects.filter(branch=branch, path__in=[p for _, p, _ in stored])
        }
        conflicts = [p for _, p, _ in stored if p in existing]
        if conflicts and not overwrite:
            raise FileConflict(conflicts)

        for name, path, blob in stored:
            entry = existing.get(path)
            if entry is not None:  # 覆盖：换内容对象引用，旧对象留给其他分支/下载
                entry.blob = blob
                entry.size = blob.size
                entry.updated_at = timezone.now()
                entry.save(update_fields=["blob", "size", "updated_at"])
            else:
                Entry.objects.create(
                    branch=branch,
                    path=path,
                    parent_path=parent_path,
                    name=name,
                    kind="file",
                    blob=blob,
                    size=blob.size,
                )
        _touch(project, branch)
        return branch


def create_folder(*, branch_id, expected_revision, parent_path, name) -> Entry:
    """在当前目录显式创建空目录。"""
    parent_path = normalize_path(parent_path or "")
    name = normalize_name(name)
    path = join_path(parent_path, name)
    with transaction.atomic():
        project, branch = _lock_pair(branch_id)
        _check_revision(branch, expected_revision)
        _require_parent_dir(branch, parent_path)
        if Entry.objects.filter(branch=branch, path=path).exists():
            raise ValidationError(f"“{name}” 已存在于该目录")
        _check_branch_capacity(branch, 0, 1)
        entry = Entry.objects.create(
            branch=branch, path=path, parent_path=parent_path, name=name, kind="dir", size=0
        )
        _touch(project, branch)
        return entry


def rename_entry(*, branch_id, expected_revision, path, new_name) -> Entry:
    """同一父目录内改名；目录改名同步更新全部后代路径。"""
    path = normalize_path(path)
    new_name = normalize_name(new_name)
    with transaction.atomic():
        project, branch = _lock_pair(branch_id)
        _check_revision(branch, expected_revision)
        entry = Entry.objects.select_for_update().filter(branch=branch, path=path).first()
        if entry is None:
            raise NotFound("路径不存在")
        if entry.name == new_name:
            return entry

        new_path = join_path(entry.parent_path, new_name)
        if (
            Entry.objects.filter(branch=branch, path=new_path)
            .exclude(pk=entry.pk)
            .exists()
        ):
            raise ValidationError(f"“{new_name}” 已存在于该目录")

        max_length = _limits()["MAX_PATH_LENGTH"]
        if entry.kind == "dir":
            prefix = path + "/"
            descendants = list(
                Entry.objects.filter(branch=branch, path__startswith=prefix)
            )
            for d in descendants:
                d.path = new_path + "/" + d.path[len(prefix):]
                if d.parent_path == path:
                    d.parent_path = new_path
                elif d.parent_path.startswith(prefix):
                    d.parent_path = new_path + "/" + d.parent_path[len(prefix):]
                if len(d.path) > max_length:
                    raise ValidationError("改名后路径过长")
            Entry.objects.bulk_update(descendants, ["path", "parent_path"])

        entry.name = new_name
        entry.path = new_path
        entry.updated_at = timezone.now()
        entry.save(update_fields=["name", "path", "updated_at"])
        _touch(project, branch)
        return entry


def delete_entry(*, branch_id, expected_revision, path) -> None:
    """删除文件或目录（含全部后代）；共享 Blob 不在线清理。"""
    path = normalize_path(path)
    with transaction.atomic():
        project, branch = _lock_pair(branch_id)
        _check_revision(branch, expected_revision)
        entry = Entry.objects.filter(branch=branch, path=path).first()
        if entry is None:
            raise NotFound("路径不存在")
        if entry.kind == "dir":
            Entry.objects.filter(branch=branch, path__startswith=path + "/").delete()
        entry.delete()
        _touch(project, branch)
