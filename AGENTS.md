# AGENTS.md

## Project

This repository implements a lightweight local coding agent from scratch.

The agent uses an LLM only for reasoning and native tool calling.
All file access, command execution, context management, tool dispatch,
termination logic, error handling, tracing, and safety policies are implemented locally in this repository.

## Hard Constraints

- Do not use any Agent framework or Agent SDK, including LangChain,
  LlamaIndex, OpenAI Agents SDK, Claude Agent SDK, AutoGen, CrewAI,
  or similar orchestration frameworks.
- Do not use provider-hosted code execution, file systems, Code Interpreter,
  Files API, hosted shell tools, or equivalent server-side execution tools.
- Model-provider client libraries and native tool/function calling are allowed.
- The model may request actions, but all tools must be executed by our own local code.
- Do not copy existing coding-agent implementations into this repository.
  External projects may only be used as design references.
- Never place API keys, tokens, credentials, personal identity information,
  or private assessment materials in the repository.

## Architecture

Keep the core architecture provider-independent:

User Task
→ AgentLoop
→ ModelAdapter
→ ToolCall
→ ToolRegistry
→ Policy
→ Local Executor
→ ToolResult
→ Context / Event Trace
→ next Agent step

Core concepts:

- `ToolCall`: normalized action requested by the model.
- `ToolResult`: normalized result returned by every local tool.
- `AgentResponse`: provider-independent model response.
- `Event`: append-only structured execution fact.
- `ModelAdapter`: isolates provider-specific API formats.
- `Policy`: deterministic safety checks separate from execution.
- `AgentLoop`: owns iteration, tool dispatch, observation feedback,
  and termination.

## Current Scope

Required implementation:

- native tool calling
- conversation/history management
- `list_files`
- `search_code`
- `read_file`
- `edit_file`
- `write_file`
- `run_command`
- normalized `ToolResult`
- tool errors returned as observations
- max-step termination
- command timeout
- output truncation
- workspace boundary enforcement
- structured JSONL trace
- Rich terminal rendering
- Git diff display
- test/verification loop

Optional only after the required scope is stable:

- lightweight no-progress detection
- small internal benchmark

Do not implement unless explicitly requested:

- Multi-Agent
- RAG/vector database
- MCP
- IDE plugin
- Web UI
- Textual TUI
- Tree-sitter semantic repo map
- complex context compaction
- Docker/remote sandbox
- long-term memory
- Plan/Executor multi-agent architecture

## Editing Strategy

For localized source edits, prefer exact SEARCH/REPLACE semantics:

- 0 matches: return a structured error.
- exactly 1 match: apply the edit.
- more than 1 match: reject and ask for more context.

Do not silently use fuzzy replacement unless explicitly requested.

## Error Handling

Tool failure is normally an observation, not an Agent crash.

Examples:

- file not found
- search text not found
- ambiguous edit
- non-zero command exit code
- command timeout
- path-policy rejection

Return these through `ToolResult` so the model can react.

## Security

- Restrict file operations to the configured workspace.
- Resolve and validate paths before access.
- Protect `.git` and sensitive files from accidental modification where appropriate.
- Commands must have a timeout.
- Large stdout/stderr must be truncated while preserving useful head and tail output.
- `.env` must remain ignored.
- Never print or log API keys.

## Testing

Use the repository virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

Before completing a coding task:

1. run relevant tests;
2. run `git diff --check`;
3. inspect `git status`;
4. ensure no credentials or unrelated files were modified.

## Git Workflow

This assessment requires preserving the real development history.

- Work on `main`.
- Each real development stage should have a focused commit.
- Do not squash or rewrite pushed history.
- Do not use `git commit --amend` on pushed commits.
- Do not rebase pushed commits.
- Do not force push.
- Do not fabricate commits only to make history look richer.
- Only commit after the requested stage is complete and tests pass.

Typical commit progression:

- `chore: initialize coding agent project`
- `docs: add project agent instructions`
- `feat: define core agent data models`
- `feat: add local file tools`
- `feat: add local command execution`
- `feat: integrate model tool calling`
- `feat: implement agent loop`
- `feat: add workspace safety policy`
- `feat: add structured execution trace`
- `feat: add rich terminal renderer`
- `test: add coding task evaluation`
- `docs: finalize usage and design`

## Scope Discipline

When given a staged task:

- inspect the repository first;
- modify only files needed for that stage;
- do not implement later stages early;
- do not perform unrelated refactors;
- if a requested change conflicts with these constraints, stop and report the conflict.

Priorities:

Correctness
→ Reliability
→ Observability
→ Presentation
→ Extra features
