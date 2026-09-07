# LocalNote Server M6 详细实施计划：只读 AI Chat / Context / 结构化辅助能力

> 阶段：M6；路由：`mf/gpt-5.6-sol`（已确定并生效）。本文件是 M6 开发 Agent 的唯一实施依据。
> 规划阶段只创建本文件；不得创建实现代码，不得修改 `PLAN.md`、`PLAN-M1.md`、`PLAN-M2.md`、`PLAN-M3.md`、`PLAN-M4.md`、`PLAN-M5.md`。
> M6 依赖 M1 Vault、M2 Workspace、M3 Metadata/Links/Search、M4 SQLite/FTS、M5 Graph，以及 M0 已有 AI status/discovery。

## 1. 目标与可验收成功标准

### 1.1 总目标

在不改变 Markdown/Vault 事实源、不引入云 AI、不增加任何 AI 写路径的前提下，把 M0 的 oMLX 只读能力发现扩展为受控的 OpenAI-compatible AI 读取层：模型适配器提供 chat（核心）以及可选 embedding/rerank 能力；所有工作流通过受限 ContextBuilder、版本化 Prompt Registry 和 Pydantic 强结构化输出；前端仅提供最小 AI Sidebar/Panel。

固定原则：`Small Model + Short Prompt + Strong Schema + Programmatic Candidate Reduction + Safe Degradation`。AI 只能读取已经由本地服务筛选的笔记上下文，绝不直接访问文件系统、shell、网络之外的本地 oMLX endpoint，也不写 Vault、SQLite 正文、frontmatter、history 或 graph。

### 1.2 成功标准（全部为 M6 门槛）

1. `AISettings` 支持 `enabled/provider/base_url/chat_model/temperature/max_context_notes`，以及现有 timeout/discovery 字段；环境变量嵌套配置和必要的扁平兼容行为稳定。`enabled=false` 或未配置时不发请求，状态可安全显示。
2. `ModelAdapter`/`ModelDiscoveryClient` 仅在 `server/ai/adapters` 封装 HTTP；OpenAI-compatible `httpx.AsyncClient` 支持 `/v1/models`、`/v1/chat/completions`，可选 `/v1/embeddings` 和 rerank 路径；绝不在 service/route 中直接 httpx 或 FS。
3. 能力检测独立于整体状态：chat 是核心，embedding/rerank 缺失只标记 false，不把 AI 整体降级；模型自动发现时 `chat_model=auto` 选择匹配 Qwen3.5-4B（推荐 4B instruct），用户可通过 Settings 选择已发现模型并在请求中使用。
4. 实现以下六个只读端点且均有 `extra="forbid"`/严格 Pydantic schema：`POST /api/v1/ai/chat`、`summarize`、`tags`、`related`、`extract_todos`、`classify`。任何端点不得产生文件/DB/索引/历史写操作。
5. 所有 workflow 均经 ContextBuilder：限制 note 数、每篇和总字符数，按 heading 截取，优先 query/相关片段，去重并带来源 path/heading；related 先由 FTS/links/graph 程序缩小候选，再交模型排序/解释。
6. Prompt Registry 位于 `server/ai/prompts/*.md`，每个 prompt 含 name/version/input schema/output schema；响应和日志记录 `prompt_version`，为 M7 history 预留但 M6 不写 history。
7. 模型输出先解析 JSON，再经 Pydantic 校验；非法 JSON、schema 错误、超时、连接失败、离线、能力缺失均返回稳定的 400/503 或结构化错误，不把上游异常变成未处理 5xx；不返回秘密、完整 prompt、原始异常或任意路径绝对值。
8. 最小前端经 REST/mock fetch 提供 Ask current note、Summarize、Generate Tags、Suggest Related；加载/空/离线/错误状态清晰，AI 不自动写入标签或正文，复杂 Chat Workspace 留后续。
9. 全部后端新增测试仅使用 `httpx.MockTransport`/fake adapter 和 `tmp_path`/受控 fixture，不触真实 oMLX、不触真实用户 Vault；已有 M1–M5 门禁（路线图记录后端约 347、前端约 74）不回退。
10. 验收包含 schema、非法 JSON、超时、offline、capability detection、上下文上限、去重、heading、候选缩小、参数注入、写隔离和前端 fetch mock；`compileall`、typecheck、Vitest、web build、`scripts/check.sh` 全通过。

## 2. 现状基线与实施前检查

### 2.1 已有基线

