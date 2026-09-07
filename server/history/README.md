# server/history — M7 History（已实现）

派生审计数据（PLAN-M7 §5.5），存放于 M4 `.localnote/index.db` 的附加表
`ai_jobs` / `ai_job_journal`；随 `.localnote/` 删除可重建，Markdown 才是
唯一事实源——删除 History 后 Undo 不可用（UI 有提示）。

- `schemas.py`：`HistoryRecord`/`JournalEntry`（extra=forbid、大小上限、
  秘密脱敏 `redact_secrets`，不存 prompt/token/绝对 root）。
- `schema.py`：DDL 记录 + 幂等 `ensure_history_tables(conn)`。
- `repository.py`：参数化 upsert（不触发 FK 级联删 journal）、分页
  `start_time DESC, job_id DESC`、journal CRUD。
- `service.py`：History/分页/详情 DTO；repository-less 时有内存镜像（单测）。

Index rebuild 只清业务派生表，**不删除 History**；有专门回归测试证明。
