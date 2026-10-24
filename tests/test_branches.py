"""分支服务与管理视图测试：独立分支、复制隔离、默认/最后分支保护、跨项目拒绝。"""
import pytest
from django.urls import reverse

from catalog.models import Blob, Branch, Entry, Project
from catalog.services.branches import (
    create_branch,
    delete_branch,
    set_default_branch,
)
from catalog.services.exceptions import (
    CapacityExceeded,
    NotFound,
    RevisionConflict,
    ValidationError,
)
from tests.factories import add_dir, add_file

pytestmark = pytest.mark.django_db


class TestCreateBranch:
    def test_empty_branch(self, project):
        branch = create_branch(project_id=project.id, name="dev")
        assert branch.name == "dev"
        assert branch.revision == 1
        assert Entry.objects.filter(branch=branch).count() == 0

    def test_copy_branch_has_independent_entries(self, project, branch):
        blob = Blob.objects.create(sha256="a" * 64, size=9)
        Entry.objects.create(
            branch=branch, path="README.md", parent_path="", name="README.md",
            kind="file", blob=blob, size=blob.size,
        )
        copied = create_branch(
            project_id=project.id, name="release-1.0",
            source_branch_id=branch.id, expected_revision=branch.revision,
        )
        origin = Entry.objects.get(branch=branch, path="README.md")
        clone = Entry.objects.get(branch=copied, path="README.md")
        assert origin.id != clone.id
        assert origin.blob_id == clone.blob_id  # 共享不可变对象
        clone.delete()
        assert Entry.objects.filter(branch=branch, path="README.md").exists()

    def test_copy_with_real_files_isolates_content(self, project, branch):
        add_dir(branch, "src")
        add_file(branch, "src/app.py", b"print('main')")
        copied = create_branch(project_id=project.id, name="copy", source_branch_id=branch.id)
        assert Entry.objects.filter(branch=copied, path="src/app.py").exists()
        Entry.objects.filter(branch=copied, path="src/app.py").delete()
        assert Entry.objects.filter(branch=branch, path="src/app.py").exists()

    def test_duplicate_name_rejected(self, project, branch):
        with pytest.raises(ValidationError, match="已存在"):
            create_branch(project_id=project.id, name="main")

    @pytest.mark.parametrize("name", ["UPPER", "-x", ".hidden", "a..b", "斜杠/名", "x" * 65])
    def test_invalid_name_rejected(self, project, name):
        with pytest.raises(ValidationError, match="分支名"):
            create_branch(project_id=project.id, name=name)

    def test_cross_project_source_rejected(self, project, group):
        other = Project.objects.create(group=group, name="其他", slug="other", default_branch=None)
        other_main = Branch.objects.create(project=other, name="main")
        with pytest.raises(NotFound):
            create_branch(project_id=project.id, name="steal", source_branch_id=other_main.id)

    def test_revision_conflict_on_copy(self, project, branch):
        with pytest.raises(RevisionConflict):
            create_branch(
                project_id=project.id, name="stale-copy",
                source_branch_id=branch.id, expected_revision=branch.revision + 5,
            )

    def test_branch_count_limit(self, project, branch, settings):
        settings.GITODS_LIMITS = {
            **settings.GITODS_LIMITS, "MAX_BRANCHES_PER_PROJECT": 2
        }
        create_branch(project_id=project.id, name="dev")
        with pytest.raises(CapacityExceeded):
            create_branch(project_id=project.id, name="one-too-many")


class TestDeleteBranch:
    def test_default_branch_protected(self, project, branch):
        # 单分支时“最后一个分支”先生效；这里构造两个分支验证默认分支保护
        create_branch(project_id=project.id, name="dev")
        with pytest.raises(ValidationError, match="默认分支"):
            delete_branch(project_id=project.id, branch_id=branch.id)

    def test_last_branch_protected(self, project, branch):
        # 先转移默认到新分支，原 main 变成最后一个之外的第二分支场景见下
        other = create_branch(project_id=project.id, name="second")
        set_default_branch(project_id=project.id, branch_id=other.id)
        delete_branch(project_id=project.id, branch_id=branch.id)
        assert project.branches.count() == 1
        with pytest.raises(ValidationError, match="最后一个分支"):
            delete_branch(project_id=project.id, branch_id=other.id)

    def test_set_default_then_old_default_deletable(self, project, branch):
        add_file(branch, "README.md", b"readme")
        new_default = create_branch(project_id=project.id, name="stable")
        set_default_branch(project_id=project.id, branch_id=new_default.id)
        delete_branch(project_id=project.id, branch_id=branch.id)
        project.refresh_from_db()
        assert project.default_branch_id == new_default.id

    def test_delete_cascades_entries_keeps_blob(self, project, branch):
        entry = add_file(branch, "README.md", b"content")
        blob_pk = entry.blob.pk
        other = create_branch(project_id=project.id, name="tmp")
        set_default_branch(project_id=project.id, branch_id=other.id)
        delete_branch(project_id=project.id, branch_id=branch.id)
        assert not Entry.objects.filter(pk=entry.pk).exists()
        assert Blob.objects.filter(pk=blob_pk).exists()