- `server/ai/service.py` 的 `AIStatusService` 只做每次请求的 `/models` 探测；`AIStatusResponse`、`AICapabilities`、Qwen 匹配和安全 endpoint 脱敏已存在。
- `server/ai/adapters/omlx_client.py` 是当前唯一 HTTP 位置；用户点名的 `server/ai/{service,adapter,__init__}.py` 中不存在单数 `adapter.py`（实际为 `adapters/` 包），本计划统一扩展现有复数包并新增 `adapters/base.py`，不创建并行的 `server/ai/adapter/` 目录；adapter 使用 `httpx`，已有 timeout、响应大小、MockTransport 测试契约。
- `server/config.py` 的 `AISettings` 当前有 `base_url`、请求/连接 timeout、模型列表大小和 Qwen pattern；需 additive 扩展，不破坏 M0 状态。
- `server/api/routes/ai.py` 目前仅有 `GET /api/v1/ai/status`；`main.py` 已注册 ai router，异常处理沿用安全错误体。
- M1 的 `VaultService` 是唯一 FS 入口；M3/M4 的 `DerivedIndexService` 提供安全的 `entry/entries`、FTS `substring_search`/`fts_search`、tags/links/backlinks；M5 提供只读 graph snapshot/related 的候选数据。
- 前端已有 API client/types、Workspace store、Ribbon 和 Graph/Search UI；AI 当前仍为状态行/disabled，占位能力可增量接入。
- `pyproject.toml` 已有 `httpx`、FastAPI、Pydantic，无需引入 OpenAI SDK；优先复用 httpx，避免依赖膨胀。

### 2.2 实施前必须执行

开发 Agent 先读取本计划和指定基线文件，再从仓库根执行并记录：

```bash
pwd
python3 --version
python -m pytest -q
pnpm install --frozen-lockfile
pnpm --filter @localnote/web test -- --run
pnpm --filter @localnote/protocol typecheck
pnpm --filter @localnote/workspace typecheck
pnpm --filter @localnote/web typecheck
pnpm --filter @localnote/web build
python -m compileall server
./scripts/check.sh
```

基线失败必须先记录，不得归因于 M6；禁止顺手修改既有计划文件或放宽旧测试。

## 3. 范围、不做项与实施边界

### 3.1 包含

- AISettings 扩展、OpenAI-compatible adapter、能力探测/模型选择。
- ContextBuilder、Prompt Registry/解析和六类**必须实现**的只读 workflow：chat、summarize、tags、related、extract_todos、classify；六者都必须有 request/response schema、REST 路由、成功与降级测试，不得以 optional/501 替代。
- Pydantic API schema、稳定错误枚举、FastAPI routes/DI。
- related 的程序候选缩小：FTS/links/graph Top N 后再交 chat/rerank；不得把全 Vault 正文发给模型。
- protocol TS DTO、client 和最小 AI Panel。
- MockTransport/fake 测试、文档和 roadmap M6 状态更新（仅开发阶段；本规划阶段不改文档）。

### 3.2 明确不做

- 任何 AI 写操作：不写 Markdown、frontmatter、tags、links、graph、SQLite、history、recovery；写操作属于 M7。
- 不实现 Agent loop、工具调用、shell/命令执行、文件操作、自动保存、Policy/Diff/Undo/审批。
- 不直接访问 FS；AI 只能通过 `VaultService`/`DerivedIndexService` 获得受限文本。
- 不触真实用户 Vault、真实 oMLX、云 AI、远程模型、API key/token 管理或 UI 密钥输入。
- 不做复杂 Chat Workspace、流式 WebSocket、持久化会话、向量数据库；embedding/rerank 若无 endpoint 只保持 optional unavailable。

## 4. 有序任务清单

