"""公开只读路由：发现页与项目目录/源码页。

分组 URL 标识的系统保留词（admin/api/explore/static/healthz）由服务层
在创建时校验，避免与此处路由冲突。
"""
from django.urls import path

from catalog.views import content, explore, project

urlpatterns = [
    path("explore/", explore.explore, name="explore"),
    path("<slug:group_slug>/<slug:project_slug>/", project.project_root, name="project-home"),
    path(
        "<slug:group_slug>/<slug:project_slug>/tree/",
        project.project_root,
        name="project-tree-root",
    ),
    path(
        "<slug:group_slug>/<slug:project_slug>/tree/<path:rel_path>",
        project.project_tree,
        name="project-tree",
    ),
    path(
        "<slug:group_slug>/<slug:project_slug>/blob/<path:rel_path>",
        project.project_blob,
        name="project-blob",
    ),
    path(
        "<slug:group_slug>/<slug:project_slug>/raw/<path:rel_path>",
        content.raw_file,
        name="project-raw",
    ),
]
