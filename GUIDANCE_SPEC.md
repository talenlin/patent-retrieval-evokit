# Retrieve Experience v2 动态插件跨电脑指导 SPEC

**SPEC 版本**：1.0
**目标插件**：`retrieve-experience-v2` 2.3.0
**目标读者**：另一台电脑上的 Codex 或具备文件、Python、Git 操作能力的开发 Agent

## 1. 产品定位

Retrieve Experience v2 不是专利检索 Skill，也不提供检索后端。它是一个伴随式 Module，为任意已有专利检索 Skill 增加三类动态记忆 Hook：

1. 检索前按领域预取历史经验；
2. 检索过程中记录哪些经验实际改变了检索，并验证结果；
3. 原检索交付完成后，写回可复用经验并输出度量。

宿主 Skill 继续拥有检索策略、后端工具、权利要求分析和报告格式。插件不得替换、复制或重排这些能力。

## 2. 架构和 Interface

```text
宿主专利检索 Skill
  ├─ 原检索流程与产出（保持不变）
  └─ 小型 Adapter 块
       ├─ Hook A：prefetch
       ├─ Hook B：mark-used / mark-outcome
       └─ Hook C：add / validate / finish-run
                       │
                       ▼
Retrieve Experience 插件 ── expctl.py ── 独立私有经验库
```

- 宿主与插件的 Interface 是三个 Hook，不是复制整份 Skill。
- `expctl.py` 是经验库唯一写入 Implementation。
- 插件和宿主 Hook 只使用抽象能力名；`tools.local.json` 保存当前电脑的 `(server, tool)` Adapter，`runtime-tools.json` 保存实测工具目录，两者均不进入 Git。
- 经验库与插件代码分离。升级插件不得覆盖真实经验库。

## 3. 包内契约

解压后的顶层目录必须是 `retrieve-experience-v2/`，并包含：

| 路径 | 作用 |
|---|---|
| `.codex-plugin/plugin.json` | 插件清单 |
| `skills/retrieve-experience-v2/SKILL.md` | 运行时 Hook 协议 |
| `skills/retrieve-experience-v2/references/HOST-INTEGRATION.md` | 宿主最小接入块和验收条件 |
| `scripts/expctl.py` | 经验库维护工具 |
| `scripts/integrate_host_skill.py` | 宿主接入的 dry-run/幂等追加工具 |
| `scripts/verify_package.py` | 解包完整性校验 |
| `tests/` | 工具与接入脚本回归测试 |
| `docs/GUIDANCE_SPEC.md` | 本指导 SPEC |
| `docs/TOOL_REFERENCE.md` | 初始化与命令参考 |
| `PACKAGE_MANIFEST.json` | 包内文件 SHA-256 清单 |

包内不得包含 `.git/`、真实经验库、案件文件、运行记录、浏览器配置、凭据或缓存。

## 4. 新电脑上的首次验证

不要直接覆盖已有插件。先把 ZIP 解压到临时目录，并执行：

```powershell
Get-FileHash .\retrieve-experience-v2-2.3.0.zip -Algorithm SHA256
Expand-Archive .\retrieve-experience-v2-2.3.0.zip .\staging
cd .\staging\retrieve-experience-v2
py scripts\verify_package.py .
py -m unittest discover -s tests -v
py scripts\expctl.py --help
```

验收条件：哈希与同目录 `.sha256` 文件一致；包校验通过；全部测试通过；帮助信息显示 v2 命令。失败时先保留输出并诊断，不得删除测试或放宽断言来制造通过。

## 5. 安装与经验库连接

按目标 Codex/Agent 的插件安装机制安装整个插件目录。若平台不支持插件清单，也可只安装 `skills/retrieve-experience-v2/`，但必须保持它能解析到 `scripts/expctl.py`，否则需要记录平台专用 Adapter。

经验库单独初始化：

```powershell
py <plugin-root>\scripts\expctl.py init --repo "D:\path\to\检索经验库"
py <plugin-root>\scripts\expctl.py --file "D:\path\to\检索经验库\检索经验.md" validate
```

随后运行 `mapping-prompt`。让能查看当前运行时 tools/list 的 Agent 生成本机 `runtime-tools.json` 与 `tools.local.json`，再使用 `tools --mapping ... --catalog ... --json` 校验。共享的 `tools.json` 只是抽象模板，不保存另一台电脑的具体工具名。不得把真实经验库放进插件目录。

## 6. 为所有专利检索 Skill 接入记忆

### 6.0 平台可见性与自足降级

Skill 目录路由通常只看 `name` 和有长度限制的 `description`；加载后可靠可见的是正文。不要把必须生效的指令只写进 `whenToUse` 等 provider metadata。影响路由的差异要放在 description 靠前位置，影响执行的规则要放在正文。

