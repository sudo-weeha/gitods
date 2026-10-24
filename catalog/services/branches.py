"""分支服务：实际独立分支与统一项目→分支锁顺序。

- 所有变更先锁 Project 再锁涉及的 Branch；跨分支按 UUID 排序
- 复制只复制 Entry 元数据行（新 ID），共享不可变 Blob，不保留任何修改历史
- revision 仅用于并发校验；默认分支与最后一个分支禁止删除
"""
import re

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from catalog.models import Branch, Entry, ImportDraft, Project
from catalog.services.exceptions import (
    CapacityExceeded,
    NotFound,
    RevisionConflict,
    ValidationError,
)

BRANCH_NAME_RE = re.compile(r"^(?!.*\.\.)[a-z0-9][a-z0-9._-]{0,63}$")


def branch_name_problem(name: str) -> str | None:
    """返回分支名的问题描述；合法返回 None。"""
    if not name or not BRANCH_NAME_RE.fullmatch(name):
        return (
            "分支名须为 1–64 位小写字母、数字、点、下划线或连字符，"
            "以字母或数字开头，且不允许连续两个点"
        )
    return None


def _lock_project(project_id) -> Project:
    project = Project.objects.select_for_update().filter(pk=project_id).first()
    if project is None:
        raise NotFound("项目不存在")
    return project


def _lock_branch(branch_id, *, project=None) -> Branch:
    qs = Branch.objects.select_for_update()
    if project is not None:
        qs = qs.filter(project=project)
    branch = qs.filter(pk=branch_id).first()
    if branch is None:
        raise NotFound("分支不存在")
    if project is not None and branch.project_id != project.pk:
        raise NotFound("分支不存在")  # 跨项目分支 ID 拒绝，不暴露存在性
    return branch


def create_branch(*, project_id, name, source_branch_id=None, expected_revision=None) -> Branch:
    """创建空分支，或复制现有分支的当前文件集合（元数据独立、共享对象）。"""
    problem = branch_name_problem(name)
    if problem:
        raise ValidationError(problem)

    with transaction.atomic():
        project = _lock_project(project_id)
        limit = settings.GITODS_LIMITS["MAX_BRANCHES_PER_PROJECT"]
        if project.branches.count() >= limit:
            raise CapacityExceeded(f"每个项目最多 {limit} 个分支")

        if Branch.objects.filter(project=project, name=name).exists():
            raise ValidationError(f"分支名 “{name}” 已存在")

        source = None
        if source_branch_id is not None:
            source = _lock_branch(source_branch_id, project=project)
            if expected_revision is not None and source.revision != expected_revision:
                raise RevisionConflict("源分支内容已更新，请刷新后重试")

        branch = Branch.objects.create(project=project, name=name)
        if source is not None:
            source_entries = list(Entry.objects.filter(branch=source))
            Entry.objects.bulk_create(
                Entry(
                    branch=branch,
                    path=e.path,
                    parent_path=e.parent_path,
                    name=e.name,
                    kind=e.kind,
                    size=e.size,
                    blob_id=e.blob_id,
                    updated_at=e.updated_at,
                )
                for e in source_entries
            )
        # 分支变化刷新项目更新时间（设计文档 4.2）
        Project.objects.filter(pk=project.pk).update(updated_at=timezone.now())
        return branch


def set_default_branch(*, project_id, branch_id) -> Project:
    with transaction.atomic():
        project = _lock_project(project_id)
        branch = _lock_branch(branch_id, project=project)
        Project.objects.filter(pk=project.pk).exclude(default_branch=branch).update(
            default_branch=branch, updated_at=timezone.now()
        )
        project.refresh_from_db()
        return project


def delete_branch(*, project_id, branch_id) -> None:
    """删除非默认且非最后一个分支；其 Entry 级联删除，共享 Blob 保留。"""
    with transaction.atomic():
        project = _lock_project(project_id)
        branch = _lock_branch(branch_id, project=project)
        # 先查最后一个分支，再查默认分支：唯一分支必然是默认分支，
        # “最后一个分支”是更准确的原因描述
        if project.branches.count() <= 1:
            raise ValidationError("最后一个分支不能删除")
        if project.default_branch_id == branch.pk:
            raise ValidationError("默认分支不能删除；请先设置其他默认分支")
        # 已完结草稿仅是记录（临时清单已清理），随分支删除；避免 PROTECT 阻塞
        ImportDraft.objects.filter(branch=branch).delete()
        branch.delete()
        Project.objects.filter(pk=project.pk).update(updated_at=timezone.now())
