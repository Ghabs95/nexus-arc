# Telegram Bot Example (Migrated from `nexus`)

This folder is now the canonical home for the Telegram bot runtime that previously lived in `ghabs/nexus/src`.

Bootstrap/runtime lifecycle reference:

- [`docs/CONFIG_BOOTSTRAP_LIFECYCLE.md`](../../docs/CONFIG_BOOTSTRAP_LIFECYCLE.md)

## What is included

- `src/`: Bot runtime, handlers, processor, webhook server, health check
- `config/project_config.yaml`: NXS-only fallback config (not the primary runtime source)
- `nexus-*.service`: systemd service templates
- `scripts/setup-webhook.sh`: Webhook service setup helper

## Quick start

## Interactive Commands

The bot supports the following key commands across Telegram/Discord:

**Git Platform Management:**

- `/assign <project> <issue#>` - Assign an issue to yourself
- `/implement <project> <issue#>` - Request AI Agent implementation
- `/prepare <project> <issue#>` - Add AI Agent instructions
- `/plan <project> <issue#>` - Request a technical implementation plan

**Workflow & Monitoring:**

- `/status [project|all]` - View pending tasks in the inbox
- `/track <project> <issue#>` - Track an issue for updates
- `/myissues` - View all your tracked issues
- `/active` or `/wfstate` - View active workflows

**General:**

- `/chat` - Speak directly with the AI
- `/help` - Show all available commands
- `/menu` - Interactive button menu

```bash
cd /opt/nexus-arc
python3 -m venv venv
source venv/bin/activate
pip install -e ".[nexus-bot]"
cd examples/nexus-bot
pip install -e .
cp .env.example .env
```

Set at minimum in `.env`:

- `TELEGRAM_TOKEN`
- `TELEGRAM_ALLOWED_USER_IDS`
- `PROJECT_CONFIG_PATH=/opt/nexus/config/project_config.yaml`
- `BASE_DIR=/home/ubuntu/git`
- `DEPLOY_TYPE=compose` (or `systemd`)

For production auth/BYOK deployments, validate your env before starting services:

```bash
# Generate a valid master key (32-byte base64url)
python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip('='))"

# Validate required OAuth/auth/runtime values are not placeholders
./scripts/check-prod-env.sh .env
```

Run the bot:

```bash
source .env
python src/telegram_bot.py
```

## systemd templates

These unit files are used only when `DEPLOY_TYPE=systemd`.
If `DEPLOY_TYPE=compose` (default), they are not used.

Service files in this folder are pre-pointed to:

- runtime: `/opt/nexus-arc/examples/nexus-bot`
- env file: `/opt/nexus/.env`

Copy and enable as needed:

```bash
sudo cp nexus-telegram.service /etc/systemd/system/
sudo cp nexus-discord.service /etc/systemd/system/
sudo cp nexus-processor.service /etc/systemd/system/
sudo cp nexus-webhook.service /etc/systemd/system/
sudo cp nexus-health.service /etc/systemd/system/
sudo systemctl daemon-reload
```

## Docker Compose (recommended runtime)

This example includes a dedicated application compose stack:

- `docker-compose.yml`
- `Dockerfile`

It runs four services:

- `bot`
- `processor`
- `webhook`
- `health`

All services read environment variables from:

- `./.env`

All services read project config from the `nexus` repository folder:

- `/opt/nexus/config/project_config.yaml`

`examples/nexus-bot/config/project_config.yaml` is kept as an NXS-only fallback,
but is not the active compose config source.

Start:

```bash
cd /opt/nexus-arc/examples/nexus-bot
docker compose up -d --build
```

Or run through deploy helper:

```bash
./scripts/deploy.sh up
```

Stop:

```bash
docker compose down
```

Logs:

```bash
docker compose logs -f bot processor webhook health
```

### Config-driven deploy mode

Deployment type is selected by `.env`:

- `DEPLOY_TYPE=compose` → uses Docker Compose
- `DEPLOY_TYPE=systemd` → uses `nexus-*.service` units

Use:

```bash
./scripts/deploy.sh [up|down|restart|status|logs]
```

### Separation from infra cloud-init

Keep this application compose file separate from the infra template in
`vsc-server-infra/cloud-init.yaml.tpl`.

- `cloud-init.yaml.tpl` should bootstrap host infrastructure only
  (Docker engine, databases, logging stack, base packages).
- Nexus app lifecycle should stay in this repository's compose file.

Reference the Nexus compose stack from cloud-init only if you explicitly want
automatic deployment at VM bootstrap time.

## Migration note

The old `ghabs/nexus` repository should now be treated as a thin profile/wrapper layer (config + dependency
declaration), while implementation/runtime logic lives here.

### Additional Performance Flags

- `NEXUS_FULL_WORKFLOW_CONTEXT=true` - By default, the workflow system uses a compact, token-saving prompt context for
  agents. Set this to `true` to inject the entire, verbose workflow step definitions instead.
