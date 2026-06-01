# FB 竞品内容采集与筛选需求文档

## 1. 项目目标

交付一个业务人员可在 Codex 中通过自然语言使用的 `fb-competitor-collector` skill，用于采集 Facebook 竞品账号和内部主页账号的可见帖子内容，统一沉淀为本地内容库，并同步到飞书在线表格 `FB竞品帖子链接`。

第一阶段主线：

```text
业务人员打开已登录 Facebook 页面
-> OpenCLI Browser Bridge 读取当前 Chrome 标签页可见帖子
-> 用主页相对时间确定候选窗口
-> 打开帖子详情页确认精确时间与评论引流链接
-> 标准化字段
-> SQLite 本地去重入库
-> 飞书表格同步
-> 按日期、账号类型、帖子类型、浏览量、点赞量筛选
```

## 2. 当前业务背景

当前业务流程是：

1. 采集竞品 Facebook 账号发文链接；
2. 将竞品帖子链接和文章链接提供给 AI；
3. AI 根据竞品内容生成仿写文章；
4. 文章发布到站内；
5. 再基于文章生成对应的 FB 引流帖；
6. 最后发布到自己的主页进行引流。

当前痛点：

1. 竞品链接采集不完整；
2. 重复输出会影响后续人工处理；
3. 抓取结果没有统一内容库；
4. 抓取阶段提前过滤会导致全量数据无法复用；
5. 业务侧希望结果直接进入飞书在线表格；
6. 后续需要每日增量更新。

## 3. 已确认边界

第一阶段必须完成：

1. 从飞书账号来源表读取竞品账号和内部主页账号；
2. 通过 OpenCLI Browser Bridge 读取业务人员当前正常 Chrome 标签页中可见的 Facebook 帖子；
3. 标准化帖子链接、文章链接、故事概要、账号类型、发帖时间、互动数据等字段；
4. 本地 SQLite 保存全量内容并按 canonical post URL 去重；
5. 同步结果到飞书输出表 `FB竞品帖子链接`；
6. 支持按日期、账号类型、帖子类型、浏览量、点赞量筛选；
7. 浏览量/点赞量不可见时留空，并备注“互动数据未确认”。

第一阶段暂不做：

1. 自动生成文章；
2. 自动发布站内；
3. 自动生成 FB 引流帖；
4. 爆款题材相似匹配；
5. 多 agent 串联发布流程。

## 4. 采集对象

需要采集两类账号：

| 类型 | 用途 | 要求 |
| --- | --- | --- |
| 竞品账号 | 采集竞品发文、帖子链接、文章链接和可见互动数据 | 数据标记为 `competitor` |
| 内部主页账号 | 分析自己主页已验证题材，供后续题材复用 | 数据标记为 `internal`，并打备注 |

账号清单由业务侧在飞书账号来源表维护。工具不保存账号密码、cookie、token。

## 5. 飞书文档配置

账号来源表只读：

```text
Wiki: https://pic6ktmsyi.feishu.cn/wiki/QzfUwyYyTi3zt7kl7TDcSzZKn3f?sheet=oZg2HR
Spreadsheet: https://pic6ktmsyi.feishu.cn/sheets/QkRSshqQDh2dfWtfLtLcikWKnIb
Sheet id: oZg2HR
```

结果输出表只写：

```text
Wiki: https://pic6ktmsyi.feishu.cn/wiki/BqkSw67zgiYlbikZWx3cqwZ5nAf
Spreadsheet: https://pic6ktmsyi.feishu.cn/sheets/Md8As2SJzhyuBHtMuOmcLqy3nyf
Sheet id: 44013b
```

重要规则：

1. 采集结果只能写入输出表；
2. 账号来源表只用于读取账号清单；
3. 飞书 CLI 必须以用户身份写入；
4. 写入前需要确认 `identity=user` 且 `tokenStatus=valid`。

## 6. 采集字段

第一阶段保存字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| account_name | 尽量 | 账号名称 |
| account_url | 是 | 账号主页链接 |
| account_type | 是 | `competitor` 或 `internal` |
| post_url | 是 | Facebook 帖子链接 |
| canonical_post_url | 是 | 去重用标准帖子链接 |
| raw_fb_url | 是 | 实际抓到的 FB 内容链接，可为 post/reel/photo/watch/video |
| parent_post_url | 尽量 | 父帖链接；只作为优先去重依据，抓不到不丢候选 |
| fb_link_kind | 是 | FB 内容链接类型 |
| post_type | 尽量 | 视频 / 文本 / 图片 / 其他 |
| posted_date | 尽量 | 发帖日期，格式 `YYMMDD` |
| posted_at | 最终入表必填 | 详情页确认后的小时级或分钟级时间，格式 `2026年5月29日 12:32` |
| relative_time_text | 是 | 主页可见相对时间，如 `3h`、`12h`、`1d`，仅用于候选窗口和滚动边界 |
| article_url | 尽量 | 外部文章链接 |
| lead_url_raw | 最终入表必填 | 评论区或评论回复中的原始引流链接 |
| landing_url | 最终入表必填 | 引流链接最终落地 URL |
| lead_link_status | 最终入表必填 | `qualified` 表示已确认落到外部网站 |
| lead_link_source | 最终入表必填 | `comment` 或 `comment_reply` |
| story_summary | 尽量 | 帖子正文或故事概要 |
| views | 尽量 | 浏览量 |
| likes | 尽量 | 点赞量 |
| engagement_raw | 否 | 页面可见互动文本 |
| crawled_at | 是 | 采集时间 |
| note | 否 | 缺失字段或异常说明 |
| raw_payload | 是 | 原始提取结果，便于排查 |

