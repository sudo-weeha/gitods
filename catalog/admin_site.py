"""GitODS 管理站点：复用 Django Admin 能力，叠加登录失败限流。

限流按用户名 + IP 组合（HMAC scope_key）记录在 PostgreSQL，
15 分钟内 5 次失败后返回 429，成功登录清除计数。
"""
from django.contrib import admin
from django.contrib.auth.forms import AuthenticationForm
from django.template.response import TemplateResponse

from catalog.services import login_throttle


class GitODSAdminSite(admin.AdminSite):
    site_header = "GitODS 管理"
    site_title = "GitODS 管理"
    index_title = "内容管理"

    def login(self, request, extra_context=None):
        if request.method == "POST":
            username = request.POST.get("username") or ""
            ip = login_throttle.client_ip(request)
            throttled, wait_minutes = login_throttle.is_throttled(username, ip)
            if throttled:
                return self._throttled_response(request, wait_minutes)
            response = super().login(request, extra_context)
            if request.user.is_authenticated:
                login_throttle.clear_failures(username, ip)
            else:
                login_throttle.record_failure(username, ip)
            return response
        return super().login(request, extra_context)

    def _throttled_response(self, request, wait_minutes):
        form = AuthenticationForm(request=request)
        # 未绑定表单不能走 add_error（无 _errors/cleaned_data），手工构造错误
        from django.forms.utils import ErrorDict

        form._errors = ErrorDict(
            {"__all__": form.error_class([f"登录尝试次数过多，请约 {wait_minutes} 分钟后再试。"])}
        )
        form.cleaned_data = {}
        context = {
            **self.each_context(request),
            "title": "登录",
            "app_path": request.get_full_path(),
            "form": form,
        }
        return TemplateResponse(request, "admin/login.html", context, status=429)


site = GitODSAdminSite(name="admin")


def register(*models):
    """装饰器形式的注册，等价 django.contrib.admin.register，但指向本站点。

    AdminSite.register 返回 None，不能直接用作装饰器。
    """

    def wrapper(admin_class):
        if not models:
            raise ValueError("register() 需要至少一个模型")
        site.register(models, admin_class=admin_class)
        return admin_class

    return wrapper
