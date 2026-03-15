"""Tests for the completion protocol module."""

import json
import os

from nexus.core.completion import (
    CompletionSummary,
    DetectedCompletion,
    budget_completion_payload,
    build_completion_comment,
    generate_completion_instructions,
    normalize_completion_comment_markdown,
    scan_for_completions,
)
from nexus.core.completion_store import CompletionStore


# ---------------------------------------------------------------------------
# CompletionSummary
# ---------------------------------------------------------------------------


class TestCompletionSummary:
    def test_from_dict_minimal(self):
        data = {"status": "complete", "agent_type": "triage"}
        s = CompletionSummary.from_dict(data)
        assert s.status == "complete"
        assert s.agent_type == "triage"
        assert s.step_id == ""
        assert s.step_num == 0
        assert s.summary == ""
        assert s.key_findings == []
        assert s.next_agent == ""

    def test_from_dict_full(self):
        data = {
            "status": "complete",
            "agent_type": "debug",
            "step_id": "develop",
            "step_num": 2,
            "summary": "Fixed the bug",
            "key_findings": ["root cause found", "test added"],
            "next_agent": "summarizer",
            "verdict": "All clear",
            "effort_breakdown": {"analysis": "30min", "fix": "1h"},
        }
        s = CompletionSummary.from_dict(data)
        assert s.step_id == "develop"
        assert s.step_num == 2
        assert s.summary == "Fixed the bug"
        assert len(s.key_findings) == 2
        assert s.next_agent == "summarizer"
        assert s.verdict == "All clear"
        assert s.effort_breakdown["analysis"] == "30min"

    def test_is_workflow_done_true_variants(self):
        for val in ["none", "None", "N/A", "null", "end", "done", "finish", "complete", ""]:
            s = CompletionSummary(next_agent=val)
            assert s.is_workflow_done is True, f"Expected done for next_agent={val!r}"

    def test_is_workflow_done_false(self):
        s = CompletionSummary(next_agent="summarizer")
        assert s.is_workflow_done is False

    def test_to_dict_round_trip(self):
        original = CompletionSummary(
            status="complete",
            agent_type="design",
            step_id="design",
            step_num=3,
            summary="Created design doc",
            key_findings=["finding1"],
            next_agent="code_reviewer",
            verdict="Approved",
        )
        d = original.to_dict()
        restored = CompletionSummary.from_dict(d)
        assert restored.agent_type == original.agent_type
        assert restored.next_agent == original.next_agent
        assert restored.verdict == original.verdict

    def test_from_dict_budgets_verbose_payload_fields(self):
        huge_line = "tool output " * 400
        data = {
            "status": "complete",
            "agent_type": "debug",
            "summary": huge_line,
            "key_findings": [f"finding {idx} " + huge_line for idx in range(20)],
            "next_agent": "reviewer",
        }
        s = CompletionSummary.from_dict(data)
        assert len(s.summary) <= 900
        assert len(s.key_findings) <= 9
        assert "omitted for budget" in s.key_findings[-1]

    def test_budget_completion_payload_trims_extra_large_string_fields(self):
        payload = budget_completion_payload(
            {
                "status": "complete",
                "agent_type": "developer",
                "summary": "ok",
                "huge_debug_blob": "x" * 5000,
            }
        )
        assert len(payload["huge_debug_blob"]) <= 1200

    def test_budget_completion_payload_normalizes_escaped_comment_markdown(self):
        payload = budget_completion_payload(
            {
                "status": "complete",
                "agent_type": "triage",
                "comment_markdown": "## Triage Complete\\n\\n- Finding 1\\n- Finding 2",
            }
        )
        assert payload["comment_markdown"] == "## Triage Complete\n\n- Finding 1\n- Finding 2"

    def test_normalize_completion_comment_markdown_keeps_single_escaped_sequence(self):
        text = normalize_completion_comment_markdown(r"Windows path: C:\new")
        assert text == r"Windows path: C:\new"

    def test_alignment_fields_round_trip(self):
        original = CompletionSummary(
            status="complete",
            agent_type="designer",
            step_id="designer",
            step_num=3,
            alignment_score=0.76,
            alignment_summary="Strong alignment to ADR docs.",
            alignment_artifacts=["docs/ADR-001.md", "README.md"],
        )
        d = original.to_dict()
        restored = CompletionSummary.from_dict(d)
        assert restored.alignment_score == 0.76
        assert restored.alignment_summary == "Strong alignment to ADR docs."
        assert restored.alignment_artifacts == ["docs/ADR-001.md", "README.md"]


