# LocalNote Server M7 详细实施计划：Policy / Action Schema / Diff / Transaction / History / Undo / 受控 Agent

> 阶段：M7；路由：`mf/gpt-5.6-sol`（已确定并生效）。本文件是 M7 开发 Agent 的唯一实施依据。
> 规划阶段只创建本文件；不得创建实现代码，不得修改 `PLAN.md`、`PLAN-M1.md`、`PLAN-M2.md`、`PLAN-M3.md`、`PLAN-M4.md`、`PLAN-M5.md` 或 `PLAN-M6.md`。
> M7 建立在 M1–M6 已实现基线之上：M1 `VaultService` 是唯一文件系统门面并提供原子写/hash 冲突/路径安全/watcher；M4 提供 `.localnote/index.db` 派生索引；M5 提供只读 graph；M6 提供只读 AI、结构化输出、allow-list 与上下文限额。M7 是第一个允许 AI 通过受控流程写入笔记的里程碑。

## 1. 目标与可验收成功标准

### 1.1 总目标

在不改变 Markdown 为唯一事实源、不引入云端/真实 oMLX/真实用户 Vault 测试、不实现 Scheduler(M8) 的前提下，新增一个与 AI 解耦的 Policy Engine、严格 Action Schema、可审阅 Diff、事务式执行/回滚、History 与不依赖 LLM 的 Undo，并以有限工具和有限工作流开放 Daily Organizer、Weekly Review 两个受控 Agent 场景。

固定安全原则：**Small Model + Strong Tools + Strong Schema + Strong Rules**。模型只能返回结构化动作；模型不能直接触碰文件系统、SQLite、shell 或任意工具；Policy 是纯程序判定，不能由 LLM 判断安全性；任何写操作均经 `Preflight → Capture Before State → Execute → Validate → Commit`，失败必须恢复；默认 Level 1 需要用户确认，Level 2 只允许极小、明确、低风险动作。

### 1.2 全部成功标准（M7 门槛）

1. `PolicyEngine` 是独立领域模块，不 import LLM adapter/workflow；对动作类型、权限等级、文件数、修改字符数、protected path、existing file、overwrite、operation scope 做确定性判定，输出 `allow | deny | confirm` 及规则命中证据。
2. 白名单以代码常量/配置策略固定：允许的最小动作仅包括 `add_tags`、`remove_tags`、`add_link`、`create_note`、`patch_note`、`move_note`（实现可拆内部动作但 wire action 语义必须固定）；删除笔记/附件、附件覆盖、递归删除、清空目录、大量正文重写、Vault 外路径、shell、系统文件、LocalNote 源码永久拒绝。
3. 模型输出只能是严格 JSON Action/ActionSet，不接受自然语言指令、shell、路径对象或 function-call 任意参数；Pydantic `extra="forbid"`、字段/路径/数量/字符上限和动作语义校验全部通过后才能进入 Policy。
4. 每个拟执行动作均生成 before/after 或 unified diff；UI 支持单条 Accept/Reject、Accept All/Reject All；未接受的 diff 永不落盘。Diff 以 bytes/hash 为依据，不用 Markdown 重序列化覆盖原文；frontmatter/tag/link 修改必须通过保留原文的安全 patch 方案并在计划偏差中说明。
5. 一次 Job 具备明确阶段和 journal：预检、捕获 before、逐动作执行、验证 after、提交；任一动作失败，已执行动作按逆序 rollback，且 `改前 5 个、第 6 个失败` 场景前 5 个全部恢复，原始 bytes/hash 与目标存在性均不变。
6. History 持久保存 `job_id/task_type/model/prompt_version/start_time/end_time/files_read/proposed_actions/executed_actions/diff/before_hash/after_hash/status/error`，记录失败、拒绝、确认等待、回滚；不保存秘密，正文内容只保存在受限 diff/journal 字段且有大小上限。
7. 所有 Agent 写操作均可通过 `POST /api/v1/history/{id}/undo` 恢复，不调用 LLM、不重跑模型；Undo 使用 transaction journal/before state/diff 并带当前 hash 冲突保护，冲突时拒绝而非覆盖。
8. 受控 Agent 只能调用以下小工具：`vault.list/read/search/note.create/note.patch/note.move/metadata.get/set/tag.add/remove/link.add/knowledge.related/backlinks/outgoing/keyword`。工具由服务实现，工具不可直接访问 FS；每个 workflow 显式声明工具读/写白名单，不实现无限自主 Agent Loop。
9. API 至少实现：`POST /api/v1/jobs`、`GET /api/v1/jobs`、`GET /api/v1/history`、`POST /api/v1/history/{id}/undo`；Daily Organizer 与 Weekly Review 均走“建议 → Diff → 用户/策略确认 → 执行 → History”。
10. 前端最小 AI History 面板支持列表/详情、Diff、Accept/Reject、Accept All/Reject All、Level 1 确认对话框，并可提供 Daily Organizer/Weekly Review 触发按钮；loading/empty/error/rollback/conflict 状态清晰。
11. 后端新增测试只用 `tmp_path`/fixtures 与 fake adapter；不触真实用户 Vault、真实 oMLX、云端；既有 M1–M6 门禁保持，当前基线约后端 467、前端 91，实际数量以开发时命令为准。
12. 测试覆盖 Policy 矩阵、Schema、Diff、五成功一失败回滚、Undo、History、禁止项、路径安全、并发/hash 冲突、Mock AI 结构化输出、前端交互；`pytest`、Vitest、typecheck、web build、`compileall`、`scripts/check.sh` 全通过。

