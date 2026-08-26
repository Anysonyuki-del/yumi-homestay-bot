#!/bin/sh
set -eu

# 启动服务前先把本地数据库迁移到当前代码要求的版本。
# 脚本位于 deploy/，应用根目录是其父目录。
APP_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON_BIN="$APP_ROOT/.venv/bin/python"

cd "$APP_ROOT"

# 使用与应用相同的配置解析器读取数据库和私有文件目录，避免迁移误用 alembic.ini 默认值。
DATABASE_URL=$("$PYTHON_BIN" -c 'from homestay_bot.config import get_settings; print(get_settings().database_url)')
PRIVATE_UPLOAD_DIR=$("$PYTHON_BIN" -c 'from homestay_bot.config import get_settings; print(get_settings().private_upload_dir)')
export DATABASE_URL

case "$PRIVATE_UPLOAD_DIR" in
    /*) ;;
    *) PRIVATE_UPLOAD_DIR="$APP_ROOT/$PRIVATE_UPLOAD_DIR" ;;
esac

# 仅 SQLite 有本机可复制文件；PostgreSQL 由云端数据库备份策略负责。
DATABASE_PATH=$("$PYTHON_BIN" -c 'import os; from sqlalchemy.engine import make_url; url = make_url(os.environ["DATABASE_URL"]); print(url.database or "" if url.get_backend_name() == "sqlite" else "")')
case "$DATABASE_PATH" in
    ""|/*) ;;
    *) DATABASE_PATH="$APP_ROOT/$DATABASE_PATH" ;;
esac

# 只有迁移版本落后时才创建备份，避免守护进程重启持续占用磁盘。
CURRENT_REV=$("$PYTHON_BIN" -m alembic current | awk 'NR == 1 {print $1}')
HEAD_REV=$("$PYTHON_BIN" -m alembic heads | awk 'NR == 1 {print $1}')
if [ "$CURRENT_REV" != "$HEAD_REV" ]; then
    BACKUP_DIR="$APP_ROOT/.backups/pre-migration-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$BACKUP_DIR"
    if [ -n "$DATABASE_PATH" ] && [ -f "$DATABASE_PATH" ]; then
        cp "$DATABASE_PATH" "$BACKUP_DIR/"
    fi
    if [ -d "$PRIVATE_UPLOAD_DIR" ]; then
        cp -R "$PRIVATE_UPLOAD_DIR" "$BACKUP_DIR/private_uploads"
    fi
fi

"$PYTHON_BIN" -m alembic upgrade head
exec "$PYTHON_BIN" -m uvicorn homestay_bot.main:app --host 127.0.0.1 --port 8010
