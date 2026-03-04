from nexus.core.processor_runtime_state import ProcessorRuntimeState


def test_processor_runtime_state_defaults_are_isolated():
    a = ProcessorRuntimeState()
    b = ProcessorRuntimeState()

    a.notified_comments.add(1)
    a.polling_failure_counts["x"] = 2

    assert 1 in a.notified_comments
    assert 1 not in b.notified_comments
    assert "x" not in b.polling_failure_counts
