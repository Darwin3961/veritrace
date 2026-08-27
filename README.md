# Coding Agent

A lightweight local coding agent implemented from scratch. The language model
reasons and requests native tool calls; repository inspection, file editing,
command execution, policy checks, history, termination, tracing, and presentation
are implemented locally by this project.

## Features

- OpenAI-compatible native tool calling through a provider-isolated adapter.
- Workspace-scoped `list_files`, `search_code`, `read_file`, `write_file`, and
  exact unique SEARCH/REPLACE through `edit_file`.
- Local shell command execution with a fixed working directory, timeouts,
  process-tree termination, and bounded stdout/stderr.
- Deterministic best-effort policy checks before local tool execution.
- Append-only JSONL events with defensive redaction and bounded previews.
- Maximum-step and repeated-action termination guards.
- Run metrics, Rich live activity, verification summaries, and read-only Git
  status/diff presentation.
- Reproducible demo workspaces and an evaluation runner with independent tests.

## Architecture

```text
User Task
   |
AgentLoop
   |
ModelAdapter
   |
ToolCall
   |
ToolRegistry
   |
SafetyPolicy
   |
Local Tools / Executor
   |
ToolResult
   |
ConversationContext
   |
next model turn
```

```text
Events
├─ JSONL Trace
├─ Rich Renderer
├─ Metrics
└─ Verification
```

The model only requests actions. Local project code validates and executes every
tool call and returns a normalized observation. Provider-hosted file systems or
code execution are not used. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Installation

Python 3.10 or newer is recommended.

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

POSIX shells:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuration

Set environment variables in your shell. The application does not automatically
load `.env` files.

- `DEEPSEEK_API_KEY`: required API credential.
- `MODEL_BASE_URL`: optional OpenAI-compatible endpoint override.
- `MODEL_NAME`: optional model-name override.

Example for PowerShell:

```powershell
$env:DEEPSEEK_API_KEY = "<your-key>"
$env:MODEL_BASE_URL = "https://api.deepseek.com"
$env:MODEL_NAME = "deepseek-v4-flash"
```

Do not commit real credentials.

## Usage

```powershell
python main.py --workspace PATH "Fix the failing tests and verify the result."
```

When the positional task is omitted, the CLI prompts on standard input. Supported
options are:

- `--workspace`: fixed directory available to local tools.
- `--max-steps`: maximum model/tool iterations.
- `--trace-dir`: JSONL trace output directory.
- `--no-trace`: keep events in memory without writing a trace file.
- `--max-repeated-actions`: repeated-action limit; `0` disables the guard.
- `--plain`: plain-text output instead of Rich rendering.
- `--no-diff`: skip final Git workspace inspection.

## Demo

Create a deterministic failing workspace and follow the walkthrough in
[docs/DEMO.md](docs/DEMO.md):

```powershell
python demo/create_demo_workspace.py --scenario bugfix --output "$env:TEMP\coding-agent-bugfix" --force
```

Available scenarios are `bugfix`, `implement`, and `multi_file`.

## Safety Model

**SafetyPolicy is a deterministic best-effort guard, not an OS sandbox.**

The implementation combines workspace path validation, sensitive-file filtering,
obvious dangerous-command detection, command timeouts, process-tree termination,
output truncation, and trace/diff redaction. Shell filtering reduces common risks
but cannot provide complete isolation against arbitrary commands. Run the agent
only in a workspace whose contents and execution environment you are prepared to
expose to local commands.

## Trace and Observability

Each run creates a fresh event session. Events are retained in memory and, unless
disabled, appended to a JSONL trace under `traces/`. Events drive Rich rendering,
metrics, and verification reporting. Known secret-shaped keys and values are
redacted defensively, but redaction is best effort and should not replace careful
credential handling.

## Evaluation

The evaluation runner creates isolated temporary workspaces, invokes the real
agent core, and then runs a separate verification command. Success is based on
that independent command rather than the model's final text.

```powershell
python eval/run_eval.py --scenario bugfix
python eval/run_eval.py --all --output-json eval/results/latest.json
```

Live evaluation requires `DEEPSEEK_API_KEY`. JSON output contains only normalized
result and metric fields.

## Project Structure

```text
coding_agent/   core agent, tools, policy, trace, and presentation
demo/           deterministic scenario definitions and workspace generator
eval/           live evaluation runner
docs/           architecture and reproducible demo guides
tests/          unit and integration tests
main.py         command-line entry point
```

## Limitations

The policy layer is not a sandbox and does not provide OS or network isolation.
The project does not implement multi-agent orchestration, RAG, MCP integration,
IDE integration, long-term memory, automatic Git commits/pushes, or rollback.
Evaluation quality still depends on the configured model and local environment.
