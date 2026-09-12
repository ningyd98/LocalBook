# LocalBook RAG 架构（M14）

本文件描述 M14 引入的检索增强生成（RAG）能力：数据链、模块边界、降级策略、
配置项、数据库表、API、测试与评估方式。核心原则与 `docs/architecture.md`
保持一致：

```text
Markdown = Source of Truth
SQLite FTS = Lexical Retrieval
Vector Index = Semantic Retrieval
RRF = Fusion
Reranker = Optional
EvidencePack = Grounding Boundary
LLM = Generator, not Source of Truth
Citation Validator = Anti-Hallucination Boundary
```

RAG 是 **LocalBook Core 能力**，不属于 Web UI：它只依赖 Vault 抽象、派生索引、
AI provider，因而不依赖 React workspace，未来 Obsidian 兼容宿主可以复用同一条链。

RAG 的检索结果同时被**其它只读能力**复用：M6 的 `Suggest Related`
（`server/ai/candidates.py` + `AIWorkflowService._rag_candidates`）把 chunk 级
混合检索结果作为**最高优先级候选来源**，因此"语义相近但用词不同"的笔记无需关键词
匹配即可进入候选；RAG 关闭或不可用时自动退回 M4 的 FTS/子串 + link/graph 路径，
且候选始终受索引 allow-list 限制（模型不可能看到库外路径）。

## 1. 数据链

```text
Markdown Vault（唯一事实源，只读）
      │  VaultService.read_bytes / list_tree
      ▼
MarkdownChunker（Markdown 感知分块）
      │
      ├──────────────► rag_chunks / rag_chunks_fts（SQLite）
      ▼
EmbeddingProvider（独立于 Chat 模型）
      │
      ▼
SqliteVectorStore（rag_documents.vec_d<N> + rag_embeddings）
      │
      ▼
HybridRetriever  ← KeywordRetriever（FTS5 → 子串/双字覆盖回退）
      │          ← VectorRetriever（余弦点积扫描）
      │          ← LinkRetriever（可选路；wikilink/backlink/tag/graph，默认关闭）
      ▼
  Reciprocal Rank Fusion（RRF, k=60）
      │
      ▼
  Optional RerankProvider（默认关闭）
      ▼
RagContextBuilder → EvidencePack（去重 / 合并相邻块 / token 预算）
      ▼
已有 AI Provider（Chat only）
      ▼
Grounded Answer
      ▼
Citation Validation（只保留 EvidencePack 中存在的 [S1]…）
      ▼
REST 响应（answer + sources + retrieval_stats + model）
```

## 2. 模块

| 路径 | 职责 |
|---|---|
| `server/rag/chunking/markdown.py` | Markdown 感知分块：标题层级、代码块、表格、frontmatter 元数据 |
| `server/rag/schemas.py` | 内部数据结构（`RAGChunk`/`RetrievalResult`/`EvidencePack`/`CitationReport`/`RAGIndexState`） |
| `server/rag/embeddings/base.py` | `EmbeddingProvider` 协议、`HashEmbeddingProvider`（离线回退）、`MockEmbeddingProvider`/`FailingEmbeddingProvider`（测试） |
| `server/rag/embeddings/openai_compatible.py` | 唯一的 embedding HTTP 边界（`POST {base_url}/embeddings`） |
| `server/rag/embeddings/runner.py` | 专用后台事件循环，供 watcher/请求线程同步调用 |
| `server/rag/vector/base.py` | `VectorStore` 协议 + float32 打包/解包 |
| `server/rag/vector/sqlite.py` | SQLite 实现：文档级向量 run、chunk FTS、状态、增量写入 |
| `server/rag/vector/kernels.py` | 相似度内核：numpy（可选加速）与纯 Python 回退，二者可互换 |
| `server/rag/retrieval/{keyword,vector,hybrid,rrf}.py` | 候选生成与 RRF 融合 |
| `server/rag/retrieval/link.py` | `LinkRetriever`：wikilink 出边 / backlink 入边 / same-tag / 2-hop 图邻居四信号加权（默认关闭，见 §5.2） |
| `server/rag/rerank/base.py` | `RerankProvider` 协议 + 本地 `LexicalOverlapReranker` |
| `server/rag/rerank/openai_compatible.py` | 可选 HTTP 重排（`POST {base}/rerank`，Jina/Cohere/vLLM 风格），失败即降级 |
| `server/rag/context/builder.py` | `EvidencePack` 构造与渲染（模型唯一可见的上下文） |
| `server/rag/citations.py` | 引用校验：删除伪造 `[S99]`，无引用时改为固定"证据不足"话术 |
| `server/rag/index_service.py` | 分块 → 嵌入 → 存储；全量 rebuild 与 watcher 增量 |
| `server/rag/service.py` | `RagService`：检索 → 融合 → 证据包 → 生成 → 校验 |
| `server/rag/factory.py` | 由配置装配整套 RAG（唯一装配点） |
| `server/rag/schema.py` | 派生表 DDL（`ensure_rag_tables`，additive、不破坏 M4 版本表） |
| `server/api/routes/rag.py` | REST 端点 |
| `apps/web/src/components/RagPanel.tsx` | 最小前端：知识库提问 + 可点击来源 |

