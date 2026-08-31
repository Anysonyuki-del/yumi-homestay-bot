FROM python:3.12-slim@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36

ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_INDEX_URL=${PIP_INDEX_URL}

WORKDIR /app

COPY requirements.lock ./
COPY pyproject.toml ./
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations

# 先按哈希锁安装运行与构建依赖，再禁止项目安装重新解析依赖。
RUN python -m pip install --no-cache-dir --retries 5 \
        --require-hashes -r requirements.lock && \
    python -m pip install --no-cache-dir --no-deps --no-build-isolation .

# 固定 UID/GID 便于部署前精确核对宿主机上传目录权限。
RUN groupadd --system --gid 10001 app && \
    useradd --system --uid 10001 --gid 10001 --home-dir /app --shell /usr/sbin/nologin app && \
    install -d -o 10001 -g 10001 /app/data/private_uploads

USER 10001:10001

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn homestay_bot.main:app --host 0.0.0.0 --port 8000"]
