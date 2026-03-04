from nexus.core.mermaid_render_service import build_mermaid_diagram


def test_build_mermaid_diagram_basic():
    steps = [
        {"name": "triage", "agent": {"name": "triage-agent"}, "status": "complete"},
        {"name": "design", "agent": {"name": "designer-agent"}, "status": "running"},
        {"name": "dev", "agent": {"name": "developer-agent"}, "status": "pending"},
    ]
    diagram = build_mermaid_diagram(steps, "73")
    assert "flowchart TD" in diagram
    assert 'I["Issue #73"]' in diagram
    assert "S1" in diagram
    assert "S2" in diagram
    assert "S3" in diagram
    assert "✅ complete" in diagram
    assert "▶️ running" in diagram
    assert "⏳ pending" in diagram


def test_build_mermaid_diagram_empty():
    diagram = build_mermaid_diagram([], "73")
    assert "flowchart TD" in diagram
    assert 'I["Issue #73"]' in diagram
    assert "S1" not in diagram


def test_build_mermaid_diagram_quote_escaping():
    steps = [
        {"name": "triage", "agent": {"name": 'Agent "Special"'}, "status": "complete"},
    ]
    diagram = build_mermaid_diagram(steps, "73")
    # Verified behavior: quotes are replaced with single quotes
    assert "Agent 'Special'" in diagram
