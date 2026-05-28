# FB 竞品内容采集与筛选解决方案

## 1. 方案总览

`fb-competitor-collector` 是本项目唯一业务入口。实时 Facebook 采集只使用 Codex Chrome Extension 读取业务人员正常 Chrome 中已经登录、已经打开、肉眼可见的 Facebook 页面。

核心链路：

```text
飞书账号来源表
  -> 读取账号清单
  -> 业务人员在正常 Chrome 打开目标 Facebook 页面
  -> Codex Chrome Extension 读取当前标签页 DOM
  -> fb_dom_extractors.js 提取帖子候选
  -> normalize_post 标准化字段
  -> SQLite 按 canonical_post_url 去重
  -> lark-cli 以用户身份写入 FB竞品帖子链接
  -> filter_posts.py 条件筛选并写入筛选结果
```

如果 Codex Chrome Extension 不可用，实时采集直接停止并提示修复插件/profile 设置。

如果 Facebook 页面出现登录入口、游客预览或只显示一条预览帖子，系统必须立即停止并返回 `human_intervention_required`。这种情况不能继续滚动试探，也不能写入飞书，因为 Facebook 常见行为是游客态只暴露少量预览内容。

## 2. 关键技术决策

1. 业务人员只使用一个 skill：`fb-competitor-collector`。
2. Facebook 实时采集只读取正常 Chrome 当前页面，不启动其他浏览器会话。
3. 采集阶段只做全量发现，不做热门筛选。
4. 本地 SQLite 保存全量内容，用 `canonical_post_url` 去重。
5. 飞书账号来源表只读，输出表只写。
6. 飞书 CLI 必须强制用户身份。
7. 页面没有真实帖子正文时不入库、不写飞书。

## 3. 文件结构

```text
fb-competitor-collector/
  SKILL.md
  README_FOR_OPERATOR.md
  agents/
    openai.yaml
  config/
    settings.yaml
    settings.yaml.example
  docs/
    business_usage.md
    fb_competitor_requirements.md
    fb_competitor_solution.md
  samples/
    sample_posts.json
    sample_posts_13_with_duplicates.json
  scripts/
    check_env.py
    chrome_extension_extract_current_tab.mjs
    config_loader.py
    fb_dom_extractors.js
    filter_posts.py
    import_existing_result.py
    lark_io.py
    models.py
    read_accounts.py
    store.py
    sync_feishu.py
  tests/
    test_local_pipeline.py
```

## 4. 配置

核心配置在 `config/settings.yaml`：

```yaml
lark_cli_path: auto
codex_home: auto
codex_chrome_plugin_base: auto
timezone: Asia/Shanghai
database_path: data/posts.sqlite

platform_overrides:
  darwin:
    lark_cli_path: /Users/a1/.npm-global/bin/lark-cli
  windows:
    lark_cli_path: lark-cli.cmd

feishu:
  source_spreadsheet_url: "https://pic6ktmsyi.feishu.cn/sheets/QkRSshqQDh2dfWtfLtLcikWKnIb"
  output_spreadsheet_url: "https://pic6ktmsyi.feishu.cn/sheets/Md8As2SJzhyuBHtMuOmcLqy3nyf"
  sheets:
    accounts: "oZg2HR"
    all_posts: "44013b"
    daily_new: "44013b"
    filter_result: "44013b"
    errors: "异常记录"

crawl:
  initial_days: 30
  daily_hours: 24
  default_run_time: "10:00"
  dedupe_key: post_url
  live_capture_route: codex_chrome_extension

filters:
  hot_views: 100000
  hot_likes: 100
```

配置规则：

1. 不保存密码、cookie、token；
2. `lark_cli_path: auto` 和 `codex_home: auto` 会按当前系统自动解析 Mac/Windows 路径；
3. 账号来源表和输出表必须分开。

如果 Windows 业务机没有把 `lark-cli.cmd` 放进 PATH，只需要在 `platform_overrides.windows.lark_cli_path` 写入完整安装路径。

## 5. 飞书数据通道

使用 `lark-cli` 读写飞书普通表格：

1. `scripts/read_accounts.py` 读取账号来源表；
2. `scripts/lark_io.py` 统一封装读写；
3. 写入前调用 `lark-cli auth status`，必须满足：
   - `identity=user`
   - `tokenStatus=valid`
4. 项目要求：
   - `lark-cli config default-as user`
   - `lark-cli config strict-mode user`

当前文档：

```text
账号来源表：QkRSshqQDh2dfWtfLtLcikWKnIb / oZg2HR
结果输出表：Md8As2SJzhyuBHtMuOmcLqy3nyf / 44013b
```

## 6. 采集实现

实时采集由 Codex Chrome Extension 执行：

1. 列出用户当前打开的 Chrome 标签页；
2. 选择业务人员肉眼可见帖子列表的 Facebook 标签页；
3. claim 该标签页；
4. 在页面内执行 `scripts/fb_dom_extractors.js` 的 DOM 提取逻辑；
5. 过滤掉登录页、空白动态壳、评论片段和无正文候选；
6. 得到帖子链接、文章链接、正文/概要、时间文本、互动文本；
7. 标准化后入库。