## 3. 数据库变化（均派生数据）

在同一 `.localnote/index.db` 中新增（不修改 M4 表、不改变 `SCHEMA_VERSION`）：

| 表 | 用途 |
|---|---|
| `rag_documents` | 每篇笔记一行：`sha256`、`content_hash`、chunk 数、嵌入模型/维度、状态、`vec_d<N>` 连续向量 run |
| `rag_chunks` | 每个语义块一行：内容、`content_hash`、标题/章节路径、行号区间、offset、tags/aliases |
| `rag_chunks_fts` | FTS5 虚表（content/heading/tags/path），chunk 级词法检索 |
| `rag_embeddings` | 每块向量（float32 BLOB）+ 模型/版本/`content_hash`，用于判断向量能否复用 |
| `rag_index_state` | 索引状态：嵌入 provider/model/维度/版本、计数、pending/failed、时间 |

删除 `.localnote/` 或 `store.reset()` 后可完整重建；RAG 从不写 Markdown。

## 4. API

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/rag/index/status` | 索引状态、计数、降级信息、`link_retrieval`（Link 路状态，见 §7.2） |
| `POST` | `/api/v1/rag/index/rebuild` | 全量重建（仅写派生数据） |
| `POST` | `/api/v1/rag/search` | 仅检索（**不调用 Chat 模型**），含 keyword/vector rank |
| `POST` | `/api/v1/rag/query` | 完整链：检索 → 融合 → 证据包 → 生成 → 引用校验 |

`/rag/query` 返回 `answer`、`sources`（`id/path/heading/start_line/end_line/excerpt`）、
`retrieval_stats`、`model`、`prompt_version`、`degraded`、`invalid_citations`。
`debug: true` 时额外返回 `retrieval_debug`（默认关闭）。
`retrieval_stats` 含 `link_candidates`（Link 路候选数）；link 命中另带
`link_rank` 与 `document_level`（后者为 `true` 表示整篇命中、**行区间非行级精确**）。
设置面：`PATCH /api/v1/settings/rag` 支持 §5 的六个 link 相关字段（其中**仅
`link_retrieval_enabled` / `link_top_k` 带 `link_` 前缀**，四个权重名为 `wikilink_weight` /
`backlink_weight` / `tag_weight` / `graph_weight`）（`RagConfiguration`
以 `extra="forbid"` 校验，未知键返回 422）。

## 5. 配置（`LOCALNOTE_RAG__*`）

| 配置 | 默认 | 说明 |
|---|---|---|
| `enabled` | `true` | 关闭后 RAG 不索引、不检索，其余功能不受影响 |
| `embedding_provider` | `hash` | `hash` / `openai_compatible` / `openai` / `none` |
| `embedding_base_url` / `embedding_model` / `embedding_api_key` | — | 与 Chat 模型**完全独立**的端点 |
| `embedding_dimension` / `embedding_version` | `256` / `v1` | 维度不硬编码；任一变化即视为索引失效 |
| `chunk_target_tokens` / `chunk_max_tokens` / `chunk_overlap_tokens` | `800` / `1200` / `100` | 分块目标（token 估算，CJK≈1/字） |
| `fts_top_k` / `vector_top_k` / `fusion_top_k` / `rerank_top_k` / `context_top_k` | `30`/`30`/`20`/`10`/`6` | 各阶段候选数 |
| `rrf_k` | `60` | RRF 常数 |
| `context_max_tokens` / `context_max_tokens_per_document` | `4000` / `1800` | 证据包预算 |
| `reranker_enabled` / `reranker_provider` | `false` / `lexical` | 重排默认关闭；`lexical` 本地启发式，`openai_compatible` 走 HTTP |
| `reranker_base_url` / `reranker_model` / `reranker_api_key` / `reranker_timeout_seconds` | — / — / — / `30` | HTTP 重排端点（密钥不回显） |
| `debounce_seconds` | `1.5` | watcher 事件合并窗口（不逐字嵌入） |
| `index_on_startup` | `false` | 启动即全量重建（默认不做，避免每次启动重新嵌入） |
| `vector_min_score` | `0.0` | 可选的"无关"余弦下限（取决于模型） |
| `link_retrieval_enabled` | `false` | Link/Graph 加权检索总开关；关闭时 `LinkRetriever` 完全不接线（见 §5.2） |
| `link_top_k` | `20` | Link 路候选数（`1..200`） |
| `wikilink_weight` | `1.0` | wikilink 出边信号权重（`0..5`，越大越强） |
| `backlink_weight` | `0.8` | backlink 入边信号权重（`0..5`） |
| `tag_weight` | `0.6` | same-tag 信号权重（`0..5`） |
| `graph_weight` | `0.4` | 2-hop 图邻居信号权重（`0..5`；为 `0` 时不展开 2-hop） |
| `require_citation` | `true` | 无有效引用时回答固定"证据不足"话术 |

以上六个 link 相关字段逐个对应 `server/config.py::RagSettings`（`link_retrieval_enabled` /
`link_top_k` / `wikilink_weight` / `backlink_weight` / `tag_weight` / `graph_weight`；
其中**仅前两个带 `link_` 前缀**），
默认值与取值范围与代码逐字一致。

## 5.1 向量内核（可选加速）

`server/rag/vector/kernels.py` 提供同一契约的两个内核：

| 内核 | 何时使用 | 20k×256 实测（本机） |
|---|---|---|
| `numpy` | 仅当运行环境**恰好装有** numpy | 首查约 86 ms，稳定态约 1.5 ms |
| `python` | 总是可用（默认） | 约 190–215 ms |

`numpy` **不是声明依赖**：LocalBook 必须在没有本地编译的平台上零依赖安装并运行；
纯 Python 内核已满足检索预算。numpy 存在时自动启用，任何异常都回退到纯 Python
内核，`GET /rag/index/status` 的 `vector_kernel` 字段会如实报告当前生效的内核。

## 5.2 Link/Graph 加权检索（`LinkRetriever`，默认关闭）

词法与向量两路回答的是"哪些 chunk **在讲**查询词"；Link 路回答的是另一个问题：
"哪些笔记与查询已经命中的笔记**相连**"。它读取已有派生图（`DerivedIndexService.graph_snapshot()`
一次性读取 `notes`/`tags`/`links`），**只读**、不写 SQLite、不写 Vault。

锚点（seed）由 `KeywordRetriever` 取前 `seed_top_k=5` 篇（查询本身命中的笔记），
再从锚点沿四个信号扩展；每个信号的贡献是"**去重后的 seed 计数 × 该信号权重**"：

| 优先级 | 信号 | 权重配置 | 含义 |
|---|---|---|---|
| 1 | `wikilink` | `wikilink_weight=1.0` | 锚点 `[[出边]]` 指向的笔记 |
| 2 | `backlink` | `backlink_weight=0.8` | 反向链接到锚点的笔记 |
| 3 | `tag` | `tag_weight=0.6` | 与锚点共享同一 tag 的笔记（每 tag 上限 `max_tag_neighbours=50`） |
| 4 | `graph` | `graph_weight=0.4` | 距锚点**恰好两跳**的图邻居（已是一跳的直接邻居不重复计分） |

排序与准入规则（这些都是"link 证据可用于引用"的前提）：

- 没有派生索引行的路径**一律丢弃**，因此断链 `[[missing]]` 不可能变成引用；
- 锚点自身、`exclude_paths`（当前笔记）、超出 `allowed_paths` 的路径都不返回；
- 排序是 `(-score, path)`，即得分降序、同分按路径升序，**与 seed 顺序无关**，
  同一索引永远得到同一顺序；
- chunk 级命中携带真实的 `chunk_id` / 行号区间 / `content_hash`；若 RAG 索引里没有
  该 path 的 chunk，则降级为 `doc::<path>` 伪 id 并标记 `document_level=True`，
  **不编造行区间**（保持默认 1/1），调用方不得把它当作行级精确引用。

**信号如何进入 RRF**：Link 路是把整份候选列表接到 RRF 的第三路上，其融合权重为
`HybridRetriever.link_rrf_weight`（模块常量 `DEFAULT_LINK_RRF_WEIGHT=0.5`，**不是**
`RagSettings` 字段）。默认 `link_add_only=True`：图扩展只**新增**直接路（词法/向量）
没有返回的文档，不参与重新排序已有命中。默认旋钮下
`0.5/(60+20)=0.00625 < 1/(60+30)=0.0111`，因此仅由 link 命中的候选必然排在所有直接
候选之后——`test_default_knobs_keep_link_candidates_below_every_direct_one` 固定了
这个不等式，使"默认配置不会用图邻居挤掉直接命中"成为可回归的性质。

为什么默认关闭、且为什么加性策略是必须的（实测）：非加性的全量融合会把直接路已经
召回的低位笔记（例如 keyword 第 6 名 + vector 第 7 名）抬到第 1 位，
**MRR 从 0.917 掉到 0.558**。即使把 RRF 权重降到 0.1，MRR 也只回到 0.911，仍低于
0.917。因此在当前数据集上，加性（只新增、不重排）是唯一不产生回退的接线方式。

诚实结论（见 §9 实测）：**在本项目的 Golden Dataset 上，Link 路没有观测到任何增益，
MRR 变化为 +0.000，因此保持默认关闭**。原因可复现——语料共有 4 条链接行
（3 条 `wikilink` + 1 条 `embed`），其中 1 条 wikilink 断链（`broken=True`）在建图时被
`_build_graph_view` 丢弃，因此可参与图扩展的解析边只有 3 条（2 条 wikilink + 1 条 embed）；
且候选池 `candidate_k=30` 远大于语料规模（15 篇 = **15 个 chunk**，每篇恰好 1 块，即候选池
覆盖全库两遍），图邻居本来就已被词法/向量路径召回；再加性过滤后进入融合的 link 候选为 0。
该结论不是"link 检索无效"的普遍判断，而是"**在这个数据集上无法证明有效**"，因此不默认启用。

## 6. 增量同步与一致性

```text
watcher event → RagIndexService.handle_event → 去抖(1.5s) → flush
   ├─ 内容 hash 未变 & 向量齐备 → skip（不调用 embedding）
   ├─ 内容变化 → 重新分块 → 仅对内容变化的 chunk 重新嵌入 → 原子替换
   ├─ 删除 → 删除该文档的 chunk/向量
   └─ rename（内容不变）→ 仅重指向 + 更新 sha256，绝不重新嵌入