| 编号 | 任务 | 依赖 | 产出 | 完成定义 | 预计文件 |
|---|---|---|---|---|---|
| M6-01 | 基线与契约勘验 | M0–M5 | 实测测试数、adapter/index/graph API 清单 | 基线命令有退出码；保护计划文件无 diff | `server/ai/*`, `server/index/service.py`, `packages/*` |
| M6-02 | 配置与错误枚举 | 01 | AISettings 字段、AI error code、统一错误体 | enabled/provider/model/limits 可解析；错误不泄漏 | `server/config.py`, `server/ai/errors.py`, `tests/backend/test_ai_config.py` |
| M6-03 | Adapter 抽象 | 02 | ModelAdapter Protocol、请求/响应内部 DTO、异常映射 | HTTP 仅在 adapter；timeout/status/invalid JSON 可测 | `server/ai/adapters/base.py`, `openai_compatible.py`, `__init__.py` |
| M6-04 | 能力与模型选择 | 03 | `/models` capability probing、auto/explicit model resolver | chat 核心独立；embedding/rerank 缺失不影响 chat；无模型 503 | `server/ai/capabilities.py`, `service.py`, tests |
| M6-05 | ContextBuilder | 01、M4/M5 | 限额、heading/片段优先、去重、来源 DTO | 所有 workflow 只接收 ContextBundle；超限可预测且无绝对路径 | `server/ai/context.py`, `tests/backend/test_ai_context.py` |
| M6-06 | Prompt Registry | 02、05 | Markdown prompts + parser/registry + versions | 每个 prompt 元数据和 schema 可验证；未知版本安全失败 | `server/ai/prompts/*.md`, `registry.py`, tests |
| M6-07 | 结构化 workflow schemas | 03–06 | chat/summarize/tags/related/extract/classify input/output | `extra=forbid`、长度/数量上限、JSON schema 可导出 | `server/ai/schemas.py`, `workflows.py`, tests |
| M6-08 | AI service 与 DI | 03–07 | `AIWorkflowService`，单一编排入口 | status 保持；service 无 FS/http 旁路；注入可替换 adapter | `server/ai/service.py`, `server/api/dependencies.py` |
| M6-09 | REST routes/error mapping | 07–08 | 六个 POST 端点 | 成功/400/503 契约固定；无写操作；OpenAPI schema 正确 | `server/api/routes/ai.py`, `server/api/main.py`, tests |
| M6-10 | related 候选程序缩小 | 05、M4/M5 | FTS/links/graph candidate provider + Top N | 先程序候选，模型不得看到候选外笔记；排序/去重稳定 | `server/ai/candidates.py`, `server/index/service.py`（仅复用现有 API） |
| M6-11 | Protocol/client | 09 | TS DTO、ApiError、6 client methods | URL/query/body 编码稳定，fetch 全 mock | `packages/protocol/src/index.ts`, `apps/web/src/api/{types,client}.ts` |
| M6-12 | 最小 AI Panel | 11 | Ask/Summarize/Tags/Related actions | 当前 note 传 path/context；只展示建议；loading/error/offline/accessibility | `apps/web/src/components/AIPanel.tsx`, `Ribbon.tsx`, `App.tsx`, `styles.css`, workspace files |
| M6-13 | 测试/安全隔离 | 02–12 | 全矩阵与写路径审计 | MockTransport；禁止真实请求/FS；旧门禁全绿 | `tests/backend/test_ai_*.py`, `tests/frontend/*AI*.test.*` |
| M6-14 | 文档/最终门禁/M7 入口 | 全部 | README/ai architecture/roadmap 更新、交付报告 | 命令全 0；偏差、未决事项、M7 入口完整 | `README.md`, `docs/ai-architecture.md`, `docs/development-roadmap.md`, `server/ai/README.md` |

## 5. 技术方案与关键设计决策

### 5.1 ModelAdapter 抽象与 OpenAI-compatible 客户端

定义面向能力的 Protocol（名称可等价但语义不得变）：

```python
class ModelAdapter(Protocol):
    async def list_models(self) -> list[DiscoveredModel]: ...
    async def chat(self, *, model: str, messages: list[ChatMessage],
                   temperature: float, response_schema: dict[str, object] | None,
                   timeout_seconds: float) -> ChatResult: ...
    async def embed(self, *, model: str, inputs: list[str],
                    timeout_seconds: float) -> EmbeddingResult: ...
    async def rerank(self, *, model: str, query: str, documents: list[str],
                     timeout_seconds: float) -> RerankResult: ...
```

`embed`/`rerank` 可以由 adapter 抛 `CapabilityUnavailable`；不得偷偷用 chat 代替。M6 核心是 chat；实现 rerank endpoint 需遵循 provider 约定，若 OpenAI-compatible 服务没有标准 rerank，保持 optional false 并让 related 使用程序候选排序。`embed` 同理，不建立向量库。

使用 `httpx.AsyncClient`（可注入 `MockTransport`），base URL 规范化到 `/v1`，请求仅发给本地配置 endpoint；Bearer/API key 不由 M6 配置、不从用户 UI 索取。默认请求 JSON：`model`, `messages`, `temperature`, `max_tokens`（可配置安全上限），chat structured output 优先发送 `response_format: {type:"json_schema", json_schema:{name,schema,strict:true}}`；上游不支持时可发送普通 JSON 指令，但仍必须本地解析和 Pydantic 校验，不能把“不支持 schema”当成功保障。

