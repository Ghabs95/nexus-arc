# Nexus ARC Claude Instructions

## Feature Placement Policy

You are responsible for placing code in the correct layer.

1. Classify every request before editing:
   - `FRAMEWORK`: reusable capabilities, core abstractions, runtime behavior, APIs, tool/memory/orchestration/transport logic.
   - `EXAMPLE`: demo wiring, sample UX text, tutorial-only glue in `examples/nexus-bot`.

2. Default to `FRAMEWORK` unless the user explicitly asks for example work using terms like:
   - "example", "demo", "sample", "nexus-bot".

3. Mandatory pre-edit output (first line of your response):
   - `Placement: FRAMEWORK` or `Placement: EXAMPLE`
   - `Reason: <one sentence>`

4. If placement is ambiguous, ask one direct question before coding:
   - "Should I implement this in Nexus ARC framework, or only in examples/nexus-bot?"

5. Never place reusable logic in `examples/nexus-bot`.

6. For every `FRAMEWORK` feature, also implement usage in `examples/nexus-bot` to validate and demonstrate the feature:
   - Implement framework capability first.
   - Then wire it in `examples/nexus-bot` as a consumer.
   - Keep reusable logic in framework files; example files should only consume framework APIs.

7. If modifying `examples/nexus-bot`, include this explicit line:
   - `Example change justification: "<quoted user request fragment>"`
   - If no quote exists and the change is not required by rule 6, do not modify example files.
