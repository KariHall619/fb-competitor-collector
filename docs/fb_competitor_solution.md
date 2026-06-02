# FB 竞品内容采集与筛选解决方案

## 1. 方案总览

`fb-competitor-collector` 是本项目唯一业务入口。实时 Facebook 采集只使用 OpenCLI Browser Bridge 读取业务人员正常 Chrome 中已经登录、已经打开、肉眼可见的 Facebook 页面。

核心链路：

```text
飞书账号来源表
  -> 读取账号清单
  -> 业务人员在正常 Chrome 打开目标 Facebook 页面
  -> OpenCLI Browser Bridge 读取当前标签页 DOM
  -> fb_dom_extractors.js 提取帖子候选
  -> 主页相对时间确定候选窗口
  -> 候选先以 partial_review 入库并创建补全任务
  -> enrichment_worker 分阶段确认精确时间、评论/回复引流链接和文章材料
  -> normalize_post 标准化字段
  -> SQLite 按 canonical_post_url 去重
  -> lark-cli 以用户身份写入 FB竞品帖子链接
  -> filter_posts.py 条件筛选并写入筛选结果
```

如果 OpenCLI Browser Bridge 不可用，实时采集直接停止并提示修复插件/profile 设置。

如果 Facebook 页面出现登录入口、游客预览或只显示一条预览帖子，系统必须立即停止并返回 `human_intervention_required`。这种情况不能继续滚动试探，也不能写入飞书，因为 Facebook 常见行为是游客态只暴露少量预览内容。

## 2. 关键技术决策

1. 业务人员只使用一个 skill：`fb-competitor-collector`。
2. Facebook 实时采集优先低打扰读取已绑定的正常 Chrome Facebook 标签页；如果低打扰读取失败，为保证采集完整性回退到已验证的前台标签页操作。
3. 采集阶段只做全量发现，不做热门筛选。
4. 本地 SQLite 保存全量内容，用 `canonical_post_url` 去重。
5. 飞书账号来源表只读，输出表只写。
6. 飞书 CLI 必须强制用户身份。
7. 页面没有真实帖子正文时不入库、不写飞书。
8. 主页上的相对时间只负责确定候选范围，正式入表时间必须来自详情页精确时间。
9. 评论/回复里的账号自发引流链接优先于详情页广告位或推荐流里的外链。

## 3. 文件结构

```text
fb-competitor-collector/
  SKILL.md
  README_FOR_OPERATOR.md
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
    check_opencli_runtime_backend.mjs
    config_loader.py
    fb_dom_extractors.js
    fb_time_extractors.js
    field_schema.py
    filter_posts.py
    import_existing_result.py
    lark_io.py
    models.py
    opencli_enrich_post_details.mjs
    opencli_extract_current_tab.mjs
    opencli_runtime.mjs
    opencli_verify_exact_time.mjs
    output_quality.py
    prepare_capture_result.py
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
opencli_path: auto
opencli_session: fb-competitor
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
  field_schema:
    output_headers:
      - "账号"
      - "账户类型"
      - "帖子链接"
      - "帖子类型"
      - "发帖时间"
      - "文章链接"
      - "故事概要"
      - "互动数据（点赞量）"
      - "浏览量"
      - "是否采用"
      - "对应站内链接"

crawl:
  initial_days: 30
  daily_hours: 24
  default_run_time: "10:00"
  dedupe_key: post_url
  live_capture_route: opencli_browser_bridge

filters:
  hot_views: 100000
  hot_likes: 100
```

配置规则：

1. 不保存密码、cookie、token；
2. `lark_cli_path: auto` 和 `opencli_path: auto` 会按当前系统自动解析 Mac/Windows 路径；OpenCLI 优先使用全局命令，缺失时可通过 `npx -y @jackwener/opencli` 运行；
3. 账号来源表和输出表必须分开；
4. 飞书输出列以 `feishu.field_schema.output_headers` 和 `scripts/field_schema.py` 为准，不在导入、筛选、同步脚本里重复写一套列顺序。

如果 Windows 业务机没有把 `lark-cli.cmd` 放进 PATH，只需要在 `platform_overrides.windows.lark_cli_path` 写入完整安装路径。

## 5. 飞书数据通道

使用 `lark-cli` 读写飞书普通表格：

1. `scripts/read_accounts.py` 读取账号来源表；
2. `scripts/field_schema.py` 识别账号来源表里的竞品/内部/通用账号列；
3. `scripts/lark_io.py` 统一封装读写；
4. 写入前调用 `lark-cli auth status`，必须满足：
   - `identity=user`
   - `tokenStatus=valid`
5. 项目要求：
   - `lark-cli config default-as user`
   - `lark-cli config strict-mode user`

当前文档：

```text
账号来源表：QkRSshqQDh2dfWtfLtLcikWKnIb / oZg2HR
结果输出表：Md8As2SJzhyuBHtMuOmcLqy3nyf / 44013b
```

## 6. 采集实现

实时采集由 OpenCLI Browser Bridge 执行：

