#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
from pathlib import Path


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def prompt_choice(question: str, choices: list[str]) -> int:
    while True:
        print(f"\n{question}")
        for i, choice in enumerate(choices, 1):
            print(f"  {i}) {choice}")
        try:
            val = int(input("\nSelect an option: "))
            if 1 <= val <= len(choices):
                return val
            print(f"Please enter a number between 1 and {len(choices)}.")
        except ValueError:
            print("Please enter a valid number.")


def prompt_multi_choice(question: str, choices: list[str]) -> list[int]:
    while True:
        print(f"\n{question}")
        for i, choice in enumerate(choices, 1):
            print(f"  {i}) {choice}")
        try:
            val = input(
                "\nSelect options separated by commas (e.g. 1, 3) or press Enter to skip: "
            ).strip()
            if not val:
                return []

            selected = [int(x.strip()) for x in val.split(",")]
            if all(1 <= x <= len(choices) for x in selected):
                return selected
            print(f"Please enter valid numbers between 1 and {len(choices)}.")
        except ValueError:
            print("Please enter valid comma-separated numbers.")


def prompt_multi_choice_required(question: str, choices: list[str]) -> list[int]:
    while True:
        selected = prompt_multi_choice(question, choices)
        if selected:
            return selected
        print("Please select at least one option.")


def prompt_string(question: str, default: str = "") -> str:
    prompt = f"{question} [{default}]: " if default else f"{question}: "
    val = input(prompt).strip()
    return val if val else default


def run_command(cmd: list[str], shell: bool = False):
    print(f"\nRunning: {' '.join(cmd) if not shell else cmd}")
    try:
        subprocess.run(cmd, shell=shell, check=True)
    except subprocess.CalledProcessError as e:
        print(f"\n[ERROR] Command failed: {e}")
        sys.exit(1)


