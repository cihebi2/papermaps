# OpenAlex 文献图谱与追踪通知实施计划

- 创建时间: 2026-02-25 10:10:59
- 时区: 本地系统时间
- 状态: Draft v1（用于分步落地）

## 1. 目标

围绕用户给定的目标论文（DOI 列表），实现以下能力：

1. 自动获取目标论文的标题、作者、摘要、参考文献。
2. 以参考文献为起点做递归扩展，构建“文献关系图谱”并可视化。
3. 追踪“最新引用这些文献”的新论文。
4. 基于语义相关内容做持续监控，并发送通知。

## 2. 总体方案（四层流水线）

1. 种子层（Seed Ingestion）
- 输入 DOI，解析到 OpenAlex Work（可辅以 Crossref）。
- 存储核心元数据：`papers`。

2. 图谱层（Citation Graph）
- 后向扩展：参考文献（references）。
- 前向扩展：引用关系（cites）。
- 存储节点和边：`papers` + `edges`。

3. 追踪层（Latest Citation Tracking）
- 按时间窗口拉取新引用论文。
- 增量更新，避免重复抓取。

4. 语义层（Semantic Monitoring & Alert）
- 基于关键词与语义相似度检索相关新文献。
- 命中后触发通知通道（飞书/邮件/企业微信）。

## 3. 分阶段拆解

## Phase 0: 项目骨架与配置

- 目标: 建立可运行的工程结构和配置体系。
- 交付物:
  - `src/` 代码骨架
  - `config.yaml`（API key、通知配置、调度配置）
  - `data/`（SQLite 数据库目录）
  - `docs/`（运行与维护文档）
- 任务:
  - 设计目录结构与模块边界。
  - 初始化 SQLite（`papers`、`edges`、`watch_targets`、`alerts`、`runs`）。
  - 增加 `.env`/配置加载与日志。
- 验收标准:
  - `python -m ... --help` 可运行。
  - 数据库初始化命令可重复执行且幂等。

## Phase 1: DOI 入库 + 引文图谱（MVP）

- 目标: 从 DOI 列表生成可视化图谱（depth 可控）。
- 交付物:
  - `ingest_doi` 命令
  - `expand_graph` 命令（BFS，`--depth`、`--max-nodes`）
  - 图谱导出：`graph.gexf` + `graph.html`
- 任务:
  - DOI -> OpenAlex Work 解析。
  - 参考文献抓取（优先 OpenAlex，必要时 Crossref 补齐）。
  - 节点去重（`openalex_id` 优先，DOI 兜底）。
  - 关系写入 `edges(type=references)`。
- 验收标准:
  - 输入 3~5 篇 DOI，能输出图谱文件并打开查看。
  - 节点、边数量与抓取日志一致。

## Phase 2: 最新引用追踪（增量）

- 目标: 自动发现“谁在最新引用目标集合”。
- 交付物:
  - `track_latest_citations` 命令
  - 增量游标机制（按日期/上次运行时间）
  - 新增关系：`edges(type=cites)`
- 任务:
  - 对受监控论文集合执行 `cites:W...` 检索。
  - 维护 `last_check_date` 和 `seen_work_ids`。
  - 仅写入新增记录并保留运行审计日志。
- 验收标准:
  - 连续两次运行，第二次无重复写入。
  - 新论文命中后能在数据库和图谱中看到新增关系。

## Phase 3: 语义监控 + 通知

- 目标: 针对关注主题进行“新论文语义相关”预警。
- 交付物:
  - `semantic_watch` 命令
  - 通知模块（飞书 webhook/邮件二选一先落地）
  - 命中记录表 `alerts`
- 任务:
  - 建立主题配置（关键词、过滤条件、最低相关阈值）。
  - 低成本模式：`title_and_abstract.search`。
  - 升级模式：`find/works` 语义相似检索（可选）。
  - 命中去重与通知防抖（同一论文只通知一次）。
- 验收标准:
  - 人工注入测试样例后可收到通知。
  - 重复运行不会重复推送同一条。

## Phase 4: 稳定性与运营

- 目标: 将系统变成可长期运行的服务。
- 交付物:
  - 定时任务（APScheduler/cron）
  - 失败重试、速率限制、监控日志
  - 每周摘要报告（新增论文、热点主题）
- 任务:
  - API 限流与失败重试策略。
  - 异常告警与回放（按 run_id 重跑）。
  - 数据备份与迁移脚本。
- 验收标准:
  - 连续运行 7 天无阻塞错误。
  - 可追溯每次抓取和通知记录。

## 4. 数据模型（初版）

1. `papers`
- `openalex_id` (PK), `doi`, `title`, `abstract`, `publication_date`, `journal`, `cited_by_count`, `raw_json`, `created_at`, `updated_at`

2. `edges`
- `src_openalex_id`, `dst_openalex_id`, `edge_type` (`references`/`cites`), `discovered_at`, `run_id`

3. `watch_targets`
- `openalex_id`, `watch_type` (`seed`/`expanded`/`semantic`), `enabled`, `last_check_date`

4. `alerts`
- `alert_id`, `openalex_id`, `topic`, `reason`, `score`, `notified_at`, `channel`, `dedupe_key`

5. `runs`
- `run_id`, `job_type`, `started_at`, `ended_at`, `status`, `stats_json`, `error_message`

## 5. 近期执行顺序（一步步实现）

1. 先做 Phase 0 + Phase 1（MVP），不做通知。
2. 图谱跑通后做 Phase 2（最新引用增量追踪）。
3. 最后接 Phase 3（语义监控和通知）。

## 6. 本周实施清单（建议）

1. Day 1: 初始化工程、数据库、配置加载、日志。
2. Day 2: DOI 入库与 reference 扩展。
3. Day 3: 图谱导出（GEXF + HTML）与结果校验。
4. Day 4: 最新引用追踪与增量去重。
5. Day 5: 飞书/邮件通知打通 + 端到端联调。

## 7. 风险与控制

1. OpenAlex/来源库统计口径差异导致参考文献数量不一致。
- 控制: 保留来源字段与原始 payload，双源交叉验证。

2. 图谱扩展过深导致节点爆炸。
- 控制: 强制 `max_depth`、`max_nodes`、`max_edges`。

3. 通知噪声高。
- 控制: 阈值、白名单主题、去重和冷却时间。

