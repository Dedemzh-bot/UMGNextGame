# UMGNextGame

UMGNextGame 把 NextGame 的 UMG 需求分析、构建验证和程序交接规范封装成一套“文件合同优先”的工作流，并提供 Codex、Hermes、WorkBuddy 三种调度适配。

核心不是某个厂商的 subagent API，而是同一组版本化 Schema、9 份独立 `AgentFindings`、严格哈希/引用校验和两次直接用户确认。运行时只负责派发和等待；通过校验的文件才是权威结果。

## 能力边界

| 运行时 | 适配方式 | 当前验证状态 |
| --- | --- | --- |
| Codex | 本地 Plugin Marketplace + `spawn_agent` / `wait_agent` 映射 | 插件、Skill、合同和离线适配均已验证 |
| Hermes | agentskills.io 风格 Skill + `delegate_task` + 可配置 MCP | 静态合同验证；未声称已在所有 Hermes 部署端到端运行 |
| WorkBuddy | Workflow Knowledge Unit，使用 `execution: main|subagent`、`depends_on` 和结果 Schema | 静态 DAG/前置依赖验证；未声称已在 WorkBuddy 服务端端到端运行 |

当宿主没有并行 subagent 时，可以按同一组内的角色顺序执行兼容模式，但仍必须保留独立输入、独立文件和全部校验屏障，并明确说明这不是并行 multi-agent 执行。

## 受保护的主链

```text
RequestPacket 校验
  -> discovery 三角色（并行）
  -> 校验 + canonical normalization
  -> focused 三角色（并行）
  -> 校验 + 唯一 synthesizer
  -> review 三角色（并行）
  -> adjudication + strict linked-file validation
  -> 展示 Requirement，停止等待第一次直接用户确认

第一次确认
  -> UMG 构建 / 编译 / 保存 / 预览 / Unreal 实际读回
  -> 展示最终制作结果，停止等待第二次直接用户确认

第二次确认
  -> build acceptance / programmer handoff / 文档验证
```

九个 findings 角色固定为：

- `visual-structure`、`text-requirements`、`project-pattern`
- `state-modeling`、`data-adaptation`、`asset-decomposition`
- `state-visual-review`、`schema-feasibility-review`、`coverage-review`

任何 adapter 都不能合并这些文件、把 agent 聊天摘要当证据、跳过 linked-file 校验，或复用第一次确认去授权第二次确认。

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

图片覆盖扫描、HTTP MCP 执行和 DOCX 模板测试需要可选依赖：

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

第一份负责 9 角色需求分析并在第一次确认门停止；第二份只处理已经构建、保存、验证和读回的结果展示及第二次确认。不要把两份 Workflow 合并成一个自动直通流程。

## 生成具体调度清单

通用 orchestrator 不调用任何厂商 API，只把闭合 DAG 绑定到一次运行目录：

```bash
python orchestration/scripts/portable_workflow.py plan \
  --workflow orchestration/nextgame-ui.requirements.workflow.json \
  --artifact-root <external-run-directory> \
  --request-packet <external-run-directory>/request-packet.json \
  --output <external-run-directory>/status/dispatch-manifest.json
```

adapter 再把该清单翻译成宿主任务。每个 worker 的返回消息只是 receipt；对应 JSON 文件通过插件 validator 后才算完成。

运行目录应放在仓库外。确实需要仓库内的临时试跑时，只能使用已忽略的 `.runs/<request-id>/`；不要把真实图片、findings、context、预览或 Requirement 放进源码目录。发布校验采用顶层源码白名单，并拒绝常见运行工件和 Unreal 资产。

## NextGame 项目数据说明

`plugins/nextgame-ui/assets/shared-widget-registry.json` 是 NextGame 项目专用的共享控件注册表，其中的 `/Game/...` 路径和 linked evidence 只有在对应 Unreal 项目及验证产物存在时才有效。跨项目或缺少证据时，不能因为仓库里记录为 `active` 就直接执行复用；必须在目标项目重新运行注册表校验和 Unreal 实际读回。

`example-composite-tabs-*` 是保持内部哈希一致的 NextGame 合同 fixture，其中的示例工程路径不是可直接搬运的执行包。真实运行必须创建新的 RequestPacket，重新计算全部 digest，不能改几行路径后复用旧 findings。

仓库不包含 UAsset、任务截图、`Saved` 运行产物、用户数据或凭据。使用条款见 [NOTICE.md](NOTICE.md)。
