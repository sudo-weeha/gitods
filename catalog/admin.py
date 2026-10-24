"""Django Admin 注册：元数据表单全部经服务层写入。

- 新增分组/项目调用 services.projects；删除经服务层校验（非空分组拒绝）
- 输入校验优先在表单层完成（用户可见错误）；服务层兜底并发竞态
- URL 标识创建后固定（表单 + readonly 双保险）
- 禁用批量删除 action，删除只能逐个走带校验的服务层
- Entry / Blob / ImportDraft / LoginAttempt 不提供原始模型编辑页
"""
from django.contrib import admin
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.urls import reverse

from catalog.admin_site import register
from catalog.forms import GroupForm, ProjectForm
from catalog.models import Branch, Group, Project
from catalog.services import projects
from catalog.services.exceptions import ServiceError


def _catch_service_error(changelist_url_name):
    """save_model 中服务层异常（表单校验漏掉的并发竞态）转错误提示而非 500。"""

    def decorator(view_func):
        def wrapper(self, request, *args, **kwargs):
            try:
                return view_func(self, request, *args, **kwargs)
            except ServiceError as exc:
                self.message_user(request, f"保存失败：{exc}", level=messages.ERROR)
                return HttpResponseRedirect(reverse(changelist_url_name))

        return wrapper

    return decorator

class SlugPrepopulateOnAddOnly:
    """slug 自动填充仅在新增页：编辑页 slug 只读，与 prepopulate 同用会 KeyError。"""

    def get_prepopulated_fields(self, request, obj=None):
        return {"slug": ("name",)} if obj is None else {}


@register(Group)
class GroupAdmin(SlugPrepopulateOnAddOnly, admin.ModelAdmin):
    form = GroupForm
    list_display = ("name", "slug", "sort_order", "project_count", "updated_at")
    search_fields = ("name", "slug")
    list_editable = ("sort_order",)
    actions = None  # 禁用批量删除

    @admin.display(description="项目数")
    def project_count(self, obj):
        return obj.projects.count()

    def get_readonly_fields(self, request, obj=None):
        return ("slug",) if obj else ()

    @_catch_service_error("admin:catalog_group_changelist")
    def add_view(self, request, form_url="", extra_context=None):
        return super().add_view(request, form_url, extra_context)

    @_catch_service_error("admin:catalog_group_changelist")
    def change_view(self, request, object_id, form_url="", extra_context=None):
        return super().change_view(request, object_id, form_url, extra_context)

    def save_model(self, request, obj, form, change):
        if not change:
            created = projects.create_group(
                name=obj.name,
                slug=obj.slug,
                description=obj.description,
                icon=obj.icon,
                sort_order=obj.sort_order,
            )
            obj.pk = created.pk
            obj._state.adding = False
        else:
            obj.save()

    def delete_model(self, request, obj):
        projects.delete_group(group_id=obj.pk)

    def delete_view(self, request, object_id, extra_context=None):
        try:
            return super().delete_view(request, object_id, extra_context)
        except ServiceError as exc:
            self.message_user(request, f"删除失败：{exc}", level=messages.ERROR)
            return HttpResponseRedirect(reverse("admin:catalog_group_changelist"))


@register(Project)
class ProjectAdmin(SlugPrepopulateOnAddOnly, admin.ModelAdmin):
    form = ProjectForm
    list_display = ("name", "group", "slug", "default_branch", "language", "updated_at")
    list_filter = ("group",)
    list_select_related = ("group", "default_branch")
    search_fields = ("name", "slug", "description")
    actions = None  # 禁用批量删除

    def get_readonly_fields(self, request, obj=None):
        # default_branch 只能经专用服务（任务 4）修改；slug 创建后固定
        base = ("default_branch",)
        return base + ("slug",) if obj else base

    @_catch_service_error("admin:catalog_project_changelist")
    def add_view(self, request, form_url="", extra_context=None):
        return super().add_view(request, form_url, extra_context)

    @_catch_service_error("admin:catalog_project_changelist")
    def change_view(self, request, object_id, form_url="", extra_context=None):
        return super().change_view(request, object_id, form_url, extra_context)

    def save_model(self, request, obj, form, change):
        if not change:
            created = projects.create_project(
                group=obj.group,
                name=obj.name,
                slug=obj.slug,
                description=obj.description,
                language=obj.language,
                license_name=obj.license_name,
            )
            obj.pk = created.pk
            obj.default_branch = created.default_branch
            obj._state.adding = False
        else:
            if "group" in form.changed_data:
                projects.move_project(project_id=obj.pk, target_group_id=obj.group_id)
            obj.save()

    def delete_model(self, request, obj):
        projects.delete_project(project_id=obj.pk)

    def delete_view(self, request, object_id, extra_context=None):
        try:
            return super().delete_view(request, object_id, extra_context)
        except ServiceError as exc:
            self.message_user(request, f"删除失败：{exc}", level=messages.ERROR)
            return HttpResponseRedirect(reverse("admin:catalog_project_changelist"))



@register(Branch)
class BranchAdmin(admin.ModelAdmin):
    """分支只读列表：创建、设默认、删除走专用管理视图（任务 4 接入）。"""

    list_display = ("name", "project", "revision", "is_default", "updated_at")
    list_filter = ("project__group",)
    list_select_related = ("project", "project__group")
    search_fields = ("name", "project__name")
    readonly_fields = ("project", "name", "revision", "created_at", "updated_at")
    actions = None

    @admin.display(description="默认分支")
    def is_default(self, obj):
        return obj.pk == obj.project.default_branch_id

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
