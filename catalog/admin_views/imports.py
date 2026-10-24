"""ZIP 导入管理页：上传 → 预览确认 → 发布/取消（两步流程）。

预览始终从服务器保存的原始清单推导；选项切换走受保护 POST，
不修改 expected_revision、不写公开分支。错误状态码按设计：
413 超限、429 校验繁忙、400 其他输入错误。
"""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from catalog.models import Branch, ImportDraft, Project
from catalog.services import imports as import_service
from catalog.services.exceptions import (
    CapacityExceeded,
    NotFound,
    ServiceError,
)
from catalog.services.imports import ImportBusy


def _project(project_id) -> Project:
    return get_object_or_404(Project.objects.select_related("group"), pk=project_id)


def _branch(request, project):
    import uuid

    raw = request.GET.get("branch") or request.POST.get("branch") or ""
    try:
        return Branch.objects.select_related("project").get(
            pk=uuid.UUID(raw), project=project
        )
    except (ValueError, Branch.DoesNotExist):
        messages.error(request, "分支不存在或未指定。")
        return None


def _home_url(project, branch):
    return f"{reverse('admin:catalog-project-imports', args=[project.pk])}?branch={branch.pk}"


def _render_home(request, project, branch, status=200, **extra):
    drafts = (
        ImportDraft.objects.filter(project=project, owner=request.user)
        .select_related("branch")
        .order_by("-created_at")[:10]
    )
    context = {
        "project": project,
        "branch": branch,
        "revision": branch.revision,
        "drafts": drafts,
        "entry_count": branch.entries.count(),
        "public_root": f"/{project.group.slug}/{project.slug}/?branch={branch.pk}",
        **extra,
    }
    return render(request, "admin/catalog/import_home.html", context, status=status)


def import_home(request, project_id):
    project = _project(project_id)
    branch = _branch(request, project)
    if branch is None:
        return redirect(f"/admin/catalog/project/{project.pk}/branches/")
    return _render_home(request, project, branch)


def import_new(request, project_id):
    project = _project(project_id)
    branch = _branch(request, project)
    if branch is None:
        return redirect(f"/admin/catalog/project/{project.pk}/branches/")
    if request.method != "POST":
        return redirect(_home_url(project, branch))
    try:
        revision = int(request.POST.get("expected_revision") or "")
    except ValueError:
        revision = None
    try:
        draft = import_service.prepare_zip_import(
            project_id=project.pk,
            branch_id=branch.pk,
            expected_revision=revision,
            owner=request.user,
            uploaded_file=request.FILES.get("zip"),
            strip_root=True,
        )
        return redirect(
            reverse("admin:catalog-project-import-preview", args=[project.pk, draft.pk])
        )
    except ImportBusy as exc:
        return _render_home(request, project, branch, status=429, last_error=str(exc), busy=True)
    except CapacityExceeded as exc:
        return _render_home(request, project, branch, status=413, last_error=str(exc))
    except ServiceError as exc:
        return _render_home(request, project, branch, status=400, last_error=str(exc))


def _owned_draft(draft_id, user) -> ImportDraft:
    draft = ImportDraft.objects.select_related("branch__project", "branch").filter(
        pk=draft_id
    ).first()
    if draft is None or draft.owner_id != user.pk:
        raise NotFound("导入草稿不存在")
    return draft


def import_preview(request, project_id, draft_id):
    project = _project(project_id)
    try:
        draft = _owned_draft(draft_id, request.user)
    except NotFound:
        messages.error(request, "导入草稿不存在。")
        return redirect(f"/admin/catalog/project/{project.pk}/branches/")

    if draft.status != ImportDraft.STATUS_READY:
        return render(
            request,
            "admin/catalog/import_result.html",
            {"project": project, "draft": draft},
        )

    try:
        entries = import_service.preview_entries(draft)
        manifest = import_service.draft_manifest(draft)
    except NotFound as exc:
        messages.error(request, str(exc))
        return redirect(_home_url(project, draft.branch))

    files = [e for e in entries if e["kind"] == "file"]
    dirs = [e for e in entries if e["kind"] == "dir"]
    common_root = manifest.get("common_root")
    return render(
        request,
        "admin/catalog/import_preview.html",
        {
            "project": project,
            "draft": draft,
            "branch": draft.branch,
            "entries_preview": entries[:60],
            "more_count": max(len(entries) - 60, 0),
            "file_count": len(files),
            "dir_count": len(dirs),
            "total_size": sum(e["size"] for e in entries),
            "current_entries": draft.branch.entries.count(),
            "excluded": manifest.get("excluded", []),
            "common_root": common_root,
        },
    )


@require_POST
def import_option(request, project_id, draft_id):
    project = _project(project_id)
    try:
        import_service.set_import_root_option(
            draft_id=draft_id,
            owner=request.user,
            strip_root=request.POST.get("strip_root") == "1",
        )
    except ServiceError as exc:
        messages.error(request, str(exc))
    return redirect(
        reverse("admin:catalog-project-import-preview", args=[project.pk, draft_id])
    )


@require_POST
def import_publish(request, project_id, draft_id):
    project = _project(project_id)
    try:
        import_service.publish_import(draft_id=draft_id, owner=request.user)
        messages.success(request, "导入已发布。")
        draft = ImportDraft.objects.get(pk=draft_id)
        return redirect(f"/{draft.branch.project.group.slug}/{draft.branch.project.slug}/?branch={draft.branch_id}")
    except NotFound as exc:
        messages.error(request, str(exc))
        return redirect(f"/admin/catalog/project/{project.pk}/branches/")
    except ServiceError as exc:
        messages.error(request, str(exc))
        return redirect(
            reverse("admin:catalog-project-import-preview", args=[project.pk, draft_id])
        )


@require_POST
def import_cancel(request, project_id, draft_id):
    project = _project(project_id)
    try:
        import_service.cancel_import(draft_id=draft_id, owner=request.user)
        messages.success(request, "导入已取消，分支内容未受影响。")
    except ServiceError as exc:
        messages.error(request, str(exc))
    return redirect(f"/admin/catalog/project/{project.pk}/branches/")
