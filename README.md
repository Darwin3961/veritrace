<div align="center">

# ✦ VeriTrace

### 一个以执行事实为核心的可验证 Coding Agent

**模型声称完成 ≠ 任务真实完成。VeriTrace 用真实执行证据验证结果。**

<small>Model claims are not execution facts. Verification uses evidence.</small>

VeriTrace 是一个从零实现的轻量级本地 Coding Agent：
自主理解代码、调用本地工具修改仓库，并通过真实命令结果、测试状态、
Git 变化和结构化 Trace 构建 Control → Trace → Verify 闭环。

<p>
  <a href="https://www.python.org/"><img alt="Python 3.12+" src="https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&amp;logoColor=white"></a>
  <a href="#how-veritrace-works"><img alt="No Agent Framework" src="https://img.shields.io/badge/Agent_Framework-none-555555"></a>
  <a href="#why-veritrace"><img alt="Local Execution" src="https://img.shields.io/badge/Execution-local-0A7E8C"></a>
  <a href="#evaluation"><img alt="376 Tests" src="https://img.shields.io/badge/Tests-376_passing-2E8B57"></a>
</p>

[演示](#demo) · [为什么是 VeriTrace](#why-veritrace) · [工作原理](#how-veritrace-works) · [核心功能](#features) · [快速开始](#quick-start) · [交互式 CLI](#interactive-cli) · [测试与评估](#evaluation) · [安全边界与局限](#safety-and-limitations)

<img src="docs/assets/veritrace-hero.gif" width="960" alt="VeriTrace Coding Agent 演示">

</div>

<a id="demo"></a>

## Demo · 演示

顶部 Hero GIF remains a condensed visualization of a reproducible execution flow，
是对 VeriTrace 执行闭环的编排展示，不是伪造的原始终端录屏。
最终视频演示使用一个独立的本地 Python 小项目 **Sprout Demo**，初始状态为：

```text
1 failed, 8 passed
```

VeriTrace 在真实 workspace 中自主完成：

```text
运行测试 → Search / Read → 定位问题 → 最小修改 → 重新测试 → 9 passed → VERIFIED
```

核心修改只有一行：

```diff
- return max(0, stage - 1)
+ return min(MAX_STAGE, stage + 1)
```

修复后，小苗可以从“种子 → 发芽 → 双叶 → 幼苗 → 花苞 → 开花”正常成长。
Sprout Demo 是录制时使用的独立本地 workspace，不属于 VeriTrace 主仓库源码。

除视频中的 Sprout 外部演示外，仓库还提供 3 个内置确定性场景用于复现和评估：
`bugfix`、`implement`、`multi_file`。场景定义位于
[`demo/scenarios.py`](demo/scenarios.py)，完整流程见
[docs/DEMO.md](docs/DEMO.md)：

```powershell
python demo/create_demo_workspace.py --scenario bugfix --output "$env:TEMP\veritrace-bugfix" --force
```

<a id="why-veritrace"></a>

## Why VeriTrace? · 为什么是 VeriTrace

| Control · 可控执行 | Trace · 全链路追踪 | Verify · 证据验证 |
|---|---|---|
| Workspace 边界 | 结构化 `Event` | 基于真实执行证据 |
| 敏感文件保护 | Append-only JSONL Trace | 测试命令结果 |
| 确定性命令策略 | Metrics / Rich CLI | Exit code / timeout |
| 超时与进程清理 | Human / Model projection | Git 变化证据 |

### Control · 可控执行

模型只提出规范化的 `ToolCall`；本地 `ToolRegistry`、`SafetyPolicy` 和执行器负责校验、
分发并执行文件或命令操作。Workspace 边界、敏感文件保护、命令超时和进程树清理
用于降低常见风险。**SafetyPolicy 是 deterministic best-effort guard（not an OS sandbox）。**

### Trace · 全链路追踪

每个工具返回规范化的 `ToolResult`，AgentLoop 将执行事实记录为 append-only `Event`。
JSONL Trace、Metrics、Rich Renderer 和模型 history 是面向不同使用者的 projection，
不会把展示逻辑混入执行事实。

### Verify · 证据验证

VeriTrace 不直接相信模型的 completion statement。Verification 根据真实 test-like
command、exit code、timeout 和 workspace changes 生成状态。`VERIFIED` 表示已有执行
证据支持当前结果，不等于对语义正确性的形式化证明。

<a id="how-veritrace-works"></a>

## How VeriTrace Works · 工作原理

<div align="center">
  <img src="docs/assets/veritrace-architecture.svg" width="1000" alt="VeriTrace 架构">
</div>

1. 用户任务进入 `AgentLoop`，由它驱动每一轮模型调用与终止判断。
2. `AgentLoop` 通过 `ModelAdapter` 获得原生 Tool Calling，并交给 `ToolRegistry`。
3. `SafetyPolicy` 完成确定性检查后，本地文件工具或 `CommandExecutor` 执行动作。
4. `ToolResult` 返回 `ConversationContext`，成为下一轮模型可见的执行 observation。
5. 同一执行过程产生结构化 `Event`，供 JSONL Trace、Metrics、Rich Renderer 和
   Verification 投影或汇总。
6. `GitInspector` 只读检查 workspace 状态，为最终展示提供 Git 变化证据。

- `ToolResult`：规范化的执行 observation。
- `Event`：append-only 的结构化执行事实。
- History：面向模型的 projection。
- Renderer：面向人的 projection。

兼容术语：`ToolResult` — normalized execution observation；
`Event` — append-only structured execution fact。

这种分层保证：**Renderer 可以演化，而不改变 Agent control logic。**
详细设计见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

<a id="features"></a>

## Features · 核心功能

### Agent Core

- 原生 OpenAI-compatible Tool Calling，不依赖 Agent framework / SDK。
- 自研 `AgentLoop`，支持 Conversation / ToolResult pairing 和一次响应中的多个工具调用。
- Tool error 作为 Observation 返回，支持最大步数终止与 repeated-action guard。

### Local Tools

- `list_files`
- `search_code`
- `read_file`
- `edit_file`（精确且唯一的 SEARCH/REPLACE）
- `write_file`
- `run_command`

### Control & Safety

- Workspace boundary 与敏感文件保护。
- Deterministic command policy。
- Command timeout、process-tree cleanup 与 bounded stdout/stderr。
- 本地执行；不使用 provider-hosted filesystem 或 code execution。

### Trace & Verify

- Append-only JSONL Trace、Metrics 与防御性脱敏。
- Rich Interactive CLI、adaptive edit preview、pytest summary 和 `/details`。
- 只读 Git status/diff 展示。
- 基于真实命令与测试 evidence 的确定性 Verification summary。

<a id="quick-start"></a>

## Quick Start · 快速开始

### Windows PowerShell

```powershell
git clone https://github.com/Darwin3961/veritrace.git
cd veritrace

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

$env:DEEPSEEK_API_KEY = "<your-key>"

# Interactive
python main.py --workspace <项目目录>

# One-shot
python main.py --workspace <项目目录> "请修复失败测试并验证结果"
```

### POSIX shell

```bash
git clone https://github.com/Darwin3961/veritrace.git
cd veritrace

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export DEEPSEEK_API_KEY="<your-key>"

python main.py --workspace ./demo-project \
  "Fix the failing tests, make the smallest correct change, and verify the result."
```

`DEEPSEEK_API_KEY` 是 live model 调用所必需的环境变量。以下变量已有默认值，仅在需要
切换 OpenAI-compatible endpoint 或模型时设置：

```powershell
$env:MODEL_BASE_URL = "https://api.deepseek.com"  # 可选
$env:MODEL_NAME = "deepseek-v4-flash"             # 可选
```

VeriTrace 不会自动加载 `.env`，也不会要求在交互界面中输入 API Key。请勿提交凭据。
当前入口是 `python main.py`，仓库没有发布 `veritrace` console script。

<a id="interactive-cli"></a>

## 交互式 CLI

Rich 模式下省略 positional task 即进入交互式 CLI：

```powershell
python main.py --workspace <项目目录>
```

每个普通任务仍然独立调用一次 `AgentLoop.run(task)`，不会转变成 persistent
conversation memory。可用命令：

| 命令 | 作用 |
|---|---|
| `/help` | 查看命令 |
| `/status` | 查看当前状态 |
| `/trace` | 查看最近执行轨迹 |
| `/verify` | 查看最近验证证据 |
| `/details` | 查看最近命令的详细输出 |
| `/clear` | 清屏并重新显示 Header |
| `/exit` | 退出 VeriTrace |

One-shot 与 `--plain` 模式继续适合脚本和自动化；完整参数可通过
`python main.py --help` 查看。

核心 CLI 选项：`--workspace`、`--max-steps`、`--trace-dir`、`--no-trace`、
`--max-repeated-actions`、`--plain`、`--no-diff`。

<a id="evaluation"></a>

## Evaluation · 测试与评估

| 项目 | 结果 |
|---|---:|
| Automated tests | 376 passed, 1 skipped |
| Reproducible scenarios | 3 (`bugfix`, `implement`, `multi_file`) |
| Recorded live model runs | 12 / 12 passed |

内置 evaluation runner 会为每个场景创建隔离 workspace，调用真实 Agent Core，再运行
独立验证命令。12 次记录由三个场景各 4 次 live model run 组成，均通过独立验证。

```powershell
python eval/run_eval.py --scenario bugfix
python eval/run_eval.py --all --output-json eval/results/latest.json
```

Live evaluation 需要 `DEEPSEEK_API_KEY`；JSON 输出只包含规范化结果与 metrics，
不包含 conversation history 或凭据。**这是小型内部可复现实验，不是通用 Coding
Agent benchmark。**

<a id="safety-and-limitations"></a>

## Safety and Limitations · 安全边界与局限

- `SafetyPolicy` 不是 sandbox；它是确定性的 best-effort 风险约束层。
- `run_command` 在 host 上执行，工作目录固定到配置的 workspace；命令过滤不能提供
  OS 或网络隔离。
- Test-command detection 是 heuristic。
- `VERIFIED` 表示观测到的命令和结果支持当前状态，不是 semantic correctness proof。
- Conversation history 当前是线性的，没有 context compaction。
- 项目面向 small / medium 本地 Coding 任务，不适合超大仓库的全量语义上下文。
- 没有 persistent interactive shell，任务成功仍受模型行为与本地环境影响。
- Multi-Agent、RAG、MCP 和 long-term memory 等高级编排能力不在当前范围内。

Advanced orchestration features such as multi-agent execution, RAG, MCP integration,
and long-term memory are intentionally out of scope.

## Project Structure · 项目结构

```text
coding_agent/
├── agent.py          Agent 主循环、终止与工具调度
├── model.py          OpenAI-compatible 模型适配
├── context.py        对话历史与 ToolResult 配对
├── registry.py       Tool schema、policy check 与 dispatch
├── tools.py          Workspace 内文件工具
├── executor.py       本地命令执行、超时与进程清理
├── policy.py         确定性 best-effort 安全策略
├── session.py        Append-only Trace 与 Metrics
├── verification.py   基于执行证据的验证摘要
├── renderer.py       Rich 人类视图
└── git_utils.py      只读 Git 状态与 diff 检查

demo/                 内置确定性场景与 workspace 生成器
eval/                 Live evaluation runner 与独立验证
docs/                 架构、演示和设计文档
scripts/              Release 与提交检查
tests/                单元及集成测试
main.py               CLI 入口
```

## Documentation · 文档

- [架构说明](docs/ARCHITECTURE.md) — 生命周期、数据契约与设计权衡。
- [可复现演示](docs/DEMO.md) — Windows PowerShell 端到端演示流程。
