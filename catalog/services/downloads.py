"""分支导出：先取完整清单快照，再写私有临时 ZIP（含空目录）。

导出与导入不共享可变物理源文件：导出只读不可变对象与元数据。
临时 ZIP 通过 FileResponse 发送并在关闭时删除（见 views.content）。
"""
import uuid
import zipfile
from pathlib import Path

from django.conf import settings

from catalog.models import Entry
from catalog.services.storage import object_path


def export_branch_zip(branch) -> tuple[Path, str]:
    """返回 (临时 zip 路径, 下载文件名)。调用方负责发送与删除。"""
    entries = list(Entry.objects.filter(branch=branch).order_by("path"))
    tmp_dir = Path(settings.DATA_ROOT) / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    zip_path = tmp_dir / f"export-{branch.pk}-{uuid.uuid4().hex}.zip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for entry in entries:
            if entry.kind == "dir":
                # 空目录同样保留：写入目录条目
                info = zipfile.ZipInfo(entry.path + "/")
                archive.writestr(info, "")
            else:
                archive.write(object_path(entry.blob.sha256), arcname=entry.path)

    filename = f"{branch.project.slug}-{branch.name}.zip"
    return zip_path, filename


def branch_entry_snapshot(branch) -> list[Entry]:
    """下载前取得完整清单快照；随后生成 ZIP，不依赖数据库事务。"""
    return list(Entry.objects.filter(branch=branch).select_related("blob").order_by("path"))
