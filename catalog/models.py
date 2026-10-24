"""GitODS 业务模型：分组、项目、分支、不可变对象、文件条目、导入草稿与登录限流记录。

依据设计文档 5.2 节。模型只承载元数据与数据库约束；
所有业务写入必须经 catalog.services 服务层，绕过服务层直接改库不受支持。
"""
import uuid

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

slug_validator = RegexValidator(
    regex=r"^[a-z0-9][a-z0-9-]{1,49}$",
    message="URL 标识仅允许 2–50 位小写字母、数字和连字符，且以字母或数字开头。",
)

branch_name_validator = RegexValidator(
    regex=r"^(?!.*\.\.)[a-z0-9][a-z0-9._-]{0,63}$",
    message="分支名须为 1–64 位小写字母、数字、点、下划线或连字符，以字母或数字开头，且不允许连续两个点。",
)


class Group(models.Model):
    """一级分组；自由创建、排序和管理。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField("名称", max_length=40, unique=True)
    slug = models.SlugField("URL 标识", max_length=50, unique=True, validators=[slug_validator])
    description = models.CharField("简介", max_length=200, blank=True, default="")
    icon = models.CharField("线性图标名", max_length=64, blank=True, default="folder")
    sort_order = models.IntegerField("排序值", default=0)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "分组"
        verbose_name_plural = "分组"

    def __str__(self):
        return self.name


class Project(models.Model):
    """项目，属于一个分组；默认分支只能经专用服务维护。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    group = models.ForeignKey(
        Group, on_delete=models.PROTECT, related_name="projects", verbose_name="所属分组"
    )
    name = models.CharField("名称", max_length=80)
    slug = models.SlugField("URL 标识", max_length=50, validators=[slug_validator])
    description = models.CharField("简介", max_length=500, blank=True, default="")
    language = models.CharField("主语言", max_length=64, blank=True, default="")
    # 数据库允许为空，用于创建与删除的中间步骤；正常存活项目一定有默认分支，
    # 由服务层保证，普通 Admin 表单不可编辑该字段。
    default_branch = models.ForeignKey(
        "Branch",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="default_for_projects",
        verbose_name="默认分支",
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-updated_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["group", "slug"], name="uniq_project_group_slug"),
        ]
        verbose_name = "项目"
        verbose_name_plural = "项目"

    def __str__(self):
        return f"{self.group.name} / {self.name}"


class Branch(models.Model):
    """实际分支：每个分支拥有独立 Entry 集合，可共享不可变 Blob。

    revision 仅用于并发校验，不是提交记录。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="branches", verbose_name="所属项目"
    )
    name = models.CharField("分支名", max_length=64, validators=[branch_name_validator])
    revision = models.PositiveIntegerField("并发基准", default=1)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["project", "name"], name="uniq_branch_project_name"),
            models.CheckConstraint(
                condition=models.Q(revision__gte=1), name="branch_revision_positive"
            ),
        ]
        verbose_name = "分支"
        verbose_name_plural = "分支"

    def __str__(self):
        return f"{self.project.slug}@{self.name}"


class Blob(models.Model):
    """不可变内容对象；对象键从 sha256 派生，内容创建后永不改写。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sha256 = models.CharField("SHA-256", max_length=64, unique=True)
    size = models.PositiveBigIntegerField("字节数")
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "对象"
        verbose_name_plural = "对象"

    def __str__(self):
        return self.sha256[:12]


class Entry(models.Model):
    """分支内的目录/文件条目。

    path 是无前导 “/” 的规范化相对路径，根目录用空字符串表示但不创建根 Entry；
    目录 blob 为空且 size=0；文件必须有 Blob。
    """

    KIND_DIR = "dir"
    KIND_FILE = "file"
    KIND_CHOICES = [(KIND_DIR, "目录"), (KIND_FILE, "文件")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name="entries", verbose_name="所属分支"
    )
    path = models.CharField("逻辑路径", max_length=1024, editable=False)
    parent_path = models.CharField(
        "父目录路径", max_length=1024, blank=True, default="", editable=False
    )
    name = models.CharField("名称", max_length=255, editable=False)
    kind = models.CharField("类型", max_length=4, choices=KIND_CHOICES, editable=False)
    size = models.PositiveBigIntegerField("大小", default=0, editable=False)
    blob = models.ForeignKey(
        Blob,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="entries",
        verbose_name="内容对象",
    )
    updated_at = models.DateTimeField("更新时间", default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["branch", "path"], name="uniq_entry_branch_path"),
            # 约束须对所有行成立：目录行必须无 blob 且 size=0；文件行必须有 blob
            models.CheckConstraint(
                condition=models.Q(kind="dir", blob__isnull=True, size=0)
                | ~models.Q(kind="dir"),
                name="entry_dir_no_blob",
            ),
            models.CheckConstraint(
                condition=models.Q(kind="file", blob__isnull=False) | ~models.Q(kind="file"),
                name="entry_file_has_blob",
            ),
        ]
        indexes = [
            models.Index(fields=["branch", "parent_path"], name="idx_entry_branch_parent"),
        ]
        verbose_name = "条目"
        verbose_name_plural = "条目"

    def __str__(self):
        return f"{self.branch.name}:{self.path}"


class ImportDraft(models.Model):
    """一次性 ZIP 导入暂存单，30 分钟有效；不是任务队列或版本历史。"""

    STATUS_READY = "ready"
    STATUS_PUBLISHED = "published"
    STATUS_CANCELLED = "cancelled"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = [
        (STATUS_READY, "等待确认"),
        (STATUS_PUBLISHED, "已发布"),
        (STATUS_CANCELLED, "已取消"),
        (STATUS_EXPIRED, "已过期"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project, on_delete=models.PROTECT, related_name="import_drafts", verbose_name="项目"
    )
    branch = models.ForeignKey(
        Branch, on_delete=models.PROTECT, related_name="import_drafts", verbose_name="目标分支"
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="import_drafts",
        verbose_name="发起管理员",
    )
    expected_revision = models.PositiveIntegerField("创建时的并发基准")
    status = models.CharField("状态", max_length=10, choices=STATUS_CHOICES, default=STATUS_READY)
    strip_root = models.BooleanField("移除最外层目录", default=True)
    # 服务器私有临时清单文件（DATA_ROOT/tmp 下），不信任用户回传的清单参数
    manifest_path = models.CharField("临时清单路径", max_length=1024, blank=True, default="")
    file_count = models.PositiveIntegerField("文件数", default=0)
    total_size = models.PositiveBigIntegerField("总字节数", default=0)
    result = models.CharField("结果摘要", max_length=200, blank=True, default="")
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    expires_at = models.DateTimeField("过期时间")
    published_at = models.DateTimeField("发布时间", null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["owner"], name="idx_importdraft_owner"),
            models.Index(fields=["expires_at"], name="idx_importdraft_expires"),
        ]
        verbose_name = "导入草稿"
        verbose_name_plural = "导入草稿"

    def __str__(self):
        return f"{self.project.slug}@{self.branch.name} {self.status}"


class LoginAttempt(models.Model):
    """登录失败限流记录：scope_key 为用户名与 IP 组合的 HMAC。

    多 Gunicorn worker 通过 PostgreSQL 共享，跨 worker 生效。
    """

    id = models.BigAutoField(primary_key=True)
    scope_key = models.CharField("限流键", max_length=128)
    created_at = models.DateTimeField("时间", auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["scope_key", "created_at"], name="idx_loginattempt_scope_time"),
        ]
        verbose_name = "登录尝试"
        verbose_name_plural = "登录尝试"

    def __str__(self):
        return f"{self.scope_key[:16]}…"
