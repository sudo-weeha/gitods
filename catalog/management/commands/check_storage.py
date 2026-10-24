"""检查数据库引用与对象目录的完整性（恢复演练与日常巡检用）。

只读操作：不修改任何数据或文件。发现缺失/失配对象时以非零码退出。
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from catalog.models import Blob, Entry, ImportDraft


class Command(BaseCommand):
    help = "核对 Entry/Blob 引用与对象文件的一致性，报告缺失、失配与孤儿对象"

    def handle(self, *args, **options):
        objects_root = Path(settings.DATA_ROOT) / "objects"
        referenced = set()
        missing = 0
        size_mismatch = 0

        blobs = {b.sha256: b for b in Blob.objects.all()}
        for entry in Entry.objects.filter(kind="file").select_related("blob"):
            referenced.add(entry.blob.sha256)
            path = objects_root / entry.blob.sha256[:2] / entry.blob.sha256
            if not path.is_file():
                missing += 1
                self.stderr.write(f"缺失对象: {entry.branch_id} {entry.path}")
            elif path.stat().st_size != entry.blob.size:
                size_mismatch += 1
                self.stderr.write(f"大小失配: {path}")

        orphan = 0
        if objects_root.is_dir():
            for shard in objects_root.iterdir():
                if not shard.is_dir():
                    continue
                for obj in shard.iterdir():
                    if obj.name not in blobs:
                        orphan += 1

        dangling_drafts = 0
        for draft in ImportDraft.objects.filter(status=ImportDraft.STATUS_READY):
            if draft.manifest_path and not Path(draft.manifest_path).is_file():
                dangling_drafts += 1

        self.stdout.write(
            f"条目 {Entry.objects.count()}，对象 {len(blobs)}，"
            f"引用对象 {len(referenced)}，孤儿对象 {orphan}，"
            f"缺失 {missing}，大小失配 {size_mismatch}，失效草稿 {dangling_drafts}"
        )
        if missing or size_mismatch:
            raise SystemExit(2)