## 2. 现状基线与实施前检查

### 2.1 已实现且必须复用的边界

- `server/vault/service.py`：所有 Vault 文件 I/O 的唯一入口；`create_bytes` 不覆盖、`write_bytes`/delete/move 强制 expected hash，内部有 `RLock`、原子写和安全重检。
- `server/vault/atomic_write.py`：同目录临时文件、fsync、原子替换/无覆盖 create；M7 不得绕过或复制一套 FS 写实现。
- `server/vault/errors.py`：稳定 `VaultErrorCode`/安全错误体；新增 M7 错误应保持同一映射风格。
- `server/vault/watcher.py`：外部变更通知；M7 执行后需考虑 watcher 自触发和 index 增量同步，但不把 watcher 当事务日志。
- `server/vault/derived.py` 与 M4：`.localnote/index.db` 是可删除重建的派生数据，不作为 Undo 事实源。
- `server/index/service.py`：只读查询/candidate/metadata/links/graph；Agent 读工具复用服务，不直接 SQL。
- `server/ai/workflows.py`：M6 只读 workflow；输出不得直接转成写动作，必须经过 M7 action translator/semantic validation。
- `server/ai/context.py`、`candidates.py`：已有上下文限额、路径安全和 candidate allow-list；M7 保留这些上限并新增 action/path allow-list。
- `server/ai/schemas.py`：严格 Pydantic 结构化 DTO；M7 新 schema 放独立 `server/actions` 或 `server/policies`，不要把 M6 response 当 command。
- 实际 adapter 位于 `server/ai/adapters/`（不是单文件 `server/ai/adapters.py`）；M7 不新增 HTTP 旁路、不扩大 provider。
- `server/api/main.py` 有 `AIError`/`VaultError` 安全 handler；`dependencies.py` 已有 Vault/Index/AI DI；路由保持薄编排。
- `server/markdown/bytes.py` 只提供 raw bytes/hash，不解析或序列化正文；任何正文写入均须通过 `VaultService`。

### 2.2 实施前命令与保护检查

