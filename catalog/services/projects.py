"""分组与项目元数据服务：Admin 与后续流程的唯一写入口。

创建项目在同一事务内完成 default_branch=NULL 的项目、main 分支与
默认关联；删除项目先清空默认关联再级联，避免循环 PROTECT 阻塞。
"""
import re

from django.db import transaction

from catalog.models import Branch, Group, ImportDraft, Project
from catalog.services.exceptions import NotFound, ValidationError

RESERVED_SLUGS = {"admin", "api", "explore", "static", "healthz"}
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}[a-z0-9]$")


def slug_problem(slug: str) -> str | None:
    """返回 URL 标识的问题描述；合法返回 None。服务与表单共用。"""
    if not slug or not SLUG_RE.fullmatch(slug):
        return "URL 标识须为 2–50 位小写字母、数字和连字符，且首尾为字母或数字"
    if slug in RESERVED_SLUGS:
        return f"“{slug}”是系统保留标识，请换一个"
    return None


def _require_slug(slug: str) -> str:
    problem = slug_problem(slug)
    if problem:
        raise ValidationError(problem)
    return slug


def _lock(model, pk):
    obj = model.objects.select_for_update().filter(pk=pk).first()
    if obj is None:
        raise NotFound("对象不存在")
    return obj


# ---------- 分组 ----------

def create_group(*, name, slug, description="", icon="", sort_order=0) -> Group:
    name = (name or "").strip()
    if not 1 <= len(name) <= 40:
        raise ValidationError("分组名称须为 1–40 个字符")
    _require_slug(slug)
    with transaction.atomic():
        if Group.objects.filter(name=name).exists():
            raise ValidationError(f"分组名称 “{name}” 已存在")
        if Group.objects.filter(slug=slug).exists():
            raise ValidationError(f"URL 标识 “{slug}” 已被占用")
        return Group.objects.create(
            name=name,
            slug=slug,
            description=(description or "")[:200],
            icon=icon or "folder",
            sort_order=sort_order,
        )


def delete_group(*, group_id) -> None:
    """非空分组禁止删除；不级联删除项目。"""
    with transaction.atomic():
        group = _lock(Group, group_id)
        count = group.projects.count()
        if count:
            raise ValidationError(f"分组 “{group.name}” 下仍有 {count} 个项目，请先移动或删除这些项目")
        group.delete()


# ---------- 项目 ----------

def create_project(*, group, name, slug, description="", language="", license_name="") -> Project:
    """创建项目并自动生成默认 main 分支（同一事务）。"""
    name = (name or "").strip()
    if not 1 <= len(name) <= 80:
        raise ValidationError("项目名称须为 1–80 个字符")
    _require_slug(slug)
    with transaction.atomic():
        group = _lock(Group, group.pk)
        if Project.objects.filter(group=group, slug=slug).exists():
            raise ValidationError(f"该分组下已存在 URL 标识 “{slug}”")
        project = Project(
            group=group,
            name=name,
            slug=slug,
            description=(description or "")[:500],
            language=(language or "")[:64],
            license_name=(license_name or "")[:100],
            default_branch=None,  # 先建项目再关联，事务内完成
        )
        project.save()
        main = Branch.objects.create(project=project, name="main")
        Project.objects.filter(pk=project.pk).update(default_branch=main)
        project.refresh_from_db()
        return project


def move_project(*, project_id, target_group_id) -> Project:
    """移动到目标分组；组内 slug 冲突时拒绝，不产生半移动状态。"""
    with transaction.atomic():
        project = _lock(Project, project_id)
        target = _lock(Group, target_group_id)
        if project.group_id == target.pk:
            return project
        if (
            Project.objects.filter(group=target, slug=project.slug)
            .exclude(pk=project.pk)
            .exists()
        ):
            raise ValidationError(
                f"目标分组 “{target.name}” 下已存在 URL 标识 “{project.slug}”，无法移动"
            )
        project.group = target
        project.save(update_fields=["group", "updated_at"])
        return project


def delete_project(*, project_id) -> None:
    """删除项目、其全部分支与文件元数据；共享 Blob 对象不删除，由停机清理回收。

    ImportDraft 对项目为 PROTECT：任务 7 接入草稿后，删除前须先取消
    该项目的未完成草稿（当前阶段尚无草稿产生途径）。
    """
    with transaction.atomic():
        project = _lock(Project, project_id)
        # 先清空默认分支关联，解除 Project↔Branch 的循环 PROTECT；
        # 草稿对项目 PROTECT，已完结记录随项目删除
        Project.objects.filter(pk=project.pk, default_branch__isnull=False).update(default_branch=None)
        project.refresh_from_db()
        ImportDraft.objects.filter(project=project).delete()
        # Branch.project CASCADE → Entry.branch CASCADE，一并删除
        project.delete()
