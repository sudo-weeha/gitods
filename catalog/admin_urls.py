"""管理区扩展路由：分支管理等带上下文的管理入口。

挂载进 GitODSAdminSite（/admin/ 前缀下），视图在 admin_site.get_urls 里
统一包一层 admin_view（登录 + staff 检查），不依赖模板隐藏按钮。
"""
from django.urls import path

from catalog.admin_site import site
from catalog.admin_views import branches as branch_views
from catalog.admin_views import entries as file_views
from catalog.admin_views import imports as import_views

urlpatterns = [
    path(
        "catalog/project/<uuid:project_id>/branches/",
        site.admin_view(branch_views.branch_list),
        name="catalog-project-branches",
    ),
    path(
        "catalog/project/<uuid:project_id>/branches/create/",
        site.admin_view(branch_views.branch_create),
        name="catalog-project-branch-create",
    ),
    path(
        "catalog/project/<uuid:project_id>/branches/<uuid:branch_id>/set-default/",
        site.admin_view(branch_views.branch_set_default),
        name="catalog-project-branch-set-default",
    ),
    path(
        "catalog/project/<uuid:project_id>/branches/<uuid:branch_id>/delete/",
        site.admin_view(branch_views.branch_delete),
        name="catalog-project-branch-delete",
    ),
    # 文件维护（上传、新建目录、重命名、删除）
    path(
        "catalog/project/<uuid:project_id>/files/",
        site.admin_view(file_views.files_manage),
        name="catalog-project-files",
    ),
    path(
        "catalog/project/<uuid:project_id>/files/upload/",
        site.admin_view(file_views.files_upload),
        name="catalog-project-files-upload",
    ),
    path(
        "catalog/project/<uuid:project_id>/files/folder/",
        site.admin_view(file_views.files_folder),
        name="catalog-project-files-folder",
    ),
    path(
        "catalog/project/<uuid:project_id>/files/<uuid:entry_id>/rename/",
        site.admin_view(file_views.files_rename),
        name="catalog-project-files-rename",
    ),
    path(
        "catalog/project/<uuid:project_id>/files/<uuid:entry_id>/delete/",
        site.admin_view(file_views.files_delete),
        name="catalog-project-files-delete",
    ),
    # ZIP 导入（两步：上传→预览确认→发布）
    path(
        "catalog/project/<uuid:project_id>/imports/",
        site.admin_view(import_views.import_home),
        name="catalog-project-imports",
    ),
    path(
        "catalog/project/<uuid:project_id>/imports/new/",
        site.admin_view(import_views.import_new),
        name="catalog-project-import-new",
    ),
    path(
        "catalog/project/<uuid:project_id>/imports/<uuid:draft_id>/",
        site.admin_view(import_views.import_preview),
        name="catalog-project-import-preview",
    ),
    path(
        "catalog/project/<uuid:project_id>/imports/<uuid:draft_id>/option/",
        site.admin_view(import_views.import_option),
        name="catalog-project-import-option",
    ),
    path(
        "catalog/project/<uuid:project_id>/imports/<uuid:draft_id>/publish/",
        site.admin_view(import_views.import_publish),
        name="catalog-project-import-publish",
    ),
    path(
        "catalog/project/<uuid:project_id>/imports/<uuid:draft_id>/cancel/",
        site.admin_view(import_views.import_cancel),
        name="catalog-project-import-cancel",
    ),
]
