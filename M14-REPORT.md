# M14 交付报告 — LocalBook RAG（检索增强生成）

> 阶段：M14（在 v1.0.0 / M0–M13 架构之上新增，不改动既有能力，不实现 Obsidian
> Plugin Runtime，不大规模重构 UI）。
> 所有数字均为本机实际运行结果（macOS、Python 3.12.13、SQLite 3.53.4、
> Node 22.22.2、无 GPU 加速），未做任何估算或伪造。

---

## 1. 实现摘要

新增一条完整、可维护、local-first 的 RAG 链路，并把它作为 **LocalBook Core 能力**
实现（不依赖 Web UI、不依赖 React workspace）：

```text
Markdown Vault（唯一事实源，只读，经 VaultService）
   → Markdown 感知分块（标题/段落/列表/引用；代码块/表格/frontmatter 不被破坏）
   → 独立 EmbeddingProvider（hash 离线回退 / OpenAI-compatible）
   → SQLite 向量索引（与 M4 同一个 .localnote/index.db，additive 表）
   → 混合检索：FTS5（chunk 级）+ 向量（余弦点积）
   → RRF 融合（k=60）
   → 可选 LinkRetriever 第三路（wikilink/backlink/tag/graph 四信号；默认关闭、只新增不重排）
   → 可选重排（默认关闭；失败只降级）
   → RagContextBuilder → EvidencePack（去重/合并相邻块/token 预算）
   → 已有 AI Provider（Chat）生成
   → 引用校验（只保留 EvidencePack 中真实存在的 [S1]…）
   → REST：answer + sources（可点击）+ retrieval_stats + model
```

阶段推进严格按依赖顺序（Phase 1→11），每阶段先写测试再实现，逐阶段跑测试。

| Phase | 内容 | 状态 |
|---|---|---|
| 1 | RAG schemas + Markdown 感知分块 | ✅ |
| 2 | EmbeddingProvider + mock/离线实现 | ✅ |
| 3 | VectorStore + 增量索引 | ✅ |
| 4 | VectorRetriever + FTS 适配 | ✅ |
| 5 | RRF HybridRetriever | ✅ |
| 6 | ContextBuilder + EvidencePack | ✅ |
| 7 | RAGService + grounded generation + 引用校验 | ✅ |
| 8 | API（4 端点 + 设置端点） | ✅ |
| 9 | Vault 生命周期增量索引接入 | ✅ |
| 10 | Web UI（AI 面板 RAG 模式 + 设置分区） | ✅ |
| 11 | Evaluation + E2E + 性能测试 | ✅ |

---

## 2. 架构变化

- **新增派生层，而非第二套架构**：RAG 表与 M4 的 `notes/notes_fts/...`、M7 的
  history 表**同库同事务**（`.localnote/index.db`），通过 `ensure_rag_tables()`
  增量建表，`SCHEMA_VERSION` 不变，打开旧库不会触发笔记重建。
- **检索分层**：`KeywordRetriever` / `VectorRetriever` / `HybridRetriever`
  各自实现同一 `Retriever` 协议，`LinkRetriever` 为同级第三路（roadmap 第 ③ 项，
  已实现，默认关闭，见 §9.5）；`GraphRetriever` 仍是同级未来扩展位；
  业务逻辑不写死在 `vector_search()` 里。
- **存储可替换**：`VectorStore` 协议 + `SqliteVectorStore` 实现；未来换
  `sqlite-vec`/其他引擎只需实现协议，不改检索/上下文/API。
- **唯一装配点**：`server/rag/factory.py::create_rag_stack`，由
  `VaultLifecycle` 在 M4 索引就绪后调用；watcher 事件经一个组合回调同时喂给
  M4 索引与 RAG 索引（`_event_callback`）。
- **配置独立**：`RagSettings`（`LOCALNOTE_RAG__*`），Chat 模型与 embedding 模型
  完全解耦；`PATCH /api/v1/settings/rag` 只重建 RAG 派生层，不重启 Vault。
- **前端最小侵入**：AI 检查器新增模式切换（原「当前笔记」行为不变），
  设置新增「RAG 知识库」分区；未改动 workspace store 的既有语义。

---

## 3. 新增文件

后端核心（`server/rag/`）

```text
__init__.py                 schema.py                 schemas.py
api_schemas.py              citations.py              errors.py
index_service.py            service.py                factory.py
chunking/markdown.py
embeddings/{base,openai_compatible,runner}.py
vector/{base,sqlite}.py
retrieval/{base,keyword,vector,hybrid,rrf}.py
retrieval/link.py                                       （③ Link/Graph 四信号加权）
rerank/base.py
context/builder.py
prompts/rag_answer.md   prompts/__init__.py
```

API

```text
server/api/routes/rag.py
```

前端

```text
apps/web/src/components/RagPanel.tsx       （知识库问答 + 可点击来源）
apps/web/src/components/RagSettings.tsx    （RAG 设置分区 + 状态 + 重建）
```

文档与测试

```text
docs/rag-architecture.md
M14-REPORT.md
tests/rag/conftest.py           tests/rag/test_index.py
tests/rag/test_retrieval.py     tests/rag/test_context_and_service.py
tests/rag/test_api.py           tests/rag/test_e2e.py
tests/rag/test_eval.py          tests/rag/test_perf.py
tests/rag/test_settings_api.py  tests/rag/test_link_retrieval.py
tests/rag/unit/test_chunking.py tests/rag/unit/test_embeddings.py
tests/rag/eval/{golden.py,run.py}
tests/frontend/RagPanel.test.tsx
tests/frontend/RagSettings.test.tsx
```

## 4. 修改文件

