# LocalNote Server M8 详细实施计划：Scheduler、可靠性强化与部署选项

> 阶段：M8。本文件是 M8 开发 Agent 的历史实施依据；当前实现状态以
> README、`docs/` 与代码/测试为准。
> 本规划阶段只创建本文件；不得创建实现代码，不得修改 `PLAN.md`、`PLAN-M1.md`、`PLAN-M2.md`、`PLAN-M3.md`、`PLAN-M4.md`、`PLAN-M5.md`、`PLAN-M6.md` 或 `PLAN-M7.md`。
> M8 建立在已实现的 M1–M7 之上，目标是完成 LocalNote 的本地 MVP 闭环：定时任务只负责何时触发，业务仍由 M7 受控 Job、Policy、Diff、确认/白名单、Transaction、History、Undo 边界负责。

## 1. 目标与可验收成功标准

### 1.1 总目标

新增一个可停止、轻量、单进程、本地优先的 Scheduler，并将 Daily Organizer、Weekly Review（以及可选的定时 index 校验/rebuild）接入已有 M7 Job 流程。Scheduler 不实现业务、不直接触碰 Vault/SQLite、不绕过 Policy，不改变现有手动 `POST /api/v1/jobs` 行为。强化重启诊断、并发/超时/幂等、History retention 和索引一致性；提供显式局域网监听选项、CORS 配置与安全告警，但不实现账号体系或 HTTPS。

### 1.2 M8 全部成功标准（必须全部满足）

1. `SchedulerSettings` 具备 `enabled`、时区、Daily/Weekly cron 或 interval、job timeout、并发策略、index 校验开关、Level 2 自动执行开关/白名单、History retention/清理周期等嵌套配置；默认 `enabled=true`，但 Daily/Weekly 默认只生成 Level 1 preview，不自动写。
2. Scheduler 明确只承担 schedule/dispatch：注册稳定 job ID、计算下一次运行、触发一次受控 handler；不包含 Agent prompt、Action、文件写入、Policy 判定或 History 业务逻辑。
3. 默认任务至少包括：`daily_organizer`（默认每天 23:00）、`weekly_review`（默认周日 20:00）；可选 `index_consistency`（定期校验，发现不一致时可显式 rebuild，默认不因校验失败自动修改正文）。任务均可手动触发。
4. 每次 Daily/Weekly 定时执行都等价于 M7 的 `AgentJobService.plan`：受控 Agent 一次模型调用 → 严格 ActionSet → Policy → Diff → 默认 `awaiting_confirmation`；只有显式配置且命中 Level 2 tag-only 白名单时才可自动 `accept`。自动执行仍重新 preflight、写 journal、经 VaultService、验证并记录 History。
5. 不出现“定时任务直接写 Vault”“定时任务绕过 M7”“定时 Level 1 自动写”等路径；定时默认只生成/预览，服务重启不自动恢复写入。
6. 每个 scheduler run 有可追踪的 run/job 标识、状态、开始/结束时间、错误分类、触发来源和幂等键；同一任务未结束时不重入，超时后可标记 failed/recovery_required 并释放调度槽。
7. 启动时扫描 M7 未完成 job/journal，标记 `recovery_required` 或 `failed` 并提供诊断/显式恢复入口；绝不因启动扫描自动写、自动 rollback 或自动 accept。恢复操作必须用户/显式 API 发起并重复 hash guard。
8. History retention 清理按配置执行、只删除派生 History/journal，不删除 Vault 文件、不影响 health/Vault/editor；删除后明确不可 Undo。陈旧 scheduler run/job 有有界清理策略。
9. Scheduler 停止、关闭、依赖不可用、任务失败均不阻塞 health、Vault、editor、search、graph、AI status、History 查询和手动 Job；shutdown 有界等待并取消/标记运行中任务。
10. 局域网监听默认仍为 `127.0.0.1`；仅当显式 `LOCALNOTE_HOST=0.0.0.0`（或嵌套 server host）且设置明确 CORS 白名单时允许启动/运行，并在日志、README、状态 API/UI 显示暴露告警。不得添加认证、账号、HTTPS。
11. 提供 `GET /api/v1/scheduler/status`、`POST /api/v1/scheduler/run/{task}`、
`GET /api/v1/scheduler/runs` 与 `POST /api/v1/scheduler/recovery/{run_id}`；
手动触发与定时触发完全共用 handler 和 M7 Policy/History 流程。
12. 前端（可选但建议）显示 Scheduler enabled/running/stopped/degraded、next run、last result/告警，并复用 `OrganizerActions` 触发 Daily/Weekly；不在前端实现定时器或自动写逻辑。
13. 新增测试只使用 fixtures、`tmp_path`、fake adapter/fake clock/fake scheduler/fake Vault，绝不触碰真实用户 Vault、真实 oMLX、云/NAS。M1–M7 既有门禁不回退（历史基线约后端 606、前端 114；当前测试数量以实际命令为准）。
14. `pytest`、Vitest、protocol/workspace/web typecheck、web build、`compileall`、`scripts/check.sh` 全通过；文档与配置示例可按命令复现。

