# Nexus ARC (Agentic Runtime Core): Framework vs Integration Layer

## The Confusion

You're right to ask this! There's a clear distinction between:

1. **The framework** (nexus-arc package) - Generic, reusable
2. **The integration layer** (nexus_core_helpers.py) - Specific to your Nexus bot

## Think of it Like This

### Framework = Django/Flask

Generic web framework that:

- Handles HTTP requests/responses
- Provides ORM for databases
- Manages sessions
- **Doesn't know about YOUR business logic**

### Integration Layer = Your Application Code

Your specific app that:

- Defines User, Product, Order models
- Implements login/checkout flows
- Connects to YOUR database
- **Uses the framework to do these things**

---

## Nexus ARC Framework (Generic)

**Location:** `nexus-arc/`

**What it provides:**

```python
# Generic workflow abstraction
class Workflow:
    id: str
    name: str
    steps: List[WorkflowStep]
    state: WorkflowState  # PENDING, RUNNING, PAUSED, COMPLETED
    metadata: dict  # Any data you want

class WorkflowStep:
    step_num: int
    name: str
    agent: Agent
    prompt_template: str
    condition: str  # Optional: when to run this step

class WorkflowEngine:
    async def create_workflow(workflow: Workflow)
    async def start_workflow(workflow_id: str)
    async def pause_workflow(workflow_id: str)
    async def resume_workflow(workflow_id: str)
    async def complete_step(workflow_id, step_num, outputs)
```

### YAML Workflow Definitions

Nexus ARC can load workflow definitions from YAML and map them into
`Workflow` and `WorkflowStep` models. The `YamlWorkflowLoader` provides
schema validation and support for advanced features like retry policies
and parallel step groups.

```python
from nexus.core import YamlWorkflowLoader

workflow = YamlWorkflowLoader.load(
    "./examples/workflows/development_workflow.yaml",
    workflow_id="demo-issue-42",
    workflow_type="full",
    strict=True  # Raise error on schema warnings
)

await engine.create_workflow(workflow)
```

#### Advanced YAML Features

**Retry Policies**: Define fine-grained retry behavior per step.

```yaml
steps:
  - id: triage
    agent_type: triage
    retry_policy:
      max_retries: 3
      backoff: exponential
      initial_delay: 1.0
```

**Parallel Step Groups**: Group steps that can be executed concurrently.

```yaml
steps:
  - id: security_scan
    agent_type: compliance
    parallel: ["style_check", "unit_tests"]
    
  - id: style_check
    agent_type: reviewer
    
  - id: unit_tests
    agent_type: developer
```

### Defining Custom Agents

The Nexus ARC framework allows for the definition of custom agent types through YAML configuration files. These agent
definitions specify the agent's purpose, required tools, input/output contracts, and AI instructions, enabling flexible
and extensible agent-driven workflows.

**Example: Business Agent Definition**

The `Business` agent (defined in `examples/agents/business-agent.yaml`) serves as an illustration of how to define an
agent that provides AI-powered feature suggestions. It showcases:

- **`agent_type`**: A unique identifier for the agent (e.g., `business`).
- **`inputs` and `outputs`**: Clearly defined schema for data the agent expects and produces.
- **`ai_instructions`**: A detailed prompt guiding the AI's behavior and desired output format.
- **`requires_tools`**: Listing the external tools the agent needs to perform its task (e.g., `github:read_issue`,
  `ai:completion`).

This modular approach ensures that new agent capabilities can be seamlessly integrated and orchestrated within Nexus ARC
workflows without modifying the core framework code.

### Workflow Monitoring & Approval Gates

Nexus ARC supports human approval gates and merge policies within workflow definitions.

**Example**: PR Merge Approval Configuration

```yaml
# examples/workflows/development_workflow.yaml

monitoring:
  log_all_decisions: true
  audit_trail: "vcs_comments"
  notify_human: true
  
  human_approval_gates:
    - before: "create_pr"  # Require review before opening PR
  
  # PR Merge Policy: Require human approval before merging PRs
  # Only applies when project config policy is 'workflow-based'
  # When project config is 'always', this is ignored (human approval always enforced)
  require_human_merge_approval: true  # Default for this workflow
```

**Two-Level Configuration**:

1. **Project-Level Policy** (`config/project_config.yaml`):

