# Patent Retrieval EvoKit

一个面向专利检索 Skill 的后端无关“动态记忆插件”。它给已有检索 Skill 增加可审计的经验读取、使用标记、运行结算和知识准入能力，但不接管宿主 Skill 的检索策略、工具调用或报告结构。

当前公开版本：`1.0.0`。插件标识 `retrieve-experience-v2` 为兼容性名称，不代表公开发行版本号。

## 它解决什么问题

- 在检索前按领域读取少量、可追溯的历史经验；
- 明确记录本次实际采用了哪些经验；
- 以运行证据结算经验效果，避免把错误、缺字段或零结果误写成成功经验；
- 将具体检索工具留在每台电脑的本地映射中，使插件可以嵌入不同的专利检索 Skill；
- 用幂等 Hook 小型改造宿主 Skill，不复制或重写宿主的核心逻辑。

```text
宿主专利检索 Skill
  ├─ 原检索策略 / MCP 调用 / 报告输出（保持原样）
  └─ EvoKit 生命周期 Hook
       ├─ prefetch       检索前读取候选经验并冻结本次分母
       ├─ mark-used      标记实际采用项
       ├─ finish-run     用运行证据结算
       └─ verify-run     只读复核计数与知识准入
```

## 快速开始

```powershell
git clone https://github.com/talenlin/patent-retrieval-evokit.git
cd patent-retrieval-evokit
py -m unittest discover -s tests -v
py -m unittest discover -s plugin/retrieve-experience-v2/tests -v
py build_plugin_package.py
```

构建结果位于 `dist/`，并同时生成 `.sha256` 校验文件。跨电脑安装、工具映射和宿主改造请从 [中文快速入门](docs/QUICKSTART.zh-CN.md) 开始；若交给另一台电脑上的 Agent 执行，可直接使用 [启动 Prompt](docs/AGENT-START-PROMPT.zh-CN.md)。

## 核心边界

1. 插件只依赖抽象能力，例如“专利检索”“专利详情”；具体 `(server, tool)` 必须从目标机器的真实工具目录映射。
2. `tools.local.json`、`runtime-tools.json`、真实经验库和运行证据不得提交。
3. 未通过映射校验时，宿主检索可以继续，但记忆插件应旁路，不能猜测工具名。
4. `prefetch --out`、`finish-run`、`verify-run` 共同构成机器可复核的计数链。
5. 插件不得把宿主 Skill 的搜索策略、工具调用和报告职责搬进自己内部。

## 仓库结构

- `expctl.py`：经验库与运行生命周期命令行工具；
- `plugin/retrieve-experience-v2/`：可安装的 Codex 插件源码；
- `build_plugin_package.py`：生成可移植 ZIP 和 SHA-256；
- `scripts/public_release_check.py`：公开发布脱敏门禁；
- `tests/`：核心行为与发布门禁测试；
- `examples/`：完全虚构的宿主改造前后示例。

## 发布前检查

```powershell
py scripts/public_release_check.py --root .
```

检查项及人工复核边界见 [公开发布清单](PUBLIC_RELEASE_CHECKLIST.md)。安全问题请参阅 [SECURITY.md](SECURITY.md)。

## 许可证

MIT License。示例仅用于说明软件集成方式，不构成专利检索或法律意见。
