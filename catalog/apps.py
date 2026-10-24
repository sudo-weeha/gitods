from pathlib import Path

from django.apps import AppConfig


class CatalogConfig(AppConfig):
    name = "catalog"
    verbose_name = "项目目录"

    def ready(self):
        # 确保私有数据目录结构存在；对象读写细节见 catalog/services/storage.py
        from django.conf import settings

        for sub in ("objects", "tmp", Path("locks")):
            (settings.DATA_ROOT / sub).mkdir(parents=True, exist_ok=True)
