"""任务 2 视觉验收：发现页 / 项目根目录 / 源码状态 × 三种视口截图与溢出检查。"""
from pathlib import Path
from urllib.parse import quote

import pytest
from django.core.management import call_command
from django.test import override_settings

pytestmark = pytest.mark.django_db(transaction=True)

SCREEN_DIR = Path(__file__).resolve().parents[2] / "artifacts" / "screenshots"
VIEWPORTS = [(1440, 1000), (1024, 900), (390, 844)]
PROJECT_HOME = "/backend/device-platform/"


@pytest.fixture
def seeded(transactional_db, tmp_path):
    """每个测试独立种子：transactional_db 在测试结束回滚数据库行，
    对象文件落在同一 tmp 目录（内容寻址，重复写入天然幂等）。"""
    with override_settings(DATA_ROOT=tmp_path / "data"):
        call_command("seed_demo", "--force")
        yield


def _open(page, live_server, url, width, height):
    page.set_viewport_size({"width": width, "height": height})
    page.goto(f"{live_server.url}{url}")
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 0, f"{width}px 视口出现整页横向滚动 {overflow}px"


def _shot(page, name, width):
    SCREEN_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREEN_DIR / f"{name}-{width}.png"), full_page=True)


@pytest.mark.parametrize("width,height", VIEWPORTS)
def test_explore_visual(page, live_server, seeded, width, height):
    _open(page, live_server, "/explore/", width, height)
    assert page.locator(".project-item").count() >= 4, "首屏项目条目不足 4 条"
    assert page.locator(".group-item").count() >= 8, "分组导航不足 8 项"
    assert page.locator(".recent-item").count() == 5
    _shot(page, "explore", width)


@pytest.mark.parametrize("width,height", VIEWPORTS)
def test_project_root_visual(page, live_server, seeded, width, height):
    # 不带 branch 参数：应 302 到默认分支地址后正常渲染
    _open(page, live_server, PROJECT_HOME, width, height)
    assert page.locator(".file-row").count() >= 4
    assert page.locator(".readme-panel").count() == 1
    assert "设备管理平台" in page.locator(".project-title").inner_text()
    _shot(page, "project", width)


@pytest.mark.parametrize("width,height", VIEWPORTS)
def test_code_visual(page, live_server, seeded, width, height):
    url = f"{PROJECT_HOME}blob/{quote('src/device_service.py')}/"
    _open(page, live_server, url, width, height)
    assert page.locator(".code-panel").count() == 1
    assert page.locator(".code-panel .linenodiv, .code-panel .ln-no").first.is_visible()
    _shot(page, "code", width)