增强 Skill 不能复制另一个 Skill 的完整 description。插件不可用时，宿主应回到自身原流程并说明记忆未启用，不能回退到另一个近似 Skill。

接入前运行：

```powershell
py <plugin-root>\scripts\check_skill_integrity.py --skills-root "<技能根>" --json
```

该检查只读扫描 description 重复、不可见指令、回退耦合、悬空引用和归档状态。

### 6.1 发现候选

1. 只读扫描 Skill 清单和每个 `SKILL.md`。
2. 依据真实能力判断是否属于专利检索：必须能执行专利查询或检索迭代；仅生成报告、格式化文档、分析用户已提供专利的 Skill 不自动纳入。
3. 输出候选表：Skill 名、路径、为何纳入、现有检索入口、建议插入位置、风险。
4. 用户确认目标清单后再写入。不要按文件名批量盲改。

### 6.2 最小接入

对每个已确认宿主先 dry-run：

```powershell
py <plugin-root>\scripts\integrate_host_skill.py --skill "<host-skill>\SKILL.md" `
  --skills-root "<技能根>" --catalog "<经验库>\runtime-tools.json" --json
```

审查差异后再加 `--apply`。脚本只在末尾追加一个带 begin/end 标记的 Adapter 块，并建立备份；重复运行必须不产生第二份 Hook。

### 6.3 宿主验收

- 原正文仍是新文件的完整前缀，frontmatter 不变；
- Hook 标记各出现一次；
- 原步骤、工具、产出、审批和失败处理不变；
- 插件不可用时，宿主仍能按原流程运行并明确说明记忆降级；
- 插件可用时，一次真实检索只产生一个 run artifact，并报告实测指标。
- run artifact 由 `prefetch --out` 生成，不使用 shell 重定向捕获机器输出；
- 全局入库有可复现证据，否定性观测有隔离/反证记录；
- 结构化产物由标准 writer 生成，所有可计算数字来自 `finish-run`/`verify-run`。

### 6.4 运行记账与知识入库

正式检索在 Hook C 前必须阅读 `references/RUN-ACCOUNTING.md`。运行记录是指标的唯一权威来源：`prefetch --out` 记录预取总数、分节分布、稳定 ID 与源行；`mark-used` 只接受该运行已预取 ID；`finish-run` 重算并校验；`verify-run` 只读独立复核。

宿主报告不得手算预取数、复用数、分节数或候选数。需要与报告比对时，输出 UTF-8 JSON 审计声明，而不是让插件从自由文本中猜测数字。

`## 1`/`## 2` 的新条目会持续注入每次检索，因此 `add` 要求 `--scope global --evidence`。证据若是 0/空/报错，还需 `--falsified-by`。被反证的旧条目使用 `retract`：保留历史、停止预取，禁止通过 `add` 静默恢复。

产物目录若进入审计，每个文件都用 `--artifact` 登记。CSV/JSON 必须由标准 writer 生成；CSV 错位、未登记文件、校验值改变或 UTF-16 记录都会使复核失败。

## 7. 当前基线和阶段

当前整体约为阶段 2.8：

| 阶段 | 状态 | 已有证据 |
|---|---|---|
| 第一阶段：正确性闭环 | 完成 | 稳定 ID、完整预取、实际采用、结果评价、部分更新 |
| 第二阶段：完整性与安全性 | 核心完成 | revision、锁、原子写、结构校验、Git 白名单、Windows 编码 |
| 第三阶段：效果验证与受控进化 | 进入前期 | 已有全局入库证据门禁、作废状态、权威单次记账与独立复核；尚缺跨运行晋升与对照评价 |

## 8. 后续完善顺序

| 顺序 | 工作项 | 产物 | 验收门槛 |
|---:|---|---|---|
| 1 | 遗留库迁移 | `migrate`、schema version、迁移报告 | 幂等、无数据丢失、失败可回滚 |
| 2 | 经验溯源扩展 | backend、工具版本、置信度 | 在已有 scope/evidence/evidence_type/retraction 上补齐环境来源 |
| 3 | 提交前安全扫描 | 可配置 secret scanner | 测试密钥被阻止，普通技术文本不过度误报 |
| 4 | 跨运行汇总 | `summarize-runs --dir ... --json` | 能按领域/时间统计复用、确认、拒绝、覆盖 |
| 5 | 效果指标 | 轮次、无效查询、人工纠正、耗时 | 能与关闭记忆的基线比较 |
| 6 | 生命周期门禁 | candidate/review/active/rejected/expired | 单次成功不自动晋升；拒绝不自动删除 |
| 7 | 真实对照试验 | 基线与实验报告 | 至少 10 次、2–3 个领域、保留原始记录 |
| 8 | 规模化存储 | SQLite/JSONL + Markdown view | 仅在规模或并发证据证明需要时启动 |

