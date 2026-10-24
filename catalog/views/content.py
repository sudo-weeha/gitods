"""raw 视图与单文件下载：纯文本 inline、二进制附件，统一 nosniff。

受控图片预览（Pillow 验证）与完整类型校验在任务 5/8 完善。
"""
from pathlib import Path

from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse

from catalog import selectors
from catalog.models import Branch, Project
from catalog.services.storage import object_path

TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".py", ".js", ".ts", ".tsx", ".jsx",
    ".java", ".go", ".rs", ".kt", ".kts", ".c", ".h", ".cpp", ".hpp", ".cs", ".rb",
    ".php", ".swift", ".vue", ".svelte", ".html", ".htm", ".css", ".scss", ".less",
    ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".env", ".sh", ".bat",
    ".ps1", ".sql", ".xml", ".svg", ".gitignore", ".dockerfile", ".tf", ".proto",
}


def _safe_filename(name: str) -> str:
    # RFC 5987 编码中文文件名；参考 Django FileResponse 的附件名处理
    try:
        name.encode("ascii")
        return f'attachment; filename="{name}"'
    except UnicodeEncodeError:
        from urllib.parse import quote

        quoted = quote(name)
        return f"attachment; filename*=UTF-8''{quoted}"


def raw_file(request, group_slug, project_slug, rel_path):
    project = get_object_or_404(
        Project.objects.select_related("group"), group__slug=group_slug, slug=project_slug
    )
    raw_branch = request.GET.get("branch", "")
    if not raw_branch:
        target = reverse(
            "project-raw", args=[group_slug, project_slug, rel_path]
        )
        return redirect_with_default(request, project, target)

    try:
        import uuid as _uuid

        branch = Branch.objects.get(pk=_uuid.UUID(raw_branch), project=project)
    except (ValueError, Branch.DoesNotExist):
        raise Http404("分支不存在")

    entry = selectors.get_entry(branch, rel_path.strip("/"))
    if entry is None or entry.kind != "file":
        raise Http404("路径不存在")

    suffix = Path(entry.name).suffix.lower()
    as_download = request.GET.get("dl") == "1"
    is_text = suffix in TEXT_SUFFIXES

    response = FileResponse(
        open(object_path(entry.blob.sha256), "rb"),
        content_type="text/plain; charset=utf-8" if is_text else "application/octet-stream",
    )
    if as_download or not is_text:
        response["Content-Disposition"] = _safe_filename(entry.name)
    response["X-Content-Type-Options"] = "nosniff"
    return response


def redirect_with_default(request, project, target):
    from django.shortcuts import redirect

    if project.default_branch is None:
        raise Http404("项目暂无分支")
    return redirect(f"{target}?branch={project.default_branch.pk}")
