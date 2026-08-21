# 3.12 (not 3.13): numpy is pinned <2.0.0, and numpy 1.26.4 ships prebuilt wheels
# only up to cp312 — on 3.13-slim it would compile from source and fail (no C
# compiler in the slim image). 3.12 installs everything from wheels.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
    
RUN pip3 install --no-cache-dir poetry

WORKDIR /app

COPY pyproject.toml poetry.lock* ./

RUN poetry install --no-root --no-interaction --no-ansi

COPY /lib /lib
COPY app/ ./app/

# Secrets are injected at runtime via docker-compose `env_file: .env` (not baked
# into the image). The compose `agent` service overrides this CMD to run the
# Messenger webhook (uvicorn); this default keeps `docker run` usable for the REPL.
CMD [ "poetry", "run", "python", "app/main.py" ]

