# Reproducible Demo

This walkthrough uses Windows PowerShell and the deterministic `bugfix` scenario.
It keeps the demo workspace outside the source repository.

## 1. Prepare the workspace

From the project root:

```powershell
$demo = Join-Path $env:TEMP "coding-agent-bugfix"
.\.venv\Scripts\python.exe .\demo\create_demo_workspace.py `
  --scenario bugfix `
  --output $demo `
  --force
```

The command prints the scenario name, resolved workspace, and task.

## 2. Optionally initialize Git

Git enables the final status and diff presentation:

```powershell
git -C $demo init
git -C $demo config user.email "demo@example.invalid"
git -C $demo config user.name "Demo User"
git -C $demo add calc.py tests/test_calc.py
git -C $demo commit -m "demo baseline"
```

## 3. Prove the initial test fails

```powershell
Push-Location $demo
& "D:\path\to\coding-agent\.venv\Scripts\python.exe" -m pytest -q
Pop-Location
```

Use the actual project path in place of `D:\path\to\coding-agent`. The failure
shows that `calc.add` subtracts instead of adding.

## 4. Run the coding agent

Set the supported environment variables in the shell. Do not display the key in
the recording.

```powershell
$env:DEEPSEEK_API_KEY = "<your-key>"
.\.venv\Scripts\python.exe .\main.py `
  --workspace $demo `
  "Fix the failing tests in this workspace. Inspect the code, make the smallest correct change, run the tests, and finish only after verification succeeds."
```

## 5. Observe the run

The default display shows Rich step and tool activity without dumping complete
file bodies. A normal successful flow inspects the workspace, reads the relevant
code, applies one exact edit, and executes pytest.

The final presentation includes:

- observed verification commands and their exit status;
- run metrics and stop reason;
- read-only Git status, diff statistics, and bounded diff;
- the append-only JSONL trace path.

## 6. Verify independently

After the agent finishes, run the test suite yourself:

```powershell
Push-Location $demo
& "D:\path\to\coding-agent\.venv\Scripts\python.exe" -m pytest -q
git diff --check
git diff
Pop-Location
```

The independent pytest command should pass, and Git should show the minimal
`return a - b` to `return a + b` change.

## Other scenarios

Replace `bugfix` with `implement` or `multi_file` to demonstrate function
implementation or cross-file reasoning. The same definitions are used by
`eval/run_eval.py`, so generation and evaluation cannot drift apart.