# ---------------------------------------------------------------------------
# build_completion_comment
# ---------------------------------------------------------------------------


class TestBuildCompletionComment:
    def test_basic_comment(self):
        s = CompletionSummary(summary="Analyzed the issue", agent_type="triage")
        comment = build_completion_comment(s)
        assert "## ✅ Agent Complete — triage" in comment
        assert "Analyzed the issue" in comment

    def test_includes_key_findings(self):
        s = CompletionSummary(key_findings=["bug in auth", "missing test"])
        comment = build_completion_comment(s)
        assert "bug in auth" in comment
        assert "missing test" in comment

    def test_includes_step_identity(self):
        s = CompletionSummary(step_id="develop", step_num=2)
        comment = build_completion_comment(s)
        assert "**Step ID:** `develop`" in comment
        assert "**Step Num:** 2" in comment

    def test_includes_next_agent(self):
        s = CompletionSummary(next_agent="summarizer")
        comment = build_completion_comment(s)
        assert "Ready for **@Summarizer**" in comment

    def test_omits_next_when_workflow_done(self):
        s = CompletionSummary(next_agent="none")
        comment = build_completion_comment(s)
        assert "@none" not in comment

    def test_includes_verdict(self):
        s = CompletionSummary(verdict="Ship it")
        comment = build_completion_comment(s)
        assert "Ship it" in comment

    def test_includes_effort_breakdown(self):
        s = CompletionSummary(effort_breakdown={"review": "2h"})
        comment = build_completion_comment(s)
        assert "review: 2h" in comment


# ---------------------------------------------------------------------------
# generate_completion_instructions
# ---------------------------------------------------------------------------


class TestGenerateCompletionInstructions:
    def test_contains_issue_number(self):
        text = generate_completion_instructions("42", "debug", "develop", 2)
        assert "completion_summary_42.json" in text

    def test_contains_agent_type(self):
        text = generate_completion_instructions("1", "triage", "triage", 1)
        assert "agent_type" in text
        assert "triage" in text

    def test_contains_workflow_steps(self):
        steps = "**Workflow Steps:**\n- 1. Triage — triage"
        text = generate_completion_instructions("1", "triage", "triage", 1, workflow_steps_text=steps)
        assert "Workflow Steps" in text

    def test_custom_nexus_dir(self):
        text = generate_completion_instructions(
            "1", "triage", "triage", 1, project_name="myproject", nexus_dir=".custom"
        )
        assert ".custom/tasks/myproject/completions" in text

    def test_uses_python_command_instead_of_heredoc(self):
        text = generate_completion_instructions("1", "triage", "triage", 1)
        assert "python3 -c" in text
        assert "NEXUS_EOF" not in text

    def test_forbids_ready_for_none_in_comments(self):
        text = generate_completion_instructions("1", "writer", "close_loop", 4)
        assert "Never write `@none`" in text
        assert "omit this line when next_agent is terminal/`none`" in text
        assert "Do NOT post issue comments directly" in text

    def test_removes_legacy_summarizer_example(self):
        text = generate_completion_instructions("1", "writer", "close_loop", 4)
        assert "Summarize & Close" not in text
        assert "`summarizer`" not in text

    def test_defaults_project_name_to_nexus_path(self):
        text = generate_completion_instructions(
            "1", "triage", "triage", 1, project_name="", nexus_dir=".nexus"
        )
        assert ".nexus/tasks/nexus/completions" in text
        assert ".nexus/tasks//completions" not in text

    def test_postgres_uses_endpoint_placeholder_when_missing_webhook_url(self):
        text = generate_completion_instructions(
            "1",
            "triage",
            "triage",
            1,
            completion_backend="postgres",
            webhook_url="",
        )
        assert "<WEBHOOK_BASE_URL>/api/v1/completion" in text
        assert "Use this command template" in text
        assert '"step_id": "triage"' in text
        assert '"step_num": 1' in text

    def test_postgres_includes_filesystem_fallback_instructions(self):
        text = generate_completion_instructions(
            "119",
            "developer",
            "develop",
            4,
            completion_backend="postgres",
            webhook_url="http://webhook:8081",
        )
        assert "If POST fails" in text
        assert "completion_summary_119.json" in text
        assert "fallback completion files are ingested automatically" in text
        assert "Do NOT stop after a failed POST without writing fallback JSON" in text

    def test_completion_markdown_template_uses_json_newline_escape(self):
        text = generate_completion_instructions("1", "triage", "triage", 1)
        assert "\\\\n" not in text
        assert "\\n\\n**Step ID:**" in text