1. 绑定业务人员同一 Chrome profile 中已打开的 Facebook 标签页；
2. 优先用 OpenCLI `--tab` 直接读取目标标签页，减少主动切换；
3. 如果直接读取失败，回退到原来的 tab select 路径，确保采集效果不下降；
4. 在页面内执行 `scripts/fb_dom_extractors.js` 的 DOM 提取逻辑；
5. 过滤掉登录页、空白动态壳、评论片段和无正文候选；
6. 得到帖子链接、候选文章链接、正文、相对时间文本、互动文本；
7. `prepare_capture_result.py` 标准化候选并保留 `needs_enrichment`；
8. `opencli_enrich_post_details.mjs` 优先复用一个详情标签页确认精确时间、评论/回复引流链接和目标日期；单帖低打扰失败时回退到原来的新开详情页流程；
9. `enrich_article_summaries.py` 抓取落地页材料，`export_summary_requests.py` 导出待 Codex 生成的中文概要请求，`apply_article_summaries.py` 写入 Codex 中文摘要；
10. 普通 `--sync` 将已确认 Facebook 帖子候选写入飞书台账；缺字段用 `是否采用` 的 `待补抓：...` 标记。只有显式使用 `--strict-ready-only` 时，才只同步 `ready_for_output` 完整记录。

采集阶段不得因为链接形态过早丢弃内容。`/posts/`、`story.php`、`permalink.php`、`reel`、`photo.php`、`watch`、`videos` 都先作为 FB 内容候选保存。父帖链接只作为优先去重依据；抓不到父帖时保留原始内容链接，后续再做相似度/人工复核去重。

详情补全阶段会进入每条候选内容，先从时间 tooltip 或 DOM 属性确认精确发帖时间，再展开评论和评论回复，寻找账号主发的引流链接，并锚定当前主帖补互动数据和帖子类型。为了减少打扰，脚本优先复用一个详情标签页处理多条候选；如果复用标签页失败或采集结果变少，则对该帖子回退到原来的单帖新开详情页流程。字段完整时记录进入 `ready_for_output`；字段不完整的有效候选仍写入飞书台账并保留为 `needs_enrichment`，用 `待补抓：...` 标明缺口，后续补采后按帖子链接更新同一行。

扩量提速后的补全任务状态保存在 SQLite `enrichment_tasks` 表中，按 `canonical_post_url + stage` 去重。`run_capture_pipeline.py` 先完成 discover/prepare/import，`enrichment_worker.py` 再按 `detail_time`, `lead_link`, `engagement`, `post_type`, `article_material` 阶段恢复执行；`summary` 阶段只校验是否已应用 Codex 中文概要，不会把标题、meta 描述或英文原文摘录伪装成概要。正式 `--sync` 是台账 upsert，`--strict-ready-only` 才是只写 `ready_for_output` 完整行，`--sync-partial` 只用于不影响正式表的业务预览。

已验证的时间流程：

1. 主页列表页先用 `3h`、`12h`、`1d` 等相对时间判断采集窗口和滚动边界；
2. 这些相对时间写入 `relative_time_text`，只作为候选线索；
3. 每条候选进入详情页后，通过 OpenCLI Browser Bridge 自动 hover 时间元素，或读取 `aria-label`、`title`、`datetime`、`data-tooltip-*` 等精确时间属性；
4. 详情页解析出的 `posted_at` 才用于目标日期过滤和飞书同步；
5. 如果详情页精确时间显示帖子实际属于前一天，即使主页相对时间看起来像“今天”，也不能写入“今天”的正式结果。

已验证的链接流程：

1. 主页/详情页里属于账号评论或评论回复的引流链接是主链接；
2. 该链接通常会经过 `l.facebook.com/l.php?u=...`，标准化后写入 `lead_url_raw` 和 `landing_url`；
3. 详情页上的右栏广告、推荐流广告或其他非评论区外链不能覆盖这个主链接；
4. 文章抓取和中文概要必须基于最终 `landing_url`，不能基于广告页标题或广告落地页内容。

登录态门禁：

1. 检测到 `登录 / 忘记账户了？`、登录表单、游客预览等信号时，立即停止；
2. 返回 `human_intervention_required`；
3. 提示业务人员在同 profile 的 Facebook 标签页手动登录或确认页面；
4. 在人工确认能连续看到多条帖子前，不入库、不同步飞书。

`scripts/opencli_extract_current_tab.mjs` 是该路线的可检查参考脚本。实际运行时应由 OpenCLI Browser Bridge runtime 控制当前标签页。
`scripts/opencli_runtime.mjs` 统一封装 OpenCLI 命令解析、session bind、tab 选择和页面 eval。
`scripts/check_opencli_runtime_backend.mjs` 只检查 OpenCLI CLI/daemon/Browser Bridge 是否可用，不替代业务采集。
`scripts/opencli_verify_exact_time.mjs` 是真实 Facebook tab 上的精确时间验证器，输出 `status=exact_time_confirmed` 才代表时间链路可用。

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
output_status
time_confirmed
time_source
summary_source
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