def main():
    clear_screen()
    print("=======================================")
    print(" 🚀 Welcome to Nexus ARC Installation 🚀")
    print("=======================================\n")

    # 1. Runtime Mode
    step_num = 1
    print(f"\n--- {step_num}. Runtime Mode ---")
    step_num += 1
    runtime_choice = prompt_choice(
        "Which runtime mode do you want to install?",
        [
            "Standalone (Nexus manages Telegram/Discord chat surfaces)",
            "OpenClaw (OpenClaw owns chat UX/transcript; Nexus runs bridge + workflow runtime)",
            "Advanced (Mix OpenClaw with optional native Nexus bot surfaces)",
        ],
    )
    runtime_mode = {1: "standalone", 2: "openclaw", 3: "advanced"}[runtime_choice]
    auth_authority = "openclaw" if runtime_mode == "openclaw" else "nexus"
    enable_openclaw = runtime_mode == "openclaw"
    install_telegram = runtime_mode == "standalone"
    install_discord = runtime_mode == "standalone"
    chat_transcript_owner = "openclaw" if runtime_mode == "openclaw" else "nexus"

    if runtime_mode == "advanced":
        print(f"\n--- {step_num}. Runtime Surfaces ---")
        step_num += 1
        selected_surfaces = prompt_multi_choice_required(
            "Which surfaces should this installation enable?",
            [
                "OpenClaw bridge",
                "Telegram bot",
                "Discord bot",
            ],
        )
        enable_openclaw = 1 in selected_surfaces
        install_telegram = 2 in selected_surfaces
        install_discord = 3 in selected_surfaces
        if enable_openclaw:
            owner_choice = prompt_choice(
                "Who should own chat transcript memory?",
                [
                    "OpenClaw (recommended)",
                    "Nexus",
                    "Split (OpenClaw transcript, Nexus summaries/facts)",
                ],
            )
            chat_transcript_owner = {1: "openclaw", 2: "nexus", 3: "split"}[owner_choice]
        else:
            chat_transcript_owner = "nexus"

    # 2. Storage Mode
    print(f"\n--- {step_num}. Storage Mode ---")
    step_num += 1
    if runtime_mode == "openclaw":
        storage_choice = prompt_choice(
            "Which storage mode do you want to use?",
            [
                "Lite (Filesystem only - great for local OpenClaw-backed testing)",
                "Persistent (PostgreSQL only - recommended for durable workflow state and audit)",
            ],
        )
        use_postgres = storage_choice == 2
        use_redis = False
    elif runtime_mode == "advanced" and chat_transcript_owner == "openclaw":
        storage_choice = prompt_choice(
            "Which storage mode do you want to use?",
            [
                "Lite (Filesystem only)",
                "Persistent (PostgreSQL only - OpenClaw owns transcript memory)",
            ],
        )
        use_postgres = storage_choice == 2
        use_redis = False
    else:
        storage_choice = prompt_choice(
            "Which storage mode do you want to use?",
            [
                "Lite (Filesystem only - no external dependencies, great for local testing)",
                "Enterprise (PostgreSQL + Redis - persistent queue, chat memory, deduplication)",
            ],
        )
        use_postgres = storage_choice == 2
        use_redis = storage_choice == 2
    is_enterprise = use_postgres and use_redis

    # 3. Infrastructure
    setup_db = False
    use_docker = False

    if use_postgres or use_redis:
        print(f"\n--- {step_num}. Infrastructure Setup ---")
        step_num += 1
        if use_redis:
            infra_choice = prompt_choice(
                "How do you want to run PostgreSQL and Redis?",
                [
                    "Docker Compose (Sandboxed, highly recommended)",
                    "System packages (e.g. brew or apt)",
                    "I already have them running (Skip installation)",
                ],
            )
            if infra_choice == 1:
                use_docker = True
                setup_db = True
            elif infra_choice == 2:
                setup_db = True
        else:
            infra_choice = prompt_choice(
                "How do you want to run PostgreSQL?",
                [
                    "System packages (e.g. brew or apt)",
                    "I already have it running (Skip installation)",
                ],
            )
            if infra_choice == 1:
                setup_db = True

    has_core = prompt_choice(
        "Do you already have a Nexus core repository configured locally?",
        ["No, I need to configure my first project", "Yes, I already have one"],
    )

    if has_core == 1:
        print("\n[Concept: The Core Repo]")
        print("Nexus ARC works best when you have a centralized 'core' repository.")
        print("This is a single git repository where you store your '*-agent.yaml' definitions")
        print("and 'workflow.yaml' files, effectively creating an org-chart of AI agents.")
        core_repo = prompt_string(
            "What is the name of your organization's 'core' repository?", "my-core"
        )

        print("\n[Workspaces & Projects]")
        base_dir = prompt_string(
            "What is your base directory where all your git clones live?", str(Path.home() / "git")
        )

        print("\n[Your First Project]")
        print(
            f"Nexus groups multiple git repositories inside a single 'workspace' folder inside {base_dir}."
        )
        project_name = prompt_string(
            "What is the short name for this project? (e.g. my-project)", "my-project"
        )
        workspace_dir = prompt_string(
            f"What is the workspace folder name (inside {base_dir})?", f"{project_name}"
        )
        git_repo = prompt_string(
            f"What is the git repository holding agents/workflows for this workspace?\n(e.g. my-org/{project_name}-nexus or username/{project_name}-nexus)",
            f"my-org/{project_name}-nexus",
        )

        bot_dir_default = str(Path(base_dir) / workspace_dir / core_repo)
    else:
        print("\n[Existing Core Repo]")
        bot_dir_default = prompt_string(
            "What is the full path to your existing core repository?",
            str(Path.home() / "git" / "my-workspace" / "my-core"),
        )
        base_dir = str(Path(bot_dir_default).parent.parent)  # Best guess

    print(f"\n--- {step_num}. Installation Directory ---")
    step_num += 1
    bot_dir_input = prompt_string(
        "Where should we create the configuration files? (e.g. .env, config/)", bot_dir_default
    )
    bot_dir = Path(bot_dir_input).expanduser().resolve()
    bot_dir.mkdir(parents=True, exist_ok=True)
    env_file = bot_dir / ".env"

    write_env = True
    if env_file.exists():
        replace = prompt_choice(
            "An existing .env file was found. Overwrite?", ["No, keep it", "Yes, overwrite"]
        )
        if replace == 1:
            print("Keeping existing .env file.")
            write_env = False

    if write_env:
        print(f"\n--- {step_num}. Credentials & Keys ---")
        step_num += 1
        telegram_token = ""
        telegram_users = ""
        discord_token = ""
        bridge_token = ""

        if install_telegram:
            telegram_token = prompt_string("Enter your Telegram Bot Token", "")
            telegram_users = prompt_string(
                "Enter your Telegram User ID (comma-separated)", ""
            )
        if install_discord:
            discord_token = prompt_string("Enter your Discord Bot Token", "")
        execution_credential_source = "nexus-store"
        openclaw_broker_url = ""
        openclaw_broker_token = ""
        if enable_openclaw:
            bridge_token = prompt_string(
                "Enter the Nexus command bridge auth token for OpenClaw",
                "replace_with_a_long_random_secret",
            )
            execution_credential_source = "openclaw-broker"
            openclaw_broker_url = prompt_string(
                "Enter the OpenClaw credential broker URL",
                "http://127.0.0.1:8092/api/v1/nexus/credentials/lease",
            )
            openclaw_broker_token = prompt_string(
                "Enter the OpenClaw credential broker bearer token",
                bridge_token or "replace_with_a_shared_broker_secret",
            )

        vcs_choice = prompt_choice(
            "Which VCS platform will you be using primarily?", ["GitHub", "GitLab"]
        )

        github_token = ""
        gitlab_token = ""
        gitlab_url = ""

        if vcs_choice == 1:
            github_token = prompt_string("Enter your GitHub Personal Access Token", "")
        else:
            gitlab_token = prompt_string("Enter your GitLab Personal Access Token (glpat-...)", "")
            gitlab_url = prompt_string("Enter your GitLab Base URL", "https://gitlab.com")

        # Generate .env content
        env_content = f"""# ================================
# BOT TOKENS & IDENTITY
# ================================
TELEGRAM_TOKEN={telegram_token}
TELEGRAM_ALLOWED_USER_IDS={telegram_users}
DISCORD_TOKEN={discord_token}
TASK_CONFIRMATION_MODE=smart
NEXUS_RUNTIME_MODE={runtime_mode}
NEXUS_CHAT_TRANSCRIPT_OWNER={chat_transcript_owner}
NEXUS_AUTH_AUTHORITY={auth_authority}
NEXUS_EXECUTION_CREDENTIAL_SOURCE={execution_credential_source}

# ================================
# PROJECT & PATHS
# ================================
BASE_DIR={base_dir}
PROJECT_CONFIG_PATH=config/project_config.yaml
NEXUS_RUNTIME_DIR=/var/lib/nexus
LOGS_DIR=/var/lib/nexus/logs

# ================================
# GIT PLATFORMS
# ================================
"""
        if github_token:
            env_content += f"GITHUB_TOKEN={github_token}\n"
        elif gitlab_token:
            env_content += f"GITLAB_TOKEN={gitlab_token}\nGITLAB_BASE_URL={gitlab_url}\n"

        env_content += (
            "\n# ================================\n# OPENCLAW / COMMAND BRIDGE\n# ================================\n"
        )
        env_content += f"NEXUS_COMMAND_BRIDGE_ENABLED={'true' if enable_openclaw else 'false'}\n"
        env_content += "NEXUS_COMMAND_BRIDGE_HOST=127.0.0.1\n"
        env_content += "NEXUS_COMMAND_BRIDGE_PORT=8091\n"
        env_content += f"NEXUS_COMMAND_BRIDGE_AUTH_TOKEN={bridge_token}\n"
        env_content += (
            "NEXUS_COMMAND_BRIDGE_ALLOWED_SOURCES=openclaw\n"
            if enable_openclaw
            else "NEXUS_COMMAND_BRIDGE_ALLOWED_SOURCES=\n"
        )
        env_content += f"NEXUS_OPENCLAW_BROKER_URL={openclaw_broker_url}\n"
        env_content += f"NEXUS_OPENCLAW_BROKER_TOKEN={openclaw_broker_token}\n"
        env_content += "NEXUS_OPENCLAW_BROKER_TIMEOUT_SECONDS=15\n"

        # Storage section
        env_content += "\n# ================================\n# INFRASTRUCTURE / STORAGE\n# ================================\n"
        if use_postgres:
            env_content += "NEXUS_STORAGE_BACKEND=postgres\n"
            env_content += "NEXUS_HOST_STATE_BACKEND=postgres\n"
            if use_docker:
                env_content += "NEXUS_STORAGE_DSN=postgresql://nexus:nexus@127.0.0.1:5432/nexus\n"
                if use_redis:
                    env_content += "REDIS_URL=redis://localhost:6379/0\n"
                env_content += "DEPLOY_TYPE=compose\n"
                env_content += "COMPOSE_PROFILES=enterprise\n"
            else:
                pg_dsn = prompt_string(
                    "Enter PostgreSQL DSN", "postgresql://nexus:nexus@127.0.0.1:5432/nexus"
                )
                env_content += f"NEXUS_STORAGE_DSN={pg_dsn}\n"
                if use_redis:
                    redis_url = prompt_string("Enter Redis URL", "redis://localhost:6379/0")
                    env_content += f"REDIS_URL={redis_url}\n"
                env_content += "DEPLOY_TYPE=systemd\n"
        else:
            env_content += "NEXUS_STORAGE_BACKEND=filesystem\n"
            env_content += "DEPLOY_TYPE=systemd\n"
            env_content += "COMPOSE_PROFILES=\n"

        # Write .env
        print("\nWriting .env file...")
        env_file.write_text(env_content)

    if has_core == 1:
        # Copy project config if it doesn't exist
        config_dir = bot_dir / "config"
        config_dir.mkdir(exist_ok=True)
        project_config = config_dir / "project_config.yaml"

        if not project_config.exists():
            example_config = bot_dir.parent / "project_config.yaml"
            if example_config.exists():
                print("Copying example project_config.yaml...")
                shutil.copy2(example_config, project_config)
            else:
                print("Creating basic project_config.yaml...")
                project_config.write_text(
                    f"""# ==========================================================
# Nexus ARC - Project Configurations
# ==========================================================
# This file maps your physical repositories to Nexus workspaces
# and assigns them to specific Agent Directories and Workflows.

# The "Core" repo is where you centrally store all your
# *-agent.yaml persona descriptions and .yaml workflow files.
# By keeping them in one repo, all your AI agents can collaborate
# across your entire engineering ecosystem.

workflow_definition_path: {core_repo}/workflows/default_workflow.yaml
shared_agents_dir: {core_repo}/agents

# Global Routing & AI Preferences
merge_queue:
  review_mode: manual

system_operations:
  inbox: triage      # Agent type that handles new webhook events
  launch: triage     # Agent type that handles workflow initiation
  default: triage

ai_tool_preferences:
  triage: {{ profile: fast, provider: auto }}
  developer: {{ profile: reasoning, provider: auto }}

# Your First Project
{project_name}:
  workspace: {workspace_dir}
  git_repo: {git_repo}
  git_repos:
    - {git_repo}
  agents_dir: {core_repo}/agents
"""
                )

    # 4. CLI Tools Setup
    print(f"\n--- {step_num}. Agent CLI Tools ---")
    step_num += 1
    print("Which CLI tools do you want to automatically install?")
    print("Note: Copilot, Gemini, Codex, and Claude require 'npm' to be installed.")

    cli_options = [
        "GitHub CLI (gh)",
        "GitLab CLI (glab)",
        "GitHub Copilot CLI",
        "Google Gemini CLI",
        "OpenAI Codex CLI",
        "Anthropic Claude Code",
        "Ollama",
    ]
    selected_clis = prompt_multi_choice("Select tools to install", cli_options)

    if selected_clis:
        print("\n--- Installing Agent CLI Tools ---")

        install_gh = 1 in selected_clis
        install_glab = 2 in selected_clis
        install_copilot = 3 in selected_clis
        install_gemini = 4 in selected_clis
        install_codex = 5 in selected_clis
        install_claude = 6 in selected_clis
        install_ollama = 7 in selected_clis

        # NPM-based installs
        npm_packages = []
        codex_npm_package = (
            str(os.getenv("NEXUS_CODEX_NPM_PACKAGE", "@openai/codex@0.113.0")).strip()
            or "@openai/codex@0.113.0"
        )
        if install_copilot:
            npm_packages.append("@github/copilot")
        if install_gemini:
            npm_packages.append("@google/gemini-cli")
        if install_codex:
            npm_packages.append(codex_npm_package)
        if install_claude:
            npm_packages.append("@anthropic-ai/claude-code")

        if npm_packages:
            if not shutil.which("npm"):
                print(
                    "⚠️ 'npm' is not installed. Skipping Copilot/Gemini/Codex/Claude CLI installation."
                )
            else:
                run_command(["npm", "install", "-g"] + npm_packages, shell=sys.platform == "nt")
                print(f"✅ NPM packages installed: {' '.join(npm_packages)}")

        # System-based installs
        if sys.platform == "darwin":
            brew_packages = []
            if install_gh:
                brew_packages.append("gh")
            if install_glab:
                brew_packages.append("glab")
            if install_ollama:
                brew_packages.append("ollama")

            if brew_packages:
                if shutil.which("brew"):
                    run_command(["brew", "install"] + brew_packages)
                    print(f"✅ Installed via Homebrew: {' '.join(brew_packages)}")
                else:
                    print(
                        f"⚠️ Homebrew not found. Skipping installation of: {' '.join(brew_packages)}"
                    )
        elif sys.platform.startswith("linux"):
            if install_gh:
                if shutil.which("apt"):
                    print("Installing GitHub CLI via apt...")
                    run_command(
                        [
                            "curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg"
                        ],
                        shell=True,
                    )
                    run_command(
                        ["sudo chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg"],
                        shell=True,
                    )
                    run_command(
                        [
                            'echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null'
                        ],
                        shell=True,
                    )
                    run_command(["sudo apt update && sudo apt install -y gh"], shell=True)
                    print("✅ GitHub CLI installed.")
                else:
                    print("⚠️ Only 'apt' is supported for automated Linux install of 'gh'.")

            if install_glab:
                print("Installing GitLab CLI...")
                run_command(["curl -sL https://j.mp/glab-cli | sudo sh"], shell=True)
                print("✅ GitLab CLI installed.")

            if install_ollama:
                print("Installing Ollama...")
                run_command(["curl -fsSL https://ollama.com/install.sh | sh"], shell=True)
                print("✅ Ollama installed.")

    # Perform Infra Setup if requested
    if setup_db:
        print("\n--- 6. Installing Infrastructure Components ---")
        if use_docker:
            if not shutil.which("docker"):
                print(
                    "[ERROR] Docker is not installed. Please install Docker and run `docker compose up -d` manually."
                )
            else:
                compose_file = bot_dir / "docker-compose.yml"
                if not compose_file.exists():
                    local_compose = Path(__file__).parent / "docker-compose.yml"
                    if local_compose.exists():
                        print("Copying local docker-compose.yml...")
                        shutil.copy2(local_compose, compose_file)
                    else:
                        print("Downloading docker-compose.yml from GitHub...")
                        import urllib.request

                        try:
                            urllib.request.urlretrieve(
                                "https://raw.githubusercontent.com/Ghabs95/nexus-arc/main/examples/nexus-bot/docker-compose.yml",
                                compose_file,
                            )
                        except Exception as e:
                            print(f"⚠️ Failed to download docker-compose.yml: {e}")

                if compose_file.exists():
                    try:
                        subprocess.run(["docker", "compose", "up", "-d"], cwd=bot_dir, check=True)
                        print("✅ Docker components started successfully.")
                    except subprocess.CalledProcessError:
                        print("⚠️ Failed to start Docker Compose. Please check the logs.")
                else:
                    print(f"⚠️ docker-compose.yml not found in {bot_dir}. Skipping.")
        else:
            if sys.platform == "darwin":
                if not shutil.which("brew"):
                    print("[ERROR] Homebrew not found. Skipping system package installation.")
                else:
                    packages = ["postgresql@15"]
                    if use_redis:
                        packages.append("redis")
                    run_command(["brew", "install"] + packages)
                    run_command(["brew", "services", "start", "postgresql@15"])
                    if use_redis:
                        run_command(["brew", "services", "start", "redis"])
                    print("✅ Infrastructure installed and started via Homebrew.")
                    print("⚠️ Note: You may need to create the 'nexus' database user manually:")
                    print("   createuser -s postgres")
                    print("   psql -U postgres -c \"CREATE USER nexus WITH PASSWORD 'nexus';\"")
                    print('   psql -U postgres -c "CREATE DATABASE nexus OWNER nexus;"')
            elif sys.platform.startswith("linux"):
                if shutil.which("apt"):
                    package_list = ["postgresql"]
                    if use_redis:
                        package_list.append("redis-server")
                    print(
                        "This requires sudo access to install "
                        + ("PostgreSQL and Redis." if use_redis else "PostgreSQL.")
                    )
                    run_command(["sudo", "apt", "update"])
                    run_command(["sudo", "apt", "install", "-y"] + package_list)
                    if use_redis:
                        run_command(["sudo", "systemctl", "enable", "--now", "redis-server"])
                    run_command(
                        ["sudo", "-u", "postgres", "createuser", "nexus", "--pwprompt"]
                    )  # Will block for prompt
                    run_command(["sudo", "-u", "postgres", "createdb", "nexus", "--owner=nexus"])
                    print("✅ Infrastructure installed via apt.")
                else:
                    print(
                        "⚠️ Only 'apt' is supported for automated Linux installs right now. Please install the required infrastructure manually."
                    )

    print("\n=======================================")
    print(" 🎉 Installation Complete! 🎉")
    print("=======================================")
    print("\nNext steps:")
    print(" 1. Review the generated .env file")
    print(" 2. Review config/project_config.yaml")
    if runtime_mode == "openclaw":
        print(" 3. Install the core package if needed: pip install -e .")
        print(" 4. Start the bridge/runtime with: nexus-bridge")
        print(" 5. Install the OpenClaw plugin from packages/nexus-arc\n")
    elif runtime_mode == "advanced":
        print(" 3. Install the core package if needed: pip install -e .")
        if install_telegram or install_discord:
            print(" 4. Install the bot package if needed: pip install -e .[nexus-bot]")
        if enable_openclaw:
            print(" 5. Start the bridge/runtime with: nexus-bridge")
        active_surfaces = []
        if install_telegram:
            active_surfaces.append("nexus-telegram-bot")
        if install_discord:
            active_surfaces.append("nexus-discord-bot")
        if active_surfaces:
            print(f" 6. Start selected bot runtimes: {' and '.join(active_surfaces)}\n")
        else:
            print(" 6. No native Nexus bots were selected for this install.\n")
    else:
        print(" 3. Make sure to pip install the package if you haven't: pip install -e .[nexus-bot]")
        print(" 4. Start bot runtimes with: nexus-telegram-bot and nexus-discord-bot\n")


if __name__ == "__main__":
    main()
