"""pytest 全局夹具。"""

import pytest


@pytest.fixture(autouse=True)
def isolated_data_root(tmp_path, settings):
    """每个测试使用独立 DATA_ROOT，避免把对象写入开发目录。"""
    root = tmp_path / "data-root"
    (root / "tmp").mkdir(parents=True)
    settings.DATA_ROOT = root
    settings.FILE_UPLOAD_TEMP_DIR = root / "tmp"
    return root
