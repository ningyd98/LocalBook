# 变更日志

本文件记录 LocalNote 的功能里程碑与修复。版本号与 `package.json` / `pyproject.toml` /
`server/__init__.py` / FastAPI `version` 保持一致。

## [1.1.0] — 2026-09-12

V1.1 把 1.0.0 之后累积的 M14 与一系列交互/可靠性改进一次性收口。**未破坏任何既有
契约**：旧配置文件、旧环境变量、旧 API 载荷与旧快照字段行为保持不变。

### 新增

- **M14 本地优先 RAG**：Markdown 感知分块 → 独立 embedding provider → SQLite 向量
  索引 → FTS5 + 向量混合检索（RRF，`k=60`）→ 可选 Link/Graph 第三路（默认关闭）→
  可选重排 → EvidencePack 证据包 → 生成 → 引用校验。端点
  `POST /rag/search`（仅检索）、`POST /rag/query`（完整链）、
  `POST /rag/index/rebuild`、`GET /rag/index/status`。含**可选 numpy 加速内核**
  与 **OpenAI-compatible 重排**（兼容 Jina/Cohere/vLLM 响应形状）。详见下方
  [M14] 小节与 [`docs/rag-architecture.md`](./docs/rag-architecture.md)。
- **多供应商 AI 档案（PLAN-PROVIDERS）**：`settings.ai.profiles[]` +
  `active_profile_id`，内置 oMLX / OpenAI / DeepSeek / Moonshot / 自定义模板，
  一键切换、密钥只写入不回显、上限 30 个。详见
  [`PLAN-PROVIDERS.md`](./PLAN-PROVIDERS.md)。
- **回收站（软删除）**：文件与文件夹整体移入服务自有的 `.localnote/trash/`，
  默认保留 **30 天**；`GET/POST /api/v1/trash`、单项恢复（可占位改名）、彻底删除、
  清空。`DELETE /vault/file` 保留为字节级永久删除原语。
- **嵌套文档与分级收缩**：文件树、编辑器正文、预览区、标签页四类右键入口；
  纯文件夹约定（`Notes/A.md` → `Notes/A/`），子文档自动补 `[[链接]]`，
  重命名时按所有者链同步改写上层链接。
- **字体与字号体系**：四套本地字体栈（含中文回退，不联网）、作用范围
  （整个界面 / 仅正文）与 **80%–160% 整体缩放**；168 处硬编码 `font-size`
  收敛为 `--fs-*` 令牌。
- **分屏同步滚动**：按相对位置双向联动，持续解析 + 自动重绑（切换笔记后仍有效），
  文档工具栏可切换。

### 修复

- **设置面板渲染崩溃**：`current` 存在但 `ai` 缺失时不再抛 `TypeError` 卸载整棵
  设置树。
- **AI 供应商保存失败**：数字输入清空不再被 `Number("") === 0` 当成 `0` 送出并
  触发 422；越界值在表单内直接列出区间并禁用保存；`invalid_request` 文案不再误用
  附件专用提示。
- **「后端比页面旧」难以定位**：`ApiError` 现在携带失败端点，兜底文案改为可诊断
  形式（如 `HTTP 404 · /settings/ai/profiles`）。
- **shell 代理导致 500**：所有出站客户端默认 `trust_env=False`，避免 `NO_PROXY`
  含 IPv6 字面量时抛 `InvalidURL` 而绕过错误映射；需要时用
  `LOCALNOTE_AI__USE_ENV_PROXY` / `LOCALNOTE_RAG__USE_ENV_PROXY` 开启。
- **文件删除从未生效**：前端此前没有接上 `DELETE /api/v1/vault/file`。

### 工程与门禁

- **版本号统一到 1.1.0**：根 `package.json`、全部 workspace 包、`pyproject.toml`、
  `server/__init__.py`；FastAPI `version` 改为引用 `server.__version__`，
  消除双份字面量。
- **README 重写为项目整体介绍**；端点一览、错误契约与环境变量全表迁至
  [`docs/api-reference.md`](./docs/api-reference.md)。
- `scripts/rag-eval.sh`：RAG 全量测试 + Golden Dataset 评估 + 门槛校验
  （低于门槛以非零码退出并打印 `REGRESSION`）。

### 实测（2026-09-12）

- 后端 `tests/backend`：**928 passed, 3 skipped**。
- 本地 RAG `tests/rag`：**272 passed, 2 skipped**。
- 前端 Vitest：**362 passed, 3 skipped**（43 个文件，1 个跳过）。
- 全部 workspace 包 TypeScript typecheck 通过；Vite 生产构建通过。

## [M14] — RAG（检索增强生成，2026-09-10 完成）

在 v1.0.0（M0–M13）之上新增完整的 local-first RAG 链路，**不改动**已有 Vault /
FTS / AI / Markdown / Metadata / Links / Graph 实现，也不实现 Obsidian 插件运行时。

### 新增能力