当前输出表使用飞书表头 A-K 11 列：

```text
账号
账户类型
帖子链接
帖子类型
发帖时间
文章链接
故事概要
互动数据（点赞量）
浏览量
是否采用
对应站内链接
```

`scripts/field_schema.py` 是输出格式的唯一代码来源。它负责：

1. A-K 默认表头；
2. 表头别名到内部字段的映射；
3. `competitor/internal` 到 `竞品/内部` 的输出转换；
4. 互动数据汇总；
5. 账号来源表中竞品、内部、通用账号列的识别。

输出规则：

1. 新增内容追加到输出表；
2. 筛选结果覆盖写入指定结果区域或同一 sheet；
3. 互动数据不可见时留空；
4. 不向账号来源表写入任何结果数据。
5. `relative_estimated`、`relative_hour`、`relative_label` 这类估算时间不能进入正式同步。

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

准备 OpenCLI 首页采集结果：

```bash
python3 scripts/prepare_capture_result.py --input exports/raw.json --output exports/prepared.json --target-date 260529
```

详情补全：

```bash
node scripts/opencli_enrich_post_details.mjs --input exports/prepared.json --output exports/detail_enriched.json --target-date 260529
```

文章材料和中文摘要：

```bash
python3 scripts/enrich_article_summaries.py --input exports/detail_enriched.json --output exports/with_article_material.json
python3 scripts/apply_article_summaries.py --input exports/with_article_material.json --summaries exports/article_summaries.json --output exports/ready.json
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

1. 业务人员在正常 Chrome profile 中登录 Facebook；
2. 确认该 profile 能打开目标竞品账号主页；
3. 确认页面上能看到真实帖子列表和帖子正文；
4. 在 Codex 中说：“使用 FB 竞品采集 skill，采集目标 Facebook 页面里可见的帖子，并同步到飞书”；
5. Codex 执行环境检查；
6. Codex 读取当前主页候选，并用 `3h/12h/1d` 等相对时间决定候选窗口；
7. Codex 打开候选帖子详情页，确认精确 `posted_at`、评论/回复引流链接和落地页；
8. Codex 基于导出的落地页材料生成中文故事概要，并通过 `apply_article_summaries.py` 应用；
9. Codex 入库去重，字段不完整的候选保留为 `needs_enrichment`；
10. Codex 将已确认 Facebook 帖子候选按 A-K 表格格式 upsert 到飞书输出表；缺字段候选用 `待补抓：...` 标记，完整候选保持 `ready_for_output` 质量状态；
11. Codex 返回新增数、重复数、异常数、跳过补全数和飞书写入结果。

## 11. Mac/Windows 迁移

Windows 迁移默认不再复制 Mac 路径。项目会通过 `check_env.py` 输出当前平台、实际 `lark-cli` 命令、OpenCLI 命令和 Browser Bridge 状态。迁移时只需要确认：

1. `lark_cli_path: auto` 是否能解析到 Windows 上的 `lark-cli.cmd`；
2. OpenCLI Browser Bridge 已安装在业务使用的 Chrome profile；
3. Facebook 已在该 profile 登录；
4. 飞书 CLI 已用户身份登录；
5. 如后续启用每日任务，再配置固定机器的定时调度。

## 12. 验收标准

Mac 当前阶段必须通过：

1. `check_env.py` 能报告 OpenCLI Browser Bridge 是否可用；
2. 能读取飞书账号来源表；
3. 能从同 profile 新采集窗口的 Facebook 页面提取真实帖子正文；
4. 能导入一条样例数据；
5. 能写入 SQLite；
6. 重复导入同一帖子不会新增重复行；
7. 能按日期和账号类型筛选；
8. 能 dry-run 写入正确输出表；
9. A-K 输出列顺序和 `feishu.field_schema.output_headers` 一致；
10. 浏览量/点赞量缺失时不阻塞本地入库；
11. 估算时间、缺评论/回复引流链接、缺文章摘要的记录不会同步正式飞书输出；
12. 插件不可用或页面没有真实帖子时停止。

Windows 交付前补齐：

1. Windows 上 `check_env.py` 的 `runtime` 和 `lark_cli` 检查结果；
2. Windows OpenCLI Browser Bridge/profile 检查结果；
3. Windows 飞书用户身份检查结果；
4. 业务人员自然语言使用说明。

## 13. 当前结论

OpenCLI Browser Bridge 是当前正式实时采集入口。它负责浏览器绑定、优先低打扰的 direct tab 读取、必要时的 tab 选择、页面执行和 hover/network 等浏览器操作；Facebook 业务字段仍由本项目的 `fb_dom_extractors.js`、详情补全和质量门禁决定，不直接采用 OpenCLI 内置 `facebook feed` 的通用输出列。

因此本项目不保留其他实时采集入口。后续所有优化都围绕 OpenCLI Browser Bridge 当前页读取、字段提取、去重、飞书同步和筛选展开。