```yaml
require_human_merge_approval: always  # Options: always | workflow-based | never
```

2. **Workflow-Level Preference** (workflow YAML `monitoring` section):

```yaml
require_human_merge_approval: true  # Only applies when project policy is 'workflow-based'
```

**Precedence Rules**:

| Project Config   | Workflow Config | Result                    |
|------------------|-----------------|---------------------------|
| `always`         | [ignored]       | Human approval REQUIRED   |
| `workflow-based` | `true`          | Human approval required   |
| `workflow-based` | `false`         | Auto-merge allowed        |
| `never`          | [ignored]       | Auto-merge ALWAYS allowed |

**Use Cases**:

- **Production safety**: Set project policy to `always` to prevent accidental auto-merges
- **Flexible workflows**: Use `workflow-based` to allow some workflows to auto-merge (e.g., docs-only changes)
- **Agent behavior**: Deployment agents (e.g., @OpsCommander) check this policy before executing merge operations

See [examples/workflows/development_workflow.yaml](../examples/workflows/development_workflow.yaml) for complete
example.

**What it does NOT know:**

- ❌ What "tier-2-standard" means
- ❌ That you have projects called "acme-app", "retail-platform"
- ❌ That you use Telegram for notifications
- ❌ How to map GitHub issue numbers to workflow IDs
- ❌ Your tier → workflow type mapping
- ❌ Your specific workflow orchestration logic

**Why?** Because someone else using nexus-arc might:

- Use GitLab instead of GitHub
- Use Discord instead of Telegram
- Have completely different workflow types (no tiers at all)
- Use different project structures

> **📝 Note:** The examples below show different integration approaches. The **recommended pattern** is to define
> workflows in YAML files and use `project_config.yaml` to reference them (see examples/workflows/). However, you can also
> programmatically build workflows in Python if your use case requires dynamic workflow generation.

---

## Integration Layer (Your Code)

**Location:** `nexus/src/nexus_core_helpers.py`

**What it does:**

```python
# Example integration approach: Programmatic workflow generation
# (For YAML-based approach, see examples/workflows/ and examples/project_config.yaml)

# YOUR tier system
def _tier_to_workflow_type(tier_name: str) -> str:
    tier_mapping = {
        "tier-1-simple": "fast-track",      # YOUR naming
        "tier-2-standard": "shortened",     # YOUR naming
        "tier-3-complex": "full",           # YOUR naming
        "tier-4-critical": "full"           # YOUR naming
    }
    return tier_mapping.get(tier_name, "shortened")

# YOUR workflow definitions
async def create_workflow_for_issue(
    issue_number: str,        # YOUR GitHub issue
    project_name: str,        # YOUR project (acme-app, etc.)
    tier_name: str,           # YOUR tier system
    task_type: str,           # YOUR task types
    description: str
):
    # Get YOUR workflow chain config
    workflow_type = _tier_to_workflow_type(tier_name)
    chain = WORKFLOW_CHAIN.get(workflow_type)  # YOUR config
    
    # Translate YOUR concepts → Framework concepts
    steps = []
    for step_num, (agent_name, step_name) in enumerate(chain):
        agent = Agent(
            name=f"{agent_name}Agent",
            display_name=agent_name,
            description=f"Step {step_num}: {step_name}"
        )
        step = WorkflowStep(
            step_num=step_num,
            name=step_name,
            agent=agent,
            prompt_template=f"{step_name}: {{description}}"
        )
        steps.append(step)
    
    # Create workflow using framework
    workflow = Workflow(
        id=f"{project_name}-{issue_number}-{tier_name}",  # YOUR format
        name=f"{project_name}/{issue_title}",             # YOUR format
        steps=steps,
        metadata={
            "issue_number": issue_number,      # YOUR metadata
            "project": project_name,           # YOUR metadata
            "tier": tier_name,                 # YOUR metadata
            "task_type": task_type,            # YOUR metadata
            "issue_url": f"https://git-host/{get_repo_slug(project_name)}/issues/{issue_number}"
        }
    )
    
    # Use framework
    engine = get_workflow_engine()
    await engine.create_workflow(workflow)
    
    # Update YOUR state tracking
    StateManager.map_issue_to_workflow(issue_number, workflow.id)
```

---

## Concrete Example

### What You Want to Do

Create a workflow for a new GitHub issue #123 in acme-app project, tier-2-standard

