"""Tests for built-in AI runtime plugin."""

import subprocess

import nexus.core.auth.access_domain as access_domain
import nexus.core.config as config_mod
from nexus.plugins.builtin.ai_runtime_plugin import AIOrchestrator, AIProvider, RateLimitedError


class _FakeCompletedProcess:
    def __init__(self, stdout: str = '{"text": "ok"}'):
        self.returncode = 0
        self.stdout = stdout
        self.stderr = ""


def test_refine_description_default_keeps_original_text(monkeypatch):
    orchestrator = AIOrchestrator()

    monkeypatch.setattr(orchestrator, "get_fallback_tool", lambda _primary: AIProvider.COPILOT)

    def _raise_rate_limit(*_args, **_kwargs):
        raise RateLimitedError("gemini quota")

    def _raise_timeout(*_args, **_kwargs):
        raise Exception("Copilot analysis timed out")

    monkeypatch.setattr(orchestrator, "_run_gemini_cli_analysis", _raise_rate_limit)
    monkeypatch.setattr(orchestrator, "_run_copilot_analysis", _raise_timeout)

    source_text = "Preserve this exact fallback text"
    result = orchestrator.run_text_to_speech_analysis(source_text, task="refine_description")

    assert result["text"] == source_text


def test_analysis_uses_operation_agent_mapping_for_primary_tool(monkeypatch):
    orchestrator = AIOrchestrator(
        {
            "tool_preferences": {
                "triage": {"provider": "copilot", "profile": "small"},
                "designer": {"provider": "gemini", "profile": "small"},
            },
            "system_operations": {
                "default": "triage",
                "refine_description": "designer",
            },
        }
    )
    called = {"gemini": 0, "copilot": 0}

    def _gemini(*_args, **_kwargs):
        called["gemini"] += 1
        return {"text": "refined by gemini"}

    def _copilot(*_args, **_kwargs):
        called["copilot"] += 1
        return {"text": "refined by copilot"}

    monkeypatch.setattr(orchestrator, "_run_gemini_cli_analysis", _gemini)
    monkeypatch.setattr(orchestrator, "_run_copilot_analysis", _copilot)

    result = orchestrator.run_text_to_speech_analysis("input", task="refine_description")

    assert result["text"] == "refined by gemini"
    assert called["gemini"] == 1
    assert called["copilot"] == 0


def test_analysis_fallback_order_comes_from_tool_preferences(monkeypatch):
    orchestrator = AIOrchestrator(
        {
            "tool_preferences": {
                "triage": {"provider": "copilot", "profile": "small"},
                "designer": {"provider": "gemini", "profile": "small"},
            },
            "system_operations": {
                "default": "triage",
            },
        }
    )
    call_order: list[str] = []

    def _copilot(*_args, **_kwargs):
        call_order.append("copilot")
        raise Exception("copilot failed")

    def _gemini(*_args, **_kwargs):
        call_order.append("gemini")
        return {"project": "nexus", "type": "feature", "task_name": "ok"}

    monkeypatch.setattr(orchestrator, "_run_copilot_analysis", _copilot)
    monkeypatch.setattr(orchestrator, "_run_gemini_cli_analysis", _gemini)

    result = orchestrator.run_text_to_speech_analysis("input", task="classify")

    assert result["task_name"] == "ok"
    assert call_order == ["copilot", "gemini"]


def test_refine_description_bug_report_prefers_triage_over_designer(monkeypatch):
    orchestrator = AIOrchestrator(
        {
            "tool_preferences": {
                "triage": {"provider": "copilot", "profile": "small"},
                "designer": {"provider": "gemini", "profile": "small"},
            },
            "system_operations": {
                "default": "triage",
                "refine_description": "designer",
                "overrides": {
                    "issue": {
                        "refine_description": "triage",
                    },
                },
            },
        }
    )
    called = {"gemini": 0, "copilot": 0}

    def _gemini(*_args, **_kwargs):
        called["gemini"] += 1
        return {"text": "designer output"}

    def _copilot(*_args, **_kwargs):
        called["copilot"] += 1
        return {"text": "triage output"}

    monkeypatch.setattr(orchestrator, "_run_gemini_cli_analysis", _gemini)
    monkeypatch.setattr(orchestrator, "_run_copilot_analysis", _copilot)

    result = orchestrator.run_text_to_speech_analysis(
        "Bug report: app crashes with traceback on login",
        task="refine_description",
    )

    assert result["text"] == "triage output"
    assert called["copilot"] == 1
    assert called["gemini"] == 0


