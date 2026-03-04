import logging
from pathlib import Path

from nexus.core.inbox.inbox_routing_service import (
    process_inbox_task_request,
    save_resolved_inbox_task_request,
)


def _render_task_markdown(**kwargs):
    return f"{kwargs['project']}|{kwargs['task_type']}|{kwargs['task_name']}|{kwargs['content']}|{kwargs['raw_text']}"


def test_process_inbox_task_request_uses_project_hint_and_saves_file(tmp_path: Path):
    class _Orch:
        def run_text_to_speech_analysis(self, **kwargs):
            raise AssertionError("classification should be skipped")

    out = process_inbox_task_request(
        text="hello",
        orchestrator=_Orch(),
        message_id_or_unique_id="123",
        project_hint="acme",
        logger=logging.getLogger("test"),
        normalize_project_key=lambda s: s.strip().lower() if s else None,
        projects={"acme": "Acme"},
        project_config={"acme": {"workspace": "ws"}},
        types_map={"feature": "Feature"},
        parse_classification_result=lambda r: r,
        refine_task_description=lambda content, project: f"{project}:{content}",
        generate_task_name=lambda *_a, **_k: "auto-name",
        normalize_task_name=lambda s: str(s or "").strip(),
        render_task_markdown=_render_task_markdown,
        get_inbox_storage_backend=lambda: "file",
        enqueue_task=lambda **_k: 1,
        base_dir=str(tmp_path),
        get_inbox_dir=lambda root, project: str(tmp_path / "inbox" / project),
    )

    assert out["success"] is True
    assert out["project"] == "acme"
    saved = (tmp_path / "inbox" / "acme" / "task_123.md").read_text()
    assert "acme|feature|auto-name|acme:hello|hello" == saved


def test_process_inbox_task_request_returns_pending_resolution_on_unknown_project():
    class _Orch:
        def run_text_to_speech_analysis(self, **kwargs):
            return {"project": "unknown", "type": "feature", "text": "x"}

    out = process_inbox_task_request(
        text="hello",
        orchestrator=_Orch(),
        message_id_or_unique_id="123",
        project_hint=None,
        logger=logging.getLogger("test"),
        normalize_project_key=lambda s: s.strip().lower() if s else None,
        projects={"acme": "Acme"},
        project_config={},
        types_map={"feature": "Feature"},
        parse_classification_result=lambda r: r,
        refine_task_description=lambda content, project: content,
        generate_task_name=lambda *_a, **_k: "auto-name",
        normalize_task_name=lambda s: str(s or "").strip(),
        render_task_markdown=_render_task_markdown,
        get_inbox_storage_backend=lambda: "file",
        enqueue_task=lambda **_k: 1,
        base_dir="/tmp",
        get_inbox_dir=lambda root, project: f"/tmp/{project}",
    )
    assert out["success"] is False
    assert "pending_resolution" in out


def test_save_resolved_inbox_task_request_file_backend(tmp_path: Path):
    out = save_resolved_inbox_task_request(
        pending_project={
            "raw_text": "raw",
            "content": "content",
            "task_type": "feature",
            "task_name": "n",
        },
        selected_project="acme",
        message_id_or_unique_id="8",
        normalize_project_key=lambda s: s.strip().lower() if s else None,
        get_inbox_storage_backend=lambda: "file",
        types_map={"feature": "Feature"},
        project_config={"acme": {"workspace": "ws"}},
        refine_task_description=lambda content, project: f"{project}:{content}",
        render_task_markdown=_render_task_markdown,
        enqueue_task=lambda **_k: 1,
        base_dir=str(tmp_path),
        get_inbox_dir=lambda root, project: str(tmp_path / "inbox" / project),
        logger=logging.getLogger("test"),
    )
    assert out["success"] is True
    assert (tmp_path / "inbox" / "acme" / "task_8.md").exists()