### Using Framework Directly (Hard)

```python
# You'd have to manually do all this:
workflow = Workflow(
    id="acme-app-123-tier-2-standard",  # Manual
    name="acme-app/feat/add-authentication",  # Manual
    steps=[
        WorkflowStep(
            step_num=1,
            name="triage",
            agent=Agent(name="ProjectLeadAgent", ...),
            prompt_template="Triage: {description}"
        ),
        WorkflowStep(
            step_num=2,
            name="implement",
            agent=Agent(name="Tier2LeadAgent", ...),
            prompt_template="Implement: {triage.output}"
        ),
        # ... manually define all steps
    ],
    metadata={
        "issue_number": "123",
        "project": "acme-app",
        # ... all your metadata
    }
)

engine = WorkflowEngine(storage=FileStorage(...))
await engine.create_workflow(workflow)

# And manually track the mapping
mapping = {"123": "acme-app-123-tier-2-standard"}
# Save it somewhere yourself
```

### Using Integration Layer (Easy)

```python
# Just call your helper function
workflow_id = create_workflow_for_issue_sync(
    issue_number="123",
    issue_title="feat/add-authentication",
    project_name="acme-app",
    tier_name="tier-2-standard",
    task_type="feature",
    description="Add user authentication"
)

# Done! Everything handled:
# - Tier → workflow type mapping
# - WORKFLOW_CHAIN lookup
# - Step creation
# - Metadata population
# - State tracking
# - Framework orchestration
```

---

## Why This Separation Matters

### If You Put Everything in Framework

**Problem 1: Framework becomes specific to you**

```python
# In framework code - BAD
class WorkflowEngine:
    def create_tier2_workflow(self, issue_num):  # What if user has no tiers?
        chain = WORKFLOW_CHAIN["shortened"]       # What if user has different config?
```

→ Framework is no longer reusable by others

**Problem 2: Can't share framework**

```python
# Someone else wants to use nexus-arc but they:
- Use GitLab (not GitHub)
- Have "complexity levels" instead of tiers
- Use different workflow structure
```

→ They can't use your framework because it's too specific

### With Separation

**Framework stays generic:**

```python
class WorkflowEngine:
    async def create_workflow(self, workflow: Workflow):
        # Generic - works for anyone
        await self.storage.save_workflow(workflow)
```

**You write YOUR adapter:**

```python
# nexus_core_helpers.py
def create_workflow_for_issue(...):
    # YOUR business logic
    # YOUR tier system
    # YOUR project structure
    return generic_workflow
```

---

## Analogy Time

### SQL Database (Framework)

```sql
-- Generic operations
INSERT INTO workflows (id, name, state) VALUES (?, ?, ?)
UPDATE workflows SET state = 'paused' WHERE id = ?
```

### Your Application Code (Integration)

```python
def pause_customer_order(order_id):
    # Your business logic
    order = get_order(order_id)
    if order.status == "processing":
        # Use database
        db.execute("UPDATE orders SET status = 'paused' WHERE id = ?", order_id)
        # Your specific tracking
        send_customer_email(order.customer, "Order paused")
```

The database doesn't know what "orders", "customers", or "paused" mean in your business.  
Your code translates business concepts → database operations.

---

## What Could Move to Framework?

Some things in `nexus_core_helpers.py` could potentially be in the framework:

### Already Generic Enough

```python
# This is actually generic - could be in framework
async def pause_workflow_by_external_id(
    external_id: str,
    id_mapping: Dict[str, str],
    reason: str
) -> bool:
    workflow_id = id_mapping.get(external_id)
    if workflow_id:
        await engine.pause_workflow(workflow_id)
        return True
    return False
```

### Too Specific to Your System

```python
# This is YOUR business logic - stays in integration
def _tier_to_workflow_type(tier_name: str) -> str:
    tier_mapping = {
        "tier-1-simple": "fast-track",
        "tier-2-standard": "shortened",
        # ...
    }
```

---

## The Right Question

> "Should the framework know about issue numbers and GitHub?"

**Answer:** The framework has a **GitPlatform adapter** that knows about Git concepts (issues, PRs, comments), but it
doesn't know:

- How YOU number your issues
- What metadata YOU want to track
- How YOU map issues to workflows

That's YOUR integration layer's job.

---

## Summary

