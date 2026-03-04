from nexus.core.processor_loops import run_processor_loop


class _FakeTime:
    def __init__(self):
        self.now = 0.0
        self.sleeps = 0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps += 1
        self.now += float(seconds)
        if self.sleeps >= 2:
            raise RuntimeError("stop-loop")


def test_run_processor_loop_filesystem_and_periodic_checks():
    calls = {
        "fs": 0,
        "pg": 0,
        "stuck": 0,
        "comments": 0,
        "completed": 0,
        "mergeq": 0,
        "stale": 0,
    }
    fake_time = _FakeTime()

    try:
        run_processor_loop(
            logger=None,
            base_dir="/tmp/base",
            sleep_interval=61,
            check_interval=60,
            get_inbox_storage_backend=lambda: "filesystem",
            drain_postgres_inbox_queue=lambda: calls.__setitem__("pg", calls["pg"] + 1),
            process_filesystem_inbox_once=lambda _base: calls.__setitem__("fs", calls["fs"] + 1),
            check_stuck_agents=lambda: calls.__setitem__("stuck", calls["stuck"] + 1),
            check_agent_comments=lambda: calls.__setitem__("comments", calls["comments"] + 1),
            check_completed_agents=lambda: calls.__setitem__("completed", calls["completed"] + 1),
            merge_queue_auto_merge_once=lambda: calls.__setitem__("mergeq", calls["mergeq"] + 1),
            cleanup_stale_worktrees_once=lambda: calls.__setitem__("stale", calls["stale"] + 1),
            time_module=fake_time,
        )
    except RuntimeError as exc:
        assert str(exc) == "stop-loop"

    assert calls["fs"] == 2
    assert calls["pg"] == 0
    assert calls["stuck"] == 1
    assert calls["comments"] == 1
    assert calls["completed"] == 1
    assert calls["mergeq"] == 1
    assert calls["stale"] == 1


def test_run_processor_loop_postgres_path():
    calls = {"fs": 0, "pg": 0}
    fake_time = _FakeTime()

    try:
        run_processor_loop(
            logger=None,
            base_dir="/tmp/base",
            sleep_interval=1,
            check_interval=999,
            get_inbox_storage_backend=lambda: "postgres",
            drain_postgres_inbox_queue=lambda: calls.__setitem__("pg", calls["pg"] + 1),
            process_filesystem_inbox_once=lambda _base: calls.__setitem__("fs", calls["fs"] + 1),
            check_stuck_agents=lambda: None,
            check_agent_comments=lambda: None,
            check_completed_agents=lambda: None,
            merge_queue_auto_merge_once=lambda: None,
            cleanup_stale_worktrees_once=lambda: None,
            time_module=fake_time,
        )
    except RuntimeError as exc:
        assert str(exc) == "stop-loop"

    assert calls["pg"] == 2
    assert calls["fs"] == 0
