# 中文快速入门

## 1. 构建与校验插件包

```powershell
git clone https://github.com/talenlin/patent-retrieval-evokit.git
cd patent-retrieval-evokit
py -m unittest discover -s tests -v
py -m unittest discover -s plugin/patent-retrieval-evokit/tests -v
py scripts/public_release_check.py --root .
py build_plugin_package.py
Get-FileHash .\dist\patent-retrieval-evokit-v1.0.0.zip -Algorithm SHA256
```

将 ZIP 解压到独立临时目录后，再运行包内 `scripts/verify_package.py`。不要直接把开发仓库当作用户经验库。

## 2. 安装插件

把 ZIP 解压到目标电脑的插件目录，确认目录根部包含 `.codex-plugin/plugin.json`。重新载入 Codex 后，应能看到 `patent-retrieval-evokit` Skill。

## 3. 初始化独立经验库

```powershell
py <插件目录>\scripts\expctl.py init --repo "<你的经验库目录>"
py <插件目录>\scripts\expctl.py --file "<经验库目录>\检索经验.md" validate
py <插件目录>\scripts\expctl.py --file "<经验库目录>\检索经验.md" doctor
```

经验库应与插件源码、宿主 Skill 和具体案件目录分开。若需要同步经验库，应使用单独的私有仓库。

## 4. 在目标电脑完成工具映射

先让能读取当前运行时 `tools/list` 的 Agent 执行：

```powershell
py <插件目录>\scripts\expctl.py --file "<经验库>\检索经验.md" mapping-prompt
```

Agent 应基于真实工具目录生成：

- `runtime-tools.json`：该电脑实际暴露的工具清单；
- `tools.local.json`：抽象能力到 `(server, tool)` 的映射。

然后校验：

```powershell
py <插件目录>\scripts\expctl.py --file "<经验库>\检索经验.md" tools `
  --mapping "<经验库>\tools.local.json" `
  --catalog "<经验库>\runtime-tools.json" --json
```

有 warning 或 error 时不要启用记忆 Hook，也不要复制另一台电脑的映射。

## 5. 小型改造宿主专利检索 Skill

先检查原 Skill，再只做 dry-run：

```powershell
py <插件目录>\scripts\check_skill_integrity.py --skills-root "<skills-root>" --json
py <插件目录>\scripts\integrate_host_skill.py `
  --skills-root "<skills-root>" `
  --catalog "<经验库>\runtime-tools.json" --json
```

逐项确认目标 Skill、差异和命名空间诊断后，才添加 `--apply`。集成器只追加带边界标记的生命周期 Hook，不应改写原检索策略、工具调用或报告要求。重复执行必须保持幂等。

## 6. 每次检索的推荐生命周期

1. `prefetch --domain <领域> --out <case-run.json>`；
2. 宿主 Skill 自己执行检索；采用经验时运行 `mark-used`；
3. 将可核验结果写入运行证据；
4. `finish-run` 结算；
5. `verify-run` 只读复核计数和知识准入。

所有机器可读 JSON 都应使用命令自身的 `--out`，不要依赖 Shell 重定向。缺失证据、工具错误和未解析字段不能折算为零结果。

## 7. 回滚

集成器只管理带明确起止标记的 Hook。需要回滚时，先备份宿主 `SKILL.md`，再删除完整受管区块；不要删除宿主原有正文。经验库数据与宿主 Hook 是两个独立层面，分别备份和回滚。
