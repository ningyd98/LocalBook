# server/recovery — M7 Transaction / Rollback / Undo（已实现）

PLAN-M7 §5.4/M7-07/M7-08。事务 = 预检（只读、无写）→ 每步写入前先持久化
journal → 逐动作经 `VaultService` 执行 → 已执行动作失败时按**逆序**以 hash guard 回滚并校验恢复结果；首个动作若预检或写入即失败，则没有已执行动作可回滚。

- `executor.py`：create/update/move 执行 + 已执行动作的逆操作回滚；回滚中遇外部修改
  → `rollback_failed`（冲突路径列出，绝不覆盖外部内容）。
- `undo.py`：UndoService 不调用模型，从 journal 恢复 before state；当前
  hash ≠ after hash → `undo_conflict`（409）；已 undo/无 journal →
  `undo_unavailable`（409）。create 的 undo = hash guard 删除；move 的
  undo = 移回源路径。
- 崩溃恢复 loop / Scheduler 属于 M8，本模块不做后台自动恢复。
