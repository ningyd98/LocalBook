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
| `DELETE /api/v1/vault/file` | delete：必须带 `expected_sha256`（缺失 → 400 `expected_hash_required`）；只接受普通文件，目录 → 400 `not_a_file` |
| `POST /api/v1/vault/file/move` | move/rename：必须带源 `expected_sha256` |
| `POST /api/v1/vault/attachments` | 用户直传附件（JSON，≤10 MiB），201 |
| `POST /api/v1/vault/attachments/multipart` | 用户直传附件（multipart 流式，>10 MiB），201 |
| `GET /api/v1/vault/resource?path=` | 只读原始 bytes（图片预览/附件下载），非 JSON |
| `GET /api/v1/trash` | 回收站列表（含 `retention_days`、每项 `days_remaining`） |
| `POST /api/v1/trash` | 软删除：文件需 `expected_sha256`，目录按整体移动，201 |
| `POST /api/v1/trash/{id}/restore` | 恢复（`rename_if_occupied` 可避开占用路径） |
| `DELETE /api/v1/trash/{id}` | 永久删除单项 |
| `DELETE /api/v1/trash` | 清空回收站（返回清空后的列表） |

### 8.1 附件上传与资源读取（附件计划 v1.1 已实现）

**入口决定落点，后端只校验执行。** `target_directory` 是必填的
root-relative POSIX 目录（空串表示 Vault 根），由前端按入口决定：

| 入口 | `target_directory` |
|---|---|
| 文件树目录右键“上传到该目录” | 被右键的那个 Vault 目录（根目录为空串） |
| 编辑器工具栏“插入附件” | 当前笔记所在目录（根笔记为空串） |
| 编辑器/预览区拖拽 | 当前笔记所在目录 |
| 剪贴板粘贴图片 | 当前笔记所在目录 |

规则与边界：

- 目标目录**必须已经存在**且必须是普通目录；**不自动创建**目标目录、中间
  目录或 `attachments/YYYY-MM/` 月目录（需要目录请先用
  `POST /vault/directory`）。`attachments/` 不是唯一合法落点。
- 拒绝绝对路径、`..`、NUL、反斜杠、盘符、UNC、空段、隐藏段（以 `.` 开头）
  与 `.localnote` 段；目录 symlink 与目标 symlink 均按
  `symlink_escape` 拒绝，提交前后重复复核。
- 命名：清洗原始 basename（NFC 仅用于名字比较/显示；折叠空白与危险字符为
  `-`，去掉 Windows 保留名/尾点空格，保留最后一个安全扩展名，UTF-8 截断到
  255 字节且不截半个字符）；同名不覆盖，按 `-2`/`-3` 递增；最终以
  no-overwrite 原子提交兜底，竞争失败重试后仍冲突返回 409
  `already_exists`。
- 正文引用始终是**相对当前笔记文件目录**的 POSIX 路径（目标在笔记目录之外
  时确定性生成 `../`），图片 `![alt](相对路径)`，其他附件
  `[原始显示名](相对路径)`；不新增 `![[...]]` 输出。
- 资源 URL：预览层先按当前笔记目录把相对引用规范化为 Vault-root-relative
  path，再编码为 `/api/v1/vault/resource?path=...`；`http(s)`/`data:`/
  `javascript:` 等协议与事件属性仍被 sanitize 管线处理，资源端点对
  HTML/SVG 返回 `application/octet-stream` + `X-Content-Type-Options: nosniff`。
- 传输：单文件 ≤10 MiB（`ATTACHMENT_JSON_MAX_BYTES`）走 JSON
  `content_base64`；>10 MiB 走 multipart 流式（服务端按 1 MiB 分块读取并
  增量 SHA-256，超限立即清理临时文件）；硬上限沿用
  `vault.max_file_bytes`（`LOCALNOTE_VAULT_MAX_FILE_BYTES`，默认 50 MiB）。
- 附件上传是**用户直传旁路**，不经过 `PolicyEngine`；`attachment_write` 对
  Agent/受控写路径仍永久 deny（回归测试锁定）。

错误体固定为 `{"error":{"code","message","path"}}`，HTTP 映射：
400 `path_traversal` / `symlink_escape` / `invalid_request` /
`expected_hash_required`；404 `not_found`；409 `already_exists` /
`file_conflict`；413 `file_too_large`；503 `vault_not_configured` /
`vault_unavailable`；500 固定安全消息。Pydantic schema 失败默认 422
（安全紧凑，不泄露内部字段路径/类型）。错误与日志绝不包含绝对 root、
堆栈或正文。

