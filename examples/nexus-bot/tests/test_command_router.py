from nexus.core.utils.command_args import normalize_command_args


def test_normalize_command_args_issue_command():
    # Issue commands should have [project, issue, extras]
    args = normalize_command_args("status", "proj-a", "42", ["tail"])
    assert args == ["proj-a", "42", "tail"]


def test_normalize_command_args_project_command():
    # Project-only commands should skip the issue number
    # Even if an issue number is provided, it should be ignored by the normalization logic
    # if the command is in the PROJECT_ONLY_COMMANDS list.
    args = normalize_command_args("agents", "proj-a", "42", ["extra"])
    assert args == ["proj-a", "extra"]


def test_normalize_command_args_with_none_rest():
    args = normalize_command_args("status", "proj-a", "123", None)
    assert args == ["proj-a", "123"]


def test_normalize_command_args_project_command_no_rest():
    args = normalize_command_args("stats", "proj-b", None, None)
    assert args == ["proj-b"]
