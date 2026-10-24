from django.urls import include, path
from django.views.generic import RedirectView

from catalog.views import healthz

# 确保模型注册到自定义 AdminSite（含登录限流）
import catalog.admin  # noqa: F401,E402
from catalog.admin_site import site  # noqa: E402

urlpatterns = [
    path("", RedirectView.as_view(url="/explore/", permanent=False)),
    path("admin/", site.urls),
    path("healthz/", healthz, name="healthz"),
    path("", include("catalog.urls")),
]
