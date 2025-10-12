FROM python:3.12-slim

ENV PYTHONBUFFERED=1\
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/root/.local/bin:$PATH"

RUN pip install --no-cache-dir poetry

WORKDIR /app

COPY pyproject.toml poetry.lock* ./

RUN poetry install --no-interaction --no-ansi
COPY /app /app
COPY /lib /lib

