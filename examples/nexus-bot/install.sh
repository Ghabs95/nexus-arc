#!/usr/bin/env bash

# Nexus ARC Interactive Installation Script (Bash Version)
# Note: Python is still highly recommended because 'nexus-bot' requires it anyway.

set -e

# Ensure cursor is restored if script exits early
trap 'tput cnorm 2>/dev/null || true' EXIT INT TERM

# --- Helpers ---
clear_screen() {
    clear
}

prompt_choice() {
    local question="$1"
    shift
    local choices=("$@")
    local selected=0

    echo -e "\n${question}" >&2
    tput civis >&2 2>/dev/null || true
    
    for i in "${!choices[@]}"; do echo "" >&2; done
    local up_cnt=${#choices[@]}
    
    redraw() {
        echo -en "\033[${up_cnt}A" >&2
        for i in "${!choices[@]}"; do
            if [ "$i" -eq "$selected" ]; then
                echo -e "\033[36m> ${choices[$i]}\033[0m\033[K" >&2
            else
                echo -e "  ${choices[$i]}\033[K" >&2
            fi
        done
    }
    
    redraw
    while true; do
        IFS= read -rsn1 key < /dev/tty
        if [[ $key == $'\x1b' ]]; then
            read -rsn2 key < /dev/tty
            if [[ $key == '[A' ]]; then # Up
                ((selected--))
                if [ $selected -lt 0 ]; then selected=$((${#choices[@]} - 1)); fi
                redraw
            elif [[ $key == '[B' ]]; then # Down
                ((selected++))
                if [ $selected -ge ${#choices[@]} ]; then selected=0; fi
                redraw
            fi
        elif [[ $key == "" ]]; then # Enter
            tput cnorm >&2 2>/dev/null || true
            echo $((selected + 1))
            return
        fi
    done
}

prompt_multi_choice() {
    local question="$1"
    shift
    local choices=("$@")
    local selected=0
    local -a toggled
    for i in "${!choices[@]}"; do
        toggled[$i]=0
    done
    
    echo -e "\n${question} \033[90m(Use Space to toggle, Enter to confirm)\033[0m" >&2
    tput civis >&2 2>/dev/null || true
    for i in "${!choices[@]}"; do echo "" >&2; done
    
    local up_cnt=${#choices[@]}
    redraw() {
        echo -en "\033[${up_cnt}A" >&2
        for i in "${!choices[@]}"; do
            local prefix="[ ]"
            if [ "${toggled[$i]}" -eq 1 ]; then
                prefix="\033[32m[x]\033[0m"
            fi
            
            if [ "$i" -eq "$selected" ]; then
                echo -e "\033[36m> ${prefix} ${choices[$i]}\033[0m\033[K" >&2
            else
                echo -e "  ${prefix} ${choices[$i]}\033[K" >&2
            fi
        done
    }
    
    redraw
    while true; do
        IFS= read -rsn1 key < /dev/tty
        if [[ $key == $'\x1b' ]]; then
            read -rsn2 key < /dev/tty
            if [[ $key == '[A' ]]; then # Up
                ((selected--))
                if [ $selected -lt 0 ]; then selected=$((${#choices[@]} - 1)); fi
                redraw
            elif [[ $key == '[B' ]]; then # Down
                ((selected++))
                if [ $selected -ge ${#choices[@]} ]; then selected=0; fi
                redraw
            fi
        elif [[ $key == " " ]]; then # Space
            if [ "${toggled[$selected]}" -eq 1 ]; then
                toggled[$selected]=0
            else
                toggled[$selected]=1
            fi
            redraw
        elif [[ $key == "" ]]; then # Enter
            local res=""
            for i in "${!choices[@]}"; do
                if [ "${toggled[$i]}" -eq 1 ]; then
                    res="$res $((i+1))"
                fi
            done
            tput cnorm >&2 2>/dev/null || true
            res="${res#"${res%%[![:space:]]*}"}"
            echo "$res"
            return
        fi
    done
}

prompt_string() {
    local question="$1"
    local default="$2"
    local val=""
    
    if [ -n "$default" ]; then
        read -e -p "${question} [${default}]: " val < /dev/tty
        if [ -z "$val" ]; then
            echo "$default"
            return
        fi
    else
        read -e -p "${question}: " val < /dev/tty
    fi
    echo "$val"
}

# --- Main execution ---
clear_screen

echo "======================================="
echo " 🚀 Welcome to Nexus ARC Installation (Bash) 🚀"
echo "======================================="

step_num=1
echo -e "\n--- ${step_num}. Deployment Mode ---"
((step_num++))
mode_choice=$(prompt_choice "Which storage mode do you want to use?" "Lite (Filesystem only)" "Enterprise (PostgreSQL + Redis)")
is_enterprise=0
if [ "$mode_choice" == "2" ]; then
    is_enterprise=1
fi

setup_db=0
use_docker=0
if [ "$is_enterprise" -eq 1 ]; then
    echo -e "\n--- ${step_num}. Infrastructure Setup ---"
    ((step_num++))
    infra_choice=$(prompt_choice "How do you want to run PostgreSQL and Redis?" "Docker Compose (Sandboxed)" "System packages (e.g. brew or apt)" "I already have them running")
    if [ "$infra_choice" == "1" ]; then
        use_docker=1
        setup_db=1
    elif [ "$infra_choice" == "2" ]; then
        setup_db=1
    fi
fi

has_core=$(prompt_choice "Do you already have a Nexus core repository configured locally?" "No, I need to configure my first project" "Yes, I already have one")

if [ "$has_core" -eq 1 ]; then
    echo -e "\n[Concept: The Core Repo]"
    echo "Nexus ARC works best when you have a centralized 'core' repository."
    echo "This is a single git repository where you store your '*-agent.yaml' definitions"
    echo "and 'workflow.yaml' files, effectively creating an org-chart of AI agents."
    core_repo=$(prompt_string "What is the name of your organization's 'core' repository?" "nexus-arc")

    echo -e "\n[Workspaces & Projects]"
    HOME_DIR=~
    base_dir=$(prompt_string "What is your base directory where all your git clones live?" "${HOME_DIR}/git")

    echo -e "\n[Your First Project]"
    echo "Nexus groups multiple git repositories inside a single 'workspace' folder inside ${base_dir}."
    project_name=$(prompt_string "What is the short name for this project? (e.g. my-project)" "my-project")
    workspace_dir=$(prompt_string "What is the workspace folder name (inside ${base_dir})?" "${project_name}")
    git_repo=$(prompt_string "What is the git repository holding agents/workflows for this workspace? (e.g. my-org/${project_name}-nexus or username/${project_name}-nexus)" "my-org/${project_name}-nexus")
    
    bot_dir_default="${base_dir}/${workspace_dir}/${core_repo}"
else
    echo -e "\n[Existing Core Repo]"
    HOME_DIR=~
    bot_dir_default=$(prompt_string "What is the full path to your existing core repository?" "${HOME_DIR}/git/my-workspace/nexus-arc")
fi



echo -e "\n--- ${step_num}. Installation Directory ---"
((step_num++))
bot_dir_input=$(prompt_string "Where should we create the configuration files? (e.g. .env, config/)" "${bot_dir_default}")
bot_dir_input="${bot_dir_input/#\~/$HOME_DIR}"
mkdir -p "$bot_dir_input"
BOT_DIR=$(cd "$bot_dir_input" && pwd)
ENV_FILE="${BOT_DIR}/.env"

write_env=1
if [ -f "$ENV_FILE" ]; then
    replace=$(prompt_choice "An existing .env file was found. Overwrite?" "No, keep it" "Yes, overwrite")
    if [ "$replace" == "1" ]; then
        echo "Keeping existing .env file."
        write_env=0
    fi
fi

if [ "$write_env" -eq 1 ]; then
    echo -e "\n--- ${step_num}. Credentials & Keys ---"
    ((step_num++))
    telegram_token=$(prompt_string "Enter your Telegram Bot Token (leave empty to skip)" "")
    telegram_users=$(prompt_string "Enter your Telegram User ID (comma-separated, leave empty to skip)" "")
    discord_token=$(prompt_string "Enter your Discord Bot Token (leave empty to skip)" "")
    
    vcs_choice=$(prompt_choice "Which VCS platform will you be using primarily?" "GitHub" "GitLab")
    github_token=""
    gitlab_token=""
    gitlab_url=""
    
    if [ "$vcs_choice" == "1" ]; then
        github_token=$(prompt_string "Enter your GitHub Personal Access Token" "")
    else
        gitlab_token=$(prompt_string "Enter your GitLab Personal Access Token" "")
        gitlab_url=$(prompt_string "Enter your GitLab Base URL" "https://gitlab.com")
    fi

    # Write .env content
    echo "Writing .env file..."
    cat <<EOF > "$ENV_FILE"
# ================================
# BOT TOKENS & IDENTITY
# ================================
TELEGRAM_TOKEN=${telegram_token}
TELEGRAM_ALLOWED_USER_IDS=${telegram_users}
DISCORD_TOKEN=${discord_token}
TASK_CONFIRMATION_MODE=smart

# ================================
# PROJECT & PATHS
# ================================
BASE_DIR=${base_dir}
PROJECT_CONFIG_PATH=config/project_config.yaml
NEXUS_RUNTIME_DIR=./data
LOGS_DIR=./logs

# ================================
# GIT PLATFORMS
# ================================
EOF

    if [ -n "$github_token" ]; then
        echo "GITHUB_TOKEN=$github_token" >> "$ENV_FILE"
    elif [ -n "$gitlab_token" ]; then
        echo "GITLAB_TOKEN=$gitlab_token" >> "$ENV_FILE"
        echo "GITLAB_BASE_URL=$gitlab_url" >> "$ENV_FILE"
    fi

    echo -e "\n# ================================" >> "$ENV_FILE"
    echo "# INFRASTRUCTURE / STORAGE" >> "$ENV_FILE"
    echo "# ================================" >> "$ENV_FILE"

    if [ "$is_enterprise" -eq 1 ]; then
        echo "NEXUS_STORAGE_BACKEND=postgres" >> "$ENV_FILE"
        echo "NEXUS_HOST_STATE_BACKEND=postgres" >> "$ENV_FILE"
        if [ "$use_docker" -eq 1 ]; then
            echo "NEXUS_STORAGE_DSN=postgresql://nexus:nexus@127.0.0.1:5432/nexus" >> "$ENV_FILE"
            echo "REDIS_URL=redis://localhost:6379/0" >> "$ENV_FILE"
            echo "DEPLOY_TYPE=compose" >> "$ENV_FILE"
            echo "COMPOSE_PROFILES=enterprise" >> "$ENV_FILE"
        else
            pg_dsn=$(prompt_string "Enter PostgreSQL DSN" "postgresql://nexus:nexus@127.0.0.1:5432/nexus")
            redis_url=$(prompt_string "Enter Redis URL" "redis://localhost:6379/0")
            echo "NEXUS_STORAGE_DSN=${pg_dsn}" >> "$ENV_FILE"
            echo "REDIS_URL=${redis_url}" >> "$ENV_FILE"
            echo "DEPLOY_TYPE=systemd" >> "$ENV_FILE"
        fi
    else
        echo "NEXUS_STORAGE_BACKEND=filesystem" >> "$ENV_FILE"
        echo "DEPLOY_TYPE=systemd" >> "$ENV_FILE"
        echo "COMPOSE_PROFILES=" >> "$ENV_FILE"
    fi
fi

if [ "$has_core" -eq 1 ]; then
    CONFIG_DIR="${BOT_DIR}/config"
    mkdir -p "$CONFIG_DIR"
    PROJECT_CONFIG="${CONFIG_DIR}/project_config.yaml"
    if [ ! -f "$PROJECT_CONFIG" ]; then
        echo "Creating basic project_config.yaml..."
        cat <<EOF > "$PROJECT_CONFIG"
# ==========================================================
# Nexus ARC - Project Configurations
# ==========================================================
# This file maps your physical repositories to Nexus workspaces
# and assigns them to specific Agent Directories and Workflows.

# The "Core" repo is where you centrally store all your
# .agent.md persona descriptions and .yaml workflow files.
# By keeping them in one repo, all your AI agents can collaborate
# across your entire engineering ecosystem.

workflow_definition_path: ${core_repo}/workflows/default_workflow.yaml
shared_agents_dir: ${core_repo}/agents

# Global Routing & AI Preferences
merge_queue:
  review_mode: manual

system_operations:
  inbox: triage      # Agent type that handles new webhook events
  launch: triage     # Agent type that handles workflow initiation
  default: triage

ai_tool_preferences:
  triage: { profile: fast, provider: auto }
  developer: { profile: reasoning, provider: auto }

# Your First Project
${project_name}:
  workspace: ${workspace_dir}
  git_repo: ${git_repo}
  git_repos:
    - ${git_repo}
  agents_dir: ${core_repo}/agents
EOF
    fi
fi

# Agent CLI Tools
echo -e "\n--- ${step_num}. Agent CLI Tools ---"
((step_num++))
echo "Which CLI tools do you want to install? Note: Copilot, Gemini, Codex, and Claude require 'npm'."
cli_choices=("GitHub CLI" "GitLab CLI" "Copilot CLI" "Gemini CLI" "Codex CLI" "Claude Code" "Ollama")
selected_clis=$(prompt_multi_choice "Select tools to install" "${cli_choices[@]}")

install_gh=0
install_glab=0
install_copilot=0
install_gemini=0
install_codex=0
install_claude=0
install_ollama=0

for s in $selected_clis; do
    case $s in
        1) install_gh=1 ;;
        2) install_glab=1 ;;
        3) install_copilot=1 ;;
        4) install_gemini=1 ;;
        5) install_codex=1 ;;
        6) install_claude=1 ;;
        7) install_ollama=1 ;;
    esac
done

if [ -n "$selected_clis" ]; then
    echo -e "\n--- Installing Agent CLI Tools ---"
    
    if [ "$install_copilot" -eq 1 ] || [ "$install_gemini" -eq 1 ] || [ "$install_codex" -eq 1 ] || [ "$install_claude" -eq 1 ]; then
        if ! command -v npm &> /dev/null; then
            echo "⚠️ 'npm' is not installed. Skipping Copilot/Gemini/Codex/Claude installation."
        else
            npm_packages=""
            [ "$install_copilot" -eq 1 ] && npm_packages="$npm_packages @github/copilot"
            [ "$install_gemini" -eq 1 ] && npm_packages="$npm_packages @google/gemini-cli"
            [ "$install_codex" -eq 1 ] && npm_packages="$npm_packages @openai/codex"
            [ "$install_claude" -eq 1 ] && npm_packages="$npm_packages @anthropic-ai/claude-code"
            npm install -g $npm_packages
            echo "✅ NPM packages installed."
        fi
    fi

    OS=$(uname -s)
    if [ "$OS" == "Darwin" ]; then
        brew_packages=""
        [ "$install_gh" -eq 1 ] && brew_packages="$brew_packages gh"
        [ "$install_glab" -eq 1 ] && brew_packages="$brew_packages glab"
        [ "$install_ollama" -eq 1 ] && brew_packages="$brew_packages ollama"

        if [ -n "$brew_packages" ]; then
            if ! command -v brew &> /dev/null; then
                echo "⚠️ Homebrew not found. Skipping $brew_packages"
            else
                brew install $brew_packages
                echo "✅ Installed via brew: $brew_packages"
            fi
        fi
    elif [ "$OS" == "Linux" ]; then
        if [ "$install_gh" -eq 1 ]; then
            if command -v apt &> /dev/null; then
                curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
                sudo chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg
                echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null
                sudo apt update && sudo apt install -y gh
                echo "✅ Installed gh"
            else
                echo "⚠️ apt not found, skipping gh install"
            fi
        fi
        
        if [ "$install_glab" -eq 1 ]; then
            curl -sL https://j.mp/glab-cli | sudo sh
            echo "✅ Installed glab"
        fi

        if [ "$install_ollama" -eq 1 ]; then
            curl -fsSL https://ollama.com/install.sh | sh
            echo "✅ Installed ollama"
        fi
    fi
fi

# 5. Infrastructure
if [ "$setup_db" -eq 1 ]; then
    echo -e "\n--- 6. Installing Infrastructure Components ---"
    if [ "$use_docker" -eq 1 ]; then
        if ! command -v docker &> /dev/null; then
            echo "[ERROR] Docker not found."
        else
            COMPOSE_FILE="${BOT_DIR}/docker-compose.yml"
            SCRIPT_DIR="$(dirname "$0")"
            if [ ! -f "$COMPOSE_FILE" ]; then
                if [ -f "$SCRIPT_DIR/docker-compose.yml" ]; then
                    echo "Copying local docker-compose.yml..."
                    cp "$SCRIPT_DIR/docker-compose.yml" "$COMPOSE_FILE"
                else
                    echo "Downloading docker-compose.yml from GitHub..."
                    curl -fsSL https://raw.githubusercontent.com/Ghabs95/nexus-arc/main/examples/nexus-bot/docker-compose.yml -o "$COMPOSE_FILE" || true
                fi
            fi
            
            if [ -f "$COMPOSE_FILE" ]; then
                (cd "$BOT_DIR" && docker compose up -d)
                echo "✅ Docker components started."
            else
                echo "⚠️ docker-compose.yml not found and failed to download."
            fi
        fi
    else
        OS=$(uname -s)
        if [ "$OS" == "Darwin" ]; then
            if command -v brew &> /dev/null; then
                brew install postgresql@15 redis
                brew services start postgresql@15
                brew services start redis
                echo "✅ DBs installed via Brew."
            fi
        elif [ "$OS" == "Linux" ]; then
            if command -v apt &> /dev/null; then
                sudo apt update && sudo apt install -y postgresql redis-server
                sudo systemctl enable --now redis-server
                sudo -u postgres createuser nexus --pwprompt
                sudo -u postgres createdb nexus --owner=nexus
                echo "✅ DBs installed via APT."
            fi
        fi
    fi
fi

echo -e "\n======================================="
echo " 🎉 Installation Complete! 🎉"
echo "======================================="
echo -e "\nNext steps:"
echo " 1. Review the generated .env file"
echo " 2. Review config/project_config.yaml"
echo " 3. pip install -e ."
echo " 4. Run nexus-bot"