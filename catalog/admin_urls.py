"""管理区扩展路由：分支管理等带上下文的管理入口。

挂载进 GitODSAdminSite（/admin/ 前缀下），视图在 admin_site.get_urls 里
统一包一层 admin_view（登录 + staff 检查），不依赖模板隐藏按钮。
"""
from django.urls import path

from catalog.admin_site import site
from catalog.admin_views import branches as branch_views

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
]
