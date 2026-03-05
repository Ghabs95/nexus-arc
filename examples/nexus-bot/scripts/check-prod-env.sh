#!/usr/bin/env bash

set -euo pipefail

ENV_FILE="${1:-.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing env file: $ENV_FILE"
  echo "Usage: $0 /path/to/.env"
  exit 1
fi

python3 - "$ENV_FILE" <<'PY'
import base64
import os
import sys

env_file = sys.argv[1]
values: dict[str, str] = {}

try:
    from dotenv import dotenv_values  # type: ignore

    values = {k: ("" if v is None else str(v).strip()) for k, v in dotenv_values(env_file).items()}
except Exception:
    # Fallback parser for KEY=VALUE lines when python-dotenv is unavailable.
    with open(env_file, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key:
                continue
            values[key] = value.strip()

errors: list[str] = []
warnings: list[str] = []


def get(name: str) -> str:
    val = values.get(name)
    return "" if val is None else str(val).strip()


def lower(value: str) -> str:
    return str(value or "").strip().lower()


def truthy(value: str) -> bool:
    return lower(value) in {"1", "true", "yes", "on"}


def placeholder(value: str) -> bool:
    normalized = lower(value)
    if not normalized:
        return True
    patterns = (
        "your_",
        "_here",
        "replace_me",
        "change_me",
        "example.com",
        "base64_encoded_32_byte_key",
        "token_here",
        "client_id",
        "client_secret",
    )
    return any(item in normalized for item in patterns)


def require_non_placeholder(name: str) -> None:
    if placeholder(get(name)):
        errors.append(f"{name} is missing or still a placeholder")


auth_enabled = truthy(get("NEXUS_AUTH_ENABLED"))
github_enabled = (not placeholder(get("NEXUS_GITHUB_CLIENT_ID"))) and (
    not placeholder(get("NEXUS_GITHUB_CLIENT_SECRET"))
)
gitlab_enabled = (not placeholder(get("NEXUS_GITLAB_CLIENT_ID"))) and (
    not placeholder(get("NEXUS_GITLAB_CLIENT_SECRET"))
)

if placeholder(get("TELEGRAM_TOKEN")) and placeholder(get("DISCORD_TOKEN")):
    errors.append("Set at least one bot token: TELEGRAM_TOKEN or DISCORD_TOKEN")

if auth_enabled:
    if lower(get("NEXUS_STORAGE_BACKEND")) not in {"postgres", "database"}:
        errors.append(
            "NEXUS_AUTH_ENABLED=true requires NEXUS_STORAGE_BACKEND=postgres (alias: database)"
        )
    require_non_placeholder("NEXUS_STORAGE_DSN")
    require_non_placeholder("NEXUS_PUBLIC_BASE_URL")
    require_non_placeholder("NEXUS_CREDENTIALS_MASTER_KEY")
    if not github_enabled and not gitlab_enabled:
        errors.append("Configure GitHub OAuth or GitLab OAuth client id/secret")
    if github_enabled and placeholder(get("NEXUS_AUTH_ALLOWED_GITHUB_ORGS")):
        errors.append("NEXUS_AUTH_ALLOWED_GITHUB_ORGS is required with GitHub OAuth")
    if gitlab_enabled and placeholder(get("NEXUS_AUTH_ALLOWED_GITLAB_GROUPS")):
        errors.append("NEXUS_AUTH_ALLOWED_GITLAB_GROUPS is required with GitLab OAuth")

public_base_url = get("NEXUS_PUBLIC_BASE_URL")
if public_base_url:
    normalized_url = lower(public_base_url)
    if not (normalized_url.startswith("http://") or normalized_url.startswith("https://")):
        errors.append("NEXUS_PUBLIC_BASE_URL must start with http:// or https://")

master_key = get("NEXUS_CREDENTIALS_MASTER_KEY")
if master_key:
    padding = "=" * ((4 - (len(master_key) % 4)) % 4)
    try:
        decoded = base64.urlsafe_b64decode((master_key + padding).encode("ascii"))
        if len(decoded) != 32:
            raise ValueError("master key length mismatch")
    except Exception:
        errors.append("NEXUS_CREDENTIALS_MASTER_KEY must be base64url-encoded 32 bytes")

if placeholder(get("WEBHOOK_SECRET")):
    warnings.append("WEBHOOK_SECRET is not set or still placeholder")

project_config_path = get("PROJECT_CONFIG_PATH")
if project_config_path:
    candidate = project_config_path
    if not os.path.isabs(candidate):
        candidate = os.path.join(os.getcwd(), candidate)
    if not os.path.isfile(candidate):
        warnings.append(f"PROJECT_CONFIG_PATH does not exist from cwd: {project_config_path}")

if errors:
    print(f"ENV CHECK FAILED ({env_file})")
    for item in errors:
        print(f" - ERROR: {item}")
    for item in warnings:
        print(f" - WARN:  {item}")
    raise SystemExit(1)

print(f"ENV CHECK PASSED ({env_file})")
for item in warnings:
    print(f" - WARN: {item}")
PY
