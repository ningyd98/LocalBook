# LocalNote 多供应商 AI 档案（Provider Profiles）— 实施方案 v1.0

> 目标：像 DSH 一样，AI 模型支持配置**多个供应商（provider）**，每个供应商独立保存
> 端点 / 密钥 / 模型与超时参数，并支持**一键切换**（含面板与状态栏的快捷入口）。
>
> 现状（基线）：`settings.json` 里只有**一组** `ai.{base_url, api_key, chat_model}`
> （见 `server/runtime.py::ConfigRepository`），`AISettings.provider` 被锁死为
> `Literal["omlx"]`（审计 S4）。切换供应商只能改同一组字段，旧配置丢失。
>
> 本方案将这一组字段升级为 **profiles[] + active_profile_id**，并保持
> **旧配置文件、旧 API 载荷、旧环境变量、旧快照字段 100% 兼容**。

---

## 1. 设计决策

| # | 决策 | 理由 |
|---|---|---|
| D1 | 档案是「供应商级」：(id, name, kind, base_url, api_key, chat_model, temperature, max_output_tokens, request_timeout_seconds, connect_timeout_seconds, max_models_response_bytes) | 与 DSH `llm-pi-ai.providers` 同构；一个供应商一份凭据 |
| D2 | **扁平字段仍是「当前生效值」的唯一真源**（`ai.base_url` / `ai.chat_model` / `ai.api_key` / `ai.temperature` / …） | `AIStatusService`、`AIWorkflowService`、`AgentJobService`、`_agent_service_from` 全部直接读扁平字段，不改它们=不引入行为回归；测试 `test_ai_m6_contracts` 要求字段仍在 |
| D3 | `profiles` 为**惰性持久化**：只有用户显式新增/编辑档案才写入 `settings.json` | 只配置单供应商的旧用户，文件内容与载荷完全不变（`AISettingsPanel.test.tsx` 断言 PATCH 载荷形状） |
| D4 | 「默认档案」由扁平字段**合成**（`id="default"`, `source="settings"`, `builtin=True`） | 磁盘上没有 profiles 也能立刻在 UI 看到一行可切换的档案 |
| D5 | 密钥**只进不出**：任何响应只暴露 `api_key_set: bool` | 沿用现有安全契约（`snapshot()` / `test_ai_api_key.py`） |
| D6 | `AISettings.provider` 从 `Literal["omlx"]` 放宽为 `Literal["omlx","openai","openai-compatible","custom"]`，由激活档案的 `kind` 推导 | 「多供应商」的定义性变更；旧默认值仍是 `omlx`，旧用例只在这一条上按 v1.0 规格更新（偏差记录） |
| D7 | 切换走 `Runtime.transition`（revision 冲突 → 409；与 Vault 切换/后台任务互斥） | 复用既有并发与回滚语义，不新增并发模型 |
| D8 | 预置模板（preset）只在前端，后端只认 `kind` | 后端保持封闭枚举，模板演进无需改后端契约 |
| D9 | 端点校验（无凭据/无 query）与 Vault 越界校验沿用 `validate_endpoint` | 单一校验入口 |

---

## 2. 数据模型（`server/config.py`）

```python
PROVIDER_KINDS = ("omlx", "openai", "openai-compatible", "custom")
ProviderKind = Literal["omlx", "openai", "openai-compatible", "custom"]

class ProviderProfile(BaseModel):
    id: str            # ^[a-z0-9][a-z0-9._-]{0,63}$（小写规范化）
    name: str = ""     # 展示名；空则回落为 id
    kind: ProviderKind = "openai-compatible"
    base_url: str | None = "http://127.0.0.1:8000/v1"
    api_key: str | None = None          # 空/空白 = 无认证
    chat_model: str = "auto"
    temperature: float          = Field(default=0.1, ge=0, le=1)
    max_output_tokens: int      = Field(default=1200, ge=64, le=8192)
    request_timeout_seconds: float = Field(default=60.0, gt=0, le=120)
    connect_timeout_seconds: float = Field(default=0.5, gt=0, le=10)
    max_models_response_bytes: int = Field(default=1_000_000, gt=0)
    builtin: bool = False               # 服务端置位，客户端忽略
    source: Literal["settings", "env", "default"] = "settings"
    from_env: bool = False              # 端点/模型由 LOCALNOTE_* 环境变量提供
```

`AISettings` 新增：

```python
profiles: list[ProviderProfile] = Field(default_factory=list, max_length=30)
active_profile_id: str = "default"
```

`AISettings` 新增方法：