```text
server/config.py                新增 RagSettings + snapshot_view（密钥不回显）
server/index/service.py         新增 database 属性（共享同一个 IndexDatabase）
server/vault/lifecycle.py       RAG 栈装配、watcher 组合回调、reconfigure_rag、shutdown 顺序
server/api/main.py              注册 rag 路由 + RagError 安全处理器
server/api/dependencies.py      get_rag_stack/get_rag_service/生成器绑定
server/api/routes/settings.py   RagConfiguration + PATCH /settings/rag
server/runtime.py               快照带 rag、apply_rag（只重建派生 RAG 层）、发布 rag_stack
packages/protocol/src/index.ts  M14 DTO（RagQuery/RagSearch/RagSource/状态）
apps/web/src/api/client.ts      RAG 四个端点客户端
apps/web/src/api/types.ts       M14 类型导出
apps/web/src/api/settings.ts    RagConfiguration/PATCH 客户端
apps/web/src/components/AIPanel.tsx        模式切换（默认仍是当前笔记）
apps/web/src/components/SettingsPanel.tsx  接入 RAG 分区
apps/web/src/styles.css        RAG 面板与设置样式（使用 --fs-* 令牌，跟随字号缩放）
README.md / CHANGELOG.md / docs/development-roadmap.md   M14 记录
```

## 5. 数据库变化

同一库 `.localnote/index.db`，全部为**可删除重建的派生数据**（不改 M4 表、
不改 `SCHEMA_VERSION`）：

| 表 | 关键列 | 说明 |
|---|---|---|
| `rag_documents` | `path`(PK) `sha256` `content_hash` `chunk_count` `embedded_count` `embedding_model/version/dimension` `status` `vec_d<N>` | 每篇笔记一行；`vec_d<N>` 是该文档 chunk 的连续 float32 向量 run |
| `rag_chunks` | `chunk_id`(PK) `document_id` `chunk_index` `content` `content_hash` `heading(_path)` `section_path` `start/end_line` `start/end_offset` `tags` `aliases` `token_estimate` | 语义块；`content` 永远是源文件切片 |
| `rag_chunks_fts` | FTS5(content, heading, tags, path, chunk_id UNINDEXED, document_id UNINDEXED) | chunk 级词法检索 |
| `rag_embeddings` | `chunk_id`(PK, FK cascade) `model` `embedding_version` `dimension` `vector` `content_hash` | 向量复用判定（hash+model+version 一致才复用） |
| `rag_index_state` | 单行 | provider/model/维度/版本、计数、pending/failed、status、last_indexed_at |

迁移策略：`ensure_rag_tables()` 在打开数据库时执行 `CREATE TABLE IF NOT EXISTS`
（与 M7 history 表同样的 additive 做法）；不存在"删库才能升级"的情形。
`store.reset()` / 删除 `.localnote/` 后 `rebuild` 可完整恢复。

## 6. 新增 API

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/rag/index/status` | 状态：`enabled/status/embedding_provider/model/dimension/version/embedding_degraded/vector_kernel/link_retrieval/indexed_notes/chunks/embedded_chunks/pending/failed/last_indexed/chunk_*_tokens/message` |
| `POST` | `/api/v1/rag/index/rebuild` | 全量重建（只写派生数据），返回计数与状态 |
| `POST` | `/api/v1/rag/search` | 仅检索（**不调用 Chat**），`{query, top_k}` → `results[{path,heading,excerpt,score,keyword_rank,vector_rank,link_rank,rerank_score,start_line,end_line,document_level}]` + `stats` |
| `POST` | `/api/v1/rag/query` | `{query, top_k?, rerank?, debug?}` → `{answer, sources[{id,path,heading,start_line,end_line,excerpt}], retrieval_stats, model, prompt_version, degraded, invalid_citations}` |
| `PATCH` | `/api/v1/settings/rag` | 保存 RAG 配置（只重建派生 RAG 层）；③ 新增六个 link 相关字段（其中**仅 `link_retrieval_enabled` / `link_top_k` 带 `link_` 前缀**，四个权重为 `wikilink_weight` / `backlink_weight` / `tag_weight` / `graph_weight`）（`extra="forbid"`，未知键 422） |

`link_retrieval` 的三个取值：`""`（未接线，前端不渲染状态卡）、`"enabled"`、
其余为降级原因（如 `"link_unavailable"`）。`retrieval_stats.link_candidates` 是
Link 路候选数；link 命中带 `link_rank`（该路内 1-based 排名）与 `document_level`
（`true` = 整篇命中，行区间不是行级精确，不得当作精确引用）。

错误语义：查询为空/超长 → 400 `invalid_request`；RAG 索引不可用 → 503
`rag_unavailable`；其余端点（health/FTS/编辑）不受影响。

## 7. UI 变化

- **AI 检查器新增模式切换**：`当前笔记`（M6 原有，行为不变）/ `知识库检索`。
- **`RagPanel`**：提问、检索统计（词法/向量候选、引用片段数、检索耗时）、
  答案正文、**来源列表**（`[S1] LocalBook设计.md › 插件兼容` + 行号 + 摘要），
  点击来源调用既有 `onOpenNote` 打开对应 Markdown；降级/错误/无证据状态均有明确文案。
- **`RagSettings`**（设置 → RAG 知识库）：启用开关、Embedding Provider/Base URL/
  Model/API Key（写入后不回显）、分块 tokens、FTS/Vector/Context Top K、重排开关、
  索引状态卡（笔记数/片段数/已嵌入/状态/模型/上次索引/待处理/失败）、
  「重建 RAG 索引」按钮。
- **③ 新增 Link 检索开关**：`Link/Graph 加权检索` 开关 + 5 个权重输入
  （`link_top_k`、`wikilink_weight`、`backlink_weight`、`tag_weight`、`graph_weight`），
  权重输入**仅在开关打开时显示**。状态卡只在 `link_retrieval` 非空时显示，
  且值不是 `"enabled"` 时给出降级提示——**不把"已配置"伪装成"已启用"**。
  保存走白名单 payload（`RAG_PATCH_KEYS`），只发送服务端声明过的键。
- 样式使用 `--fs-*` 令牌，跟随字号缩放（`FontPreferences` 门禁保持通过）；
  ③ 新增的开关与权重输入复用既有类名，未引入新的字号硬编码。

## 8. 测试结果（实际运行）

```text
后端（pytest，含 RAG）——数字为 2026-09-12（③ 落地后）实测：
  python -m pytest tests -q --ignore=tests/backend/perf
  → 1195 passed, 5 skipped（③ 之前为 1135 passed, 5 skipped；2 项 skip 是
     "本机未安装 numpy" 时的加速内核测试，属预期跳过）

  RAG 专用门禁：./scripts/rag-eval.sh → 272 passed, 2 skipped + 评估表 + 门槛校验通过（exit 0）
  （`python -m pytest tests/rag -q` 单独运行同样为 272 passed, 2 skipped）
  其中 ③ 新增：test_link_retrieval.py 35 项；`-k link` 选中 57 项；
  test_settings_api.py 14 项（原 6 + ③ 新增 8）。