### 8.2 文件重命名与 wikilink 建笔记（M10/M11，复用既有端点）

- **重命名**不新增端点：`POST /vault/file/move` 做同目录移动，`expected_sha256`
  取磁盘当前 bytes（因此未保存的草稿不会让改名失败），目标已存在 → 409
  `already_exists`；名字必须是单一路径段（前端拒绝 `/`、`\`、`.`、`..`、空名）。
  目录重命名仍不支持（`move_file` 仅接受文件）。
- **从 `[[wikilink]]` 创建笔记**：先按全库 basename 解析（与
  `server/index/service.py::_resolve_ref` 同一规则），命中即打开；否则在源笔记
  所在目录用 `POST /vault/file` 创建（`[[子目录/名]]` 用
  `POST /vault/directory` 逐级补建缺失层级）。`..`/绝对路径/URL/空名一律拒绝，
  不产生任何文件；正文引用语法（`[[...]]`）与索引/Graph 解析器未改动。

### 8.3 回收站与软删除（1.0.0 后新增）

- **位置**：`<root>/.localnote/trash/<uuid>/<原名>`，索引为 `.localnote/trash/index.json`。
  移动（同文件系统 rename）而非复制，不产生第二份正文；整目录一次移动。
- **为何不走公开 API**：`.localnote` 被 `is_reserved_derived_path` 统一拒绝，所以软删除必须是
  服务内部的一次受信移动。源路径仍走完整校验（词法、保留目录、逐级 symlink、containment），
  目标路径由服务生成（客户端只能给「待删的相对路径」与「回收站 id」）。
- **保留期**：默认 30 天（`vault.trash_retention_days`，1–3650）。过期条目在任意一次回收站
  操作（列表/软删除/恢复）时被清理，删除的是回收站里的载荷与索引记录，不触碰 Vault 正文。
- **恢复**：放回原相对路径；缺失的上级目录会被逐级重建；目标已存在 → 409
  `already_exists`，带 `rename_if_occupied` 时改为 `<原名> (restored)`（重名再加序号）。
- **派生语义**：`index.json` 或整个 `.localnote/` 被删除只会丢记账，正文与回收站载荷都不受影响
  （载荷本身在 Vault 树之外）。

### 8.4 嵌套文档的分级约定（纯文件夹，无新语法）

「新建子文档」把正文写进**同级同名目录**，前端据此渲染分级树；后端没有父子字段，
正文/frontmatter 不被写入任何层级标记，服务端不新增端点（仍只用 `POST
/vault/directory` 逐级补建 + `POST /vault/file` 创建）：

| 上层文档 | 子文档落点 | 说明 |
|---|---|---|
| `Notes/A.md` | `Notes/A/名.md` | 目录名 = 去掉扩展名的笔记名，位于同一目录 |
| `Notes/A/index.md` | `Notes/A/名.md` | 目录笔记代表它所在的目录本身 |
| `Notes/A/B.md` | `Notes/A/B/名.md` | 递归适用，可无限分级 |

由此推导的渲染规则（`packages/workspace/src/hierarchy.ts`）：

- `Notes/A/` 只在同级存在 `Notes/A.md` 时挂到该笔记下；普通目录（如 `docs/` 且无
  `docs.md`）保持独立目录行，不会被吞并。
- `notes/A/index.md` 代表 `notes/A/` 这一行，因此目录不会重复出现。
- 「收缩」只记录被折叠的路径；文档层级是**所有者链**（`Notes/A/B.md` → `Notes/A.md`），
  折叠任意一层会隐藏其内部所有子文档，与路径前缀链无关。
- 打开笔记会清除它与其上层目录的折叠标记，使当前笔记在树中可见。
- **正文引用**：新建子文档时，若上层笔记已打开且没有未保存改动/冲突/保存错误，在其正文末尾
  追加一段 `[[子文档名]]`（走既有 `updateContent` + 自动保存通道）；不满足条件时该笔记的
  bytes 完全不变，也不把它切到前台。重命名子文档会用它移动前的所有者链定位上层笔记并改写
  `[[旧名]]` → `[[新名]]`（保留别名/`#小节`/`^块引用`/`!嵌入` 与目录前缀；同一 stem 在全库
  出现多次时视为歧义，不改写）。嵌套层级本身仍**不写**任何 frontmatter 字段。

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
