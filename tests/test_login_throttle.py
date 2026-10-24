"""登录失败限流：15 分钟窗口内 5 次失败后 429，成功清除计数，按用户名+IP 隔离。"""
import pytest

from catalog.models import LoginAttempt
from catalog.services.login_throttle import scope_key

pytestmark = pytest.mark.django_db

LOGIN_URL = "/admin/login/"
GOOD = {"username": "test-admin", "password": "Test-only-Long-Password-42"}


def _fail(client, times=1, **meta):
    for _ in range(times):
        client.post(LOGIN_URL, {"username": "test-admin", "password": "totally-wrong"})


class TestThrottle:
    def test_locked_after_five_failures(self, client, admin_user):
        _fail(client, 5)
        response = client.post(LOGIN_URL, GOOD)
        assert response.status_code == 429
        assert "次数过多" in response.content.decode()

    def test_below_limit_still_allowed(self, client, admin_user):
        _fail(client, 4)
        response = client.post(LOGIN_URL, GOOD)
        assert response.status_code == 302

    def test_success_clears_counter(self, client, admin_user):
        _fail(client, 4)
        assert client.post(LOGIN_URL, GOOD).status_code == 302
        client.logout()
        _fail(client, 4)
        assert client.post(LOGIN_URL, GOOD).status_code == 302
        assert not LoginAttempt.objects.exists()

    def test_other_ip_not_affected(self, client, admin_user):
        _fail(client, 5)  # 127.0.0.1 被限流
        response = client.post(LOGIN_URL, GOOD, REMOTE_ADDR="10.9.9.9")
        assert response.status_code == 302

    def test_other_username_not_affected(self, client, admin_user):
        _fail(client, 5)
        client.post(LOGIN_URL, {"username": "someone-else", "password": "x"})
        # 不同用户名仍是独立计数，不叠加
        assert LoginAttempt.objects.count() == 6

    def test_scope_key_is_hmac_not_plaintext(self):
        key = scope_key("admin", "1.2.3.4")
        assert len(key) == 64
        assert "admin" not in key and "1.2.3.4" not in key

    def test_scope_key_case_insensitive_username(self):
        assert scope_key("Admin", "1.2.3.4") == scope_key("admin", "1.2.3.4")