适配器异常统一为 `AIAdapterError(kind=offline|timeout|http_error|invalid_response|capability_unavailable|model_not_found)`；不携带 response body、headers、token 或完整异常到客户端。总 timeout、connect timeout 和 response bytes 上限均受配置限制；默认不重试，避免小模型和本地服务压力放大。

### 5.2 配置与模型选择

建议：

```python
class AISettings(BaseModel):
    enabled: bool = True
    provider: Literal["omlx"] = "omlx"
    base_url: str | None = "http://127.0.0.1:8000/v1"
    chat_model: str = "auto"             # auto 或 /models 中精确 id
    temperature: float = Field(0.1, ge=0, le=1)
    max_context_notes: int = Field(8, ge=1, le=50)
    max_context_chars_per_note: int = Field(12000, ge=500, le=100000)
    max_context_chars_total: int = Field(60000, ge=1000, le=300000)
    request_timeout_seconds: float = Field(20.0, gt=0, le=120)
    connect_timeout_seconds: float = Field(0.5, gt=0, le=10)
    max_output_tokens: int = Field(1200, ge=64, le=8192)
```

保留现有 `max_models_response_bytes`、`qwen_match_pattern`；如 `enabled=false` 或 base URL 空，状态为 `not_configured`/workflow 返回 `ai_disabled`（建议 503）。`chat_model=auto` 依次选择匹配 Qwen3.5-4B 且 chat=true 的模型，再选择显式 chat 模型；明确模型不存在返回 400 `model_not_found`，不自动换模型。Settings UI 只能选择 status 返回的模型 id，不可输入任意远程 URL；模型选择是会话/设置配置，不写 Vault。

### 5.3 ContextBuilder

Context source 只能是既有 service：当前 note 由 `VaultService.read_bytes` 经依赖注入读取并 UTF-8 解码；metadata/links/FTS/graph 候选来自相应只读服务。`ContextBuilder.build(request)` 生成：

```python
class ContextSource(BaseModel):
    path: RelativeNotePath
    title: str
    heading: str | None
    text: str
    relevance: float | None
    truncated: bool
class ContextBundle(BaseModel):
    notes: list[ContextSource]
    total_chars: int
    omitted_notes: int
    context_version: Literal["m6-v1"] = "m6-v1"
```

规则：先去重 path；当前 note 优先；related 候选按 FTS score/Graph 邻接/links relevance 合并后稳定排序；按 heading 找到相关标题下的子树，截取时保留 heading；无命中取开头/尾部有限窗口；每篇和总字符数硬上限；超限按 relevance、当前 note、path 稳定淘汰；不得把 base64/二进制、`.localnote` 或绝对 root 送模型。上下文包在日志只记录 count/长度/hash，不记录正文。

所有 workflow 必须调用同一 builder，route 不得自行拼 prompt。chat 也必须有 `max_context_notes` 限制；空上下文应可回答“未找到上下文”而不盲猜。

### 5.4 Prompt Registry 与规范

每个文件采用固定 frontmatter + Markdown：

```markdown
---
name: summarize_note
version: m6.1
input_schema: SummarizeRequest
output_schema: SummarizeResponse
---

You summarize only the supplied note context. Do not invent facts.
Return JSON matching the output schema, with concise bullets.
```

至少创建：`chat.md`, `summarize_note.md`, `generate_tags.md`, `suggest_links.md`（related 的 prompt）、`extract_todos.md`, `classify_note.md`。Registry 启动/测试时检查 name/version 唯一、input/output schema 可解析；prompt_version 进入每个成功响应和结构化错误的 metadata，未来可直接写 M7 history。M6 不保存 prompt/history，不允许用户任意提交 system prompt。

### 5.5 强结构化输出与降级

所有 output model `extra="forbid"`，字符串/列表/条数/字符长度有限制，path 只能是已提供的相对路径。推荐 DTO：

- `ChatRequest(note_path?, question, context_note_paths?)` → `ChatResponse(answer, citations[], prompt_version, model, degraded=false)`。
- `SummarizeRequest(note_path)` → `SummarizeResponse(note_path, summary, key_points[], prompt_version, model)`。
- `TagsRequest(note_path)` → `TagsResponse(note_path, tags[{name, reason}], prompt_version, model)`；仅建议，不写回。
- `RelatedRequest(note_path, limit=5)` → `RelatedResponse(note_path, related[{path,title,reason,score}], candidates_considered, prompt_version, model)`。
- `ExtractTodosRequest(note_path)` → `ExtractTodosResponse(items[{text,source_heading?,due_hint?}], ...)`。
- `ClassifyRequest(note_path, labels?)` → `ClassifyResponse(label, confidence, alternatives[], ...)`。

