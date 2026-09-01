VeriTrace 使用说明

一个以执行事实为核心的可验证 Coding Agent：自主理解代码、受控修改仓库，并以真实运行证据验证结果。

让每一步执行都有边界、每一次行为都有轨迹、每一个完成结论都有证据。

【Git 仓库】
https://github.com/Darwin3961/veritrace

【运行方式】
Python 3.12+；安装依赖：pip install -r requirements.txt
设置环境变量 DEEPSEEK_API_KEY；MODEL_BASE_URL、MODEL_NAME 可选。
交互模式：python main.py --workspace <项目目录>
单次任务：python main.py --workspace <项目目录> "请修复失败测试并验证结果"

【设计亮点】
VeriTrace 以真实执行事实构建 Control—Trace—Verify 闭环，使 Coding Agent 的行为可控、过程透明、结果可信。

1. 可控执行（Control）：自研 AgentLoop、ConversationContext、ToolRegistry 与 Tool Calling 解析；文件和命令均在本地执行，并通过 workspace 边界、敏感文件保护、危险命令策略及 timeout 约束风险。
2. 全链路追踪（Trace）：工具结果统一为 ToolResult 并记录为 append-only Event；Trace、Metrics 与 Rich CLI 均由结构化执行事实驱动，使 Agent 运行可审计、可回看、可解释。
3. 证据驱动验证（Verify）：Verification 不把模型的“任务已完成”直接视为成功，而是依据真实命令结果、exit code、timeout 与工作区变化生成验证状态，将模型声明与执行证据分离。
4. 可复现评测（Reproduce）：提供 bugfix、implement、multi_file 三类可复现场景。

【工程验证】
当前自动测试 376 passed、1 skipped，并完成 12/12 次真实模型端到端运行验证。

仓库首页提供 Demo、架构图、Quick Start 与安全边界说明。
