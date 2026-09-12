# LocalNote 多供应商 AI 档案 — 开发日志（PRV-01 … PRV-14）

- 依据：`PLAN-PROVIDERS.md` v1.0（用户确认「A 完整实现」后开发）
- 用户需求：类似 DSH，AI 模型支持配置多供应商，可快速切换
- 状态图例：完成 / 部分完成 / 未完成

## 基线记录（开发前实测）

- [x] 后端：`830 passed, 3 skipped, 3 warnings in 35.55s`
- [x] 前端：`31 files / 242 passed`
- [x] 后端 node/pnpm 环境：`pnpm` 未在 PATH，改用 `corepack pnpm`（9.15.0，Node 22.22.2 固定版本）

## 逐项状态

| 编号 | 状态 | 改动文件 | 完成定义 | 测试命令与结果 |
|---|---|---|---|---|
| PRV-01 | 完成 | `server/config.py` | `ProviderProfile`（id slug 校验、空密钥/空端点归一、kind 封闭集合、数值边界）；`AISettings.profiles`/`active_profile_id`；`synthesized_profiles`/`effective_profile`/`ensure_active_profile`/`with_profile`/`without_profile`/`with_flat_overrides` 纯函数；投影不变式在 `model_validator(mode="wrap")` 中保证 | `pytest tests/backend/test_ai_profiles.py -k "profile or effective or synthesized"` 全通过 |
| PRV-02 | 完成 | `server/runtime.py`（`ConfigRepository`） | 仅在 `profiles` 非空时写入 `profiles`+`active_profile_id`；旧文件（无这两个键）可正常载入；档案结构损坏 ⇒ 503 `settings_unreadable`（不猜测、不静默丢弃） | `pytest -k "legacy_file or round_trip or unreadable"` 全通过 |
| PRV-03 | 完成 | `server/runtime.py`（`snapshot`/`_profile_views`） | 快照 `ai.profiles[]` 带 `is_active`/`builtin`/`source`/`api_key_set`，任何响应都不含 `api_key` 明文 | `pytest -k "snapshot_carries_profiles or never_echoes"` 全通过 |
| PRV-04 | 完成 | `server/runtime.py`（`_commit_ai`/`activate_profile`/`upsert_profile`/`delete_profile`） | 三个事务方法共用 `transition` 路径；幂等激活不自增 revision；当前生效项与最后一个档案不可删除；上限 30；失败保留旧设置 | `pytest -k "activate or delete or cap or stale_revision"` 全通过 |
| PRV-05 | 完成 | `server/ai/schemas.py`、`server/ai/service.py`、`server/api/dependencies.py` | `/ai/status` 返回 `active_profile_id`/`name`/`kind`（三态均带）；服务端 profile 元数据解析失败绝不影响探测 | `pytest tests/backend/test_ai_status.py tests/backend/test_ai_profiles.py -k "status"` 全通过 |
| PRV-06 | 完成 | `server/api/routes/settings.py` | 5 个路由 + `extra="forbid"` 严格 DTO；端点复用 `validate_endpoint`（无凭据/无 query）；错误码 404/409/422 映射；`/test` 只探测 | `pytest tests/backend/test_ai_profiles.py` 全通过 |
| PRV-07 | 完成 | `server/ai/adapters/openai_compatible.py` | 401/403 ⇒ `auth_error`（原仅 404 有专门分类），使“密钥错误”与“服务不可达”可区分；`_error_kind` 纯函数 | `pytest tests/backend/test_ai_adapter.py tests/backend/test_ai_profiles.py -k "auth or error_kind"` 全通过 |
| PRV-08 | 完成 | `tests/backend/test_ai_profiles.py`（新增） | 71 用例：模型归一/边界、库语义与纯函数、持久化兼容、HTTP 读写/切换/删除/上限/校验、只探测不写盘、`/ai/status` 档案回显、扁平 PATCH 与档案一致性 | `pytest tests/backend/test_ai_profiles.py -q` → `71 passed` |
| PRV-09 | 完成 | `apps/web/src/api/settings.ts` | `AIProviderProfile`/`AIProviderPayload`/`AIProfileList` 类型 + 5 个调用；`AIConfiguration` 增加 `active_profile_id`/`profiles` | `pnpm --filter @localnote/web typecheck` 通过 |
| PRV-10 | 完成 | `apps/web/src/components/ProviderSettings.tsx`（新增） | 档案列表（使用中标记/端点/密钥状态）+ 预置模板 + 编辑表单（id/名称/类型/端点/密钥/模型/温度/输出/超时）+ 测试连接 + 仅保存/保存并切换 + 删除确认 | `vitest run AIProviders` 全通过 |
| PRV-11 | 完成 | `apps/web/src/components/AIProviderSwitch.tsx`（新增）、`AIPanel.tsx`、`App.tsx`、`SettingsPanel.tsx` | 面板与状态栏两个快捷切换下拉（切换即 `activate` 并回填快照）；“管理供应商…”深链到设置 AI 区；连接状态对话框显示当前档案 | `vitest run AIProviders App AIPanel AISettingsPanel SettingsSafety` 全通过 |
| PRV-12 | 完成 | `apps/web/src/styles.css`、`apps/web/src/i18n/errors.ts` | 档案列表/表单/切换器样式；新增 4 个稳定错误码的中文映射（en 沿用服务端英文 message） | 前端构建通过 |
| PRV-13 | 完成 | `tests/frontend/AIProviders.test.tsx`（新增） | 9 用例：列表与切换、预置新增载荷、未触碰密钥不重发、仅保存、只探测、删除守卫与确认、过期 revision 引导错误、快捷切换回填、无档案与读取失败降级 | `vitest run AIProviders` → `9 passed` |
| PRV-14 | 完成 | `README.md`、`docs/ai-architecture.md`、本日志、`PLAN-PROVIDERS.md` | 记录契约（5 个端点、字段、错误码）、双视图设计、惰性物化、`provider` 语义放宽、前端入口 | 文档评审（人工） |

