# Copilot Instructions: Compliance

You are implementing the `Compliance` agent for Nexus Core.

## Agent Definition

**Name:** Compliance
**Description:** Reviews PRs for security, privacy, and regulatory compliance
**Version:** 0.1.0

## Purpose

When a PR passes code review, this agent:
1. Scans for security vulnerabilities
2. Checks for personal data handling (GDPR concerns)
3. Verifies no secrets or credentials are exposed
4. Reviews data retention and third-party sharing
5. Approves or blocks with detailed reasoning

## Routing rules (mandatory)

The routing decision is based on finding severity:

- **CRITICAL or HIGH (unresolved)** → `compliance_status = "blocked"` → end with `Ready for **@Developer**`
- **MEDIUM / LOW only, or no issues** → `compliance_status = "approved"` → end with `Ready for **@Deployer**`

Do NOT route to @Deployer when blocking (CRITICAL/HIGH) findings exist.


### Input Schema
You will receive:
- `implementation_pr_url` (string, required): URL to the implementation PR

### Output Schema
You must return an object with:
- `compliance_status` (enum): Whether the PR passes compliance review
- `compliance_issues` (array): List of compliance issues found

## AI Instructions

When calling the LLM, use this prompt:

```

```

## Implementation Notes

1. Follow the Nexus Core async/await patterns
2. Add proper error handling and retries
3. Include type hints for all parameters
4. Write docstrings for public methods
5. Add logging at key decision points

## Testing

After implementation:
1. Write at least 2 unit tests covering different input scenarios
2. Test error conditions (missing inputs, API failures, etc.)
3. Verify output matches the schema
4. Test timeout/retry behavior

## Resources

- Parent workflow: Check `examples/workflows/` for how this agent is used
- Similar agents: See `examples/agents/` for reference implementations
- Framework docs: Check `docs/USAGE.md` for Nexus Core patterns
