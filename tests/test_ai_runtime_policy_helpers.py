import io
import os

from nexus.plugins.builtin.ai_runtime.agent_invoke_service import (
    extract_issue_number,
    invoke_agent_with_fallback,
)
from nexus.plugins.builtin.ai_runtime.analysis_service import (
    build_analysis_prompt,
    parse_analysis_result,
    run_analysis_attempts,
    run_analysis_with_provider,
    strip_cli_tool_output,
)
from nexus.plugins.builtin.ai_runtime.fallback_policy import (
    fallback_order_from_preferences,
    resolve_analysis_tool_order,
)
from nexus.plugins.builtin.ai_runtime.provider_invokers.agent_invokers import (
    invoke_copilot_agent_cli,
    invoke_gemini_agent_cli,
)
from nexus.plugins.builtin.ai_runtime.provider_invokers.analysis_invokers import (
    run_copilot_analysis_cli,
    run_gemini_analysis_cli,
)
from nexus.plugins.builtin.ai_runtime.provider_invokers.codex_invoker import invoke_codex_cli
from nexus.plugins.builtin.ai_runtime.provider_invokers.subprocess_utils import (
    wrap_timeout_error,
)
from nexus.plugins.builtin.ai_runtime.provider_invokers.transcription_invokers import (
    transcribe_with_copilot_cli,
    transcribe_with_gemini_cli,
)
from nexus.plugins.builtin.ai_runtime.provider_invokers.whisper_invoker import (
    transcribe_with_local_whisper,
)
from nexus.plugins.builtin.ai_runtime.provider_registry import (
    parse_tool_preference,
    parse_provider,
    supports_analysis,
    unique_tools,
)
from nexus.plugins.builtin.ai_runtime.system_operation_policy import resolve_issue_override_agent
from nexus.plugins.builtin.ai_runtime.transcription_service import (
    is_non_transcription_artifact,
    is_transcription_refusal,
    normalize_local_whisper_model_name,
    run_transcription_attempts,
    resolve_transcription_attempts,
)
from nexus.plugins.builtin.ai_runtime_plugin import AIProvider


def _noop_rate_limit_with_retries(tool, exc: Exception, retries: int, context: str) -> None:
    return None


def _noop_rate_limit(tool, exc: Exception, context: str) -> None:
    return None


def test_fallback_order_from_preferences_dedupes_and_parses():
    prefs = {
        "triage": {"provider": "gemini", "profile": "small"},
        "designer": {"provider": "copilot", "profile": "large"},
        "writer": {"provider": "gemini", "profile": "small"},
        "x": {"provider": "unknown", "profile": "small"},
    }
    order = fallback_order_from_preferences(
        resolved_tool_preferences=prefs,
        parse_provider=lambda v: {
            "gemini": AIProvider.GEMINI,
            "copilot": AIProvider.COPILOT,
        }.get(v.get("provider")),
    )
    assert order == [AIProvider.GEMINI, AIProvider.COPILOT]


def test_agent_invoke_service_extract_issue_number():
    assert extract_issue_number("https://github.com/org/repo/issues/83") == "83"
    assert extract_issue_number("https://github.com/org/repo/pull/9") is None
    assert extract_issue_number(None) is None


def test_resolve_issue_override_agent_uses_issue_override_for_bug_text():
    result = resolve_issue_override_agent(
        task_key="refine_description",
        mapped_agent="designer",
        text="Bug: traceback and exception",
        system_operations={"overrides": {"issue": {"refine_description": "triage"}}},
        looks_like_bug_issue=lambda text: "bug" in text.lower(),
    )
    assert result == "triage"


def test_resolve_analysis_tool_order_prefers_mapped_agent_and_filters():
    order = resolve_analysis_tool_order(
        task="refine_description",
        text="feature request",
        project_name="nexus",
        fallback_enabled=True,
        system_operations={"default": "triage", "refine_description": "designer"},
        default_chat_agent_type="triage",
        resolve_issue_override_agent=lambda **kwargs: kwargs["mapped_agent"],
        get_primary_tool=lambda agent, project: (
            AIProvider.COPILOT if agent == "designer" else AIProvider.GEMINI
        ),
        fallback_order_from_preferences_fn=lambda project: [AIProvider.GEMINI, AIProvider.COPILOT],
        unique_tools=lambda items: list(dict.fromkeys(items)),
        supports_analysis=lambda tool: tool in {AIProvider.GEMINI, AIProvider.COPILOT},
        default_tools=[AIProvider.GEMINI, AIProvider.COPILOT],
    )
    assert order == [AIProvider.COPILOT, AIProvider.GEMINI]


