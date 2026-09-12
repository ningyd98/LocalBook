# LocalNote AI 架构（M6 只读实现）

> 本文档描述 oMLX/OpenAI-compatible 假设、Qwen3.5-4B 匹配策略、status
> schema、能力模型、超时/错误与安全，M6 只读 workflow 的实现边界，以及
> M7 已实现的受控 Agent planner 边界。M6 已实现六个只读 workflow；M7 的
> Daily Organizer / Weekly Review 复用同一 adapter 契约：**最多一次**
> `chat` 调用 + 一次 ActionSet（严格 JSON），模型从不直接调用工具或写
> 文件；embedding/rerank 仍为可选能力，缺失时明确
> `capability_unavailable`，从不伪装。

## 1. 假设与默认

- oMLX 暴露 OpenAI-compatible HTTP，默认 `http://127.0.0.1:8000/v1`（配置
  `LOCALNOTE_AI__BASE_URL`）。
- OpenAI models 响应至少含 `data` 数组与字符串 `id`；额外 capability 字段
  不保证存在。
- `base_url` 为空/None ⇒ `not_configured`，**不发任何请求**；默认 URL 表示
  尝试本地 oMLX，不可达 ⇒ `offline`。
- oMLX 未运行、模型未加载都不阻塞 FastAPI 启动与 health。

## 2. 状态枚举与 HTTP 语义

| status | 含义 | 触发 |
|---|---|---|
| `not_configured` | 端点显式未配置 | `base_url` None/空 |
| `offline` | 已配置但探测失败 | 连接拒绝/DNS、超时、HTTP ≥400、JSON/schema 非法、响应超限 |
| `connected` | 可达且响应合法 | 至少得到合法模型列表 |

`connected` 不要求 Qwen 已加载：可达但没有匹配模型时返回 `connected` +
`qwen_model: null` + `error_code: "no_matching_model"` + 诊断 message，UI
显示 `Qwen3.5-4B / Not detected`。所有三种状态 HTTP 均为 200 —— “AI 离线”
不是 API 崩溃，前端不应误判。

## 3. 响应 Schema（权威源：server/ai/schemas.py）

```python
class AIStatus(str, Enum):
    NOT_CONFIGURED = "not_configured"; OFFLINE = "offline"; CONNECTED = "connected"

class AICapabilities(BaseModel):
    chat: bool = False; embedding: bool = False; rerank: bool = False

class DiscoveredModel(BaseModel):
    id: str; owned_by: str | None = None
    capabilities: AICapabilities = Field(default_factory=AICapabilities)

class AIStatusResponse(BaseModel):
    status: AIStatus
    provider: Literal["omlx"] = "omlx"
    endpoint: str | None = None          # 规范化地址，无凭据/query
    qwen_model: str | None = None
    models: list[DiscoveredModel] = []
    capabilities: AICapabilities = ...
    error_code: Literal["not_configured","connection_refused","timeout",
                        "http_error","invalid_response","no_matching_model","unknown"] | None
    message: str | None = None
    checked_at: datetime | None = None
```

TypeScript 镜像：`packages/protocol/src/index.ts`（types only），前端经
`apps/web/src/api/types.ts` 复用。

示例（connected）：

```json
{
  "status": "connected",
  "provider": "omlx",
  "endpoint": "http://127.0.0.1:8000/v1",
  "qwen_model": "Qwen3.5-4B-Instruct-4bit",
  "models": [{"id": "Qwen3.5-4B-Instruct-4bit", "owned_by": "omlx",
              "capabilities": {"chat": true, "embedding": false, "rerank": false}}],
  "capabilities": {"chat": true, "embedding": false, "rerank": false},
  "error_code": null,
  "message": null
}
```

## 4. 能力模型

- `chat` 是核心：发现目标 Qwen ⇒ `chat=true`；模型带显式能力元数据则按安全
  白名单读取；无法确认时不虚构（`chat=false` + 诊断）。
- `embedding`/`rerank` 是 optional：只从元数据白名单读取布尔值；缺失或非法
  只标 false，**不把状态打成 offline**。
- 聚合：`chat = 发现 Qwen 或任一模型显式 chat`；`embedding/rerank = 任一
  模型显式标记`。

## 5. Qwen3.5-4B 匹配

纯函数（`server/ai/matching.py`）：

1. `casefold()`（Unicode 安全）；
2. 去掉连续分隔符（空白、`_`、`.`、`-`）；
3. 在折叠后的 ID 上应用可配置正则（默认 `qwen3\.?5[-_ ]?4b`，环境变量
   `LOCALNOTE_AI__QWEN_MATCH_PATTERN`）。