`SuggestLinksResponse` 可作为 related 内部/外部命名，公共路径固定 `/related`。服务流程：上游文本 → JSON decode（去除可安全识别的 markdown code fence，但不做宽松 eval）→ Pydantic validate → semantic validation（引用 path 必须在 context/candidate 集）→ response。非法 JSON/schema 返回 502 `ai_invalid_output`（若产品严格要求不暴露上游问题，也可统一 503，但状态码必须固定并测试）；请求字段非法返回 400；未配置/offline/timeout/model unavailable 返回 503 `ai_unavailable`/`ai_timeout`；能力缺失返回 503 `ai_capability_unavailable`。关键功能不返回 500。所有失败都带 `{error:{code,message,path:null}, meta:{prompt_version,model?}}` 的安全形状。

### 5.6 Related 候选缩小

程序先对 note path 做安全验证并取得当前正文/标题。候选来源优先：M4 `fts_search`（query 使用标题/关键词，固定上限）、现有 links/backlinks、M5 graph local snapshot；按 path 去重，排除当前 note、外链、broken、超大/不可读项，最多 `max_context_notes - 1` 或 `related_candidate_limit`（默认 20）。只有这些候选的受限片段送给 chat；模型只能返回候选 path，服务拒绝未在候选集合中的幻觉 path。若 rerank 能力存在，可用 `rerank` 对这 20 个候选排序；没有 rerank 则程序 score 排序后由 chat 生成原因，不把 embedding 缺失视为整体不可用。候选为 0 时返回结构化空结果，不调用模型或返回可解释 `no_candidates`。

### 5.7 生命周期、DI 与隔离

`AIStatusService` 保持 per-request discovery 语义；workflow service 通过 `get_ai_workflow_service` 构造 settings + adapter，并允许测试 override。可选择在一次 workflow 请求内复用同一 models discovery 结果，但不得引入后台缓存/线程。FastAPI route 只负责输入验证、调用服务、错误映射；AI route 不触 `VaultService` 之外的 FS。Index unavailable 对 related 应返回 503 或在候选为空时安全 `degraded`; 建议 related 仅在 index ready 时执行并返回 `index_unavailable`，其他当前 note workflows 不依赖 index。

## 6. 公共 API、DTO、错误与数据流

### 6.1 REST

```http
POST /api/v1/ai/chat
POST /api/v1/ai/summarize
POST /api/v1/ai/tags
POST /api/v1/ai/related
POST /api/v1/ai/extract_todos
POST /api/v1/ai/classify
```

请求 body 禁止 arbitrary system prompt、URL、shell、操作名；`note_path` 是 M1 同样的 root-relative POSIX path。所有端点只读。

示例：

```bash
curl -fsS -X POST http://127.0.0.1:3780/api/v1/ai/chat \
  -H 'content-type: application/json' \
  -d '{"note_path":"notes/会议.md","question":"这篇笔记的三个结论是什么？"}'

curl -fsS -X POST http://127.0.0.1:3780/api/v1/ai/related \
  -H 'content-type: application/json' \
  -d '{"note_path":"notes/会议.md","limit":5}'
```

成功响应示例：

```json
{
  "answer":"……",
  "citations":[{"path":"notes/会议.md","heading":"结论","quote":"……"}],
  "model":"Qwen3.5-4B-Instruct-4bit",
  "prompt_version":"chat@m6.1",
  "degraded":false
}
```

Related：

```json
{"note_path":"notes/会议.md","related":[{"path":"notes/项目.md","title":"项目","reason":"共享标签与链接","score":0.82}],"candidates_considered":7,"model":"Qwen3.5-4B-Instruct-4bit","prompt_version":"suggest_links@m6.1"}
```

### 6.2 错误枚举

`ai_disabled`, `ai_not_configured`, `ai_unavailable`, `ai_timeout`, `ai_model_not_found`, `ai_capability_unavailable`, `ai_invalid_output`, `ai_invalid_request`, `ai_context_too_large`, `not_found`, `index_unavailable`, `internal_error`。建议映射：400 输入/模型/上下文请求错误；404 note 不存在；502 非法上游输出（或固定 503，开发报告需说明）；503 离线/超时/能力缺失/未配置/index 不可用；500 仅防御性未知错误且不泄漏细节。`AIStatus` 的三种 HTTP 200 行为保持不变。

