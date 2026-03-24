from nexus.core.config.runtime import (
    bridge_requires_authorized_sender,
    chat_metadata_backend,
    chat_transcript_persistence_enabled,
    default_rate_limit_backend,
    normalize_auth_authority,
    normalize_chat_transcript_owner,
    normalize_runtime_mode,
)


def test_normalize_runtime_mode_defaults_to_standalone():
    assert normalize_runtime_mode("") == "standalone"
    assert normalize_runtime_mode("unknown") == "standalone"


def test_normalize_runtime_mode_accepts_known_values():
    assert normalize_runtime_mode("openclaw") == "openclaw"
    assert normalize_runtime_mode("ADVANCED") == "advanced"


def test_normalize_chat_transcript_owner_defaults_by_runtime_mode():
    assert normalize_chat_transcript_owner("", "standalone") == "nexus"
    assert normalize_chat_transcript_owner("", "openclaw") == "openclaw"


def test_normalize_chat_transcript_owner_accepts_explicit_values():
    assert normalize_chat_transcript_owner("split", "advanced") == "split"
    assert normalize_chat_transcript_owner("nexus", "openclaw") == "nexus"


def test_chat_transcript_persistence_enabled_only_for_nexus_owner():
    assert chat_transcript_persistence_enabled("", "standalone") is True
    assert chat_transcript_persistence_enabled("", "openclaw") is False
    assert chat_transcript_persistence_enabled("split", "advanced") is False


def test_chat_metadata_backend_tracks_transcript_owner():
    assert chat_metadata_backend("", "standalone") == "redis"
    assert chat_metadata_backend("", "openclaw") == "filesystem"
    assert chat_metadata_backend("split", "advanced") == "filesystem"


def test_normalize_auth_authority_defaults_by_runtime_mode():
    assert normalize_auth_authority("", "standalone") == "nexus"
    assert normalize_auth_authority("", "openclaw") == "openclaw"
    assert normalize_auth_authority("OPENCLAW", "advanced") == "openclaw"


def test_bridge_requires_authorized_sender_when_openclaw_owns_auth():
    assert bridge_requires_authorized_sender("", "standalone") is False
    assert bridge_requires_authorized_sender("", "openclaw") is True
    assert bridge_requires_authorized_sender("openclaw", "advanced") is True


def test_default_rate_limit_backend_tracks_runtime_ownership():
    assert default_rate_limit_backend("filesystem", runtime_mode="standalone") == "redis"
    assert default_rate_limit_backend("filesystem", runtime_mode="openclaw") == "filesystem"
    assert default_rate_limit_backend("postgres", runtime_mode="openclaw") == "database"
    assert (
        default_rate_limit_backend(
            "postgres",
            runtime_mode="advanced",
            transcript_owner="split",
        )
        == "database"
    )
