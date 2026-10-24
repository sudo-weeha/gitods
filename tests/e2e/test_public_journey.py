"""公共浏览闭环：发现→筛选→搜索→项目→分支切换→目录→源码→README 内链→
单文件下载→分支 ZIP 下载→后退恢复，含 HTMX 历史缓存清空后的完整页恢复。"""
import re
from pathlib import Path
from urllib.parse import quote

import pytest
from django.core.management import call_command
from django.test import override_settings

pytestmark = pytest.mark.django_db(transaction=True)

SCREEN_DIR = Path(__file__).resolve().parents[2] / "artifacts" / "screenshots"
PROJECT = "/backend/device-platform"


@pytest.fixture
def seeded(transactional_db, tmp_path):
    with override_settings(DATA_ROOT=tmp_path / "data"):
        call_command("seed_demo", "--force")
        yield


def _no_console_errors(page):
    errors = []
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    return errors


def test_public_journey(page, live_server, seeded):
    errors = _no_console_errors(page)
    page.set_viewport_size({"width": 1440, "height": 1000})
    base = live_server.url

    # 发现页：分组导航 + 项目列表
    page.goto(f"{base}/explore/")
    assert page.locator(".project-item").count() >= 4

    # 分组筛选（HTMX 局部替换 + push URL）
    page.click('.group-item:has-text("前端组件")')
    page.wait_for_url("**/explore/?group=frontend")
    assert page.locator(".project-item:has-text('表格组件库')").count() == 1

    # 清筛选回全部，再搜索
    page.click('.group-item:has-text("全部分组")')
    page.wait_for_url(re.compile(r"/explore/$|/explore/\?"))
    page.fill(".explore-search-input", "设备")
    page.press(".explore-search-input", "Enter")
    page.wait_for_url(re.compile(r"[?&]q="))
    assert page.locator(".project-item:has-text('设备管理平台')").count() == 1

    # 进入项目（无 branch → 302 默认分支）
    page.click(".project-item:has-text('设备管理平台') .project-item-link")
    page.wait_for_url("**/backend/device-platform/?branch=*")
    assert page.locator(".file-row").count() >= 4

    # 切换分支 release-1.0（保持根目录上下文）
    page.click(".branch-summary")
    page.click('.branch-option:has-text("release-1.0")')
    page.wait_for_url("**/backend/device-platform/?branch=*")
    assert page.locator(".file-row:has-text('CHANGELOG.md')").count() == 1
    page.screenshot(path=str(SCREEN_DIR / "branch-switch-1440.png"), full_page=False)

    # 切回 main（释放分支菜单后键盘 Esc 应能关闭）
    page.click(".branch-summary")
    page.keyboard.press("Escape")
    assert page.locator("details.branch-menu").get_attribute("open") is None
    page.click(".branch-summary")
    page.click('.branch-option:has-text("main")')
    page.wait_for_load_state()

    # 进入目录 src
    page.click('.file-row.is-dir a:has-text("src")')
    page.wait_for_url("**/tree/src?branch=*")
    assert page.locator(".file-row:has-text('device_service.py')").count() == 1

    # 源码：行号 + 高亮 + #L30 定位
    page.click('.file-row a:has-text("device_service.py")')
    page.wait_for_url("**/blob/src/device_service.py?branch=*")
    assert page.locator(".code-panel .linenodiv").first.is_visible()
    page.goto(f"{base}{PROJECT}/blob/src/device_service.py?branch={_branch_id(page)}#L30")
    # #L30 命中行锚点（零尺寸 <a>，验证存在与所在行内容）
    assert page.locator("#L30").count() == 1
    assert "device" in page.evaluate(
        "document.getElementById('L30').parentElement.textContent"
    )

    # README 内链：根 README 的 架构说明 链接 → blob docs/架构说明.md，分支保持
    page.goto(f"{base}{PROJECT}/?branch={_branch_id(page)}")
    page.click('.markdown-body a:has-text("架构说明")')
    page.wait_for_url("**/blob/docs/%E6%9E%B6%E6%9E%84%E8%AF%B4%E6%98%8E.md?branch=*")

    # 单文件下载（raw?dl=1 → 附件）
    with page.expect_download() as download_info:
        page.click('.code-head-actions a:has-text("下载")')
    assert download_info.value.suggested_filename == "架构说明.md"

    # 当前分支 ZIP 下载
    with page.expect_download() as download_info:
        page.click('.toolbar-actions a:has-text("下载当前分支")')
    assert download_info.value.suggested_filename == "device-platform-main.zip"

    # 浏览器后退：下载不产生历史条目，上一层回到项目根且完整渲染
    page.go_back()
    page.wait_for_url("**/backend/device-platform/?branch=*")
    assert page.locator(".topbar .brand").is_visible()
    assert page.locator(".readme-panel").is_visible()

    # 清空 HTMX 历史缓存后后退/前进：历史恢复请求必须返回完整页面
    page.evaluate(
        """() => { for (let i = 0; i < localStorage.length; i++) {
            const key = localStorage.key(i);
            if (key && key.includes('htmx')) { localStorage.removeItem(key); }
        } }"""
    )
    page.goto(f"{base}/explore/")
    page.click('.group-item:has-text("前端组件")')
    page.wait_for_url("**/explore/?group=frontend")
    page.evaluate(
        """() => { for (let i = localStorage.length - 1; i >= 0; i--) {
            const key = localStorage.key(i);
            if (key && key.includes('htmx')) { localStorage.removeItem(key); }
        } }"""
    )
    page.go_back()
    page.wait_for_load_state()
    # 历史缓存未命中 → 服务端全页 → 顶栏与分组导航完整
    assert page.locator(".topbar .brand").is_visible()
    assert page.locator(".group-item").count() >= 8

    assert errors == [], f"控制台错误: {errors}"


