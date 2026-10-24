"""写入开发/验收演示数据：8 个中文分组、12 个真实语义项目、分支与文件。

示例项目仅用于开发/验收：生产环境（DEBUG=False）拒绝执行，
也永远不会在启动或部署流程中自动调用。
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from catalog.models import Branch, Entry, Group, Project
from catalog.services.storage import store_bytes

GROUPS = [
    ("后端服务", "backend", "服务端框架、中间件与业务平台", "server"),
    ("前端组件", "frontend", "Web 组件库与可视化工具", "layout-grid"),
    ("移动应用", "mobile", "Android / iOS / 跨端应用与 SDK", "smartphone"),
    ("数据与算法", "data-algo", "存储引擎、检索与算法服务", "database"),
    ("开发工具", "devtools", "调试、构建与效率工具", "terminal"),
    ("文档资料", "docs", "部署手册、规范与培训材料", "book-open"),
    ("运维部署", "ops", "监控、告警与发布系统", "cloud"),
    ("安全认证", "security", "密钥、审计与访问控制", "shield"),
]

README_MAIN = """# 设备管理平台

面向园区与工厂场景的物联网设备管理平台：统一接入不同厂商的网关与传感器，
提供规则引擎、告警策略与固件灰度升级，支持百万级设备长连接。

## 技术栈

| 层次 | 选型 | 版本 |
| --- | --- | --- |
| 接入层 | Netty + MQTT Broker | 4.1 / 5.0 |
| 服务层 | Spring Boot | 3.3 |
| 存储层 | PostgreSQL + Redis | 16 / 7.2 |
| 部署 | Docker Compose + Caddy | - |

## 快速开始

```bash
# 准备配置并启动
cp config/application.example.yaml config/application.yaml
docker compose up -d db
./gradlew bootRun
```

## 目录结构

- `src/` 服务端源码
- `config/` 运行配置样例
- `docs/` 架构与部署文档，见 [架构说明](docs/架构说明.md)

> 生产部署请先阅读运维手册中的备份恢复章节。
"""

DEVICE_SERVICE_PY = '''"""设备接入服务：会话管理与上行消息分发。"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

Handler = Callable[["DeviceMessage"], Awaitable[None]]


@dataclass
class DeviceMessage:
    device_id: str
    topic: str
    payload: bytes
    qos: int = 0


@dataclass
class DeviceSession:
    """一条长连接对应的设备会话。"""

    device_id: str
    keepalive: int = 60
    topics: set[str] = field(default_factory=set)

    def subscribe(self, topic: str) -> None:
        self.topics.add(topic)
        logger.info("device=%s subscribe topic=%s", self.device_id, topic)


class DeviceService:
    def __init__(self) -> None:
        self._sessions: dict[str, DeviceSession] = {}
        self._handlers: list[Handler] = []

    def register(self, handler: Handler) -> None:
        self._handlers.append(handler)

    async def on_connect(self, device_id: str) -> DeviceSession:
        session = self._sessions.get(device_id)
        if session is None:
            session = DeviceSession(device_id=device_id)
            self._sessions[device_id] = session
        # 超过 keepalive 两倍未收到心跳时由网关侧断开，这里只负责登记；示意行故意写长以验证横向滚动： CONNECT 设备上线后需要同步影子状态、补发离线消息并通知规则引擎重新加载该设备的告警策略配置
        return session

    async def on_message(self, message: DeviceMessage) -> None:
        for handler in self._handlers:
            await handler(message)

    async def on_disconnect(self, device_id: str) -> None:
        self._sessions.pop(device_id, None)
        logger.info("device=%s disconnected", device_id)


async def main() -> None:
    service = DeviceService()
    session = await service.on_connect("gw-0001")
    session.subscribe("sensor/+/temperature")
    await service.on_message(DeviceMessage("gw-0001", "sensor/1/temperature", b"23.5"))


if __name__ == "__main__":
    asyncio.run(main())
'''

RULES_ENGINE_PY = '''"""规则引擎：把指标表达式编译成可执行谓词。"""
import operator
import re

OPS = {"gt": operator.gt, "ge": operator.ge, "lt": operator.lt, "le": operator.le}
EXPR = re.compile(r"^(\\w+)\\s+(gt|ge|lt|le)\\s+([0-9.]+)$")


def compile_rule(expr: str):
    match = EXPR.match(expr.strip())
    if match is None:
        raise ValueError(f"非法规则表达式: {expr}")
    metric, op, bound = match.groups()
    return lambda values: OPS[op](values.get(metric, 0.0), float(bound))
