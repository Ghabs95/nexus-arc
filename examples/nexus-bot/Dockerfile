FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        git \
        curl \
        ca-certificates \
        procps \
        gpg \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | gpg --dearmor -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

RUN apt-get update \
    && apt-get install -y --no-install-recommends glab \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 24 + AI CLIs used by Nexus runtime checks.
ARG COPILOT_NPM_PACKAGE="@github/copilot"
ARG GEMINI_NPM_PACKAGE="@google/gemini-cli"
ARG CODEX_NPM_PACKAGE="@openai/codex"
ARG CLAUDE_NPM_PACKAGE="@anthropic-ai/claude-code"
RUN curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g "$COPILOT_NPM_PACKAGE" "$GEMINI_NPM_PACKAGE" "$CODEX_NPM_PACKAGE" "$CLAUDE_NPM_PACKAGE" \
    && npm cache clean --force

WORKDIR /app

ARG NEXUS_ARC_EXTRAS="nexus-bot-runtime"

RUN mkdir -p /home/ubuntu \
    && chmod 755 /home/ubuntu

# Install nexus-arc from the parent repo (editable so we stay in sync)
# Install nexus-arc from the parent repo (editable so we stay in sync)
COPY pyproject.toml /nexus-arc/pyproject.toml
COPY nexus /nexus-arc/nexus
COPY README.md /nexus-arc/README.md
RUN pip install --no-cache-dir "/nexus-arc[${NEXUS_ARC_EXTRAS}]"

# Install the chat runtime package (console scripts: nexus-telegram-bot, nexus-discord-bot, etc.)
COPY examples/nexus-bot/pyproject.toml /app/pyproject.toml
COPY examples/nexus-bot/src /app/src
RUN pip install --no-cache-dir /app

COPY examples/nexus-bot/config /app/config
COPY examples/nexus-bot/.env.example /app/.env.example

RUN mkdir -p /app/data /app/logs

CMD ["nexus-telegram-bot"]
