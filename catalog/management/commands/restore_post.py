"""备份恢复后的收尾：清空旧会话并作废未完成导入草稿。

备份不包含 DATA_ROOT/tmp：草稿引用的临时清单在恢复环境不存在，
必须标记取消并清空引用，避免预览/发布读到悬空路径。数据库角色与
环境密钥不随备份走，需另行准备（见 docs/operations.md）。
"""
from django.core.management.base import BaseCommand
from django.contrib.sessions.models import Session

from catalog.models import ImportDraft


class Command(BaseCommand):
    help = "恢复备份后的收尾：清空旧会话、作废未完成导入草稿并清空其临时清单引用"

    def handle(self, *args, **options):
        sessions = Session.objects.count()
        Session.objects.all().delete()

        unfinished = ImportDraft.objects.filter(status=ImportDraft.STATUS_READY)
        count = unfinished.count()
        for draft in unfinished:
            draft.status = ImportDraft.STATUS_CANCELLED
            draft.result = "备份恢复时作废：临时清单不在备份内"
            draft.manifest_path = ""
        ImportDraft.objects.bulk_update(
            unfinished, ["status", "result", "manifest_path"]
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"已清空 {sessions} 个会话；作废 {count} 个未完成导入草稿"
            )
        )