采集阶段不得因为链接形态过早丢弃内容。`/posts/`、`story.php`、`permalink.php`、`reel`、`photo.php`、`watch`、`videos` 都先作为 FB 内容候选保存。父帖链接只作为优先去重依据；抓不到父帖时保留原始内容链接，后续再做相似度/人工复核去重。

详情补全阶段会打开每条候选内容，展开评论和评论回复，寻找账号主发的引流链接。只有当该链接最终解析到外部网站，并且发帖时间、落地页摘要等字段齐全时，记录才进入 `ready_for_output` 并允许写入飞书最终表。发帖时间优先使用 Facebook 精确时间；只能抓到相对时间时按采集时间估算，并在飞书表格中标注 `约`。字段不完整的候选保留为 `needs_enrichment`。

登录态门禁：

1. 检测到 `登录 / 忘记账户了？`、登录表单、游客预览等信号时，立即停止；
2. 返回 `human_intervention_required`；
3. 提示业务人员在当前 Chrome profile 手动登录 Facebook；
4. 在人工确认能连续看到多条帖子前，不入库、不同步飞书。

`scripts/chrome_extension_extract_current_tab.mjs` 是该路线的可检查参考脚本。实际运行时应由 Codex Chrome plugin runtime 控制当前标签页。

## 7. 数据库设计

主表 `posts` 关键字段：

```text
account_name
account_url
account_type
post_url
canonical_post_url
post_type
posted_date
article_url
story_summary
views
likes
crawled_at
source_skill
note
engagement_raw
crawl_status
time_confirmed
coverage_note
first_seen_at
last_seen_at
raw_payload
```

约束：

1. `post_url` 唯一；
2. `canonical_post_url` 唯一；
3. 重复采集同一帖子时更新缺失字段，不新增重复行；
4. 原始结果保存到 `raw_payload` 方便排查。

## 8. 飞书输出字段

当前输出表使用业务指定 8 列：

```text
账号
帖子链接
发帖时间
文章链接
故事概要
互动数据（浏览量、点赞量）
是否采用
对应站内链接
```

输出规则：

1. 新增内容追加到输出表；
2. 筛选结果覆盖写入指定结果区域或同一 sheet；
3. 互动数据不可见时留空；
4. 不向账号来源表写入任何结果数据。

## 9. 常用执行路径

环境检查：

```bash
python3 scripts/check_env.py --config config/settings.yaml
```

读取账号：

```bash
python3 scripts/read_accounts.py --config config/settings.yaml
```

导入样例并入库：

```bash
python3 scripts/import_existing_result.py --config config/settings.yaml --input samples/sample_posts.json --no-sync
```

筛选：

```bash
python3 scripts/filter_posts.py --config config/settings.yaml --date 260521 --account-type competitor
```

筛选并 dry-run 写入飞书：

```bash
python3 scripts/filter_posts.py --config config/settings.yaml --date 260521 --account-type competitor --sync --dry-run
```

## 10. 业务操作流程

1. 业务人员在正常 Chrome 中登录 Facebook；
2. 打开目标竞品账号主页；
3. 确认页面上能看到真实帖子列表和帖子正文；
4. 在 Codex 中说：“使用 FB 竞品采集 skill，采集当前 Chrome 页面里可见的帖子，并同步到飞书”；
5. Codex 执行环境检查；
6. Codex 读取当前页面；
7. Codex 汇总提取结果，让操作者确认是否合理；
8. Codex 入库去重；
9. Codex 写入飞书输出表；
10. Codex 返回新增数、重复数、异常数和飞书写入结果。

## 11. Mac/Windows 迁移

Windows 迁移默认不再复制 Mac 路径。项目会通过 `check_env.py` 输出当前平台、实际 `lark-cli` 命令、Codex home 和 Chrome 插件包路径。迁移时只需要确认：

1. `lark_cli_path: auto` 是否能解析到 Windows 上的 `lark-cli.cmd`；
2. Codex Chrome Extension 已安装在业务使用的 Chrome profile；
3. Facebook 已在该 profile 登录；
4. 飞书 CLI 已用户身份登录；
5. 如后续启用每日任务，再配置固定机器的定时调度。

## 12. 验收标准

Mac 当前阶段必须通过：

1. `check_env.py` 能报告 Chrome Extension 是否可用；
2. 能读取飞书账号来源表；
3. 能从当前 Chrome Facebook 页面提取真实帖子正文；
4. 能导入一条样例数据；
5. 能写入 SQLite；
6. 重复导入同一帖子不会新增重复行；
7. 能按日期和账号类型筛选；
8. 能 dry-run 写入正确输出表；
9. 浏览量/点赞量缺失时不阻塞；
10. 插件不可用或页面没有真实帖子时停止。

Windows 交付前补齐：

1. Windows 上 `check_env.py` 的 `runtime` 和 `lark_cli` 检查结果；
2. Windows Chrome Extension/profile 检查结果；
3. Windows 飞书用户身份检查结果；
4. 业务人员自然语言使用说明。

## 13. 当前结论

Chrome Extension 路线已经验证能读取正常 Chrome 已登录页面中的真实帖子 DOM，并能通过 `fb_dom_extractors.js` 提取帖子候选。

因此本项目不保留其他实时采集入口。后续所有优化都围绕 Chrome Extension 当前页读取、字段提取、去重、飞书同步和筛选展开。