```

- chunk id = `sha256(path + chunk 内容 hash)`，因此插入段落不会导致后续块"重新编号"；
- 编辑一段只重新嵌入该段（实测：720 chunk 的库中改一段 = 1 次嵌入）；
- embedding 失败：文档标记 `pending`/`failed`，chunk 与 FTS 仍然存在，词法检索可用，
  可 `retry_failed()` 重试；不影响编辑、FTS、启动。

## 7. 降级矩阵

| 情况 | 行为 |
|---|---|
| 未配置 embedding 端点 | 使用本地 `hash` 回退嵌入并在状态中标记 `embedding_degraded`；语义检索质量有限 |
| embedding 服务不可用 | `degraded=["vector_unavailable"]`，仅词法路径作答 |
| RAG 关闭 | `/rag/query` 返回明确说明且不检索 |
| 索引不可用 | HTTP 503 `rag_unavailable`；health/FTS/编辑不受影响 |
| 无检索结果 | 不调用模型，直接返回"根据当前知识库内容，没有找到足够证据回答这个问题。" |
| 模型不可用 | 返回检索到的证据清单（含真实来源），并标记 `generation_failed` |
| 模型给出伪造引用 | 引用被删除并记入 `invalid_citations`；无有效引用则改用固定话术 |
| Link 路未开启 / 降级 | 见 §7.2：`link_retrieval` 如实报告 `""`/`enabled`/`link_unavailable`，故障只降级、不阻断 RAG |

## 7.1 Shell 代理策略（`use_env_proxy`，默认关闭）

所有出站 HTTP 客户端（Chat adapter、模型发现、Embedding、Reranker）都以
`trust_env=False` 创建。原因是一个真实故障：httpx 默认 `trust_env=True`，会读取
`HTTP_PROXY`/`NO_PROXY`，当 `NO_PROXY` 含 IPv6 字面量（例如
`localhost,127.0.0.1,::1,[::1]`）时**在发起请求前就抛 `InvalidURL`**，绕过了
适配器的错误映射，最终表现为 HTTP 500 —— 用户明明没要求 LocalBook 走代理。

- 默认：忽略 shell 代理（local-first 服务默认只访问本机端点）；
- 需要时用 `LOCALNOTE_AI__USE_ENV_PROXY=1` / `LOCALNOTE_RAG__USE_ENV_PROXY=1` 显式开启；
- 回归测试：`tests/rag/test_transport_proxy.py`（含"若用 httpx 默认值则会崩"的对照断言）。

## 7.2 Link/Graph 路的降级（`link_retrieval` 状态）

Link 路是**可选第三路**，任何故障都只降级、绝不影响另外两路与问答主链：

| 情况 | 行为 |
|---|---|
| 未开启（默认） | `LinkRetriever` 根本不接线：`link_retrieval=""`（**空字符串，不是 `"off"`**；前端因此在状态卡里整行不渲染，避免声称任何状态），候选生成、RRF 输入列表与融合顺序与"没有 link 路"时逐位相同 |
| 开启且有派生索引 | 接线并报 `link_retrieval="enabled"` |
| 开启但派生索引不可用（`index=None`） | **仍然接线**并如实报 `link_retrieval="link_unavailable"` + 一条 WARNING 日志；不隐藏这次配置错误 |
| 构造 `LinkRetriever` 抛异常 | 工厂返回 `None` + 记日志，RAG 栈照常启动——坏掉的 link 路不得阻断 RAG |
| 图读取失败（`IndexUnavailable`） | 本次检索降级：`stats.degraded` 记 `link_unavailable`，其余两路正常作答 |
| 查询没有词法锚点 | 本次检索降级：`stats.degraded` 记 `link_no_seed`（这是"查询没匹配到任何笔记"，不是故障） |
| 邻接集合为空 | **正常结果**，不是降级（不写任何 degraded 标记） |

`link_retrieval` 的可达性由 `LinkRetriever` 依据配置静态判定，**不读 `last_degraded`**：
后者只在 `retrieve()` 之后才有值，若用它判断"是否启用"，一个尚未检索过的新进程会把
不可达的图报成 `enabled`——这正是该字段要防止的"假装已开启"。同理，`link_no_seed`
属于**按查询**的条件，只出现在 `stats.degraded`，不进入状态字段。

## 8. 安全与可靠性

- RAG 只读 Vault：读路径经 `VaultService`，写路径只写 `.localnote/index.db`；
- 不提供任何"由 RAG 直接修改笔记"的通道；未来写操作仍须走 M7 Policy/Action/History；
- 引用来源永远由 EvidencePack 生成，模型文本不能创造可点击路径；
- 模型只看到编号证据块（`path`/`heading`/`lines`/`content`），而不是整库正文；
- 日志不记录笔记正文，只记录计数、耗时与降级原因。

## 9. 测试与评估

- 单元/集成：`tests/rag/`（分块、嵌入、向量库、RRF、混合检索、**Link/Graph 加权检索**、
  上下文、引用校验、增量/重命名/删除、API、生命周期、性能）；
  2026-09-12 实测 `python -m pytest tests/rag -q` → **272 passed, 2 skipped**
  （其中 `tests/rag/test_link_retrieval.py` 35 项覆盖四信号、准入规则、确定性、
  降级与接线；`-k link` 选中 57 项）；
- Golden Dataset：`tests/rag/eval/golden.py`（15 篇、14 问，含中文/英文/中英混合、
  同义改写、易混淆主题、wikilink）；
- 评估指标：Recall@5 / Recall@10 / MRR，五种模式（keyword / vector / hybrid /
  hybrid_rerank / hybrid_link）**同池比较**，
  `python -m tests.rag.eval.run --vault <tmp>` 可复现打印报告；
  报告同时打印 link 贡献行与"link 路调参记录"（见下），使"中性"这一结论可复现而非声明；
- 回归门槛（`FLOORS`）定义在 **`tests/rag/eval/run.py`**（阈值低于实测值，只用于拦截
  显著回退）：hybrid `recall@5 ≥ 0.90` / `recall@10 ≥ 0.95` / `MRR ≥ 0.80`，
  keyword 与 vector 各 `recall@10 ≥ 0.85`，hybrid_link 只继承 hybrid 的两条 **recall**
  门槛；link 路的排序风险不用冻结阈值表达，而用一条**相对规则**——
  `hybrid_link.mrr >= hybrid.mrr`（基线取**同一次运行**的纯 hybrid），因为写死的 MRR
  常数就是这条检查本身要避免的"针对某次运行调参"；`tests/rag/test_eval.py` 另用一份
  与之等值的常量做同类断言，并把每条调参记录的 `LINK_SWEEP` 行都钉在 `<=` baseline；
- **门槛在每次运行都会评估并打印**（`floors: OK|FAILED`），与是否传参无关；
  `--check` **只决定违反时是否让退出码变成非零**（不带它也会打印 `REGRESSION` 明细），
  `--no-tests` 同样不能关掉规则——一个"漏传参数就能关掉的 gate"不是 gate；
- CI/本地一键门禁：`./scripts/rag-eval.sh`（跑 `tests/rag` 全量 + 评估 + 门槛校验）；
  `./scripts/rag-eval.sh --no-tests` 只跑评估报告（`floors` 与调参记录仍会打印）。

实测（离线 `hash` 嵌入、256 维、等候选池；下列为 `./scripts/rag-eval.sh` 输出的**节选**，
逐字保留原文含缩进）：

```text
RAG evaluation (Golden Dataset)
  mode=keyword n=14 recall@5=1.000 recall@10=1.000 mrr=0.964
  mode=vector n=14 recall@5=0.929 recall@10=1.000 mrr=0.938
  mode=hybrid n=14 recall@5=1.000 recall@10=1.000 mrr=0.917
  mode=hybrid_rerank n=14 recall@5=1.000 recall@10=1.000 mrr=0.952
  mode=hybrid_link n=14 recall@5=1.000 recall@10=1.000 mrr=0.917
  rerank: MRR=0.952 vs fused 0.917 (+0.036); recall@5=1.000 vs 1.000
  link: recall@5=1.000 vs fused 1.000; recall@10=1.000 vs 1.000; MRR=0.917 vs 0.917 (+0.000)
  link: neutral (no measurable change on this dataset; the additive third list never displaced a direct hit — see the link contribution line below)
  link contribution: raw candidates=25 kept by the additive filter=0 over 14 questions
  hybrid recall@5=1.000 (best single 1.000), MRR=0.917 (best single 0.964) -> tradeoff (recall equal-or-better, MRR lower)
