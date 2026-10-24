"""文件维护管理页：上传（含覆盖确认）、新建目录、重命名、删除确认。

GET 仅展示与确认，不产生副作用；全部写入 POST+CSRF。
expected_revision 随页面下发，提交时由服务层复核。
"""
from urllib.parse import quote

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from catalog import selectors
from catalog.models import Branch, Entry, Project
from catalog.services import entries as entry_service
from catalog.services.entries import FileConflict
from catalog.services.exceptions import NotFound, ServiceError
from catalog.services.paths import normalize_path, parent_of


def _project(project_id) -> Project:
    return get_object_or_404(Project.objects.select_related("group"), pk=project_id)


def _branch(request, project):
    raw = request.GET.get("branch") or request.POST.get("branch") or ""
    try:
        import uuid

        branch = Branch.objects.select_related("project").get(pk=uuid.UUID(raw), project=project)
    except (ValueError, Branch.DoesNotExist):
        messages.error(request, "分支不存在或未指定。")
        return None
    return branch


def _path(request):
    raw = request.GET.get("path") or request.POST.get("path") or ""
    try:
        return normalize_path(raw.strip("/"))
    except ServiceError:
        return ""


def _manage_url(project, branch, path):
    url = reverse("admin:catalog-project-files", args=[project.pk])
    return f"{url}?branch={branch.pk}" + (f"&path={quote(path)}" if path else "")


def _public_url(project, branch, path):
    args = [project.group.slug, project.slug]
    url = reverse("project-tree", args=args + [path]) if path else reverse("project-home", args=args)
    return f"{url}?branch={branch.pk}"


def _revision(request):
    raw = request.POST.get("expected_revision") or ""
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _manage_context(request, project, branch, path, **extra):
    listing = selectors.list_directory(branch, path)
    context = {
        "project": project,
        "branch": branch,
        "path": path,
        "parent_path": parent_of(path),
        "listing": listing,
        "revision": branch.revision,
        "public_root": f"/{project.group.slug}/{project.slug}/?branch={branch.pk}",
        **extra,
    }
    context.update(extra)
    return context


def files_manage(request, project_id):
    project = _project(project_id)
    branch = _branch(request, project)
    if branch is None:
        return redirect(f"/admin/catalog/project/{project.pk}/branches/")
    path = _path(request)
    if path:
        entry = selectors.get_entry(branch, path)
        if entry is None or entry.kind != "dir":
            messages.warning(request, f"目录 “{path}” 不存在，已回到根目录。")
            return redirect(_manage_url(project, branch, ""))
    return render(
        request,
        "admin/catalog/files_manage.html",
        _manage_context(request, project, branch, path),
    )


def files_upload(request, project_id):
    project = _project(project_id)
    branch = _branch(request, project)
    if branch is None:
        return redirect(f"/admin/catalog/project/{project.pk}/branches/")
    path = _path(request)
    if request.method != "POST":
        return redirect(_manage_url(project, branch, path))
    files = request.FILES.getlist("files")
    overwrite = request.POST.get("overwrite") == "1"
    try:
        entry_service.upload_files(
            branch_id=branch.pk,
            expected_revision=_revision(request),
            parent_path=path,
            files=files,
            overwrite=overwrite,
        )
        messages.success(request, f"已上传 {len(files)} 个文件。")
        return redirect(_public_url(project, branch, path))
    except FileConflict as exc:
        return render(
            request,
            "admin/catalog/files_manage.html",
            _manage_context(
                request, project, branch, path,
                conflict_files=exc.conflicts, last_error=str(exc),
            ),
        )
    except ServiceError as exc:
        return render(
            request,
            "admin/catalog/files_manage.html",
            _manage_context(request, project, branch, path, last_error=str(exc)),
        )


@require_POST
def files_folder(request, project_id):
    project = _project(project_id)
    branch = _branch(request, project)
    if branch is None:
        return redirect(f"/admin/catalog/project/{project.pk}/branches/")
    path = _path(request)
    try:
        entry_service.create_folder(
            branch_id=branch.pk,
            expected_revision=_revision(request),
            parent_path=path,
            name=request.POST.get("name", ""),
        )
        messages.success(request, "目录已创建。")
        return redirect(_public_url(project, branch, path))
    except ServiceError as exc:
        messages.error(request, str(exc))
        return redirect(_manage_url(project, branch, path))


def _entry_or_404(branch, entry_id) -> Entry:
    import uuid

    try:
        pk = uuid.UUID(str(entry_id))
    except ValueError:
        raise NotFound("条目不存在")
    entry = Entry.objects.filter(branch=branch, pk=pk).first()
    if entry is None:
        raise NotFound("条目不存在")
    return entry


def files_rename(request, project_id, entry_id):
    project = _project(project_id)
    branch = _branch(request, project)
    if branch is None:
        return redirect(f"/admin/catalog/project/{project.pk}/branches/")
    path = _path(request)
    try:
        entry = _entry_or_404(branch, entry_id)
    except NotFound:
        messages.error(request, "条目不存在，可能已被删除。")
        return redirect(_manage_url(project, branch, path))

    if request.method == "POST":
        try:
            updated = entry_service.rename_entry(
                branch_id=branch.pk,
                expected_revision=_revision(request),
                path=entry.path,
                new_name=request.POST.get("new_name", ""),
            )
            messages.success(request, f"已重命名为 “{updated.name}”。")
            return redirect(_manage_url(project, branch, parent_of(updated.path)))
        except ServiceError as exc:
            messages.error(request, str(exc))
            return redirect(_manage_url(project, branch, path))

    # GET：确认表单（无副作用）
    return render(
        request,
        "admin/catalog/files_rename.html",
        {
            "project": project,
            "branch": branch,
            "path": path,
            "entry": entry,
            "revision": branch.revision,
        },
    )


def files_delete(request, project_id, entry_id):
    project = _project(project_id)
    branch = _branch(request, project)
    if branch is None:
        return redirect(f"/admin/catalog/project/{project.pk}/branches/")
    path = _path(request)
    try:
        entry = _entry_or_404(branch, entry_id)
    except NotFound:
        messages.error(request, "条目不存在，可能已被删除。")
        return redirect(_manage_url(project, branch, path))

    if request.method == "POST":
        try:
            entry_service.delete_entry(
                branch_id=branch.pk,
                expected_revision=_revision(request),
                path=entry.path,
            )
            messages.success(request, f"已删除 “{entry.name}”。")
        except ServiceError as exc:
            messages.error(request, str(exc))
        # 当前浏览目录被删除时，回到最近仍存在的父目录
        target = path if path != entry.path else parent_of(path)
        while target:
            still = selectors.get_entry(branch, target)
            if still is not None and still.kind == "dir":
                break
            target = parent_of(target)
        return redirect(_public_url(project, branch, target))

    # GET：确认页，列明路径与受影响文件数量
    descendant_count = (
        Entry.objects.filter(branch=branch, path__startswith=entry.path + "/").count()
        if entry.kind == "dir"
        else 0
    )
    return render(
        request,
        "admin/catalog/files_delete.html",
        {
            "project": project,
            "branch": branch,
            "path": path,
            "entry": entry,
            "descendant_count": descendant_count,
            "revision": branch.revision,
        },
    )