## 2. 现状基线与实施前检查

### 2.1 已实现边界（M8 必须复用）

- `server/config.py` 已有 pydantic-settings、`LOCALNOTE_` 前缀、`__` 嵌套分隔；当前 `SchedulerSettings(enabled=True)` 已提供 M8 配置，默认任务仍以 preview 为安全行为。
- `server/api/main.py` lifespan 顺序为 Vault lifecycle → SQLite `.localnote/index.db` → rebuild → watcher；已注册 jobs/history 路由、M7Error handler、CORS middleware。Scheduler 应在 index/Vault/Agent DI 就绪后启动，在 shutdown 前停止。
- `server/api/dependencies.py` 已构造 `AgentJobService`、`HistoryRepository/Service`、Policy、adapter、ToolContext；应增加 scheduler/status/runner 的 app-state 单例依赖，禁止每个请求新建后台 scheduler。
- `server/agents/service.py` 的 `plan()` 已包含 ActionSet、scope、preflight、Policy、Diff、History，并在 `request.execute` 且 Policy `allow` 时执行；M8 应调用其公共受控入口或添加明确的 `trigger_scheduled()` 薄包装，不复制执行代码。
- `server/agents/workflows.py` / `registry.py` 已限制 Daily/Weekly 一次模型请求、只读工具、无 loop；定时触发不得扩大工具或 prompt 权限。
- `server/policies/engine.py` 已将 Level 1 判为 `confirm`，Level 2 只在配置 tag actions 中 `allow`；Scheduler 只能读取该结果，不能将 confirm 改成 allow。
- `server/recovery/{journal,executor,service}.py` 已有写前 journal、逆序 rollback、hash guard；M8 增强未完成状态扫描/诊断/显式恢复，不自动猜测恢复意图。
- `server/history/service.py`/repository 已将 History/journal 存于 `.localnote/index.db` 派生表；M8 增加 retention/按时间清理和 scheduler run 记录，不能破坏 Undo 查询语义。
- `server/index/db.py` 为单连接、RLock、WAL、显式事务；重建是可删除派生数据的原子操作。校验/rebuild 必须复用 `DerivedIndexService` 和既有锁。
- `server/vault/service.py` 是唯一 Vault FS 门面，具备 mutation `RLock`、原子写、expected hash、watcher；Scheduler/handler 不得 `open/pathlib` 旁路。
- 前端已有 `OrganizerActions`、AIHistoryPanel、API client/types、workspace store；新增 Scheduler UI 必须渐进式复用，不重做 M7 UI。

### 2.2 实施前检查与保护

