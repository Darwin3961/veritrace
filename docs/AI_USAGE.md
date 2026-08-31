# AI Usage and Development Assistance

## 1. Purpose

VeriTrace uses AI in two distinct roles: an LLM participates in agent runs, and
AI-assisted development tools contributed to building the repository. This
document makes both roles explicit and describes the review boundary applied to
their output.

## 2. Runtime Model Usage

At runtime, VeriTrace calls an LLM through an OpenAI-compatible client interface.
The current examples and defaults target DeepSeek, while `ModelAdapter` isolates
provider-specific request and response formats from the rest of the agent. The
model reasons over conversation history and may return native tool/function
calls, which the adapter normalizes into project-owned `ToolCall` objects.

The remote model does not directly edit the workspace or execute commands on a
provider-hosted machine. Local VeriTrace code owns:

- workspace file access and exact edits;
- command execution, timeout handling, and output bounds;
- tool schemas, dispatch, and normalized `ToolResult` observations;
- conversation and tool-call history management;
- deterministic safety-policy checks;
- event tracing, metrics, termination, and no-progress handling;
- verification summaries derived from observed execution facts.

The model proposes actions; the local runtime decides whether and how to execute
them. `SafetyPolicy` reduces obvious risk but is a deterministic best-effort
guard, not an operating-system sandbox.

## 3. AI-Assisted Development

AI-assisted tools, including ChatGPT and Codex, were used during development of
VeriTrace. Their assistance included interpreting requirements and constraints,
architecture discussion and design review, implementation support, generating or
refining local code changes, debugging failures, test design and regression
analysis, documentation organization and wording, and Git/release workflow
support.

This assistance was substantive and was not limited to documentation polishing.
AI suggestions were treated as proposals to inspect, adapt, test, or reject—not
as automatically correct changes and not as evidence that a task was complete.

## 4. Review and Verification

AI-assisted changes were reviewed in the repository and checked against actual
local behavior. Depending on the change, validation included:

- focused and full `pytest` runs;
- deterministic coding-task scenarios with independent verification commands;
- `git diff` review and `git diff --check`;
- append-only structured execution traces and metrics;
- paired `tool_call` / `tool_result` facts used by verification logic;
- independent command execution where an agent's final statement was not enough.

Model-generated statements are not proof that code is correct. VeriTrace's own
design follows the same principle: verification status is derived from locally
observed execution evidence rather than natural-language completion claims.

## 5. Responsibility and Limitations

AI tools are development aids, not independent project authors. Final
architectural choices, acceptance criteria, repository state, and responsibility
for submitted or published work remain with the project developer. AI assistance
does not bypass VeriTrace's local execution, policy, tracing, or verification
boundaries.

Models can produce incorrect code, incomplete tests, misleading explanations, or
unsafe suggestions. Their output therefore requires inspection and empirical
verification. This document is a voluntary transparency record; it does not
claim that AI assistance guarantees quality or correctness.
