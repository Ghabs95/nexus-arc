from __future__ import annotations

import os
import re
from typing import Any, cast

from nexus.core.chat_agents_schema import get_project_chat_agent_types
from nexus.core.handlers.agent_resolution_handler import resolve_agents_for_project
from nexus.core.utils.log_utils import log_unauthorized_access


async def handle_direct_request(
    ctx: Any,
    deps: Any,
    *,
    resolve_agent_type: Any,
    build_direct_chat_persona: Any,
) -> bool:
    deps.logger.info(f"Direct request by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return True

    if len(ctx.args) < 3:
        await ctx.reply_text(
            "⚠️ Usage: /direct <project> <@agent> <message>\n\n"
            "Example: /direct nexus @developer Add caching to API endpoints\n"
            "Optional: add `--new-chat` for strategic agents to start a fresh chat thread"
        )
        return True

    project = ctx.args[0].lower()
    agent = ctx.args[1].lstrip("@")
    message_tokens = [token for token in ctx.args[2:] if token != "--new-chat"]
    create_new_chat = "--new-chat" in ctx.args[2:]
    message = " ".join(message_tokens).strip()
    if not message:
        await ctx.reply_text(
            "⚠️ Please include a message after the agent.\n\n"
            "Example: /direct nexus @designer --new-chat Which strategy should we prioritize next quarter?"
        )
        return True
    if project not in deps.project_config:
        await ctx.reply_text(f"❌ Unknown project '{project}'")
        return True

    agents_subdir = cast(str, deps.project_config[project]["agents_dir"])
    agents_dir = os.path.join(str(deps.base_dir), agents_subdir)
    agents_map = resolve_agents_for_project(agents_dir, deps.nexus_dir_name)
    if agent not in agents_map:
        available = ", ".join([f"@{a}" for a in sorted(agents_map.keys())])
        await ctx.reply_text(f"❌ Unknown agent '@{agent}' for {project}\n\nAvailable: {available}")
        return True

    source_filename = agents_map.get(agent, "")
    project_cfg = deps.project_config.get(project) if isinstance(deps.project_config, dict) else {}
    project_chat_agent_types = get_project_chat_agent_types(
        project_cfg if isinstance(project_cfg, dict) else {}
    )
    agent_type = resolve_agent_type(
        agent,
        source_filename,
        agents_dir,
        deps.nexus_dir_name,
        available_agent_types=project_chat_agent_types,
    )

    if agent_type and agent_type in project_chat_agent_types:
        msg_id = await ctx.reply_text(f"🤖 Asking @{agent} directly...")
        try:
            user_id = int(ctx.user_id)
            if create_new_chat:
                deps.create_chat(
                    user_id,
                    title=f"Direct @{agent} ({project})",
                    metadata={"project_key": project, "primary_agent_type": agent_type},
                )
            deps.append_message(user_id, "user", message)
            history = deps.get_chat_history(user_id)
            persona = build_direct_chat_persona(deps.ai_persona, project, agent, agent_type)
            chat_result = deps.orchestrator.run_text_to_speech_analysis(
                text=message, task="chat", history=history, persona=persona, project_name=project
            )
            reply_text = chat_result.get("text", "I couldn't generate a response right now.")
            deps.append_message(user_id, "assistant", reply_text)
            await ctx.edit_message_text(
                message_id=msg_id,
                text=(
                    f"🤖 *{agent} ({agent_type})*: \n\n{reply_text}\n\n"
                    f"🧵 Chat thread: {'new' if create_new_chat else 'current'}\n"
                    "💬 Use /chat to manage conversation threads and context."
                ),
            )
            return True
        except Exception as exc:
            deps.logger.error(f"Error in direct chat request: {exc}")
            await ctx.edit_message_text(
                message_id=msg_id, text=f"❌ Error in direct chat reply: {exc}"
            )
            return True

    msg_id = await ctx.reply_text(f"🚀 Creating direct request for @{agent}...")

    try:
        title = f"Direct Request: {message[:50]}"
        body = f"""**Direct Request** to @{agent}

{message}

**Project:** {project}
**Assigned to:** @{agent}

---
*Created via /direct command - invoke {agent} immediately*"""

        repo = deps.get_repo(project)
        plugin = deps.get_direct_issue_plugin(repo)
        if not plugin:
            await ctx.edit_message_text(
                message_id=msg_id,
                text="❌ Failed to initialize Git issue plugin",
            )
            return True

        issue_url = plugin.create_issue(
            title=title,
            body=body,
            labels=["workflow:fast-track"],
        )
        if not issue_url:
            await ctx.edit_message_text(
                message_id=msg_id,
                text="❌ Failed to create issue\n\nIf this is a discussion, use /chat instead.",
            )
            return True

        match = re.search(r"/issues/(\d+)$", issue_url)
        if not match:
            await ctx.edit_message_text(
                message_id=msg_id,
                text="❌ Failed to get issue number",
            )
            return True

        issue_num = match.group(1)
        comment_body = f"🎯 Direct request from @Ghabs\n\nReady for `@{agent}`"
        plugin.add_comment(issue_num, comment_body)

        await ctx.edit_message_text(
            message_id=msg_id,
            text=(
                f"✅ Direct request created for @{agent} (Issue #{issue_num})\n\n"
                f"Message: {message}\n\n"
                f"The auto-chaining system will invoke @{agent} on the next cycle (~60s)\n\n"
                f"🔗 {issue_url}\n\n"
                "💬 For conversational strategy Q&A, use /chat."
            ),
        )
        return True
    except Exception as exc:
        deps.logger.error(f"Error in direct request: {exc}")
        await ctx.edit_message_text(
            message_id=msg_id,
            text=f"❌ Error: {exc}",
        )
        return True
