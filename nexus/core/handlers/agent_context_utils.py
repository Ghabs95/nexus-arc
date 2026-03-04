"""Shared utility helpers for agent context and feature-ideation prompt assembly."""

from __future__ import annotations

import os
import re
from typing import Any

import yaml

from nexus.core.prompt_budget import apply_prompt_budget, truncate_text


def resolve_project_root(base_dir: str, project_key: str, project_cfg: dict[str, Any]) -> str:
    normalized_base_dir = str(base_dir or "").strip()
    if not normalized_base_dir:
        return ""

    if isinstance(project_cfg, dict):
        workspace = str(project_cfg.get("workspace", "")).strip()
        if workspace:
            if os.path.isabs(workspace):
                return workspace
            return os.path.join(normalized_base_dir, workspace)

    fallback = os.path.join(normalized_base_dir, project_key)
    return fallback if os.path.isdir(fallback) else ""


def resolve_path(project_root: str, raw_path: str) -> str:
    candidate = str(raw_path or "").strip()
    if not candidate:
        return ""
    if os.path.isabs(candidate):
        return candidate
    primary = os.path.normpath(os.path.join(project_root, candidate))
    if os.path.exists(primary):
        return primary

    # Some configs store paths relative to BASE_DIR (e.g. "ghabs/...") while the
    # resolver receives a project_root (e.g. ".../ghabs"). Try the parent root too.
    parent_root = os.path.dirname(os.path.normpath(project_root))
    if parent_root and parent_root != project_root:
        fallback = os.path.normpath(os.path.join(parent_root, candidate))
        if os.path.exists(fallback):
            return fallback

    return primary