接受：`Qwen3.5-4B-Instruct-4bit`、`qwen-3.5-4b`、`qwen35-4b`（兼容形式）、
量化/供应商后缀。拒绝：`qwen`、`qwen2.5-4b`、`qwen3.5-8b`、`llama` 等近似
名称。**不硬编码任何完整模型 ID**。匹配规则有参数化纯函数测试。

## 6. 超时、错误与安全（Phase 0 行为）

- httpx 显式超时：connect 0.5s、总请求 2s（可配置）；不重试（status 必须
  快）。
- 响应体上限（默认 1 MB）流式读取，超限 ⇒ `invalid_response`。
- 只解析 JSON object + `data` 列表中合法字符串 `id`；`owned_by` 类型错误 →
  None；capabilities 白名单（chat/embedding/rerank）仅接受 bool。
- 错误分类：ConnectError→`connection_refused`；Timeout→`timeout`；HTTP
  ≥400→`http_error`；JSON/schema 非法→`invalid_response`；其余→`unknown`。
- 安全：
  - 响应/日志**不返回** Authorization、Cookie、环境变量、完整异常堆栈、
    响应体内容；日志只记 endpoint host、错误分类与耗时；
  - 回显 `endpoint` 前剥离 userinfo 与 query；
  - message 为固定文案，不含上游细节。
- 每次请求即时探测；无缓存、无后台 watcher（M1+ 再引入）。

## 7. HTTP 抽象与测试

- 唯一 HTTP 位置：`server/ai/adapters/` —— `omlx_client.py` 负责 oMLX 模型
  发现（`OMLXModelDiscoveryClient` + `ModelDiscoveryClient` Protocol），
  `openai_compatible.py` 负责 workflow 的 chat 请求
  （`OpenAICompatibleAdapter`）。
- `httpx.AsyncClient` 只存在于 `server/ai/adapters/`；service 通过构造注入
  client / transport。
- 测试一律用 `httpx.MockTransport` 或 fake client 覆盖
  not_configured/connection_refused/timeout/http_error/invalid_response/
  connected + discovery/optional capability/endpoint 脱敏 —— **严禁真实
  oMLX 网络调用**。

## 8. M6 实现边界（已实现）

M6 已实现六个只读 REST workflow（`chat`、`summarize`、`tags`、`related`、
`extract_todos`、`classify`），统一编排在 `server/ai/workflows.py`：

1. **程序先行**：`related` 先用当前笔记标题/关键词构造查询，经
   `server/ai/candidates.py` 走 FTS5 主路径（或中文等不可 FTS 表示时的
   substring 降级）并合并 links/graph 邻居，去重、排除当前笔记/broken/web、
   按上限截断——模型永远看不到候选集合之外的笔记。
2. **受限上下文**：所有正文经 ContextBuilder 限制 note 数、heading 片段、
   单篇/总字符数，重复与不安全 path（绝对路径/`..`/`.localnote`/反斜杠）
   被剔除；非 UTF-8 笔记 → 400，缺失 → 404。
3. **Prompt Registry**：`server/ai/prompts/*.md` 的真实正文与
   `prompt_version`（如 `chat@m6.1`）进入系统提示并回显到每个响应/meta；
   无硬编码 prompt 文案。input/output schema 名称须解析为实际 Pydantic
   模型，name/version 唯一。
4. **强结构化输出**：模型收到的 JSON schema 已剥离服务端属主字段
   （`prompt_version`/`model`/`note_path`/`candidates_considered`/
   `degraded`），输出仍必须本地 JSON 解码并通过严格 Pydantic schema 与
   引用 allow-list；`response_format` 不被支持时 adapter 降级为普通 JSON
   重试一次，本地严格校验不放松。
5. **降级与错误**：embedding/rerank 缺失 → `capability_unavailable`，从不
   使 chat 不可用；offline/timeout/未配置 → 503；非法上游输出 → 502；
   AI 离线不影响 health/Vault/index/graph/editor。结构化失败响应携带
   `meta: {prompt_version, model}`。
6. **只读隔离**：M6 不写 Vault、SQLite、图、frontmatter 或 history；测试
   只用 `MockTransport`、fake adapter、`tmp_path` 与 fixture Vault。

`tests/backend/test_ai_m6_contracts.py` 用真实断言固定六路由（schema-gated
422，不依赖 Vault/AI）；`test_ai_*.py` 覆盖上表全部维度。

## 9. 未来受控 Agent 边界（M6/M7，非 Phase 0）

- 原则：**Small Model + Strong Tools / Schema / Rules**。
- 所有 AI 写路径必须经过：强输出 Schema → Policy（白名单/风险）→ Diff →
  Undo/Recovery 边界；每次执行有预算、超时、审批与审计记录。