| Concern                               | Framework | Integration Layer |
|---------------------------------------|-----------|-------------------|
| **Generic workflow orchestration**    | ✅ Yes     | ❌ No              |
| **Storage adapters (File, Postgres)** | ✅ Yes     | ❌ No              |
| **Git adapters (GitHub, GitLab)**     | ✅ Yes     | ❌ No              |
| **Your tier system**                  | ❌ No      | ✅ Yes             |
| **Your project structure**            | ❌ No      | ✅ Yes             |
| **Your Telegram bot**                 | ❌ No      | ✅ Yes             |
| **Issue → Workflow mapping**          | ❌ No      | ✅ Yes             |
| **WORKFLOW_CHAIN config**             | ❌ No      | ✅ Yes             |

**Framework = Generic tools**  
**Integration = Your specific usage of those tools**

Does this clarify the separation? The framework is like a library you could publish and others could use. The
integration layer is YOUR code that uses that library for YOUR specific needs.

---

## Example: How Someone Else Would Use Nexus ARC

### Scenario: E-commerce Order Fulfillment System

Imagine a company wants to use nexus-arc for order processing workflows. They have:

- **No concept of "tiers"** (they have order types: standard, express, international)
- **Different steps** (payment, inventory, shipping, delivery)
- **GitLab instead of GitHub**
- **Slack instead of Telegram**

### Their Integration Layer

```python
# their_app/workflow_helpers.py

from nexus import WorkflowEngine
from nexus.adapters.storage.file import FileStorage
from nexus.adapters.git.gitlab import GitLabPlatform  # Their adapter
from nexus.adapters.notifications.slack import SlackChannel  # Their adapter
from nexus.core.models import Workflow, WorkflowStep, Agent

# THEIR configuration (not yours)
ORDER_WORKFLOWS = {
    "standard": [
        ("PaymentProcessor", "Verify Payment"),
        ("InventoryManager", "Reserve Items"),
        ("WarehouseBot", "Pick & Pack"),
        ("ShippingBot", "Create Label"),
        ("CustomerService", "Send Tracking")
    ],
    "express": [
        ("PaymentProcessor", "Verify Payment"),
        ("PriorityInventory", "Priority Reserve"),
        ("ExpressWarehouse", "Rush Pick & Pack"),
        ("ExpressShipping", "Same-Day Shipping"),
        ("CustomerService", "Express Notification")
    ],
    "international": [
        ("PaymentProcessor", "Verify Payment + Forex"),
        ("ComplianceBot", "Export Compliance Check"),
        ("InventoryManager", "Reserve Items"),
        ("CustomsBot", "Prepare Customs Docs"),
        ("WarehouseBot", "Pick & Pack"),
        ("InternationalShipping", "Create Intl Label"),
        ("CustomerService", "International Tracking")
    ]
}

async def create_workflow_for_order(
    order_id: str,
    order_type: str,  # THEIR concept (not your tiers)
    customer_id: str,
    items: list
):
    """Create workflow for an e-commerce order - THEIR business logic."""
    
    # THEIR business logic - map order type to workflow
    if order_type not in ORDER_WORKFLOWS:
        order_type = "standard"
    
    chain = ORDER_WORKFLOWS[order_type]
    
    # Translate THEIR concepts → Framework concepts
    steps = []
    for step_num, (agent_name, step_description) in enumerate(chain, start=1):
        agent = Agent(
            name=agent_name,
            display_name=agent_name,
            description=step_description,
            timeout=1800,  # 30 min
            max_retries=2
        )
        
        step = WorkflowStep(
            step_num=step_num,
            name=step_description.lower().replace(" ", "_"),
            agent=agent,
            prompt_template=f"{step_description} for order {{order_id}}"
        )
        steps.append(step)
    
    # Create workflow using framework (same API you use!)
    workflow = Workflow(
        id=f"order-{order_id}-{order_type}",  # THEIR format
        name=f"Order #{order_id} ({order_type})",
        version="1.0",
        description=f"Fulfillment workflow for {len(items)} items",
        steps=steps,
        metadata={
            "order_id": order_id,          # THEIR metadata
            "order_type": order_type,      # THEIR metadata
            "customer_id": customer_id,    # THEIR metadata
            "item_count": len(items),      # THEIR metadata
            "gitlab_issue_url": f"https://gitlab.com/ecommerce/ops/issues/{order_id}"
        }
    )
    
    # Use framework
    storage = FileStorage(base_path="/var/lib/ecommerce/workflows")
    engine = WorkflowEngine(storage=storage)
    await engine.create_workflow(workflow)
    
    return workflow.id


async def cancel_order_workflow(order_id: str):
    """Cancel an order workflow - THEIR business logic."""
    
    # THEIR mapping system
    workflow_id = f"order-{order_id}-standard"  # Lookup from DB in real app
    
    # Use framework
    storage = FileStorage(base_path="/var/lib/ecommerce/workflows")
    engine = WorkflowEngine(storage=storage)
    
    # Pause instead of cancel (framework doesn't have "cancel")
    await engine.pause_workflow(workflow_id)
    
    # THEIR notification system
    slack = SlackChannel(webhook_url=os.getenv("SLACK_WEBHOOK"))
    await slack.send_message(
        f"⚠️ Order #{order_id} workflow paused by customer service"
    )


# THEIR Slack bot handler (not Telegram)
async def handle_slack_resume_command(order_id: str):
    """Resume order workflow from Slack - THEIR integration."""
    
    workflow_id = f"order-{order_id}-standard"
    
    storage = FileStorage(base_path="/var/lib/ecommerce/workflows")
    engine = WorkflowEngine(storage=storage)
    
    workflow = await engine.resume_workflow(workflow_id)
    
    # Get status for rich feedback
    current_step = workflow.steps[workflow.current_step]
    
    return {
        "message": f"✅ Order #{order_id} workflow resumed",
        "current_step": f"{current_step.name} ({workflow.current_step + 1}/{len(workflow.steps)})",
        "agent": current_step.agent.display_name
    }
```