开发 Agent 必须先从仓库根读取本计划及用户指定文件，并执行、记录每条命令退出码：

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
git diff -- PLAN.md PLAN-M1.md PLAN-M2.md PLAN-M3.md PLAN-M4.md PLAN-M5.md PLAN-M6.md PLAN-M7.md
```

若 `pnpm install` 或既有基线失败，先记录并区分基线问题，不删除/放宽旧测试，不修改保护计划。确认当前 Python 依赖是否已安装 APScheduler；检查 `uv.lock` 是否需要更新。测试只能复制 `tests/fixtures/vault` 到 `tmp_path` 或自行构造临时 fixture。

## 3. 范围、任务策略与不做项

### 3.1 任务触发语义

- Scheduler 的职责是“何时执行”：调度器在触发时间调用 `JobRunner.run(task, trigger)`。
- JobRunner 的职责是“执行哪条既有业务链”：构造受控 M7 `JobRequest`，调用 `AgentJobService.plan`；根据配置决定只预览或对策略明确允许的 Level 2 结果调用 M7 accept。
- Scheduler 任务不保存 prompt，不接受用户传入任意 prompt/URL/shell，不直接调用工具/FS/SQL。
- 每次触发只生成一个 run；任务超时、异常、重复均记录可审计结果，不影响下一次任务和手动流程。

### 3.2 默认权限策略

| 场景 | 默认权限 | 默认结果 | 可自动写条件 |
|---|---:|---|---|
| Daily 定时 | Level 1 | 生成 Diff，`awaiting_confirmation` | 仅显式开启 Level 2，且所有动作均为配置白名单 tag-only、单文件、预算内 |
| Weekly 定时 | Level 1 | 生成 Diff，`awaiting_confirmation` | 同上；`create_note`/`patch_note`/`add_link`/`move_note` 永不 Level 2 自动 |
| 手动 `/scheduler/run/{task}` | 请求指定但服务端夹紧 | 默认 preview | 若请求 `auto=true` 也必须命中服务器配置和 Policy；客户端不能强行提升权限 |
| index consistency | 只读 | status/diagnostic | 可配置 rebuild 仅写派生 index，不写 Markdown，且不阻塞手动功能 |

### 3.3 M8 明确不做

不实现云/NAS/远程同步、云队列/Redis/Celery/Postgres、多副本/分布式锁、微服务、账号/鉴权体系、HTTPS/TLS、远程暴露默认开启、真实用户 Vault 操作、真实 oMLX 集成、Agent loop/递归工具调用、任意定时正文重写、定时删除/附件覆盖、自动恢复写入、自动恢复未知冲突、WebSocket、系统睡眠唤醒保证、任务编排 DSL、用户自定义任意 Python/shell job。Level 2 仍只允许极小 tag-only 白名单。

## 4. 有序实施任务清单

开发必须按下列顺序推进；每项均需满足依赖、产出和完成定义后再进入下一项。

| 编号 | 任务 | 依赖 | 产出 | 完成定义 | 主要文件 |
|---|---|---|---|---|---|
| M8-01 | 基线、依赖与契约勘验 | M7 | 基线报告、APS/asyncio 决策、现有状态清单 | 读取指定文件；确认 APScheduler 可安装/锁定，或记录 asyncio 降级；保护文件零 diff | `pyproject.toml`, `uv.lock`, `server/*`, `tests/*` |
| M8-02 | Scheduler 配置与错误枚举 | 01 | 完整 Settings、校验、稳定错误码 | 嵌套 env、默认安全、cron/interval/时区/上限校验；不泄漏路径/环境变量 | `server/config.py`, `server/scheduler/errors.py`, tests |
| M8-03 | 调度抽象与任务注册 | 02 | SchedulerBackend、JobDefinition、Clock/trigger adapter | start/stop 幂等、稳定 IDs、next run、max instances=1、misfire/coalesce 明确；backend 可 fake | `server/scheduler/{backend,models,registry,clock}.py`, tests |
| M8-04 | APScheduler/asyncio backend | 03 | 本地后台调度实现 | APScheduler `BackgroundScheduler`（首选）使用 timezone-aware cron；依赖不可用时受控 asyncio loop fallback；无业务代码 | `server/scheduler/{apscheduler_backend,asyncio_backend}.py`, `pyproject.toml`, tests |
| M8-05 | M7 Scheduled JobRunner | 02、03 | 任务触发→M7 Job 适配器 | Daily/Weekly 默认 preview；Level2 需配置+Policy allow；统一 idempotency/run metadata；无复制写逻辑 | `server/scheduler/runner.py`, `service.py`, tests |
| M8-06 | History scheduler run 与 retention | 02、05 | run 状态表/DTO、清理服务 | 记录 trigger/status/error/started/finished/next; retention 清理有界、保留 active/recovery；History 删除后 Undo 明确不可用 | `server/history/{schema,repository,service,schemas}.py`, `server/index/schema.py`, tests |
| M8-07 | 崩溃恢复诊断与显式恢复 | 03、06、M7 recovery | startup scanner、recovery_required 状态/API | 扫描 pending journal/非终态 job；只标记 failed/recovery_required，不写、不自动 rollback；显式恢复 hash guard | `server/recovery/{scanner,service,schemas}.py`, `server/agents/service.py`, tests |
| M8-08 | 锁、超时、幂等、清理 | 03、05、06 | job lock、run lease、timeout/cancel、stale cleanup | 同 task 不重入；重复 trigger 返回 existing/duplicate；超时有界；陈旧记录清理不删 active | `server/scheduler/{locks,runner,cleanup}.py`, tests |
| M8-09 | Index consistency job | 03、M4 | 定时只读校验/可选 rebuild | 校验 hash/count/ready；配置允许时复用 rebuild；任何故障只影响 index，health/Vault/editor 仍可用 | `server/scheduler/index_job.py`, `server/index/service.py`, tests |
| M8-10 | FastAPI lifespan/DI/API | 05–09 | 状态和触发端点 | 启动/停止顺序可靠；scheduler disabled/stopped 不影响既有路由；错误映射固定 | `server/api/{main,dependencies}.py`, `server/api/routes/scheduler.py`, tests |
| M8-11 | Protocol/client/workspace | 10 | TS DTO、API client、状态 slice | DTO 与 OpenAPI 对齐，竞态守卫，网络错误安全；不在前端调度 | `packages/protocol/src/index.ts`, `apps/web/src/api/{types,client}.ts`, `packages/workspace/src/*` |
| M8-12 | Scheduler/Organizer UI（建议） | 11 | 状态指示、手动 Daily/Weekly、预览链接 | stopped/disabled/degraded/next/last/error/loading/empty 清晰；复用 M7 History/Diff/Confirm | `apps/web/src/components/{SchedulerStatus,OrganizerActions}.tsx`, `App.tsx`, `styles.css` |
| M8-13 | 全矩阵测试与安全审计 | 02–12 | 后端/前端/隔离测试 | fake clock、fake backend、fake adapter/Vault；M1–M7 测试全绿；无真实网络/用户 Vault | `tests/backend/test_m8_*.py`, `tests/frontend/*M8*.test.*` |
| M8-14 | 文档、部署脚本、验收和收尾 | 全部 | README/architecture/AI 文档/roadmap/示例 | 配置、局域网告警、启动停止、恢复限制、禁止项、验收命令完整；M8 MVP 闭环声明可核验 | `README.md`, `docs/{architecture,ai-architecture,development-roadmap}.md`, `scripts/dev.sh`（仅必要 additive） |

## 5. 技术方案与关键设计决策

### 5.1 APScheduler 优先，asyncio 为受控降级

M8-01 先在当前 Python 3.12 环境验证 `APScheduler` 可用性和锁文件变更。首选 APScheduler 3.x `BackgroundScheduler`：单进程、线程后台、`CronTrigger`/`IntervalTrigger`、timezone 支持、`max_instances=1`、`coalesce=True`、`misfire_grace_time` 已满足本地任务需求。任务函数只提交给 JobRunner，不做业务。

抽象为：

```python
class SchedulerBackend(Protocol):
    def start(self) -> None: ...
    def stop(self, *, wait: bool = True, timeout: float = ...) -> None: ...
    def add(self, definition: JobDefinition) -> None: ...
    def remove(self, job_id: str) -> None: ...
    def status(self) -> SchedulerStatus: ...
    def run_now(self, job_id: str) -> RunHandle: ...
```

如果 APScheduler 无法在受支持的本机环境安装，采用 `asyncio` 单一后台 task + `asyncio.Event` + injectable clock；不得在运行期静默安装依赖，也不得同时启动两个 backend。启动日志/status 必须显示 `backend=apscheduler` 或 `backend=asyncio` 及 degraded reason。asyncio fallback 只提供同一抽象的 cron/interval 计算（可用标准库 zoneinfo），并使用 monotonic deadline；不宣称跨休眠精确唤醒。

### 5.2 任务注册、时区与触发

静态注册三个定义：

- `daily_organizer`：默认 `0 23 * * *`，timezone `scheduler.timezone`。
- `weekly_review`：默认 `0 20 * * 0`（周日），timezone 同上。
- `index_consistency`：默认关闭或 interval 24h；只读校验，若 `index.auto_rebuild=false` 不自动 rebuild。

所有 cron 字段先做严格范围校验，拒绝任意 Python 表达式；`zoneinfo.ZoneInfo` 无法加载时配置错误或回退 UTC（建议启动失败为配置错误，不猜时区）。记录 `scheduled_for`、`started_at`、`finished_at` 均使用 UTC ISO 时间，同时返回配置时区下的 next run。系统休眠/电脑关机期间任务可能错过：`coalesce=true` 最多补一次；错过超过 `misfire_grace_seconds` 则跳过该槽位，不创建 `missed` run 行，绝不补发大量历史任务。

### 5.3 定时任务接入 M7 Job

数据流固定为：

```text
Scheduler trigger
  → JobRunner.acquire(task, idempotency_key)
  → build bounded JobRequest(task_type, permission_level, scope, execute=False)
  → AgentJobService.plan()
  → one controlled Agent call (Daily/Weekly) or seeded/fake action
  → ActionSet validation
  → preflight + Policy
  → Diff + History(jobs + scheduler run)
  → Level 1: awaiting_confirmation, no write
  → explicit user accept via existing /jobs/{id}/accept
  → re-preflight → journal → TransactionExecutor → VaultService → validate → History
  → optional Undo via existing /history/{id}/undo
```

若配置 `level2_auto_enabled=true`，Runner 仍先以 Level 2 请求调用 M7；只有 M7 Policy 返回 `allow`、每个 action 属于服务器白名单（默认仅 `add_tags`/`remove_tags`）、单文件、无正文变化、无 overwrite、预算内，才调用现有 accept/execute。任何 `confirm`、`deny`、非白名单动作都停止在 preview/failed，不降级绕过。Weekly 的 `create_note` 永不自动执行。

Runner 不接受 scheduler HTTP 请求中的任意 actions、prompt 或 permission escalation；任务范围来自受限配置/固定 scope builder（默认最大文件数和字符数复用 M7 上限）。手动触发只改变 `trigger=manual`，其余链路完全相同。为了兼容当前 `AgentJobService.plan` 的 `execute` 语义，优先新增一个内部明确命名的受控入口或在 Runner 中先 `execute=False`，再调用既有 accept；不得复制 `_execute_locked`。

### 5.4 运行状态、锁、超时和幂等

建议 DTO：

```python
class SchedulerRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PREVIEWED = "previewed"
    COMMITTED = "committed"
    SKIPPED_DUPLICATE = "skipped_duplicate"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    RECOVERY_REQUIRED = "recovery_required"

class JobDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: Literal["daily_organizer", "weekly_review", "index_consistency"]
    trigger: Literal["cron", "interval"]
    expression: str
    timezone: str
    enabled: bool = True

class SchedulerRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: UUID
    task: str
    trigger: Literal["scheduled", "manual", "startup"]
    idempotency_key: str
    status: SchedulerRunStatus
    agent_job_id: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    error_code: str | None = None
    message: str | None = None
```

进程内使用每 task 一把 `threading.Lock`/asyncio lock；数据库记录 `task + scheduled_for` 作为审计字段；它不是唯一约束。重复触发在锁外先查 active run，按实现返回已有 run 或追加 `skipped_duplicate`，不宣称跨进程幂等。超时用 `scheduler.job_timeout_seconds`，标记 timed_out/recovery_required；不能强杀正在写的线程，shutdown 等待 bounded timeout 后只做诊断。任务执行中的 M7 transaction 仍由 Vault/History hash guard 保护。

### 5.5 崩溃恢复与显式诊断

启动后在 scheduler start 前运行轻量 `RecoveryScanner`：查 `ai_jobs` 非终态（`planned/preflighted/captured/executing/validating`）和 journal `pending/applied`，以及 `scheduler_runs` 的 running/queued。将不确定状态标为 `recovery_required`，记录固定原因 `process_interrupted`，并释放 scheduler lease；不自动 accept、rollback、重跑模型或写 Vault。扫描失败只记 `recovery_scan_failed`，不阻塞 health。

增加只读诊断（可合并在 scheduler status）和显式恢复 API（若实现）：`POST /api/v1/scheduler/recovery/{run_id}`，body 明确 `action: diagnose|retry_preview|rollback_if_safe`。默认只允许 `diagnose`；`retry_preview` 创建新 run，不复用旧 idempotency；`rollback_if_safe` 必须由 RecoveryService 使用每个 after hash 检查后执行，遇外部变化返回 conflict，绝不覆盖。所有动作记录 History/run。

### 5.6 History retention 与陈旧清理

扩展 `HistorySettings`：

```python
retention_days: int = Field(30, ge=1, le=3650)
cleanup_enabled: bool = True
cleanup_interval_hours: int = Field(24, ge=1, le=168)
max_scheduler_runs: int = Field(1000, ge=100, le=100000)
```

扩展 `SchedulerSettings`：

```python
enabled: bool = True
timezone: str = "UTC"
daily_cron: str = "0 23 * * *"
weekly_cron: str = "0 20 * * 0"
index_check_enabled: bool = False
index_check_interval_hours: int = 24
level2_auto_enabled: bool = False
level2_auto_actions: list[Literal["add_tags", "remove_tags"]] = []
job_timeout_seconds: float = Field(300, gt=0, le=3600)
misfire_grace_seconds: int = Field(3600, ge=0, le=86400)
max_instances: int = Field(1, ge=1, le=1)
coalesce: bool = True
stale_run_after_seconds: int = Field(3600, ge=60, le=604800)
```

具体 retention 上限以上述默认/范围为实施建议；开发若发现现有 History 数据规模或产品要求不合适，必须在偏差报告中给出理由并同步测试。清理只删除 `finished_at < now-retention` 且为终态的 `ai_jobs`/journal/run；保留 `awaiting_confirmation`、`recovery_required`、active 和最近一条每任务记录。清理操作自身有 lock、分页上限、事务和日志；不删除 Markdown、不触 Vault。

### 5.7 Index 一致性

新增 `IndexConsistencyChecker`，只通过 `VaultService.list/read` 与 `DerivedIndexService` 公共查询比较受限计数/hash/路径集合，避免读取 `.localnote/index.db` 的旁路 SQL。输出 `{ready, checked, mismatches, degraded, duration_ms}`。默认不自动 rebuild；若 `index.auto_rebuild=true` 且 mismatch 明确，可调用既有 `rebuild()`（派生数据单事务、复用 index lock），并记录 `index_rebuilt`。校验/rebuild 失败返回 scheduler run failed/index_unavailable，不影响 health/Vault/editor/AI/manual jobs。watcher 与 rebuild 已有锁；M8 只增加调度互斥，不修改正文事实源。

### 5.8 Lifespan、DI、停止顺序与部署

启动顺序：设置 `app.state.settings` → Vault/Index lifecycle → 构造 Agent/History services → RecoveryScanner（诊断）→ 构造并注册 Scheduler → 若 enabled 则 start；任一 scheduler 初始化错误记录 degraded 并继续提供 API。shutdown 顺序：先 stop 接受新触发 → bounded wait/cancel queued scheduler callbacks → flush/close History/Index/Vault（沿用现有 lifecycle）。Scheduler stop 必须幂等，且不能调用 `sys.exit`。

`LOCALNOTE_HOST` 仍默认 `127.0.0.1`；只有显式 host `0.0.0.0` 才作为局域网模式。建议启动日志和 `/scheduler/status` 返回 `network_exposure_warning=true`；当 host 非 loopback 且 CORS 为空、`*` 或不明确时，记录固定警告并建议设置 `LOCALNOTE_SERVER__CORS_ORIGINS='["http://<trusted-lan-client>:5173"]'`。不实现认证；README 必须明确任何同网设备可调用写 API，需防火墙限制、不要暴露公网。CORS 不允许凭据通配；沿用 `allow_credentials=False`。

## 6. 公共 API、错误、DTO 与示例

### 6.1 REST API

新增：

```http
GET  /api/v1/scheduler/status
POST /api/v1/scheduler/run/{task}
GET  /api/v1/scheduler/runs?limit=20&offset=0&task=daily_organizer
POST /api/v1/scheduler/recovery/{run_id}   # 可选，默认仅 diagnose
```

`task` 只允许 `daily_organizer|weekly_review|index_consistency`；unknown task 为 400。`run` body 建议：

```json
{
  "confirm": false,
  "auto_level2": false,
  "scope": {"paths": [], "max_files": 10, "max_chars": 60000}
}
```

服务端忽略/拒绝非法 scope、`auto_level2=true` 不能打开配置开关；Level 1 `confirm` 只表示允许生成 preview，不表示允许写。对已有 M7 pending job，用户仍调用 `POST /api/v1/jobs/{id}/accept`。

status 响应示例：

```json
{
  "enabled": true,
  "running": true,
  "backend": "apscheduler",
  "timezone": "Asia/Shanghai",
  "network_exposure_warning": false,
  "jobs": [
    {"id":"daily_organizer","enabled":true,"next_run_at":"2026-08-17T23:00:00+08:00","last_status":"previewed"},
    {"id":"weekly_review","enabled":true,"next_run_at":"2026-08-23T20:00:00+08:00","last_status":"previewed"}
  ],
  "active_runs": 0,
  "recovery_required": 0,
  "history_retention_days": 30
}
```

手动触发：

```bash
curl -fsS -X POST http://127.0.0.1:3780/api/v1/scheduler/run/daily_organizer \
  -H 'content-type: application/json' \
  -d '{"confirm":false,"scope":{"paths":[],"max_files":10,"max_chars":60000}}'
```

返回：

```json
{
  "run_id":"…",
  "task":"daily_organizer",
  "status":"previewed",
  "trigger":"manual",
  "agent_job_id":"…",
  "policy":{"decision":"confirm","matched_rules":["level1_requires_confirmation"]},
  "next_run_at":"…"
}
```

### 6.2 稳定错误枚举

建议新增 `SchedulerErrorCode`：`scheduler_disabled`(409/503，推荐 409)、`scheduler_unavailable`(503)、`unknown_task`(400)、`invalid_schedule`(400)、`duplicate_run`(409)、`job_timeout`(504)、`job_in_progress`(409)、`recovery_required`(409)、`recovery_not_safe`(409)、`scheduler_config_invalid`(400)、`history_cleanup_failed`(503)、`index_check_failed`(503)、`network_exposure_warning`（状态告警，不作为阻断错误）。错误沿用 `{error:{code,message,path},meta:{run_id?,task?}}`，不返回绝对 root、prompt、token、堆栈或上游异常。

### 6.3 数据流与状态关系

Scheduler run 状态和 M7 `JobStatus` 分开：scheduler `previewed` 对应 M7 `awaiting_confirmation`；只有 M7 `committed` 后 scheduler 才为 `committed`；Policy deny/AI offline/timeout 分别映射 failed 的安全 meta。History 同时保存 `trigger=scheduled/manual`、scheduler run ID 和 M7 job ID，详情可跳转已有 Diff/Undo。

## 7. 目录树与文件用途

```text
server/
  scheduler/
    __init__.py          # 公共导出
    models.py            # JobDefinition/Run/Status DTO
    errors.py            # M8 稳定异常与错误码
    clock.py             # timezone-aware injectable clock
    registry.py          # 静态 daily/weekly/index 任务定义
    backend.py           # SchedulerBackend Protocol
    apscheduler_backend.py # BackgroundScheduler 适配
    asyncio_backend.py   # APScheduler 不可用时的受控降级
    runner.py            # lock/idempotency/timeout/JobRunner
    service.py           # status/start/stop/run API facade
    locks.py             # process-local task lock/lease
    cleanup.py           # stale run/retention trigger
    index_job.py         # index consistency handler
  recovery/
    scanner.py           # startup non-terminal job/journal diagnostics
    service.py           # existing M7 recovery boundary additive extension
  history/
    schemas.py/repository.py/service.py # retention + scheduler run records
  api/routes/scheduler.py # thin HTTP orchestration only

packages/protocol/src/index.ts       # scheduler/run/error DTO mirror
apps/web/src/api/{types,client}.ts   # fetch status/run/list runs
packages/workspace/src/*              # scheduler slice/status race guard (if needed)
apps/web/src/components/
  SchedulerStatus.tsx                  # status/next run/warning
  OrganizerActions.tsx                 # reuse Daily/Weekly trigger
apps/web/src/App.tsx                  # composition only
apps/web/src/styles.css               # states/accessibility

tests/backend/test_m8_config.py
 tests/backend/test_m8_scheduler_backend.py
 tests/backend/test_m8_runner.py
 tests/backend/test_m8_m7_integration.py
 tests/backend/test_m8_recovery.py
 tests/backend/test_m8_retention.py
 tests/backend/test_m8_index_consistency.py
 tests/backend/test_m8_api.py
 tests/backend/test_m8_isolation.py
tests/frontend/SchedulerStatus.test.tsx
 tests/frontend/M8ClientContract.test.ts
 tests/frontend/M8Isolation.test.tsx
```

若 APScheduler 使用第三方导入，需在 `pyproject.toml`/`uv.lock` 明确版本上限；若依赖无法锁定，保留 asyncio backend 并在报告说明，不新增网络服务。

## 8. 测试矩阵与验收命令

### 8.1 后端测试矩阵

| 维度 | 必测场景 |
|---|---|
| 配置 | 默认 enabled=true；nested `LOCALNOTE_SCHEDULER__*`；flat host；cron/interval/timezone 非法；Level2 默认关闭；白名单只能 tag actions；retention 边界 |
| 启停 | start/stop/stop twice；disabled 不注册任务；backend unavailable degraded；shutdown 有界；手动 API 在 stopped 时返回安全状态 |
| 时间 | fake clock 触发 daily 23:00、weekly Sunday 20:00；时区/DST；misfire/coalesce；系统休眠模拟不补发多次 |
| 注册 | 稳定 job IDs；next run；不重复注册；max_instances=1；同 task 并发只一条 run |
| M7 接入 | Daily/Weekly fake adapter 一次调用；ActionSet schema；Level1 preview/confirm；Level2 空白名单不写；tag-only 白名单可 auto；create/patch/link/weekly note 永不 auto；Policy deny 仍 deny |
| 事务 | auto Level2 仍 journal→VaultService→History；fake 第 N 步失败逆序 rollback；手动 accept/undo 契约不变 |
| 幂等/超时 | 相同 idempotency key；active duplicate；fake clock timeout；stale lease；新一轮可运行；不重复模型调用 |
| 崩溃 | pending journal/非终态 job/run 启动扫描为 recovery_required；不写、不 rollback、不调用模型；诊断/显式安全恢复与 hash conflict |
| retention | 终态过期清理；保留 active/awaiting/recovery；journal 一并处理；删除后 Undo unavailable；Vault bytes/hash 不变 |
| index | 一致/不一致/不可用；可选 rebuild；定时失败不影响 health/Vault/editor/search/graph/AI/manual jobs |
| 部署 | loopback 默认；0.0.0.0 警告；显式 CORS 预检；`*`/credentials 不放行；不实现 auth/HTTPS |
| 隔离 | monkeypatch 禁止网络、真实路径、subprocess；所有 Vault 为 tmp fixture，AI 为 fake adapter |

### 8.2 前端测试矩阵

- client URL/body/错误 DTO：status、run、runs；unknown task/409/503 显示稳定 message。
- SchedulerStatus：enabled/running/stopped/degraded、next run、last run、recovery/network warning、loading/empty/error。
- OrganizerActions：Daily/Weekly 仅调用 API，默认 `execute=false`/preview，不在组件中自动 accept。
- 与 M7 History/Diff/Confirm 集成：preview 后可打开已有 job detail，Level1 accept 保持原交互。
- isolation：无 `setInterval`/`setTimeout` 业务调度、无 FS/window secret、无直连 oMLX。

### 8.3 最终验收命令

```bash
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

可选性能/时序 smoke 必须显式环境变量开启，且仅生成 tmp fixture；不得因真实系统时间或真实 oMLX 导致门禁不稳定。验收还应运行一次受控 smoke：fake clock 触发 Daily → 得到 `awaiting_confirmation` → fake accept → History committed → fake undo，确认 Markdown 原始 bytes/hash 正确；再 stop scheduler，确认 health/Vault/search/graph/AI/history/manual job 仍可访问。

## 9. 依赖、风险、降级与明确假设

### 9.1 风险与应对

- **APScheduler 依赖/锁文件**：优先锁定兼容 Python 3.12 的版本；不可安装则单一 asyncio fallback，显示 degraded，不静默安装。
- **定时精度/系统休眠/电池**：本地进程无法保证关机期间执行；coalesce 最多补跑一次，超出 grace 的槽位直接跳过，文档说明不提供唤醒保证。
- **并发/线程与 SQLite 单连接**：每 task max one、Runner lock、复用 M7/Index RLock；跨进程不保证，M8 不做分布式锁。
- **崩溃窗口**：写前 journal 已有基础；启动只诊断，外部 hash 冲突时不覆盖；显式恢复失败必须可审计。
- **时区/DST**：使用 `zoneinfo`、存 UTC、返回本地 offset；非法时区配置在 Settings 阶段报错。
- **History 膨胀**：retention 有上限、分页清理、journal cap 复用 M7；保留 recovery/pending 以免破坏诊断。
- **AI offline/慢模型**：Runner timeout/failed；预览不自动重试，不影响核心 API。
- **局域网暴露**：默认 loopback；0.0.0.0 明确警告、CORS 显式白名单、防火墙建议；无 auth 属已知限制。
- **watcher/index race**：复用既有 index/vault locks；校验不是事实源，rebuild 仅派生。
- **配置误开 Level2**：白名单由 Settings validator 与 Policy hard set 双重限制；UI 不提供绕过开关。

### 9.2 明确假设

1. 当前 M7 Job API/DTO 语义保持兼容，M8 只 additive 增加 scheduler metadata/status。
2. Scheduler 是单 FastAPI 进程单实例；多进程 uvicorn workers 不在支持范围，文档需警告不要这样部署。
3. 默认时区暂定 UTC（或开发按产品环境指定）；Daily/Weekly 默认时间可配置，示例使用 23:00/周日 20:00。
4. 默认定时任务只生成 Level 1 preview；Level2 auto 必须显式配置 `enabled`、action whitelist 和正预算。此为 M8 安全默认。
5. History retention 默认 30 天、最大 scheduler run 1000；若审计/产品要求调整，必须更新配置校验、文档和测试，不静默扩大。
6. `scripts/dev.sh` 继续负责启动/优雅清理；不另起 server，不要求前端自行运行 scheduler。

### 9.3 未决事项（实施前确认并记录）

- APScheduler 是否能在当前本机和锁文件稳定安装；若不能，确认 asyncio fallback 的 cron 解析实现和降级文案。
- 产品是否接受默认 Level1 preview（本计划推荐），以及是否首发开放任何 Level2 auto；建议首发关闭。
- 默认时区到底为 UTC 还是部署机器本地时区；建议配置默认 UTC，文档要求显式设置本地时区。
- History retention 是否为 30 天、scheduler run 上限是否为 1000；建议先采用上述有界默认。
- `index_consistency` 是否默认启用；建议默认关闭，仅在启用时执行只读校验，auto rebuild 继续关闭。
- 是否实现 `GET /scheduler/runs` 与显式 recovery endpoint；若不实现，status 必须至少提供 active/recovery/last run 诊断。

## 10. M8 收尾定义：产品 MVP 闭环

M8 完成的验收口径是：本地服务启动后 Scheduler 可见、可停止、可手动触发；Daily/Weekly 能生成受 M7 Policy 约束的 Diff/History，Level 1 由用户确认后执行，显式 Level2 仅 tag-only 白名单可自动执行；重启不会自动写，未完成事务进入 recovery_required 并可诊断；History 可按 retention 清理且核心功能不受影响；局域网开启有显式 CORS/暴露告警；所有 M1–M7 手动和只读能力继续工作。达到此口径即完成 LocalNote Server 的本地产品 MVP 闭环，而不是宣称生产级分布式调度或安全部署。

M8 最终报告必须列出：实际 backend 选择（APScheduler/asyncio）、新增依赖及版本、每个任务的完成状态、配置默认值、API/DTO 变更、测试数量和命令退出码、偏差/未决项、局域网风险告警、以及上述所有不做项。任何计划缺陷或实现偏差必须显式记录，不得静默重设计整体架构。
