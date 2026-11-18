#!/bin/sh
set -eu

# 启动服务前先把本地数据库迁移到当前代码要求的版本。
# 脚本位于 deploy/，应用根目录是其父目录。
APP_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON_BIN="$APP_ROOT/.venv/bin/python"

cd "$APP_ROOT"

# 登录和配置修复页只依赖基础参数，部署脚本不应要求已配置外部服务凭据。
DATABASE_URL=$("$PYTHON_BIN" -c 'from homestay_bot.config import BootstrapSettings; print(BootstrapSettings().database_url)')
PRIVATE_UPLOAD_DIR=$("$PYTHON_BIN" -c 'from homestay_bot.config import BootstrapSettings; print(BootstrapSettings().private_upload_dir)')
export DATABASE_URL

case "$PRIVATE_UPLOAD_DIR" in
    /*) ;;
    *) PRIVATE_UPLOAD_DIR="$APP_ROOT/$PRIVATE_UPLOAD_DIR" ;;
esac

# 独立获取版本以保留两条命令的退出码；已到 head 的重启不再备份。
CURRENT_REV=$("$PYTHON_BIN" -m alembic current)
HEAD_REV=$("$PYTHON_BIN" -m alembic heads)
if [ "$CURRENT_REV" != "$HEAD_REV" ]; then
    mkdir -p "$APP_ROOT/.backups"
    BACKUP_DIR=$(mktemp -d "$APP_ROOT/.backups/pre-migration-$(date +%Y%m%d-%H%M%S)-XXXXXX")
    "$PYTHON_BIN" - "$BACKUP_DIR" <<'PY'
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

url = make_url(os.environ["DATABASE_URL"])
if url.get_backend_name() == "sqlite":
    # 让 SQLite 解析真实文件位置（含 URI 连接串）；纯内存库没有持久文件。
    with create_engine(url.set(drivername="sqlite")).connect() as source:
        filename = source.exec_driver_sql("PRAGMA database_list").one()[2]
        if filename:
            # 先取得有锁等待上限的读快照，再用在线备份包含尚未检查点的已提交 WAL。
            source.exec_driver_sql("BEGIN")
            source.exec_driver_sql("SELECT count(*) FROM sqlite_master").scalar_one()
            with closing(sqlite3.connect(Path(sys.argv[1]) / Path(filename).name)) as target:
                source.connection.driver_connection.backup(target)
PY
    if [ -d "$PRIVATE_UPLOAD_DIR" ]; then
        cp -R "$PRIVATE_UPLOAD_DIR" "$BACKUP_DIR/private_uploads"
    fi
fi

"$PYTHON_BIN" -m alembic upgrade head
exec "$PYTHON_BIN" -m uvicorn homestay_bot.main:app --host 127.0.0.1 --port 8010