| 方法 | 语义 |
|---|---|
| `effective_profile()` | 在 `profiles` 中按 `active_profile_id` 查找；找不到则**合成** `builtin` 默认档案（D4） |
| `synthesized_profiles()` | 返回 `profiles or [合成默认档案]`（快照/API 用） |
| `apply_profile_to_flat(profile)` | 把档案写回扁平字段（含 `provider = profile.kind`） |
| `with_profile(profile, *, activate=True)` | 纯函数：替换/追加档案，按需激活，返回新 `AISettings` |
| `without_profile(id)` | 纯函数：删除档案；最后一个档案不可删 |

校验规则（`model_validator`）：
- `profiles` 内 `id` 唯一（重复 → `ValidationError`）；
- `active_profile_id` 非空字符串；
- 若 `active_profile_id` 指向不存在的档案且 `profiles` 非空 → 自动回落第一个档案（读旧文件时不炸）。

---

## 3. 运行时（`server/runtime.py`）

**持久化**（`ConfigRepository.save`）：在既有 `ai` 对象里追加两个键

```json
{"revision": 4,
 "vault_root": "…",
 "ai": {"enabled": true, "base_url": "…", "api_key": "…", "chat_model": "auto",
        "active_profile_id": "deepseek",
        "profiles": [{ "id": "deepseek", "name": "DeepSeek 官方", "kind": "openai", "…": "…" }]}}
```

- `save` 只在 `profiles` 非空时写入这两个键 → 旧用户文件字节级不变（D3）。
- `load` 兼容两种文件（有/无 `profiles`）；`profiles` 解析失败 → 走既有
  `settings_unreadable` 503 语义（不猜测、不静默丢弃）。

**快照**（`Runtime.snapshot`）在既有 `ai` 对象里新增只读字段：

```json
"ai": {"enabled": true, "base_url": "…", "chat_model": "auto", "api_key_set": true,
       "active_profile_id": "deepseek",
       "profiles": [{"id":"deepseek","name":"DeepSeek 官方","kind":"openai",
                     "base_url":"https://api.deepseek.com/v1","api_key_set":true,
                     "chat_model":"deepseek-chat","is_active":true,"builtin":false,
                     "source":"settings","from_env":false,
                     "temperature":0.1,"max_output_tokens":1200,
                     "request_timeout_seconds":60.0,"connect_timeout_seconds":0.5,
                     "max_models_response_bytes":1000000}]}
```

**事务方法**（全部走 `self.transition(revision)`，失败保留旧设置）：

| 方法 | 行为 |
|---|---|
| `activate_profile(profile_id, revision)` | 档案不存在 → 404 `ai_profile_not_found`；已是激活档案 → 幂等返回快照（**不**自增 revision） |
| `upsert_profile(profile, revision, *, activate=True)` | 新增或整体替换同 id 档案；`profiles` 为空时先固化合成默认档案（避免丢配置）；超 30 条 → 422 |
| `delete_profile(profile_id, revision)` | 删除激活档案 → 409 `ai_profile_active`；仅剩一个 → 409 `ai_profile_last`；不存在 → 404 |

三个方法共用私有 `_apply_ai_settings(new_ai, revision)`：`transition` → 重建
`AgentJobService`/`SchedulerService` → 停旧调度器 → 提交 → `_publish()`。
（重构 `apply_ai` 复用同一路径，不改变其对外语义。）

---

## 4. HTTP 契约（`server/api/routes/settings.py`，均需本地可信页 + JSON）

| 方法 | 路径 | 载荷 | 返回 |
|---|---|---|---|
| GET | `/api/v1/settings/ai/profiles` | — | `{revision, active_profile_id, profiles: [ProviderProfileView]}` |
| POST | `/api/v1/settings/ai/profiles` | `{provider, expected_revision, activate=true}` | `ServiceSettings` 快照 |
| POST | `/api/v1/settings/ai/profiles/activate` | `{profile_id, expected_revision}` | 快照 |
| POST | `/api/v1/settings/ai/profiles/delete` | `{profile_id, expected_revision}` | 快照 |
| POST | `/api/v1/settings/ai/profiles/test` | `{provider, expected_revision?}` | 与 `/settings/ai/models` 同形：`{status, models, selected_model, error_code, http_status?, key_sent, message}` |

- 载荷 `extra="forbid"`；`id` 长度 1–64；`name` ≤ 64；`base_url` ≤ 4096；
  `api_key` ≤ 4096；`chat_model` 1–256 且非空白。
- `provider.api_key` 省略/`null` = **保持该档案已存密钥**；`""` = 清除密钥。
  （与既有 `PATCH /settings` 的语义一致）
- 错误码：`ai_profile_not_found` 404、`ai_profile_active` / `ai_profile_last` 409、
  `ai_profiles_full` 422、`invalid_ai_endpoint` 422、`settings_conflict` 409。
- 「测试连接」**不写盘**、不发送任何笔记内容，只用**提交的**端点/密钥（省略密钥时
  用该档案已存密钥）。