- **Markdown 感知分块**（`server/rag/chunking/markdown.py`）：按标题层级、段落、
  列表、引用分块并保留代码块/表格/frontmatter 完整性；每块携带
  `chunk_id/document_id/path/content_hash/heading_path/start_line/end_line/
  start_offset/end_offset/tags/aliases/chunk_index`；`content` 永远是源文件切片
  （`text[start:end]` 可校验）。
- **独立 EmbeddingProvider**：`hash`（离线回退，状态中标为 degraded）与
  OpenAI-compatible 端点；维度自动发现、失败映射为稳定错误码；Chat 模型与
  embedding 模型完全解耦。
- **SQLite 向量索引**：`rag_documents`/`rag_chunks`/`rag_chunks_fts`/
  `rag_embeddings`/`rag_index_state`（additive、可删除重建）；文档级 float32
  向量 run + 纯 Python 点积扫描（≈720 chunk 中位 8 ms，无需 Qdrant/Milvus）。
- **混合检索**：FTS5（chunk 级）+ 向量，RRF 融合（`k=60`，可配置）；BM25 与
  余弦分不做未归一化线性相加；`KeywordRetriever`/`VectorRetriever`/
  `HybridRetriever` 分实现；`LinkRetriever` 已在后续小阶段落地（见下），
  `GraphRetriever` 仍未单独实现。
- **可选重排**：`RerankProvider` 协议 + 本地 `LexicalOverlapReranker`，默认关闭；
  重排失败只降级不中断。
- **EvidencePack + ContextBuilder**：去重、合并同一文档相邻块、总/单文档
  token 预算、保留 path/heading/行号来源信息；模型只看到编号证据块。
- **Grounding 与引用校验**：`RagService` 检索→融合→证据包→生成→校验；
  伪造 `[S99]` 被删除并记入 `invalid_citations`；无有效引用时回答固定
  "根据当前知识库内容，没有找到足够证据回答这个问题。"；模型不可用时返回证据清单。
- **增量索引**：watcher create/modify/delete/move → 去抖 1.5 s → 内容 hash 判定
  → 仅重新嵌入变化的 chunk；rename（内容不变）不重新嵌入；embedding 失败标记
  `pending/failed` 且不影响编辑、FTS、启动。
- **API**：`POST /rag/query`、`POST /rag/search`（不调用 Chat）、
  `POST /rag/index/rebuild`、`GET /rag/index/status`；RAG 故障返回 503
  `rag_unavailable`，其余端点不受影响。
- **前端**：AI 检查器新增「知识库检索 / Vault RAG」模式（`RagPanel`），
  展示答案、检索统计与**可点击的真实来源**；原有「当前笔记」模式不变。
- **评估**：`tests/rag/eval/` 固定 Golden Dataset（15 篇 / 14 问，中英混合、
  同义改写、易混淆主题），输出 Recall@5 / Recall@10 / MRR；
  `python -m tests.rag.eval.run --vault <tmp>` 可复现。

### 后续增强（同一阶段内完成）

- **可选 numpy 加速内核**（`server/rag/vector/kernels.py`）：numpy 存在时向量扫描
  用矩阵点积（20k×256 首查约 86 ms、稳定态约 1.5 ms），否则回退纯 Python 内核
  （约 190–215 ms）。numpy **不是声明依赖**，缺失/异常自动回退，
  `/rag/index/status` 的 `vector_kernel` 如实报告当前内核。
- **可选 HTTP 重排**（`server/rag/rerank/openai_compatible.py`）：`POST {base}/rerank`
  兼容 Jina/Cohere/vLLM 两种响应形状；配置缺失或失败只降级到融合排序；
  新增 `POST /api/v1/settings/rag/reranker/test` 探活端点。
- **评估新增 rerank 模式**：`python -m tests.rag.eval.run` 现在同时输出
  `hybrid_rerank`，实测 MRR 0.917 → 0.952（recall@5 保持 1.000）。

### Link/Graph 加权检索（roadmap 第 ③ 项，同一阶段内完成）

- **新增 `LinkRetriever`**（`server/rag/retrieval/link.py`）：从"查询已命中的笔记"
  出发做图扩展，四信号加权——wikilink 出边 `1.0`、backlink 入边 `0.8`、
  same-tag `0.6`、2-hop 图邻居 `0.4`；得分 = 去重 seed 计数 × 权重，
  排序 `(-score, path)` 完全确定。图取自 `DerivedIndexService.graph_snapshot()`
  （**只读派生数据**，不写 SQLite、不写 Vault）。断链 `[[missing]]` 与非索引路径
  一律不返回；chunk 级命中给真实 `chunk_id`/行号/`content_hash`，无 chunk 时降级为
  `doc::<path>` + `document_level=True` 并**不编造行级区间**。