def test_refine_description_bug_report_without_issue_override_keeps_mapped_agent(monkeypatch):
    orchestrator = AIOrchestrator(
        {
            "tool_preferences": {
                "triage": {"provider": "copilot", "profile": "small"},
                "designer": {"provider": "gemini", "profile": "small"},
            },
            "system_operations": {
                "default": "triage",
                "refine_description": "designer",
            },
        }
    )
    called = {"gemini": 0, "copilot": 0}

    def _gemini(*_args, **_kwargs):
        called["gemini"] += 1
        return {"text": "designer output"}

    def _copilot(*_args, **_kwargs):
        called["copilot"] += 1
        return {"text": "triage output"}

    monkeypatch.setattr(orchestrator, "_run_gemini_cli_analysis", _gemini)
    monkeypatch.setattr(orchestrator, "_run_copilot_analysis", _copilot)

    result = orchestrator.run_text_to_speech_analysis(
        "Bug report: app crashes with traceback on login",
        task="refine_description",
    )

    assert result["text"] == "designer output"
    assert called["gemini"] == 1
    assert called["copilot"] == 0


def test_chat_defaults_to_project_chat_agent(monkeypatch):
    orchestrator = AIOrchestrator(
        {
            "tool_preferences": {
                "triage": {"provider": "copilot", "profile": "small"},
                "designer": {"provider": "gemini", "profile": "small"},
            },
            "system_operations": {
                "default": "triage",
            },
            "chat_agent_types_resolver": lambda project: (
                ["designer"] if project == "nexus" else ["triage"]
            ),
        }
    )
    called = {"gemini": 0, "copilot": 0}

    def _gemini(*_args, **_kwargs):
        called["gemini"] += 1
        return {"text": "designer response"}

    def _copilot(*_args, **_kwargs):
        called["copilot"] += 1
        return {"text": "triage response"}

    monkeypatch.setattr(orchestrator, "_run_gemini_cli_analysis", _gemini)
    monkeypatch.setattr(orchestrator, "_run_copilot_analysis", _copilot)

    result = orchestrator.run_text_to_speech_analysis(
        "hello",
        task="chat",
        project_name="nexus",
    )

    assert result["text"] == "designer response"
    assert called["gemini"] == 1
    assert called["copilot"] == 0


def test_chat_falls_through_full_provider_chain_and_returns_honest_default(monkeypatch):
    orchestrator = AIOrchestrator(
        {
            "tool_preferences": {
                "triage": {"provider": "copilot", "profile": "small"},
                "designer": {"provider": "gemini", "profile": "small"},
            },
            "system_operations": {"default": "triage"},
            "chat_agent_types_resolver": lambda project: ["designer"],
        }
    )
    call_order: list[str] = []

    def _gemini(*_args, **_kwargs):
        call_order.append("gemini")
        raise Exception("gemini auth missing")

    def _copilot(*_args, **_kwargs):
        call_order.append("copilot")
        raise Exception("copilot auth missing")

    def _claude(*_args, **_kwargs):
        call_order.append("claude")
        raise Exception("claude auth missing")

    def _codex(*_args, **_kwargs):
        call_order.append("codex")
        raise Exception("codex auth missing")

    def _ollama(*_args, **_kwargs):
        call_order.append("ollama")
        raise Exception("ollama unavailable")

    monkeypatch.setattr(orchestrator, "_run_gemini_cli_analysis", _gemini)
    monkeypatch.setattr(orchestrator, "_run_copilot_analysis", _copilot)
    monkeypatch.setattr(orchestrator, "_run_claude_analysis", _claude)
    monkeypatch.setattr(orchestrator, "_run_codex_analysis", _codex)
    monkeypatch.setattr(orchestrator, "_run_ollama_analysis", _ollama)

    result = orchestrator.run_text_to_speech_analysis(
        "hello",
        task="chat",
        project_name="nexus",
    )

    assert call_order == ["gemini", "copilot", "claude", "codex", "ollama"]
    assert "couldn't generate a reply right now" in result["text"].lower()
    assert "gemini, copilot, claude, codex, ollama" in result["text"].lower()
    assert "last error: ollama unavailable" in result["text"].lower()