# ---------------------------------------------------------------------------
# scan_for_completions
# ---------------------------------------------------------------------------


class TestScanForCompletions:
    def test_finds_completion_files(self, tmp_path):
        # Create valid structure: base/.nexus/tasks/myproject/completions/completion_summary_42.json
        completion_dir = tmp_path / ".nexus" / "tasks" / "myproject" / "completions"
        completion_dir.mkdir(parents=True)
        data = {"status": "complete", "agent_type": "debug", "summary": "done"}
        (completion_dir / "completion_summary_42.json").write_text(json.dumps(data))

        results = scan_for_completions(str(tmp_path))
        assert len(results) == 1
        assert results[0].issue_number == "42"
        assert results[0].summary.agent_type == "debug"

    def test_ignores_invalid_json(self, tmp_path):
        completion_dir = tmp_path / ".nexus" / "tasks" / "myproject" / "completions"
        completion_dir.mkdir(parents=True)
        (completion_dir / "completion_summary_99.json").write_text("not json{")

        results = scan_for_completions(str(tmp_path))
        assert len(results) == 0

    def test_custom_nexus_dir(self, tmp_path):
        completion_dir = tmp_path / ".custom" / "tasks" / "myproject" / "completions"
        completion_dir.mkdir(parents=True)
        data = {"status": "complete", "agent_type": "triage"}
        (completion_dir / "completion_summary_7.json").write_text(json.dumps(data))

        results = scan_for_completions(str(tmp_path), nexus_dir=".custom")
        assert len(results) == 1
        assert results[0].issue_number == "7"

    def test_dedup_key_includes_agent_type(self, tmp_path):
        completion_dir = tmp_path / ".nexus" / "tasks" / "myproject" / "completions"
        completion_dir.mkdir(parents=True)
        data = {"status": "complete", "agent_type": "debug"}
        (completion_dir / "completion_summary_10.json").write_text(json.dumps(data))

        results = scan_for_completions(str(tmp_path))
        assert results[0].dedup_key == "10:unknown-workflow:unknown-step:0:debug"

    def test_uses_newest_completion_per_issue_across_paths(self, tmp_path):
        older_dir = tmp_path / ".nexus" / "tasks" / "project_a" / "completions"
        newer_dir = tmp_path / "repo" / ".nexus" / "tasks" / "project_b" / "completions"
        older_dir.mkdir(parents=True)
        newer_dir.mkdir(parents=True)

        older = older_dir / "completion_summary_44.json"
        newer = newer_dir / "completion_summary_44.json"

        older.write_text(json.dumps({"status": "complete", "agent_type": "deployer"}))
        newer.write_text(json.dumps({"status": "complete", "agent_type": "writer"}))

        older_ts = 1_700_000_000
        newer_ts = 1_700_000_100
        os.utime(older, (older_ts, older_ts))
        os.utime(newer, (newer_ts, newer_ts))

        results = scan_for_completions(str(tmp_path))
        assert len(results) == 1
        assert results[0].issue_number == "44"
        assert results[0].summary.agent_type == "writer"
        assert str(results[0].file_path).endswith(
            "repo/.nexus/tasks/project_b/completions/completion_summary_44.json"
        )

    def test_falls_back_to_older_valid_when_newest_invalid(self, tmp_path):
        older_dir = tmp_path / ".nexus" / "tasks" / "project_a" / "completions"
        newer_dir = tmp_path / "repo" / ".nexus" / "tasks" / "project_b" / "completions"
        older_dir.mkdir(parents=True)
        newer_dir.mkdir(parents=True)

        older = older_dir / "completion_summary_52.json"
        newer = newer_dir / "completion_summary_52.json"

        older.write_text(json.dumps({"status": "complete", "agent_type": "reviewer"}))
        newer.write_text("not-json")

        older_ts = 1_700_000_000
        newer_ts = 1_700_000_100
        os.utime(older, (older_ts, older_ts))
        os.utime(newer, (newer_ts, newer_ts))

        results = scan_for_completions(str(tmp_path))
        assert len(results) == 1
        assert results[0].issue_number == "52"
        assert results[0].summary.agent_type == "reviewer"


# ---------------------------------------------------------------------------
# DetectedCompletion
# ---------------------------------------------------------------------------