- **接入 RRF 第三路**（`HybridRetriever`）：默认 `link_add_only=True`——图扩展只
  **新增**直接路没返回的文档，不重排已有命中。这是实测要求的：非加性全量融合会把
  直接路已召回的低位笔记抬到第 1 位，**MRR 0.917 → 0.558**；即使把 RRF 权重降到 0.1
  也只回到 0.911，仍低于 baseline。
- **配置与前端暴露**：`RagSettings` 新增 6 个字段 `link_retrieval_enabled`
  （默认 `False`）、`link_top_k=20`、`wikilink_weight=1.0`、`backlink_weight=0.8`、
  `tag_weight=0.6`、`graph_weight=0.4`；`PATCH /api/v1/settings/rag` 支持这 6 项
  （`extra="forbid"`，未知键 422）；前端新增开关 + 5 个权重输入（仅开关打开时显示）
  + 状态卡（非 `"enabled"` 时提示降级，不把"已配置"伪装成"已启用"）。
- **降级不隐藏、不阻断**：关闭时 `link_retrieval=""`（工厂不接线，行为与 ③ 之前
  逐位相同）；开启但派生索引不可用 → 仍接线并报 `"link_unavailable"` + WARNING；
  构造异常 → `None` + 日志，绝不阻断 RAG 启动；按查询无锚点 → `stats.degraded`
  记 `link_no_seed`。
- **实测结论：中性，零增益，故默认关闭。** `hybrid_link` 的 recall@5/@10/MRR 为
  **1.000 / 1.000 / 0.917**，与纯 `hybrid` **逐位相同（MRR +0.000）**。
  "不得降低 recall@5/@10"满足，但**没有观测到任何提升**。可复现原因：语料共有
  4 条链接行（3 条 `wikilink` + 1 条 `embed`），其中 1 条 wikilink 断链被 `_build_graph_view`
  丢弃，故**可参与图扩展的解析边只有 3 条**（2 条 wikilink + 1 条 embed）；且候选池
  `candidate_k=30` 远大于语料规模（15 篇 = **15 个 chunk**，每篇恰好 1 块，即覆盖全库
  两遍），图邻居本就已被 keyword/vector 召回 ⇒ 加性过滤后进入融合的 link 候选 **0 / 14 题**。
  只保留 wikilink、只从最佳锚点扩展、降 RRF 权重到 0.1 都试过，**没有任何配置把 MRR
  抬到 baseline 之上**。未因该项调低任何既有门槛、未删除任何既有模式。
- **`raw` 与 `kept` 的区别决定结论怎么写**（harness 每次打印）：`raw` 是图里真实找到的
  link 候选数，`kept` 是经加性过滤真正进入融合的数。产品行 `kept=0` 是 MRR 停在
  0.917 的**机制**（link 列表无法重排），诊断行 `kept=25` 立刻劣化到 0.558——这就是
  默认值的理由。而 `raw=25 / 18 / 11` 说明 link 路**确实会触发**，因此结论是
  "**没有增益**"（no gain），**不是**"没有效果"（no effect）：只有前者被数据支持。
- **结论的适用范围**：以上都在 15 篇语料、`candidate_k=30` 已覆盖全库的条件下测得，
  所以 `kept=0` 是**关于该语料**的陈述，不等于"图扩展普遍无用"。代码默认关闭，
  是因为**在这里无法证明有增益**，而不是已证明其无效。
- **测试**：新增 `tests/rag/test_link_retrieval.py`（35 项）；`test_settings_api.py`
  6 → 14 项；`tests/frontend/RagSettings.test.tsx` 9 → 18 项。
  `test_link_tuning_record_never_costs_recall` 把"任何配置不得降 recall + 无配置超过
  baseline"钉为断言，将来若出现真实增益会失败并强制更新文档结论。

### 传输策略修复（同一阶段内完成）

- **shell 代理改为显式开启**：httpx 默认 `trust_env=True` 会解析
  `HTTP_PROXY`/`NO_PROXY`，当 `NO_PROXY` 含 IPv6 字面量（如 `[::1]`）时在发起请求前
  抛 `InvalidURL`，绕过适配器错误映射并让 Chat/发现/Agent/Embedding/重排全部 500。
  现在所有出站客户端默认 `trust_env=False`，需要时用
  `LOCALNOTE_AI__USE_ENV_PROXY=1` / `LOCALNOTE_RAG__USE_ENV_PROXY=1` 开启；
  新增 `tests/rag/test_transport_proxy.py` 回归覆盖。
- **RAG 门禁脚本** `scripts/rag-eval.sh`：跑 `tests/rag` 全量 + Golden Dataset 评估
  （`--check` 门槛校验，低于门槛以非零码退出并打印 `REGRESSION` 明细）。

### 与 M6 打通（同一阶段内完成）

- **`Suggest Related` 复用 RAG 检索**：`server/ai/candidates.py` 新增最高优先级候选来源
  `rag_hits`（chunk 级 FTS + 向量 + RRF），由 `AIWorkflowService._rag_candidates`
  提供。语义相近但无关键词重合的笔记因此可以进入候选；RAG 关闭/失败/空查询时自动
  退回原 M4 FTS/子串 + link/graph 路径，候选仍先程序化收窄并进行 allow-list 校验
  （库外路径不可能成为候选）。新增 `tests/rag/test_related_integration.py`（9 项）。

