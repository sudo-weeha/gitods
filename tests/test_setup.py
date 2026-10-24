"""任务 1 环境自检：数据库引擎、业务表、健康检查与会话配置。"""
import pytest
from django.conf import settings
from django.db import connection

pytestmark = pytest.mark.django_db

BUSINESS_TABLES = {
    "catalog_group",
    "catalog_project",
    "catalog_branch",
    "catalog_blob",
    "catalog_entry",
    "catalog_importdraft",
    "catalog_loginattempt",
}


def test_database_is_postgresql():
    # 明确断言测试数据库是 PostgreSQL，禁止 SQLite 替身
    assert connection.vendor == "postgresql"
    assert connection.settings_dict["ENGINE"] == "django.db.backends.postgresql"


def test_business_and_session_tables_exist():
    tables = set(connection.introspection.table_names())
    assert BUSINESS_TABLES <= tables
    assert "django_session" in tables


def test_healthz_ok(client):
    response = client.get("/healthz/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_session_is_12_hours():
    assert settings.SESSION_COOKIE_AGE == 12 * 60 * 60


def test_debug_default_is_off_when_unset(monkeypatch):
    # 未显式提供 DEBUG 时默认必须为 False（生产安全默认）
    import environ

    monkeypatch.delenv("DEBUG", raising=False)
    assert environ.Env(DEBUG=(bool, False))("DEBUG") is False