'''

APP_YAML = """# 设备管理平台运行配置样例
server:
  host: 0.0.0.0
  port: 8080
mqtt:
  broker: tcp://127.0.0.1:1883
  keepalive: 60
storage:
  postgres: postgresql://device:change-me@127.0.0.1:5432/device
  redis: redis://127.0.0.1:6379/0
alarm:
  window_seconds: 300
  cooldown_seconds: 900
"""

ARCH_DOC = """# 架构说明

接入层与业务层之间通过内部消息总线解耦，规则引擎消费标准化后的指标流。

## 分层

1. **接入层**：MQTT 网关，维护设备会话与影子状态。
2. **业务层**：设备档案、分组、告警策略与固件任务。
3. **数据层**：PostgreSQL 存档，Redis 缓存热点会话。

## 扩展点

- 新厂商协议在接入层实现 `ProtocolAdapter`。
- 告警动作在业务层实现 `AlarmSink`。
"""

GITIGNORE = """# 构建产物
build/
dist/
*.class

# 本地环境
.env
.env.*
!.env.example

# IDE
.idea/
.vscode/
"""

LICENSE_TEXT = "Apache License\nVersion 2.0, January 2004\nhttp://www.apache.org/licenses/\n\n（演示用许可证文本，完整文本以 Apache 官方站点为准）\n"

CHANGELOG = """# 变更记录（release-1.0）

- 新增固件灰度发布任务
- 规则引擎支持窗口聚合
- 修复设备影子状态在弱网下的丢失问题
"""


def _readme(title: str, desc: str, lang: str, extra: str = "") -> str:
    return f"""# {title}

{desc}

## 特性

- 开箱即用的模块化实现
- 完整的类型标注与单元测试
- 详细的部署文档

{extra}
## 许可证

以仓库根目录 LICENSE 文件为准。
"""


PROJECTS = [
    {
        "group": "backend", "name": "设备管理平台", "slug": "device-platform",
        "desc": "面向园区与工厂场景的物联网设备管理平台：统一接入不同厂商网关与传感器，提供规则引擎、告警策略与固件灰度升级，支持百万级设备长连接与影子状态同步。",
        "lang": "Java", "license": "Apache-2.0", "hours_ago": 2,
        "dirs": ["src", "src/rules", "config", "docs"],
        "files": [
            ("README.md", README_MAIN),
            ("src/device_service.py", DEVICE_SERVICE_PY),
            ("src/rules/engine.py", RULES_ENGINE_PY),
            ("config/application.yaml", APP_YAML),
            ("docs/架构说明.md", ARCH_DOC),
            (".gitignore", GITIGNORE),
            ("LICENSE", LICENSE_TEXT),
        ],
        "release": CHANGELOG,
    },
    {
        "group": "backend", "name": "订单结算中心", "slug": "order-settlement",
        "desc": "承接商城、门店与第三方渠道的订单资金结算：分账、对账与发票流转，幂等接口保证重复回调安全。",
        "lang": "Java", "license": "MIT", "hours_ago": 5,
        "dirs": ["docs"],
        "files": [("README.md", _readme("订单结算中心", "多渠道订单分账与对账服务。", "Java"))],
    },
    {
        "group": "backend", "name": "用户认证服务", "slug": "user-auth-service",
        "desc": "轻量统一认证：多租户、多因素登录、设备指纹与会话风控，兼容 OIDC 协议。",
        "lang": "Go", "license": "Apache-2.0", "hours_ago": 26,
        "dirs": [],
        "files": [("README.md", _readme("用户认证服务", "多租户统一认证与 OIDC 兼容服务。", "Go"))],
    },
    {
        "group": "frontend", "name": "数据表格组件库", "slug": "data-table-kit",
        "desc": "高性能表格组件：虚拟滚动、列固定、单元格编辑与服务端分页，适配主流 Vue 3 生态。",
        "lang": "TypeScript", "license": "MIT", "hours_ago": 8,
        "dirs": [],
        "files": [("README.md", _readme("数据表格组件库", "虚拟滚动与可编辑单元格的 Vue 3 表格组件。", "TS"))],
    },
    {
        "group": "frontend", "name": "可视化表单设计器", "slug": "form-designer",
        "desc": "拖拽式表单设计器：20 余种控件、条件显隐与校验规则配置，Schema 可导入导出。",
        "lang": "Vue", "license": "MIT", "hours_ago": 49,
        "dirs": [],
        "files": [("README.md", _readme("可视化表单设计器", "拖拽式低代码表单设计与渲染。", "Vue"))],
    },
    {
        "group": "mobile", "name": "门店巡检 App", "slug": "store-inspector",
        "desc": "连锁门店巡检客户端：离线任务、扫码点名、拍照回传与整改闭环。",
        "lang": "Kotlin", "license": "Apache-2.0", "hours_ago": 72,
        "dirs": [],
        "files": [("README.md", _readme("门店巡检 App", "离线优先的连锁门店巡检客户端。", "Kotlin"))],
    },
    {
        "group": "mobile", "name": "消息推送 SDK", "slug": "push-sdk",
        "desc": "Flutter 消息推送 SDK：厂商通道聚合、到达回执与角标同步。",
        "lang": "Dart", "license": "BSD-3-Clause", "hours_ago": 10,
        "dirs": [],
        "files": [("README.md", _readme("消息推送 SDK", "聚合厂商通道的 Flutter 推送 SDK。", "Dart"))],
    },
    {
        "group": "data-algo", "name": "日志检索引擎", "slug": "log-search",
        "desc": "面向可观测性的列式日志检索：倒排 + 分区裁剪，秒级查询十亿行日志。",
        "lang": "Rust", "license": "Apache-2.0", "hours_ago": 120,
        "dirs": [],
        "files": [("README.md", _readme("日志检索引擎", "列式存储的轻量日志检索引擎。", "Rust"))],
    },
    {
        "group": "data-algo", "name": "推荐召回服务", "slug": "recall-service",
        "desc": "多路召回编排：向量近邻、行为共现与运营位混合，支持 A/B 分流。",
        "lang": "Python", "license": "Apache-2.0", "hours_ago": 6,
        "dirs": [],
        "files": [("README.md", _readme("推荐召回服务", "多路召回与混排服务。", "Python"))],
    },
    {
        "group": "devtools", "name": "接口调试工具", "slug": "api-debugger",
        "desc": "命令行接口调试工具：环境变量、断言脚本与请求录制回放。",
        "lang": "Go", "license": "MIT", "hours_ago": 200,
        "dirs": [],
        "files": [("README.md", _readme("接口调试工具", "可脚本化的 HTTP 接口调试 CLI。", "Go"))],
    },
    {
        "group": "docs", "name": "部署运维手册集", "slug": "deploy-handbook",
        "desc": "团队内部部署与运维手册合集：上线检查单、故障复盘模板与值班手册。",
        "lang": "Markdown", "license": "CC-BY-4.0", "hours_ago": 300,
        "dirs": [],
        "files": [("README.md", _readme("部署运维手册集", "上线检查单与值班手册合集。", "Markdown"))],
    },
    {
        "group": "ops", "name": "监控告警网关", "slug": "monitor-gateway",
        "desc": "统一告警网关：聚合 Prometheus 与日志告警，按值班表分级路由到电话、IM 与工单。",
        "lang": "Go", "license": "Apache-2.0", "hours_ago": 96,
        "dirs": [],
        "files": [("README.md", _readme("监控告警网关", "多源告警聚合与值班路由。", "Go"))],
    },
]


def _parent(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _add_file(branch, path: str, content: str, ts) -> None:
    blob = store_bytes(content.encode("utf-8"))
    Entry.objects.create(
        branch=branch,
        path=path,
        parent_path=_parent(path),
        name=path.rsplit("/", 1)[-1],
        kind="file",
        blob=blob,
        size=blob.size,
        updated_at=ts,
    )


def _add_dir(branch, path: str, ts) -> None:
    Entry.objects.create(
        branch=branch,
        path=path,
        parent_path=_parent(path),
        name=path.rsplit("/", 1)[-1],
        kind="dir",
        size=0,
        updated_at=ts,
    )


class Command(BaseCommand):
    help = "写入开发/验收演示数据（8 个分组、12 个项目、分支与文件）；生产环境不会自动执行"

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="清空现有分组/项目/分支后重建")
        parser.add_argument(
            "--force", action="store_true", help="跳过 DEBUG 环境检查（自动化测试用）"
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError("生产环境禁止执行 seed_demo：示例项目仅用于开发/验收")
        if options["reset"]:
            Entry.objects.all().delete()
            Branch.objects.all().delete()
            Project.objects.all().delete()
            Group.objects.all().delete()
        elif Group.objects.exists():
            raise CommandError("已存在数据；确认重建请加 --reset")

        now = timezone.now()
        groups = {}
        for i, (name, slug, description, icon) in enumerate(GROUPS):
            groups[slug] = Group.objects.create(
                name=name, slug=slug, description=description, icon=icon, sort_order=i * 10
            )

        for spec in PROJECTS:
            ts = now - timedelta(hours=spec["hours_ago"])
            project = Project.objects.create(
                group=groups[spec["group"]],
                name=spec["name"],
                slug=spec["slug"],
                description=spec["desc"],
                language=spec["lang"],
                license_name=spec["license"],
            )
            main = Branch.objects.create(project=project, name="main")
            Project.objects.filter(pk=project.pk).update(default_branch=main)
            for d in spec.get("dirs", []):
                _add_dir(main, d, ts)
            for path, content in spec["files"]:
                _add_file(main, path, content, ts)
            if spec.get("release"):
                release = Branch.objects.create(project=project, name="release-1.0")
                for e in Entry.objects.filter(branch=main):
                    Entry.objects.create(
                        branch=release,
                        path=e.path,
                        parent_path=e.parent_path,
                        name=e.name,
                        kind=e.kind,
                        size=e.size,
                        blob=e.blob,
                        updated_at=e.updated_at,
                    )
                _add_file(release, "CHANGELOG.md", spec["release"], ts)
            # auto_now 会被 create 触发，用 update 指定演示时间
            Project.objects.filter(pk=project.pk).update(updated_at=ts, created_at=ts)

        self.stdout.write(
            self.style.SUCCESS(f"演示数据完成：{len(GROUPS)} 个分组，{len(PROJECTS)} 个项目")
        )
