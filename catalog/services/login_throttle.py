"""登录失败限流：用户名与 IP 组合的 HMAC scope_key，经 PostgreSQL 共享。

多 Gunicorn worker / 多进程下均读到同一份失败记录，不使用进程内字典。
成功登录清除该组合的全部失败记录。
"""
import hashlib
import hmac
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from catalog.models import LoginAttempt


def client_ip(request) -> str:
    """只信任 REMOTE_ADDR：反向代理头仅由本机 Caddy 设置，应用不重复解析。"""
    return (request.META.get("REMOTE_ADDR") or "").strip()


def scope_key(username: str, ip: str) -> str:
    payload = f"{(username or '').strip().lower()}\n{ip or ''}".encode("utf-8")
    return hmac.new(settings.SECRET_KEY.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _limits() -> dict:
    return settings.GITODS_LIMITS


def is_throttled(username: str, ip: str) -> tuple[bool, int]:
    """返回 (是否已限流, 建议等待分钟数)。"""
    window_minutes = _limits()["LOGIN_FAIL_WINDOW_MINUTES"]
    window_start = timezone.now() - timedelta(minutes=window_minutes)
    failures = LoginAttempt.objects.filter(
        scope_key=scope_key(username, ip), created_at__gte=window_start
    ).count()
    if failures >= _limits()["LOGIN_FAIL_LIMIT"]:
        return True, window_minutes
    return False, 0


def record_failure(username: str, ip: str) -> None:
    LoginAttempt.objects.create(scope_key=scope_key(username, ip))


def clear_failures(username: str, ip: str) -> None:
    LoginAttempt.objects.filter(scope_key=scope_key(username, ip)).delete()
