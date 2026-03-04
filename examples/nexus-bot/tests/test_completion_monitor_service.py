from nexus.core.completion_monitor_service import run_completion_monitor_cycle


def test_run_completion_monitor_cycle_invokes_delegate():
    calls = {"n": 0}

    run_completion_monitor_cycle(
        post_completion_comments_from_logs=lambda: calls.__setitem__("n", calls["n"] + 1)
    )

    assert calls["n"] == 1
