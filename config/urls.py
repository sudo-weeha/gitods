from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from catalog.views import healthz

urlpatterns = [
    path("", RedirectView.as_view(url="/explore/", permanent=False)),
    path("admin/", admin.site.urls),
    path("healthz/", healthz, name="healthz"),
    path("", include("catalog.urls")),
]
