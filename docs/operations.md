# GitODS 运维手册

架构：单机 Docker Compose（`web` Django/Gunicorn + `db` PostgreSQL 17 + `caddy` HTTPS 反代）。
数据持久化：`pgdata`（数据库）、`appdata`（`/data/gitods`：不可变对象、临时文件、文件锁）、`caddy_data`（证书）。

## 1. 首次部署

```bash
cp .env.example .env   # 填 SECRET_KEY、POSTGRES_PASSWORD、GITODS_APP_PASSWORD、GITODS_DOMAIN
docker compose config --quiet        # 校验编排与变量
docker compose build                 # 构建应用镜像
docker compose up -d db              # 初始化角色（init-db.sh 自动执行）
docker compose run --rm web python manage.py migrate          # 迁移（显式步骤）
docker compose run --rm web python manage.py collectstatic --noinput
docker compose run --rm web python manage.py check --deploy   # 生产检查
docker compose run --rm web python manage.py createsuperuser  # 管理员（无默认账号密码）
docker compose up -d                 # 启动 web + caddy（HTTP→HTTPS 自动证书）
curl -fsS http://localhost/healthz/  # 健康检查 {"status":"ok"}
```

注意：

- **不要**自动加载演示数据；`seed_demo` 仅限开发环境（生产 `DEBUG=False` 拒绝执行）。
- `DATA_ROOT`（`/data/gitods`）是私有目录，永不作为静态目录公开，也不经 Caddy 直读。
- 请求体上限：Caddy `max_size 64MB`（给 50 MiB 业务上限留 multipart 开销）；应用内仍按实际读入字节执行单文件/批次/解压限制。

## 2. 日常操作

| 事项 | 命令 |
|---|---|
| 日志 | `docker compose logs -f web`（stdout；不记录密码/Cookie/源码正文） |
| 重启 | `docker compose restart web` |
| 迁移（发版时） | `docker compose run --rm web python manage.py migrate` |
| 健康巡检 | `curl -fsS https://<域名>/healthz/` |
| 存储完整性 | `docker compose run --rm web python manage.py check_storage` |

## 3. 备份（每日；至少保留 7 份，另放一份到其他磁盘/主机）

```bash
GITODS_APP_PASSWORD=... scripts/backup.sh
```

同一停止应用窗口内完成：`stop web`（最长等待 30s 排空请求）→ **db 保持运行** → `pg_dump -Fc` + `objects/` 打包 → 写入版本/时间/校验和（`meta.txt`、`SHA256SUMS`）→ **无论成败都恢复 web** → 成功后轮转旧备份。

不要直接复制运行中的 PostgreSQL 数据目录；也不要只备数据库漏掉 `objects/`。

## 4. 恢复演练与实际恢复

```bash
POSTGRES_PASSWORD=... GITODS_APP_PASSWORD=... scripts/restore.sh backups/gitods-YYYYmmdd-HHMMSS
```

步骤：校验备份完整 → 匹配应用版本（不符则中止，`FORCE=1` 跳过）→ 停 web → 重建并载入数据库 → 解开 `objects/` → `restore_post`（清空旧会话、作废未完成导入草稿并清空临时清单引用——备份不含 tmp）→ `check_storage` 核验 → 确认后 `up -d web`。

数据库角色与环境密钥不随备份走：恢复环境先备好 `.env` 与角色（`pg_dump` 不含集群角色）。

上线验收必须实际恢复一份备份，确认：分组、默认分支、不同分支源码、README 图片与 ZIP 下载可用。

## 5. 停机清理（cleanup_storage）

未引用对象会随失败上传/导入积累；旧下载可能仍持有清单，因此**清理必须停机**：

```bash
docker compose stop -t 30 web
docker compose run --rm web python manage.py cleanup_storage            # dry-run 预览
docker compose run --rm web python manage.py cleanup_storage --apply \
    --yes-i-stopped-web        # 已备份且确认 web 停止后才执行
docker compose up -d web
```

清理内容：未引用对象、过期/完结草稿临时目录、遗留上传/导出临时文件、过期草稿行、旧登录限流记录。

## 6. 版本回退

```bash
git checkout <上一个发布版本>
docker compose build && docker compose run --rm web python manage.py migrate
docker compose up -d
# 数据不回退；如需回到备份时点，走第 4 节恢复流程
```

## 7. 容量调整

上限集中在 `.env`（`GITODS_MAX_BRANCHES_PER_PROJECT` 等）与 `config/settings.py` 的 `GITODS_LIMITS`；修改后重启 web。磁盘剩余空间低于 1 GiB 时应用自动拒绝新上传/导入，浏览不受影响。

## 8. 演练记录

（首次部署后在此登记：日期、备份目录、恢复结果、核验项。隔离演练环境验证两分支不同 README、相对图片、目录浏览、ZIP 下载与容器重启数据留存。）
