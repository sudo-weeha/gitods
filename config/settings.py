"""GitODS Django 配置。

环境差异全部通过环境变量 / .env 注入（变量说明见 .env.example）。
默认 DEBUG=False；本地开发需在 .env 中显式打开。
"""
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    CSRF_TRUSTED_ORIGINS=(list, []),
    SITE_ORIGIN=(str, "http://127.0.0.1:8000"),
    DATA_ROOT=(str, str(BASE_DIR / "var" / "data")),
)
environ.Env.read_env(BASE_DIR / ".env")

DEBUG = env("DEBUG")

SECRET_KEY = env("SECRET_KEY", default="")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "dev-insecure-secret-key-not-for-production"
    else:
        raise ImproperlyConfigured("生产环境必须通过 SECRET_KEY 环境变量提供密钥")

ALLOWED_HOSTS = env("ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env("CSRF_TRUSTED_ORIGINS")
SITE_ORIGIN = env("SITE_ORIGIN")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "catalog.apps.CatalogConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# 只允许 PostgreSQL；禁用 SQLite 替身
DATABASES = {"default": env.db("DATABASE_URL", default="postgres:///gitods")}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
if DEBUG:
    STORAGES["staticfiles"]["BACKEND"] = "whitenoise.storage.CompressedStaticFilesStorage"
    WHITENOISE_AUTOREFRESH = True
    WHITENOISE_USE_FINDERS = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/admin/login/"
LOGIN_REDIRECT_URL = "/explore/"

# 会话与安全：12 小时会话；生产 Secure Cookie
SESSION_COOKIE_AGE = 12 * 60 * 60
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # 代理头只信任本机 nginx：应用端口不对公网暴露
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=0)
    if SECURE_HSTS_SECONDS:
        SECURE_HSTS_INCLUDE_SUBDOMAINS = True

# 私有数据目录：不可变对象 / 临时文件 / 主机内文件锁，绝不作为静态目录公开
_data_root = Path(env("DATA_ROOT"))
DATA_ROOT = _data_root if _data_root.is_absolute() else BASE_DIR / _data_root
# 内存转磁盘阈值；文件大小上限由服务层按实际读入字节计数执行
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024

# GitODS 容量与行为限制（设计文档 5.4）
M = 1024 * 1024
GITODS_LIMITS = {
    "MAX_FILE_BYTES": 20 * M,
    "MAX_UPLOAD_BATCH_BYTES": 50 * M,
    "MAX_ZIP_BYTES": 50 * M,
    "MAX_ZIP_UNCOMPRESSED_BYTES": 200 * M,
    "MAX_BRANCH_BYTES": 200 * M,
    "MAX_BRANCH_ENTRIES": 10_000,
    "MAX_PATH_DEPTH": 32,
    "MAX_PATH_LENGTH": 1024,
    "MAX_SEGMENT_BYTES": 255,
    "MAX_BRANCHES_PER_PROJECT": env.int("GITODS_MAX_BRANCHES_PER_PROJECT", default=20),
    "TEXT_PREVIEW_MAX_BYTES": M,
    "TEXT_PREVIEW_MAX_LINES": 20_000,
    "HIGHLIGHT_MAX_BYTES": 200 * 1024,
    "HIGHLIGHT_MAX_LINES": 5_000,
    "IMAGE_MAX_BYTES": 20 * M,
    "IMAGE_MAX_PIXELS": 20_000_000,
    "MIN_FREE_DISK_BYTES": 1024 * 1024 * 1024,
    "IMPORT_DRAFT_TTL_MINUTES": env.int("GITODS_IMPORT_DRAFT_TTL_MINUTES", default=30),
    "LOGIN_FAIL_LIMIT": env.int("GITODS_LOGIN_FAIL_LIMIT", default=5),
    "LOGIN_FAIL_WINDOW_MINUTES": env.int("GITODS_LOGIN_FAIL_WINDOW_MINUTES", default=15),
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
}
