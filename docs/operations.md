# GitODS 运维手册

架构：单机 Docker Compose（`web` Django/Gunicorn + `db` PostgreSQL 17 + `nginx` 反向代理）。
数据持久化：`pgdata`（数据库）、`appdata`（`/data/gitods`：不可变对象、临时文件、文件锁）、`nginx_certs`（TLS 证书，启用 HTTPS 时）。

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
docker compose up -d                 # 启动 web + nginx（默认 HTTP；上 HTTPS 见下节）
curl -fsS http://localhost/healthz/  # 健康检查 {"status":"ok"}
```

注意：

- **不要**自动加载演示数据；`seed_demo` 仅限开发环境（生产 `DEBUG=False` 拒绝执行）。
- `DATA_ROOT`（`/data/gitods`）是私有目录，永不作为静态目录公开，也不经 nginx 直读。
- 请求体上限：nginx `client_max_body_size 64m`（给 50 MiB 业务上限留 multipart 开销）；应用内仍按实际读入字节执行单文件/批次/解压限制。

### 启用 HTTPS（生产）

nginx 不自动签发证书，需一次性配置：

```bash
# 1) 宿主机用 certbot webroot 签发（DNS 先指向本机）
certbot certonly --webroot -w /var/www/certbot -d <域名>
# 2) 证书放入卷（或直接改挂宿主机路径）
docker cp /etc/letsencrypt/live/<域名>/fullchain.pem $(docker compose ps -q nginx):/etc/nginx/certs/
docker cp /etc/letsencrypt/live/<域名>/privkey.pem  $(docker compose ps -q nginx):/etc/nginx/certs/
# 3) 换挂 TLS 模板：compose.yaml 中 nginx 服务的挂载改为
#    ./deploy/nginx-tls.conf.template:/etc/nginx/templates/gitods.conf.template:ro
#    并在 .env 设置 SECURE_HSTS_SECONDS=31536000
docker compose up -d nginx
# 4) 证书自动续期：宿主机 crontab 执行 certbot renew && docker cp ... && docker compose exec nginx nginx -s reload
```

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

**2026-10-24 容器全流程演练（本机 OrbStack，Docker Compose 生产编排）**

- 镜像：`gitods-web`（python:3.12-slim-bookworm 构建）、`postgres:17.11-alpine`、`nginx:1.30.3`；应用角色 `gitods_app`（rolsuper=f，无 CREATEDB）
- 部署步骤实测：`config --quiet` → `build` → `up -d db`（init-db.sh 自动建角色/库）→ `migrate` → `collectstatic`（176 文件）→ `check --deploy`（仅 W004/W008 两条文档化警告：HSTS 可选、HTTPS 跳转由 nginx TLS 模板承担）→ `createsuperuser` → `up -d`
- 内容经真实服务创建（`scripts/drill_content.py`：ZIP 导入 main、复制 release-1.0、覆盖 README、设默认）
- 备份：`backup.sh` 成功（停 web→pg_dump -Fc→objects 归档→meta/SHA256SUMS→自动恢复 web）；演练中验证了失败路径（脚本兼容性问题导致中途退出时 trap 正确恢复 web）
- 灾难恢复：删除 objects/ 目录 + 重建数据库后 `restore.sh`：版本匹配→载入→restore_post（清会话/作废草稿）→check_storage「14 条目、5 对象、缺失 0、失配 0」；恢复后 HTTP 核验全部通过：分组/项目列表、main 与 release-1.0 不同 README、相对图片 raw（image/png）、中文目录、分支 ZIP 字节一致（`docs/架构说明.md`、README 内容逐一比对）
- 重启留存：`docker compose down && up -d` 后 1 项目/2 分支/14 条目完好，check_storage 零缺失
- 备份产物不入版本库（含数据库数据）；演练证据见 `docs/superpowers/plans/2026-10-24-gitods.md` 任务 10 验证记录
- 未发布公网：无域名与服务器，本地演练即到此为止；上线按第 1 节在生产主机重复执行并登记新记录
