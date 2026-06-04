# FB 竞品采集操作手册

这份文档给业务使用者和帮业务执行的 Codex 看。正常使用时，不需要手动拼脚本，直接在 Codex 里说要采集什么。

## 一句话流程

```text
你发采集指令
-> Codex 检查飞书、OpenCLI、Chrome/Facebook 状态
-> 能自动修复的先自动修复
-> 不能自动修复的中断并告诉你怎么处理
-> 后台打开目标账号页并采集
-> 自动补抓缺字段
-> 字段完整后写入 FB竞品帖子链接
-> Codex 汇报账号、帖子数、完整度和异常
```

## 常用说法

检查能不能用：

```text
检查 FB 竞品采集工具现在是否准备好。
```

采集全部账号：

```text
采集全部目标账号过去 24 小时的帖子，并同步到飞书。
```

采集某个账号：

```text
采集这个 Facebook 账号今天的帖子，并同步到飞书：<账号主页链接>
```

补抓某条帖子：

```text
补抓这条 Facebook 帖子，字段完整后同步到飞书：<帖子链接>
```

导入已有结果：

```text
把这个 JSON/CSV 抓取结果导入内容库：<文件路径>
```

只筛选本地库：

```text
筛选 5 月 21 日的竞品帖子。
```

## Codex 会自动做什么

- 读取本次范围：全部账号、单账号、单帖、库内补抓、库外导入。
- 检查 `config/settings.yaml`。
- 检查飞书来源表、输出表和 `lark-cli` 用户身份。
- 遇到可恢复的 Feishu token 状态时先自动刷新。
- 检查 OpenCLI daemon 和 Browser Bridge。
- OpenCLI daemon 没启动时先自动恢复。
- 通过 OpenCLI 在同一个正常 Chrome profile 里打开后台账号页。
- 从账号主页顶部发现候选帖子。
- 打开详情页补精确发帖时间、评论/回复引流链接、互动数据、帖子类型和文章材料。
- 生成/应用基于文章材料的中文故事概要。
- 不完整记录留在 SQLite 和补抓队列。
- 只有完整记录通过质量门后，才写入正式飞书表。

## 什么时候需要你手动处理

出现以下情况时，Codex 会停止采集并说明处理步骤：

- Facebook 已退出登录。
- 页面是游客预览，或只看到一条预览帖子。
- 出现验证码、风控、权限页面。
- Chrome profile 不对。
- OpenCLI Browser Bridge 扩展没有连接到正在使用的 Chrome profile。
- 目标账号主页没有真实加载出帖子列表。

处理后，直接让 Codex 按它输出的 `next_commands` 或原始采集指令继续。

## 正式入表标准

普通“同步到飞书”只写完整结果。每条正式输出必须满足：

- 有有效 Facebook 帖子/视频/图片/Reel/分享链接。
- 发帖时间来自详情页精确时间，不使用 `1h`、`12h` 这类相对时间估算。
- 引流链接来自账号自己的评论、评论回复，或当前主帖里的明确 CTA。
- 引流链接最终落到 Facebook/Meta 之外的网站。
- 有文章/落地页材料。
- 故事概要是中文，并基于文章材料生成。
- 帖子类型、互动数据和覆盖状态通过当前质量检查。

缺字段的候选不会丢，会留在本地继续补抓；但不会被普通 `--sync` 写进正式飞书表。

## 结果汇报怎么看

Codex 最后应该汇报：

- 本次尝试了哪些账号。
- 每个账号发现多少候选帖子。
- 每个账号最终写入/可用多少条。
- 是否所有必填字段都完整。
- 剩余缺口属于覆盖、精确时间、引流链接、互动数据、帖子类型、文章材料、故事概要、飞书同步还是人工登录问题。
- 是否存在几乎没有抓到字段的特殊帖子。

只要 `run_status` 不是 `complete`，就说明本次业务流程还没完全完成。

## 工程命令速查

检查环境并尽量自动修复：

```bash
python3 scripts/check_env.py --config config/settings.yaml --fix-auth --fix-opencli
```

采集全部账号：

```bash
python3 scripts/run_accounts_job.py --config config/settings.yaml --last-hours 24 --sync
```

采集单账号：

```bash
python3 scripts/run_account_job.py --config config/settings.yaml --account-url "<facebook-account-url>" --last-hours 24 --sync
```

续跑单账号：

```bash
python3 scripts/run_account_job.py --config config/settings.yaml --account-url "<facebook-account-url>" --target-date YYMMDD --resume-only --force-recover-running --sync
```

读取账号配置：

```bash
python3 scripts/read_accounts.py --config config/settings.yaml
```

本地测试：

```bash
python3 tests/test_local_pipeline.py
```

## 不要做的事

- 不要让业务人员手动写脚本命令，除非他们明确要求。
- 不要用 Playwright、CDP-only、旧 Chrome Extension、userscript 替代 OpenCLI Browser Bridge 做实时 Facebook 采集。
- 不要写入账号来源表。
- 不要把游客预览数据导入或同步。
- 不要因为帖子短、是图片、视频、Reel、watch 或缺父帖链接就丢弃候选。
- 不要用主页相对时间当正式发帖时间。
- 不要用 Facebook 正文、文章标题、meta 描述或英文原文冒充故事概要。
- 不要把 `data/`、`exports/`、Chrome profile、SQLite 数据库当源码提交。
