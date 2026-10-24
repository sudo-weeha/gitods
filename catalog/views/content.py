"""raw 视图、单文件下载与当前分支 ZIP 导出。

读取流程恒为「项目+分支+逻辑路径 → Entry → Blob → sha256 派生对象键」，
URL 路径从不拼接服务器磁盘路径。受控图片（Pillow 验证）在任务 8 接入。
"""
import uuid
from pathlib import Path

from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse

from catalog import selectors
from catalog.models import Branch, Project
from catalog.services import downloads as download_service
from catalog.services.exceptions import ValidationError
from catalog.services.paths import normalize_path
from catalog.services.storage import object_path

TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".py", ".js", ".ts", ".tsx", ".jsx",
    ".java", ".go", ".rs", ".kt", ".kts", ".c", ".h", ".cpp", ".hpp", ".cs", ".rb",
    ".php", ".swift", ".vue", ".svelte", ".html", ".htm", ".css", ".scss", ".less",
    ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".sh", ".bat",
    ".ps1", ".sql", ".xml", ".svg", ".gitignore", ".dockerfile", ".tf", ".proto",
}


def _safe_disposition(name: str) -> str:
    # RFC 5987 编码中文文件名，参考 Django FileResponse 的附件名处理
    try:
        name.encode("ascii")
        return f'attachment; filename="{name}"'
    except UnicodeEncodeError:
        from urllib.parse import quote

        return f"attachment; filename*=UTF-8''{quote(name)}"


def raw_file(request, group_slug, project_slug, rel_path):
    project = get_object_or_404(
        Project.objects.select_related("group", "default_branch"),
        group__slug=group_slug,
        slug=project_slug,
    )

    raw_branch = request.GET.get("branch", "")
    if not raw_branch:
        target = reverse("project-raw", args=[group_slug, project_slug, rel_path])
        default = project.default_branch
        if default is None:
            raise Http404("项目暂无分支")
        return redirect(f"{target}?branch={default.pk}")

    try:
        branch = Branch.objects.get(pk=uuid.UUID(raw_branch), project=project)
    except (ValueError, Branch.DoesNotExist):
        raise Http404("分支不存在")

    try:
        path = normalize_path(rel_path.strip("/"))
    except ValidationError:
        raise Http404("路径不存在")

    entry = selectors.get_entry(branch, path)
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
        response["Content-Disposition"] = _safe_disposition(entry.name)
    response["X-Content-Type-Options"] = "nosniff"
    return response


class _DeletedOnClose:
    """发送完自动删除的临时文件包装（FileResponse 关闭时调用 close）。"""

    def __init__(self, path):
        self._path = Path(path)
        self._file = open(path, "rb")

    def read(self, *args):
        return self._file.read(*args)

    def close(self):
        try:
            self._file.close()
        finally:
            self._path.unlink(missing_ok=True)


def branch_download(request, group_slug, project_slug):
    """导出当前分支全部文件与空目录为 ZIP；不含数据库与其他分支。"""
    project = get_object_or_404(
        Project.objects.select_related("group", "default_branch"),
        group__slug=group_slug,
        slug=project_slug,
    )
    raw_branch = request.GET.get("branch", "")
    if not raw_branch:
        target = reverse("project-download", args=[group_slug, project_slug])
        default = project.default_branch
        if default is None:
            raise Http404("项目暂无分支")
        return redirect(f"{target}?branch={default.pk}")

    try:
        branch = Branch.objects.get(pk=uuid.UUID(raw_branch), project=project)
    except (ValueError, Branch.DoesNotExist):
        raise Http404("分支不存在")

    snapshot = download_service.branch_entry_snapshot(branch)
    if not snapshot:
        return HttpResponse(
            "该分支没有任何文件，无法下载 ZIP；请先上传或导入文件。",
            status=409,
            content_type="text/plain; charset=utf-8",
        )

    zip_path, filename = download_service.export_branch_zip(branch)
    response = FileResponse(
        _DeletedOnClose(zip_path),
        as_attachment=True,
        filename=filename,
        content_type="application/zip",
    )
    response["X-Content-Type-Options"] = "nosniff"
    return response
