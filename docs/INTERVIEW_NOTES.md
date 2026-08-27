# Technical Interview Notes

## 1. Why no agent framework?

The core loop is intentionally small enough to implement directly. Owning the
loop makes message ordering, tool dispatch, error recovery, and termination easy
to inspect and test. It also avoids hidden hosted execution or framework-specific
state. The tradeoff is that higher-level capabilities must be built explicitly.

## 2. Why normalize ToolCall and ToolResult?

Provider response objects stay inside `ModelAdapter`. `ToolCall` gives the loop a
stable action shape, while `ToolResult` gives every local handler the same success,
output, error, and metadata contract. Provider migration and tool testing then do
not require rewriting the agent loop.

## 3. Why exact SEARCH/REPLACE?

An exact edit is predictable and auditable. Zero matches mean the context is stale
or incorrect; multiple matches mean the requested edit is ambiguous. Rejecting
both cases prevents silent fuzzy changes and prompts the model to read more exact
context before retrying.

## 4. How is the workspace boundary enforced?

Each file path is interpreted relative to one resolved workspace root. Absolute
paths, parent escapes, `.git`, and resolved symlinks outside the root are rejected.
Directory traversal also skips ignored and sensitive locations. The boundary is
validated before access, not inferred from the model's intent.

## 5. Why use `shell=True`, and what are its limitations?

Coding tasks often need compound developer commands and host-shell syntax, so the
executor accepts one command string. This is convenient and portable at the API
level, but the actual syntax remains host dependent. Shell parsing also means the
executor cannot serve as a security boundary; policy is only a preliminary guard.

## 6. How does process-tree timeout work?

The process starts in a new session on POSIX or a new process group on Windows.
After timeout, POSIX sends a kill signal to the process group; Windows invokes
`taskkill /T /F` for the process tree, with a direct kill fallback. Captured output
is collected again after termination when possible.

## 7. Why is the policy not a sandbox?

`SafetyPolicy` uses deterministic patterns to block common sensitive files,
environment dumps, traversal, and obvious destructive commands. Equivalent shell
behavior can be expressed in many ways, and allowed commands retain the user's OS
permissions. Complete isolation requires an OS, container, or comparable sandbox.

## 8. Why are tool errors observations?

File-not-found, ambiguous edits, non-zero exits, timeouts, and policy denials are
expected during iterative work. Returning them as `ToolResult` lets the model
inspect evidence and recover in the next turn. Exceptions remain reserved for
unexpected failures that cannot be represented safely.

## 9. How is native tool-call history preserved?

Assistant history stores each provider-independent call ID, function name, and
JSON arguments in the standard assistant tool-call shape. Each following tool
message references the same call ID and contains normalized JSON. This preserves
the protocol required by OpenAI-compatible chat completion APIs.

## 10. Why use an append-only JSONL Event trace?

Each line is independently parseable and is written immediately after an event.
Monotonic sequence numbers and `source_seq` links retain order and tool causality.
Append-only facts simplify debugging and allow UI, metrics, verification, or
future replay tools to derive their own projections.

## 11. How does redaction work, and what are its limits?

Trace and Git presentation redact known secret-shaped keys, assignments, bearer
values, and common key formats. Sensitive file diff bodies are hidden completely,
and long fields are bounded. Pattern-based redaction cannot recognize every
credential, so sensitive values should never enter the workspace or task text.

## 12. How does no-progress detection work?

Ordered tool names and canonical JSON arguments form a response fingerprint.
Only consecutive identical tool-call responses increment the counter. At the
configured threshold, the current calls are not executed and the run stops.
Changing action resets the counter, preserving legitimate test-edit-test cycles.

## 13. Why is the renderer a projection?

The event stream is the source of execution facts. `RichRenderer` consumes those
facts through an optional sink and decides what a human should see. The agent and
trace modules do not import Rich, so presentation failure cannot change loop
semantics or persistence.

## 14. How does verification avoid trusting model claims?

Verification pairs recorded `tool_call` and `tool_result` events by causal
sequence. It recognizes test commands and counts success only when the observed
result is successful, has exit code zero, and did not time out. Assistant prose
alone never becomes verification evidence.

## 15. How is Git diff designed?

`GitInspector` runs only a fixed whitelist of read-only Git argument arrays with
`shell=False`. It combines status, staged and unstaged statistics, and bounded
diffs. Secret-shaped values and sensitive-file sections are redacted. It never
stages, restores, commits, or mutates repository state.

## 16. What would be improved next?

A stronger execution boundary could use an opt-in OS or container sandbox with a
separate network policy. Interactive approval could handle medium-risk commands.
Evaluation could grow to more languages and repeat runs for reliability metrics.
Context budgeting could be added without changing the normalized tool contracts.
