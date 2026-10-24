"""pytest 全局夹具。"""
import pytest

from catalog.services.projects import create_group, create_project


@pytest.fixture(autouse=True)
def isolated_data_root(request, tmp_path, settings):
    """单元测试使用独立 DATA_ROOT，避免把对象写入开发目录。

    tests/e2e 不适用：演示种子由 seeded 夹具统一写入同一个 DATA_ROOT，
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


@pytest.fixture
def group(db):
    return create_group(name="后端服务", slug="backend")


@pytest.fixture
def project(db, group):
    return create_project(group=group, name="设备管理平台", slug="device-platform")


@pytest.fixture
def branch(project):
    return project.default_branch


@pytest.fixture
def admin_user(db, django_user_model):
    return django_user_model.objects.create_superuser(
        "test-admin", password="Test-only-Long-Password-42"
    )


@pytest.fixture
def admin_client(client, admin_user):
    client.force_login(admin_user)
    return client