class TestPublicBranchFlow:
    def test_no_branch_redirects_to_default(self, client, project):
        response = client.get("/backend/device-platform/")
        assert response.status_code == 302
        assert f"branch={project.default_branch_id}" in response.url

    def test_cross_project_branch_id_404(self, client, project, group):
        other = Project.objects.create(group=group, name="其他", slug="other", default_branch=None)
        other_main = Branch.objects.create(project=other, name="main")
        response = client.get(f"/backend/device-platform/?branch={other_main.id}")
        assert response.status_code == 404

    def test_invalid_branch_id_404(self, client, project):
        response = client.get("/backend/device-platform/?branch=not-a-uuid")
        assert response.status_code == 404

    def test_switch_preserves_path(self, client, project, branch):
        add_dir(branch, "src")
        add_file(branch, "src/app.py", b"x")
        copied = create_branch(project_id=project.id, name="dev", source_branch_id=branch.id)
        response = client.get(f"/backend/device-platform/tree/src/?branch={copied.id}")
        assert response.status_code == 200
        html = response.content.decode()
        # 分支菜单里的切换链接保持 tree/src 路径（模式本身无尾斜杠）
        assert f"/backend/device-platform/tree/src?branch={branch.id}" in html

    def test_missing_path_keeps_branch_context(self, client, project, branch):
        empty = create_branch(project_id=project.id, name="empty")
        response = client.get(f"/backend/device-platform/tree/src/?branch={empty.id}")
        assert response.status_code == 200
        html = response.content.decode()
        assert "此路径在该分支中不存在" in html
        assert "返回该分支根目录" in html
        assert f"?branch={empty.id}" in html


class TestAdminBranchViews:
    def test_list_requires_staff(self, client, project):
        response = client.get(reverse("admin:catalog-project-branches", args=[project.pk]))
        assert response.status_code == 302
        assert "/admin/login/" in response.url

    def test_list_shows_branches(self, admin_client, project, branch):
        create_branch(project_id=project.id, name="dev")
        response = admin_client.get(reverse("admin:catalog-project-branches", args=[project.pk]))
        assert response.status_code == 200
        html = response.content.decode()
        assert "main" in html and "dev" in html

    def test_create_empty_via_post(self, admin_client, project):
        url = reverse("admin:catalog-project-branch-create", args=[project.pk])
        response = admin_client.post(url, {"name": "feature", "mode": "empty"})
        assert response.status_code == 302
        assert Branch.objects.filter(project=project, name="feature").exists()

    def test_create_copy_via_post(self, admin_client, project, branch):
        add_file(branch, "README.md", b"readme")
        url = reverse("admin:catalog-project-branch-create", args=[project.pk])
        response = admin_client.post(url, {
            "name": "release", "mode": "copy",
            "source_branch": str(branch.id), "expected_revision": str(branch.revision),
        })
        assert response.status_code == 302
        copied = Branch.objects.get(project=project, name="release")
        assert Entry.objects.filter(branch=copied, path="README.md").exists()

    def test_create_duplicate_shows_error(self, admin_client, project):
        url = reverse("admin:catalog-project-branch-create", args=[project.pk])
        response = admin_client.post(url, {"name": "main", "mode": "empty"})
        assert response.status_code == 200
        assert "已存在" in response.content.decode()

    def test_get_delete_page_has_no_side_effects(self, admin_client, project, branch):
        other = create_branch(project_id=project.id, name="dev")
        url = reverse("admin:catalog-project-branch-delete", args=[project.pk, other.id])
        response = admin_client.get(url)
        assert response.status_code == 200
        assert Branch.objects.filter(pk=other.id).exists()

    def test_post_delete_removes_branch(self, admin_client, project, branch):
        other = create_branch(project_id=project.id, name="dev")
        url = reverse("admin:catalog-project-branch-delete", args=[project.pk, other.id])
        response = admin_client.post(url)
        assert response.status_code == 302
        assert not Branch.objects.filter(pk=other.id).exists()

    def test_post_delete_default_shows_error(self, admin_client, project, branch):
        url = reverse("admin:catalog-project-branch-delete", args=[project.pk, branch.id])
        response = admin_client.post(url)
        assert response.status_code == 302  # 错误消息 + 返回列表
        assert Branch.objects.filter(pk=branch.id).exists()

    def test_delete_with_next_returns_to_project(self, admin_client, project, branch):
        other = create_branch(project_id=project.id, name="dev")
        url = reverse("admin:catalog-project-branch-delete", args=[project.pk, other.id])
        response = admin_client.post(url, {"next": f"/backend/device-platform/?branch={other.id}"})
        assert response.status_code == 302
        # next 指向已删除分支 → 回默认根目录
        assert response.url == "/backend/device-platform/"
