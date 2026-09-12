# server/vault — M1 Vault Core（已实现）

唯一的 Vault 文件系统门面与生命周期：

- `path_safety.py` — 词法拒绝（`..`、绝对、NUL、反斜杠、drive/UNC）→
  resolve/containment → 逐级 lstat；保守策略拒绝一切用户可达 symlink。
- `service.py` — `VaultService`：list tree / read bytes / create / write
  （原子）/ delete / move；所有操作带 root-relative 字符串输入 + hash 冲突。
- `errors.py` — 领域异常 + 稳定错误 code（不依赖 FastAPI）。
- `schemas.py` — REST DTO / `RelativePath` / `Sha256` / `Base64Bytes` 校验。
- `atomic_write.py` — 同目录临时文件 + fsync + 原子替换/无覆盖创建。
- `derived.py` — `.localnote/` 目录 + `state.json` 占位（无 SQLite）。
- `events.py` / `watcher.py` — 归一化事件与 watchdog adapter（去抖、过滤、
  start/stop/flush；缺失时状态 `unavailable`）。
- `lifecycle.py` — app lifespan wiring（未配置不启动；root 缺失 → 503）。

详细契约见 [`docs/vault-spec.md`](../../docs/vault-spec.md)。
M2 入口：编辑器/Workspace 只准调用
`VaultService` / REST，不得直接触碰 FS。