前端（vitest）：
  pnpm --filter @localnote/web test -- --run
  → 41 passed / 1 skipped 文件；355 passed, 3 skipped（exit 0；③ 之前为 346 passed）

  其中 M14 新增（单独运行，两个新文件）：
  tests/frontend/RagPanel.test.tsx + RagSettings.test.tsx → 2 files, 26 passed
  （RagPanel 8 项 + RagSettings 18 项，RagSettings 中 ③ 新增 9 项）

  说明（如实记录）：上述 `--run` 在本轮共执行 12 次，其中 11 次为
  355 passed / exit 0，1 次（与后端 pytest 并发执行时）出现
  "2 failed | 353 passed"、exit 1，随后 11 次连续干净通过且未能复现出具体失败用例名。
  因此本报告把它记为**一次未复现的偶发波动**，不写成稳定通过。
  ③ 之前记录的 AIProviders.test.tsx post-render 错误已由后续修复消除，本次运行无未捕获错误。

其他门禁：
  python -m compileall server            → ok
  ruff check server/rag tests/rag        → All checks passed
  pnpm --filter @localnote/{protocol,workspace,web} typecheck → 全部通过
  pnpm --filter @localnote/web build     → ✓ built（唯一警告是既有的 500kB chunk 提示）
```

RAG 测试覆盖（按题目要求逐项）：

| 要求 | 对应测试 |
|---|---|
| Chunker 单元测试 | `tests/rag/unit/test_chunking.py`（15 项：标题层级、代码块/表格不被切、frontmatter 元数据、行号/offset 一致性、长中文段落切分、chunk id 稳定性） |
| Embedding provider mock 测试 | `tests/rag/unit/test_embeddings.py`（13 项：维度发现、批量、鉴权/HTTP/超时/非法响应、维度变化检测、Chat/Embedding 解耦） |
| VectorStore 测试 | `tests/rag/test_index.py`（打包往返、写入/读回、替换、删除、reset、维度切换清理、复用判定） |
| RRF 测试 | `test_retrieval.py`（双方命中优先、确定性、1/(k+rank)、非法 k） |
| Hybrid retrieval 测试 | `test_retrieval.py`（无 embedding/embedding 失败降级、双路融合、rerank 可选与失败降级、allowed_paths、top_k） |
| ContextBuilder 测试 | `test_context_and_service.py`（去重、相邻合并、总/单文档 token 预算、编号与来源渲染） |
| citation validation 测试 | `test_context_and_service.py`（保留合法、删除 `[S99]`、来源只来自 pack、无引用改为固定话术） |
| 增量索引测试 | `test_index.py`（仅重索该文档、未变不嵌入、只重嵌变化 chunk、FTS 无残留行） |
| rename/delete 测试 | `test_index.py`（rename 不重嵌、rename+改文重嵌、delete 清理） |
| embedding model 变化测试 | `test_index.py`（model/version 维度过滤）、`test_settings_api.py`（配置生效） |
| API 测试 | `test_api.py`（4 端点 + 400/422/503 + 会话语义）、`test_settings_api.py`（快照/PATCH/冲突/密钥） |
| RAG E2E 测试 | `test_e2e.py`（15 篇语料，见 §9） |
| 相似度内核（可选 numpy 加速） | `test_kernels.py`（内核契约、numpy 缺失/异常回退、与纯 Python 排名一致、真实加速） |
| M6 `related` 复用 RAG 证据 | `test_related_integration.py`（RAG 命中优先/去重/不越界/不含当前笔记/上限，RAG 缺失时退回 M4 路径，端到端语义邻居） |
| 出站传输策略（shell 代理） | `test_transport_proxy.py`（Chat/发现/Embedding/Reranker 默认忽略 shell 代理、可显式开启、代理变量损坏时 `/ai/status` 降级而非 500） |
| 可选重排（本地 + HTTP） | `test_rerank.py`（覆盖度排序、工厂禁用/接线/配置缺失、HTTP 形状与错误映射、文档上限、探活端点、循环内调用） |
| **③ Link/Graph 加权检索** | `test_link_retrieval.py`（35 项：四信号加权与去重 seed 计数、断链 `[[missing]]` 不返回、锚点/`exclude_paths`/非索引路径不返回、`(-score,path)` 确定性、chunk 级真实行号 vs `document_level` 降级不编造行区间、`link_unavailable`/`link_no_seed`、默认旋钮下 link 候选必在直接候选之后、加性 vs 全量融合） |
| **③ Link 配置面** | `test_settings_api.py`（14 项 = 原 6 + 新增 8：默认中性、生效并 bump revision、状态标记真实、无索引降级不隐藏、构造失败降级、越界 422、幻影键 422、跨语言契约漂移）、`tests/frontend/RagSettings.test.tsx`（18 项 = 原 9 + 新增 9：默认值与冻结单例、权重仅开关打开显示、保存 payload 仅白名单键、失败 `role=alert`、未保存编辑存活、状态标记三态、PATCH 净化） |

## 9. RAG Eval 结果（实际输出）

Golden Dataset：`tests/rag/eval/golden.py`，**15 篇笔记 / 14 个问题**，覆盖中文、
英文、中英混合、同义改写（semantic_only 标注）、精确术语（lexical_only 标注）、
易混淆主题（园艺 vs 软件）、wikilink 内容。

同一候选池（每路 30 个候选，按前 10 篇文档评分）比较**五种模式**：

```text
$ ./scripts/rag-eval.sh        # 2026-09-12 实测（③ 落地后）；节选，逐字保留原文（含缩进）
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
== RAG gate passed ==
```

（节选：完整输出还包含 `== RAG golden-dataset evaluation ==` 标题行、`Link-path tuning
record` 调参记录块，以及带 `exit 0` 的收尾；上表只保留与本报告结论直接相关的行，除标注外未改写任何字符。）

如实说明：

- 这是**离线确定性 hash 嵌入**（256 维）下的结果，用于**回归对比**，不代表用户
  使用真实 embedding 模型时的绝对质量；
- 语料只有 15 篇，词法路径在中文字符双字覆盖回退下召回也很高，因此
  "hybrid 优于单路"在这里主要体现在 **recall@5 = 1.000**（优于 vector 的 0.929），
  而 MRR 略低于词法单路（0.917 vs 0.964）——报告不隐藏这一点，而是标记为
  `tradeoff`；`test_mrr_degradation_is_reported_not_hidden` 专门锁定"必须如实报告"这个行为；
- **③ Link 路在本数据集上是"中性"而非增益**：`hybrid_link` 的
  recall@5 / recall@10 / MRR 与纯 `hybrid` **完全相同**（1.000 / 1.000 / 0.917，
  MRR 差 **+0.000**）。"不得降低 recall@5/@10"的硬要求满足（保持 1.000），
  但**没有观测到任何提升**，因此 link 路保持默认关闭。详见 §9.5；
- 门禁阈值（`tests/rag/eval/run.py::FLOORS`）刻意低于实测值，只用于拦截显著回退：
  hybrid recall@5 ≥ 0.90、recall@10 ≥ 0.95、MRR ≥ 0.80，keyword recall@10 ≥ 0.85，
  vector recall@10 ≥ 0.85；hybrid_link **只**继承 hybrid 的 recall 两条，
  **刻意没有 MRR 下限**——改用相对规则 `hybrid_link.mrr >= hybrid.mrr`（基线取同一次
  运行的纯 hybrid），因为冻结的 MRR 常数正是这条检查要避免的调参。
  规则**每次运行都会评估并打印**（`floors: OK|FAILED`），`--check` 只决定违反时是否
  以非零码退出；`--no-tests` 也不能关掉规则。**未因 ③ 调低任何既有门槛，
  也未删除任何既有模式。**

E2E 验收（`tests/rag/test_e2e.py`，12 项，全部通过）逐条对应题目"至少验证"：

| 验收点 | 测试 | 结果 |
|---|---|---|
| FTS + Vector + RRF 三路都参与 | `test_e2e_full_chain_uses_fts_vector_and_rrf` | fts_candidates ≥1、vector_candidates ≥1、fused ≥2 |
| 引用绝不出界 | `test_e2e_answers_are_grounded_in_real_notes`、`test_e2e_citations_never_point_outside_the_vault` | 每个 source 都在 Vault 内，行号在文件行数范围内 |
| 不存在的内容不被当作事实 | `test_e2e_nonexistent_content_is_not_answered_as_fact`、`test_e2e_answer_without_citation_is_replaced` | 无证据时不调用模型并返回固定"证据不足"话术；有证据但无引用时替换为固定话术 |
| 伪造引用被删除 | `test_e2e_invalid_citation_is_removed_and_reported` | `S99` 不在 answer 中，列入 `invalid_citations` |
| RAG 只读 Vault | `test_e2e_rag_never_writes_to_the_vault` | 三次问答前后所有笔记 sha256 完全一致 |
| 删除索引可完整重建 | `test_e2e_derived_index_can_be_deleted_and_rebuilt` | `reset()` 后检索为空，`rebuild()` 后排名与之前一致 |
| embedding 下线可降级 | `test_e2e_embedding_outage_degrades_without_breaking_lexical_search` | `degraded=["vector_unavailable"]`，词法证据仍可作答，笔记仍可读 |
| 中文查询 / 英文查询跨语言 | `test_e2e_chinese_query_finds_chinese_note_and_english_finds_chinese` | 中文问题命中中文笔记，英文问题命中英文/中文笔记 |
| 比较两篇笔记 | `test_e2e_comparison_query_returns_two_documents` | 两个来源都是真实文档 |
| EvidencePack 是模型边界 | `test_e2e_evidence_pack_is_the_model_boundary` | prompt 中每一块都有编号 + path/lines，块数等于 pack 块数 |

性能（`tests/rag/test_perf.py`，120 篇 / 720 chunk，Mock 嵌入，本机实测）：

同一台机器、同一测试、**8 次不同时点**的实测（说明这是**负载相关的波动区间**，不是回归）：

```text
[perf] indexed 120 notes / 720 chunks in 344 ms      （最早一次，空载）
[perf] hybrid retrieval over 720 chunks: median 6.5 ms / worst 6.6 ms (fts=30, vector=30)
[perf] chunking one note: 0.19 ms
[perf] one-note edit embedded 1 chunk(s) (vault has 720 chunks)

