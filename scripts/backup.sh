#!/usr/bin/env bash
# GitODS 备份：同一停止应用窗口内 pg_dump -Fc + 对象目录打包。
# 流程：停止并排空 web（db 保持运行）→ 备份 → 无论成败恢复 web → 成功后轮转。
# 每日执行；至少保留 7 份，并复制一份到其他磁盘/主机。
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-compose.yaml}"
BACKUP_ROOT="${BACKUP_ROOT:-./backups}"
KEEP="${KEEP:-7}"

cd "$(dirname "$0")/.."
STAMP="$(date +%Y%m%d-%H%M%S)"
DEST="$BACKUP_ROOT/gitods-$STAMP"
APP_VERSION="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"

mkdir -p "$DEST"

restore_web() {
    echo "[备份] 恢复 web 服务"
    docker compose -f "$COMPOSE_FILE" up -d web >/dev/null
}
trap restore_web EXIT

echo "[备份] 停止 web 并等待请求退出（db 保持运行）"
docker compose -f "$COMPOSE_FILE" stop -t 30 web >/dev/null

echo "[备份] 导出数据库（pg_dump -Fc）"
docker compose -f "$COMPOSE_FILE" exec -T \
    -e PGPASSWORD="${GITODS_APP_PASSWORD:?需要在环境提供 GITODS_APP_PASSWORD}" \
    db pg_dump -U gitods_app -d gitods -Fc > "$DEST/db.dump"

echo "[备份] 归档对象目录 objects/"
docker compose -f "$COMPOSE_FILE" run --rm --no-deps \
    -v "$(cd "$DEST" && pwd):/backup" \
    --entrypoint tar web czf /backup/objects.tar.gz -C /data/gitods objects

echo "[备份] 写入元数据与校验和"
{
    echo "app_version=$APP_VERSION"
    echo "created_at=$(date -Iseconds)"
    echo "pg_dump_format=custom"
    du -b "$DEST/db.dump" "$DEST/objects.tar.gz" | awk '{print $2"="$1}'
} > "$DEST/meta.txt"
( cd "$DEST" && sha256sum db.dump objects.tar.gz > SHA256SUMS )

# 完整性自检：dump 非空 + 校验和复核
[ -s "$DEST/db.dump" ] || { echo "[备份] 失败：db.dump 为空" >&2; exit 1; }
( cd "$DEST" && sha256sum -c SHA256SUMS >/dev/null ) || { echo "[备份] 失败：校验和不匹配" >&2; exit 1; }

restore_web
trap - EXIT

echo "[备份] 完成：$DEST"
echo "[备份] 轮转：保留最近 $KEEP 份"
ls -1dt "$BACKUP_ROOT"/gitods-* | tail -n +$((KEEP + 1)) | xargs -r rm -rf
echo "[备份] 提醒：将 $DEST 复制一份到其他磁盘/主机"
