"""e2e 目录专用配置。

pytest-playwright 的同步 page 夹具运行在事件循环线程上，种子夹具的
同步 ORM 调用会触发 Django 的异步安全保护；测试进程内放行不影响
被测代码——live server 运行在独立线程的真实同步环境。
"""
import os

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