def test_resolve_analysis_tool_order_chat_uses_default_chat_agent():
    order = resolve_analysis_tool_order(
        task="chat",
        text="hello",
        project_name="nexus",
        fallback_enabled=False,
        system_operations={"default": "triage"},
        default_chat_agent_type="designer",
        resolve_issue_override_agent=lambda **kwargs: kwargs["mapped_agent"],
        get_primary_tool=lambda agent, project: (
            AIProvider.COPILOT if agent == "designer" else AIProvider.GEMINI
        ),
        fallback_order_from_preferences_fn=lambda project: [AIProvider.GEMINI, AIProvider.COPILOT],
        unique_tools=lambda items: list(dict.fromkeys(items)),
        supports_analysis=lambda tool: True,
        default_tools=[AIProvider.GEMINI, AIProvider.COPILOT],
    )
    assert order == [AIProvider.COPILOT]


def test_resolve_analysis_tool_order_appends_builtin_fallbacks_after_preferences():
    order = resolve_analysis_tool_order(
        task="chat",
        text="hello",
        project_name="nexus",
        fallback_enabled=True,
        system_operations={"default": "triage"},
        default_chat_agent_type="designer",
        resolve_issue_override_agent=lambda **kwargs: kwargs["mapped_agent"],
        get_primary_tool=lambda agent, project: (
            AIProvider.COPILOT if agent == "designer" else AIProvider.GEMINI
        ),
        fallback_order_from_preferences_fn=lambda project: [AIProvider.GEMINI, AIProvider.COPILOT],
        unique_tools=lambda items: list(dict.fromkeys(items)),
        supports_analysis=lambda tool: tool != AIProvider.OLLAMA,
        default_tools=[
            AIProvider.GEMINI,
            AIProvider.COPILOT,
            AIProvider.CLAUDE,
            AIProvider.CODEX,
            AIProvider.OLLAMA,
        ],
    )
    assert order == [
        AIProvider.COPILOT,
        AIProvider.GEMINI,
        AIProvider.CLAUDE,
        AIProvider.CODEX,
    ]


def test_resolve_transcription_attempts_uses_mapped_agent_primary():
    attempts = resolve_transcription_attempts(
        project_name="nexus",
        system_operations={"transcribe_audio": "triage"},
        fallback_enabled=True,
        fallback_provider="gemini",
        get_primary_tool=lambda agent, project: AIProvider.COPILOT,
        fallback_order_from_preferences_fn=lambda project: [AIProvider.GEMINI, AIProvider.COPILOT],
        unique_tools=lambda items: list(dict.fromkeys(items)),
        supported_providers=[AIProvider.GEMINI, AIProvider.COPILOT],
    )
    assert attempts == ["copilot", "gemini"]


def test_resolve_transcription_attempts_falls_back_to_primary_modes():
    assert resolve_transcription_attempts(
        project_name=None,
        system_operations={},
        fallback_enabled=False,
        fallback_provider="whisper",
        get_primary_tool=lambda agent, project: AIProvider.GEMINI,
        fallback_order_from_preferences_fn=lambda project: [],
        unique_tools=lambda items: items,
        supported_providers=[AIProvider.GEMINI, AIProvider.COPILOT],
    ) == ["whisper"]


def test_transcription_filters_and_whisper_model_normalization():
    assert normalize_local_whisper_model_name("whisper-1") == "base"
    assert is_transcription_refusal("I cannot transcribe audio directly") is True
    assert is_non_transcription_artifact("file: note.ogg", "/tmp/note.ogg") is True
    assert is_non_transcription_artifact("hello world", "/tmp/note.ogg") is False


def test_provider_registry_helpers():
    assert (
        parse_provider({"provider": "gemini", "profile": "small"}, AIProvider) == AIProvider.GEMINI
    )
    assert (
        parse_provider({"provider": "copilot", "profile": "large"}, AIProvider)
        == AIProvider.COPILOT
    )
    assert parse_provider({"provider": "codex", "profile": "large"}, AIProvider) == AIProvider.CODEX
    assert parse_provider("unknown", AIProvider) is None
    assert parse_provider({"provider": "auto", "profile": "small"}, AIProvider) is None
    parsed = parse_tool_preference({"provider": "gemini", "profile": "small"}, AIProvider)
    assert parsed.valid is True
    assert parsed.provider == AIProvider.GEMINI
    assert parsed.profile == "small"
    assert (
        supports_analysis(
            AIProvider.GEMINI,
            supported_tools=[AIProvider.GEMINI, AIProvider.COPILOT],
        )
        is True
    )
    assert (
        supports_analysis(
            AIProvider.CODEX,
            supported_tools=[AIProvider.GEMINI, AIProvider.COPILOT],
        )
        is False
    )
    assert unique_tools([AIProvider.GEMINI, AIProvider.COPILOT, AIProvider.GEMINI]) == [
        AIProvider.GEMINI,
        AIProvider.COPILOT,
    ]


def test_run_analysis_with_provider_dispatch():
    result = run_analysis_with_provider(
        tool=AIProvider.GEMINI,
        providers_map={
            AIProvider.GEMINI: lambda text, task, **kwargs: {"tool": "gemini"},
            AIProvider.COPILOT: lambda text, task, **kwargs: {"tool": "copilot"},
        },
        text="x",
        task="classify",
        kwargs={},
        tool_unavailable_error=RuntimeError,
    )
    assert result == {"tool": "gemini"}


