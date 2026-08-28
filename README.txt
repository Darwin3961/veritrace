VeriTrace 使用说明

仓库：https://github.com/Darwin3961/veritrace
环境：Python 3.10+，支持 Windows 与 POSIX。安装：python -m venv .venv，然后激活虚拟环境并执行 pip install -r requirements.txt。

配置：在当前 shell 设置 DEEPSEEK_API_KEY；可选设置 MODEL_BASE_URL 和 MODEL_NAME。程序不会自动加载 .env，请勿提交真实密钥。

运行：python main.py --workspace PATH "Fix the failing tests and verify the result."

核心功能：原生 Tool Calling、本地文件搜索/读写、唯一精确文本替换、本地命令执行、超时与进程树终止、输出截断、结构化 JSONL Trace、脱敏、运行指标、重复动作终止、Rich 实时界面、Git diff 和基于实际命令结果的 Verification。

安全：文件操作受 workspace 边界限制；SafetyPolicy 会阻止常见敏感文件、环境变量导出和明显危险命令。它只是确定性的 best-effort 防护，不是操作系统 sandbox。

Demo：python demo/create_demo_workspace.py --scenario bugfix --output PATH。可选场景为 bugfix、implement、multi_file；随后运行 Agent 并独立执行 pytest 验证。详细步骤见 docs/DEMO.md。