class TestDetectedCompletion:
    def test_dedup_key_format(self):
        s = CompletionSummary(agent_type="design", workflow_id="wf-5", step_id="build")
        d = DetectedCompletion(
            file_path="/some/path/completion_summary_5.json",
            issue_number="5",
            summary=s,
        )
        assert d.dedup_key == "5:wf-5:build:0:design"

    def test_dedup_key_includes_step_num_for_repeated_step_ids(self):
        first = CompletionSummary(
            agent_type="developer",
            workflow_id="wf-119",
            step_id="develop",
            step_num=4,
        )
        second = CompletionSummary(
            agent_type="developer",
            workflow_id="wf-119",
            step_id="develop",
            step_num=6,
        )

        first_detected = DetectedCompletion(
            file_path="/tmp/completion_summary_119.json",
            issue_number="119",
            summary=first,
        )
        second_detected = DetectedCompletion(
            file_path="/tmp/completion_summary_119.json",
            issue_number="119",
            summary=second,
        )

        assert first_detected.dedup_key == "119:wf-119:develop:4:developer"
        assert second_detected.dedup_key == "119:wf-119:develop:6:developer"


class _FakeCompletionStorage:
    def __init__(self, rows):
        self.rows = rows

    async def list_completions(self, issue_number=None):
        return self.rows


class TestCompletionStore:
    def test_postgres_scan_uses_step_identity_for_dedup(self):
        row_v1 = {
            "_db_id": 10,
            "_issue_number": "113",
            "_agent_type": "developer",
            "issue_number": "113",
            "agent_type": "developer",
            "status": "complete",
            "summary": "v1",
            "next_agent": "reviewer",
            "_updated_at": "2026-03-09T00:06:03.000000+00:00",
        }
        storage = _FakeCompletionStorage([row_v1])
        store = CompletionStore(backend="postgres", storage=storage)
        first = store.scan("113")
        assert len(first) == 1
        first_key = first[0].dedup_key

        row_v2 = {
            **row_v1,
            "summary": "v2",
            "_updated_at": "2026-03-09T00:08:29.000000+00:00",
        }
        storage.rows = [row_v2]
        second = store.scan("113")
        assert len(second) == 1
        second_key = second[0].dedup_key

        assert first_key == "113:unknown-workflow:unknown-step:0:developer"
        assert second_key == "113:unknown-workflow:unknown-step:0:developer"

    def test_postgres_scan_includes_filesystem_fallback(self, tmp_path):
        storage = _FakeCompletionStorage([])
        completion_dir = tmp_path / ".nexus" / "tasks" / "nexus" / "completions"
        completion_dir.mkdir(parents=True)
        (completion_dir / "completion_summary_119.json").write_text(
            json.dumps(
                {
                    "status": "complete",
                    "agent_type": "developer",
                    "step_id": "develop",
                    "step_num": 4,
                    "summary": "fallback",
                    "next_agent": "reviewer",
                }
            )
        )

        store = CompletionStore(
            backend="postgres",
            storage=storage,
            base_dir=str(tmp_path),
            nexus_dir=".nexus",
        )
        detected = store.scan("119")
        assert len(detected) == 1
        assert detected[0].issue_number == "119"
        assert detected[0].summary.agent_type == "developer"

    def test_postgres_scan_dedups_db_and_filesystem_same_step(self, tmp_path):
        row = {
            "_db_id": 10,
            "_issue_number": "119",
            "_agent_type": "developer",
            "issue_number": "119",
            "agent_type": "developer",
            "status": "complete",
            "summary": "db",
            "next_agent": "reviewer",
            "step_id": "develop",
            "step_num": 4,
            "_updated_at": "2026-03-15T22:10:00.000000+00:00",
        }
        storage = _FakeCompletionStorage([row])
        completion_dir = tmp_path / ".nexus" / "tasks" / "nexus" / "completions"
        completion_dir.mkdir(parents=True)
        (completion_dir / "completion_summary_119.json").write_text(
            json.dumps(
                {
                    "status": "complete",
                    "agent_type": "developer",
                    "step_id": "develop",
                    "step_num": 4,
                    "summary": "filesystem",
                    "next_agent": "reviewer",
                }
            )
        )

        store = CompletionStore(
            backend="postgres",
            storage=storage,
            base_dir=str(tmp_path),
            nexus_dir=".nexus",
        )
        detected = store.scan("119")
        assert len(detected) == 1
        assert detected[0].summary.summary == "db"