### 顺带修复

- **设置面板渲染崩溃**：`SettingsPanel` 直接读 `current?.ai.api_key_set`，当 `current`
  存在但 `ai` 缺失时（旧版服务端 / 上一 revision 的响应 / 测试替身）仍会在渲染阶段抛
  `TypeError`，React 卸载整棵设置树，并使 `vitest run` 以非零码退出。改为受保护的
  `currentAi` 视图后前端门禁恢复 `exit=0`（346 passed，无未捕获错误）。

### 实测

- 后端（2026-09-12，③ 落地后）：`python -m pytest tests -q --ignore=tests/backend/perf`
  → **1195 passed, 5 skipped**（③ 之前为 1135 passed, 5 skipped；2 项 skip 是未装
  numpy 时的加速内核测试，属预期跳过）。
- 前端（vitest）：**355 passed, 3 skipped**，`exit 0`（③ 之前为 346 passed；
  M14 新增共 26 项 = RagPanel 8 + RagSettings 18，其中 ③ 新增 9 项）。
- RAG 专用门禁：`./scripts/rag-eval.sh` → **272 passed, 2 skipped** + 评估表 +
  门槛校验通过（exit 0）；`tests/rag` 单独运行同为 272 passed, 2 skipped。
- Golden Dataset（离线 hash 嵌入、256 维、15 篇 / 14 问、同候选池）：
  keyword recall@5=1.000 / recall@10=1.000 / MRR=0.964，
  vector 0.929 / 1.000 / 0.938，
  hybrid 1.000 / 1.000 / 0.917（hybrid recall 更高、MRR 略低，报告如实标注 tradeoff），
  hybrid+本地重排 1.000 / 1.000 / 0.952（recall 不变、MRR +0.036），
  **hybrid_link 1.000 / 1.000 / 0.917（③ 新增；与 hybrid 逐位相同、MRR +0.000，
  即中性零增益，故默认关闭）**。
- link 路调参记录（harness 每次打印）：加性策略下 raw=25 / kept=0；
  非加性全量融合 w=1.0 → MRR 0.558、w=0.3 → 0.604、w=0.1 → 0.911——
  **没有任何配置超过 baseline 0.917**。
- 性能（120 篇 / 720 chunk，Mock 嵌入；环境：macOS arm64 / Apple M4、
  SQLite 3.53.4、Python 3.12.13、**未安装 numpy（纯 Python 内核）**）：8 次实测全量索引
  373–613 ms；混合检索中位 6.5–8.7 ms（worst 6.8–12.9 ms）；分块 0.17–0.28 ms/篇；
  修改一段 = 1 次嵌入（未变内容 0 次）。区间内的差异是**负载波动**，
  不构成回归也不构成承诺；③ 落地前后该区间重叠，未观测到 link 路带来的性能变化。

## [1.0.0] — 2026-09-10

首个可用版本：本地优先的 Markdown 笔记服务（FastAPI + React/Vite），
正文永远是 Vault 里的原始文件，`.localnote/` 只保存可删除重建的派生索引。

### 能力总览（M0–M13）

- **M0 Bootstrap**：pnpm monorepo + Python 3.12，一键开发/门禁脚本
  （`scripts/dev.sh`、`scripts/check.sh`）。
- **M1 Vault 安全读写**：唯一 FS 门面 `VaultService`；路径词法校验 + resolve
  containment + 逐级 lstat；拒绝 `..`/绝对路径/NUL/反斜杠/盘符/UNC 与**一切
  symlink**；Markdown 与附件按原始 bytes 读写（JSON base64 + SHA-256），
  BOM/LF/CRLF/非 UTF-8/未知语法逐字节保真；同目录临时文件 + fsync 的原子写，
  update/delete/move 强制 `expected_sha256`（冲突 409，绝不静默覆盖）；
  watchdog 去抖事件流；`.localnote/` 可删除重建。
- **M2 Workspace/Editor/Preview**：文件树、CodeMirror 6 源码编辑、安全只读预览、
  多标签、分屏、800ms 防抖自动保存与冲突处理（重新加载 / 保留本地）。
- **M3 Metadata/Links/搜索**：frontmatter（未知字段逐字保留）、wikilink/embed
  扫描（heading/block/alias）、outgoing/backlinks、关键词搜索。
- **M4 SQLite 派生索引**：`.localnote/index.db`（WAL + 版本表迁移 + FTS5），
  watcher 增量更新，`POST /index/rebuild` 全量重建；索引故障不影响正文读写。
- **M5 Graph**：`/graph`、`/graph/local/{note}`、`/graph/tag/{tag}` 只读查询 +
  Graphology/Sigma 可视化（WebGL 缺失时降级）。
