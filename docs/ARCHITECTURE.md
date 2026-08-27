# Architecture

## 1. Goals

The project provides a small, inspectable coding-agent core without an
orchestration framework. The model performs reasoning and native function
calling, while local code owns every side effect, safety decision, observation,
and termination condition.

## 2. Core data types

`ToolCall` is the provider-independent action requested by the model.
`ToolResult` is a normalized execution observation, including success, output,
error, and metadata. `AgentResponse` normalizes one provider response. `Event` is
an append-only execution fact with sequence, time, step, and optional causal link.

## 3. Agent lifecycle

`AgentLoop` creates a fresh context and session, adds the user task, calls the
model, records the assistant response, executes zero or more requested tools, and
returns results to the next model turn. It stops on a final answer, step limit,
repeated invalid responses, repeated actions, or an unhandled exception.

## 4. ModelAdapter

`ModelAdapter` contains OpenAI-compatible request and response details. It accepts
ordinary messages and JSON tool schemas, parses function arguments as JSON
objects, rejects malformed structures, and returns only project-owned data types.
API credentials come from constructor input or the process environment.

## 5. ConversationContext

The context owns system, user, assistant, and tool messages. Assistant tool calls
retain their IDs, function names, and serialized arguments. Tool results are fed
back as normalized JSON. History is a projection for the model, not the canonical
execution log.

## 6. ToolRegistry

The registry publishes six JSON schemas and maps normalized calls to local
handlers. It invokes `SafetyPolicy` before dispatch, turns policy denials and
invalid arguments into `ToolResult` observations, and prevents local exceptions
from leaking through normal tool-failure paths.

## 7. WorkspaceTools

File operations resolve model-supplied paths against one workspace. Absolute
paths, parent traversal, `.git`, escaping symlinks, and common credential files
are rejected or filtered. Local edits require one exact old-text match; zero or
multiple matches fail without modifying the file.

## 8. CommandExecutor

Commands always run with the configured workspace as `cwd`. The executor captures
stdout/stderr, treats non-zero exit as a failed observation, bounds long output,
and enforces a timeout. On timeout it attempts to terminate the process group on
POSIX and the process tree with `taskkill` on Windows.

The string command interface uses `shell=True` for practical developer commands.
This enables shell syntax but also means the executor is not a security boundary.

## 9. SafetyPolicy

Policy is a deterministic best-effort guard applied before dispatch. It rejects
common sensitive files, environment dumps, parent traversal in commands, obvious
destructive Git or file commands, privilege escalation, encoded PowerShell, and
network-content pipe-to-shell patterns. It deliberately remains separate from the
executor and is not an OS sandbox.

## 10. SessionTrace

Each run owns a `SessionTrace`. Events are appended to memory and optionally to a
JSONL file in sequence order. The trace applies bounded previews and defensive
redaction before persistence. Event-sink failures do not corrupt the trace or
stop the agent.

`Event` is the append-only fact source. Trace files, metrics, verification, and
rendering are projections of those facts.

## 11. No-progress detection

Consecutive tool-call responses are fingerprinted from ordered tool names and
canonical JSON arguments. Reaching the configured repeated-action threshold stops
before executing the threshold-triggering call. Different actions reset the
counter, so test-edit-test cycles remain valid.

## 12. RichRenderer

The renderer consumes events through an optional sink and shows bounded, markup-
safe activity. It hides file bodies and edit content, distinguishes failures,
policy blocks, and timeouts, and produces a final human summary. Renderer is a
projection for a human and is not imported by the agent loop or trace layer.

## 13. GitInspector

Git inspection uses a small read-only subprocess whitelist with `shell=False`.
It combines status, staged and unstaged statistics, and bounded diffs. Sensitive
file sections and secret-shaped values are redacted before presentation. It does
not stage, restore, commit, or otherwise change repository state.

## 14. Verification

Verification derives facts only from paired `tool_call` and `tool_result` events.
It counts successful and failed commands, timeouts, file changes, policy blocks,
and recognized test commands. Model prose alone never becomes test evidence.

## 15. Eval flow

Scenario definitions are shared by the demo generator and evaluation runner. For
each scenario, the runner creates an isolated temporary workspace, runs the real
agent core, and then executes the scenario's verification command independently.
Normalized results and metrics can be written as JSON without conversation data.

## 16. Design tradeoffs

The small architecture favors explicit control flow and testability over broad
framework integrations. Native tool calling avoids a custom action language.
Exact replacement sacrifices fuzzy convenience for predictable edits. Event
projections keep model history, persistence, and presentation decoupled.

## 17. Known limitations

The process runs with the current user's OS permissions. Policy and redaction are
pattern-based and best effort, not complete isolation or data-loss prevention.
Shell portability depends on the host. There is no container/network sandbox,
interactive approval flow, multi-agent system, semantic repository map, persistent
memory, automatic Git commit, or rollback mechanism.
