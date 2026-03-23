# Agentic Awesome Skills Guide

> **Credit & Upstream Repository**:
> The [sickn33/antigravity-awesome-skills](https://github.com/sickn33/antigravity-awesome-skills/) repository is the
> central authority on these skills. If you find these skills useful for your workflows, please consider staring their
> repository or supporting the project.

This guide explains how to install and use their massive collection of 900+ curated, high-performance agentic skills
seamlessly within an existing multi-agent ecosystem across various AI coding assistants (Antigravity, Cursor, Claude
Code, Gemini CLI, etc.).

## 1. Installation Strategies

You don't need to replace your custom agents. The awesome skills can work *alongside* them.

### Option A: Global Installation (Recommended)

This installs all 900+ skills globally on your machine. Your chosen AI assistant will load these in addition to the
project-specific agents.

```bash
# Default (Antigravity)
npx antigravity-awesome-skills

# Cursor
npx antigravity-awesome-skills --cursor

# Claude Code
npx antigravity-awesome-skills --claude

# Gemini CLI
npx antigravity-awesome-skills --gemini
```

### Option B: Cherry-Picking Skills (Workspace Installation)

If you only want specific skills or want to commit them to the repository for the entire team:

1. Browse the [GitHub repository](https://github.com/sickn33/antigravity-awesome-skills/tree/main/skills).
2. Copy the desired skill folder.
3. Paste it into your project's local skills directory (e.g., `.agent/skills/` for Antigravity, `.cursor/skills/` for
   Cursor, `.claude/skills/` for Claude Code).

## 2. Finding the Right Skills (Bundles)

The repository uses **Bundles** to help you find skills relevant to your role. **Bundles are not separate installations.
** They are just curated lists of recommended skills.

Check the [Bundles Documentation](https://github.com/sickn33/antigravity-awesome-skills/blob/main/docs/BUNDLES.md) to
find packs like:

- **Essentials** (5 skills everyone needs, e.g., `@brainstorming`, `@lint-and-validate`)
- **Web Wizard** (Frontend specific skills)
- **Security Developer** (Auditing and hardening skills)

## 3. How to Use Skills

Once installed (either globally or locally), you invoke a skill by simply mentioning its name in your prompt to the AI.

### Basic Syntax

Just use `@skill-name` in your natural language prompt.

> "Use `@brainstorming` to help me design the new authentication flow."
> "Run `@lint-and-validate` on `examples/app/lib/main.dart`."

**In the Antigravity IDE (Agent Mode):**

```bash
Use @brainstorming to plan this feature
```

**In Cursor:**

```bash
@brainstorming help me design a new feature
```

**In Claude Code / Gemini CLI:**

```bash
Use the @brainstorming skill to help me plan my app
```

### Combining Skills & Custom Agents

Your custom agents (like a `@TechLead` or `@Developer`) act as specialized personas. You can ask a custom agent to use a
specific technical skill from the awesome-skills repo!

**Example:**
> "@TechLead, use the `@flutter-expert` and `@testing-patterns` skills to write unit tests for the login screen."

**Example Chaining:**
> "@BackendLead, use `@brainstorming` to design the new API endpoint, then apply `@api-security-best-practices` to
> ensure it's secure."

## 4. Best Practices

- **Be Specific:** Always mention the skill and the file/context it should apply to.
  *(Good: "Use @lint-and-validate to check `examples/src/components/Button.tsx`")*
- **Start with Planning:** Use `@brainstorming` or `@writing-plans` before diving into code.
- **Synthesize:** If a skill from the repo perfectly matches one of your custom agents, consider copying its `SKILL.md`
  instructions and merging them into the agent's definition to make your agents even smarter.