def test_run_analysis_attempts_falls_back_and_defaults():
    class _RateLimit(Exception):
        pass

    logs: list[tuple[str, tuple]] = []

    class _Logger:
        def info(self, msg, *args):
            logs.append(("info", (msg, *args)))

        def warning(self, msg, *args):
            logs.append(("warning", (msg, *args)))

        def error(self, msg, *args):
            logs.append(("error", (msg, *args)))

    attempts = [AIProvider.GEMINI, AIProvider.COPILOT]
    seen = {"calls": 0}

    def _invoke(tool, text, task, kwargs):
        seen["calls"] += 1
        if tool == AIProvider.GEMINI:
            raise _RateLimit("429")
        return {"ok": True, "tool": tool.value}

    out = run_analysis_attempts(
        tool_order=attempts,
        text="hello",
        task="classify",
        kwargs={},
        invoke_provider=_invoke,
        rate_limited_error_type=_RateLimit,
        record_rate_limit_with_context=lambda tool, exc, retry_count, context: logs.append(
            ("rate", (tool.value, context))
        ),
        get_default_analysis_result=lambda task, **kwargs: {"default": True},
        logger=_Logger(),
    )
    assert out == {"ok": True, "tool": "copilot"}
    assert ("rate", ("gemini", "analysis:classify")) in logs

    out_default = run_analysis_attempts(
        tool_order=[AIProvider.GEMINI],
        text="hello",
        task="chat",
        kwargs={},
        invoke_provider=lambda tool, text, task, kwargs: (_ for _ in ()).throw(Exception("fail")),
        rate_limited_error_type=_RateLimit,
        record_rate_limit_with_context=_noop_rate_limit_with_retries,
        get_default_analysis_result=lambda task, **kwargs: {"default": True},
        logger=_Logger(),
    )
    assert out_default == {"default": True}


def test_analysis_output_strip_and_parse_helpers():
    noisy = '● No-op\n$ true\n└ 1 line...\n\n{"project":"nexus"}\n'
    cleaned = strip_cli_tool_output(noisy)
    assert cleaned == '{"project":"nexus"}'

    class _Logger:
        def __init__(self):
            self.warned = False

        def warning(self, *args, **kwargs):
            self.warned = True

    logger = _Logger()
    assert parse_analysis_result(cleaned, "classify", logger=logger) == {"project": "nexus"}
    parsed_bad = parse_analysis_result("```json\n{oops}\n```", "classify", logger=logger)
    assert parsed_bad.get("parse_error") is True
    assert logger.warned is True


def test_build_analysis_prompt_helpers_cover_chat_and_classify():
    classify_prompt = build_analysis_prompt(
        "Implement login form",
        "classify",
        projects=["nexus", "site"],
        types=["feature", "bug"],
    )
    assert "Classify this task" in classify_prompt
    assert "nexus, site" in classify_prompt
    assert "feature, bug" in classify_prompt

    chat_prompt = build_analysis_prompt(
        "What should we do next?",
        "chat",
        history="U: hi\nA: hello",
        persona="You are triage.",
    )
    assert chat_prompt.startswith("You are triage.")
    assert "Recent Conversation History" in chat_prompt
    assert "What should we do next?" in chat_prompt


def test_detect_intent_prompt_disambiguates_single_integration_from_ideation():
    prompt = build_analysis_prompt(
        "Do you think we could integrate Ollama (https://ollama.com/) in Nexus ARC?",
        "detect_intent",
    )
    assert "single concrete change/integration feasibility questions" in prompt
    assert "can we integrate X?" in prompt
    assert 'prefer "feature_ideation": false' in prompt


def test_gemini_analysis_invoker_rate_limit_and_timeout(monkeypatch):
    import nexus.plugins.builtin.ai_runtime.provider_invokers.analysis_invokers as invokers_mod

    class _RateLimited(Exception):
        pass

    class _Result:
        returncode = 1
        stderr = "quota exceeded"
        stdout = ""

    monkeypatch.setattr(invokers_mod.subprocess, "run", lambda *args, **kwargs: _Result())
    try:
        run_gemini_analysis_cli(
            check_tool_available=lambda provider: True,
            gemini_provider=AIProvider.GEMINI,
            gemini_cli_path="gemini",
            gemini_model="gemini-2.0-flash",
            build_analysis_prompt=lambda text, task, **kwargs: "prompt",
            parse_analysis_result=lambda output, task: {"ok": True},
            tool_unavailable_error=RuntimeError,
            rate_limited_error=_RateLimited,
            text="x",
            task="classify",
            timeout=5,
            kwargs={},
        )
        assert False, "expected rate-limit error"
    except _RateLimited:
        pass

    def _raise_timeout(*args, **kwargs):
        raise invokers_mod.subprocess.TimeoutExpired(cmd="gemini", timeout=5)

    monkeypatch.setattr(invokers_mod.subprocess, "run", _raise_timeout)
    try:
        run_gemini_analysis_cli(
            check_tool_available=lambda provider: True,
            gemini_provider=AIProvider.GEMINI,
            gemini_cli_path="gemini",
            gemini_model="",
            build_analysis_prompt=lambda text, task, **kwargs: "prompt",
            parse_analysis_result=lambda output, task: {"ok": True},
            tool_unavailable_error=RuntimeError,
            rate_limited_error=_RateLimited,
            text="x",
            task="classify",
            timeout=5,
            kwargs={},
        )
        assert False, "expected timeout error"
    except Exception as exc:
        assert "timed out" in str(exc)