def normalize_paths(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def extract_referenced_paths_from_markdown(agents_text: str) -> list[str]:
    if not agents_text:
        return []

    referenced: list[str] = []
    for match in re.findall(r"`([^`]+)`", agents_text):
        candidate = str(match).strip()
        if not candidate:
            continue
        if candidate.startswith("/"):
            continue
        if " " in candidate and "/" not in candidate and "." not in candidate:
            continue
        if candidate not in referenced:
            referenced.append(candidate)
    return referenced


def collect_context_candidate_files(
    context_root: str,
    seed_files: list[str] | None = None,
    search_root: str | None = None,
) -> list[str]:
    candidates: list[str] = []
    seed_markdown_files: list[str] = []

    def _append_file(path: str) -> None:
        if os.path.isfile(path) and path not in candidates:
            candidates.append(path)

    def _append_from_dir(path: str, max_files: int = 12) -> None:
        if not os.path.isdir(path):
            return
        count = 0
        for root, _dirs, files in os.walk(path):
            for name in sorted(files):
                if not name.endswith((".md", ".yaml", ".yml", ".json", ".txt")):
                    continue
                full = os.path.join(root, name)
                if full not in candidates:
                    candidates.append(full)
                    count += 1
                if count >= max_files:
                    return

    def _append_by_basename(filename: str) -> None:
        if not filename or "/" in filename or "\\" in filename:
            return
        roots: list[str] = []
        for root in [context_root, search_root]:
            normalized = str(root or "").strip()
            if normalized and os.path.isdir(normalized) and normalized not in roots:
                roots.append(normalized)

        matches: list[str] = []
        for root in roots:
            for walk_root, _dirs, files in os.walk(root):
                if filename not in files:
                    continue
                full = os.path.join(walk_root, filename)
                if os.path.isfile(full) and full not in matches:
                    matches.append(full)

        if not matches:
            return

        def _score(path: str) -> tuple[int, int, str]:
            normalized = path.replace("\\", "/").lower()
            docs_bonus = 0 if "/docs/" in normalized else 1
            rel_len = len(os.path.relpath(path, search_root or context_root))
            return (docs_bonus, rel_len, normalized)

        _append_file(sorted(matches, key=_score)[0])

    seed = seed_files if isinstance(seed_files, list) and seed_files else []

    if not seed:
        return candidates

    for rel in seed:
        resolved = os.path.join(context_root, rel)
        if os.path.isfile(resolved):
            _append_file(resolved)
            if resolved.lower().endswith(".md"):
                seed_markdown_files.append(resolved)
        elif os.path.isdir(resolved):
            _append_from_dir(resolved)

    for seed_path in seed_markdown_files:
        seed_text = ""
        try:
            with open(seed_path, encoding="utf-8") as handle:
                seed_text = handle.read()
        except Exception:
            seed_text = ""

        for rel in extract_referenced_paths_from_markdown(seed_text):
            resolved = os.path.join(context_root, rel)
            if os.path.isfile(resolved):
                _append_file(resolved)
            elif os.path.isdir(resolved):
                _append_from_dir(resolved)
            else:
                _append_by_basename(rel)

    return candidates


def extract_agent_prompt_metadata_from_yaml(path: str, max_chars: int = 3000) -> tuple[str, str]:
    try:
        with open(path, encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}
    except Exception:
        return "", ""

    if not isinstance(payload, dict):
        return "", ""
    spec = payload.get("spec")
    if not isinstance(spec, dict):
        return "", ""

    agent_type = str(spec.get("agent_type") or "").strip().lower()

    prompt = str(spec.get("prompt_template") or "").strip()
    if not prompt:
        prompt = str(spec.get("purpose") or "").strip()
    if not prompt:
        return "", agent_type

    return prompt[:max_chars], agent_type


def load_agent_prompt_from_definition(
    base_dir: str,
    project_root: str,
    project_cfg: dict[str, Any],
    routed_agent_type: str,
) -> str:
    if not project_root or not isinstance(project_cfg, dict):
        return ""

    agents_dir = str(project_cfg.get("agents_dir", "")).strip()
    if not agents_dir:
        return ""

    agents_root = (
        agents_dir if os.path.isabs(agents_dir) else os.path.join(str(base_dir or ""), agents_dir)
    )
    if not os.path.isdir(agents_root):
        return ""

    by_filename_match: str | None = None
    by_type_match: str | None = None

    preferred_filename = os.path.join(agents_root, f"{routed_agent_type}.yaml")
    if os.path.isfile(preferred_filename):
        prompt, _agent_type = extract_agent_prompt_metadata_from_yaml(preferred_filename)
        if prompt:
            return prompt

    try:
        for entry in sorted(os.listdir(agents_root)):
            if not entry.endswith((".yaml", ".yml")):
                continue
            candidate = os.path.join(agents_root, entry)
            prompt, agent_type = extract_agent_prompt_metadata_from_yaml(candidate)
            if not prompt:
                continue
            lowered = entry.lower()
            if agent_type == routed_agent_type and by_type_match is None:
                by_type_match = prompt
            if routed_agent_type in lowered and by_filename_match is None:
                by_filename_match = prompt
    except Exception:
        return ""

    if by_type_match:
        return by_type_match
    if by_filename_match:
        return by_filename_match

    return ""


def load_role_context(
    project_root: str,
    agent_cfg: dict[str, Any],
    max_chars: int = 18000,
    mode: str | None = None,
    query: str = "",
    summary_max_chars: int = 1200,
) -> str:
    if not project_root or not isinstance(agent_cfg, dict):
        return ""

    configured_max = agent_cfg.get("context_max_chars")
    if isinstance(configured_max, int) and configured_max > 0:
        max_chars = min(max_chars, configured_max)

    resolved_mode = str(mode or agent_cfg.get("context_mode") or "index").strip().lower()
    if resolved_mode not in {"full", "index"}:
        resolved_mode = "index"

    context_paths = normalize_paths(agent_cfg.get("context_path"))
    if not context_paths:
        context_paths = normalize_paths(agent_cfg.get("context_paths"))
    if not context_paths:
        return ""

    context_files = normalize_paths(agent_cfg.get("context_files"))

    chunks: list[str] = []
    used_chars = 0
    resolved_context_roots: list[str] = []
    collected_files: list[str] = []

    query_tokens = {
        token.strip().lower()
        for token in re.split(r"[^a-zA-Z0-9_]+", str(query or ""))
        if token.strip()
    }

    def _file_score(path: str) -> tuple[int, int, str]:
        rel_name = os.path.relpath(path, project_root).lower()
        score = 0
        if query_tokens:
            score += sum(1 for token in query_tokens if token and token in rel_name) * 2
        docs_bias = 1 if "/docs/" in f"/{rel_name}" else 0
        return (-score, -docs_bias, rel_name)

    for raw_context_path in context_paths:
        context_root = resolve_path(project_root, raw_context_path)
        if not os.path.isdir(context_root):
            continue
        resolved_context_roots.append(context_root)

        for file_path in collect_context_candidate_files(
            context_root,
            seed_files=context_files,
            search_root=project_root,
        ):
            if file_path not in collected_files:
                collected_files.append(file_path)

    if resolved_mode == "index":
        lines = ["Context index (retrieval mode):"]
        for file_path in sorted(collected_files, key=_file_score):
            try:
                with open(file_path, encoding="utf-8") as handle:
                    content = handle.read().strip()
            except Exception:
                continue

            if not content:
                continue

            rel_name = os.path.relpath(file_path, project_root)
            heading = ""
            for line in content.splitlines():
                candidate = line.strip().lstrip("#").strip()
                if candidate:
                    heading = candidate
                    break
            if not heading:
                heading = "(no heading)"
            heading = truncate_text(heading, max_chars=180, suffix="...")
            line = f"- {rel_name}: {heading}"
            remaining = max_chars - used_chars
            if remaining <= 0:
                break
            line = truncate_text(line, max_chars=remaining, suffix="...")
            lines.append(line)
            used_chars += len(line) + 1
            if used_chars >= max_chars:
                break
        if len(lines) <= 1:
            return ""
        text = "\n".join(lines)
        budget = apply_prompt_budget(
            text,
            max_chars=max_chars,
            summary_max_chars=summary_max_chars,
        )
        roots_display = ", ".join(
            os.path.relpath(path, project_root) for path in resolved_context_roots
        )
        return (
            "\n\nUse this project context index as source material (do not invent facts):\n"
            f"Context folders: {roots_display}\n"
            f"{budget['text']}"
        )

    for file_path in sorted(
        collected_files, key=lambda p: os.path.relpath(p, project_root).lower()
    ):
        try:
            with open(file_path, encoding="utf-8") as handle:
                content = handle.read().strip()
        except Exception:
            continue

        if not content:
            continue

        remaining = max_chars - used_chars
        if remaining <= 0:
            break

        budget = apply_prompt_budget(
            content,
            max_chars=remaining,
            summary_max_chars=summary_max_chars,
        )
        excerpt = str(budget["text"])
        rel_name = os.path.relpath(file_path, project_root)
        block = f"[{rel_name}]\n{excerpt}"
        chunks.append(block)
        used_chars += len(block)
        if used_chars >= max_chars:
            break

    if not chunks:
        return ""

    roots_display = ", ".join(
        os.path.relpath(path, project_root) for path in resolved_context_roots
    )

    return (
        "\n\nUse this project context as source material (do not invent facts):\n"
        f"Context folders: {roots_display}\n" + "\n\n".join(chunks)
    )
