def get_direct_issue_plugin(*, repo: str, get_profiled_plugin):
    """Resolve issue plugin with backward-compatible profile fallback."""
    overrides = {"repo": repo}
    cache_key = f"git:direct:{repo}"
    try:
        return get_profiled_plugin(
            "git_telegram",
            overrides=overrides,
            cache_key=cache_key,
        )
    except Exception:
        return get_profiled_plugin(
            "git_agent_launcher",
            overrides=overrides,
            cache_key=cache_key,
        )
