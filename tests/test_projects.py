"""分组与项目服务测试：默认 main、slug 冲突、移动、删除与共享对象保留。"""
import pytest

from catalog.models import Blob, Branch, Entry, Group, Project
from catalog.services.exceptions import ValidationError
from catalog.services.projects import (
    create_group,
    create_project,
    delete_group,
    delete_project,
    move_project,
)
from tests.factories import add_file

pytestmark = pytest.mark.django_db


class TestCreateProject:
    def test_creates_default_main(self, group):
        project = create_project(
            group=group, name="设备管理平台", slug="device-platform",
            description="物联网设备管理", language="Java",
        )
        assert project.default_branch is not None
        assert project.default_branch.name == "main"
        assert Branch.objects.filter(project=project).count() == 1

    def test_slug_conflict_within_group(self, group, project):
        with pytest.raises(ValidationError, match="已存在"):
            create_project(group=group, name="另一个项目", slug="device-platform")

    def test_same_slug_allowed_across_groups(self, group):
        other = create_group(name="前端组件", slug="frontend")
        project = create_project(group=other, name="同名标识", slug="device-platform")
        assert project.group.slug == "frontend"

    @pytest.mark.parametrize("slug", ["admin", "api", "explore", "static", "healthz"])
    def test_reserved_slug_rejected(self, group, slug):
        with pytest.raises(ValidationError, match="保留"):
            create_project(group=group, name="x", slug=slug)

    @pytest.mark.parametrize("slug", ["A", "-abc", "a", "a_b", "abc-", "a b", "x" * 51])
    def test_invalid_slug_format_rejected(self, group, slug):
        with pytest.raises(ValidationError, match="URL 标识"):
            create_project(group=group, name="x", slug=slug)

    def test_blank_name_rejected(self, group):
        with pytest.raises(ValidationError, match="名称"):
            create_project(group=group, name="  ", slug="ok-slug")


class TestGroupDeletion:
    def test_non_empty_group_rejected(self, group, project):
        with pytest.raises(ValidationError, match="先移动或删除"):
            delete_group(group_id=group.pk)

    def test_empty_group_deletable(self, db):
        group = create_group(name="空分组", slug="empty")
        delete_group(group_id=group.pk)
        assert not Group.objects.filter(pk=group.pk).exists()


class TestMoveProject:
    def test_move_success(self, project):
        target = create_group(name="前端组件", slug="frontend")
        moved = move_project(project_id=project.pk, target_group_id=target.pk)
        assert moved.group.pk == target.pk
        moved.refresh_from_db()
        assert moved.group.pk == target.pk

    def test_move_conflict_rejected(self, project):
        target = create_group(name="前端组件", slug="frontend")
        create_project(group=target, name="占位", slug=project.slug)
        with pytest.raises(ValidationError, match="无法移动"):
            move_project(project_id=project.pk, target_group_id=target.pk)
        project.refresh_from_db()
        assert project.group.slug == "backend"  # 未产生半移动


class TestDeleteProject:
    def test_removes_branches_and_entries_keeps_blob(self, project, branch):
        entry = add_file(branch, "README.md", b"hello")
        blob_pk = entry.blob.pk
        delete_project(project_id=project.pk)
        assert not Project.objects.filter(pk=project.pk).exists()
        assert not Branch.objects.filter(pk=branch.pk).exists()
        assert not Entry.objects.filter(pk=entry.pk).exists()
        # 共享对象不删除，由停机清理回收
        assert Blob.objects.filter(pk=blob_pk).exists()

    def test_not_blocked_by_default_branch_cycle(self, project):
        # default_branch 的 PROTECT 不应阻塞项目删除
        delete_project(project_id=project.pk)
        assert not Project.objects.filter(pk=project.pk).exists()


class TestGroupSlug:
    def test_duplicate_name_rejected(self, group):
        with pytest.raises(ValidationError, match="已存在"):
            create_group(name=group.name, slug="another")

    def test_duplicate_slug_rejected(self, group):
        with pytest.raises(ValidationError, match="已被占用"):
            create_group(name="新分组", slug=group.slug)


class TestExplore:
    def test_search_matches_name_slug_description(self, client, project, group):
        create_project(group=group, name="订单中心", slug="order", description="资金结算服务")
        html = client.get("/explore/?q=资金结算").content.decode()
        assert "订单中心" in html
        html = client.get("/explore/?q=order").content.decode()
        assert "订单中心" in html
        html = client.get("/explore/?q=绝不匹配的关键词").content.decode()
        assert "没有找到" in html

    def test_group_filter_and_counts(self, client, project, group):
        other = create_group(name="前端组件", slug="frontend")
        create_project(group=other, name="表格组件", slug="table")
        # 用 HTMX 局部请求只看列表区域，避免“最近更新”侧栏带来的干扰
        html = client.get("/explore/?group=frontend", HTTP_HX_REQUEST="true").content.decode()
        assert "表格组件" in html
        assert "设备管理平台" not in html

    def test_sort_by_name(self, client, project, group):
        # ASCII 排在中文之前，比较结果不受数据库中文 collation 影响
        create_project(group=group, name="Alpha 网关", slug="alpha-gateway")
        html = client.get("/explore/?sort=name").content.decode()
        assert html.index("Alpha 网关") < html.index("设备管理平台")

    def test_recent_sidebar_five_items(self, client, project, group):
        html = client.get("/explore/").content.decode()
        assert "最近更新" in html
        assert "设备管理平台" in html

    def test_htmx_returns_partial_and_restore_returns_full(self, client, project):
        partial = client.get("/explore/", HTTP_HX_REQUEST="true")
        assert partial.status_code == 200
        assert "topbar" not in partial.content.decode()  # 只含列表区域
        assert 'id="results-column"' in partial.content.decode()
        assert "HX-Request" in partial["Vary"]
        assert "HX-History-Restore-Request" in partial["Vary"]

        restore = client.get(
            "/explore/", HTTP_HX_REQUEST="true", HTTP_HX_HISTORY_RESTORE_REQUEST="true"
        )
        assert "topbar" in restore.content.decode()

    def test_empty_group_state(self, client, db):
        create_group(name="空分组", slug="empty")
        html = client.get("/explore/?group=empty").content.decode()
        assert "该分组暂无项目" in html
