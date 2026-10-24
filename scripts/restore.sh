#!/usr/bin/env bash
# GitODS 恢复：在已停止 web 的目标环境恢复匹配版本的数据库与对象目录。
# 不会对未确认的生产库做破坏性覆盖：要求显式提供备份目录，
# 应用版本不匹配时中止（FORCE=1 跳过）。
# 备份不含 DATA_ROOT/tmp 与数据库角色/密钥，恢复后由 restore_post 收尾。
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-compose.yaml}"
BACKUP_DIR="${1:?用法: restore.sh <备份目录 gitods-YYYYmmdd-HHMMSS>}"
APP_VERSION="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"

cd "$(dirname "$0")/.."

[ -f "$BACKUP_DIR/db.dump" ] && [ -f "$BACKUP_DIR/objects.tar.gz" ] || {
    echo "[恢复] 备份目录不完整：$BACKUP_DIR" >&2; exit 1;
}
( cd "$BACKUP_DIR" && sha256sum -c SHA256SUMS >/dev/null ) || {
    echo "[恢复] 校验和不匹配，备份可能损坏" >&2; exit 1;
}

BACKUP_VERSION="$(sed -n 's/^app_version=//p' "$BACKUP_DIR/meta.txt")"
if [ "$APP_VERSION" != "$BACKUP_VERSION" ] && [ "${FORCE:-0}" != "1" ]; then
    echo "[恢复] 应用版本不匹配（当前 $APP_VERSION / 备份 $BACKUP_VERSION）；" >&2
    echo "[恢复] 先切到对应版本再恢复，或 FORCE=1 明确跳过" >&2
    exit 1
fi

echo "[恢复] 确认 web 已停止"
docker compose -f "$COMPOSE_FILE" stop -t 30 web >/dev/null

echo "[恢复] 重建数据库（ DROP + CREATE，owner=gitods_app ）"
docker compose -f "$COMPOSE_FILE" exec -T \
    -e PGPASSWORD="${POSTGRES_PASSWORD:?需要在环境提供 POSTGRES_PASSWORD}" \
    db psql -U postgres -v ON_ERROR_STOP=1 <<SQL
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='gitods' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS gitods;
CREATE DATABASE gitods OWNER gitods_app;
SQL

echo "[恢复] 载入 pg_dump"
docker compose -f "$COMPOSE_FILE" exec -T \
    -e PGPASSWORD="${GITODS_APP_PASSWORD:?需要在环境提供 GITODS_APP_PASSWORD}" \
    db pg_restore -U gitods_app -d gitods --no-owner --no-privileges < "$BACKUP_DIR/db.dump"

echo "[恢复] 解开对象目录 objects/"
docker compose -f "$COMPOSE_FILE" run --rm --no-deps \
    -v "$(cd "$BACKUP_DIR" && pwd):/backup:ro" \
    --entrypoint tar web xzf /backup/objects.tar.gz -C /data/gitods

echo "[恢复] 收尾：清空旧会话、作废未完成草稿（备份不含 tmp）"
docker compose -f "$COMPOSE_FILE" run --rm --no-deps web python manage.py restore_post

echo "[恢复] 完整性核验"
docker compose -f "$COMPOSE_FILE" run --rm --no-deps web python manage.py check_storage

echo "[恢复] 完成。确认无误后：docker compose -f $COMPOSE_FILE up -d web"
