# 变更日志

本文件记录 LocalNote 的功能里程碑与修复。版本号与 `package.json` / `pyproject.toml` /
`server/__init__.py` / FastAPI `version` 保持一致。

## [1.0.0] — 2026-09-10

首个可用版本：本地优先的 Markdown 笔记服务（FastAPI + React/Vite），
正文永远是 Vault 里的原始文件，`.localnote/` 只保存可删除重建的派生索引。

### 能力总览（M0–M13）

- **M0 Bootstrap**：pnpm monorepo + Python 3.12，一键开发/门禁脚本
  （`scripts/dev.sh`、`scripts/check.sh`）。
- **M1 Vault 安全读写**：唯一 FS 门面 `VaultService`；路径词法校验 + resolve
  containment + 逐级 lstat；拒绝 `..`/绝对路径/NUL/反斜杠/盘符/UNC 与**一切
  symlink**；Markdown 与附件按原始 bytes 读写（JSON base64 + SHA-256），
  BOM/LF/CRLF/非 UTF-8/未知语法逐字节保真；同目录临时文件 + fsync 的原子写，
  update/delete/move 强制 `expected_sha256`（冲突 409，绝不静默覆盖）；
  watchdog 去抖事件流；`.localnote/` 可删除重建。
- **M2 Workspace/Editor/Preview**：文件树、CodeMirror 6 源码编辑、安全只读预览、
  多标签、分屏、800ms 防抖自动保存与冲突处理（重新加载 / 保留本地）。
- **M3 Metadata/Links/搜索**：frontmatter（未知字段逐字保留）、wikilink/embed
  扫描（heading/block/alias）、outgoing/backlinks、关键词搜索。
- **M4 SQLite 派生索引**：`.localnote/index.db`（WAL + 版本表迁移 + FTS5），
  watcher 增量更新，`POST /index/rebuild` 全量重建；索引故障不影响正文读写。
- **M5 Graph**：`/graph`、`/graph/local/{note}`、`/graph/tag/{tag}` 只读查询 +
  Graphology/Sigma 可视化（WebGL 缺失时降级）。
- **M6 只读 AI**：六个只读 workflow（chat/summarize/tags/related/extract_todos/
  classify），版本化 Prompt Registry、强 schema 校验、候选程序化缩小；本地
  oMLX / OpenAI 兼容端点。
- **M7 Policy/Diff/History/Recovery/受控 Agent**：纯程序 PolicyEngine、
  diff 审阅、Journal/Undo、事务逆序回滚；Agent 写入默认只生成 Level 1 preview。
- **M8 Scheduler/可靠性**：APScheduler 首选（asyncio 降级）、三个稳定任务、
  run 审计、超时/幂等/清理、启动扫描标记 `recovery_required`、显式 hash-guard
  恢复、History retention、局域网暴露告警。
- **M9 用户直传附件**：`POST /vault/attachments`（JSON ≤10 MiB）、
  `POST /vault/attachments/multipart`（流式）、`GET/HEAD /vault/resource`
  （只读预览/下载）；四入口（工具栏 / 拖拽 / 粘贴 / 文件树目录右键），落点由
  入口决定且目标目录必须已存在；禁止覆盖、同名 `-2/-3` 去重；用户直传不经
  PolicyEngine，Agent 写附件仍被 `attachment_write` 永久拒绝。
- **M10 文件重命名**：文件树右键 / 双击行内改名，复用
  `POST /vault/file/move`（未新增端点）；名字须为单一路径段；预期摘要取磁盘当前
  bytes，未保存草稿随标签迁移；目标已存在 → 409。
- **M11 从 wikilink 创建嵌套笔记**：预览里失效 `[[链接]]` 一键创建并打开；
  先按全库 basename 解析（与索引同规则），未命中则落在源笔记目录，
  `[[子目录/名]]` 按需逐级建目录；`..`/URL/空名拒绝。
- **M12 单栏实时预览（Live Preview）**：同一 CodeMirror 实例内渲染标题/粗斜体/
  行内代码/链接/wikilink/图片 widget/列表/引用，**光标所在行保留原始语法**；
  原有编辑/预览/分屏视图保留。
- **M13 任务清单点击切换**：`- [ ]` / `- [x]` 在预览与实时模式渲染为可点击复选
  框，点一下改写源码标记并加删除线；跳过围栏代码块。

### 编辑器与交互增强

- 补全 CodeMirror keymap：此前未注册键位表，**Enter / Backspace / 方向键 / 撤销
  全部失效**；现注册 `defaultKeymap` + `historyKeymap` + `indentWithTab`。
- `Ctrl/⌘+1…6` 设为 1–6 级标题、`Ctrl/⌘+0` 取消，同级再按一次切回正文；
  工具栏提供 H1–H6 与「正文」按钮。
- 附件引用插入到**光标处**（无编辑器时回退为末尾追加）。
- 预览恢复有序列表序号（Tailwind Preflight 清掉了 `list-style`）。

### 修复

- `App.tsx` 漏注册 `uploadAttachmentBase64/Multipart`，导致附件上传永远
  不发请求；新增 `AppApiRegistration` 契约测试锁定。
- 重命名在文件有未保存修改时被 409 拒绝（误用会话 `baseSha256`），改为始终读
  磁盘当前摘要。
- 实时预览点击复选框会被 CodeMirror 的「点哪光标跳哪」干扰，导致 widget 重建、
  点击丢失；改为阻止 `mousedown` 默认行为。
- 渲染器把 hast `data` 对象序列化成 `data="[object Object]"`；改用 `dataXxx`。
- 编辑器外部值同步会整篇替换文档并重置光标，改为最小差异替换。
- AI 一直显示「离线」：`/ai/status` 只请求 `/v1/models`（毫秒级）所以状态正常，
  而生成超时默认 **2 秒**，本地 4B 模型一次推理约 7 秒必然 `ai_timeout`——
  默认值放宽到 **60 秒**（上限 120）。
- `pull` 式手工重启会丢掉 launchd plist 里的
  `LOCALNOTE_SERVER__SETTINGS_TRUSTED_HOSTS`，导致公网域名访问 `/api/v1/settings`
  被 `settings_local_only` 拒绝；README 增加常驻服务小节与
  `launchctl kickstart` 重启方式。

### 依赖

- 新增 `python-multipart`（FastAPI multipart 路由的前提）。
- `packages/markdown` 新增 `@localnote/protocol` 与 `@types/hast`。
- 未引入其他运行时依赖（任务清单与实时预览均为自研实现）。

### 测试与门禁

- `./scripts/check.sh`：后端 `pytest` + `@localnote/protocol|graph|web` typecheck +
  前端 Vitest + 生产构建。
- 本版本：后端 **830 passed / 3 skipped**；前端 **31 文件 / 242 passed**。
- 测试只使用 `tests/fixtures/vault` 的受控副本与 `tmp_path`，绝不触碰真实 Vault；
  手写临时脚本须显式设置 `LOCALNOTE_SETTINGS_FILE`。

### 已知限制（1.0 明确不做）

- 无账号/认证/HTTPS；局域网或公网暴露需自行用防火墙与反代 Basic Auth 保护。
- 目录重命名/移动、附件拖动移动、附件全文索引、批量上传、断点续传。
- AI 写正文、Agent 递归 loop、自动修复 broken/ambiguous 链接。
- embedding/rerank 真实端点（能力探测可见，调用返回 `capability_unavailable`）。
- 真正的富文本编辑器：实时预览是「源码 + 装饰层」，不引入 AST 写路径。
- 目录重命名、云同步、多进程调度、分布式锁。