```

本地启发式重排在 recall 不变的前提下把 MRR 从 0.917 提到 0.952；这是"可选阶段
必须被度量、而不是被假设"的实证。

**Link 路的诚实结论：中性，未观测到增益，保持默认关闭。**
`hybrid_link` 的 recall@5/@10 与 MRR 与纯 `hybrid` **完全相同**（1.000 / 1.000 /
0.917，MRR 差 +0.000）。原因在 output 里可直接读到：本数据集只有 3 条可参与图扩展的
解析边，而候选池 `candidate_k=30` 远大于语料规模（15 篇 = **15 个 chunk**，每篇恰好 1 块，
候选池覆盖全库两遍），图邻居本来就已被词法/向量路径召回，加性过滤后进入融合的 link
候选为 **0 / 14 题**。因此"不降低 recall@5/@10"
的硬要求**满足**（保持 1.000），但"带来增益"**没有成立**——文档不把它写成增益。

（上句的"3 条"是**可参与图扩展的解析边**数，不是 wikilink 数：语料共有 4 条链接行
（3 条 `wikilink` + 1 条 `embed`），其中 1 条 wikilink 断链被 `_build_graph_view` 丢弃，
剩下 3 条 = 2 条 wikilink + 1 条 embed；`_build_graph_view` 不按 kind 过滤，
`embed` 与 `wikilink` 同等入图。）

调参记录（同一候选池，任何可复现；`./scripts/rag-eval.sh` 每次打印）：

```text
hybrid (link off)                                 r@5=1.000 r@10=1.000 mrr=0.917 raw=0  kept=0
hybrid_link (add-only, shipped default)           r@5=1.000 r@10=1.000 mrr=0.917 raw=25 kept=0
link additive, wikilinks only (graph=0, tag=0)    r@5=1.000 r@10=1.000 mrr=0.917 raw=18 kept=0
link additive, single best anchor (seed_top_k=1)  r@5=1.000 r@10=1.000 mrr=0.917 raw=11 kept=0
DIAGNOSTIC link_add_only=False, w=1.0             r@5=1.000 r@10=1.000 mrr=0.558 raw=25 kept=25
DIAGNOSTIC link_add_only=False, w=0.3             r@5=1.000 r@10=1.000 mrr=0.604 raw=25 kept=25
DIAGNOSTIC link_add_only=False, w=0.1             r@5=1.000 r@10=1.000 mrr=0.911 raw=25 kept=25
```

连"仅 wikilink"、"只从最佳锚点扩展"、以及把 RRF 权重一路降到 0.1 都试过：recall
全程不掉，但**没有任何配置把 MRR 抬到 baseline 之上**；非加性全量融合只会更差。

读这张表时请注意两个容易混淆的点：

- `raw` 是**图里真实找到的 link 候选数**，`kept` 是**通过加性过滤、真正进入融合的数**。
  产品行 `kept=0` 正是 MRR 停在 0.917 的**机制**（link 列表无法重排任何东西），
  而不是碰巧；诊断行一旦 `kept=25` 就立刻劣化到 0.558——这就是默认值的正当性来源。
- `raw=25 / 18 / 11`（默认加性 / 仅 wikilink / 单锚点）说明 link 路**确实会触发**，
  并不是"永不生效的开关"。所以结论是"**没有增益**"（no gain），**不是**"没有效果"
  （no effect）——只有前一种说法被数据支持。

`test_link_tuning_record_never_costs_recall` 把"任何配置不得降 recall + 无配置超过
baseline"钉成断言，将来若出现真实增益该测试会失败并强制更新本文档的结论。

说明（同样适用于上面的 link 数字）：语料很小，词法路径在双字覆盖回退下也能召回；
真实质量取决于用户选择的 embedding 模型，上述数字用于**回归对比**而不是性能承诺。

性能（120 篇 / 720 chunk，Mock 嵌入）：

```text
全量索引 373–613 ms；混合检索中位数 6.5–8.7 ms（worst 6.6–12.9 ms）；分块 0.17–0.28 ms/篇；
改一段 = 1 次嵌入（8 次不同时点实测，差异为负载波动、非回归；环境：macOS arm64 / Apple M4、
Python 3.12.13、SQLite 3.53.4、未安装 numpy（纯 Python 内核）、Mock 嵌入）
```

## 10. 已知限制与后续

见 `M14-REPORT.md` 第 10 节。