### 6.3 数据流

```text
POST request (note_path/question)
  → FastAPI/Pydantic validation
  → VaultService / IndexService 读取并程序筛选
  → ContextBuilder（heading、relevance、dedupe、limits）
  → Prompt Registry（name/version + schema）
  → OpenAI-compatible adapter
  → Qwen3.5-4B（推荐本地 4B）
  → JSON decode + Pydantic + semantic validation
  → Response DTO（model/prompt_version/citations）
```

绝不 `AI → write`；M7 才允许 `AI output → Policy → Diff → History → 用户确认 → Vault write`。

## 7. 目录树与文件用途

```text
server/ai/
  __init__.py                 # exports，不改变 M0 import
  service.py                  # 保留 AIStatusService；协调 workflow service
  schemas.py                  # status + M6 request/response DTO
  errors.py                   # AI domain errors/code
  capabilities.py             # model capability normalization/resolution
  context.py                  # ContextBuilder/ContextBundle
  candidates.py               # related 的 FTS/links/graph candidate reduction
  registry.py                 # prompt frontmatter/schema registry
  workflows.py                # chat/summarize/tags/related/extract/classify orchestration
  adapters/
    __init__.py
    base.py                   # ModelAdapter Protocol、内部结果 DTO
    omlx_client.py            # 既有 discovery adapter，保持兼容
    openai_compatible.py      # chat/embed/rerank HTTP 实现（httpx only）
  prompts/
    __init__.py
    chat.md
    summarize_note.md
    generate_tags.md
    suggest_links.md
    extract_todos.md
    classify_note.md
  profiles/                   # 仍非执行型占位；不引入 Agent profile
server/api/
  dependencies.py             # get_ai_workflow_service
  routes/ai.py                # status + 六个只读 POST
  main.py                     # 复用 AI error handler/安全异常映射
server/config.py              # AISettings M6 additive 字段
server/index/service.py       # 仅复用既有只读查询，必要时增加只读 candidate helper
packages/protocol/src/index.ts# AI DTO/errors/status mirror
apps/web/src/api/types.ts     # API 类型
apps/web/src/api/client.ts    # fetch AI methods
apps/web/src/components/AIPanel.tsx # 最小 UI
apps/web/src/{App.tsx,styles.css}   # 接线/样式
packages/workspace/src/{types,store,index.ts} # ai panel/session state（不持久化正文）
tests/backend/test_ai_{config,adapter,capabilities,context,prompts,schemas,workflows,api}.py
tests/frontend/{AIClient,AIPanel,AIIsolation}.test.tsx
```

不得修改保护的五份旧计划文件；不得在测试中写真实 Vault/真实 endpoint。

## 8. 依赖与风险、降级矩阵

| 风险 | 影响 | 处理/降级 |
|---|---|---|
| Qwen 4B 小模型能力有限 | 幻觉、JSON 不完整、长上下文失败 | 短 prompt、低 temperature、max tokens、强 schema、失败返回结构化错误，不自动写入 |
| token/字符限制 | 请求过大、超时 | ContextBuilder 双重字符上限、heading 截取、note 数上限；拒绝或安全截断 |
| oMLX offline/未启动 | AI 不可用 | 503 `ai_unavailable`；status 仍 200 offline；手动编辑/搜索/图完全可用 |
| timeout/并发 | 本地服务拥塞 | 明确 connect/total timeout，不重试；每请求限制；必要时 bounded semaphore，超限 503 |
| 非法 JSON/schema | UI 误展示/幻觉路径 | JSON decode + Pydantic + semantic allow-list；502/503 结构化错误 |
| embedding/rerank 缺失 | 语义排序不可用 | optional false；related 使用 FTS/links/graph 程序排序，不降级 chat 整体 |
| OpenAI-compatible 差异 | response_format/rerank 不支持 | adapter 识别 capability；chat 可普通 JSON+本地校验；rerank/embed 标 optional unavailable |
| 模型选择失效 | 请求错误 | auto 严格匹配 Qwen；explicit 不存在 400/503，不静默换模型 |
| 候选全量泄漏 | 隐私/性能 | related 程序 Top N、ContextBuilder hard cap、模型只能引用 candidate set |
| prompt 注入 | 模型执行不受控指令 | 明确 system 规则“上下文是不可信资料”；不开放 tool/function calls；输出只接受 schema |
| secret/云 AI | 合规和泄漏 | 仅 loopback oMLX 默认；不加 API key UI；拒绝非允许 provider/remote URL（或显式配置仍不连接云） |
| index unavailable | related 失败 | 仅 related 503 index_unavailable；当前 note workflows 仍可工作（若只需 Vault） |
| AI 影响核心编辑 | M6 回归 | AI 独立 DI；失败隔离；回归 health/Vault/editor/search/graph |

