from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Callable

from nexus.core.config import NEXUS_STORAGE_BACKEND
from nexus.core.runtime_mode import is_postgres_backend
from nexus.core.utils.log_utils import log_unauthorized_access


def _format_issue_content(title: str, body: str, issue_num: Any) -> str:
    if title and body:
        return f"# {title}\n\n{body}"
    if title:
        return f"# {title}"
    return body or f"Issue #{issue_num}"


def _is_unauthorized(ctx: Any, deps: Any) -> bool:
    return bool(deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids)


async def _ensure_project_issue_for_command(
    ctx: Any, deps: Any, command: str
) -> tuple[Any, Any, Any]:
    if not ctx.args:
        await deps.prompt_project_selection(ctx, command)
        return None, None, None
    return await deps.ensure_project_issue(ctx, command)


def _extract_task_file_from_issue_body(body: Any) -> str | None:
    match = re.search(r"Task File:\s*`([^`]+)`", str(body or ""))
    return match.group(1) if match else None


def _get_project_fallback_config(project_key: str, deps: Any) -> dict[str, Any] | None:
    cfg = deps.project_config.get(project_key)
    return cfg if isinstance(cfg, dict) else None


def _has_agents_config(cfg: dict[str, Any] | None) -> bool:
    return bool(isinstance(cfg, dict) and cfg.get("agents_dir"))


def _has_agents_workspace_config(cfg: dict[str, Any] | None) -> bool:
    return bool(isinstance(cfg, dict) and cfg.get("agents_dir") and cfg.get("workspace"))


def _load_issue_details(
    issue_num: Any,
    repo: str,
    deps: Any,
    *,
    requester_nexus_id: str | None = None,
) -> dict[str, Any] | None:
    kwargs = {"repo": repo}
    if requester_nexus_id:
        kwargs["requester_nexus_id"] = requester_nexus_id
    try:
        data = deps.get_issue_details(issue_num, **kwargs)
    except TypeError:
        data = deps.get_issue_details(issue_num, repo=repo)
    return data if isinstance(data, dict) else None


