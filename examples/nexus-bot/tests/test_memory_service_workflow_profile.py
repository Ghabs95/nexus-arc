import importlib
import sys
import types


def _load_memory_service_module():
    if "redis" not in sys.modules:
        redis_stub = types.ModuleType("redis")

        class _RedisClient:  # pragma: no cover - type placeholder only
            pass

        class _RedisConnectionError(Exception):
            pass

        def _from_url(*_args, **_kwargs):
            raise _RedisConnectionError("redis unavailable in test runtime")

        redis_stub.Redis = _RedisClient
        redis_stub.ConnectionError = _RedisConnectionError
        redis_stub.from_url = _from_url
        sys.modules["redis"] = redis_stub

    return importlib.import_module("nexus.core.memory")


def test_default_chat_metadata_uses_project_workflow_profile(monkeypatch):
    memory_service = _load_memory_service_module()
    monkeypatch.setattr(
        memory_service, "_get_workflow_profile", lambda project: "sampleco/workflows/master.yaml"
    )

    metadata = memory_service._default_chat_metadata("sampleco")

    assert metadata["workflow_profile"] == "sampleco/workflows/master.yaml"


def test_normalize_chat_data_replaces_generic_profile_with_project_specific(monkeypatch):
    memory_service = _load_memory_service_module()
    monkeypatch.setattr(
        memory_service, "_get_workflow_profile", lambda project: "sampleco/workflows/master.yaml"
    )
    monkeypatch.setattr(
        memory_service, "_get_chat_agent_types", lambda project: ["business", "marketing"]
    )

    normalized = memory_service._normalize_chat_data(
        {
            "id": "chat1",
            "metadata": {
                "project_key": "sampleco",
                "workflow_profile": "default_workflow",
            },
        }
    )

    metadata = normalized["metadata"]
    assert metadata["project_key"] == "sampleco"
    assert metadata["workflow_profile"] == "sampleco/workflows/master.yaml"
    assert metadata["primary_agent_type"] == "business"