开发 Agent 必须先读取本计划和 M1–M6 基线，然后从仓库根执行并记录退出码：

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
git diff -- PLAN.md PLAN-M1.md PLAN-M2.md PLAN-M3.md PLAN-M4.md PLAN-M5.md PLAN-M6.md
```

基线失败先记录，不归因于 M7；不得为了绿灯删除/放宽旧测试。测试只复制 fixture 到 `tmp_path`，不得触真实用户 Vault/oMLX。

## 3. 范围、等级和永久禁止项

### 3.1 权限等级

- **Level 0（只读）**：list/read/search/metadata/knowledge 查询；永不写，策略直接允许但不能产生 mutation action。
- **Level 1（建议）**：模型提出的 tag/link/note patch/create/move 建议；默认 `confirm`，必须 UI 明确接受；单动作或受控批量均显示 Diff。
- **Level 2（低风险自动）**：仅允许策略明确定义的低风险 metadata/tag 增量，默认关闭或仅限 `tag.add`/`tag.remove`、已有文件、单文件、无正文覆盖、无 protected path、无 overwrite、字符预算极小；即使允许也必须写 History。Level 2 不允许删除、move、create、正文 patch、附件写入或多文件大批量。

开发时必须把 Level 2 的最终动作集合写入配置/常量和测试；不能以“模型判断低风险”替代规则。

### 3.2 动作白名单与风险维度

动作类型与最低等级建议如下：

| action | 含义 | 最低等级 | 默认决策 | 备注 |
|---|---|---:|---|---|
| `add_tags` | 给已有笔记增加 frontmatter tags | 1 | confirm | Level 2 仅单文件、小数量、无正文变化时可选 auto |
| `remove_tags` | 移除明确列出的 tags | 1 | confirm | 不得清空未知 tags；protected path 拒绝 |
| `add_link` | 在明确 heading/末尾安全位置增加 wikilink | 1 | confirm | 目标必须 allow-list；不改附件/代码块 |
| `create_note` | 创建新 Markdown 笔记 | 1 | confirm | 仅安全 notes/目录；不得覆盖已有文件 |
| `patch_note` | 对正文生成有限 patch | 1 | confirm | 强制 Diff；字符预算/范围/冲突检查 |
| `move_note` | 移动/重命名单个 Markdown 文件 | 1 | confirm | 不覆盖、不跨 root；Level 2 永禁 |
| delete/attachment/overwrite/recursive/shell | 任何删除、附件覆盖等 | none | deny | 永久 deny，不提供执行工具 |

Policy 必须逐项计算：`action_type`、`permission_level`、`file_count`、`modified_char_count`、`protected_path`、`existing_file`、`overwrite`、`operation_scope`（单文件/批量/目录/递归/Vault 外）。规则按 deny 优先、confirm 次之、allow 最后，输出 `matched_rules[]`、预算和安全摘要，便于审计。

### 3.3 M7 不做

- 不实现 Scheduler、cron、后台定时任务、M8 可靠性部署和局域网暴露。
- 不实现无限 Agent loop、自动递归规划、模型自定义工具、shell/process/subprocess、Python 执行、网络代理或任意 URL fetch。
- 不删除笔记/附件，不覆盖附件，不递归删除，不清空目录，不批量重写大量正文，不修改 Vault 外文件、`.localnote`、系统文件或 LocalNote 源码。
- 不引入云端 AI、真实 oMLX 集成测试、真实用户 Vault 测试、Postgres/Redis/队列；不把 SQLite 派生行当 Undo 原件。
- 不把 M6 `TagsResponse`/`ExtractTodosResponse`/`ClassifyResponse` 直接解释为写命令；必须经过显式 translator 和 Action Schema。

## 4. 有序任务清单（顺序、依赖、产出、完成定义、文件）

| 编号 | 任务 | 依赖 | 产出 | 完成定义 | 预计文件 |
|---|---|---|---|---|---|
| M7-01 | 基线/边界勘验 | M1–M6 | 实测命令、公共 API 清单、保护 diff | 读取所有指定文件；基线和偏差记录完成 | `server/*`, `packages/*`, `tests/*` |
| M7-02 | 配置/错误/枚举 | 01 | Policy/History/transaction 配置和稳定错误码 | 默认安全、范围校验、无 secret/path 泄漏 | `server/config.py`, `server/policies/errors.py`, tests |
| M7-03 | Action Schema 与语义校验 | 02 | Action/ActionSet/Job DTO、JSON schema | `extra=forbid`、action 白名单、路径/预算/patch 限制；自然语言拒绝 | `server/actions/schemas.py`, `validators.py`, tests |
| M7-04 | Policy Engine | 03 | 独立纯规则引擎 | 无 AI import；矩阵 allow/deny/confirm 全通过；可解释规则命中 | `server/policies/engine.py`, `rules.py`, tests |
| M7-05 | Diff/patch 生成器 | 03、M1、M3 | before/after hash、统一 diff、结构化 hunks | bytes 保真、上下文/字符上限、路径 allow-list；Accept/Reject 语义固定 | `server/actions/diff.py`, `patches.py`, tests |
| M7-06 | History 存储 | 02、03、05 | SQLite derived history schema/repository | `.localnote/index.db` migration 可删重建；完整字段/上限/查询分页 | `server/history/{schema,repository,service,schemas}.py`, `server/index/schema.py`, tests |
| M7-07 | Transaction journal/执行器 | 03、04、05、06 | preflight/capture/execute/validate/commit/rollback | 第 6 个失败可逆；逆序回滚、外部冲突不覆盖、watcher/index 安全同步 | `server/recovery/{journal,executor,service,schemas}.py`, tests |
| M7-08 | Undo | 06、07 | History→before-state 恢复服务 | 不调用 LLM；hash guard；成功/冲突/重复 undo 固定 | `server/recovery/undo.py`, `server/history/service.py`, tests |
| M7-09 | 受控工具与 workflow | 03、04、07 | 只读/写工具 registry、Daily/Weekly workflow | 工具按任务白名单；无 loop；建议先于执行；Mock AI 输出严格解析 | `server/agents/{tools,registry,workflows,service,schemas}.py`, tests |
| M7-10 | Job/History REST API | 06–09 | 四个必需端点与 DI/错误映射 | 建议、确认 token/accept、执行、分页详情、undo 均有契约 | `server/api/routes/jobs.py`, `history.py`, `dependencies.py`, `main.py`, tests |
| M7-11 | Protocol/client/workspace | 10 | Python/TS DTO 镜像、client、状态 | URL/body/错误一致；竞态保护；不保存秘密/正文超限 | `packages/protocol/src/index.ts`, `apps/web/src/api/{types,client}.ts`, `packages/workspace/src/*` |
| M7-12 | AI History/Diff UI | 11 | History panel/detail/diff/confirm/buttons | Accept/Reject/All、Level 1 dialog、rollback/conflict/accessibility | `apps/web/src/components/{AIHistoryPanel,DiffView,ConfirmDialog}.tsx`, `App.tsx`, `styles.css` |
| M7-13 | 测试/安全/回归 | 02–12 | 全矩阵、隔离审计、旧门禁 | 全部仅 fixture/tmp/fake；467/91 基线不回退 | `tests/backend/test_m7_*.py`, `tests/frontend/*M7*.test.*` |
| M7-14 | 文档/验收/M8 入口 | 全部 | README/docs/roadmap 状态与交付报告 | 命令全 0；偏差/未决/禁止项/M8 入口清楚 | `README.md`, `docs/ai-architecture.md`, `docs/architecture.md`, `docs/development-roadmap.md` |

开发 Agent 必须按此顺序推进；计划缺陷只能记录偏差与理由，不得静默改设计。

## 5. 技术方案与关键设计决策

### 5.1 Action Schema：模型只输出结构化动作

建议 Python DTO（命名可等价但语义固定）：

```python
class ActionType(StrEnum):
    ADD_TAGS = "add_tags"
    REMOVE_TAGS = "remove_tags"
    ADD_LINK = "add_link"
    CREATE_NOTE = "create_note"
    PATCH_NOTE = "patch_note"
    MOVE_NOTE = "move_note"

class PermissionLevel(IntEnum):
    READ_ONLY = 0
    SUGGEST = 1
    LOW_RISK_AUTO = 2

class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: UUID
    action: ActionType
    permission_level: Literal[0, 1, 2]
    file: RelativePath
    target_file: RelativePath | None = None
    tags: list[SafeTag] = Field(default_factory=list, max_length=10)
    link_target: RelativePath | None = None
    patch: list[PatchHunk] = Field(default_factory=list, max_length=20)
    content_base64: str | None = None       # create only, bounded
    reason: str = Field(min_length=1, max_length=500)
    expected_sha256: str | None = None

class ActionSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actions: list[Action] = Field(max_length=20)
    task_type: Literal["daily_organizer", "weekly_review", "manual"]
    model: str | None = None
    prompt_version: str | None = None
```

`RelativePath` 复用 M1 词法规则并在 VaultService 再检；拒绝绝对路径、`..`、NUL、反斜杠、`.localnote`、隐藏受保护目录、Vault 外和源码路径。`PatchHunk` 只能表示有限、可定位的 old hash/old text 或 byte span/new text；不得接受任意自然语言 patch。ActionSet 通过语义 validator 检查 action 与字段关系（例如 add_tags 必须有 tags、create 不得 expected hash、move 必须 target、patch 不得 content_base64），并将模型自带的 `permission_level/model/prompt_version`视为不可信元数据，由服务端覆盖/校验。

模型 prompt 明确“只输出 JSON ActionSet，不输出解释或自然语言命令”；adapter 仍沿用 M6 JSON decode + Pydantic，结构化失败返回稳定 `invalid_action_output`，不重试为自由文本。M6 的候选路径和上下文上限继续作为输入边界。

### 5.2 PolicyEngine：独立于 AI 的规则求值

Policy API：

```python
class PolicyDecision(StrEnum): ALLOW = "allow"; DENY = "deny"; CONFIRM = "confirm"
class PolicyResult(BaseModel):
    decision: PolicyDecision
    level: int
    action_type: str
    matched_rules: list[str]
    reasons: list[str]
    budgets: PolicyBudget

class PolicyEngine:
    def evaluate(self, action: Action, *, facts: ActionFacts) -> PolicyResult: ...
    def evaluate_set(self, actions: list[Action], *, facts: FactsProvider) -> PolicySetResult: ...
```

`ActionFacts` 由 Vault/index/metadata 只读预检取得：是否 existing、当前 hash、protected path、file count、modified chars、scope、attachment/markdown、target existence、overwrite、directory/recursive flags。Engine 只接受 facts，不读文件、不调用 LLM、不写 History；上层负责 facts 采集。

判定顺序：

1. action type 不在白名单、权限级别非法、path 不安全、scope 非 single-file、附件/目录/系统源码、delete/overwrite/recursive、大于任一预算 ⇒ **deny**。
2. existing/overwrite 不符合动作语义、expected hash 缺失、目标不在 M6 allow-list、modified chars/file count 超限、protected path 命中 ⇒ **deny**。
3. Level 0 mutation ⇒ deny；Level 1 合法 mutation ⇒ confirm；Level 2 仅在 `AUTO_ACTIONS` 且单文件、无正文、无 overwrite、预算内 ⇒ allow，否则 confirm/deny（建议默认只 allow `add_tags`/`remove_tags`）。
4. 空动作集、重复 action_id、同一文件互相冲突、累计预算超限 ⇒ deny；批量中任一 deny 默认全组 deny，不部分执行。

默认预算建议：单 job `max_actions=20`、`max_files=10`、Level 1 可配置 `max_modified_chars=20_000`、Level 2 `max_files=1`/`max_modified_chars=0`；`max_diff_bytes`、History 字段和请求 body 同样有硬上限。具体值必须配置校验并写入测试矩阵。

### 5.3 Diff 生成与接受语义

DiffBuilder 在 preflight 读取 raw bytes 与 SHA-256，生成 `DiffEntry(path, operation, before_hash, after_hash, before_size, after_size, unified_diff, hunks, status)`。对于 UTF-8 Markdown，展示 unified diff；对非 UTF-8/附件只展示 hash/byte length，且所有附件 mutation deny。Diff 是建议的不可变快照，Accept/Reject 只改变 job action 状态，不直接写文件。

`Accept All` 只接受策略为 confirm 且用户明确选择的整组；`Reject All` 让 job 进入 `rejected`，不执行。即使 UI 显示 Accept，服务端仍重新读取 before hash 并重新评估 Policy，避免 stale UI/并发。Diff 生成失败或 old text 不匹配时 job 失败且不写。

Tag/link 变更优先调用独立保留格式的 patch helper：只在 frontmatter/body 的明确安全位置插入/删除，保留 BOM、换行风格、未知字段和其它正文；若实际 frontmatter writer 不能保证保真，宁可返回 `unsupported_action`/deny，不用 YAML dump 重写整篇。所有 write 最终只调用 VaultService 的 `create_bytes`/`write_bytes`/`move_file`，绝不调用 pathlib/open。

### 5.4 事务、回滚与 Undo

一个 Job 事务状态：`planned → preflighted → captured → executing → validating → committed`，异常分支为 `awaiting_confirmation`、`rejected`、`rolled_back`、`failed`、`conflict`、`undone`。伪代码：

```text
parse/validate ActionSet
→ preflight all actions and Policy all actions (no writes)
→ capture every source/target before state: existence, bytes (bounded), sha256, type, path
→ persist journal + proposed diff + pending History
→ execute actions in deterministic order; each write records inverse operation
→ after each write validate path/type/hash and expected semantic result
→ on any error: inverse actions in reverse order, validate restored before hashes
→ if all valid: commit journal/History, trigger/allow normal watcher/index convergence
```

Journal 必须在任何写前落入 `.localnote/index.db` 的 history/journal 表（或等价 atomic record）；每条 entry 含 operation、path、before existence/bytes/hash、after hash、inverse action、completed。事务中的 before bytes 受单文件和总 journal cap 限制；超限 preflight deny，不“部分备份”。Rollback 不是无条件覆盖：若当前 hash 仍等于本事务 after hash，才用 before state 恢复；外部修改则标记 `rollback_conflict`，停止并保留诊断，绝不覆盖外部改动。对 create 的 rollback 是删除仅当文件 hash 等于 created hash；move rollback 只在 destination/source 状态符合 journal 时执行。

M1 的 `VaultService` 单 mutation lock 可保证进程内串行，但 M7 必须在 job 层使用 job lock/idempotency key；跨进程 TOCTOU 仍由 expected hash 检测。watcher 事件不作为成功确认；index 是派生数据，执行后由既有 watcher 增量更新，必要时提供安全 rebuild，不把 index 写入事务事实。

Undo：读取 committed History/journal；确认当前每个 after hash 与 journal 一致，再逆序恢复 before state；成功写入新的 `undo_job_id`/History 事件，原 History 不覆盖。已 undo、无 before state、hash 冲突、禁止恢复路径均返回稳定错误。Undo 不生成新 LLM 建议、不调用 Policy 以外的模型，但必须重复路径和 operation safety check。

### 5.5 History 存储选择

采用 **M4 `.localnote/index.db` 增量 schema migration**，而非另建不可管理数据库：History 是派生数据，随 `.localnote/` 删除可重建，Markdown 才是事实源；但删除 history 会丢审计/Undo 能力，UI 必须明确“History 可删除、删除后不可 Undo”。开发 Agent 必须在 M7-06 根据 M4 migration API 增加版本并记录取舍，不修改 M4 既有表语义。

建议表：

- `ai_jobs(job_id TEXT PRIMARY KEY, task_type, model, prompt_version, permission_level, start_time, end_time, status, error_json, files_read_json, proposed_actions_json, executed_actions_json, diff_json, before_hash_json, after_hash_json, created_at)`；
- `ai_job_journal(job_id, seq, operation, path, before_exists, before_bytes_base64, before_hash, after_exists, after_hash, inverse_json, state, UNIQUE(job_id,seq))`；
- 可用 JSON 字段保持首期 schema 小，但每个 JSON 经过 Pydantic 严格模型和字节/条数上限；禁止存完整 prompt、token、Authorization、绝对 root、无限正文。

History repository 只通过 `IndexDatabase` 参数化 SQL、单事务、同一 RLock/connection 生命周期；API 分页稳定按 `start_time DESC, job_id DESC`，详情按 journal seq。Index rebuild 不清空 History；若现有 M4 rebuild `DELETE` 只针对业务派生表，开发必须测试 migration 表保留。若实现发现 M4 rebuild 会误删 history，先修为表分离/白名单并在报告记录偏差。

### 5.6 受控 Agent 工具和 Workflow

工具 registry 使用静态定义：名称、读/写、最低权限、输入 schema、输出 schema、成本预算、允许 workflow。工具实现只依赖 VaultService/Index/Metadata/Links/Graph，禁止 FS/SQL 旁路：

- 只读：`vault.list/read/search`、`metadata.get`、`knowledge.related/backlinks/outgoing/keyword`；
- 受控写：`note.create`（仅新 `.md`、不覆盖）、`note.patch`（需 expected hash/diff）、`note.move`（单文件不覆盖）、`metadata.set`（仅允许显式安全字段且仍生成 diff）、`tag.add/remove`、`link.add`。

`ToolRegistry.invoke` 在调用前检查 workflow allow-list、action type、Policy、预算和确认状态；写工具不能被模型直接 invoke，模型只能提出 ActionSet，执行器在用户/策略确认后调用。没有 tool call 递归：每个 Workflow 最多一个模型请求（若 M6 adapter 的 schema fallback 需要协议重试，仍不算 Agent loop）、一次 ActionSet、一次执行事务。

- **Daily Organizer**：读取最近/指定范围的 notes（由显式 request paths/query 限额决定），可建议增量 tags、有限 links、极少 patch；默认全为 Level 1 confirm；流程 `POST jobs` 返回 diff → 用户接受 → `POST jobs/{id}/execute`（若实现额外端点必须文档化）→ History。
- **Weekly Review**：读取范围内 notes/related/backlinks/outgoing/metadata，生成 review 建议和可选新 review note；`create_note` 必须用户确认、目标不存在且目录安全；不得自动重写旧笔记或删除。

任务 request 必须声明范围、最大文件数/字符数、workflow；不接受 prompt/system instruction、URL、shell。M6 context/candidate allow-list 传给 workflow，模型只能引用 context，并由 action validator 限定可写路径为 request scope。

## 6. 公共 API、DTO、错误与示例

### 6.1 REST 端点

必需端点：

```http
POST /api/v1/jobs                 # 创建建议/执行前 job，默认不写
GET  /api/v1/jobs?limit=20&offset=0
GET  /api/v1/history?limit=20&offset=0&status=committed
GET  /api/v1/history/{id}          # 详情、diff、journal 摘要
POST /api/v1/history/{id}/undo    # 不依赖 LLM 的恢复
```

为完成“确认→执行”而不让 GET/POST 语义含糊，推荐 additive：

```http
POST /api/v1/jobs/{id}/accept      # 接受指定 action ids 或 all，执行事务
POST /api/v1/jobs/{id}/reject      # 拒绝 diff，不写
```

如果开发采用单一 `POST /jobs` 的 `phase=preview|execute`，必须保持默认 preview、执行带一次性 confirmation token，并在 OpenAPI/测试固定；不得默认创建请求就写盘。

创建请求示例：

```json
{
  "task_type":"daily_organizer",
  "permission_level":1,
  "scope":{"paths":["notes/today.md","notes/project.md"],"max_files":2},
  "execute":false
}
```

响应示例：

```json
{
  "job_id":"7b8c...",
  "status":"awaiting_confirmation",
  "task_type":"daily_organizer",
  "model":"Qwen3.5-4B-Instruct-4bit",
  "prompt_version":"daily_organizer@m7.1",
  "policy":{"decision":"confirm","matched_rules":["level_1_requires_confirmation"]},
  "actions":[{"action_id":"...","action":"add_tags","file":"notes/today.md","tags":["工作"],"reason":"..."}],
  "diff":[{"path":"notes/today.md","operation":"update","before_hash":"sha256:...","after_hash":"sha256:...","unified_diff":"@@ ..."}]
}
```

Accept/Undo：

```bash
curl -fsS -X POST http://127.0.0.1:3780/api/v1/jobs \
  -H 'content-type: application/json' \
  -d '{"task_type":"daily_organizer","permission_level":1,"scope":{"paths":["notes/today.md"],"max_files":1},"execute":false}'

curl -fsS -X POST http://127.0.0.1:3780/api/v1/jobs/7b8c.../accept \
  -H 'content-type: application/json' -d '{"action_ids":["..."],"confirm":true}'

curl -fsS -X POST http://127.0.0.1:3780/api/v1/history/7b8c.../undo \
  -H 'content-type: application/json' -d '{"confirm":true}'
```

### 6.2 DTO/错误契约

Python 与 TypeScript 必须镜像：`JobStatus` (`planned/preflighted/awaiting_confirmation/executing/committed/rejected/rolled_back/failed/conflict/undone`)、`PolicyDecision`、`ActionType`、`PermissionLevel`、`ActionDTO`、`PolicyResultDTO`、`DiffEntryDTO`、`JobSummaryDTO`、`JobDetailDTO`、`HistoryPageDTO`、`UndoResponseDTO`。所有 response `extra=forbid`（TS interface 仅镜像，不放任意 JSON）。

新增错误码建议：`policy_denied`(403)、`confirmation_required`(409)、`invalid_action`(400)、`invalid_action_output`(502)、`job_not_found`(404)、`job_state_conflict`(409)、`transaction_failed`(500/409)、`rollback_failed`(500)、`undo_conflict`(409)、`undo_unavailable`(409)、`history_unavailable`(503)、`history_limit_exceeded`(413)、`unsupported_action`(400)。错误形状延续 `{"error":{"code","message","path":null},"meta":{...}}`，不返回绝对路径、正文、token、stack 或上游响应。

### 6.3 数据流

```text
POST /jobs (task_type/scope/level)
  → request schema/path validation
  → controlled workflow + M6 bounded context/candidate allow-list
  → Mock/Local model JSON only
  → ActionSet Pydantic + semantic validation
  → Preflight facts (Vault/index read only)
  → PolicyEngine (deny/confirm/allow, no LLM)
  → Capture before bytes/hash + journal
  → Diff response (no writes)
  → User Accept/Reject or Level-2 policy allow
  → transaction Execute via VaultService
  → Validate after hashes/semantic postconditions
  → Commit History; watcher/index converge
  → History list/detail / Undo (before-state, hash guard)
```

## 7. 目录树与文件用途

```text
server/
  actions/
    __init__.py                 # Action exports
    schemas.py                  # Action/ActionSet/PatchHunk/Job DTO
    validators.py               # semantic schema/path/action validation
    diff.py                     # raw bytes hash + unified/structured diff
    patches.py                  # bounded format-preserving tag/link/note patches
  policies/
    __init__.py
    engine.py                   # pure PolicyEngine; no AI/FS/HTTP
    rules.py                    # whitelist, budgets, protected paths, level rules
    errors.py                   # policy/action stable domain errors
  history/
    __init__.py
    schemas.py                  # History/Job/Journal DTO
    schema.py                   # migration/table definitions (or index migration hook)
    repository.py                # parameterized SQLite CRUD, page/detail
    service.py                   # history lifecycle, size redaction
  recovery/
    __init__.py
    schemas.py                  # transaction/journal/undo DTO
    journal.py                  # durable before/after/inverse entries
    executor.py                 # preflight/capture/execute/validate/rollback/commit
    undo.py                      # hash-guarded inverse execution, no LLM
    service.py                   # orchestration boundary
  agents/
    __init__.py
    schemas.py                  # workflow/tool request/response
    tools.py                    # small tool implementations via service boundaries
    registry.py                  # static tool metadata and allow-lists
    workflows.py                # Daily Organizer/Weekly Review, no loop
    service.py                   # controlled job planning entry
  api/routes/
    jobs.py                     # POST/GET jobs + accept/reject if used
    history.py                  # history list/detail/undo
  api/{dependencies.py,main.py} # DI, routers, error handlers
  config.py                     # additive policy/history/recovery caps

packages/protocol/src/index.ts  # M7 DTO/error/type mirror
packages/workspace/src/{types,store,index.ts} # jobs/history/diff state, requestVersion
apps/web/src/api/{types,client}.ts            # jobs/history/undo client
apps/web/src/components/
  AIHistoryPanel.tsx            # history list/detail
  DiffView.tsx                  # safe text diff, no HTML injection
  ConfirmDialog.tsx             # Level 1 confirmation
  OrganizerActions.tsx          # optional Daily/Weekly buttons
apps/web/src/{App.tsx,styles.css} # wiring/accessibility

tests/backend/test_m7_{schemas,policy,diff,history,transaction,rollback,undo,agents,api,security}.py
tests/frontend/{M7History,M7Diff,M7Client,M7Isolation}.test.tsx
```

## 8. 配置、依赖、风险与降级

### 8.1 配置建议

在 `PolicySettings`/`HistorySettings`/`RecoverySettings` 中 additive 增加：`enabled`、`max_actions`、`max_files`、`max_modified_chars`、`max_diff_bytes`、`max_journal_bytes`、`protected_path_prefixes`、`level2_auto_actions`（默认空或 tag-only）、`history_page_size/max`、`confirmation_ttl_seconds`、`job_timeout_seconds`、`max_undo_bytes`。环境变量沿用 `LOCALNOTE_POLICY__*` 等；所有上限必须 `Field` 校验，禁止客户端覆盖服务端硬上限。默认 `policy.enabled=true`、Level 2 auto 为空最安全。

### 8.2 风险矩阵

| 风险 | 影响 | 降级/控制 |
|---|---|---|
| 回滚中途失败 | 部分写入 | 每步 journal+逆序；hash guard；状态 `rollback_failed`，UI 告警，禁止继续自动覆盖 |
| 外部 watcher/编辑器并发 | stale write/误恢复 | 每动作 expected hash；preflight 与执行前重检；冲突 409，用户 reload |
| tag/link 保格式困难 | 正文损坏 | 只允许可证明的 bounded patch；否则 `unsupported_action`，不写 |
| 小模型幻觉 action/path | 越界或错误修改 | strict schema、M6 allow-list、Policy、Diff、用户确认；未知 path deny |
| 批量/字符预算爆炸 | 性能/风险 | preflight 总预算、History cap、deny；不部分执行 |
| History SQLite 损坏/太大 | 审计/Undo 不可用 | 独立派生表、分页/字段上限；history unavailable 时禁写/禁 undo，Vault 仍可用 |
| Index 重建/History migration | 启动/数据丢失 | migration 事务、旧表白名单、fixture 重建验证；History 可删除重建但 Undo 明确不可用 |
| watcher 自触发 | 重复 index 事件 | 使用既有 debounce/lock；不把事件作为二次 AI job；请求只等待 Vault 验证 |
| UI 接受过期 Diff | 覆盖用户修改 | server 重查 before hash；conflict，不信任 UI diff |
| 进程崩溃 | journal 未完成 | 记录 `executing`；M7 不做后台恢复 loop，启动只将未完成 job 标记 `failed/recovery_required`，不自动写；可靠性演练留 M8 |
| prompt injection | 模型提出危险动作 | context 标记不可信；工具不可由模型直接调用；Policy deny 永久禁止项 |
| 秘密/正文泄漏 | 隐私 | 日志只记 hash/count；History/diff 硬上限；不存 prompt/token/凭据；错误固定文案 |

## 9. 测试矩阵与验收命令

### 9.1 后端测试

1. **Policy 矩阵**：每个 action×Level 0/1/2；allow/deny/confirm；文件数、字符数、protected path、existing/non-existing、overwrite、scope、attachment、directory、delete、Vault 外、源码路径。
2. **Schema**：extra field、自然语言/markdown fenced command、非法 path、未知 action、action 字段错配、重复 id、超长 reason/tags/patch/content、model-owned 字段篡改、JSON schema。
3. **M6 接合**：Mock adapter 输出合法 ActionSet、非法 JSON、自然语言、越界候选 path、超限 context；断言只读 response 不能直接写。
4. **Diff**：UTF-8/BOM/LF/CRLF/非 UTF-8 bytes hash 保持；tag/link/patch hunks；Accept/Reject/All；Diff 不产生写。
5. **事务**：单 create/patch/move；preflight deny 无写；确认前无写；五动作成功；第六动作失败后前五 before bytes/hash/存在性全恢复；rollback journal/状态正确。
6. **Vault 安全**：`../`、绝对、反斜杠、symlink、`.localnote`、附件、源码、root 外；所有错误不触外部 sentinel；调用只经过 VaultService fake spy。
7. **并发/hash**：用户外部修改、watcher 修改、两个 job 同 path、stale Accept、rollback conflict；均 409/不覆盖。
8. **History**：全部字段、失败/拒绝/confirm/rollback/commit、分页、大小上限、secret/path redaction、migration、rebuild 不误删、index unavailable 隔离。
9. **Undo**：成功恢复、多文件逆序、重复 undo、after hash 改变、journal 缺失、create/move/patch inverse；无 adapter 调用；新 Undo History 可审计。
10. **Agent**：每个 workflow tool allow-list；Daily/Weekly 只一次模型调用；写工具不能被模型直接 invoke；无限 loop/未知工具拒绝。
11. **API**：OpenAPI、422/400/403/404/409/413/502/503/500、preview 默认无写、accept/reject/undo；错误无 absolute root/stack/body/secret。
12. **回归**：M1–M6 全 suite、health/Vault/editor/index/graph/AI 离线隔离；所有 AI 均 fake/MockTransport。

### 9.2 前端测试

- client 请求 method/body/path/query、DTO、ApiError code/meta、全 fetch mock；
- History list/detail pagination、loading/empty/offline/history unavailable；
- Diff 文本显示、单条 Accept/Reject、Accept All/Reject All、confirm dialog；不使用 `dangerouslySetInnerHTML`，不执行 diff HTML；
- Level 1 未确认不能调用 accept；Level 2 允许状态仍显示审计；rollback/conflict/undo 状态；
- Daily/Weekly trigger disabled/no Vault/no current note、requestVersion 丢弃旧响应、重复点击幂等；
- Accessibility：按钮/对话框 aria、键盘 Escape/焦点、错误可读；Files/Search/Graph/AIPanel 回归；不写本地正文或 token。

### 9.3 验收命令

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

可选受控 smoke 只能用 fixture 副本/tmp 和 fake model：创建 preview → 检查文件 hash 不变 → accept 单 action → 读取 after hash → undo → before hash；再运行第 6 个动作失败的事务 fixture，证明五个文件均恢复。不得启动真实 oMLX、不得把真实用户 Vault 配入环境。最终报告包含每条命令退出码、后端/前端实际数量、Mock AI/MockTransport 证据、保护文件 diff、未触真实网络/文件证明。

## 10. 明确假设、未决事项与 M8 入口

### 10.1 假设

1. Python 3.12、FastAPI/Pydantic v2、SQLite/M4 `IndexDatabase` 和 M1 VaultService API 保持兼容；不引入新 Python 第三方依赖。
2. 单用户 loopback、同一进程内请求串行锁足以支撑 M7；expected hash 处理同权限外部变更，不能消除 OS 级 TOCTOU。
3. Markdown/附件仍由 Vault bytes 作为唯一事实源；History/Journal/Diff 是可删除派生审计数据。
4. M6 adapter 的结构化 JSON 能力可被 fake；真实 oMLX 永不成为测试依赖，云端不支持。
5. 初始 Level 2 默认无自动动作，若产品需要放行，最多 tag-only 单文件无正文变更；所有自动动作同样记录 History。
6. 受控 workflow 每次最多一个模型请求/一个 ActionSet，不做自主循环、后台恢复或调度。

### 10.2 未决事项（开发 Agent 必须明确取舍并测试，不得静默改变）

- History 存 `.localnote/index.db` 的同库新表，还是独立 `.localnote/history.db`：本计划推荐同一 M4 数据库、独立表/迁移，减少生命周期分裂；若 M4 API 无法安全扩展，再选择独立派生库并说明。
- History 是否保存 before bytes：推荐在 `max_journal_bytes` 内保存；超限 preflight deny，而不是仅保存不可 Undo 的 hash。
- Level 2 自动动作最终白名单/预算：推荐默认空，产品明确批准后仅 tag add/remove。
- frontmatter/tag/link patch 的保真策略：若不能保留未知字段/换行/BOM，动作应 deny/unsupported，不使用 YAML 全量 dump。
- job accept/execute 是否拆成 endpoints：推荐拆 `POST /jobs/{id}/accept`，preview 默认无写；若合并必须有一次性 confirmation token 和同等测试。
- M7 启动时未完成 journal 是否只标记失败还是提供显式恢复 API：推荐只标记 `recovery_required`、不自动恢复；崩溃恢复演练和自动恢复属于 M8。
- History retention/清理：M7 不实现 scheduler/自动清理；可提供显式、受 Policy 保护的未来管理入口，但本期不删除 history API。
- index watcher 在 Job commit 后的等待语义：推荐不等待 watcher，将 Vault hash/History commit 作为成功，派生 index eventual update；UI 显示 indexing 状态（若已有状态能力）。
- `metadata.set` 可写字段列表：推荐先只开放 tags/明确 allow-list 元数据，不开放任意 frontmatter key。

### 10.3 M8 入口

M7 审计通过后，M8 才能在不破坏手动 Job/Undo 的前提下设计 Scheduler、可靠性与部署：定时 Daily/Weekly、崩溃 journal 恢复演练、锁/超时/清理、显式局域网暴露。M8 必须复用 M7 Policy/Transaction/History，而非另造写路径；Scheduler 停止时核心 Vault、AI、历史和手动执行仍可用。

## 11. 文档、交付与审计格式

开发阶段按需更新（不得修改旧计划文件）：`README.md`、`docs/architecture.md`、`docs/ai-architecture.md`、`docs/development-roadmap.md`，并可新增 `server/policies/README.md`、`server/history/README.md`、`server/recovery/README.md`、`server/agents/README.md`。文档必须明确 M7 已开放的受控写路径、默认 Level 1 确认、Level 2 实际范围、永久禁止项、History 可删除属性和 Undo 冲突语义。

开发 Agent 交付必须返回：

1. M7-01–M7-14 完成/未完成表、关键文件和每个偏差；
2. 真实 Policy 规则/等级/预算矩阵和 deny 优先证据；
3. Python Action Schema、TS DTO、错误码、curl/JSON 与 OpenAPI；
4. Diff/Accept/Reject/Confirm 的 UI 与服务端二次校验；
5. 五成功一失败事务回滚、journal、History/Undo/hash 冲突证据；
6. Daily Organizer/Weekly Review 工具 allow-list、模型调用次数和 Mock AI 证据；
7. `.localnote/index.db` history migration、重建/删除语义与大小上限；
8. 禁止项、路径安全、秘密脱敏、VaultService 唯一路径审计；
9. 后端/前端测试数量、所有验收命令退出码、无真实网络/Vault 证明；
10. 未决事项、M8 入口、旧计划文件未修改证明。

审计 Agent 独立检查：需求符合性、Policy 是否完全脱离 LLM、Action schema/TS 一致性、Diff 保真、事务与回滚可逆性、Undo 不依赖模型、History 完整性/迁移、Agent loop 禁止、工具 allow-list、永久禁止项、路径/秘密安全、并发/hash、前后端测试、M1–M6 回归、文档和保护文件 diff。审计不通过时只将阻断/重要问题回开发，最多两轮。

## 12. 计划完成定义

本文件已以 UTF-8 Markdown 写入 `/Users/ningyedong/Documents/LocalBook/PLAN-M7.md`。规划阶段没有创建实现代码，没有修改 `PLAN.md` 或 `PLAN-M1.md`–`PLAN-M6.md`。开发 Agent 必须先读取本文件，逐项执行并如实记录偏差；M7 任何写路径都必须可解释、可审阅、可回滚、可审计。