def test_chat_analysis_uses_requester_scoped_env_and_project_cwd(monkeypatch, tmp_path):
    project_root = tmp_path / "workspace"
    project_root.mkdir()
    captured = {}
    orchestrator = AIOrchestrator(
        {
            "tool_preferences": {
                "designer": {"provider": "copilot", "profile": "small"},
            },
            "chat_agent_types_resolver": lambda _project: ["designer"],
        }
    )

    monkeypatch.setattr(access_domain, "build_execution_env", lambda _nexus_id: ({"GITHUB_TOKEN": "user-token"}, None))
    monkeypatch.setattr(config_mod, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(config_mod, "PROJECT_CONFIG", {"nexus": {"workspace": "workspace"}})

    def _capture_provider(tool, text, task, **kwargs):
        captured["tool"] = tool
        captured["text"] = text
        captured["task"] = task
        captured["kwargs"] = kwargs
        return {"text": "ok"}

    monkeypatch.setattr(orchestrator, "_run_analysis_with_provider", _capture_provider)

    result = orchestrator.run_text_to_speech_analysis(
        "hello",
        task="chat",
        project_name="nexus",
        requester_context={"nexus_id": "nexus-42"},
    )

    assert result["text"] == "ok"
    assert captured["task"] == "chat"
    assert captured["kwargs"]["_provider_env"] == {"GITHUB_TOKEN": "user-token"}
    assert captured["kwargs"]["_analysis_cwd"] == str(project_root)


def test_copilot_analysis_timeout_respects_config(monkeypatch):
    orchestrator = AIOrchestrator({"analysis_timeout": 45})
    captured = {"timeout": None}

    monkeypatch.setattr(orchestrator, "check_tool_available", lambda _tool: True)

    def _fake_run(*_args, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return _FakeCompletedProcess('{"project": "nexus", "type": "feature", "task_name": "abc"}')

    monkeypatch.setattr(subprocess, "run", _fake_run)

    result = orchestrator._run_copilot_analysis("input", "classify")

    assert captured["timeout"] == 45
    assert result["project"] == "nexus"


def test_copilot_analysis_passes_provider_env_and_cwd(monkeypatch, tmp_path):
    orchestrator = AIOrchestrator()
    captured = {"env": None, "cwd": None}

    monkeypatch.setattr(orchestrator, "check_tool_available", lambda _tool: True)

    def _fake_run(*_args, **kwargs):
        captured["env"] = kwargs.get("env")
        captured["cwd"] = kwargs.get("cwd")
        return _FakeCompletedProcess('{"project": "nexus", "type": "feature", "task_name": "abc"}')

    monkeypatch.setattr(subprocess, "run", _fake_run)

    result = orchestrator._run_copilot_analysis(
        "input",
        "classify",
        _provider_env={"GITHUB_TOKEN": "user-token"},
        _analysis_cwd=str(tmp_path),
    )

    assert result["project"] == "nexus"
    assert captured["env"] == {"GITHUB_TOKEN": "user-token"}
    assert captured["cwd"] == str(tmp_path)


def test_analysis_without_project_workspace_does_not_pass_string_none_cwd(monkeypatch):
    orchestrator = AIOrchestrator(
        {
            "tool_preferences": {
                "triage": {"provider": "copilot", "profile": "small"},
            },
            "system_operations": {"default": "triage"},
        }
    )
    captured = {"cwd": "unset"}

    monkeypatch.setattr(config_mod, "PROJECT_CONFIG", {})
    monkeypatch.setattr(orchestrator, "check_tool_available", lambda _tool: True)

    def _fake_run(*_args, **kwargs):
        captured["cwd"] = kwargs.get("cwd")
        return _FakeCompletedProcess('{"project": "nexus", "type": "question", "task_name": "ok"}')

    monkeypatch.setattr(subprocess, "run", _fake_run)

    result = orchestrator.run_text_to_speech_analysis(
        "Which new features could we add to the framework?",
        task="detect_intent",
        project_name="default",
    )

    assert result["project"] == "nexus"
    assert captured["cwd"] is None


def test_gemini_analysis_uses_analysis_timeout(monkeypatch):
    orchestrator = AIOrchestrator({"analysis_timeout": 47})
    captured = {"timeout": None}

    monkeypatch.setattr(orchestrator, "check_tool_available", lambda _tool: True)

    def _fake_run(*_args, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return _FakeCompletedProcess('{"project": "nexus", "type": "feature", "task_name": "abc"}')

    monkeypatch.setattr(subprocess, "run", _fake_run)

    result = orchestrator._run_gemini_cli_analysis("input", "classify")

    assert captured["timeout"] == 47
    assert result["project"] == "nexus"


def test_gemini_agent_launch_uses_configured_model(monkeypatch, tmp_path):
    orchestrator = AIOrchestrator({"gemini_model": "gemini-3-pro"})
    captured = {"cmd": None}

    monkeypatch.setattr(orchestrator, "check_tool_available", lambda _tool: True)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    class _FakePopen:
        def __init__(self, cmd, **_kwargs):
            captured["cmd"] = cmd
            self.pid = 12345

        def poll(self):
            return None

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)

    pid = orchestrator._invoke_gemini_agent(
        agent_prompt="hello",
        workspace_dir=str(tmp_path),
        agents_dir=str(tmp_path),
        base_dir=str(tmp_path),
        issue_num="55",
    )

    assert pid == 12345
    assert "--model" in captured["cmd"]
    assert "gemini-3-pro" in captured["cmd"]


class TestStripCliToolOutput:
    """Tests for _strip_cli_tool_output."""

    def test_no_op_true_block(self):
        text = "● No-op\n" "  $ true\n" "  └ 1 line...\n" "\n" "Add a dry-run validation mode."
        assert AIOrchestrator._strip_cli_tool_output(text) == "Add a dry-run validation mode."

    def test_multiple_tool_blocks(self):
        text = (
            "● List directory .\n"
            "  └ 7 files found\n"
            "\n"
            "● Read file.py\n"
            "  └ 100 lines read\n"
            "\n"
            "The answer is 42."
        )
        assert AIOrchestrator._strip_cli_tool_output(text) == "The answer is 42."

    def test_no_artifacts_passthrough(self):
        text = "Clean text\nwith no tool output."
        assert AIOrchestrator._strip_cli_tool_output(text) == text

    def test_mixed_content(self):
        text = "Summary:\n" "● No-op\n" "  $ true\n" "  └ 1 line...\n" "\n" "Details here."
        result = AIOrchestrator._strip_cli_tool_output(text)
        assert "No-op" not in result
        assert "$ true" not in result
        assert "Summary:" in result
        assert "Details here." in result

    def test_empty_string(self):
        assert AIOrchestrator._strip_cli_tool_output("") == ""

    def test_json_not_stripped(self):
        text = '{"project": "nexus", "type": "feature"}'
        assert AIOrchestrator._strip_cli_tool_output(text) == text

    def test_codex_transcript_blocks_are_stripped(self):
        text = (
            '✗ Grep "OpenClaw" (/home/ubuntu/git/ghabs)\n'
            "  └ Permission denied and could not request permission from user\n"
            "\n"
            "✗ List repos and markdown files\n"
            '  $ find /home/ubuntu/git/ghabs -maxdepth 3 -name "*.md"\n'
            "  Permission denied and could not request permission from user\n"
            "\n"
            "Based on the codebase and OpenClaw's public profile, here's a comparison."
        )
        assert AIOrchestrator._strip_cli_tool_output(text) == (
            "Based on the codebase and OpenClaw's public profile, here's a comparison."
        )


class TestParseAnalysisResult:
    def test_parses_fenced_json_result(self):
        orchestrator = AIOrchestrator()

        output = (
            "```json\n"
            '{"project": "nexus", "type": "feature", "task_name": "add-audio-transcription-support"}'
            "\n```"
        )

        parsed = orchestrator._parse_analysis_result(output, task="classify")

        assert parsed["project"] == "nexus"
        assert parsed["type"] == "feature"
        assert parsed["task_name"] == "add-audio-transcription-support"

    def test_parses_json_embedded_in_text(self):
        orchestrator = AIOrchestrator()

        output = (
            "Analysis result:\n"
            '{"project": "nexus", "type": "feature", "task_name": "abc"}'
            "\nDone."
        )

        parsed = orchestrator._parse_analysis_result(output, task="classify")

        assert parsed == {"project": "nexus", "type": "feature", "task_name": "abc"}


def test_refusal_text_is_not_accepted_as_transcription(monkeypatch):
    orchestrator = AIOrchestrator()

    monkeypatch.setattr(orchestrator, "check_tool_available", lambda _tool: True)
    monkeypatch.setattr("os.path.exists", lambda _path: True)
    monkeypatch.setattr("shutil.copy2", lambda _src, _dst: None)

    def _fake_run(*_args, **_kwargs):
        return _FakeCompletedProcess(
            "I am sorry, but I cannot directly transcribe audio files. "
            "My capabilities are limited to text-based interactions."
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)

    try:
        orchestrator._transcribe_with_copilot_cli("temp_voice.ogg")
        raise AssertionError("Expected refusal text to be rejected")
    except Exception as exc:
        assert "non-transcription content" in str(exc)


def test_detects_common_transcription_refusal_markers():
    refusal = (
        "My capabilities are limited to text-based interactions and I cannot transcribe audio."
    )
    assert AIOrchestrator._is_transcription_refusal(refusal) is True
    assert AIOrchestrator._is_transcription_refusal("ship issue 53 fix now") is False


def test_gemini_file_reference_echo_is_rejected(monkeypatch):
    orchestrator = AIOrchestrator()

    monkeypatch.setattr(orchestrator, "check_tool_available", lambda _tool: True)
    monkeypatch.setattr("os.path.exists", lambda _path: True)

    def _fake_run(*_args, **_kwargs):
        return _FakeCompletedProcess("File: temp_voice.ogg")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    try:
        orchestrator._transcribe_with_gemini_cli("temp_voice.ogg")
        raise AssertionError("Expected file-reference echo to be rejected")
    except Exception as exc:
        assert "non-transcription content" in str(exc)


def test_copilot_tool_debug_output_is_rejected(monkeypatch):
    orchestrator = AIOrchestrator()

    monkeypatch.setattr(orchestrator, "check_tool_available", lambda _tool: True)
    monkeypatch.setattr("os.path.exists", lambda _path: True)
    monkeypatch.setattr("shutil.copy2", lambda _src, _dst: None)

    debug_output = (
        "✗ Check whisper availability\n"
        '$ python3 -c "import whisper"\n'
        "Permission denied and could not request permission from user\n"
        "I'm unable to transcribe the audio file."
    )

    def _fake_run(*_args, **_kwargs):
        return _FakeCompletedProcess(debug_output)

    monkeypatch.setattr(subprocess, "run", _fake_run)

    try:
        orchestrator._transcribe_with_copilot_cli("temp_voice.ogg")
        raise AssertionError("Expected tool-debug output to be rejected")
    except Exception as exc:
        assert "non-transcription content" in str(exc)


def test_copilot_transcription_uses_add_dir(monkeypatch):
    orchestrator = AIOrchestrator()
    captured = {"cmd": None}

    monkeypatch.setattr(orchestrator, "check_tool_available", lambda _tool: True)
    monkeypatch.setattr("os.path.exists", lambda _path: True)
    monkeypatch.setattr("shutil.copy2", lambda _src, _dst: None)

    def _fake_run(cmd, **_kwargs):
        captured["cmd"] = cmd
        return _FakeCompletedProcess("hello from audio")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    text = orchestrator._transcribe_with_copilot_cli("temp_voice.ogg")

    assert text == "hello from audio"
    assert "--add-dir" in captured["cmd"]
    assert "--add-file" not in captured["cmd"]


def test_transcript_provider_env_uses_gemini_by_default(monkeypatch):
    orchestrator = AIOrchestrator()
    called = {"copilot": False, "gemini": False}

    def _copilot(_path):
        called["copilot"] = True
        return "transcribed"

    def _gemini(_path):
        called["gemini"] = True
        return "transcribed"

    monkeypatch.setattr(orchestrator, "_transcribe_with_copilot_cli", _copilot)
    monkeypatch.setattr(orchestrator, "_transcribe_with_gemini_cli", _gemini)

    result = orchestrator.transcribe_audio("temp_voice.ogg")

    assert result == "transcribed"
    assert called["copilot"] is False
    assert called["gemini"] is True


def test_transcript_provider_uses_whisper_when_configured(monkeypatch):
    monkeypatch.setenv("TRANSCRIPT_PROVIDER", "whisper")
    orchestrator = AIOrchestrator({"fallback_enabled": False})
    called = {"whisper": False, "gemini": False, "copilot": False}

    def _whisper(_path):
        called["whisper"] = True
        return "spoken text"

    def _gemini(_path):
        called["gemini"] = True
        return "gemini"

    def _copilot(_path):
        called["copilot"] = True
        return "copilot"

    monkeypatch.setattr(orchestrator, "_transcribe_with_whisper_api", _whisper)
    monkeypatch.setattr(orchestrator, "_transcribe_with_gemini_cli", _gemini)
    monkeypatch.setattr(orchestrator, "_transcribe_with_copilot_cli", _copilot)

    result = orchestrator.transcribe_audio("temp_voice.ogg")

    assert result == "spoken text"
    assert called["whisper"] is True
    assert called["gemini"] is False
    assert called["copilot"] is False


def test_transcription_timeout_respects_config(monkeypatch):
    orchestrator = AIOrchestrator({"transcription_timeout": 150})
    captured = {"timeout": None}

    monkeypatch.setattr(orchestrator, "check_tool_available", lambda _tool: True)
    monkeypatch.setattr("os.path.exists", lambda _path: True)
    monkeypatch.setattr("shutil.copy2", lambda _src, _dst: None)

    def _fake_run(*_args, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return _FakeCompletedProcess("voice transcript")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    text = orchestrator._transcribe_with_copilot_cli("temp_voice.ogg")

    assert text == "voice transcript"
    assert captured["timeout"] == 150


def test_transcription_operation_mapping_overrides_transcript_provider(monkeypatch):
    monkeypatch.setenv("TRANSCRIPT_PROVIDER", "gemini")
    orchestrator = AIOrchestrator(
        {
            "tool_preferences": {
                "triage": {"provider": "copilot", "profile": "small"},
                "designer": {"provider": "gemini", "profile": "small"},
            },
            "system_operations": {
                "transcribe_audio": "triage",
            },
        }
    )
    called = {"copilot": 0, "gemini": 0}

    def _copilot(_path):
        called["copilot"] += 1
        return "copilot transcript"

    def _gemini(_path):
        called["gemini"] += 1
        return "gemini transcript"

    monkeypatch.setattr(orchestrator, "_transcribe_with_copilot_cli", _copilot)
    monkeypatch.setattr(orchestrator, "_transcribe_with_gemini_cli", _gemini)

    result = orchestrator.transcribe_audio("temp_voice.ogg")

    assert result == "copilot transcript"
    assert called["copilot"] == 1
    assert called["gemini"] == 0


def test_transcription_operation_mapping_uses_global_fallback_order(monkeypatch):
    orchestrator = AIOrchestrator(
        {
            "tool_preferences": {
                "triage": {"provider": "copilot", "profile": "small"},
                "designer": {"provider": "gemini", "profile": "small"},
            },
            "system_operations": {
                "transcribe_audio": "triage",
            },
            "fallback_enabled": True,
        }
    )
    call_order: list[str] = []

    def _copilot(_path):
        call_order.append("copilot")
        raise Exception("copilot failed")

    def _gemini(_path):
        call_order.append("gemini")
        return "gemini transcript"

    monkeypatch.setattr(orchestrator, "_transcribe_with_copilot_cli", _copilot)
    monkeypatch.setattr(orchestrator, "_transcribe_with_gemini_cli", _gemini)

    result = orchestrator.transcribe_audio("temp_voice.ogg")

    assert result == "gemini transcript"
    assert call_order == ["copilot", "gemini"]


def test_primary_tool_parses_model_wrapped_preferences():
    orchestrator = AIOrchestrator(
        {
            "tool_preferences": {
                "triage": {"provider": "copilot", "profile": "large"},
                "writer": {"provider": "gemini", "profile": "small"},
            }
        }
    )
    assert orchestrator.get_primary_tool("triage") == AIProvider.COPILOT
    assert orchestrator.get_primary_tool("writer") == AIProvider.GEMINI


def test_copilot_model_override_is_capability_gated(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    def _fake_invoke(**kwargs):
        captured.update(kwargs)
        return 111

    monkeypatch.setattr(
        "nexus.plugins.builtin.ai_runtime_plugin.invoke_copilot_agent_cli_impl",
        _fake_invoke,
    )

    orchestrator = AIOrchestrator(
        {
            "copilot_model": "global-model",
            "copilot_supports_model": False,
            "tool_preferences": {"triage": {"provider": "copilot", "profile": "small"}},
            "model_profiles": {"small": {"copilot": "agent-model"}},
        }
    )

    pid = orchestrator._invoke_copilot(
        agent_prompt="go",
        workspace_dir=str(tmp_path),
        agents_dir=str(tmp_path),
        base_dir=str(tmp_path),
        model_override=orchestrator._resolve_model_for_tool(
            tool=AIProvider.COPILOT,
            agent_name="triage",
        ),
    )
    assert pid == 111
    assert captured["copilot_model"] == "global-model"
    assert captured["copilot_supports_model"] is False


def test_auto_provider_fast_profile_prefers_available_provider(monkeypatch):
    orchestrator = AIOrchestrator(
        {
            "tool_preferences": {"triage": {"provider": "auto", "profile": "fast"}},
            "model_profiles": {
                "fast": {
                    "gemini": "gemini-2.0-flash",
                    "copilot": "gpt-4o-mini",
                }
            },
        }
    )

    monkeypatch.setattr(
        orchestrator,
        "check_tool_available",
        lambda tool: tool != AIProvider.GEMINI,
    )
    assert orchestrator.get_primary_tool("triage") == AIProvider.COPILOT


def test_profile_tool_order_keeps_same_profile_fallback_chain(monkeypatch):
    orchestrator = AIOrchestrator(
        {
            "tool_preferences": {"triage": {"provider": "auto", "profile": "reasoning"}},
            "model_profiles": {
                "reasoning": {
                    "codex": "gpt-5.3-codex",
                    "copilot": "gpt-5.3-codex",
                }
            },
        }
    )

    monkeypatch.setattr(orchestrator, "check_tool_available", lambda _tool: True)
    order = orchestrator._get_tool_order(agent_name="triage", project_name=None)
    assert order == [AIProvider.CODEX, AIProvider.COPILOT]


def test_custom_profile_provider_priority_overrides_default_order(monkeypatch):
    orchestrator = AIOrchestrator(
        {
            "tool_preferences": {"triage": {"provider": "auto", "profile": "fast"}},
            "model_profiles": {
                "fast": {
                    "gemini": "gemini-2.0-flash",
                    "copilot": "gpt-4o-mini",
                    "codex": "gpt-5-mini",
                }
            },
            "profile_provider_priority": {"fast": ["codex", "copilot", "gemini"]},
        }
    )

    monkeypatch.setattr(orchestrator, "check_tool_available", lambda _tool: True)
    order = orchestrator._get_tool_order(agent_name="triage", project_name=None)
    assert order == [AIProvider.CODEX, AIProvider.COPILOT, AIProvider.GEMINI]
