# server/agents — M7 受控 Agent（已实现）

有限、可审计的 Agent 场景（PLAN-M7 §5.6/M7-09）：**模型从不直接调用工具
或写文件**，只输出严格 JSON ActionSet；AgentJobService 负责
validate →（最多一次模型调用）→ 只读预检 facts → Policy → Diff（无写）→
用户接受/执行（TransactionExecutor）→ History/Undo。

- `registry.py`：静态工具元数据（名称/读或写/最低权限/IO schema/成本/
  允许 workflow）。只读工具：vault.list/read/search、metadata.get、
  knowledge.related/backlinks/outgoing/keyword；写工具
  note.create/patch/move、metadata.set、tag.add/remove、link.add **不可被
  模型 invoke**（registry 直接拒绝）。
- `tools.py`：读工具只经 Vault/Index/Metadata/Links/Graph 服务；写计划为
  纯函数（executor 专用）。
- `workflows.py`：Daily Organizer / Weekly Review 各最多一次 chat 调用 +
  一次 ActionSet，无 Agent loop；ActionSet 路径必须落在 scope/context
  allow-list 内。
- `service.py`：job 生命周期（plan/accept/reject/undo、stale-accept 409、
  journal 上限 413、undo 不调用模型）。