- **M6 只读 AI**：六个只读 workflow（chat/summarize/tags/related/extract_todos/
  classify），版本化 Prompt Registry、强 schema 校验、候选程序化缩小；本地
  oMLX / OpenAI 兼容端点。
- **M7 Policy/Diff/History/Recovery/受控 Agent**：纯程序 PolicyEngine、
  diff 审阅、Journal/Undo、事务逆序回滚；Agent 写入默认只生成 Level 1 preview。
- **M8 Scheduler/可靠性**：APScheduler 首选（asyncio 降级）、三个稳定任务、
  run 审计、超时/幂等/清理、启动扫描标记 `recovery_required`、显式 hash-guard
  恢复、History retention、局域网暴露告警。
- **M9 用户直传附件**：`POST /vault/attachments`（JSON ≤10 MiB）、
  `POST /vault/attachments/multipart`（流式）、`GET/HEAD /vault/resource`
  （只读预览/下载）；四入口（工具栏 / 拖拽 / 粘贴 / 文件树目录右键），落点由
  入口决定且目标目录必须已存在；禁止覆盖、同名 `-2/-3` 去重；用户直传不经
  PolicyEngine，Agent 写附件仍被 `attachment_write` 永久拒绝。
- **M10 文件重命名**：文件树右键 / 双击行内改名，复用
  `POST /vault/file/move`（未新增端点）；名字须为单一路径段；预期摘要取磁盘当前
  bytes，未保存草稿随标签迁移；目标已存在 → 409。
- **M11 从 wikilink 创建嵌套笔记**：预览里失效 `[[链接]]` 一键创建并打开；
  先按全库 basename 解析（与索引同规则），未命中则落在源笔记目录，
  `[[子目录/名]]` 按需逐级建目录；`..`/URL/空名拒绝。
- **M12 单栏实时预览（Live Preview）**：同一 CodeMirror 实例内渲染标题/粗斜体/
  行内代码/链接/wikilink/图片 widget/列表/引用，**光标所在行保留原始语法**；
  原有编辑/预览/分屏视图保留。
- **M13 任务清单点击切换**：`- [ ]` / `- [x]` 在预览与实时模式渲染为可点击复选
  框，点一下改写源码标记并加删除线；跳过围栏代码块。

### 编辑器与交互增强

- 补全 CodeMirror keymap：此前未注册键位表，**Enter / Backspace / 方向键 / 撤销
  全部失效**；现注册 `defaultKeymap` + `historyKeymap` + `indentWithTab`。
- `Ctrl/⌘+1…6` 设为 1–6 级标题、`Ctrl/⌘+0` 取消，同级再按一次切回正文；
  工具栏提供 H1–H6 与「正文」按钮。
- 附件引用插入到**光标处**（无编辑器时回退为末尾追加）。
- 预览恢复有序列表序号（Tailwind Preflight 清掉了 `list-style`）。

### 修复

- `App.tsx` 漏注册 `uploadAttachmentBase64/Multipart`，导致附件上传永远
  不发请求；新增 `AppApiRegistration` 契约测试锁定。
- 重命名在文件有未保存修改时被 409 拒绝（误用会话 `baseSha256`），改为始终读
  磁盘当前摘要。
- 实时预览点击复选框会被 CodeMirror 的「点哪光标跳哪」干扰，导致 widget 重建、
  点击丢失；改为阻止 `mousedown` 默认行为。
- 渲染器把 hast `data` 对象序列化成 `data="[object Object]"`；改用 `dataXxx`。
- 编辑器外部值同步会整篇替换文档并重置光标，改为最小差异替换。
- AI 一直显示「离线」：`/ai/status` 只请求 `/v1/models`（毫秒级）所以状态正常，
  而生成超时默认 **2 秒**，本地 4B 模型一次推理约 7 秒必然 `ai_timeout`——
  默认值放宽到 **60 秒**（上限 120）。
- `pull` 式手工重启会丢掉 launchd plist 里的
  `LOCALNOTE_SERVER__SETTINGS_TRUSTED_HOSTS`，导致公网域名访问 `/api/v1/settings`
  被 `settings_local_only` 拒绝；README 增加常驻服务小节与
  `launchctl kickstart` 重启方式。

### 嵌套文档与分级收缩（1.0.0 后新增）

- **右键新建子文档**：文件树（笔记行、目录行、空白处）、编辑器正文、预览区、标签页
  都能右键新建文档。「新建子文档」把新文档写进同级同名目录
  （`Notes/A.md` → `Notes/A/未命名文档.md`），「新建同级文档」写在同一个容器里；
  默认名与目标目录内已有文件查重（`未命名文档.md`、`未命名文档 2.md`…）。
- **纯文件夹约定，无新语法**：层级完全由 Markdown + 目录表达，正文与 frontmatter 不
  写入任何父子标记，Obsidian/文件管理器看到的就是普通目录；服务端未新增端点，只用既有
  `POST /vault/directory`（逐级补建）+ `POST /vault/file`。
