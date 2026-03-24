import urllib.request
import json
import sys

payload = {
    "issue_number": "123",
    "agent_type": "designer",
    "step_id": "design",
    "step_num": 3,
    "status": "complete",
    "summary": "Designed the Social Platform Adapter Layer and created ADR-085.",
    "key_findings": [
        "Created ADR-085 covering the SocialPlatformAdapter protocol and platform-specific adapters (Discord, X, LinkedIn, Meta).",
        "Defined state model extensions for campaign context, including campaign_id, content_bundle, and audit fields.",
        "Specified idempotency key generation `(campaign_id, platform, scheduled_time_utc)` and backoff retry logic.",
        "Outlined the credential flow to enforce requester-scoped OAuth tokens without exposing raw secrets.",
        "Detailed policy and approval gates for compliance and review steps."
    ],
    "next_agent": "developer",
    "alignment_score": 1.0,
    "alignment_summary": "Created ADR-085 based on ADR-084 to clarify API contracts and architecture for social platform adapters.",
    "alignment_artifacts": ["docs/ADR-085-Social-Platform-Adapters.md"],
    "comment_markdown": """## 🔍 Architecture & Design Complete — designer

**Severity:** High
**Target Sub-Repo:** `nexus`
**Workflow:** enterprise_workflow

**Step ID:** `design`
**Step Num:** 3

### Findings

- **Created ADR-085**: Documented the `SocialPlatformAdapter` protocol and specific adapters for Discord, X, LinkedIn, and Meta.
- **State Model Extensions**: Defined extensions for `campaign_id`, `objective`, `content_bundle`, and audit fields (`publish_results`, `approval_decisions`).
- **Idempotency & Retries**: Defined key generation `(campaign_id, platform, scheduled_time_utc)` and exponential backoff retry logic for live publishing.
- **Security Model**: Outlined the credential flow to enforce requester-scoped OAuth tokens, preventing the exposure of raw secrets.
- **Compliance Gates**: Specified policies for the `reviewer` and `compliance` agents to block invalid formats or unsafe prompts.
- **Alignment Score:** 1.0
- **Alignment Summary:** Created ADR-085 based on ADR-084 to clarify API contracts and architecture for social platform adapters.
- **Matched Artifacts:** `docs/ADR-085-Social-Platform-Adapters.md`

### SOP Checklist

- [x] 1. **Triage & Classification** — `triage` : Classify the issue, assign priority, and decide the path
- [x] 3. **Architecture & Design** — `designer` : Create a design proposal covering API contracts and ADRs
- [ ] 4. **Implementation** — `developer` : Implement code changes based on design or triage output
- [ ] 5. **Code Review** — `reviewer` : Review implementation for correctness, quality, and standards; approve only after full regression (or equivalent full CI suite) passes
- [ ] 7. **Compliance & Security Review** — `compliance` : Verify privacy, security, and regulatory compliance
- [ ] 9. **Merge & Deploy** — `deployer` : Merge approved PR and create a release
- [ ] 10. **Document & Close** — `writer` : Update docs, write changelog entry, and close the issue
- [ ] 11. **Close Rejected Issue** — `finalizer` : Post summary and close issues that failed review

Ready for **@Developer**"""
}

req = urllib.request.Request(
    'http://webhook:8081/api/v1/completion',
    data=json.dumps(payload).encode('utf-8'),
    headers={'Content-Type': 'application/json'}
)

try:
    with urllib.request.urlopen(req) as response:
        print(f"Status: {response.status}")
        print(response.read().decode('utf-8'))
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)
