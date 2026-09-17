# 公开发布清单

## 自动门禁

- [ ] 核心测试与插件测试全部通过；
- [ ] `py scripts/public_release_check.py --root .` 通过；
- [ ] 插件包可构建，清洁解压后 `verify_package.py` 通过；
- [ ] Git 中没有 `tools.local.json`、`runtime-tools.json`、运行证据或真实经验库；
- [ ] 没有旧私有仓库地址、绝对本机路径、常见 Token 或私钥标记。

## 人工复核

- [ ] 示例中的主体、专利号、技术方案和检索结果均为虚构；
- [ ] 文档没有客户名、内部项目名、账号、邮箱、截图或业务数据；
- [ ] README 中的安装、测试、构建命令在清洁目录验证通过；
- [ ] `git diff --cached` 与待推送提交范围经过最终复核。

自动扫描只能降低误传概率，不能替代人工审阅或 GitHub Secret Scanning。
