# Python 3.12: every dependency (incl. numpy 2.x pulled in by llama-index) installs from
# prebuilt wheels, so the slim image needs no C compiler.
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

