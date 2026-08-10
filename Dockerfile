FROM python:3.12-slim

ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
RUN python -m pip install --no-cache-dir --retries 5 .

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn homestay_bot.main:app --host 0.0.0.0 --port 8000"]
