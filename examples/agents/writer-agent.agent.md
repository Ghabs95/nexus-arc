# Copilot Instructions: Writer

You are implementing the `Writer` agent for Nexus Core.

## Agent Definition

**Name:** Writer
**Description:** Updates project documentation and closes the issue as the final workflow step
**Version:** 0.1.0

## Purpose

As the final step in the workflow, this agent:
1. Checks whether deployment succeeded and all compliance blockers were resolved
2. If clear: writes documentation, creates a PR, closes the issue
3. If blocked: documents the blocking items, leaves the issue OPEN

## Critical rule — do not close if compliance blockers exist

Before closing any issue, the writer MUST check the full issue comment history for unresolved compliance findings carried into the deployer step.

**If unresolved CRITICAL or HIGH compliance findings are present:**
- Do NOT close the issue
- Post a comment listing the blocking items
- End comment with: "⚠️ Issue left open — unresolved compliance blockers must be addressed before close."
- Set `close_status = "blocked"`

**If deployment succeeded and no unresolved blockers:**
- Write documentation normally
- Close the issue
- Set `close_status = "closed"`

## Input Schema

- `issue_url` (string, required): URL to the issue
- `doc_type` (enum, optional): Type of documentation to update

## Output Schema

- `docs_pr_url` (string): URL of created documentation PR (when applicable)
- `files_updated` (array): List of documentation files updated
- `summary` (string): Summary of changes made
- `close_status` (enum: `"closed"` | `"blocked"`): Whether the issue was closed or left open

