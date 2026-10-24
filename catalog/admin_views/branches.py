"""分支管理视图：列表、创建（空/复制）、设默认、删除确认；写入全部 POST+CSRF。

经 catalog.admin_site 挂载到 /admin/catalog/project/<UUID>/branches/...，
视图由 GitODSAdminSite.get_urls 注入并统一 admin_view 保护。
"""
from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from catalog.forms import BranchCreateForm
from catalog.models import Branch, Project
from catalog.services import branches as branch_service
from catalog.services.exceptions import NotFound, ServiceError


def _project(project_id) -> Project:
    return get_object_or_404(Project.objects.select_related("group"), pk=project_id)


def _list_url(project):
    return f"/admin/catalog/project/{project.pk}/branches/"


def branch_list(request, project_id):
    project = _project(project_id)
    branches = list(
        Branch.objects.filter(project=project)
        .annotate(entry_count=Count("entries"))
        .order_by("created_at", "id")
    )
    form = BranchCreateForm()
    return render(
        request,
        "admin/catalog/branch_list.html",
        {
            "project": project,
            "branches": branches,
            "form": form,
        },
    )


def branch_create(request, project_id):
    project = _project(project_id)
    if request.method != "POST":
        return redirect(_list_url(project))
    form = BranchCreateForm(request.POST)
    source_branch_id = form.data.get("source_branch") or None
    expected_revision = form.data.get("expected_revision") or None
    if form.is_valid():
        try:
            branch = branch_service.create_branch(
                project_id=project.pk,
                name=form.cleaned_data["name"],
                source_branch_id=form.cleaned_data.get("source_branch"),
                expected_revision=form.cleaned_data.get("expected_revision"),
            )
            messages.success(request, f"分支 “{branch.name}” 已创建。")
            return redirect(_list_url(project))
        except ServiceError as exc:
            form.add_error(None, str(exc))
    branches = list(
        Branch.objects.filter(project=project)
        .annotate(entry_count=Count("entries"))
        .order_by("created_at", "id")
    )
    return render(
        request,
        "admin/catalog/branch_list.html",
        {"project": project, "branches": branches, "form": form},
    )


@require_POST
def branch_set_default(request, project_id, branch_id):
    project = _project(project_id)
    try:
        branch_service.set_default_branch(project_id=project.pk, branch_id=branch_id)
        messages.success(request, "默认分支已更新。")
    except ServiceError as exc:
        messages.error(request, str(exc))
    return redirect(_list_url(project))


def branch_delete(request, project_id, branch_id):
    project = _project(project_id)
    branch = get_object_or_404(Branch, pk=branch_id, project=project)

    if request.method == "POST":
        try:
            branch_service.delete_branch(project_id=project.pk, branch_id=branch.pk)
            messages.success(request, f"分支 “{branch.name}” 已删除。")
        except ServiceError as exc:
            messages.error(request, str(exc))
        # 返回来源页；来源指向已删除分支时回到项目默认根目录
        next_url = request.POST.get("next", "")
        if next_url:
            if next_url.startswith("/") and str(branch_id) not in next_url:
                return redirect(next_url)
            return redirect(f"/{project.group.slug}/{project.slug}/")
        return redirect(_list_url(project))

    # GET：确认页，不产生副作用
    blockers = []
    if project.default_branch_id == branch.pk:
        blockers.append("这是默认分支，请先设置其他默认分支")
    if project.branches.count() <= 1:
        blockers.append("项目至少保留一个分支")
    return render(
        request,
        "admin/catalog/branch_delete.html",
        {
            "project": project,
            "branch": branch,
            "entry_count": branch.entries.count(),
            "blockers": blockers,
            "next": request.GET.get("next", ""),
        },
    )
