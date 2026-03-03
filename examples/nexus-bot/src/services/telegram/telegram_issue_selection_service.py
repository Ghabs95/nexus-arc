def list_project_issues(
    *,
    project_key: str,
    project_config: dict,
    get_repos,
    get_direct_issue_plugin,
    logger,
    state: str = "open",
    limit: int = 10,
) -> list[dict]:
    config = project_config.get(project_key, {})
    if not isinstance(config, dict):
        return []

    repo_candidates: list[str] = []

    def _add_repo(value: object) -> None:
        repo_value = str(value or "").strip()
        if repo_value and repo_value not in repo_candidates:
            repo_candidates.append(repo_value)

    _add_repo(config.get("git_repo"))
    repo_list = config.get("git_repos")
    if isinstance(repo_list, list):
        for repo_name in repo_list:
            _add_repo(repo_name)
    for repo_name in get_repos(project_key):
        _add_repo(repo_name)

    if not repo_candidates:
        return []

    merged: list[dict] = []
    multi_repo = len(repo_candidates) > 1
    for repo in repo_candidates:
        try:
            plugin = get_direct_issue_plugin(repo)
            if not plugin:
                continue
            rows = plugin.list_issues(state=state, limit=limit, fields=["number", "title", "state"])
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                item = {
                    "number": row.get("number"),
                    "title": row.get("title"),
                    "state": row.get("state"),
                }
                if multi_repo:
                    repo_suffix = repo.split("/")[-1] if "/" in repo else repo
                    title = str(item.get("title") or "").strip()
                    item["title"] = f"[{repo_suffix}] {title}" if title else f"[{repo_suffix}]"
                merged.append(item)
        except Exception as exc:
            logger.error(
                "Failed to list %s issues for %s via %s: %s", state, project_key, repo, exc
            )

    deduped: list[dict] = []
    seen_numbers: set[str] = set()
    for item in merged:
        num = str(item.get("number") or "").strip()
        if not num or num in seen_numbers:
            continue
        seen_numbers.add(num)
        deduped.append(item)
        if len(deduped) >= max(1, int(limit)):
            break
    return deduped


def parse_project_issue_args(
    *, args: list[str], normalize_project_key
) -> tuple[str | None, str | None, list[str]]:
    sanitized_args: list[str] = []
    for token in args:
        value = str(token or "").strip()
        if not value:
            continue
        if all(ch in {"=", ">", "-", "→"} for ch in value):
            continue
        sanitized_args.append(value)
    if len(sanitized_args) < 2:
        return None, None, []
    project_key = normalize_project_key(sanitized_args[0])
    issue_num = sanitized_args[1].lstrip("#")
    return project_key, issue_num, sanitized_args[2:]


def issue_state_for_command(command: str, *, allow_any: bool = False) -> str:
    command_name = str(command or "").strip().lower()
    if command_name in {"logs", "logsfull", "tail"}:
        return "closed"
    if allow_any and command_name == "wfstate":
        return "any"
    return "open"


async def ensure_project_issue(
    *,
    update,
    context,
    command: str,
    iter_project_keys,
    normalize_project_key,
    parse_project_issue_args_fn,
    prompt_project_selection,
    prompt_issue_selection,
) -> tuple[str | None, str | None, list[str]]:
    project_keys = iter_project_keys()
    single_project = project_keys[0] if len(project_keys) == 1 else None

    sanitized_args: list[str] = []
    for token in list(context.args or []):
        value = str(token or "").strip()
        if not value:
            continue
        if all(ch in {"=", ">", "-", "→"} for ch in value):
            continue
        sanitized_args.append(value)

    project_key, issue_num, rest = parse_project_issue_args_fn(sanitized_args)
    default_issue_state = issue_state_for_command(command)
    if not project_key or not issue_num:
        if len(sanitized_args) == 1:
            arg = sanitized_args[0]
            maybe_issue = arg.lstrip("#")
            if maybe_issue.isdigit():
                if single_project:
                    return single_project, maybe_issue, []
                context.user_data["pending_issue"] = maybe_issue
                await prompt_project_selection(update, context, command)
            else:
                normalized = normalize_project_key(arg)
                if normalized and normalized in project_keys:
                    context.user_data["pending_command"] = command
                    context.user_data["pending_project"] = normalized
                    await prompt_issue_selection(
                        update, context, command, normalized, issue_state=default_issue_state
                    )
                else:
                    await prompt_project_selection(update, context, command)
        else:
            if single_project:
                context.user_data["pending_command"] = command
                context.user_data["pending_project"] = single_project
                await prompt_issue_selection(
                    update, context, command, single_project, issue_state=default_issue_state
                )
                return None, None, []
            await prompt_project_selection(update, context, command)
        return None, None, []
    if project_key not in project_keys:
        await update.effective_message.reply_text(f"❌ Unknown project '{project_key}'.")
        return None, None, []
    if not issue_num.isdigit():
        await update.effective_message.reply_text("❌ Invalid issue number.")
        return None, None, []
    return project_key, issue_num, rest


async def handle_pending_issue_input(
    *,
    update,
    context,
    is_feature_ideation_request,
    dispatch_command,
) -> bool:
    pending_command = context.user_data.get("pending_command")
    pending_project = context.user_data.get("pending_project")
    pending_issue = context.user_data.get("pending_issue")
    if not pending_command or not pending_project:
        return False

    text = (update.message.text or "").strip()
    if pending_issue is None:
        if is_feature_ideation_request(text) or (len(text) > 15 and " " in text):
            return False
        issue_num = text.lstrip("#")
        if not issue_num.isdigit():
            await update.effective_message.reply_text(
                "Please enter a valid issue number (e.g., 1)."
            )
            return True
        context.user_data["pending_issue"] = issue_num
        if pending_command == "respond":
            await update.effective_message.reply_text(
                "Now send the response message for this issue."
            )
            return True
    else:
        issue_num = pending_issue

    rest: list[str] = [text] if pending_command == "respond" else []
    context.user_data.pop("pending_command", None)
    context.user_data.pop("pending_project", None)
    context.user_data.pop("pending_issue", None)
    await dispatch_command(update, context, pending_command, pending_project, issue_num, rest)
    return True