业务当前可接受的最小数据结构：

```text
帖子链接
文章链接
简述
```

注意：最小结构用于手工导入候选；正式输出表要求有发帖时间、评论/回复引流落地链接和基于落地页的中文摘要。精确时间优先；如果只能抓到相对时间，则按采集时间估算，并在表格发帖时间中标注 `约`。采集阶段先保留候选，字段不完整时标记 `needs_enrichment`，不静默丢弃。

如果互动数据不可见，字段留空，不编造。

时间确认要求：

1. 主页相对时间只用于决定候选范围，不能直接换算为正式 `posted_at`；
2. 对“今天”这类请求，先采集 `3h`、`12h` 等候选，看到稳定 `1d` 可作为滚动边界；
3. 每条候选必须进入帖子详情页，通过 tooltip 或 DOM 精确时间属性确认 `posted_at`；
4. 只有详情页精确时间属于目标日期时，才能写入该日期的正式结果。

引流链接要求：

1. 最终入表的文章链接必须来自账号在评论区或评论回复中发布的引流链接；
2. 如果详情页同时出现广告位外链、推荐流外链或其他非评论链接，不能覆盖已经确认的评论引流链接；
3. 故事概要必须基于最终 `landing_url` 对应的文章内容，不允许用广告页标题或 Facebook 帖子正文代替。

## 7. 日期与采集频率

业务初始期望：

1. 首次采集近 1 个月发文数据；
2. 后续每天早上 10 点采集过去 24 小时帖子数据。

当前实现约束：

1. Facebook 页面不提供稳定的日期范围接口；
2. 实时采集依赖业务人员打开并加载目标页面；
3. 第一版优先保证当前可见页面采集、入库、去重、同步和筛选稳定；
4. 每日自动化需要在 OpenCLI Browser Bridge 当前页采集稳定后再单独配置固定运行机和执行方式。

## 8. 筛选需求

第一阶段支持：

1. 浏览量大于指定阈值，默认 100000；
2. 点赞量大于指定阈值，默认 100；
3. 指定日期；
4. 指定日期范围；
5. 指定账号类型：竞品账号 / 内部主页账号；
6. 指定帖子类型。

筛选阈值在 `config/settings.yaml` 中配置，不写死。

## 9. 业务使用方式

业务人员在 Codex 中用自然语言操作，例如：

```text
使用 FB 竞品采集 skill，检查一下现在能不能用。
```

```text
使用 FB 竞品采集 skill，试一下目标 Facebook 页面能不能抓到帖子正文，不要写飞书。
```

```text
使用 FB 竞品采集 skill，采集目标 Facebook 页面里可见的帖子，并同步到飞书。
```

```text
使用 FB 竞品采集 skill，筛选 5 月 21 日的竞品帖子，并写入飞书筛选结果。
```

## 10. 验收标准

第一阶段验收标准：

1. 能读取飞书账号来源表；
2. 能确认飞书 CLI 为用户身份；
3. 能确认 OpenCLI Browser Bridge 可用；
4. 能从正常 Chrome 已登录页面读取真实帖子正文；
5. 能提取帖子链接、文章链接、故事概要；
6. 能写入 SQLite；
7. 重复帖子不会重复入库；
8. 能同步到正确的飞书输出表；
9. 不会写入账号来源表；
10. 能按日期和账号类型筛选；
11. 浏览量/点赞量缺失时不阻塞流程；
12. 插件不可用或页面不可见真实帖子时停止，不写假结果。

## 11. 当前实现事实

当前正式实时采集入口是 OpenCLI Browser Bridge，不保留 Codex Chrome Extension 或其他浏览器实时采集入口。OpenCLI 只负责浏览器连接、tab 绑定、页面 eval、滚动、hover 和详情页打开；Facebook 业务字段仍由项目内脚本决定。

项目内主要脚本职责：

1. `scripts/check_env.py`：检查平台、`lark-cli`、OpenCLI CLI/daemon/Browser Bridge、飞书读写配置和推荐采集路线；
2. `scripts/read_accounts.py`：从飞书账号来源表读取竞品/内部账号，支持竞品列、内部列和通用账号列；
3. `scripts/opencli_extract_current_tab.mjs`：当前 Chrome Facebook tab 首页候选提取参考入口；
4. `scripts/prepare_capture_result.py`：把首页候选标准化，保留短帖、媒体链接和缺字段候选为 `needs_enrichment`；
5. `scripts/opencli_enrich_post_details.mjs`：打开候选详情页，确认精确时间，展开评论/回复，解析账号自发引流链接，并按目标日期过滤；
6. `scripts/enrich_article_summaries.py` 与 `scripts/apply_article_summaries.py`：基于落地页材料生成或应用中文概要；
7. `scripts/output_quality.py`：最终输出质量门禁；
8. `scripts/field_schema.py`：飞书 A-K 输出列、表头别名和账号来源表表头识别。

当前飞书输出表使用 A-K 列：`账号`, `账户类型`, `帖子链接`, `帖子类型`, `发帖时间`, `文章链接`, `故事概要`, `互动数据（点赞量）`, `浏览量`, `是否采用`, `对应站内链接`。这个顺序写在 `config/settings.yaml` 的 `feishu.field_schema.output_headers`，代码实现以 `scripts/field_schema.py` 为准。