def _resolve_task_file_and_prefetched_issue(
    project_key: str,
    issue_num: Any,
    deps: Any,
    *,
    requester_nexus_id: str | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    task_file = (
        None
        if is_postgres_backend(NEXUS_STORAGE_BACKEND)
        else deps.find_task_file_by_issue(issue_num)
    )
    if task_file:
        return task_file, None

    repo = deps.project_repo(project_key)
    details = _load_issue_details(
        issue_num,
        repo,
        deps,
        requester_nexus_id=requester_nexus_id,
    )
    if not details:
        return None, None
    if is_postgres_backend(NEXUS_STORAGE_BACKEND):
        return None, details
    return _extract_task_file_from_issue_body(details.get("body", "")), details


async def _resolve_reprocess_source(
    ctx: Any,
    deps: Any,
    *,
    project_key: str,
    issue_num: Any,
    requester_nexus_id: str | None = None,
) -> tuple[str | None, dict[str, Any] | None, str, dict[str, Any] | None]:
    task_file, details = _resolve_task_file_and_prefetched_issue(
        project_key,
        issue_num,
        deps,
        requester_nexus_id=requester_nexus_id,
    )

    if (not is_postgres_backend(NEXUS_STORAGE_BACKEND)) and task_file and os.path.exists(task_file):
        project_name, config = deps.resolve_project_config_from_task(task_file)
        if not _has_agents_config(config):
            fallback_config = _get_project_fallback_config(project_key, deps)
            if _has_agents_config(fallback_config):
                config = fallback_config
                project_name = project_key
        if not _has_agents_config(config):
            await ctx.reply_text(f"❌ No agents config for project '{project_name or 'unknown'}'.")
            return None, None, "", None
        content = await asyncio.to_thread(_read_text_file, task_file)
        return project_name, config, content, details

    return await _resolve_reprocess_issue_fallback_source(
        ctx,
        deps,
        project_key=project_key,
        issue_num=issue_num,
        details=details,
        requester_nexus_id=requester_nexus_id,
    )


async def _resolve_reprocess_issue_fallback_source(
    ctx: Any,
    deps: Any,
    *,
    project_key: str,
    issue_num: Any,
    details: dict[str, Any] | None,
    requester_nexus_id: str | None = None,
) -> tuple[str | None, dict[str, Any] | None, str, dict[str, Any] | None]:
    fallback_config = _get_project_fallback_config(project_key, deps)
    if not fallback_config:
        await ctx.reply_text(
            f"❌ Task file not found for issue #{issue_num} and no project config fallback."
        )
        return None, None, "", None
    if not _has_agents_workspace_config(fallback_config):
        await ctx.reply_text(
            f"❌ Project config for '{project_key}' is missing agents_dir/workspace."
        )
        return None, None, "", None
    if not details:
        repo = deps.project_repo(project_key)
        details = _load_issue_details(
            issue_num,
            repo,
            deps,
            requester_nexus_id=requester_nexus_id,
        )
    if not details:
        await ctx.reply_text(f"❌ Could not load issue #{issue_num}.")
        return None, None, "", None
    title = str(details.get("title") or "").strip()
    body = str(details.get("body") or "").strip()
    return project_key, fallback_config, _format_issue_content(title, body, issue_num), details


async def _ensure_open_issue_details(
    ctx: Any,
    deps: Any,
    *,
    issue_num: Any,
    repo: str,
    existing: dict[str, Any] | None,
    requester_nexus_id: str | None = None,
) -> dict[str, Any] | None:
    details = existing or _load_issue_details(
        issue_num,
        repo,
        deps,
        requester_nexus_id=requester_nexus_id,
    )
    if not details:
        await ctx.reply_text(f"❌ Could not load issue #{issue_num}.")
        return None
    if details.get("state") == "closed":
        await ctx.reply_text(
            f"⚠️ Issue #{issue_num} is closed. Reprocess only applies to open issues."
        )
        return None
    return details


async def _resolve_reprocess_tier(
    ctx: Any,
    deps: Any,
    *,
    issue_num: Any,
    project_name: str,
    project_key: str,
) -> str | None:
    tracker_tier = deps.get_last_tier_for_issue(issue_num)
    label_tier = deps.get_sop_tier_from_issue(issue_num, project_name or project_key)
    tier_name = label_tier or tracker_tier
    if tier_name:
        return tier_name
    await ctx.reply_text(
        f"⚠️ Cannot determine workflow tier for issue #{issue_num}.\nAdd a `workflow:` label (e.g. `workflow:full`) to the issue and retry."
    )
    return None


def _infer_reprocess_task_type(details: dict[str, Any] | None) -> str:
    labels = details.get("labels") if isinstance(details, dict) else None
    if isinstance(labels, list):
        normalized = {str(label or "").strip().lower() for label in labels}
        if "hotfix" in normalized:
            return "hotfix"
        if "bug" in normalized or "type:bug" in normalized:
            return "bug"
    return "feature"


async def _prepare_reprocess_workflow_instance(
    ctx: Any,
    deps: Any,
    *,
    issue_num: Any,
    project_name: str,
    tier_name: str,
    details: dict[str, Any] | None,
) -> bool:
    workflow_plugin = deps.get_workflow_state_plugin(
        **deps.workflow_state_plugin_kwargs, cache_key="workflow:state-engine"
    )
    if not workflow_plugin:
        return True

    issue_title = str((details or {}).get("title") or f"issue-{issue_num}").strip() or (
        f"issue-{issue_num}"
    )
    task_type = _infer_reprocess_task_type(details)

    try:
        create_result = workflow_plugin.create_workflow_for_issue(
            issue_number=str(issue_num),
            issue_title=issue_title,
            project_name=str(project_name),
            tier_name=str(tier_name),
            task_type=str(task_type),
            description=f"Reprocess workflow for issue #{issue_num}",
            force_new_instance=True,
        )
        workflow_id = await create_result if asyncio.iscoroutine(create_result) else create_result
    except TypeError:
        create_result = workflow_plugin.create_workflow_for_issue(
            issue_number=str(issue_num),
            issue_title=issue_title,
            project_name=str(project_name),
            tier_name=str(tier_name),
            task_type=str(task_type),
            description=f"Reprocess workflow for issue #{issue_num}",
        )
        workflow_id = await create_result if asyncio.iscoroutine(create_result) else create_result
    except Exception as exc:
        deps.logger.error(
            "Failed creating fresh workflow instance for reprocess issue #%s: %s",
            issue_num,
            exc,
            exc_info=True,
        )
        await ctx.reply_text(f"❌ Failed to reset workflow for issue #{issue_num}: {exc}")
        return False

    if not workflow_id:
        await ctx.reply_text(f"❌ Could not create workflow instance for issue #{issue_num}.")
        return False

    try:
        start_result = workflow_plugin.start_workflow(str(workflow_id))
        started = await start_result if asyncio.iscoroutine(start_result) else start_result
    except Exception as exc:
        deps.logger.error(
            "Failed starting fresh workflow instance %s for reprocess issue #%s: %s",
            workflow_id,
            issue_num,
            exc,
            exc_info=True,
        )
        await ctx.reply_text(f"❌ Failed to start workflow instance for issue #{issue_num}: {exc}")
        return False
    if not started:
        await ctx.reply_text(f"❌ Could not start workflow instance for issue #{issue_num}.")
        return False

    deps.logger.info(
        "Reprocess issue #%s will use fresh workflow instance %s",
        issue_num,
        workflow_id,
    )
    return True


async def _launch_reprocess(
    ctx: Any,
    deps: Any,
    *,
    issue_num: Any,
    config: dict[str, Any],
    issue_url: str,
    tier_name: str,
    content: str,
    project_name: str,
    project_key: str,
    requester_nexus_id: str | None = None,
) -> None:
    msg_id = await ctx.reply_text(f"🔁 Reprocessing issue #{issue_num}...")
    pid, tool_used = deps.invoke_ai_agent(
        agents_dir=os.path.join(deps.base_dir, config["agents_dir"]),
        workspace_dir=os.path.join(deps.base_dir, config["workspace"]),
        issue_url=issue_url,
        tier_name=tier_name,
        task_content=content,
        log_subdir=project_name or project_key,
        project_name=project_name or project_key,
        requester_nexus_id=requester_nexus_id,
    )
    if pid:
        await ctx.edit_message_text(
            message_id=msg_id,
            text=f"✅ Reprocess started for issue #{issue_num}. Agent PID: {pid} (Tool: {tool_used})\n\n🔗 {issue_url}",
        )
        return
    await ctx.edit_message_text(
        message_id=msg_id, text=f"❌ Failed to launch reprocess for issue #{issue_num}."
    )


async def handle_reprocess(
    ctx: Any, deps: Any, *, build_issue_url: Callable[..., str], resolve_repo: Callable[..., str]
) -> None:
    deps.logger.info(f"Reprocess requested by user: {ctx.user_id}")
    if _is_unauthorized(ctx, deps):
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    requester_nexus_id = None
    if callable(getattr(deps, "requester_context_builder", None)) and str(ctx.user_id).isdigit():
        try:
            requester_context = deps.requester_context_builder(int(str(ctx.user_id)))
            requester_nexus_id = str(requester_context.get("nexus_id") or "").strip() or None
        except Exception:
            requester_nexus_id = None
    project_key, issue_num, _ = await _ensure_project_issue_for_command(ctx, deps, "reprocess")
    if not project_key:
        return

    project_name, config, content, details = await _resolve_reprocess_source(
        ctx,
        deps,
        project_key=project_key,
        issue_num=issue_num,
        requester_nexus_id=requester_nexus_id,
    )
    if not project_name or not config:
        return

    repo = resolve_repo(config, deps.default_repo)
    details = await _ensure_open_issue_details(
        ctx,
        deps,
        issue_num=issue_num,
        repo=repo,
        existing=details,
        requester_nexus_id=requester_nexus_id,
    )
    if not details:
        return

    tier_name = await _resolve_reprocess_tier(
        ctx, deps, issue_num=issue_num, project_name=project_name, project_key=project_key
    )
    if not tier_name:
        return

    if not await _prepare_reprocess_workflow_instance(
        ctx,
        deps,
        issue_num=issue_num,
        project_name=project_name or project_key,
        tier_name=tier_name,
        details=details,
    ):
        return

    issue_url = build_issue_url(repo, issue_num, config)
    await _launch_reprocess(
        ctx,
        deps,
        issue_num=issue_num,
        config=config,
        issue_url=issue_url,
        tier_name=tier_name,
        content=content,
        project_name=project_name,
        project_key=project_key,
        requester_nexus_id=requester_nexus_id,
    )


def _read_text_file(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _prepare_continue_context(
    issue_num: Any,
    project_key: str,
    rest: Any,
    deps: Any,
    *,
    requester_nexus_id: str | None = None,
) -> dict[str, Any]:
    return deps.prepare_continue_context(
        issue_num=issue_num,
        project_key=project_key,
        rest_tokens=rest or [],
        base_dir=deps.base_dir,
        project_config=deps.project_config,
        default_repo=deps.default_repo,
        find_task_file_by_issue=deps.find_task_file_by_issue,
        get_issue_details=deps.get_issue_details,
        resolve_project_config_from_task=deps.resolve_project_config_from_task,
        get_runtime_ops_plugin=deps.get_runtime_ops_plugin,
        scan_for_completions=deps.scan_for_completions,
        normalize_agent_reference=deps.normalize_agent_reference,
        get_expected_running_agent_from_workflow=deps.get_expected_running_agent_from_workflow,
        get_sop_tier_from_issue=deps.get_sop_tier_from_issue,
        get_sop_tier=deps.get_sop_tier,
        requester_nexus_id=requester_nexus_id,
    )


async def _handle_continue_status_outcome(
    ctx: Any,
    deps: Any,
    *,
    issue_num: Any,
    continue_ctx: dict[str, Any],
    finalize_workflow: Callable[..., Any],
) -> bool:
    status = str(continue_ctx.get("status") or "")
    if status in {"error", "already_running", "mismatch", "workflow_done_closed"}:
        await ctx.reply_text(continue_ctx["message"])
        return True

    if status == "workflow_done_open":
        msg_id = await ctx.reply_text(
            f"✅ Workflow complete for issue #{issue_num} (last agent: `{continue_ctx['resumed_from']}`)\n"
            f"Issue is still open — running finalization now..."
        )
        try:
            finalize_workflow(
                issue_num,
                continue_ctx["repo"],
                continue_ctx["resumed_from"],
                continue_ctx["project_name"],
            )
            await ctx.edit_message_text(
                message_id=msg_id,
                text=(
                    f"✅ Workflow complete for issue #{issue_num}\n"
                    f"Last agent: `{continue_ctx['resumed_from']}`\n"
                    "Issue finalized (PR handled; issue closes after merged PR/MR webhook)."
                ),
            )
        except Exception as exc:
            deps.logger.error(f"Finalization failed for issue #{issue_num}: {exc}", exc_info=True)
            await ctx.edit_message_text(
                message_id=msg_id, text=f"⚠️ Finalization error for issue #{issue_num}: {exc}"
            )
        return True

    if status != "ready":
        await ctx.reply_text(f"⚠️ Unexpected continue state: {continue_ctx['status']}")
        return True

    return False


async def _maybe_reset_continue_workflow_position(
    ctx: Any, deps: Any, *, issue_num: Any, continue_ctx: dict[str, Any]
) -> bool:
    from nexus.core.workflow_runtime.workflow_signal_sync import normalize_agent_reference

    workflow_plugin = deps.get_workflow_state_plugin(
        **deps.workflow_state_plugin_kwargs, cache_key="workflow:state-engine"
    )
    workflow_state = ""
    if workflow_plugin and hasattr(workflow_plugin, "get_workflow_status"):
        try:
            status_payload = workflow_plugin.get_workflow_status(str(issue_num))
            if asyncio.iscoroutine(status_payload):
                status_payload = await status_payload
            if isinstance(status_payload, dict):
                workflow_state = str(status_payload.get("state") or "").strip().lower()
        except Exception as exc:
            deps.logger.debug(
                "Failed to read workflow status for issue #%s before continue reset: %s",
                issue_num,
                exc,
            )

    workflow_failed = workflow_state == "failed"
    should_reset = bool(
        continue_ctx.get("forced_agent_override")
        or continue_ctx.get("sync_workflow_to_agent")
        or workflow_failed
    )
    if not should_reset:
        return True

    raw_agent_type = str(continue_ctx.get("agent_type") or "").strip()
    agent_type = normalize_agent_reference(raw_agent_type).strip().lower() if raw_agent_type else ""
    if not agent_type:
        await ctx.reply_text(f"❌ Missing target agent for workflow reset on issue #{issue_num}.")
        if raw_agent_type:
            deps.logger.warning(
                "Continue issue #%s: refusing to reset workflow to invalid agent reference %r",
                issue_num,
                raw_agent_type,
            )
        return False

    reset_ok = False
    if workflow_plugin:
        try:
            reset_ok = await workflow_plugin.reset_to_agent_for_issue(issue_num, agent_type)
        except Exception as exc:
            deps.logger.error(
                "Failed to reset workflow state for issue #%s to %s: %s",
                issue_num,
                agent_type,
                exc,
                exc_info=True,
            )
    if reset_ok:
        return True

    await ctx.reply_text(f"❌ Could not reset workflow to `{agent_type}` for issue #{issue_num}.")
    return False


async def _launch_continue_agent(
    ctx: Any, deps: Any, *, issue_num: Any, continue_ctx: dict[str, Any]
) -> None:
    async def _finalize_progress_message(message_id: Any, text: str) -> None:
        try:
            await ctx.edit_message_text(
                message_id=message_id,
                text=text,
                parse_mode=None,
            )
            return
        except Exception as exc:
            deps.logger.warning(
                "Failed to edit continue progress message for issue #%s (message_id=%s): %s",
                issue_num,
                message_id,
                exc,
            )

        # Fallback for Telegram edge-cases: remove transient message and send final output.
        try:
            tg_ctx = getattr(ctx, "telegram_context", None)
            bot = getattr(tg_ctx, "bot", None)
            chat_id = getattr(ctx, "chat_id", None)
            msg_id = int(str(message_id)) if str(message_id).isdigit() else message_id
            if bot and chat_id and msg_id:
                await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception as exc:
            deps.logger.debug(
                "Continue progress fallback delete failed for issue #%s: %s",
                issue_num,
                exc,
            )

        await ctx.reply_text(text, parse_mode=None)

    resume_info = f" (after {continue_ctx['resumed_from']})" if continue_ctx["resumed_from"] else ""
    msg_id = await ctx.reply_text(
        f"⏩ Continuing issue #{issue_num} with `{continue_ctx['agent_type']}`{resume_info}..."
    )
    try:
        pid, tool_used = deps.invoke_ai_agent(
            agents_dir=continue_ctx["agents_abs"],
            workspace_dir=continue_ctx["workspace_abs"],
            issue_url=continue_ctx["issue_url"],
            tier_name=continue_ctx["tier_name"],
            task_content=continue_ctx["content"],
            continuation=True,
            continuation_prompt=continue_ctx["continuation_prompt"],
            log_subdir=continue_ctx["log_subdir"],
            agent_type=continue_ctx["agent_type"],
            project_name=continue_ctx["log_subdir"],
            requester_nexus_id=str(continue_ctx.get("requester_nexus_id") or "").strip() or None,
        )
    except Exception as exc:
        deps.logger.error(
            "Failed to continue issue #%s with %s: %s",
            issue_num,
            continue_ctx.get("agent_type"),
            exc,
            exc_info=True,
        )
        await _finalize_progress_message(
            msg_id,
            f"❌ Failed to continue agent for issue #{issue_num}: {exc}",
        )
        return
    if pid:
        await _finalize_progress_message(
            msg_id,
            (
                f"✅ Agent continued for issue #{issue_num}. PID: {pid} (Tool: {tool_used})\n\n"
                f"Prompt: {continue_ctx['continuation_prompt']}\n\n"
                "ℹ️ **Note:** The agent will first check if the workflow has already progressed.\n"
                "If another agent is already handling the next step, this agent will exit gracefully.\n"
                "Use /continue only when an agent is truly stuck mid-step.\n\n"
                f"🔗 {continue_ctx['issue_url']}"
            ),
        )
        return
    await _finalize_progress_message(
        msg_id,
        f"❌ Failed to continue agent for issue #{issue_num}.",
    )


async def handle_continue(ctx: Any, deps: Any, *, finalize_workflow: Callable[..., Any]) -> None:
    deps.logger.info(f"Continue requested by user: {ctx.user_id}")
    if _is_unauthorized(ctx, deps):
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return
    requester_nexus_id = None
    if callable(getattr(deps, "requester_context_builder", None)) and str(ctx.user_id).isdigit():
        try:
            requester_context = deps.requester_context_builder(int(str(ctx.user_id)))
            requester_nexus_id = str(requester_context.get("nexus_id") or "").strip() or None
        except Exception:
            requester_nexus_id = None
    project_key, issue_num, rest = await _ensure_project_issue_for_command(ctx, deps, "continue")
    if not project_key:
        return

    continue_ctx = _prepare_continue_context(
        issue_num,
        project_key,
        rest,
        deps,
        requester_nexus_id=requester_nexus_id,
    )
    should_try_reconcile = str(
        continue_ctx.get("status") or ""
    ) == "ready" and not continue_ctx.get("forced_agent_override")
    if should_try_reconcile:
        repo = deps.project_repo(project_key)
        is_reset_like_fallback = (
            str(continue_ctx.get("agent_type") or "").strip().lower() == "triage"
            and not str(continue_ctx.get("resumed_from") or "").strip()
        )
        if is_reset_like_fallback:
            deps.logger.info(
                "Continue issue #%s: detected reset-like triage fallback; trying remote reconciliation",
                issue_num,
            )
        else:
            deps.logger.info(
                "Continue issue #%s: trying remote reconciliation before launch",
                issue_num,
            )
        try:
            reconcile_result = await deps.reconcile_issue_from_signals(
                issue_num=str(issue_num),
                project_key=project_key,
                repo=repo,
                get_issue_plugin=deps.get_direct_issue_plugin,
                extract_structured_completion_signals=deps.extract_structured_completion_signals,
                workflow_state_plugin_kwargs=deps.workflow_state_plugin_kwargs,
                write_local_completion_from_signal=deps.write_local_completion_from_signal,
            )
            applied = int((reconcile_result or {}).get("signals_applied") or 0)
            seeded = bool((reconcile_result or {}).get("completion_seeded"))
            if bool((reconcile_result or {}).get("ok")) and (applied > 0 or seeded):
                if applied > 0:
                    await ctx.reply_text(
                        f"🔄 Reconciled issue #{issue_num} from remote signals ({applied} step(s)); "
                        "resuming from recovered workflow state..."
                    )
                else:
                    await ctx.reply_text(
                        f"🔄 Reconciled issue #{issue_num} from remote signals (seeded latest handoff); "
                        "resuming from recovered workflow state..."
                    )
                continue_ctx = _prepare_continue_context(
                    issue_num,
                    project_key,
                    rest,
                    deps,
                    requester_nexus_id=requester_nexus_id,
                )
        except Exception as exc:
            deps.logger.warning(
                "Continue issue #%s: reconcile-before-continue failed: %s",
                issue_num,
                exc,
            )

    if await _handle_continue_status_outcome(
        ctx,
        deps,
        issue_num=issue_num,
        continue_ctx=continue_ctx,
        finalize_workflow=finalize_workflow,
    ):
        return
    if not await _maybe_reset_continue_workflow_position(
        ctx, deps, issue_num=issue_num, continue_ctx=continue_ctx
    ):
        return
    continue_ctx["requester_nexus_id"] = requester_nexus_id
    await _launch_continue_agent(ctx, deps, issue_num=issue_num, continue_ctx=continue_ctx)