- 拒绝无限自主循环；Agent 只能调用白名单工具；未过 Policy 的写操作拒绝。
- M6 已开放**只读** chat/completion 请求（强 schema、候选 allow-list、
  无写路径）；embedding/rerank 真实端点仍未开放（能力探测可见但调用返回
  `capability_unavailable`）；`server/ai/prompts/` 已从 placeholder 变为
  版本化只读 prompt 仓库（正文只含指令，模型输出从不是事实源）。
- 任何 AI 写路径（输出 → Policy → Diff → History → Recovery → 用户确认 →
  Vault write）属于 M7，M6 不实现。


## 10. M7 受控 Agent planner（已实现）

- 位置：`server/agents/{registry,tools,workflows,service}.py`。模型输出必须是
  ActionSet JSON（Pydantic `extra="forbid"`）；任何自然语言/URL/shell/路径
  对象在解析层拒绝（`invalid_action_output`, 502）。
- 工具：静态元数据 + 读/写种类 + 最低权限 + IO schema + 成本 + 允许的
  workflow。读工具只经 Vault/Index/Metadata/Links/Graph 服务；**写工具
  不能被模型 invoke**（registry 一律拒绝），模型只产 ActionSet，执行由
  AgentJobService 在 Policy/用户确认后经 TransactionExecutor 完成。
- 每次 workflow（daily_organizer / weekly_review）恰好一次模型 chat 调用，
  无工具递归、无 Agent loop；ActionSet 路径必须落在 scope/context
  allow-list 内，超界即拒绝且不二次调用。

## 11. M8 定时触发的 AI 边界（已实现）

- Scheduler 只提供“何时触发”（`server/scheduler/*`）：Daily/Weekly 定时与
  手动触发共用同一条 M7 受控链，即一次受控 planner 调用（仍恰好一次模型
  chat 请求）→ ActionSet 严格校验 → Policy → Diff → History。
- 默认只生成 Level 1 preview（`awaiting_confirmation`，run=`previewed`）；
  Level 2 自动执行必须同时满足配置开关、tag-only 白名单与 Policy `allow`，
  且仍然经由既有公开 accept（journal→VaultService→History），不会绕过
  M7 的任何校验。AI offline/超时只把该次 run 记为 failed 安全分类，不重试、
  不影响手动 job、health 与其它 API。
- Scheduler 本身不构造 prompt、不调用 adapter、不保存任何模型输出副本
  （输出只进入 M7 的 derived History 记录）。

## 12. 多供应商档案（PLAN-PROVIDERS，已实现）

- **一份配置、两个视图**：`server/config.py` 的 `AISettings` 既保留 M6 以来的
  扁平字段（`base_url` / `api_key` / `chat_model` / 超时 / 温度 / 最大输出），
  也新增 `profiles: list[ProviderProfile]` 与 `active_profile_id`。扁平字段是
  **当前生效配置**（所有 AI 消费者只读它），档案是**已保存的供应商库**；激活
  档案即把档案投影到扁平字段，因此 `AIStatusService`、`AIWorkflowService`、
  `AgentJobService` 的实现无需知道多供应商存在。
- **惰性物化**：磁盘上 `profiles` 为空时不会自动写入档案；只有用户显式新增/
  编辑档案才会持久化 `profiles` + `active_profile_id`（旧配置文件字节形状不变）。
  快照始终展示一行由扁平字段合成的 “default” 档案（`source: "default"`），
  因此界面从第一天起就有可切换的一行。
- **`provider` 语义放宽**（D6）：M6 审计把 `AISettings.provider` 锁为
  `Literal["omlx"]`；多供应商要求它跟随激活档案的 `kind`，故改为封闭集合
  `omlx | openai | openai-compatible | custom`（未知 kind 仍被拒绝）。
- **HTTP 边界**：`/settings/ai/profiles`（GET/POST）、`/activate`、`/delete`、
  `/test` 全部走 `Runtime.transition`（revision 冲突 409、与 Vault 切换/后台任务
  互斥、失败保留旧设置并回滚调度器）。密钥写入沿用 `PATCH /settings` 语义：
  省略/`null` = 保留，`""` = 清除，其余 = 覆盖；任何响应只回显 `api_key_set`。
- **状态可观测**：`GET /ai/status` 增加 `active_profile_id` / `active_profile_name`
  / `active_profile_kind`；`GET /settings` 的 `ai` 增加 `active_profile_id` 与
  `profiles[]`（每项含 `is_active` / `builtin` / `source` / `api_key_set`）。
- **认证错误分类**：OpenAI 兼容适配器把 401/403 映射为 `auth_error`（原先只有
  404 有专门分类），使“密钥错误/缺失”与“服务不可达”在界面上可区分。
- **前端**：设置 →「AI 配置」内提供档案列表 + 预置模板 + 编辑表单 + 只探测的
  “测试连接”；AI 面板头部与状态栏各有一个快捷切换下拉，切换后返回完整快照，
  由 shell 统一回填（无需重载页面）。
