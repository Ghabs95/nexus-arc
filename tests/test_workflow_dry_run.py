"""Tests for WorkflowDefinition.dry_run validation and simulation."""

from nexus.core.models import DryRunReport
from nexus.core.workflow import WorkflowDefinition

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_workflow(**overrides):
    """Return a minimal valid workflow dict."""
    base = {
        "metadata": {"name": "Test Workflow"},
        "steps": [
            {"id": "step1", "name": "Step One", "agent_type": "triage"},
            {"id": "step2", "name": "Step Two", "agent_type": "developer", "on_success": "step3"},
            {"id": "step3", "name": "Step Three", "agent_type": "reviewer", "final_step": True},
        ],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# DryRunReport model
# ---------------------------------------------------------------------------


class TestDryRunReport:
    def test_is_valid_no_errors(self):
        report = DryRunReport(errors=[], predicted_flow=["RUN  step1 (triage)"])
        assert report.is_valid is True

    def test_is_valid_with_errors(self):
        report = DryRunReport(errors=["something broken"], predicted_flow=[])
        assert report.is_valid is False


# ---------------------------------------------------------------------------
# Validation checks
# ---------------------------------------------------------------------------


class TestDryRunValidation:
    def test_valid_workflow_returns_no_errors(self):
        report = WorkflowDefinition.dry_run(_minimal_workflow())
        assert report.is_valid, f"Unexpected errors: {report.errors}"

    def test_missing_name_and_id(self):
        data = {
            "steps": [{"id": "s1", "agent_type": "triage"}],
        }
        report = WorkflowDefinition.dry_run(data)
        assert any("name" in e or "id" in e for e in report.errors)

    def test_empty_steps_list(self):
        data = {"metadata": {"name": "Broken"}, "steps": []}
        report = WorkflowDefinition.dry_run(data)
        assert any("steps" in e.lower() or "No steps" in e for e in report.errors)

    def test_missing_workflow_type_tier(self):
        # Requesting a tier that doesn't exist should yield an error
        data = {"metadata": {"name": "Tiered"}, "full_workflow": {"steps": [{"id": "s1", "agent_type": "dev"}]}}
        report = WorkflowDefinition.dry_run(data, workflow_type="fast-track")
        assert not report.is_valid

    def test_step_missing_agent_type(self):
        data = {
            "name": "Bad",
            "steps": [
                {"id": "s1", "name": "Step 1"},  # no agent_type
            ],
        }
        report = WorkflowDefinition.dry_run(data)
        assert any("agent_type" in e for e in report.errors)

    def test_invalid_on_success_reference(self):
        data = {
            "metadata": {"name": "Bad Ref"},
            "steps": [
                {"id": "s1", "agent_type": "triage", "on_success": "nonexistent"},
            ],
        }
        report = WorkflowDefinition.dry_run(data)
        assert any("nonexistent" in e for e in report.errors)

    def test_malformed_condition(self):
        data = {
            "metadata": {"name": "Bad Cond"},
            "steps": [
                {"id": "s1", "agent_type": "triage", "condition": "result =="},
            ],
        }
        report = WorkflowDefinition.dry_run(data)
        assert any("condition" in e for e in report.errors)

    def test_valid_condition_no_error(self):
        data = {
            "name": "Good Cond",
            "steps": [
                {"id": "s1", "agent_type": "triage", "condition": "result.get('tier') == 'high'"},
            ],
        }
        report = WorkflowDefinition.dry_run(data)
        assert not any("condition" in e for e in report.errors)

    def test_non_dict_input(self):
        report = WorkflowDefinition.dry_run("not a dict")  # type: ignore[arg-type]
        assert not report.is_valid

    def test_returns_dry_run_report_instance(self):
        report = WorkflowDefinition.dry_run(_minimal_workflow())
        assert isinstance(report, DryRunReport)


# ---------------------------------------------------------------------------
# Simulation / predicted_flow
# ---------------------------------------------------------------------------


class TestDryRunSimulation:
    def test_predicted_flow_populated(self):
        report = WorkflowDefinition.dry_run(_minimal_workflow())
        assert len(report.predicted_flow) > 0

    def test_all_unconditional_steps_marked_run(self):
        report = WorkflowDefinition.dry_run(_minimal_workflow())
        for entry in report.predicted_flow:
            assert entry.startswith("RUN")

    def test_false_condition_marked_skip(self):
        data = {
            "metadata": {"name": "Conditional"},
            "steps": [
                {"id": "s1", "agent_type": "triage"},
                {"id": "s2", "agent_type": "developer", "condition": "1 == 2"},
            ],
        }
        report = WorkflowDefinition.dry_run(data)
        assert any("SKIP" in e for e in report.predicted_flow)

    def test_true_condition_marked_run(self):
        data = {
            "metadata": {"name": "Conditional"},
            "steps": [
                {"id": "s1", "agent_type": "triage"},
                {"id": "s2", "agent_type": "developer", "condition": "1 == 1"},
            ],
        }
        report = WorkflowDefinition.dry_run(data)
        assert all("SKIP" not in e for e in report.predicted_flow)

    def test_name_error_condition_treated_as_run(self):
        # Condition references an output variable not yet available → should be RUN
        data = {
            "metadata": {"name": "Name Error"},
            "steps": [
                {"id": "s1", "agent_type": "triage", "condition": "result['tier'] == 'high'"},
            ],
        }
        report = WorkflowDefinition.dry_run(data)
        assert any("RUN" in e for e in report.predicted_flow)

    def test_router_steps_excluded_from_predicted_flow(self):
        data = {
            "metadata": {"name": "With Router"},
            "steps": [
                {"id": "s1", "agent_type": "triage", "on_success": "router1"},
                {
                    "id": "router1",
                    "agent_type": "router",
                    "routes": [{"when": "x == 1", "then": "s2"}, {"default": "s2"}],
                },
                {"id": "s2", "agent_type": "developer"},
            ],
        }
        report = WorkflowDefinition.dry_run(data)
        assert not any("router" in e for e in report.predicted_flow)

    def test_tiered_workflow_dry_run(self):
        data = {
            "metadata": {"name": "Tiered"},
            "fast_track_workflow": {
                "steps": [
                    {"id": "t1", "agent_type": "triage"},
                    {"id": "d1", "agent_type": "developer"},
                ]
            },
        }
        report = WorkflowDefinition.dry_run(data, workflow_type="fast-track")
        assert report.is_valid
        assert len(report.predicted_flow) == 2
