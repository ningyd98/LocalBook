# LocalNote Vault 兼容性规范（M1 实现状态）

> 本文档是 **规范/契约 + M1 实现状态**。Phase 0 只声明了约束；M1 把第 2–5 节
> 落地为可运行的 `VaultService` 与 REST API（见 `PLAN-M1.md`）。任何未来实现
> 不得破坏这些保证。原文（Markdown + 附件）永远是唯一事实源。

## 1. 目标

用户 Vault 是一组以 Markdown 为主的普通文件夹，可能由 Obsidian 等其他工具
创建/共享。LocalNote 必须做到：

1. **不损坏**：读写绝不改写未知语法、frontmatter、链接、模板或附件引用。
2. **共存**：LocalNote 自己的派生数据放在 Vault 根下的 `.localnote/` 内，
   删除它不影响 Markdown/附件/正文。
3. **可迁移**：换工具、重装、删除派生目录都不丢正文。

## 2. Vault 根与相对路径规则（M1 已实现）

- 配置：`LOCALNOTE_VAULT__ROOT`（`VaultSettings.root`；扁平别名
  `LOCALNOTE_VAULT_ROOT` 兼容，嵌套优先）。空/空白 = 未配置 = `Not configured`，
  LocalNote 不启动任何 Vault 功能、不扫描也不创建目录；endpoint 返回 503
  `vault_not_configured`。
- 配置的 root **必须已存在且为目录**：LocalNote 从不替用户创建 root；配置了但
  缺失/不可用 → 503 `vault_unavailable`（不伪装成空树）。`.localnote/` 只在
  root 存在且可写时初始化（root 下创建失败 → `vault_unavailable`）。
- 所有访问都通过唯一的 `VaultService`，输入一律是 **root 相对 POSIX 路径**：
  - 词法校验先于一切 syscall：拒绝空串、`.`、`..`、绝对路径、NUL、反斜杠、
    Windows drive/UNC/设备前缀、空 segment、超长 segment/路径；
  - 再 `resolve()` 并与 root 的真实路径做 containment 比较；对已存在部分和
    所有父级逐级 `lstat`；
  - **安全默认：拒绝一切用户可达的 symlink**（即使目标仍在 root 内）——
    逃逸文件、逃逸目录、父链 symlink 一律 `symlink_escape`，不跟随、不读、
    不写、不列；
  - 越界/逃逸是**拒绝**（错误），不是规范化后放行；路由层永远拿不到用户
    提供的 `Path`，不存在绕过 `VaultService` 直接碰 FS 的代码路径。
- TOCTOU 最小防护：写前快照 hash、写临时文件、rename 前再次 lstat/realpath
  + 重读 hash、rename 后 post-check；删除/移动在 syscall 前二次 hash。
  纯 Python `Path` 实现无法消除同权限攻击者的全部竞态（文档化残余风险）；
  冲突检测不能替代 OS 权限。

## 3. Markdown / 附件保真（M1 已实现）

- M1 **没有 parser**：输入/输出统一为原始 `bytes`（JSON 走 `content_base64`），
  不解析、不规范化换行、不剥 BOM、不推断标题、不自动追加换行。
- UTF-8/LF/CRLF/无末尾换行/BOM/非 UTF-8/未知 Obsidian 语法/HTML/代码块/
  嵌入/脚注/emoji 全部逐字节 round-trip；hash 为文件原始 bytes 的 SHA-256
  （`sha256:<hex>`）。
- 附件按相同相对路径规则存取，内容不解析；扩展名不决定可读性（服务端只做
  `content_type` 展示）。
- 重复标题是内容问题：M1 不去重，两个同标题文件是两个独立 path/bytes。

## 4. `.localnote/` 派生目录约定（M1 已实现最小形态）

- root 下 `.localnote/`：M1 只创建目录 + `state.json` 占位
  （`{"format":"localnote-m1","initialized":true,"version":1}`）。
  **无 SQLite、无 schema、无 FTS、无正文副本**（M4 才引入索引库）。
- 特性：
  - **可删除重建**：删掉 `.localnote/` 不影响任何正文/附件；下次打开重建
    占位；正文/附件 hash 不变；
  - **不是备份**：正文永不依赖 `.localnote/` 中的副本；
  - 派生目录对 API 不可见也不可写（`include_hidden` 不能泄露/触达它）。
- 具体的 SQLite schema/migration/versioning 仍是 M4 未决事项。

## 5. Symlink / path traversal（M1 已实现并测试）

1. 拒绝绝对路径与 `..`（词法 → `400 path_traversal`）。
2. 拒绝指向 root 外部的 symlink（文件、目录、父链）→ `400 symlink_escape`；
   策略为拒绝 root 内 symlink（统一行为、文档化）。
