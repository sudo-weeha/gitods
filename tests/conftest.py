"""pytest 全局夹具。"""

import pytest


@pytest.fixture(autouse=True)
def isolated_data_root(request, tmp_path, settings):
    """单元测试使用独立 DATA_ROOT，避免把对象写入开发目录。

    tests/e2e 不适用：演示种子由模块级 seeded 夹具统一写入同一个 DATA_ROOT，
    每个测试函数再切换目录会让服务线程找不到对象文件。
    """
    if "tests/e2e" in str(request.path):
        yield None
        return
    root = tmp_path / "data-root"
    (root / "tmp").mkdir(parents=True)
    settings.DATA_ROOT = root
    settings.FILE_UPLOAD_TEMP_DIR = root / "tmp"
    yield root
