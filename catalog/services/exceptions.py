"""业务服务层异常与共享约定。任务 3 起使用，管理视图映射到设计文档的响应与表单错误。"""
from django.core.exceptions import PermissionDenied


class ServiceError(Exception):
    """服务层业务错误基类。"""


class ValidationError(ServiceError):
    """输入或状态不满足业务约束（映射 400 / 表单错误）。"""


class NotFound(ServiceError):
    """对象不存在（映射 404）。"""


class Conflict(ServiceError):
    """并发或唯一性冲突（映射 409）。"""


class RevisionConflict(Conflict):
    """expected_revision 过期：内容已被其他会话修改，提示重新加载。"""


class CapacityExceeded(ServiceError):
    """超出容量或限制（映射 413 / 507）。"""