3. resolve-after-validation + 操作前/后复核（lstat/realpath + containment +
   hash），残余 TOCTOU 风险已记录；`tests/backend/test_vault_paths.py` 用
   实际 symlink + 注入 hook 验证不越界读/写/删/移。
4. 附件路径规则与正文相同。
5. 跨平台差异：API 只接受 POSIX `/` 相对路径（反斜杠/盘符/UNC 一律非法）；
   macOS 大小写/NFD 行为不归一化、不假设；watchdog 平台差异见 §6。

## 6. 外部变更 Watcher（M1 已实现最小形态）

- watchdog 是锁定的 runtime 依赖；`server/vault/watcher.py` 仍带导入降级：
  缺失/启动失败时读写不受影响，状态为 `unavailable` 并记录诊断，绝不伪装
  已启用。状态枚举：`disabled` / `stopped` / `running` / `unavailable`。
- 事件归一化 `create|modify|delete|move`（move 带 old/new path），按
  `(kind, path)` / `(kind, old, new)` 在 `watcher_debounce_ms`（默认 200ms）
  窗口合并；`.localnote/` 与 root 外/不安全路径事件丢弃；回调异常被隔离。
- 生命周期：`start()` / `stop(timeout)` / `flush()`；shutdown 会 flush 并 join
  有界超时，不遗留线程。
- Watcher 只发通知、不写正文、不写索引（M4 才消费事件做索引/全量重建）。

## 7. 原子保存 / 冲突（M1 已实现）

- 更新时序：读当前 bytes+hash（快照 A）→ 校验 `expected_sha256` == A →
  同目录唯一临时文件（0600）写入+flush+fsync → rename 前重读源并比对
  （快照 B）→ `os.replace` 原子替换 → 可选父目录 fsync → 读回返回。
- 任何中间失败/冲突：临时文件清理，目标保持原 bytes/hash。
- **update/delete/move 一律要求 `expected_sha256`**：缺失 →
  `400 expected_hash_required`；不匹配/外部修改 → `409 file_conflict`，
  绝不静默覆盖或误删/误移。create 不允许覆盖（已存在 →
  `409 already_exists`），父目录必须已存在（服务不做递归 mkdir）。
- move/rename 不覆盖目标（已存在 → `already_exists`）、同文件系统
  （跨设备 → `atomic_write_failed`，禁止 copy-delete fallback）。
- Undo/History/Recovery（M7 的 diff/恢复编排）尚未实现。

## 8. REST 契约与错误体（M1 已实现）

前缀 `/api/v1/vault`，全部经 `get_vault_service` DI：

| 方法/路径 | 说明 |
|---|---|
| `GET /api/v1/vault/files?path=&recursive=&include_hidden=` | 树/列表（Unicode code point 排序；`.localnote` 永远过滤） |
| `GET /api/v1/vault/file?path=` | 读取：`content_base64` + `byte_length` + `sha256` + `content_type` |
| `POST /api/v1/vault/file` | create（201；重复 409；body 为空 = 空文件） |
| `PATCH /api/v1/vault/file` | update：必须带 `expected_sha256` |
| `DELETE /api/v1/vault/file` | delete：必须带 `expected_sha256` |
| `POST /api/v1/vault/file/move` | move/rename：必须带源 `expected_sha256` |

错误体固定为 `{"error":{"code","message","path"}}`，HTTP 映射：
400 `path_traversal` / `symlink_escape` / `invalid_request` /
`expected_hash_required`；404 `not_found`；409 `already_exists` /
`file_conflict`；413 `file_too_large`；503 `vault_not_configured` /
`vault_unavailable`；500 固定安全消息。Pydantic schema 失败默认 422
（安全紧凑，不泄露内部字段路径/类型）。错误与日志绝不包含绝对 root、
堆栈或正文。

## 9. M1 现状声明（边界）

- 已实现：Vault root 配置与生命周期、路径安全、bytes 保真读写、原子写与
  hash 冲突、`.localnote/` 占位初始化、watcher、Vault REST API 与错误映射。
- 未实现（后续里程碑）：Editor/Preview/Tabs/Split/Workspace UI（M2）；
  Markdown parser/AST/frontmatter/Properties/wikilink/backlink/Metadata
  （M3）；Search/FTS/SQLite schema/索引（M3/M4）；Graph runtime（M5）；
  AI chat/embedding/rerank 与写操作（M6/M7）；Policy/Diff/Undo/History/
  Recovery/Agent loop/Scheduler（M7/M8）；云/NAS/同步/多副本/备份。
- 测试只使用 `tests/fixtures/vault/` 的受控副本或 pytest `tmp_path`，绝不
  触碰真实用户 Vault。
