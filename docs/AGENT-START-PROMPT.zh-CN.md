# 另一台电脑启动 Prompt

把以下内容连同插件 ZIP、对应 `.sha256` 文件和待改造的专利检索 Skill 一起交给另一台电脑上的 Agent。首次运行先检查，不要直接批量修改。

```text
请安装并验证 Patent Retrieval EvoKit，然后以“动态记忆伴生插件”的方式小型改造我现有的专利检索 Skills。

强制边界：
1. 不得改写或搬走宿主 Skill 的检索策略、MCP 调用、审批节点和报告结构。
2. 插件正文与宿主 Hook 只写抽象能力，不写猜测的工具名。
3. 先读取本机真实 tools/list，生成本机 runtime-tools.json 与 tools.local.json，并按 (server, tool) 校验；不得复制其他电脑的映射。
4. 工具映射未通过时，保持宿主检索可用并旁路记忆插件，不得伪装工具或继续激活 Hook。
5. 真实经验库、运行证据、客户数据、Token、Cookie 和本机映射不得写入插件目录或公开仓库。
6. 先运行全部测试、包完整性校验、经验库 validate/doctor、Skill 完整性检查和集成 dry-run。
7. 向我汇报：发现的 Skills、候选目标、架构阻断项、拟追加的精确 Hook 差异、回滚方式。等待我确认后才使用 --apply。
8. 应用后重复检查幂等性，并用一个完全虚构的小型案例走通 prefetch → mark-used → finish-run → verify-run；不要使用真实案件数据做验收。

插件包：<ZIP绝对路径>
校验文件：<SHA256文件绝对路径>
宿主Skills根目录：<绝对路径>
独立经验库目录：<绝对路径>
```