## 门禁输出

后端与构建（本机实测）：

```
== backend pytest ==            902 passed, 3 skipped, 2 warnings in 36.54s
== typecheck (@localnote/protocol) ==  OK
== typecheck (@localnote/graph) ==      OK
== typecheck (@localnote/web) ==        OK
== frontend build ==                   ✓ built in 3.21s
== frontend tests ==                   32 files / 254 tests（含新增 AIProviders 9 用例）
```

### 前端测试的既有失败（与本特性无关）

`tests/frontend/M2Matrix.test.tsx` 有 **2 个 FileTree 用例失败**，根因是工作区中
**尚未提交** 的文件树改动（`apps/web/src/components/FileTree.tsx`、
`packages/workspace/src/hierarchy.ts`、`Sidebar.tsx`、`store.ts` 等）：

- 新实现用 `collapsedPaths`（“默认展开、显式折叠”）替代旧的 `expanded` 语义，
  但渲染路径未把 `docs/child.md` 视为 `docs` 的子行，于是折叠 `docs` 后
  `child.md` 仍可见；
- 证据：单独运行 `vitest run M2Matrix -t FileTree` 同样 2 失败，与 AI 供应商
  改动无耦合（AIProviders/App/AIPanel/AISettingsPanel/SettingsSafety 全绿）；
- 该区域未在本特性中修改，**建议由文件树改动的作者决定修实现还是修用例**。

新增/扩展测试文件与用例数：

| 文件 | 用例数 | 说明 |
|---|---|---|
| `tests/backend/test_ai_profiles.py` | 71 | 档案模型/库语义/持久化兼容/HTTP 全路径/只探测/状态回显 |
| `tests/frontend/AIProviders.test.tsx` | 9 | 设置页档案管理 + 面板/状态栏快捷切换 |

后端 830 → 902（+72，含 1 条 M6 规格更新），前端 242 → 254（+12），均高于基线。

## 偏差记录

1. **`AISettings.provider` 不再是 `Literal["omlx"]`**（规格 D6 明确要求）
   - 偏差：M6 审计 S4 把 provider 锁死为 omlx；多供应商要求它跟随激活档案的
     `kind`，故放宽为 `omlx | openai | openai-compatible | custom`。
   - 理由：不放宽就无法表达“已激活的供应商是 OpenAI 兼容云端服务”。
   - 影响：`tests/backend/test_ai_config.py` 中“provider 锁死 omlx”一条按新规格
     更新为“未知 kind 仍拒绝 + 白名单内接受”，并新增白名单断言。
   - 建议：保留（默认值仍是 `omlx`，未配置档案的安装行为不变）。

2. **`Runtime.snapshot()` 的 `ai` 对象新增两个键**
   - 偏差：`tests/backend/test_runtime_settings.py` 原先断言 `ai` 对象全等。
   - 理由：前端需要档案库与当前档案指针才能渲染切换器。
   - 影响：该用例改为“M6 字段逐项相等 + 新键单独断言”，未放宽任何原断言。
   - 建议：保留。

3. **OpenAI 兼容适配器错误分类新增 `auth_error`**
   - 偏差：原先 401/403 归 `http_error`。
   - 理由：供应商测试需要区分“密钥错误/缺失（401）”与“服务不可达”。
   - 影响：仅错误 kind 值更精确；既有用例（500/400/404）不受影响。
   - 建议：保留。

4. **规范化/事件顺序调整**
   - `PATCH /settings` 改为通过 `AISettings.with_flat_overrides()` 合并，使
     “只提交部分字段”不会重置档案中的其它调参；空字符串密钥仍归一为 `null`
     （与既有契约一致）。
   - `activate` 在任何写入前校验 revision，因此过期页面的幂等切换也会得到
     409（不会让旧页面以为切换成功）。

5. **前端测试语言**：`tests/frontend/render.tsx` 以 `initialLocale="en-US"` 渲染，
   但本地环境实际生效中文（与 `errorText` 既有实现一致），故新用例的断言
   不依赖具体语言文案（既覆盖中文映射，又不因语言环境变化而失败）。

## 安全与兼容性自检

- [x] 任何响应（`/settings`、`/settings/ai/profiles*`、`/ai/status`）都不含
      `api_key` 明文，只含 `api_key_set`；测试逐响应断言 `TOKEN not in response.text`。
- [x] 密钥仍只写入实例配置文件（目录 0700、文件 0600，沿用既有 mkstemp+replace+fsync）。
- [x] `/settings/*` 继续要求本机可信页面（回环对端 + Host 白名单 + same-origin），
      新增路由继承同一依赖；非 JSON 请求 415。
- [x] 旧 `settings.json`（无 `profiles`）可直接启动；仅改单供应商时 PATCH 载荷
      与文件形状不变（用例 `test_legacy_file_shape_is_unchanged`）。
- [x] 切换供应商不触碰 Vault/索引/历史；AI 失败仍然只影响 AI 自身状态。