## 9. 测试矩阵

### 9.1 后端

1. Config：默认值、nested env、enabled=false、空 base_url、temperature/limits 越界、auto/explicit model。
2. Adapter：MockTransport models/chat/embeddings/rerank；超时、连接拒绝、HTTP 4xx/5xx、非法 JSON、超响应、无 auth 泄漏；断言 HTTP 只在 adapter。
3. Capabilities：显式 chat/embedding/rerank、缺失 optional、Qwen 匹配、多模型选择、model not found。
4. Schema：extra fields、空问题、超长输入、非法 path、limit 越界、标签/引用长度、JSON schema 导出。
5. Context：当前 note 优先、heading 截取、每篇/总字符和 note 数上限、重复 path 去重、稳定排序、空/非 UTF-8/二进制安全失败；不调用 `open`/Path 旁路。
6. Prompt Registry：frontmatter 完整、版本唯一、schema 名称匹配、prompt_version 进入响应；缺 prompt/坏 metadata 安全失败。
7. Workflow：每个 workflow 只调用 ContextBuilder + adapter；温度/模型/response schema 正确；语义引用只能来自上下文。
8. Related：FTS/links/graph 候选先缩小、排除当前/broken/web、候选为空不调用模型、模型越界 path 被拒绝、rerank optional fallback。
9. Degrade：offline/not configured/timeout/invalid output/capability unavailable 的固定 status/code；不产生 5xx（除明确防御性内部错误）；AI 故障不影响 health/Vault/index/graph/editor。
10. Read-only：mock Vault/index 写方法为 fail-fast，调用六端点后断言无 write/rename/SQLite mutation/history。
11. API：TestClient 覆盖六 POST、OpenAPI schema、400/404/502/503、路径/正文/异常/秘密不泄漏；真实网络一律禁用。

### 9.2 前端

- client 请求 body/path、ApiError code/status/meta、全 fetch mock。
- AIPanel 四个最小 action：current note 参数、loading、success、empty、offline/503、invalid output。
- Tags/related 只显示建议，不触发 Vault PATCH；点击 related 仅调用既有 openFile。
- Ribbon AI enable 不影响 Files/Search/Graph；无 note 时按钮 disabled/说明。
- accessibility：button label、错误文本、键盘焦点；无 `dangerouslySetInnerHTML`。
- workspace requestVersion/取消或旧响应保护（如采用）；不持久化 prompt/正文。

## 10. 验收命令与受控 smoke

```bash
uv sync --dev
pnpm install --frozen-lockfile
python -m pytest -q
pnpm --filter @localnote/protocol typecheck
pnpm --filter @localnote/workspace typecheck
pnpm --filter @localnote/web typecheck
pnpm --filter @localnote/web test -- --run
pnpm --filter @localnote/web build
python -m compileall server
./scripts/check.sh
```

可选真实 API smoke 只允许应用指向 `tmp_path`/`tests/fixtures/vault`，AI 请求必须 MockTransport；不得启动或触真实 oMLX。可用测试示意：

```bash
LOCALNOTE_VAULT__ROOT="$PWD/tests/fixtures/vault" \
LOCALNOTE_AI__BASE_URL="http://127.0.0.1:8000/v1" \
python -m pytest -q tests/backend/test_ai_api.py
```

验收记录需包括实际后端/前端数量（路线图当前预期约 347/74 回归基线）、每条命令退出码、MockTransport 证明、无真实网络证明、保护文件 diff：

```bash
git diff -- PLAN.md PLAN-M1.md PLAN-M2.md PLAN-M3.md PLAN-M4.md PLAN-M5.md
```

## 11. 明确假设、未决事项与 M7 入口

### 11.1 假设