`GET /api/v1/ai/status` 响应新增（全部可空）：

```json
{"provider":"omlx","active_profile_id":"deepseek","active_profile_name":"DeepSeek 官方",
 "active_profile_kind":"openai"}
```

---

## 5. 前端

1. `apps/web/src/api/settings.ts`：新增 `AIProviderProfile` / `AIProfiles` 类型与
   `fetchAIProfiles / saveAIProfile / activateAIProfile / deleteAIProfile / testAIProfile`
   五个调用；`AIConfiguration` 增加可选 `active_profile_id` / `profiles`（快照同形）。
2. `SettingsPanel.tsx` → 「AI 配置」：新增**供应商档案**区
   - 左列档案列表（名称 + 端点 + 当前激活标记 + 密钥状态）；
   - 右侧编辑表单（名称 / 类型 / 端点 / API Key / 模型 / 温度 / 最大输出 / 超时）；
   - 「+ 新增」→ 预置模板下拉（oMLX 本地、OpenAI、DeepSeek、Moonshot/Kimi、
     自定义 OpenAI 兼容）；「复制」「删除」（带确认）；「测试连接」；「保存并切换」/「保存」。
3. `AIPanel.tsx`：面板头部**快捷切换**下拉（`AIProviderSwitch`），选择即调
   `activate`，成功后刷新状态；附「管理供应商…」入口（回调打开设置 AI 区）。
4. `App.tsx`：面板传入 `onManageProviders`；底部状态栏 AI 项显示激活档案名；
   连接状态对话框显示供应商 + 档案 + 模型。
5. `i18n`：中英文文案；失败走既有 `errorText` 稳定错误码映射（新增 4 个码）。

---

## 6. 任务清单

| 编号 | 任务 | 文件 |
|---|---|---|
| P-01 | `ProviderProfile` / `AISettings.profiles` / 合成与纯函数 | `server/config.py` |
| P-02 | 持久化（仅在有档案时写两个键）+ 读回兼容 | `server/runtime.py` |
| P-03 | 快照脱敏输出 `profiles[]` / `active_profile_id` | `server/runtime.py` |
| P-04 | `activate/upsert/delete` + `_apply_ai_settings` 重构 | `server/runtime.py` |
| P-05 | AI 状态响应带激活档案 | `server/ai/schemas.py`、`server/ai/service.py`、`server/api/dependencies.py` |
| P-06 | 五个路由 + 严格 DTO + 错误映射 | `server/api/routes/settings.py` |
| P-07 | 后端测试 | `tests/backend/test_ai_profiles.py`、`tests/backend/test_ai_config.py`（1 条规格更新） |
| P-08 | 前端 API 客户端与类型 | `apps/web/src/api/settings.ts` |
| P-09 | 设置页档案管理 UI | `SettingsPanel.tsx`、`styles.css` |
| P-10 | 面板快捷切换 + 状态栏/对话框显示 | `AIPanel.tsx`、`App.tsx` |
| P-11 | 前端测试 | `tests/frontend/AIProviders.test.tsx` |
| P-12 | 门禁 `./scripts/check.sh` 全绿 | — |
| P-13 | 文档：README、`docs/ai-architecture.md`、开发日志 | 三处 |

## 7. 验收标准

- 后端：新增 `tests/backend/test_ai_profiles.py` ≥ 30 用例全通过；既有 830+ 用例全通过
  （唯一变更：`test_ai_config.py` 中「provider 锁死 omlx」一条按 D6 更新）。
- 前端：新增 `tests/frontend/AIProviders.test.tsx` ≥ 8 用例；既有 194 用例全通过。
- 安全：任何响应不回显密钥；`/settings/*` 仍限本机可信页；测试连接不发笔记内容。
- 兼容：不带 `profiles` 的旧 `settings.json` 可正常启动；只改单供应商时 PATCH 载荷形状不变。
- 门禁：`./scripts/check.sh` exit 0。

## 8. 风险与对策

| 风险 | 对策 |
|---|---|
| 切换供应商时后台计划任务/Agent 正在运行 | 复用 `transition`（`runtime_busy` 409），不做乐观切换 |
| 档案 id 与展示名混淆 | id 小写规范化 + 唯一性校验；展示名可重复 |
| 多档案后「模型自动选择」依赖 `qwen_match_pattern` | 保持既有解析顺序：显式模型名优先，`auto` 才回落 pattern |
| 密钥随档案增多而扩散 | 只在本地 `settings.json`（0600 目录）；响应仅 `api_key_set` |
| 前端表单与既有 AI 单配置双写冲突 | 单配置表单改为「当前激活档案」的投影，保存走 `upsert_profile(activate=true)`；旧扁平 PATCH 路径保留给脚本/CLI |