## 9. 每轮改进的强制流程

1. 记录当前 commit、版本、工作区状态和全量测试结果。
2. 用真实失败、最小复现或明确的新验收条件定义问题。
3. 标明变更位于 Interface、Implementation、Adapter 还是 schema；默认保持三个 Hook 和现有 CLI 兼容。
4. 先增加稳定失败的行为测试，再做最小实现。
5. 运行全量测试、`py_compile`，写入类变更还要验证幂等、并发冲突、中断恢复和特殊字符。
6. 更新真正受影响的 Skill、文档、版本和进度记录。
7. 重建 ZIP，从 ZIP 全新解压并独立复测。

不得通过删除测试、放宽错误条件或只匹配固定输出文案来证明功能正确。

## 10. 不可破坏的约束

1. 插件永远不是专利检索后端，不能接管宿主核心流程。
2. `expctl.py` 是唯一经验库写入入口。
3. 稳定 ID 的算法不能静默改变；如需改变必须提供映射和迁移。
4. 部分更新不能清空未提供字段、首次日期或证据。
5. 旧 revision 和旧 run artifact 不能覆盖新内容。
6. `mark-used` 幂等，且只有实际改变检索才算复用。
7. outcome 必须基于本次观察并附证据。
8. rejected 进入复核，不能自动删除或自动改写技术结论。
9. 剪枝和状态不得改变去重 key。
10. Git 只处理受管文件；凭据和案件材料永不入库。
11. 插件指令只使用抽象能力；具体 `(server, tool)` 必须运行时探测并写入本机 `tools.local.json`。
12. 全文不可用时必须降低置信度。
13. 必须生效的行为写在正文；路由差异写在 description；不得只依赖 `whenToUse`。
14. 降级必须自足，不得回退到另一个近似 Skill。
15. 移除 Skill 前先清理引用、备份并移出扫描根，再验证技能计数下降。
16. `## 1`/`## 2` 是持续生效的全局指令；只有可复现证据才能入库，否定性观测还必须记录隔离/反证测试。
17. 作废不删除历史；已作废条目不得预取或被 `add` 静默恢复。
18. 运行总数与分节数必须同时一致；指标只能由运行记录机械计算。
19. 机器可读记录只能用 `--out` 产生 UTF-8，不能使用 PowerShell `>`。
20. CSV/JSON 用标准 writer 生成；审计产物目录时每个文件都必须登记。

## 11. 第三阶段退出建议

- 同一领域至少有两次独立复用确认；
- 累计不少于 10 次真实检索并覆盖 2–3 个领域；
- 复用确认率建议达到 80%，拒绝率建议低于 10%；
- 相对关闭记忆的基线，无效查询或人工纠正至少一项下降 20%；
- 不得为了达标删除 rejected、neutral 或不利运行记录。

这些是初始试运行阈值，不是专利事实；有足够样本后应按领域成本重新校准。

## 12. 给另一台电脑上 Agent 的启动指令

```text
请先阅读插件包内 docs/GUIDANCE_SPEC.md、skills/retrieve-experience-v2/SKILL.md、
HOST-INTEGRATION.md 和 RUN-ACCOUNTING.md。不要立即修改代码或任何宿主 Skill。

先校验 ZIP SHA-256 和 PACKAGE_MANIFEST.json，在临时目录全新解压并运行全部测试，
报告包版本、测试结果、当前阶段及任何环境差异。初始化或定位经验库后先运行 mapping-prompt，
从当前运行时真实 tools/list 生成 runtime-tools.json 与 tools.local.json，并让 tools --catalog
校验无 warning。不得照抄另一台电脑的工具名。随后只读扫描本机 Skill，列出真正具有
专利检索能力的候选及理由，等待我确认后，才使用 integrate_host_skill.py 逐个 dry-run、
审查并 apply。必须保持宿主原流程、产出、工具和审批边界不变。

继续完善插件时，一次只选择 SPEC 第 8 节一个工作项：先建立最小复现和失败测试，
再做最小兼容实现。完成后通过发布门禁，重建 ZIP，并从 ZIP 独立复测。
自动修改插件核心指令、创建远程、推送、安装软件、迁移真实经验库或删除记录前，
必须说明影响并获得确认。
```

## 13. 每轮交付报告模板

```markdown
# 本轮改进报告
- 基线版本/commit：
- 选择的工作项：
- 最小复现与失败证据：
- Interface/Adapter 是否变化：
- 实现摘要：
- 全量测试结果：
- 新包文件名与 SHA-256：
- 全新解压复测结果：
- 宿主兼容性与迁移影响：
- 回滚方式：
- 尚未解决事项：
```
