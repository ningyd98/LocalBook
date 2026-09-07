# server/policies — M7 Policy Engine（已实现）

纯程序、与 AI 完全解耦的规则引擎（PLAN-M7 §5.2/§9.1-1）：

- `rules.py`：动作白名单（只含 add_tags/remove_tags/add_link/create_note/
  patch_note/move_note）、每动作最低等级、硬预算与默认 protected 前缀
  （`.localnote`）；删除/附件覆盖/递归/Vault 外/源码路径**不可表示**。
- `engine.py`：`PolicyEngine.evaluate / evaluate_set`，输入只读
  `ActionFacts`，输出 `allow | deny | confirm` + `matched_rules[]` 证据；
  deny 优先，Level 1 合法动作 confirm，Level 2 默认关闭（`AUTO_ACTIONS`
  交集 + 显式配置才可能 allow，且仅 tag-only 单文件、无正文变更）。
- `errors.py`：M7 稳定错误码（policy_denied 403、invalid_action 400、
  invalid_action_output 502、job_state_conflict 409、history_limit_exceeded
  413 等），API 只回 `{"error":{...},"meta":{...}}`。

引擎不 import AI/workflow、不读文件、不写 History；facts 由上层只读预检
采集，任何写仍需用户确认或显式 Level 2 自动配置并通过后仍写 History。
