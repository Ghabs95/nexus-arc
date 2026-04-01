import asyncio

from nexus.core.orchestration import nexus_core_helpers as helpers


def test_resolve_project_name_for_repo_matches_config(monkeypatch):
    monkeypatch.setattr(
        helpers,
        "_get_project_config",
        lambda: {
            "example-org": {"workspace": "../example-org", "repos": ["example-org/example-project"]},
            "nexus": {"workspace": ".", "repos": ["ghabs-org/nexus-arc"]},
        },
    )
    monkeypatch.setattr(
        helpers,
        "get_repos",
        lambda project: ["example-org/example-project"]
        if project == "example-org"
        else ["ghabs-org/nexus-arc"],
    )
    monkeypatch.setattr(helpers, "get_default_project", lambda: "nexus")
    monkeypatch.setattr(helpers, "get_repo", lambda project: "ghabs-org/nexus-arc")

    assert helpers.resolve_project_name_for_repo("example-org/example-project") == "example-org"


def test_can_access_issue_context_denies_on_project_acl(monkeypatch):
    class _CredStore:
        @staticmethod
        def get_issue_requester(_repo, _issue):
            return "nexus-user-1"

    class _Auth:
        @staticmethod
        def auth_enabled():
            return True

        @staticmethod
        def has_project_access(_nexus_id, _project, auto_sync=True):
            return False

    monkeypatch.setattr("nexus.core.auth.access_domain.auth_enabled", _Auth.auth_enabled)
    monkeypatch.setattr("nexus.core.auth.has_project_access", _Auth.has_project_access)
    monkeypatch.setattr(
        "nexus.core.auth.credential_store.get_issue_requester",
        _CredStore.get_issue_requester,
    )

    allowed, reason = helpers.can_access_issue_context(
        nexus_id="nexus-user-1",
        issue_number="42",
        repo="example-org/example-project",
        project_name="example-org",
    )

    assert allowed is False
    assert reason == "project-access-denied"


def test_can_access_issue_context_denies_on_requester_mismatch(monkeypatch):
    monkeypatch.setattr("nexus.core.auth.access_domain.auth_enabled", lambda: True)
    monkeypatch.setattr("nexus.core.auth.has_project_access", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        "nexus.core.auth.credential_store.get_issue_requester",
        lambda _repo, _issue: "nexus-user-other",
    )

    allowed, reason = helpers.can_access_issue_context(
        nexus_id="nexus-user-1",
        issue_number="42",
        repo="example-org/example-project",
        project_name="example-org",
    )

    assert allowed is False
    assert reason == "requester-mismatch"


def test_get_workflow_context_returns_status_and_audit(monkeypatch):
    async def _fake_status(_issue_number):
        return {"workflow_id": "wf-1", "state": "running"}

    monkeypatch.setattr(helpers, "get_workflow_status", _fake_status)
    monkeypatch.setattr(
        helpers.AuditStore,
        "get_audit_history",
        lambda issue_num, limit=25: [{"issue": issue_num, "limit": limit}],
    )

    result = asyncio.run(helpers.get_workflow_context("42", audit_limit=15))

    assert result is not None
    assert result["issue_number"] == "42"
    assert result["workflow"]["workflow_id"] == "wf-1"
    assert result["audit"][0]["issue"] == 42
    assert result["audit"][0]["limit"] == 15