- **分级收缩展开**：文件树按文档层级渲染，有子文档的笔记行显示展开箭头，点箭头只切换
  折叠、点行仍打开笔记；折叠一层会连同其内部子文档（任意深度）一起收起。为避免与其他
  工具创建的层级冲突，`Notes/A/` 只在同级存在 `Notes/A.md` 时才挂到该笔记下，
  `notes/A/index.md` 则代表它所在目录本身；普通目录（如 `docs/` 且无 `docs.md`）
  保持独立目录行。列表每次刷新默认全开，只记录被折叠的路径；打开笔记会展开其上层文档，
  文档工具栏显示可点击的上层文档面包屑。
- **子文档一定有链接**：新建子文档/同级文档时，若「上层笔记」已打开且没有未保存改动，
  会自动在它正文末尾补一条 `[[子文档名]]`（独立段落）并走正常自动保存；该笔记没打开、
  或存在草稿/冲突/保存错误时**完全不碰它的字节**，新建文档也不会把它切到前台。重命名
  子文档时按「所有者链」定位上层笔记并改写 `[[旧名]]` → `[[新名]]`（保留别名、
  `#小节`、`^块引用`、`!嵌入` 与作者写的目录前缀；全库有同名笔记造成歧义时不擅自改写）。
  父子关系因此同时存在于文件树、正文与链接索引（反链/图谱）中。
- **配套修复**：新建文档的默认名查重此前按全库路径比较，跨目录会误判重名，现按目标目录
  内的文件名比较；文件树行按钮补上 `aria-label`（完整路径），使行元素的读屏名称不再受
  子行文本影响。
- **测试**：`tests/frontend/NestedDocuments.test.tsx`（层级模型、store 动作、四个右键
  入口、面包屑）；`tests/frontend/NestedDocumentLinks.test.tsx`（自动链接、关闭/脏稿时
  不写入、重命名改写、源码改写器边界）；`NestedDocuments.e2e.test.tsx` 为可选真实后端
  集成测试，仅当设置 `LOCALNOTE_E2E_BASE_URL` 时运行（配方见 `tests/frontend/.env.test`），
  默认门禁仍完全离线。

### 删除文件／文档（1.0.0 后新增）

- 文件树右键新增**「删除」**（文件行与目录行都有），确认对话框会说明将删除什么：目标路径、
  「无法撤销」、该文件是否有未保存改动（草稿会一起消失）、会同时关闭几个已打开的标签；
  删除后对应的标签/会话/折叠状态一起清理，并刷新文件树。
- 走既有 `DELETE /api/v1/vault/file`（**此前前端从未接入，所以文件一直删不掉**）：前端沿用
  「不静默覆盖」的原则，先读磁盘当前 `sha256` 再带 `expected_sha256` 删除，文件被其他程序改过
  就是 409 `file_conflict`，绝不误删别人的改动。
- 与后端契约一致的边界：`DELETE /vault/file` 只删普通文件（目录 → 400 `not_a_file`），
  所以目录会给出可读提示（非空目录：「先删除里面的文件」；空目录：「暂时只能删除文件」），
  客户端也会在非空目录上直接拒绝，连请求都不发。
- 测试：`tests/frontend/DeleteEntry.test.tsx`（store 动作的摘要校验/标签清理/非空目录拒绝/
  冲突透传，文件树菜单，Shell 端确认与未保存提示）。

### 删除文件夹与回收站（1.0.0 后新增）

- **文件夹可删除**：文件树目录行右键「移到回收站」，整个文件夹连同里面的文件一起进入回收站，
  `_` 不再是「只能删文件」。服务端把目录作为整体移动，不做逐个删除。
- **回收站暂存 30 天**：软删除的条目落在服务自有的 `.localnote/trash/`（`<uuid>/<原名>`，
  移动而非复制，不占双份空间），索引写在 `.localnote/trash/index.json`；默认保留
  **30 天**（`LOCALNOTE_VAULT__TRASH_RETENTION_DAYS`，1–3650），到期在任意一次回收站操作时
  自动清理。**派生数据语义不变**：删掉 `index.json` 只丢记账，删掉整个 `.localnote/` 也不会
  动正文（回收站内容本来就在 Vault 树之外）。
- **可恢复**：侧栏新增「回收站」区（默认收起），列出条目、原路径、删除时间与「剩余 N 天」，
  支持单项恢复、彻底删除、清空回收站；恢复会重建缺失的上级目录，原路径被占用时默认 409，
  UI 走 `rename_if_occupied` 恢复为「原名 (restored)」，并把新路径回给前端打开。
- **安全边界**：`.localnote` 依旧对文件 API 完全不可达（回收站走的是服务内部的一次受信移动，
  源路径仍要过词法校验、保留目录检查、symlink 链与 containment）；文件软删除必须带磁盘当前
  `expected_sha256`（缺失 400、不符 409），目录按整体移动；回收站 id 由服务生成、客户端只能
  用它做 4 种操作，永远拼不进路径。
