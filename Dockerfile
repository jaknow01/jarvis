# Jarvis (harness + Messenger webhook). Self-contained image for k3s: code and committed
# config are baked in; secrets come from the environment (k8s Secret) and the principals file
# is mounted at /app/config/principals.yaml.
# Python 3.12: every dependency (incl. numpy 2.x pulled in by llama-index) installs from
# prebuilt wheels, so the slim image needs no C compiler.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    TZ=Europe/Warsaw

RUN pip3 install --no-cache-dir poetry

WORKDIR /app

COPY pyproject.toml poetry.lock poetry.toml ./
RUN poetry install --no-root --no-interaction --no-ansi --only main

COPY lib ./lib
COPY app ./app
COPY config ./config

RUN useradd --uid 10001 --create-home jarvis && mkdir -p /app/data /app/logs && chown -R jarvis /app/data /app/logs
USER 10001

EXPOSE 8002
# Messenger webhook (the long-running service). `docker run … python app/main.py` gives the REPL.
CMD ["/app/.venv/bin/uvicorn", "app.webhook:app", "--host", "0.0.0.0", "--port", "8002"]
