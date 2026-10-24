"""管理闭环：登录→新建分组→新建项目→导入 main→复制 release→替换 release
文件→设置默认→验证 main 不变→删除非默认分支；附导入确认页截图。"""
import io
import zipfile
from pathlib import Path

import pytest
from django.core.management import call_command
from django.test import override_settings

pytestmark = pytest.mark.django_db(transaction=True)

SCREEN_DIR = Path(__file__).resolve().parents[2] / "artifacts" / "screenshots"


@pytest.fixture
def seeded(transactional_db, tmp_path, django_user_model):
    with override_settings(DATA_ROOT=tmp_path / "data"):
        call_command("seed_demo", "--force")
        django_user_model.objects.create_superuser(
            "journey-admin", password="Journey-Admin-42"
        )
        yield


def _zip_bytes(files: dict) -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return payload.getvalue()


def test_admin_journey(page, live_server, seeded):
    page.set_viewport_size({"width": 1440, "height": 1000})
    base = live_server.url
    project = None  # 由测试内创建

    # 登录
    page.goto(f"{base}/admin/login/")
    page.fill('input[name="username"]', "journey-admin")
    page.fill('input[name="password"]', "Journey-Admin-42")
    page.click("input[type=submit]")
    page.wait_for_load_state()
    assert page.locator(".topbar-link, #site-name").first.is_visible()

    # 新建分组
    page.goto(f"{base}/admin/catalog/group/add/")
    page.fill('input[name="name"]', "旅程分组")
    page.fill('input[name="slug"]', "journey")
    page.fill('input[name="description"]', "E2E 闭环创建")
    page.click('input[name="_continue"], input[type=submit] >> nth=0')
    page.wait_for_load_state()

    # 新建项目（自动生成默认 main）
    page.goto(f"{base}/admin/catalog/project/add/")
    page.fill('input[name="name"]', "闭环验证项目")
    page.fill('input[name="slug"]', "journey-app")
    page.select_option('select[name="group"]', label="旅程分组")
    page.click('input[type=submit] >> nth=0')
    page.wait_for_load_state()

    from catalog.models import Project

    created = Project.objects.get(slug="journey-app")
    assert created.default_branch.name == "main"
    project = created

    # 导入 main：上传 ZIP → 预览 → 发布
    zip_bytes = _zip_bytes({
        "README.md": "# 闭环验证\n\n由 E2E 导入。\n",
        "src/main.py": "print('journey')\n",
        "docs/说明.md": "# 说明\n",
        ".git/config": "[core]",  # 排除项
    })
    page.goto(f"{base}/admin/catalog/project/{project.pk}/imports/?branch={project.default_branch_id}")
    page.set_input_files(
        'form#upload-form input[type=file]',
        files=[{"name": "journey.zip", "mimeType": "application/zip", "buffer": zip_bytes}],
    )
    page.click('form#upload-form input[type=submit]')
    page.wait_for_url("**/imports/*/", timeout=30000)

    # 预览页：替换范围、路径预览、排除项
    body = page.content()
    assert "替换范围" in body
    assert ".git/config" in body
    page.screenshot(path=str(SCREEN_DIR / "import-confirm-1440.png"), full_page=True)

    page.click('text=确认发布（替换整分支）')
    page.wait_for_url("**/journey/journey-app/**", timeout=30000)
    # 根目录：README + src/docs 目录行；main.py 在 src 内
    assert page.locator(".file-row:has-text('README.md')").count() == 1
    assert page.locator(".file-row.is-dir:has-text('src')").count() == 1
    page.click('.file-row.is-dir a:has-text("src")')
    page.wait_for_url("**/tree/src?branch=*")
    assert page.locator(".file-row:has-text('main.py')").count() == 1
    page.go_back()

    # 复制 release 分支
    page.goto(f"{base}/admin/catalog/project/{project.pk}/branches/")
    page.fill('input[name="name"]', "release-1.0")
    page.check('input[value="copy"]')
    page.click('form:has(input[name="name"]) input[type=submit]')
    page.wait_for_load_state()
    assert page.locator("text=release-1.0").first.is_visible()

    # 替换 release 文件：新文件直接成功（release 复制自 main，已有 README）
    from catalog.models import Branch

    release = Branch.objects.get(project=project, name="release-1.0")
    files_url = f"{base}/admin/catalog/project/{project.pk}/files/?branch={release.pk}"
    page.goto(files_url)
    page.set_input_files(
        'form#upload-form input[type=file]',
        files=[{"name": "发布说明.md", "mimeType": "text/markdown", "buffer": "# release 版本\n".encode()}],
    )
    page.click('form#upload-form input[type=submit]')
    page.wait_for_url("**/journey/journey-app/**", timeout=30000)

    # 同名默认拒绝 → 冲突提示（XHR 重渲染管理页，不导航）→ 勾选覆盖后成功
    page.goto(files_url)
    page.set_input_files(
        'form#upload-form input[type=file]',
        files=[{"name": "README.md", "mimeType": "text/markdown", "buffer": "# release v2\n".encode()}],
    )
    page.click('form#upload-form input[type=submit]')
    # 等待仅出现在冲突重渲染中的文案（原页面的复选框标签同名会误匹配）
    page.wait_for_selector("text=以下文件已存在", timeout=30000)
    page.wait_for_timeout(300)  # document.write 替换后等待 DOM 稳定
    assert page.locator("code", has_text="README.md").first.is_visible()
    page.check('input[name="overwrite"]')
    page.set_input_files(
        'form#upload-form input[type=file]',
        files=[{"name": "README.md", "mimeType": "text/markdown", "buffer": "# release v2\n".encode()}],
    )
    page.click('form#upload-form input[type=submit]')
    page.wait_for_url("**/journey/journey-app/**", timeout=30000)

    # 设置 release 为默认
    page.goto(f"{base}/admin/catalog/project/{project.pk}/branches/")
    row = page.locator("tr", has=page.locator("text=release-1.0")).first
    row.locator('input[value="设为默认"]').click()
    page.wait_for_load_state()

    # 验证 main 不变（release 的覆盖没有影响 main）
    page.goto(f"{base}/journey/journey-app/?branch={project.default_branch_id}")
    main_id = str(project.default_branch_id)
    page.wait_for_load_state()
    page.goto(f"{base}/journey/journey-app/?branch={main_id}")
    page.click('.file-row a:has-text("README.md")')
    page.wait_for_url("**/blob/README.md**")
    assert page.locator(".markdown-body").inner_text().startswith("闭环验证")

    # 删除非默认分支（main）
    page.goto(f"{base}/admin/catalog/project/{project.pk}/branches/")
    main_row = page.locator("tr", has=page.locator("th:has-text('main')")).first
    main_row.locator('a:has-text("删除…")').click()
    page.wait_for_load_state()
    page.click('input[value="确认删除"]')
    page.wait_for_load_state()
    # main 已删；release 为默认且仍在
    project.refresh_from_db()
    assert project.default_branch.name == "release-1.0"
    assert not Branch.objects.filter(project=project, name="main").exists()
