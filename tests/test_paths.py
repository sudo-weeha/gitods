"""逻辑路径规范化测试：合法输入、拒绝规则与 NFC 统一。"""
import unicodedata

import pytest
from django.conf import settings as dj_settings

from catalog.services.exceptions import ValidationError
from catalog.services.paths import (
    join_path,
    name_of,
    normalize_name,
    normalize_path,
    parent_of,
)

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "path", ["../x", "/etc/passwd", "a/../../x", "a\\b", "a/./b", "a\x00b"]
)
def test_reject_unsafe_logical_path(path):
    with pytest.raises(ValidationError):
        normalize_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "a//b",          # 空段
        "a/",            # 尾随斜杠产生空段
        "a/.",           # 当前段
        "a/b/../c",      # 中间 ..
        "..",
        ".",
        "a\tb",          # 控制字符
        "a\x1bb",
        "a\x7fb",
    ],
)
def test_reject_malformed_paths(path):
    with pytest.raises(ValidationError):
        normalize_path(path)


def test_root_is_empty_string():
    assert normalize_path("") == ""


@pytest.mark.parametrize("path", ["README.md", "src/main.py", "文档/架构说明.md", "带 空格/a b.py", "a.b-c_d~e/中文 名.txt"])
def test_accept_valid_paths(path):
    assert normalize_path(path) == path


def test_nfc_normalization():
    composed = unicodedata.normalize("NFC", "é")
    decomposed = unicodedata.normalize("NFD", "é")
    assert composed != decomposed
    assert normalize_path(f"文档/{decomposed}.md") == f"文档/{composed}.md"


def test_path_length_limit(settings):
    limits = {**dj_settings.GITODS_LIMITS, "MAX_PATH_LENGTH": 16}
    settings.GITODS_LIMITS = limits
    with pytest.raises(ValidationError, match="路径过长"):
        normalize_path("a" * 17)
    assert normalize_path("a" * 16) == "a" * 16


def test_path_depth_limit(settings):
    settings.GITODS_LIMITS = {
        **dj_settings.GITODS_LIMITS, "MAX_PATH_DEPTH": 3
    }
    with pytest.raises(ValidationError, match="层级过深"):
        normalize_path("a/b/c/d")
    assert normalize_path("a/b/c") == "a/b/c"


def test_segment_byte_limit(settings):
    settings.GITODS_LIMITS = {
        **dj_settings.GITODS_LIMITS, "MAX_SEGMENT_BYTES": 6
    }
    with pytest.raises(ValidationError, match="单段"):
        normalize_path("中文中文中文")  # 9 字节 > 6
    assert normalize_path("中文") == "中文"  # 6 字节


class TestNameHelpers:
    def test_normalize_name(self):
        assert normalize_name("设备 配置.yaml") == "设备 配置.yaml"

    @pytest.mark.parametrize("name", ["", None, "a/b", "..", ".", "a//b"])
    def test_normalize_name_rejects(self, name):
        with pytest.raises(ValidationError):
            normalize_name(name)

    def test_parent_name_join(self):
        assert parent_of("a/b/c.txt") == "a/b"
        assert parent_of("a") == ""
        assert name_of("a/b/c.txt") == "c.txt"
        assert join_path("a/b", "c.txt") == "a/b/c.txt"
        assert join_path("", "c.txt") == "c.txt"
