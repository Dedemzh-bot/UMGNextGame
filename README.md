# UMGNextGame

UMGNextGame 把 NextGame 的 UMG 需求分析、构建验证和程序交接规范封装成一套“文件合同优先”的工作流，并提供 Codex、Hermes、WorkBuddy 三种调度适配。

核心不是某个厂商的 subagent API，而是同一组版本化 Schema、9 份独立 `AgentFindings`、9 份无历史角色包、严格哈希/引用校验和两次直接用户确认。运行时只负责派发和等待；通过校验的文件才是权威结果。

## 能力边界

| 运行时 | 适配方式 | 当前验证状态 |
| --- | --- | --- |
| Codex | 本地 Plugin Marketplace + `spawn_agent` / `wait_agent` 映射 | 插件、Skill、合同和离线适配均已验证 |
| Hermes | agentskills.io 风格 Skill + `delegate_task` + 可配置 MCP | 静态合同验证；未声称已在所有 Hermes 部署端到端运行 |
| WorkBuddy | Workflow Knowledge Unit，使用 `execution: main|subagent`、`depends_on` 和结果 Schema | 静态 DAG/前置依赖验证；未声称已在 WorkBuddy 服务端端到端运行 |

当宿主没有并行 subagent 时，可以按同一组内的角色顺序执行兼容模式，但每次仍必须从无历史状态启动、只读取自己的 `agent-inputs/<role>.json`，并保留独立文件和全部校验屏障；同时明确说明这不是并行 multi-agent 执行。

## 受保护的主链

```text
RequestPacket 校验 + Registry shortlist + discovery role packets
  -> discovery 三角色（packet-only / no-history，并行）
  -> 校验 + canonical normalization + focused context projections/packets
  -> focused 三角色（packet-only / no-history，并行）
  -> 校验 + 唯一 synthesizer + 3 个 Review Views/packets
  -> review 三角色（packet-only / no-history，并行）
  -> adjudication + Draft-aware strict linked-file validation
  -> 展示 Requirement，停止等待第一次直接用户确认

第一次确认
  -> 生成并验证 Accepted Build View（仅 projected + buildAllowed）
  -> fresh/no-history 构建规划 Agent 只读 View，且不得连接 Unreal
  -> 完整 Requirement + View 预校验 staged Bundle、布局与确定性执行计划
  -> 预校验状态绑定后才允许 UMG 构建 / 编译 / 保存 / 最终验证 / Unreal 实际读回
  -> 展示最终制作结果，停止等待第二次直接用户确认

第二次确认
  -> build acceptance / programmer handoff / 文档验证
```

九个 findings 角色固定为：

- `visual-structure`、`text-requirements`、`project-pattern`
- `state-modeling`、`data-adaptation`、`asset-decomposition`
- `state-visual-review`、`schema-feasibility-review`、`coverage-review`

任何 adapter 都不能合并这些文件、向角色暴露完整 Request/context/Draft、把 agent 聊天摘要当证据、跳过 linked-file 校验，或复用第一次确认去授权第二次确认。Review View 或 Accepted Build View 覆盖不完整时必须回退或停止，不能以缩减输入换取较弱校验。

## 仓库结构

```text
.agents/plugins/marketplace.json   Codex 本地 Marketplace
plugins/nextgame-ui/               完整 NextGame UI Plugin、Schema、validator、Skill
orchestration/                     厂商无关的闭合 DAG、Schema、计划渲染 CLI 和测试
adapters/codex/                    Codex 调度映射
adapters/hermes/                   Hermes Skill 适配
adapters/workbuddy/                WorkBuddy Workflow Knowledge Units
scripts/validate_release.py        一键发布验证
```

## 快速验证

Python 标准库即可运行核心合同和适配器验证：

```bash
python orchestration/scripts/portable_workflow.py validate \
  --workflow orchestration/nextgame-ui.requirements.workflow.json \
  --schema orchestration/workflow.schema.json
python adapters/validate_adapters.py
```

