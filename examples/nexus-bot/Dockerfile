FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        git \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install nexus-arc from the parent repo (editable so we stay in sync)
# Install nexus-arc from the parent repo (editable so we stay in sync)
COPY pyproject.toml /nexus-arc/pyproject.toml
COPY nexus /nexus-arc/nexus
COPY README.md /nexus-arc/README.md
RUN pip install --no-cache-dir "/nexus-arc[telegram-bot]"

# Install the telegram-bot package (console scripts: nexus-bot, etc.)
COPY examples/nexus-bot/pyproject.toml /app/pyproject.toml
COPY examples/nexus-bot/src /app/src
RUN pip install --no-cache-dir /app

COPY examples/nexus-bot/config /app/config
COPY examples/nexus-bot/.env.example /app/.env.example

RUN mkdir -p /app/data /app/logs

CMD ["nexus-bot"]