def _branch_id(page):
    return page.url.split("branch=")[-1].split("&")[0]


def test_feedback_states(page, live_server, seeded):
    errors = _no_console_errors(page)
    base = live_server.url

    # 无搜索结果
    page.goto(f"{base}/explore/?q=%E7%BB%9D%E4%B8%8D%E5%8C%B9%E9%85%8D")
    assert page.locator("text=没有找到").is_visible()

    # 空分支
    from catalog.models import Branch, Project
    from catalog.services.branches import create_branch

    project = Project.objects.get(slug="device-platform")
    empty = create_branch(project_id=project.pk, name="empty-state")
    page.goto(f"{base}{PROJECT}/?branch={empty.pk}")
    assert page.locator("text=该分支还没有任何文件").is_visible()

    # 空分支 ZIP 下载说明（预期 409，浏览器会记录一条资源加载日志）
    page.goto(f"{base}{PROJECT}/download/?branch={empty.pk}")
    assert "没有任何文件" in page.content()
    errors = [e for e in errors if "409" not in e]

    # 长路径深目录可逐层进入（种子 docs/架构说明.md 已含中文路径）
    page.goto(f"{base}{PROJECT}/tree/docs?branch={_main_id()}")
    assert page.locator('.file-row a:has-text("架构说明.md")').is_visible()

    # 移动端菜单：分支下拉可打开、触控目标足够
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{base}{PROJECT}/?branch={_main_id()}")
    summary = page.locator(".branch-summary")
    box = summary.bounding_box()
    assert box["height"] >= 30
    summary.click()
    page.wait_for_timeout(200)
    assert page.locator("details.branch-menu").get_attribute("open") is not None
    page.screenshot(path=str(SCREEN_DIR / "mobile-menu-390.png"), full_page=False)

    assert errors == []


def _main_id():
    from catalog.models import Branch

    return str(Branch.objects.get(project__slug="device-platform", name="main").pk)