完整回归：

```bash
python scripts/validate_release.py
```

图片覆盖扫描、HTTP MCP 执行、DOCX 模板测试和发布期 JSON Schema 交叉校验需要可选依赖：

```bash
python -m pip install -r requirements-optional.txt
```

## Codex 安装

```bash
git clone https://github.com/Dedemzh-bot/UMGNextGame.git
cd UMGNextGame
codex plugin marketplace add .
codex plugin add nextgame-ui@umg-nextgame
```

安装或更新后请开启一个新任务，使 Codex 重新发现 Plugin/Skill。Codex 的具体调度约束见 [`adapters/codex/README.md`](adapters/codex/README.md)。

## Hermes 适配

把 [`adapters/hermes/nextgame-ui-portable`](adapters/hermes/nextgame-ui-portable) 作为 Hermes Skill 安装，并在运行时显式提供：

- 本仓库或已安装 `nextgame-ui` 的 `plugin_root`
- 当前需求的持久化 `run_root`
- 需要读取 Unreal Editor 证据时，由宿主配置的 MCP 连接

Hermes 根 Agent 使用 `delegate_task` 派发九个有界角色；子 Agent 只写各自文件。如果 `delegate_task` 不可用，Skill 会退到已披露的顺序兼容模式，而不是弱化合同。

## WorkBuddy 适配

将下面两份 Workflow Knowledge Unit 导入 WorkBuddy：

- [`nextgame-ui-requirement-analysis.md`](adapters/workbuddy/nextgame-ui-requirement-analysis.md)
- [`nextgame-ui-build-acceptance.md`](adapters/workbuddy/nextgame-ui-build-acceptance.md)

第一份负责 9 角色需求分析并在第一次确认门停止；第二份在后续人工恢复时结构化执行 Accepted Build View、仅 View 可见且不触碰 Editor 的构建规划、完整 Requirement + View 的构建前校验、UMG 执行与最终校验、Unreal 读回、持久展示状态和第二次确认。两份 Workflow 之间必须保留第一次人工门，不能配置成自动直通。

## 生成具体调度清单

通用 orchestrator 不调用任何厂商 API，只把闭合 DAG 绑定到一次运行目录：

```bash
python orchestration/scripts/portable_workflow.py plan \
  --workflow orchestration/nextgame-ui.requirements.workflow.json \
  --artifact-root <external-run-directory> \
  --request-packet <external-run-directory>/request-packet.json \
  --output <external-run-directory>/status/dispatch-manifest.json
```

adapter 再把该清单翻译成宿主任务。清单分别记录协调器/validator 的完整 `inputs` 与 Agent 可见的单一 `agentInputs`；每个 worker 的返回消息只是 receipt，对应 JSON 文件通过插件 validator 后才算完成。

运行目录应放在仓库外。确实需要仓库内的临时试跑时，只能使用已忽略的 `.runs/<request-id>/`；不要把真实图片、findings、context、预览或 Requirement 放进源码目录。发布校验采用顶层源码白名单，并拒绝常见运行工件和 Unreal 资产。

## NextGame 项目数据说明

`plugins/nextgame-ui/assets/shared-widget-registry.json` 是 NextGame 项目专用的共享控件注册表，其中的 `/Game/...` 路径和 linked evidence 只有在对应 Unreal 项目及验证产物存在时才有效。跨项目或缺少证据时，不能因为仓库里记录为 `active` 就直接执行复用；必须在目标项目重新运行注册表校验和 Unreal 实际读回。

`example-composite-tabs-*` 是保持内部哈希一致的 NextGame 合同 fixture，其中的示例工程路径不是可直接搬运的执行包。真实运行必须创建新的 RequestPacket，重新计算全部 digest，不能改几行路径后复用旧 findings。

仓库不包含 UAsset、任务截图、`Saved` 运行产物、用户数据或凭据。使用条款见 [NOTICE.md](NOTICE.md)。