### Key Observations

Notice how:

1. **Framework API is identical** (`create_workflow`, `pause_workflow`, `resume_workflow`)
2. **But the concepts are completely different:**
    - You: `tier-2-standard` → They: `order_type="express"`
    - You: `acme-app-123` → They: `order-54321-international`
    - You: GitHub + Telegram → They: GitLab + Slack
    - You: `WORKFLOW_CHAIN` → They: `ORDER_WORKFLOWS`

3. **Same framework, different integration layer**

---

## Example: Writing a Custom Handler

Let's say you want to add a new workflow type called **"security-audit"** that doesn't exist yet.

### Step 1: Define YOUR Workflow (Integration Layer)

```python
# In config.py - add to WORKFLOW_CHAIN
WORKFLOW_CHAIN = {
    "full": [...],
    "shortened": [...],
    "fast-track": [...],
    
    # NEW: Security audit workflow
    "security-audit": [
        ("SecurityScanner", "Automated Security Scan"),
        ("ThreatAnalyst", "Manual Threat Analysis"),
        ("ComplianceChecker", "Regulatory Compliance Check"),
        ("PenetrationTester", "Pen Test"),
        ("SecurityLead", "Review & Sign-off"),
        ("Scribe", "Document Findings")
    ]
}
```

### Step 2: Add Handler Function (Integration Layer)

```python
# In nexus_core_helpers.py - add new function

async def create_security_audit_workflow(
    issue_number: str,
    project_name: str,
    severity: str,  # "critical", "high", "medium", "low"
    description: str
) -> Optional[str]:
    """
    Create security audit workflow - YOUR specific workflow type.
    
    This is YOUR business logic for YOUR security audit process.
    """
    
    # YOUR business logic: map severity to workflow type
    if severity in ["critical", "high"]:
        workflow_type = "security-audit"  # Full 6-step audit
    else:
        workflow_type = "fast-track"  # Quick 4-step check
    
    chain = WORKFLOW_CHAIN[workflow_type]
    
    # Translate to framework concepts
    steps = []
    for step_num, (agent_name, step_name) in enumerate(chain, start=1):
        agent = Agent(
            name=f"{agent_name}Agent",
            display_name=agent_name,
            description=f"Security Step {step_num}: {step_name}",
            timeout=7200,  # 2 hours for security work
            max_retries=1   # Don't retry security scans
        )
        
        step = WorkflowStep(
            step_num=step_num,
            name=step_name.lower().replace(" ", "_"),
            agent=agent,
            prompt_template=f"{step_name}: {{description}}"
        )
        steps.append(step)
    
    # Create workflow ID using YOUR format
    workflow_id = f"{project_name}-{issue_number}-security-{severity}"
    
    # Create workflow using framework
    workflow = Workflow(
        id=workflow_id,
        name=f"{project_name}/security-audit-{issue_number}",
        version="1.0",
        description=description,
        steps=steps,
        metadata={
            "issue_number": issue_number,
            "project": project_name,
            "workflow_type": "security-audit",
            "severity": severity,
            "requires_penetration_test": severity in ["critical", "high"],
            "issue_url": f"https://git-host/{get_repo_slug(project_name)}/issues/{issue_number}"
        }
    )
    
    # Use framework
    engine = get_workflow_engine()
    await engine.create_workflow(workflow)
    
    # YOUR state tracking
    StateManager.map_issue_to_workflow(issue_number, workflow_id)
    StateManager.audit_log(
        int(issue_number),
        "SECURITY_AUDIT_STARTED",
        f"Severity: {severity}, Type: {workflow_type}"
    )
    
    return workflow_id


# Sync wrapper for use in inbox_processor
def create_security_audit_workflow_sync(*args, **kwargs):
    return asyncio.run(create_security_audit_workflow(*args, **kwargs))
```

