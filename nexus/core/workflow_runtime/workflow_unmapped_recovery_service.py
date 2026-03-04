from __future__ import annotations

import os
import re
import time
from typing import Any, Callable


def recover_unmapped_issues_from_completions(
    *,
    max_relaunches: int,
    logger: Any,
    runtime: Any,
    completion_store: Any,
    host_state_manager: Any,
    get_workflow_id: Callable[[str], str | None],
    normalize_agent_reference: Callable[[str], str],
    is_terminal_agent_reference: Callable[[str], bool],
    find_task_file_for_issue: Callable[[str], str | None],
    resolve_project_from_task_file: Callable[[str], str | None],
    get_default_project: Callable[[], str],
    project_config: dict[str, Any],
    resolve_repo_for_issue: Callable[[str, str | None], str],
    build_issue_url: Callable[[str, str, dict[str, Any]], str],
    get_sop_tier: Callable[..., tuple[str, str, str]],
    invoke_ai_agent: Callable[..., tuple[Any, Any]],
    base_dir: str,
    orphan_recovery_last_attempt: dict[str, float],
    orphan_recovery_cooldown_seconds: float,
) -> int:
    if runtime is None:
        return 0

    try:
        detected = completion_store.scan()
    except Exception as exc:
        logger.debug(f"Unmapped recovery skipped (completion scan failed): {exc}")
        return 0

    if not detected:
        return 0

    latest_by_issue: dict[str, object] = {}
    for item in detected:
        issue_num = str(getattr(item, "issue_number", "") or "").strip()
        if not issue_num:
            continue
        existing = latest_by_issue.get(issue_num)
        if existing is None:
            latest_by_issue[issue_num] = item
            continue
        try:
            if os.path.getmtime(getattr(item, "file_path", "")) > os.path.getmtime(
                getattr(existing, "file_path", "")
            ):
                latest_by_issue[issue_num] = item
        except Exception:
            continue

    launched = host_state_manager.load_launched_agents(recent_only=False)
    if not isinstance(launched, dict):
        launched = {}

    now = time.time()

    def _completion_mtime(issue_key: str) -> float:
        detection = latest_by_issue.get(issue_key)
        if detection is None:
            return 0.0
        try:
            return float(os.path.getmtime(getattr(detection, "file_path", "")))
        except Exception:
            return 0.0

    issue_order = sorted(latest_by_issue.keys(), key=_completion_mtime, reverse=True)
    relaunched = 0

    for issue_num in issue_order:
        if relaunched >= max_relaunches:
            break

        completion_mtime = _completion_mtime(issue_num)
        if completion_mtime > 0 and (now - completion_mtime) > (7 * 24 * 3600):
            continue

        workflow_id = get_workflow_id(issue_num)
        expected_running_agent = runtime.get_expected_running_agent(issue_num)
        workflow_state = runtime.get_workflow_state(issue_num)
        if workflow_state in {"PAUSED", "STOPPED", "COMPLETED", "FAILED", "CANCELLED"}:
            continue
        if workflow_id and expected_running_agent:
            continue

        last_attempt = orphan_recovery_last_attempt.get(issue_num, 0.0)
        if (now - last_attempt) < orphan_recovery_cooldown_seconds:
            continue
        if runtime.is_process_running(issue_num):
            continue

        tracker_entry = launched.get(issue_num, {})
        if not isinstance(tracker_entry, dict):
            tracker_entry = {}
        tracker_pid = tracker_entry.get("pid")
        if isinstance(tracker_pid, int) and tracker_pid > 0 and runtime.is_pid_alive(tracker_pid):
            continue

        detection = latest_by_issue[issue_num]
        summary = getattr(detection, "summary", None)
        raw_next_agent = str(getattr(summary, "next_agent", "") or "")
        next_agent = normalize_agent_reference(raw_next_agent) or raw_next_agent
        next_agent = str(next_agent or "").strip().lower().lstrip("@")
        if not next_agent or is_terminal_agent_reference(next_agent):
            continue

        task_file = find_task_file_for_issue(issue_num)
        project_name = None
        task_content = ""
        task_type = "feature"

        if task_file and os.path.exists(task_file):
            project_name = resolve_project_from_task_file(task_file)
            try:
                with open(task_file, encoding="utf-8") as handle:
                    task_content = handle.read()
            except Exception:
                task_content = ""
            type_match = re.search(r"\*\*Type:\*\*\s*(.+)", task_content)
            if type_match:
                task_type = type_match.group(1).strip().lower()
        else:
            raw_payload = getattr(summary, "raw", {})
            if isinstance(raw_payload, dict):
                guessed_project = raw_payload.get("_project")
                if isinstance(guessed_project, str) and guessed_project.strip():
                    project_name = guessed_project.strip()
                findings = raw_payload.get("key_findings")
                findings_text = ""
                if isinstance(findings, list) and findings:
                    cleaned = [str(item).strip() for item in findings if str(item).strip()]
                    if cleaned:
                        findings_text = "\n".join(f"- {item}" for item in cleaned[:5])
                summary_text = str(raw_payload.get("summary") or "").strip()
                if summary_text:
                    task_content = f"Recovered from completion summary:\n{summary_text}"
                    if findings_text:
                        task_content += f"\n\nKey findings:\n{findings_text}"

        project_name = project_name or get_default_project()
        project_cfg = project_config.get(project_name, {})
        if not isinstance(project_cfg, dict):
            continue

        agents_dir = project_cfg.get("agents_dir")
        workspace = project_cfg.get("workspace")
        if not agents_dir or not workspace:
            continue

        repo_name = resolve_repo_for_issue(issue_num, project_name)
        if runtime.is_issue_open(issue_num, repo_name) is not True:
            continue

        issue_url = build_issue_url(repo_name, issue_num, project_cfg)
        if not task_content:
            task_content = f"Issue #{issue_num}"

        tier_name = host_state_manager.get_last_tier_for_issue(issue_num)
        if not tier_name:
            tier_name, _, _ = get_sop_tier("issue", title=f"Issue #{issue_num}", body=task_content)

        orphan_recovery_last_attempt[issue_num] = now
        pid, tool_used = invoke_ai_agent(
            agents_dir=os.path.join(base_dir, str(agents_dir)),
            workspace_dir=os.path.join(base_dir, str(workspace)),
            issue_url=issue_url,
            tier_name=tier_name,
            task_content=task_content,
            continuation=True,
            continuation_prompt="Automatic recovery after restart: continue from last completion signal.",
            log_subdir=project_name,
            agent_type=next_agent,
            project_name=project_name,
        )

        if pid:
            relaunched += 1
            logger.warning(
                "Recovered issue #%s from completion signal: launching %s (PID %s, tool=%s)",
                issue_num,
                next_agent,
                pid,
                tool_used,
            )
        else:
            logger.info(
                "Completion-signal recovery skipped/failed for issue #%s (next_agent=%s)",
                issue_num,
                next_agent,
            )

    return relaunched
