"""停机维护清理：未引用对象、过期/完结草稿临时文件、遗留临时 ZIP、旧登录记录。

默认 dry-run 只报告；真实删除要求：
1. 已停止 web 并排空请求（--yes-i-stopped-web）
2. 先完成备份
不能在线执行：已开始的下载可能仍要读取即将删除的旧对象。
"""
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from catalog.models import Blob, Entry, ImportDraft, LoginAttempt


class Command(BaseCommand):
    help = "停机清理未引用对象与临时文件；默认 dry-run，--apply 且 --yes-i-stopped-web 才执行"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="执行删除（默认仅报告）")
        parser.add_argument(
            "--yes-i-stopped-web",
            action="store_true",
            help="确认 web 已停止且请求已排空，并已完成备份",
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        if apply and not options["yes_i_stopped_web"]:
            raise CommandError(
                "真实删除前必须：①停止 web 并排空请求 ②完成备份；确认后加 --yes-i-stopped-web"
            )

        data_root = Path(settings.DATA_ROOT)
        objects_root = data_root / "objects"
        tmp_root = data_root / "tmp"

        # 1) 未引用对象
        referenced = set(
            Blob.objects.filter(entries__isnull=False).values_list("sha256", flat=True)
        )
        # ready 草稿在发布时才落对象；当前引用以外（含历史导入失败残留）均可回收
        orphan_objects = []
        if objects_root.is_dir():
            for shard in objects_root.iterdir():
                if not shard.is_dir():
                    continue
                for obj in shard.iterdir():
                    if obj.name not in referenced:
                        orphan_objects.append(obj)
        self.stdout.write(f"未引用对象: {len(orphan_objects)}")

        # 2) 未完成草稿的临时目录（过期/取消/发布完成都应清理）
        finished_drafts = ImportDraft.objects.exclude(status=ImportDraft.STATUS_READY)
        stale_draft_dirs = []
        for draft in finished_drafts:
            if draft.manifest_path:
                draft_dir = Path(draft.manifest_path).resolve().parent
                if draft_dir.is_dir() and draft_dir.is_relative_to(tmp_root.resolve()):
                    stale_draft_dirs.append((draft, draft_dir))

        # 3) tmp 下遗留的上传/导出临时文件
        keep = {d.resolve() for _, d in stale_draft_dirs}
        stray_tmp = []
        if tmp_root.is_dir():
            for item in tmp_root.iterdir():
                if item.is_dir() and item.name.startswith("import-"):
                    if item.resolve() not in keep:
                        stray_tmp.append(item)
                elif item.is_file() and item.name.startswith(("upload-", "export-")):
                    stray_tmp.append(item)
        self.stdout.write(
            f"草稿临时目录: {len(stale_draft_dirs)}，遗留临时文件: {len(stray_tmp)}"
        )

        # 4) 过期 ImportDraft 行与旧登录记录
        now = timezone.now()
        expired_drafts = list(
            ImportDraft.objects.filter(status=ImportDraft.STATUS_READY, expires_at__lt=now)
        )
        old_attempts = LoginAttempt.objects.filter(
            created_at__lt=now
            - timezone.timedelta(minutes=30)
        )
        self.stdout.write(
            f"过期草稿: {len(expired_drafts)}，旧登录记录: {old_attempts.count()}"
        )

        freed = sum(p.stat().st_size for p in orphan_objects + stray_tmp if p.is_file())
        for _, d in stale_draft_dirs:
            freed += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        self.stdout.write(f"预计释放: {freed} 字节")

        if not apply:
            self.stdout.write(self.style.WARNING("dry-run：未删除任何内容；加 --apply 执行"))
            return

        for obj in orphan_objects:
            obj.unlink(missing_ok=True)
            try:
                obj.parent.rmdir()  # 空分片目录一并移除
            except OSError:
                pass
        for draft, draft_dir in stale_draft_dirs:
            shutil.rmtree(draft_dir, ignore_errors=True)
            if draft.manifest_path:
                draft.manifest_path = ""
                draft.save(update_fields=["manifest_path"])
        for item in stray_tmp:
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)
        for draft in expired_drafts:
            draft.status = ImportDraft.STATUS_EXPIRED
            draft.result = draft.result or "已过期（清理时标记）"
            draft.manifest_path = ""
            draft.save(update_fields=["status", "result", "manifest_path"])
        old_attempts.delete()
        self.stdout.write(self.style.SUCCESS("清理完成"))