### Step 3: Use in Telegram Bot (Integration Layer)

```python
# In telegram_bot.py - add new command

@rate_limited("security_audit")
async def security_audit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Start security audit workflow.
    Usage: /security_audit <issue#> <severity>
    """
    if ALLOWED_USER_ID and update.effective_user.id != ALLOWED_USER_ID:
        return
    
    if len(context.args) < 2:
        await update.effective_message.reply_text(
            "⚠️ Usage: /security_audit <issue#> <severity>\n\n"
            "Severity: critical, high, medium, low\n"
            "Example: /security_audit 789 critical"
        )
        return
    
    issue_num = context.args[0].lstrip("#")
    severity = context.args[1].lower()
    
    if severity not in ["critical", "high", "medium", "low"]:
        await update.effective_message.reply_text("❌ Invalid severity level")
        return
    
    # Get issue details (simplified)
    project_name = "security-team"  # Or lookup from issue labels
    description = f"Security audit requested for issue #{issue_num}"
    
    # Create workflow using YOUR handler
    workflow_id = create_security_audit_workflow_sync(
        issue_number=issue_num,
        project_name=project_name,
        severity=severity,
        description=description
    )
    
    if workflow_id:
        # Get workflow status for feedback
        status = get_workflow_status_sync(issue_num)
        
        await update.effective_message.reply_text(
            f"🔒 **Security Audit Started**\n\n"
            f"Issue: #{issue_num}\n"
            f"Severity: {severity.upper()}\n"
            f"Workflow: {workflow_id}\n"
            f"Steps: {status['total_steps']}\n\n"
            f"Current: {status['current_step_name']}"
        )
    else:
        await update.effective_message.reply_text("❌ Failed to create security audit workflow")

# Register handler
application.add_handler(CommandHandler("security_audit", security_audit_handler))
```

### What This Shows

1. **Framework doesn't change** - You didn't modify nexus-arc at all
2. **You added YOUR business logic** - Security audit concept is specific to YOU
3. **Integration layer grows** - nexus_core_helpers.py gets new functions
4. **Framework just orchestrates** - Creates workflow, manages state, handles pause/resume

---

## When to Add to Framework vs Integration

### Add to Framework If:

- ✅ **Multiple users would need it** (e.g., "retry failed step", "conditional branching")
- ✅ **It's a generic workflow pattern** (e.g., "parallel execution", "approval gates")
- ✅ **It's infrastructure** (e.g., "PostgreSQL storage adapter", "Slack notification adapter")

### Keep in Integration If:

- ✅ **It's YOUR business logic** (e.g., tier mappings, security audit workflows)
- ✅ **It's YOUR naming/structure** (e.g., issue number format, project names)
- ✅ **It's YOUR specific use case** (e.g., Telegram bot commands, inbox processor logic)

---

## The Test

**Ask yourself:** *"If I published nexus-arc on PyPI, could someone use it without knowing anything about my Nexus
bot?"*

- **Framework:** Yes ✅ (Generic workflow engine, adapters for common services)
- **Integration:** No ❌ (Knows about your tiers, projects, Telegram bot, etc.)

That's the right separation.