1. Python 3.12、FastAPI/Pydantic v2、httpx 和现有 M1–M5 API 保持兼容；不引入 OpenAI SDK，复用 httpx。
2. 默认运行是单用户 loopback、本地 oMLX；M6 不设计身份鉴权或云 provider。
3. `VaultService` 是唯一 FS 入口；M4/M5 只读接口足以支撑程序候选；若实际 helper 名不同，做最小 adapter，不改既有 DTO。
4. Qwen3.5-4B 是推荐默认目标，但完整 id 仍以 `/models` 自动发现，不硬编码。
5. structured output 能力并非所有兼容服务均支持；本地校验是最终权威，普通 JSON fallback 仍不能放宽 schema。
6. AI 响应不会被当成事实源；用户必须自行确认，M6 不自动应用建议。

### 11.2 未决事项（开发 Agent 必须在报告明确取舍）

- embedding/rerank 本阶段是否实现真实 endpoint：推荐实现 adapter Protocol 和能力探测，embed/rerank 先 optional；若无稳定 oMLX 契约，仅返回 capability unavailable，不伪造。
- chat 是否含 RAG：推荐 M6 仅做受限本地 context/RAG-lite，不做向量库、后台 embedding 或全库语义检索；related 先 FTS/links/graph。
- `prompt_version` 是否进入 history：M6 仅放 response/meta，为 M7 history schema 预留；不写 history。
- `AISettings.enabled` 默认 true 还是 false：推荐 true 但空 base_url/offline 不发请求或安全降级；产品可选 false，必须保持现有 status 兼容。
- OpenAI response_format 不支持时是否普通 JSON fallback：推荐允许 fallback + 严格本地校验；不得接受自由文本。
- `ai_invalid_output` 采用 502 还是 503：二者择一固定，建议 502 表示上游协议错误，并在 TS ApiError 中保留。
- AI Panel 是否做流式回答：不做；M7/后续 Chat Workspace 再评估。
- 模型 Settings 是一次请求覆盖还是应用级配置：推荐应用级 `AISettings` + 仅允许已发现模型的 session choice；任何持久化写入属于后续设置系统，不写 Vault。

### 11.3 M7 入口

M7 在 M6 审计通过后，才可把 M6 的结构化建议接入 `Policy → Diff → History → Recovery → explicit user approval → Vault write`。M7 必须重新定义写 DTO、风险等级、可审计事件、撤销/恢复和预算；不得把 M6 `TagsResponse`、`ExtractTodosResponse` 或 `ClassifyResponse` 直接当写命令。`prompt_version`、model、context hash、candidate set 可作为 M7 history 事件字段，但 M6 不创建 history 表或写入。

## 12. M6 不做哪些（最终范围审计）

- 不写任何 Markdown、frontmatter、tags、links、graph、SQLite 正文或用户设置文件。
- 不生成 shell、Python、文件操作、网络代理或任意工具调用。
- 不使用真实用户 Vault/真实 oMLX/云 AI；测试只用 fixture/tmp/MockTransport。
- 不实现 Agent loop、Policy、Diff、Undo、History、Recovery、Scheduler、WebSocket、流式 Chat Workspace。
- 不把 embedding/rerank 缺失伪装为已支持，不把 AI offline 变成核心 API 崩溃。
- 不修改五份旧计划文件；不重新设计整体 LocalNote 架构；只做 additive M6 AI 层。

## 13. 开发与审计交付格式

开发 Agent 必须返回：

1. M6-01–M6-14 完成/未完成表、实际文件与每个偏差；
2. 最终 AISettings、ModelAdapter、能力检测和模型选择行为；
3. 六个 endpoint 的真实 Pydantic/TS DTO、错误码、curl/JSON 示例；
4. ContextBuilder 限额、heading、去重、候选缩小证据；
5. Prompt Registry 文件、name/version/input/output schema 和 prompt_version 传播；
6. adapter timeout/offline/invalid output 以及 embed/rerank optional 证据；
7. 只读隔离证据（无 Vault/SQLite/history 写操作）；
8. 后端/前端测试数量、MockTransport 说明、所有命令退出码；
9. 安全/隐私审计、4B 风险、并发和 token 降级；
10. 未决事项、M7 入口和保护文件未修改证明。

审计 Agent 独立检查：需求符合性、计划执行、Python/TS schema 一致、adapter 边界、ContextBuilder 限额、prompt 版本、related 候选缩小、错误降级、秘密/路径安全、只读隔离、测试 mock、M1–M5 回归和文档。

## 14. 计划完成定义

本文件已以 UTF-8 写入 `/Users/ningyedong/Documents/LocalBook/PLAN-M6.md`。开发阶段必须先读取本文件并逐项执行；规划阶段不创建实现代码、不修改 `PLAN.md` 或 `PLAN-M1.md`–`PLAN-M5.md`。