def test_copilot_analysis_invoker_success_and_unavailable(monkeypatch):
    import nexus.plugins.builtin.ai_runtime.provider_invokers.analysis_invokers as invokers_mod

    class _Result:
        returncode = 0
        stderr = ""
        stdout = '{"ok": true}'

    monkeypatch.setattr(invokers_mod.subprocess, "run", lambda *args, **kwargs: _Result())
    out = run_copilot_analysis_cli(
        check_tool_available=lambda provider: True,
        copilot_provider=AIProvider.COPILOT,
        copilot_cli_path="copilot",
        copilot_model="gpt-5-mini",
        copilot_supports_model=True,
        build_analysis_prompt=lambda text, task, **kwargs: "prompt",
        parse_analysis_result=lambda output, task: {"parsed": output, "task": task},
        tool_unavailable_error=RuntimeError,
        text="x",
        task="classify",
        timeout=7,
        kwargs={},
    )
    assert out == {"parsed": '{"ok": true}', "task": "classify"}

    try:
        run_copilot_analysis_cli(
            check_tool_available=lambda provider: False,
            copilot_provider=AIProvider.COPILOT,
            copilot_cli_path="copilot",
            copilot_model="",
            copilot_supports_model=False,
            build_analysis_prompt=lambda text, task, **kwargs: "prompt",
            parse_analysis_result=lambda output, task: {"ok": True},
            tool_unavailable_error=RuntimeError,
            text="x",
            task="classify",
            timeout=7,
            kwargs={},
        )
        assert False, "expected unavailable error"
    except RuntimeError as exc:
        assert "Copilot CLI not available" in str(exc)


def test_wrap_timeout_error_helper_message():
    import subprocess

    exc = subprocess.TimeoutExpired(cmd="tool", timeout=9)
    wrapped = wrap_timeout_error(exc, provider_name="Gemini", timeout=9)
    assert "Gemini analysis timed out (>9s)" in str(wrapped)