- **接口**：`GET/POST /api/v1/trash`、`POST /api/v1/trash/{id}/restore`、
  `DELETE /api/v1/trash/{id}`、`DELETE /api/v1/trash`（清空）。`DELETE /vault/file` 保留为
  字节级永久删除原语（`deleteEntry` 仍在，供「彻底删除」路径使用）。
- **测试**：`tests/backend/test_vault_trash.py`（21 项：软删除/文件夹整移/hash 守卫/保留目录
  拒绝/恢复/占位改名/保留期边界/索引损坏降级/REST 契约与错误体）；前端
  `tests/frontend/TrashBin.test.tsx`（12 项）与更新后的 `DeleteEntry.test.tsx`。

### 字体与字号（整体放大缩小，1.0.0 后新增）

- **字号整体缩放 80%–160%**：设置 → 外观里有预设按钮（90/100/115/130%）+ 滑块实时预览，
  状态栏右侧还有常驻的 `− 100% +` 控件（点百分比即恢复默认）。缩放作用面是**整个界面**：
  侧栏、文件树、标签页、面板、状态栏、预览与编辑器一起变大变小，而不是只放大正文。
  为此把样式表里 **168 处硬编码 font-size** 全部换成 `--fs-*` 令牌（由 `--font-scale` 派生），
  并把承载文字的固定高度（标签栏 44px、文档工具栏 51px、侧栏标题 40px、按钮/输入框
  26/31px 等 20 处）改为 `calc(... * var(--font-scale))`，避免放大后文字被裁切。
- **字体可选**：无衬线 / 衬线 / 等宽 / 圆体四套本地字体栈（都带中文回退，不联网加载字体），
  并可选择作用范围「整个界面」或「仅笔记正文」（后者让界面保持无衬线，笔记用所选字体）；
  编辑器与预览、实时预览共用同一套 `--font-note`。
- **快捷键**：`⌘/Ctrl +`、`⌘/Ctrl -`、`⌘/Ctrl 0`（恢复默认）以及 `Ctrl + 滚轮`，与浏览器缩放互不冲突
  （应用自带缩放，快捷键被应用自己接管）。
- **可用 URL 预置**：`?fontScale=130&fontFamily=serif&fontTarget=note` 可在首次渲染前写入偏好，
  方便分享"我常用的字号"；只接受这三项，越界值会被夹到范围内，其他偏好不受影响。
- **默认值不变**：`fontScale: 100` / `fontFamily: sans` / `fontTarget: all` 渲染结果与改动前一致
  （13px 基础字号、原字体栈），旧版本存下的偏好也能直接读入（缺字段回落到默认）。
- **测试**：`tests/frontend/FontPreferences.test.tsx`（15 项：偏好校验与夹取、令牌成比例缩放、
  URL 预置、文档根变量与 `data-font-target`、状态栏控件与两端禁用、键盘/滚轮缩放、设置页三类控件，
  以及**样式表级断言**——不再存在裸 px 字号、承载文字的高度必须跟随缩放、编辑器必须用同一套令牌）。

### AI 供应商：错误提示与部署时序（1.0.0 后修复）

- **现象**：AI 供应商列表一直显示「操作未完成，请检查连接或稍后重试。」，且无法新增供应商。
- **根因**：前端（Vite HMR 即时更新）已经在调用 `GET/POST /api/v1/settings/ai/profiles`，
  而正在运行的后端进程是 providers 代码写到一半时启动的——该路由返回 **404**，`/settings`
  快照里也没有 `profiles`/`active_profile_id` 字段。后端由 launchd
  （`com.ningyd.localbook-backend`）托管，需要重启才能真正加载新代码；重启后两个接口均正常
  （实测新增/切换/删除/重启留存，旧格式设置文件会自动补出 `profiles` 与 `active_profile_id`）。
- **顺带改进报错**：`ApiError` 现在携带失败端点，兜底文案由「操作未完成，请检查连接或稍后重试。」
  改为**带诊断信息**的形式（`HTTP 404 · /settings/ai/profiles`），中英文一致；这样"后端比页面旧"
  这类问题一眼可辨，不再是一句无从下手的提示。有明确错误码的路径仍优先使用既有文案映射。
- 已用一次性临时 Vault 复现并验证修复，未改动任何真实笔记内容；探针用的临时供应商已删除，
  配置文件除必要的 schema 迁移（补 `profiles`/`active_profile_id`）与 revision 递增外与原来一致。

### AI 供应商保存失败：数值输入与错误文案（1.0.0 后修复）

- **现象**：保存供应商时报「请求无效，请检查文件名或目标目录。」并无法新增。
- **根因（两层）**：
  1. 供应商表单的三个数字框（温度 / 最大输出 tokens / 请求超时）清空后仍会被送出
     `0`——`Number("") === 0` 能通过 `Number.isFinite` 判断——而后端要求
     `temperature ≥ 0.1`、`max_output_tokens ≥ 64`、`request_timeout_seconds > 0`，
     于是整个请求被判 422 `invalid_request`。
  2. 前端把 `invalid_request` 的文案写成了**附件上传专用**的「请检查文件名或目标目录」，
     设置类请求也被套用，所以提示与实际原因完全对不上。
