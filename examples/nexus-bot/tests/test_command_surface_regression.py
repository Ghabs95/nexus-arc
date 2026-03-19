from __future__ import annotations

import ast
from pathlib import Path

from nexus.core import command_contract

REPO_ROOT = Path(__file__).resolve().parents[3]
TELEGRAM_BOOTSTRAP_FILE = REPO_ROOT / "nexus" / "core" / "telegram" / "telegram_main_bootstrap_service.py"
TELEGRAM_BOT_FILE = REPO_ROOT / "examples" / "nexus-bot" / "src" / "telegram_bot.py"
DISCORD_BOT_FILE = REPO_ROOT / "examples" / "nexus-bot" / "src" / "discord_bot.py"
INTERACTIVE_AGENT_FILE = REPO_ROOT / "examples" / "nexus-bot" / "src" / "interactive_agent.py"

TELEGRAM_FRONTEND_ONLY_COMMANDS = {
    "login",
    "setup_status",
    "whoami",
    "rename",
}

DISCORD_FRONTEND_ONLY_COMMANDS = {
    "login",
    "setup-status",
    "whoami",
}

CENTRALIZED_INTERACTIVE_COMMANDS = {
    "active",
    "agents",
    "assign",
    "audit",
    "chat",
    "chatagents",
    "comments",
    "continue",
    "direct",
    "forget",
    "fuse",
    "implement",
    "kill",
    "logs",
    "logsfull",
    "myissues",
    "pause",
    "plan",
    "prepare",
    "reconcile",
    "reprocess",
    "respond",
    "resume",
    "stats",
    "status",
    "stop",
    "tail",
    "tailstop",
    "track",
    "tracked",
    "untrack",
    "wfstate",
}


def _parse_module(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _str_constant(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _attribute_chain(node: ast.AST) -> tuple[str, ...]:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return tuple(reversed(parts))


def _extract_telegram_command_specs(path: Path) -> list[str]:
    tree = _parse_module(path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "command_specs" for target in node.targets):
            continue
        if not isinstance(node.value, ast.List):
            continue
        commands: list[str] = []
        for item in node.value.elts:
            if not isinstance(item, ast.Tuple) or not item.elts:
                continue
            command = _str_constant(item.elts[0])
            if command:
                commands.append(command)
        return commands
    raise AssertionError(f"Could not find command_specs in {path}")


def _extract_telegram_conversation_entry_commands(path: Path) -> list[str]:
    tree = _parse_module(path)
    commands: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "CommandHandler":
            continue
        if not node.args:
            continue
        command = _str_constant(node.args[0])
        if command == "new":
            commands.append(command)
    return commands


def _extract_discord_slash_commands(path: Path) -> list[str]:
    tree = _parse_module(path)
    commands: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if _attribute_chain(decorator.func) != ("bot", "tree", "command"):
                continue
            for keyword in decorator.keywords:
                if keyword.arg != "name":
                    continue
                command = _str_constant(keyword.value)
                if command:
                    commands.append(command)
    return commands


def _extract_registered_plugin_commands(path: Path) -> list[str]:
    tree = _parse_module(path)
    commands: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "register_command":
            continue
        if not node.args:
            continue
        command = _str_constant(node.args[0])
        if command:
            commands.append(command)
    return commands


def _assert_command_surface_matches(actual: list[str], expected: set[str]) -> None:
    actual_set = set(actual)
    missing = sorted(expected - actual_set)
    unexpected = sorted(actual_set - expected)
    duplicates = sorted({command for command in actual if actual.count(command) > 1})
    assert not missing and not unexpected and not duplicates, (
        f"missing={missing}, unexpected={unexpected}, duplicates={duplicates}"
    )


def test_telegram_command_surface_matches_contract_and_frontend_extras():
    actual = _extract_telegram_command_specs(
        TELEGRAM_BOOTSTRAP_FILE
    ) + _extract_telegram_conversation_entry_commands(TELEGRAM_BOT_FILE)
    expected = set(command_contract.TELEGRAM_COMMANDS) | TELEGRAM_FRONTEND_ONLY_COMMANDS
    _assert_command_surface_matches(actual, expected)


def test_discord_command_surface_matches_contract_and_frontend_extras():
    actual = _extract_discord_slash_commands(DISCORD_BOT_FILE)
    expected = set(command_contract.DISCORD_COMMANDS) | DISCORD_FRONTEND_ONLY_COMMANDS
    _assert_command_surface_matches(actual, expected)


def test_centralized_interactive_command_surface_matches_expected_bridge_commands():
    actual = _extract_registered_plugin_commands(INTERACTIVE_AGENT_FILE)
    _assert_command_surface_matches(actual, CENTRALIZED_INTERACTIVE_COMMANDS)
