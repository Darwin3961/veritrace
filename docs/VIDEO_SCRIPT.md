# Video Script (about 110 seconds)

## 0–10 seconds — Introduction

“VeriTrace is a lightweight local coding agent built from scratch. The model
reasons and requests native tool calls, while every file operation and command
executes locally in a controlled workspace.”

Show only the project terminal and the short README overview.

## 10–25 seconds — Architecture

Point to the architecture diagram:

```text
AgentLoop → ModelAdapter → ToolCall → ToolRegistry → SafetyPolicy → Local Tools
    ↑                                                          │
    └──────────── ToolResult / Context / Trace ─────────────────┘
```

“AgentLoop orchestrates the model and local tool cycle. The provider adapter
normalizes native tool calling; ToolResult observations return through context
and trace for the next step. There is no agent framework or provider-hosted code
execution.”

## 25–75 seconds — Bug-fix demo

Create the deterministic `bugfix` workspace. Show `calc.py` and run pytest once
to establish the failing baseline. Start the agent with the printed scenario task.

Let the Rich activity view show repository inspection, the exact edit, and the
pytest command. Avoid opening environment configuration. Briefly explain that
tool failures would be returned to the model as observations rather than crashing
the loop.

## 75–95 seconds — Evidence

Pause on the final presentation. Highlight the observed test-command result,
metrics, stop reason, bounded Git diff, and JSONL trace path. Run pytest manually
once more to show independent verification.

## 95–110 seconds — Design highlights

“The main design choices are normalized ToolResult observations, append-only
Event tracing, a deterministic best-effort safety policy, and a no-progress guard
that stops repeated actions. The policy reduces obvious risk but is not an OS
sandbox.”

End on the architecture or feature list.

## Recording checklist

- Keep API credentials and environment-variable values off screen.
- Do not open sensitive files or runtime traces containing task data.
- Do not show personal profiles, account pages, or identifying local paths.
- Frame the terminal tightly around project commands and demo output.
- Confirm the demo workspace contains only the deterministic scenario files.
