"""项目目录与源码页：同一页头、分支工具栏与简介栏，目录/源码/缺失三态。

- 未指定分支的入口解析默认分支并临时重定向到带稳定分支 ID 的地址；
  非法或跨项目分支 ID 返回 404
- 同一响应的多个元数据查询使用短 REPEATABLE READ READ ONLY 快照，
  事务结束后才读取不可变对象内容
"""
import uuid

from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse

from catalog import selectors
from catalog.models import Branch, Project
from catalog.services import rendering
from catalog.services.exceptions import ValidationError
from catalog.services.paths import normalize_path
from catalog.services.storage import read_object_bytes

MARKDOWN_EXTS = {".md", ".markdown"}


def _get_project(group_slug, project_slug) -> Project:
    project = selectors.get_project(group_slug, project_slug)
    if project is None:
        raise Http404("项目不存在")
    return project


def _location_url(project, mode, rel_path) -> str:
    args = [project.group.slug, project.slug]
    if mode == "blob":
        return reverse("project-blob", args=args + [rel_path])
    if rel_path:
        return reverse("project-tree", args=args + [rel_path])
    return reverse("project-home", args=args)


def _resolve_branch(request, project, mode, rel_path):
    """返回 (branch, redirect)；redirect 非空时视图直接返回它。"""
    raw = request.GET.get("branch", "")
    if raw:
        try:
            branch_id = uuid.UUID(raw)
        except ValueError:
            raise Http404("分支不存在")
        branch = Branch.objects.filter(pk=branch_id, project=project).first()
        if branch is None:
            raise Http404("分支不存在")
        return branch, None
    default = project.default_branch
    if default is None:
        raise Http404("项目暂无分支")
    return None, redirect(f"{_location_url(project, mode, rel_path)}?branch={default.pk}")


def _normalize_or_missing(request, project, branch, rel_path, render_missing):
    try:
        return normalize_path(rel_path), None
    except ValidationError:
        return None, render_missing(request, project, branch, rel_path)


def _breadcrumbs(project, branch, rel_path):
    """面包屑：根为项目名，逐段可点击；末段为当前位置不加链接。"""
    crumbs = [
        {
            "name": project.slug,
            "href": f"{_location_url(project, 'tree', '')}?branch={branch.pk}",
        }
    ]
    if rel_path:
        parts = rel_path.split("/")
        for i, part in enumerate(parts):
            is_last = i == len(parts) - 1
            if is_last:
                crumbs.append({"name": part, "href": None})
            else:
                sub = "/".join(parts[: i + 1])
                crumbs.append(
                    {
                        "name": part,
                        "href": f"{reverse('project-tree', args=[project.group.slug, project.slug, sub])}?branch={branch.pk}",
                    }
                )
    return crumbs


def _base_context(request, project, branch, mode, rel_path, branches, file_count):
    return {
        "nav": "project",
        "project": project,
        "group": project.group,
        "branch": branch,
        "branches": branches,
        "branch_count": len(branches),
        "is_default_branch": branch.pk == project.default_branch_id,
        "file_count": file_count,
        "is_staff": request.user.is_staff,
        "mode": mode,
        "rel_path": rel_path,
        "branch_switch_base": _location_url(project, mode if mode != "missing" else "tree", rel_path),
    }


def _render(request, project, branch, mode, rel_path, branches, file_count, **extra):
    context = _base_context(request, project, branch, mode, rel_path, branches, file_count)
    context.update(extra)
    return render(request, "catalog/project.html", context)


def _missing(request, project, branch, rel_path, branches=None, file_count=None):
    """路径缺失状态：保留分支与路径上下文，不静默显示其他文件。"""
    if branches is None:
        with selectors.readonly_snapshot():
            branches = selectors.project_branches(project)
            file_count = selectors.count_entries(branch)
    return _render(
        request,
        project,
        branch,
        "missing",
        rel_path,
        branches,
        file_count,
        breadcrumbs=_breadcrumbs(project, branch, rel_path),
    )


def project_root(request, group_slug, project_slug):
    return _tree(request, group_slug, project_slug, "")


def project_tree(request, group_slug, project_slug, rel_path):
    return _tree(request, group_slug, project_slug, rel_path)


def _tree(request, group_slug, project_slug, rel_path):
    project = _get_project(group_slug, project_slug)
    branch, redirect_response = _resolve_branch(request, project, "tree", rel_path)
    if redirect_response:
        return redirect_response

    rel_path, missing = _normalize_or_missing(request, project, branch, rel_path.strip("/"), _missing)
    if missing:
        return missing

    entry = selectors.get_entry(branch, rel_path) if rel_path else None
    if rel_path and entry is None:
        return _missing(request, project, branch, rel_path)
    if entry is not None and entry.kind == "file":
        return redirect(f"{reverse('project-blob', args=[group_slug, project_slug, rel_path])}?branch={branch.pk}")

    with selectors.readonly_snapshot():
        listing = selectors.list_directory(branch, rel_path)
        branches = selectors.project_branches(project)
        file_count = selectors.count_entries(branch)
        readme = selectors.find_readme(branch) if not rel_path else None
        readme_sha = readme.blob.sha256 if readme is not None else None
        readme_error = False

    readme_html = None
    if readme_sha is not None:
        try:
            data = read_object_bytes(readme_sha)
            readme_html = rendering.render_markdown(data.decode("utf-8"), branch, readme.path).html
        except UnicodeDecodeError:
            readme_error = True

    return _render(
        request,
        project,
        branch,
        "tree",
        rel_path,
        branches,
        file_count,
        listing=listing,
        readme=readme,
        readme_html=readme_html,
        readme_error=readme_error,
        breadcrumbs=_breadcrumbs(project, branch, rel_path),
    )


def project_blob(request, group_slug, project_slug, rel_path):
    project = _get_project(group_slug, project_slug)
    branch, redirect_response = _resolve_branch(request, project, "blob", rel_path)
    if redirect_response:
        return redirect_response

    rel_path, missing = _normalize_or_missing(request, project, branch, rel_path.strip("/"), _missing)
    if missing:
        return missing

    with selectors.readonly_snapshot():
        entry = selectors.get_entry(branch, rel_path)
        branches = selectors.project_branches(project)
        file_count = selectors.count_entries(branch)

    if entry is None:
        return _missing(request, project, branch, rel_path, branches, file_count)
    if entry.kind == "dir":
        return redirect(f"{reverse('project-tree', args=[group_slug, project_slug, rel_path])}?branch={branch.pk}")

    if entry.size == 0:
        rendered = rendering.Rendered(line_count=0)
        data = b""
    else:
        data = read_object_bytes(entry.blob.sha256)
        rendered = rendering.render_source(data, entry.name)

    markdown_html = None
    is_markdown = entry.name.lower().endswith(tuple(MARKDOWN_EXTS))
    if is_markdown and not rendered.binary and not rendered.too_large:
        markdown_html = rendering.render_markdown(data.decode("utf-8"), branch, entry.path).html

    raw_base = reverse("project-raw", args=[group_slug, project_slug, rel_path])
    return _render(
        request,
        project,
        branch,
        "blob",
        rel_path,
        branches,
        file_count,
        entry=entry,
        rendered=rendered,
        markdown_html=markdown_html,
        is_markdown=is_markdown,
        show_source=request.GET.get("view") == "source",
        raw_url=f"{raw_base}?branch={branch.pk}",
        raw_download_url=f"{raw_base}?branch={branch.pk}&dl=1",
        breadcrumbs=_breadcrumbs(project, branch, rel_path),
    )