- **修复**：数字框的取值改为「空白 = 不发送该字段（服务端保留原值）」，非空但越界的值会在表单内
  直接列出字段名与允许区间并**禁用保存按钮**（不再白跑一次 422）；`invalid_request` 改为通用文案
  「请求内容无效，请检查填写的字段后重试。」，附件相关的说法保留在
  `invalid_attachment_name` 上。实测：旧 payload（0/0/0）→ 422，新 payload（省略）→ 200 且服务端
  回填默认 0.1 / 1200 / 60。
- **测试**：`tests/frontend/ProviderPayload.test.tsx`（6 项，锁定空白省略、越界命名并拦截、有效值原样透传、
  修正后立即恢复可保存）。

### 分屏同步滚动（1.0.0 后新增）

- **行为**：分屏（编辑 + 预览）时两侧按**相同相对位置**同步滚动；任一侧手动滚动都会带动另一侧，
  且不会来回抖动（写入时抑制对端 `scroll` 事件，下一帧才恢复监听）。
- **为什么用比例而不是行号锚点**：左边是源码行、右边是排版后的块（标题、图片、表格、列表），
  两者不存在等价的像素位置；按比例映射对普通正文跟随得很准，也永远不会因为两边结构差异而"跳锚"，
  文档编辑过程中依然有效。任一侧无法滚动（内容不够高）时不联动，避免短文档把长文档拖到底。
- **可控**：文档工具栏在分屏模式下多出「同步滚动」开关（默认开启），偏好写入
  `localnote-preferences.syncScroll`，与其他外观偏好一起持久化。
- **测试**：`tests/frontend/SyncedScroll.test.tsx`（7 项，给 jsdom 造出可滚动几何后验证
  双向映射、无回环、单侧不可滚动时不联动、关闭开关后不联动、开关仅在分屏出现、默认开启并持久化）。

### 分屏同步滚动：时序修复（1.0.0 后修复）

- **现象**：「有时候一点进去并不能实时同步」——打开一篇笔记进入分屏后，两侧互不联动，
  要等别的操作把状态改一遍才开始同步。
- **根因**：绑定只做一次。hook 在挂载时查找两侧滚动容器，**而编辑器侧此时还不存在**：
  `.cm-scroller` 由 CodeMirror 在自己的 effect 里创建，晚于本 hook。一次查找失败就永久放弃，
  之后没有任何重试，于是两侧一直是独立的（"有时候"= 取决于这次挂载时 CodeMirror 是否已经建好）。
- **修复**：绑定改为**持续解析 + 自动重绑**——在 10 秒窗口内每 50ms 重试直到两个滚动容器都存在；
  绑定后仍保持轻量巡检，一旦某一侧的元素被**替换**（切换笔记会新建 CodeMirror 实例）立即重绑到
  新元素，不需要任何额外交互。回环抑制也从"跨两帧的全局标志"改为**"只吞掉刚写入那个元素的一次事件"**：
  连续拖拽的每一步都真实生效，不会被误吞，也不会自我干扰。
- **测试**：`tests/frontend/SyncedScroll.test.tsx` 增至 9 项，新增"滚动容器晚到也能绑上"、
  "容器被替换后重绑"、"连续拖拽每一步都跟随"三个用例。

### 依赖

- 新增 `python-multipart`（FastAPI multipart 路由的前提）。
- `packages/markdown` 新增 `@localnote/protocol` 与 `@types/hast`。
- 未引入其他运行时依赖（任务清单与实时预览均为自研实现）。

### 测试与门禁

- `./scripts/check.sh`：后端 `pytest` + `@localnote/protocol|graph|web` typecheck +
  前端 Vitest + 生产构建。
- 本版本：后端 **830 passed / 3 skipped**；前端 **31 文件 / 242 passed**。
- 测试只使用 `tests/fixtures/vault` 的受控副本与 `tmp_path`，绝不触碰真实 Vault；
  手写临时脚本须显式设置 `LOCALNOTE_SETTINGS_FILE`。

### 已知限制（1.0 明确不做）

- 无账号/认证/HTTPS；局域网或公网暴露需自行用防火墙与反代 Basic Auth 保护。
- 目录重命名/移动、附件拖动移动、附件全文索引、批量上传、断点续传。
- AI 写正文、Agent 递归 loop、自动修复 broken/ambiguous 链接。
- embedding/rerank 真实端点（能力探测可见，调用返回 `capability_unavailable`）。
- 真正的富文本编辑器：实时预览是「源码 + 装饰层」，不引入 AST 写路径。
- 目录重命名、云同步、多进程调度、分布式锁。
