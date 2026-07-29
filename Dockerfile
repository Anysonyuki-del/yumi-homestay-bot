FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
RUN pip install --no-cache-dir .

CMD ["uvicorn", "homestay_bot.main:app", "--host", "0.0.0.0", "--port", "8000"]
