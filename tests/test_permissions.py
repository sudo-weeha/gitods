"""权限测试：公开只读、管理区受控、CSRF 与登录返回地址。"""
import pytest
from django.test import Client

pytestmark = pytest.mark.django_db


class TestPublicReadOnly:
    def test_explore_public(self, client, project):
        assert client.get("/explore/").status_code == 200

    def test_project_public(self, client, project):
        response = client.get("/backend/device-platform/")
        assert response.status_code == 302  # 默认分支重定向
        assert "branch=" in response.url
        follow = client.get(response.url)
        assert follow.status_code == 200

    def test_visitor_sees_login_link_not_admin_buttons(self, client, project):
        html = client.get("/explore/").content.decode()
        assert "管理员登录" in html
        assert "管理分组" not in html
        assert "新建项目" not in html

    def test_staff_sees_admin_buttons(self, admin_client, project):
        html = admin_client.get("/explore/").content.decode()
        assert "管理分组" in html
        assert "新建项目" in html


class TestAdminGuard:
    def test_admin_redirects_anonymous(self, client):
        response = client.get("/admin/catalog/group/")
        assert response.status_code == 302
        assert "/admin/login/" in response.url

    def test_admin_rejects_non_staff(self, client, django_user_model):
        user = django_user_model.objects.create_user("plain", password="Plain-User-Password-1")
        client.force_login(user)
        response = client.get("/admin/catalog/group/")
        # Django Admin：非 staff 直接登录也会被拒（跳登录页带错误）
        assert response.status_code in (302, 403)

    def test_staff_can_open_changelist(self, admin_client, group):
        assert admin_client.get("/admin/catalog/group/").status_code == 200

    def test_bulk_delete_action_disabled(self, admin_client, group):
        response = admin_client.get("/admin/catalog/group/")
        assert b"action-select" not in response.content  # 无批量操作复选框


class TestAdminServiceWiring:
    def test_create_group_via_admin(self, admin_client):
        response = admin_client.post(
            "/admin/catalog/group/add/",
            {
                "name": "测试分组",
                "slug": "test-group",
                "description": "经 Admin 创建",
                "icon": "layers",
                "sort_order": "10",
            },
        )
        assert response.status_code == 302
        from catalog.models import Group

        assert Group.objects.filter(slug="test-group").exists()

    def test_create_project_via_admin_creates_main(self, admin_client, group):
        from catalog.models import Branch, Project

        response = admin_client.post(
            "/admin/catalog/project/add/",
            {
                "group": str(group.pk),
                "name": "新建项目",
                "slug": "new-project",
                "description": "描述",
                "language": "Python",
                "license_name": "MIT",
            },
        )
        assert response.status_code == 302
        project = Project.objects.get(slug="new-project")
        assert project.default_branch.name == "main"
        assert Branch.objects.filter(project=project).count() == 1

    def test_reserved_slug_blocked_in_admin_form(self, admin_client, group):
        response = admin_client.post(
            "/admin/catalog/project/add/",
            {
                "group": str(group.pk),
                "name": "保留标识项目",
                "slug": "explore",
                "description": "",
                "language": "",
                "license_name": "",
            },
        )
        assert response.status_code == 200  # 表单报错重渲染
        assert "系统保留标识" in response.content.decode()
        from catalog.models import Project

        assert not Project.objects.filter(slug="explore").exists()

    def test_change_pages_render(self, admin_client, group, project):
        # 回归：prepopulated slug 与编辑页只读 slug 冲突曾致 500
        assert admin_client.get(f"/admin/catalog/group/{group.pk}/change/").status_code == 200
        assert admin_client.get(f"/admin/catalog/project/{project.pk}/change/").status_code == 200

    def test_delete_non_empty_group_blocked(self, admin_client, group, project):
        # 项目 FK 为 PROTECT：Admin 原生受保护对象页拒绝删除并列出项目
        response = admin_client.post(
            f"/admin/catalog/group/{group.pk}/delete/", {"post": "yes"}
        )
        assert response.status_code == 200
        from catalog.models import Group

        assert Group.objects.filter(pk=group.pk).exists()
        assert "设备管理平台" in response.content.decode()


class TestCsrf:
    def test_write_without_csrf_rejected(self, client, group):
        strict = Client(enforce_csrf_checks=True)
        response = strict.post(
            "/admin/catalog/group/add/", {"name": "x", "slug": "xx-yy"}
        )
        assert response.status_code == 403


class TestLoginRedirect:
    def test_next_param_honored(self, client, admin_user):
        response = client.post(
            "/admin/login/?next=/explore/",
            {"username": "test-admin", "password": "Test-only-Long-Password-42"},
        )
        assert response.status_code == 302
        assert response.url == "/explore/"

    def test_unsafe_next_ignored(self, client, admin_user):
        response = client.post(
            "/admin/login/?next=https://evil.example.com/",
            {"username": "test-admin", "password": "Test-only-Long-Password-42"},
        )
        assert response.status_code == 302
        assert "evil.example.com" not in response.url
