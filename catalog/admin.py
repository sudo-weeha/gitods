"""Django Admin 注册。

仅暴露元数据表单；分支只读展示，Entry / Blob / ImportDraft / LoginAttempt
不提供原始模型编辑页，所有业务写入必须经 catalog.services 服务层。
"""
from django.contrib import admin

from catalog.models import Branch, Group, Project


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "sort_order", "project_count", "updated_at")
    search_fields = ("name", "slug")
    list_editable = ("sort_order",)
    prepopulated_fields = {"slug": ("name",)}

    @admin.display(description="项目数")
    def project_count(self, obj):
        return obj.projects.count()

    def get_readonly_fields(self, request, obj=None):
        # URL 标识创建后固定
        return ("slug",) if obj else ()


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "group", "slug", "default_branch", "language", "updated_at")
    list_filter = ("group",)
    list_select_related = ("group", "default_branch")
    search_fields = ("name", "slug", "description")
    fields = ("group", "name", "slug", "description", "language", "license_name")
    prepopulated_fields = {"slug": ("name",)}

    def get_readonly_fields(self, request, obj=None):
        # default_branch 只能经专用服务修改；slug 创建后固定
        base = ("default_branch",)
        return base + ("slug",) if obj else base


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    """分支只读列表：创建、设默认、删除走专用管理视图（任务 4 接入）。"""

    list_display = ("name", "project", "revision", "is_default", "updated_at")
    list_filter = ("project__group",)
    list_select_related = ("project", "project__group")
    search_fields = ("name", "project__name")
    readonly_fields = ("project", "name", "revision", "created_at", "updated_at")

    @admin.display(description="默认分支")
    def is_default(self, obj):
        return obj.pk == obj.project.default_branch_id

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