def test_codex_invoker_unavailable_and_success(monkeypatch, tmp_path):
    import nexus.plugins.builtin.ai_runtime.provider_invokers.codex_invoker as codex_mod

    class _Logger:
        def __init__(self):
            self.messages = []

        def info(self, msg, *args):
            self.messages.append(("info", msg % args if args else msg))

        def error(self, msg, *args):
            self.messages.append(("error", msg % args if args else msg))

    logger = _Logger()

    try:
        invoke_codex_cli(
            check_tool_available=lambda provider: False,
            codex_provider=AIProvider.CODEX,
            codex_cli_path="codex",
            codex_model="",
            get_tasks_logs_dir=lambda workspace, subdir: str(tmp_path),
            tool_unavailable_error=RuntimeError,
            logger=logger,
            agent_prompt="do work",
            workspace_dir="/tmp/work",
        )
        assert False, "expected unavailable error"
    except RuntimeError as exc:
        assert "Codex CLI not available" in str(exc)

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    try:
        invoke_codex_cli(
            check_tool_available=lambda provider: True,
            codex_provider=AIProvider.CODEX,
            codex_cli_path="codex",
            codex_model="",
            get_tasks_logs_dir=lambda workspace, subdir: str(tmp_path),
            tool_unavailable_error=RuntimeError,
            logger=logger,
            agent_prompt="do work",
            workspace_dir="/tmp/work",
        )
        assert False, "expected missing key error"
    except RuntimeError as exc:
        assert "OPENAI_API_KEY" in str(exc)

    monkeypatch.setattr(codex_mod.time, "strftime", lambda fmt: "20260101_120000")
    captured: dict[str, Any] = {}
    codex_home = tmp_path / ".codex"
    empty_rollout = codex_home / "sessions" / "2026" / "02" / "28" / "rollout-empty.jsonl"
    non_empty_rollout = codex_home / "sessions" / "2026" / "02" / "28" / "rollout-ok.jsonl"
    empty_rollout.parent.mkdir(parents=True, exist_ok=True)
    empty_rollout.write_text("")
    non_empty_rollout.write_text('{"ok":true}\n', encoding="utf-8")
    os.utime(empty_rollout, (946684800, 946684800))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    class _Proc:
        pid = 4321
        stdout = None

        def poll(self):
            return None

    def _fake_popen(cmd, cwd, stdin, stdout, stderr, env, text, bufsize):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["env"] = env
        captured["stdout"] = stdout
        captured["text"] = text
        captured["bufsize"] = bufsize
        return _Proc()

    monkeypatch.setattr(codex_mod.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(codex_mod, "_start_output_tee", lambda **kwargs: None)
    tick = {"value": 946685000.0}

    def _fake_time():
        tick["value"] += 10.0
        return tick["value"]

    monkeypatch.setattr(codex_mod.time, "time", _fake_time)
    monkeypatch.setattr(codex_mod.time, "sleep", lambda _seconds: None)
    pid = invoke_codex_cli(
        check_tool_available=lambda provider: True,
        codex_provider=AIProvider.CODEX,
        codex_cli_path="codex",
        codex_model="gpt-5-codex",
        get_tasks_logs_dir=lambda workspace, subdir: str(tmp_path / "logs"),
        tool_unavailable_error=RuntimeError,
        logger=logger,
        agent_prompt="do work",
        workspace_dir=str(tmp_path / "repo"),
        issue_num="83",
        log_subdir="nexus",
        env={"FOO": "BAR", "OPENAI_API_KEY": "test-key", "NEXUS_REQUESTER_ID": "31efac50-8610-4b4b-9129-6a48e96a110c"},
    )
    assert pid == 4321
    assert captured["cmd"] == [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        "-c",
        'sandbox_permissions=["network-access"]',
        "--model",
        "gpt-5-codex",
        "do work",
    ]
    assert captured["cwd"] == str(tmp_path / "repo")
    assert captured["stdout"] == codex_mod.subprocess.PIPE
    assert captured["text"] is True
    assert captured["bufsize"] == 1
    assert isinstance(captured["env"], dict) and captured["env"]["FOO"] == "BAR"
    assert captured["env"]["OPENAI_API_KEY"] == "test-key"
    assert any(
        "Codex auth diagnostic: issue=83 requester=31efac50... has_openai_key=True openai_key_len=8 env_has_openai=True" in message
        for level, message in logger.messages
        if level == "info"
    )
    assert os.path.exists(non_empty_rollout)
    assert not os.path.exists(empty_rollout)


def test_copilot_agent_invoker_success(monkeypatch, tmp_path):
    import nexus.plugins.builtin.ai_runtime.provider_invokers.agent_invokers as agent_mod

    class _Logger:
        def info(self, *args, **kwargs):
            pass

        def error(self, *args, **kwargs):
            pass

    monkeypatch.setattr(agent_mod.time, "strftime", lambda fmt: "20260101_120000")
    captured: dict[str, Any] = {}

    class _Proc:
        pid = 9876
        stdout = None

        def poll(self):
            return None

    def _fake_popen(cmd, cwd, stdin, stdout, stderr, env, text, bufsize):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["env"] = env
        captured["stdout"] = stdout
        captured["text"] = text
        captured["bufsize"] = bufsize
        return _Proc()

    monkeypatch.setattr(agent_mod.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(agent_mod, "_start_output_tee", lambda **kwargs: None)
    pid = invoke_copilot_agent_cli(
        check_tool_available=lambda provider: True,
        copilot_provider=AIProvider.COPILOT,
        copilot_cli_path="copilot",
        copilot_model="gpt-5-mini",
        copilot_supports_model=True,
        get_tasks_logs_dir=lambda workspace, subdir: str(tmp_path / "logs"),
        tool_unavailable_error=RuntimeError,
        rate_limited_error=RuntimeError,
        logger=_Logger(),
        agent_prompt="run agent",
        workspace_dir=str(tmp_path / "repo"),
        agents_dir=str(tmp_path / "repo" / "agents"),
        base_dir=str(tmp_path),
        issue_num="10",
        env={"X": "1"},
    )
    assert pid == 9876
    assert "--allow-all-tools" in captured["cmd"]
    assert "--model" in captured["cmd"]
    assert "gpt-5-mini" in captured["cmd"]
    assert captured["stdout"] == agent_mod.subprocess.PIPE
    assert captured["text"] is True
    assert captured["bufsize"] == 1
    assert isinstance(captured["env"], dict) and captured["env"]["X"] == "1"


def test_copilot_agent_invoker_immediate_exit_rate_limit(monkeypatch, tmp_path):
    import nexus.plugins.builtin.ai_runtime.provider_invokers.agent_invokers as agent_mod

    class _Logger:
        def info(self, *args, **kwargs):
            pass

        def error(self, *args, **kwargs):
            pass

    class _RateLimited(Exception):
        pass

    monkeypatch.setattr(agent_mod.time, "strftime", lambda fmt: "20260101_120000")

    class _Proc:
        pid = 456
        stdout = io.StringIO("Copilot error: 402 You have no quota\n")

        def poll(self):
            return 1

    def _fake_popen(cmd, cwd, stdin, stdout, stderr, env, text, bufsize):
        return _Proc()

    monkeypatch.setattr(agent_mod.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(
        agent_mod,
        "_start_output_tee",
        lambda **kwargs: agent_mod._stream_process_output(
            stream=kwargs["process"].stdout,
            log_path=kwargs["log_path"],
            logger=kwargs["logger"],
            output_label=kwargs["output_label"],
        ),
    )
    monkeypatch.setattr(agent_mod.time, "sleep", lambda seconds: None)

    try:
        invoke_copilot_agent_cli(
            check_tool_available=lambda provider: True,
            copilot_provider=AIProvider.COPILOT,
            copilot_cli_path="copilot",
            copilot_model="",
            copilot_supports_model=False,
            get_tasks_logs_dir=lambda workspace, subdir: str(tmp_path / "logs"),
            tool_unavailable_error=RuntimeError,
            rate_limited_error=_RateLimited,
            logger=_Logger(),
            agent_prompt="run agent",
            workspace_dir=str(tmp_path / "repo"),
            agents_dir=str(tmp_path / "repo" / "agents"),
            base_dir=str(tmp_path),
            issue_num="12",
        )
        assert False, "expected rate-limited immediate-exit error"
    except _RateLimited as exc:
        assert "rate limit" in str(exc).lower() or "quota" in str(exc).lower()


def test_gemini_agent_invoker_immediate_exit_rate_limit(monkeypatch, tmp_path):
    import nexus.plugins.builtin.ai_runtime.provider_invokers.agent_invokers as agent_mod

    class _Logger:
        def info(self, *args, **kwargs):
            pass

        def error(self, *args, **kwargs):
            pass

    class _RateLimited(Exception):
        pass

    monkeypatch.setattr(agent_mod.time, "strftime", lambda fmt: "20260101_120000")

    class _Proc:
        pid = 123
        stdout = io.StringIO("RateLimitExceeded: 429\n")

        def poll(self):
            return 1

    def _fake_popen(cmd, cwd, stdin, stdout, stderr, env, text, bufsize):
        return _Proc()

    monkeypatch.setattr(agent_mod.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(
        agent_mod,
        "_start_output_tee",
        lambda **kwargs: agent_mod._stream_process_output(
            stream=kwargs["process"].stdout,
            log_path=kwargs["log_path"],
            logger=kwargs["logger"],
            output_label=kwargs["output_label"],
        ),
    )
    monkeypatch.setattr(agent_mod.time, "sleep", lambda seconds: None)

    try:
        invoke_gemini_agent_cli(
            check_tool_available=lambda provider: True,
            gemini_provider=AIProvider.GEMINI,
            gemini_cli_path="gemini",
            gemini_model="",
            get_tasks_logs_dir=lambda workspace, subdir: str(tmp_path / "logs"),
            tool_unavailable_error=RuntimeError,
            rate_limited_error=_RateLimited,
            logger=_Logger(),
            agent_prompt="run agent",
            workspace_dir=str(tmp_path / "repo"),
            agents_dir=str(tmp_path / "repo" / "agents"),
            issue_num="11",
        )
        assert False, "expected rate-limited immediate-exit error"
    except _RateLimited as exc:
        assert "rate limit" in str(exc).lower()


def test_agent_output_tee_writes_log_and_emits_lines(tmp_path):
    import nexus.plugins.builtin.ai_runtime.provider_invokers.agent_invokers as agent_mod

    class _Logger:
        def __init__(self):
            self.messages = []

        def info(self, msg, *args):
            self.messages.append(msg % args if args else msg)

    logger = _Logger()
    log_path = tmp_path / "agent.log"

    agent_mod._stream_process_output(
        stream=io.StringIO("first line\nsecond line\n"),
        log_path=str(log_path),
        logger=logger,
        output_label="codex",
    )

    assert log_path.read_text(encoding="utf-8") == "first line\nsecond line\n"
    assert "[codex] first line" in logger.messages
    assert "[codex] second line" in logger.messages


def test_agent_lifecycle_logging_redacts_prompt_and_logs_exit(monkeypatch):
    import nexus.plugins.builtin.ai_runtime.provider_invokers.agent_invokers as agent_mod

    class _Logger:
        def __init__(self):
            self.messages = []

        def info(self, msg, *args):
            self.messages.append(msg % args if args else msg)

        def warning(self, msg, *args):
            self.messages.append(msg % args if args else msg)

    class _Proc:
        pid = 321

        def wait(self):
            return 0

    class _ImmediateThread:
        def __init__(self, *, target, daemon):
            self._target = target

        def start(self):
            self._target()

    logger = _Logger()
    monkeypatch.setattr(agent_mod.threading, "Thread", _ImmediateThread)

    agent_mod._monitor_process_lifecycle(
        process=_Proc(),
        logger=logger,
        output_label="copilot",
    )

    rendered = agent_mod._redact_command_for_logs(
        ["copilot", "-p", "very secret prompt", "--allow-all-tools"]
    )

    assert "<prompt>" in rendered
    assert "very secret prompt" not in rendered
    assert "[agent:copilot] exit pid=321 code=0" in logger.messages


def test_gemini_transcription_invoker_rate_limit_and_artifact(monkeypatch, tmp_path):
    import nexus.plugins.builtin.ai_runtime.provider_invokers.transcription_invokers as tx_mod

    audio = tmp_path / "note.ogg"
    audio.write_text("x")

    class _Logger:
        def info(self, *args, **kwargs):
            pass

    class _RateLimited(Exception):
        pass

    class _ResultRate:
        returncode = 1
        stderr = "quota exceeded"
        stdout = ""

    monkeypatch.setattr(tx_mod.subprocess, "run", lambda *args, **kwargs: _ResultRate())
    try:
        transcribe_with_gemini_cli(
            check_tool_available=lambda provider: True,
            gemini_provider=AIProvider.GEMINI,
            gemini_cli_path="gemini",
            strip_cli_tool_output=lambda t: t,
            is_non_transcription_artifact=lambda text, path: False,
            tool_unavailable_error=RuntimeError,
            rate_limited_error=_RateLimited,
            logger=_Logger(),
            audio_file_path=str(audio),
            timeout=5,
        )
        assert False, "expected rate limit"
    except _RateLimited:
        pass

    class _ResultOk:
        returncode = 0
        stderr = ""
        stdout = "artifact"

    monkeypatch.setattr(tx_mod.subprocess, "run", lambda *args, **kwargs: _ResultOk())
    try:
        transcribe_with_gemini_cli(
            check_tool_available=lambda provider: True,
            gemini_provider=AIProvider.GEMINI,
            gemini_cli_path="gemini",
            strip_cli_tool_output=lambda t: t,
            is_non_transcription_artifact=lambda text, path: True,
            tool_unavailable_error=RuntimeError,
            rate_limited_error=_RateLimited,
            logger=_Logger(),
            audio_file_path=str(audio),
            timeout=5,
        )
        assert False, "expected artifact rejection"
    except Exception as exc:
        assert "non-transcription" in str(exc)


def test_copilot_transcription_invoker_stages_and_returns_text(monkeypatch, tmp_path):
    import nexus.plugins.builtin.ai_runtime.provider_invokers.transcription_invokers as tx_mod

    audio = tmp_path / "voice.ogg"
    audio.write_text("x")

    class _Logger:
        def info(self, *args, **kwargs):
            pass

    captured: dict[str, Any] = {}

    class _Result:
        returncode = 0
        stderr = ""
        stdout = "hello world"

    def _fake_run(cmd, capture_output, text, timeout):
        captured["cmd"] = cmd
        return _Result()

    monkeypatch.setattr(tx_mod.subprocess, "run", _fake_run)
    out = transcribe_with_copilot_cli(
        check_tool_available=lambda provider: True,
        copilot_provider=AIProvider.COPILOT,
        copilot_cli_path="copilot",
        strip_cli_tool_output=lambda t: t,
        is_non_transcription_artifact=lambda text, path: False,
        tool_unavailable_error=RuntimeError,
        logger=_Logger(),
        audio_file_path=str(audio),
        timeout=7,
    )
    assert out == "hello world"
    assert captured["cmd"][0] == "copilot"
    assert "--add-dir" in captured["cmd"]


def test_local_whisper_invoker_loads_model_and_returns_cache_state(tmp_path):
    audio = tmp_path / "voice.ogg"
    audio.write_text("x")

    class _Model:
        def transcribe(self, path, **kwargs):
            assert path == str(audio)
            assert kwargs["fp16"] is False
            assert kwargs["language"] == "en"
            return {"text": "hello", "language": "en"}

    class _WhisperModule:
        def load_model(self, model_name):
            assert model_name == "base"
            return _Model()

    class _Logger:
        def info(self, *args, **kwargs):
            pass

    result = transcribe_with_local_whisper(
        audio_file_path=str(audio),
        current_model_instance=None,
        current_model_name="",
        configured_model="whisper-1",
        whisper_language="en",
        whisper_languages=["en", "it"],
        normalize_local_whisper_model_name=lambda configured: "base",
        import_whisper=lambda: _WhisperModule(),
        tool_unavailable_error=RuntimeError,
        logger=_Logger(),
    )
    assert result["text"] == "hello"
    assert result["model_name"] == "base"
    assert result["model_instance"] is not None


def test_local_whisper_invoker_rejects_disallowed_language(tmp_path):
    audio = tmp_path / "voice.ogg"
    audio.write_text("x")

    class _Model:
        def transcribe(self, path, **kwargs):
            return {"text": "ciao", "language": "fr"}

    class _WhisperModule:
        def load_model(self, model_name):
            return _Model()

    class _Logger:
        def info(self, *args, **kwargs):
            pass

    try:
        transcribe_with_local_whisper(
            audio_file_path=str(audio),
            current_model_instance=None,
            current_model_name="",
            configured_model="base",
            whisper_language=None,
            whisper_languages=["en", "it"],
            normalize_local_whisper_model_name=lambda configured: configured,
            import_whisper=lambda: _WhisperModule(),
            tool_unavailable_error=RuntimeError,
            logger=_Logger(),
        )
        assert False, "expected language restriction error"
    except Exception as exc:
        assert "outside allowed set" in str(exc)


def test_run_transcription_attempts_fallback_and_rate_limit_recording():
    class _RateLimited(Exception):
        pass

    events: list[tuple[str, tuple]] = []

    class _Logger:
        def info(self, msg, *args):
            events.append(("info", (msg, *args)))

        def warning(self, msg, *args):
            events.append(("warning", (msg, *args)))

        def error(self, msg, *args):
            events.append(("error", (msg, *args)))

    out = run_transcription_attempts(
        attempts=["gemini", "copilot"],
        audio_file_path="/tmp/audio.ogg",
        transcribers_map={
            "whisper": lambda p: "unused",
            "gemini": lambda p: (_ for _ in ()).throw(_RateLimited("429")),
            "copilot": lambda p: "hello",
        },
        rate_limited_error_type=_RateLimited,
        record_rate_limit_with_context=lambda tool, exc, context: events.append(
            ("rate", (tool, context))
        ),
        provider_to_tool={
            "gemini": "gemini-provider",
            "copilot": "copilot-provider",
        },
        logger=_Logger(),
    )
    assert out == "hello"
    assert ("rate", ("gemini-provider", "transcribe")) in events

    out_none = run_transcription_attempts(
        attempts=["whisper"],
        audio_file_path="/tmp/audio.ogg",
        transcribers_map={
            "whisper": lambda p: (_ for _ in ()).throw(Exception("fail")),
            "gemini": lambda p: None,
            "copilot": lambda p: None,
        },
        rate_limited_error_type=_RateLimited,
        record_rate_limit_with_context=_noop_rate_limit,
        provider_to_tool={
            "gemini": "gemini-provider",
            "copilot": "copilot-provider",
        },
        logger=_Logger(),
    )
    assert out_none is None


def test_invoke_agent_with_fallback_handles_exclusion_rate_limit_and_success():
    class _Tool:
        def __init__(self, value):
            self.value = value

    gemini = _Tool("gemini")
    copilot = _Tool("copilot")
    codex = _Tool("codex")
    events: list[tuple[str, tuple]] = []

    class _RateLimited(Exception):
        pass

    class _Unavailable(Exception):
        pass

    class _Logger:
        def info(self, msg, *args):
            events.append(("info", (msg, *args)))

        def warning(self, msg, *args):
            events.append(("warning", (msg, *args)))

        def error(self, msg, *args):
            events.append(("error", (msg, *args)))

    def _invoke(tool, issue_num):
        assert issue_num == "83"
        if tool is gemini:
            raise _RateLimited("429")
        if tool is copilot:
            return None
        return 1234

    pid, tool = invoke_agent_with_fallback(
        issue_url="https://github.com/o/r/issues/83",
        exclude_tools=[],
        get_tool_order=lambda: [gemini, copilot, codex],
        check_tool_available=lambda t: True,
        invoke_tool=_invoke,
        record_rate_limit_with_context=lambda tool, exc, context: events.append(
            ("rate", (tool.value, context))
        ),
        record_failure=lambda tool: events.append(("fail", (tool.value,))),
        rate_limited_error_type=_RateLimited,
        tool_unavailable_error_type=_Unavailable,
        logger=_Logger(),
    )
    assert pid == 1234
    assert tool is codex
    assert ("rate", ("gemini", "invoke_agent")) in events


def test_invoke_agent_with_fallback_all_excluded_raises():
    class _Tool:
        def __init__(self, value):
            self.value = value

    class _Unavailable(Exception):
        pass

    class _Logger:
        def info(self, *args, **kwargs):
            pass

        def warning(self, *args, **kwargs):
            pass

        def error(self, *args, **kwargs):
            pass

    try:
        invoke_agent_with_fallback(
            issue_url=None,
            exclude_tools=["gemini"],
            get_tool_order=lambda: [_Tool("gemini")],
            check_tool_available=lambda t: True,
            invoke_tool=lambda tool, issue_num: 1,
            record_rate_limit_with_context=_noop_rate_limit,
            record_failure=lambda tool: None,
            rate_limited_error_type=RuntimeError,
            tool_unavailable_error_type=_Unavailable,
            logger=_Logger(),
        )
        assert False, "expected unavailable error"
    except _Unavailable as exc:
        assert "All tools excluded" in str(exc)


from typing import Any
