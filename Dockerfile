FROM python:3.12-slim

ENV PYTHONBUFFERED=1\
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/root/.local/bin:$PATH"

RUN pip install --no-cache-dir poetry