[perf] indexed 120 notes / 720 chunks in 373 ms      （③ 落地后，最后一次）
[perf] hybrid retrieval over 720 chunks: median 6.6 ms / worst 6.9 ms (fts=30, vector=30)
[perf] chunking one note: 0.19 ms

[perf] indexed 120 notes / 720 chunks in 613 ms      （③ 落地后，负载高峰）
[perf] hybrid retrieval over 720 chunks: median 8.5 ms / worst 11.4 ms (fts=30, vector=30)
[perf] chunking one note: 0.28 ms
```

8 次全量索引落点：344 / 373 / 389 / 409 / 410 / 436 / 456 / 613 ms；
混合检索中位数落点：6.5 / 6.6 / 6.6 / 7.1 / 8.0 / 8.5 / 8.7 ms（worst 6.6–12.9 ms）。

测量环境（各次相同）：macOS arm64 / Apple M4 · Python 3.12.13 · SQLite 3.53.4 ·
未安装 numpy（走纯 Python 内核）· Mock 嵌入。因此正确读法是
**"373–613 ms 全量索引 / 6.5–8.7 ms 中位检索"**，而不是某个单点数字；
本次 ③ 改动未观测到 link 路带来的性能变化（区间在 ③ 前后重叠）。

结论：**已完成 embedding 的情况下检索不依赖 Chat 模型**，720 chunk 规模中位
6.5–8.7 ms（目标 < 500 ms 达成）；全量索引 373–613 ms；改一段只重新嵌入 1 个 chunk，
未变内容 0 次嵌入。

## 9.1 本轮修复的真实缺陷（附带回归测试）

在把 RAG 门禁脚本化时，测试环境暴露出一个**与 M14 无关但会直接毁掉本机 AI 的既有缺陷**：

- `httpx.AsyncClient` 默认 `trust_env=True`，会解析 `HTTP_PROXY`/`NO_PROXY`；
  当 `NO_PROXY` 含 IPv6 字面量（本机为 `localhost,127.0.0.1,::1,[::1]`）时，
  httpx 在**建立连接之前**抛 `InvalidURL: Invalid port: ':1]'`；
- 该异常发生在 `OpenAICompatibleAdapter._request` 的 `try` 之外，绕过
  `AIAdapterError`/`AIError` 映射，因此在真实环境里会让 **M6 Chat、模型发现、
  Agent、Embedding、重排** 全部以 HTTP 500 失败——用户并未配置任何代理。

修复：所有出站客户端改为 `trust_env=False`（`AISettings.use_env_proxy` /
`RagSettings.use_env_proxy` 可显式开启），并新增
`tests/rag/test_transport_proxy.py` 覆盖四个客户端 + `/ai/status` 端到端降级行为
（其中一条断言刻意证明"若沿用 httpx 默认值，在本环境确实会崩"）。

## 9.2 M6 `Suggest Related` 复用同一证据路径（M14 §八）

`Suggest Related` 原本只有"关键词 + 链接/图谱邻居"候选。现在
`reduce_candidates(..., rag_hits=...)` 增加**最高优先级来源**：M14 的 chunk 级
混合检索（FTS + 向量 + RRF），由 `AIWorkflowService._rag_candidates` 提供。

- 复用同一条检索链：RAG 答案与相关笔记建议的候选来自**同一批 chunk**；
- 不需要关键词重合：语义相近但用词不同的笔记也能进入候选（E2E 测试
  `test_related_prefers_semantic_neighbour_found_only_by_rag` 覆盖）；
- 候选仍然**先程序化收窄再调用模型**，并且必须在索引 allow-list 内——
  `test_rag_hits_never_allow_a_path_outside_the_index` 证明伪造路径不可能成为候选；
- RAG 关闭/不可用/查询为空 → 自动退回原 M4 路径（`test_without_rag_hits_the_m4_path_still_works`、
  `test_related_still_works_when_rag_is_absent`），既有 M6 契约与测试不受影响。

## 9.3 顺带修好的设置面板崩溃（既有缺陷）

前端全量测试长期存在一个 **post-render 未捕获错误**（`AIProviders.test.tsx` 之后报
`Cannot read properties of undefined (reading 'api_key_set')`），使 `vitest run`
以非零码退出（断言全过但门禁不过）。

根因：`SettingsPanel` 直接读 `current?.ai.api_key_set`——`current` 本身存在但 `ai`
缺失时（旧版服务端、上一个 revision 的响应、测试替身）可选链不会兜住，渲染阶段抛错，
React 卸载整棵设置树。

修复：引入受保护视图 `const currentAi = (current?.ai ?? {})`，三处读取改走它。
现在 `pnpm --filter @localnote/web test` **exit=0**（41 files / 346 passed / 3 skipped，
无未捕获错误；该 346 为修复当时的值，③ 落地后为 355 passed，见 §8）。

## 9.4 验证流程缺陷（已确认并修正）

独立验证者（t4）在实施前审查验证计划时发现并报告了一个**会让"未放宽断言"检查变成假通过**的缺陷，队长已独立复核确认：

```text
$ git status --short -- server/rag tests/rag
?? server/rag/
?? tests/rag/
$ git ls-files server/rag tests/rag | wc -l
0
$ git diff -- tests/rag/test_eval.py | wc -l
0
```

M14 的 `server/rag/**` 与 `tests/rag/**` 全部是 **untracked**，因此
`git diff -- tests/rag` 永远打印空内容——把它当作"M14 既有断言未被改写/放宽"的证据是**无效检查**（vacuous check），会静默给出假通过。修正后的替代方案：

- 以改动前的文件级 **sha256 清单**（`/tmp/vfy/baseline_rag_hashes.txt`，覆盖 `server/rag` +
  `tests/rag` 共 51 个文件）做 before/after 对比，并逐条人工比对断言差异；
- 同时以**改动前冻结的评估基线**作为"默认关闭时行为不变"的判据（仅看改动后数字无法证明这一点）：

| mode | recall@5 | recall@10 | MRR |
|---|---|---|---|
| keyword | 1.000 | 1.000 | 0.964 |
| vector | 0.929 | 1.000 | 0.938 |
| hybrid | 1.000 | 1.000 | 0.917 |
| hybrid_rerank | 1.000 | 1.000 | 0.952 |

- **预注册的评估诚实性关注点**：hybrid 的 recall@5/@10 已在 1.000 天花板，"link 不得降低
  recall"实际上无法证伪任何东西；真正的风险是 **MRR 回退**（hybrid 0.917 本就低于 keyword
  0.964）与**通过修改 Golden Dataset 期望路径制造增益**。因此验证要求：显式比较 MRR、
  校验 Golden Dataset 内容 hash 未被改动。

**对文档与 CI 的结论**：`git diff` 不能作为 M14 回归保护手段；回归保护依赖
`tests/rag/**` 的断言本身与 `scripts/rag-eval.sh` 的门槛校验（该脚本与测试文件已入库，
但请确保提交时把 `server/rag/`、`tests/rag/` 纳入版本控制，否则上述保护在 review 流程中不可见）。

## 9.5 Link/Graph 加权检索（roadmap 第 ③ 项）——实现与**负结果**

### 9.5.1 实现了什么

新增 `server/rag/retrieval/link.py::LinkRetriever`，作为 RRF 的**可选第三路**接入
`HybridRetriever`。它回答的不是"哪些 chunk 在讲查询词"，而是"哪些笔记与查询已命中
的笔记**相连**"：

| 优先级 | 信号 | 默认权重 | 含义 |
|---|---|---|---|
| 1 | `wikilink` | `1.0` | 锚点 `[[出边]]` 指向的笔记 |
| 2 | `backlink` | `0.8` | 反向链接到锚点的笔记 |
| 3 | `tag` | `0.6` | 与锚点共享 tag 的笔记（每 tag 上限 50） |
| 4 | `graph` | `0.4` | 距锚点**恰好两跳**的图邻居 |

锚点（seed）取 `KeywordRetriever` 前 5 篇；得分 = 各信号"去重 seed 计数 × 权重"之和。
图来自 `DerivedIndexService.graph_snapshot()`（一次性读 `notes`/`tags`/`links`），
**全程只读派生数据**，不写 SQLite、不写 Vault。

准入与确定性（这些是"link 候选可用于引用"的前提）：非派生索引行的路径一律丢弃
（断链 `[[missing]]` 不可能成为引用）；锚点自身 / `exclude_paths` / 越界 `allowed_paths`
不返回；排序 `(-score, path)`，与 seed 顺序无关。chunk 级命中给真实
`chunk_id`/行号/`content_hash`；无 chunk 时降级 `doc::<path>` + `document_level=True`
并**保持默认 1/1 行区间，不编造行级区间**。

### 9.5.2 量化结果：**中性，零增益**（这是结论，不是保留意见）

```text
模式（同 candidate_k=30 候选池）       recall@5  recall@10  MRR
keyword                                 1.000     1.000    0.964
vector                                  0.929     1.000    0.938
hybrid                                  1.000     1.000    0.917
hybrid_rerank                           1.000     1.000    0.952
hybrid_link  ← ③ 新增                   1.000     1.000    0.917
                                          ↑ 与 hybrid 逐位相同（+0.000）
link contribution: raw candidates=25, kept by the additive filter=0, over 14 questions
```

- **硬要求满足**：`hybrid_link` 的 recall@5 / recall@10 保持 **1.000 / 1.000**，
  没有降低；
- **增益没有出现**：MRR 与纯 `hybrid` **完全相同**（0.917，差 +0.000）。
  因此本报告**不声称 link 路带来任何收益**，并保持 `link_retrieval_enabled=False`。

### 9.5.3 为什么是零（可复现的原因，不是借口）

1. 语料共有 **4 条链接行**（3 条 `wikilink` + 1 条 `embed`），其中 **1 条 wikilink 断链**
   （`Notes/wiki-links.md` 的 `[[云边协同机械臂系统]]`，`resolved=None` / `broken=True`）
   在建图时被 `_build_graph_view` 丢弃（它只保留 resolved 且非 broken 且目标有 `notes`
   行的边，**不按 kind 过滤**），因此**可参与图扩展的解析边只有 3 条**
   （2 条 wikilink + 1 条 embed）；
2. 候选池 `candidate_k=30` **远大于语料规模**（15 篇 = **15 个 chunk**，每篇恰好 1 块，
   实测 `rag_documents` 与 `rag_chunks` 均为 15，即候选池覆盖全库两遍），因此所有图邻居
   **本来就已被** keyword/vector 召回——link 路能提供的"新"候选天然为 0；
3. 默认 `link_add_only=True`（图扩展只**新增**直接路没返回的文档），且默认旋钮下
   `0.5/(60+20)=0.00625 < 1/(60+30)=0.0111`，link-only 候选必然排在所有直接候选之后。

三点叠加 ⇒ 加性过滤后进入融合的 link 候选 = **0 / 14 题**，融合结果逐位不变。

### 9.5.4 调参记录（harness 每次打印，任何可复现）

```text
hybrid (link off)                                 r@5=1.000 r@10=1.000 mrr=0.917 raw=0  kept=0
hybrid_link (add-only, shipped default)           r@5=1.000 r@10=1.000 mrr=0.917 raw=25 kept=0
link additive, wikilinks only (graph=0, tag=0)    r@5=1.000 r@10=1.000 mrr=0.917 raw=18 kept=0
link additive, single best anchor (seed_top_k=1)  r@5=1.000 r@10=1.000 mrr=0.917 raw=11 kept=0
DIAGNOSTIC link_add_only=False, w=1.0             r@5=1.000 r@10=1.000 mrr=0.558 raw=25 kept=25
DIAGNOSTIC link_add_only=False, w=0.3             r@5=1.000 r@10=1.000 mrr=0.604 raw=25 kept=25
DIAGNOSTIC link_add_only=False, w=0.1             r@5=1.000 r@10=1.000 mrr=0.911 raw=25 kept=25
```

- **`raw` 与 `kept` 是机制，不是脚注**：产品行 `kept=0`（被加性过滤全部挡下）正是
  MRR 停在 0.917 的**原因**，而不是运气；诊断行 `kept=25` 一旦放开排序就立刻劣化
  （0.558），这恰好就是默认值得以成立的理由。
- **link 路是真的会触发的，不是"永不生效的开关"**：`raw=25 / 18 / 11` 分别对应默认
  加性、仅 wikilink、单锚点三种配置——三种都在图里真的找到了候选。所以本节的结论是
  "**没有增益**"（no gain），**不是**"没有效果"（no effect）：只有前者被数据支持。
- 只保留 wikilink、只从最佳锚点扩展、把 RRF 权重降到 0.1 —— 都试过：**recall 全程不掉，
  但没有任何配置把 MRR 抬到 baseline 之上**（这两行调参证据即上表的
  `wikilinks only (graph=0, tag=0)` 与 `single best anchor (seed_top_k=1)`）；
- **非加性全量融合更差**：它把直接路已召回的低位笔记（keyword 第 6 + vector 第 7）
  抬到第 1 位，MRR **0.917 → 0.558**；即使 w 降到 0.1 也只回到 0.911，仍低于 0.917。
  因此 `link_add_only=True` 不是保守选择，而是当前唯一**不产生回退**的接线方式；
- 上表的 `DIAGNOSTIC` 行**不对应任何设置项**（`link_add_only` 未暴露为配置），
  仅用于复现 `hybrid.py` 注释里引用的 0.558 这一测量，避免"数字无出处"。

测试把结论钉住：`test_link_tuning_record_never_costs_recall` 断言"任何配置不得降
recall + 无配置超过 baseline"，`test_default_knobs_keep_link_candidates_below_every_direct_one`
固定上述不等式。将来若出现真实增益，这些测试会失败并**强制**更新本节结论，
而不是让文档留下过期的"中性"说法。

### 9.5.5 默认关闭与降级（不隐藏、不阻断）

- `link_retrieval_enabled` 默认 **False**；关闭时工厂不接线（`link=None`），
  候选生成、RRF 输入列表、融合顺序、`stats`、`degraded`、`debug` 与 ③ 之前**逐位相同**
  （既有断言未改且全绿）；
- 开启但派生索引不可用 → **仍然接线**并报 `link_retrieval="link_unavailable"` + WARNING，
  **不把配置错误藏成"未启用"**；构造异常 → `None` + 日志，绝不阻断 RAG 启动；
- 配置面：`RagSettings` 六个字段 + `PATCH /api/v1/settings/rag`（`extra="forbid"`）
  + 前端开关与 5 个权重输入（仅开关打开时显示）+ 状态卡（非 `"enabled"` 时给降级提示）。

## 10. 已知限制

1. **向量搜索是内存中的暴力扫描**（无 sqlite-vec 依赖）。已实现可选 numpy 加速
   内核：numpy 存在时 20k×256 从约 190–215 ms（纯 Python）降到首查 86 ms、
   稳定态约 1.5 ms；numpy **不是声明依赖**，缺失或异常时自动回退到纯 Python 内核
   （③ 之前曾以干净 venv（未装 numpy）复跑全量测试 1118 passed；该数字属 ③ 之前的
   时点，本轮未重跑干净 venv，故未随 ③ 的计数一起更新）。10 万级 chunk 仍需要真正的
   向量索引后端，`VectorStore` 协议已为此预留。
2. **默认 embedding 是本地 `hash` 回退**（字符 n-gram 哈希，`is_degraded=true`）。
   它能端到端跑通并可回归测试，但语义质量远不如真实模型；状态接口与会话 UI
   都会显示 `embedding_degraded` 提示，不会伪装成真实语义检索。
3. **`vector_min_score` 默认 0.0（不过滤）**：合理的余弦下限依赖具体模型，
   设错会静默丢弃真实证据，因此默认关闭、由用户按模型设置。
4. **中文词法回退使用"字符双字覆盖 ≥ 0.5"** 这一启发式阈值（`CJK_MIN_COVERAGE`）。
   它显著提高了中文召回（keyword recall@10 从 0.857 → 1.000），代价是可能引入
   低相关候选；这部分噪声由向量路径与重排（可选）抑制。
5. **`/rag/search` 的 `rerank` 尚未暴露**（`/rag/query` 已支持按请求开关）；
   搜索端点保持"纯检索"定位。
6. **不做 GraphRAG / Agentic RAG**：③ 只落地了 `LinkRetriever` 这一"图邻居加权扩展"，
   **且默认关闭、在本数据集上实测零增益**（见 §9.5）；真正的 GraphRAG
   （社区摘要、多跳推理生成）与 Agentic RAG（自迭代检索）**仍未实现**。
7. **不做 Obsidian Plugin Runtime**（本阶段明确出界）。
8. **RAG 不提供任何写入能力**：未来若要由 RAG 触发写操作，必须经 M7
   Policy/Diff/Journal/History 通道（当前无此通道）。
9. **未支持附件/图片内容检索**（仅 Markdown 正文）；附件仍按 M9 只做上传/预览。
10. **首次全量索引耗时取决于真实 embedding 服务**：重建会产生真实的模型请求，
   当前实现支持批处理与失败重试（`retry_failed`），但没有在 UI 上展示逐条进度条。
11. **设置页对 HTTP 重排的暴露仍不完整**：`reranker_base_url`、`reranker_model`、
    `reranker_provider` 与探活按钮已在设置页可用，但 `reranker_api_key` 与
    `reranker_timeout_seconds` 仍只能通过 `LOCALNOTE_RAG__*` 配置。
12. **`sqlite-vec` 内核未实现**：本机未安装该扩展，缺少可验证的实现环境；
    当前加速手段是可选 numpy 内核（见第 1 条）。
13. **Link 路默认关闭，且在本数据集上零增益**：`hybrid_link` 的
    recall@5/@10 与 MRR 与纯 `hybrid` 完全相同（1.000/1.000/0.917，MRR +0.000）。
    这是"**在此数据集上无法证明有效**"，不是"link 检索无效"的普遍结论。
    可复现原因见 §9.5.3（可参与图扩展的解析边仅 3 条 = 2 wikilink + 1 embed，
    且候选池 `candidate_k=30` 覆盖 15 篇 / 15 chunk 全库两遍）。
    若要真正评估 link 路的价值，需要**更大、链接更密集**的语料（见 §11 第 7 条）。
14. **`link_rrf_weight` 与 `link_add_only` 不是配置项**：两者都是
    `HybridRetriever` 的构造参数（`DEFAULT_LINK_RRF_WEIGHT=0.5`、
    `link_add_only=True`），**未**暴露到 `RagSettings`/设置 API/前端。因此用户无法
    从 UI 复现 §9.5.4 的 `DIAGNOSTIC` 行；这些行只能用 harness 或代码构造。
    另外 `seed_top_k`、`max_tag_neighbours`、`document_char_limit` 同样只在
    构造函数里可调。
15. **Link 路只有 `LinkRetriever`，没有独立 `GraphRetriever`**：题目 §十五 预留的
    `GraphRetriever` 名字未单独实现，2-hop 图邻居扩展被并入 `LinkRetriever` 的
    `graph` 信号。
16. **未验证的刻度**：Link 路只在 15 篇/14 问的 Golden Dataset 上量化过；没有任何
    100 篇以上语料的 link 路测量，因此本报告**不给任何性能或质量承诺**（引用数字时
    必须连同"离线 hash 嵌入、256 维、同池、n=14"一起引用）。

## 11. 下一阶段建议

1. ~~向量后端可选升级~~ **已完成**：`server/rag/vector/kernels.py` 提供 numpy 加速
   内核 + 纯 Python 回退，`vector_kernel` 在状态接口可见。下一步是实现
   `sqlite-vec` 内核（同一 `VectorStore` 协议），把内存扫描换成磁盘索引。
2. ~~重排端点~~ **已完成**：`OpenAICompatibleReranker`（`POST {base}/rerank`，兼容
   Jina/Cohere/vLLM 两种响应形状）+ 设置探活端点 + Golden Dataset 量化
   （MRR 0.917 → 0.952）。下一步是把 `reranker_base_url/model/api_key` 暴露到
   前端设置页，并接入本地 cross-encoder。
3. ~~Link/Graph 加权检索~~ **已完成（结论：在该数据集上中性，默认关闭）**：
   `server/rag/retrieval/link.py::LinkRetriever` 实现了 wikilink 出边 / backlink 入边 /
   same-tag / 2-hop 图邻居四信号加权，并作为 RRF 第三路接入；开关与 5 个权重已暴露到
   `RagSettings`、`PATCH /api/v1/settings/rag` 与前端设置页。
   **但实测没有增益**（`hybrid_link` 与 `hybrid` 逐位相同，MRR +0.000），因此默认关闭。
   下一步（若仍想推进）见下方第 7 条。
4. **索引进度与可中断恢复**：把 rebuild 的进度（已处理/总数、失败明细）暴露到
   `/rag/index/status` 与设置页，支持中断后继续（当前支持失败重试与增量补齐）。
5. **RAG 评测常态化**：把 `tests/rag/eval` 纳入 CI 作为质量门禁，并扩充语料
   （100+ 篇合成笔记、跨语言/表格/代码块问题），加入 nDCG 与"必须拒绝回答"的
   负样本集，使"不编造"也成为可量化指标。
6. ~~与 M6 打通~~ **已完成（M6 部分）**：`Suggest Related` 复用 chunk 级混合检索作为
   最高优先级候选来源，并保持"先程序化收窄、再调用模型、候选受 allow-list 限制"的契约。
   M7 受控 Agent 复用 `EvidencePack` 仍未开始（它需要新的只读工具与 Policy 边界评审）。
7. **让 Link 路的结论变得可证伪（推荐的下一步，优先级最高）**：当前"中性"结论的
   根本原因是**语料太链接稀疏**（可参与图扩展的解析边仅 3 条、15 篇 = 15 chunk、
   候选池 30 覆盖全库两遍），而不是算法无效。要判断 link 路是否真有价值，必须先换语料：
   构造 **100+ 篇、链接密集（每篇 3–5 条 wikilink）、含 same-tag 簇与多跳链路**的
   Golden Dataset，并把 `candidate_k` 调到**远小于**语料规模（例如 10 篇 vs 100 篇），
   使"图邻居不在直接候选池内"成为常态。**只有在那样的语料上仍测得零增益，
   才能把结论写成"link 路无效"**；在此之前只能写"在本数据集上无法证明有效"。
8. **若要进一步降低回退风险**：为非加性融合实现"图邻居只做**候选扩充**、不参与
   已命中文档的重排"之外的策略（例如仅当直接路候选数不足 `fusion_top_k` 时才注入
   link 候选）。注意：本轮已实测 w=0.1 的非加性融合仍低于 baseline（0.911 < 0.917），
   所以任何放宽 `link_add_only` 的改动都**必须**带 Golden Dataset 的 MRR 对比。
