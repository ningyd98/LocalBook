# LocalNote 图片/附件上传并嵌入笔记实施计划

- **版本**：v1.1
- **日期**：2026-08-16
- **状态**：待用户确认后进入开发；本文件仅为计划，不包含实现代码

## 修订记录

- **v1.0 → v1.1：右键目录语义改为真实落点。** 文件树目录右键“上传到该目录”现在必须把文件写入用户右键的那个 Vault 目录；工具栏、编辑器/预览区拖拽和粘贴则写入当前笔记所在目录，根目录笔记对应 Vault 根。删除“右键仅作为入口、仍写入固定 attachments/”的解释，因为该解释被用户裁决否决。
- **v1.0 → v1.1：取消固定 `attachments/YYYY-MM/` 布局和自动月目录。** `attachments/` 不再是唯一合法落点；默认不自动创建月目录或中间目录，目标目录必须已经存在。理由是用户目录语义优先，也避免任意目录写入被隐式扩大为递归建目录。
- **v1.0 → v1.1：统一目标目录、命名、引用和接口契约。** 上传服务的落点改为“目标目录 + 清洗后的 basename”；请求新增 `target_directory`，并明确由前端根据入口决定默认值、后端只校验和执行。
- **v1.0 → v1.1：补充任意目录写入的安全边界。** 增加目标目录的 root-relative POSIX 校验、目录存在性、symlink 二次复核、禁止覆盖、`.localnote`/隐藏段/include_hidden/watcher 关系，以及 ATT-20 的针对性测试。
- **v1.0 → v1.1：改为相对当前笔记的 Markdown 引用。** 当目标目录不在当前笔记目录下时允许并规范生成 `../`；resolver 按笔记路径解析后再转资源 API URL。这样引用在笔记移动时仍遵循 Markdown 的目录语义，并明确区别于 root-relative API `path`。
- **保持不变：** 现有 5 个 Vault 端点语义、`policy attachment_write`、sanitize 管线、≤10MiB JSON/>10MiB multipart 回退与流式约束均不改变。

## 1. 目标与成功标准

### 1.1 目标

在不改变现有 Vault、Markdown、Policy、索引和前后端分层语义的前提下，增加用户直传图片/附件能力：

1. 工具栏选文件、编辑器/预览区拖拽、剪贴板截图粘贴、文件树目录菜单四个入口均可上传。
2. 文件原始 bytes 落盘到**入口选定的已存在 Vault 目录**：文件树右键落到被右键的目录；工具栏、拖拽、粘贴落到当前笔记所在目录；根目录笔记落到 Vault 根。`attachments/` 不再是唯一合法落点，默认不自动建立 `attachments/YYYY-MM/` 月目录。
3. 同名不覆盖，自动生成安全且可预测的去重 basename；落点始终为“目标目录 + 清洗后的 basename”。上传成功后将与实际落点一致的 Markdown 引用插入当前笔记；图片在预览区显示，普通附件可下载/查看。
4. ≤10 MiB 复用 JSON `content_base64`；>10 MiB 使用 multipart 流式端点；任何单文件不得超过 `VaultSettings.max_file_bytes`（默认 50 MiB）。

### 1.2 可判定成功标准

- 四种入口各至少有一个自动化测试，成功响应后正文出现与实际落点一致的正确引用，Vault 中 bytes 与上传前逐字节相等。
- 四种入口传入的目标目录分别符合入口规则；目标可以是 Vault 根或任意既有普通目录，但不能是 `.localnote/`、隐藏段、symlink 目录或越界路径。
- 图片预览的 `img.src` 是前端可加载的、经过 URL 编码的只读资源 URL，而不是 Vite 页面相对地址；危险协议/属性仍被 sanitize 移除。
- 上传目录、文件名和资源读取全部经过 `VaultService`；`server/api/routes/vault.py` 不出现 `Path`、`open`、`os.*` 文件操作。
- 路径攻击（绝对、`..`、NUL、反斜杠、盘符、UNC、空段、symlink、隐藏段、`.localnote`）均按既有错误体拒绝；不覆盖返回 409 `already_exists`；现有 PATCH 的 `expected_sha256`/409 `file_conflict` 不变。
- multipart 从请求流分块写入服务端临时文件并原子提交，不将整个 payload 聚合为 bytes；超限在写入过程中提前终止并清理临时文件。
- `server/policies/engine.py` 的 `attachment_write` 永久拒绝语义不修改，并有回归测试证明 Agent 写附件仍被拒。
- `./scripts/check.sh` 全绿：后端 pytest、protocol/graph/web typecheck、Vitest、web build。

## 2. 范围与非范围

### 2.1 范围

- 新增附件命名、入口到目标目录的解析、已存在目标目录校验、用户上传服务方法和 JSON/multipart API；不再实现固定附件根或自动月目录。
- 新增只读原始资源 API，供 Markdown 图片预览使用。
- protocol DTO 镜像、Web API client、Workspace store action、编辑器插入/拖放/粘贴、预览 URL 重写、文件树附件打开/下载与目录上传菜单。
- 必要的 CSS、i18n、后端/前端测试和契约文档更新。

### 2.2 明确不做

- 不增加附件目录设置项；本期不自动创建任意目标目录、中间目录或月目录。目标目录必须已存在；用户可先使用既有 `create_directory` 创建目录。
- 不解析、转码、压缩、缩略、改写附件内容；不自动改变既有笔记正文、换行、BOM 或未知 Markdown。
- 不修改 remark/rehype 管线为其他渲染器，不移除 `rehype-sanitize`，不允许脚本、事件属性或危险 URL。
- 不让 AI/Agent 通过用户上传端点写附件；不修改 Policy 规则或放开 `attachment_write`。
- 不实现云同步、认证、断点续传、批量上传、拖动移动附件、删除附件、附件编辑器或附件全文索引。
- 不改变现有 Vault 端点的 method、路径、请求字段和错误语义；新能力使用新增端点/DTO/字段。
- 不把附件变成 Graph 节点；既有 `wikilinks.py`/Graph 对非 Markdown 目标的行为保持不变。

## 3. 任务清单

估算以一个熟悉本仓库的开发者计，S=0.5 天，M=1 天，L=2 天；依赖表示必须先完成的编号。

### A. 后端 Vault 核心与命名

- **ATT-01（M，新增/修改，依赖无）**：在 `server/vault/attachments.py`（新增）定义附件命名纯函数和常量：输入原始 basename、目标 root-relative 目录、现有名称集合，输出“目标目录 + safe-name”，不再拼接固定 `attachments/YYYY-MM/`。规则：Unicode NFC 仅用于名字比较/显示清洗（不得用于内容）；去掉 NUL、`/`、`\\`、控制字符、`.`/`..` 段及 Windows 保留尾点/空格；空名用 `attachment`；保留最后一个普通扩展名并限制扩展名为安全字节；basename 按 Unicode code point 清洗后 UTF-8 截断到 255 字节且不截半个 UTF-8 字符；总 root-relative 路径 ≤4096 字节。保留可读字符（含中文/emoji），空白折叠为 `-`，危险字符统一为 `-`，连续 `-` 合并。基础名冲突时按 `-2`, `-3` 递增；若调用方提供内容 digest，可使用 `-<前 6 位 hex>` 作为首次去重后缀（不作为内容摘要替代）。完成定义：纯函数测试覆盖空格、中文、emoji、超长、无扩展名、点文件、危险字符、Windows 名和边界字节长度，并证明目录只作为已校验的 root-relative 目标。
- **ATT-02（L，修改 `server/vault/atomic_write.py`、`server/vault/service.py`，依赖 ATT-01）**：增量加入流式 no-overwrite 创建原语。服务接收受控 binary stream/callback，不接收用户 `Path`；在已存在且已复核的目标目录内创建随机服务端临时文件（0600、O_EXCL、O_NOFOLLOW），按 1 MiB 分块写入、同步并增量 SHA-256，超过 max bytes 立即清理并抛 `FileTooLarge`；用同目录 exclusive link/原子提交保证并发同名只能一个成功，目标已存在映射 `AlreadyExists`，最后对目录、目标和 symlink 安全复核并返回 `{path, sha256, byte_length, operation:"created"}`。保留既有 `atomic_create_bytes`/PATCH 实现不变语义。若 Windows 不支持 hard-link 提交流程，采用已验证的 `os.replace` + exclusive target 创建方案，必须有竞态测试且禁止覆盖。
- **ATT-03（M，修改 `server/vault/service.py`/`schemas.py`，依赖 ATT-01/02）**：增加 `upload_attachment_bytes(...)`（JSON 小文件）与 `upload_attachment_stream(...)`（multipart），统一校验前端传入的 root-relative 目标目录、父目录已存在且为真实目录、命名和路径安全。**禁止自动创建不存在的中间目录**，包括 `attachments` 和月目录；理由是既有 `create_directory` 语义要求父目录存在，且任意目录写入不应隐式扩张目录树。目录是 Vault 根时允许直接落根。命名碰撞在 `_mutation_lock` 内重新检查；系统外并发通过原子提交返回 already_exists，再重试下一个名字（限定次数并防止无限循环）。目标路径与 symlink 在解析前、打开前、提交前均二次复核。
- **ATT-04（S，修改 `server/vault/errors.py`、`server/api/main.py`，依赖 ATT-03）**：只补充需要的新错误码（例如 `invalid_attachment_name` 若确有稳定区分必要）并保持既有 `{"error":{"code","message","path"}}`；multipart 内容类型/缺失文件/字段错误映射 400 `invalid_request`，目标目录不存在、非法目录或 symlink 映射既有稳定路径/目录错误，超限 413 `file_too_large`，目标竞争失败可重试后仍冲突则 409 `already_exists`，Vault 不可用仍 503。不得把异常、root、临时文件名或正文写入响应/日志。

### B. 后端 API 与资源读取

- **ATT-05（M，修改 `server/vault/schemas.py`、`server/api/routes/vault.py`、`server/api/main.py`，依赖 ATT-03/04）**：新增 DTO `AttachmentUploadRequest { original_name: str, target_directory: str, content_base64: Base64Bytes }`（`extra=forbid`，`original_name` 非空且有长度上限，`target_directory` 必填字符串但允许空串表示 Vault 根，content 解码受限）。新增 `AttachmentUploadResponse` 字段 `path:string`、`sha256:string`、`byte_length:int`、`content_type:string`、`operation:"created"`、`original_name:string`。新增 `POST /api/v1/vault/attachments`，仅用户直传，调用服务并返回 201。`target_directory` 由前端根据入口明确决定并发送；后端不根据请求上下文猜测当前笔记目录，也不接受客户端传来的绝对路径。原 `/vault/file` 不改。
- **ATT-06（M，新增/修改 `server/api/routes/vault.py`、`server/vault/service.py`，依赖 ATT-03/05）**：新增 `POST /api/v1/vault/attachments/multipart`，multipart 字段 `file`（UploadFile 必填）、可选 `original_name`（默认 `UploadFile.filename`）、必填 `target_directory`（空串表示 Vault 根）。路由只读取 FastAPI `UploadFile` 流并交给 Service，不能 `Path/open` 或自行落盘。通过 Starlette/FastAPI 的分块读取将 stream 交给 `upload_attachment_stream`；不得调用 `await file.read()` 无界聚合。响应 DTO 与 ATT-05 相同，201。
- **ATT-07（M，新增/修改 `server/api/routes/vault.py`、`server/vault/service.py`，依赖 ATT-04）**：新增 `GET /api/v1/vault/resource?path=...` 只读原始资源端点。Service 执行同一公开路径校验、symlink/`.localnote`/隐藏段检查、最大字节限制和安全复核，返回受控 streaming body/文件句柄及 content type；路由只构造 `StreamingResponse`，设置 `Content-Type`、`Content-Length`、安全 `Content-Disposition:inline`，不回显 root。资源端点只允许 GET/HEAD（如实现 HEAD 必须复用 Service 元数据），不支持任意 Range，超上限 413；不得为了预览把整个大图转成 JSON/base64。
- **ATT-08（S，修改 `server/config.py`、CORS 配置和文档，依赖 ATT-05/06）**：复用 `vault.max_file_bytes`，新增 ≤10 MiB 的 JSON 上传阈值常量（不新增用户配置）；确保 CORS `POST` 已包含，必要时显式保留 `HEAD`。不改变 `LOCALNOTE_VAULT_MAX_FILE_BYTES` 默认 50 MiB。

### C. 协议与客户端

- **ATT-09（S，修改 `packages/protocol/src/index.ts`、`apps/web/src/api/types.ts`，依赖 ATT-05/06/07）**：镜像 `AttachmentUploadResponse`、含 `target_directory` 的上传请求类型、`AttachmentResource`（如需元数据）和 `AttachmentUploadError`，不另造不一致类型。DTO 字段采用现有 wire 风格：后端 snake_case 字段在 TS 中明确对应 `target_directory`/`byte_length`/`content_type`，并补充 `VaultErrorCode` 仅在需要时；所有新增响应 `extra` 行为由服务端 schema 保证。
- **ATT-10（M，修改 `apps/web/src/api/client.ts`，依赖 ATT-09）**：新增 `uploadAttachmentBase64({originalName,contentBase64,targetDirectory})` 调 `POST /vault/attachments`；新增 `uploadAttachmentMultipart(file, targetDirectory, originalName?)` 用 `FormData`，不手动设置 multipart Content-Type boundary；统一沿用 `request` 的 Vault session、错误解析和 stale response。新增 `vaultResourceUrl(path)`：每段 `encodeURIComponent`，使用 `/api/v1/vault/resource?path=...`；不把未编码路径直接拼 URL。

### D. Workspace、编辑器和预览

- **ATT-11（L，修改 `packages/workspace/src/types.ts`、`store.ts`、`selectors.ts`，依赖 ATT-10）**：`WorkspaceApi` 增加带 `targetDirectory` 的 `uploadAttachmentBase64`、`uploadAttachmentMultipart`；`WorkspaceState` 增加 `uploadAndInsertAttachment(file: File, options?: {notePath?: string; targetDirectory?: string; source?: "toolbar"|"editor-drop"|"preview-drop"|"paste"|"tree-context";})` 和必要的 `insertMarkdownAtSelection`/错误状态。前端是目标目录的唯一入口决策者：tree-context 必须使用右键目录；其他入口必须使用当前笔记所在目录（根笔记为空串）；后端不默认推断。上传成功后只对当前 UTF-8、非只读 Markdown session 插入一行：引用路径是相对当前笔记文件目录的 POSIX 路径，图片为 `![安全 alt](相对路径)`，其他为 `[原始显示名](相对路径)`；目标目录在当前笔记目录外时确定性生成 `../` 段，绝不把 `target_directory` 直接当成正文相对路径。插入通过 CodeMirror selection API 或受控文本拼接，正文仍由既有保存队列 PATCH，保存带原 expected hash。上传失败不改变正文。二进制转 base64 使用 chunked `Uint8Array` 转换（或 `FileReader.readAsDataURL` 去掉前缀），严禁对完整二进制使用深层 `String.fromCharCode(...bytes)`/`btoa(...spread)`。
- **ATT-12（M，修改 `packages/editor/src/CodeMirrorEditor.tsx`、`apps/web/src/components/EditorPane.tsx`、必要新增 `AttachmentToolbar.tsx`，依赖 ATT-11）**：向 CodeMirror 暴露 ref/selection 插入回调；工具栏按钮打开隐藏 `<input type=file>`，允许 image/* 与任意附件，选择后先按当前笔记目录决定目标（文件在根目录时为空串），再走大小分流（文件 `size <= 10 MiB` JSON，否则 multipart；服务端最终限制）。绑定编辑器 DOM 的 `dragover/drop/paste`：拖拽文件和 Clipboard `image/*` 阻止默认、以当前笔记目录上传并插入；文本粘贴不拦截。粘贴截图以 `File`/Blob 生成确定性 `pasted-image` 名。预览区 drag/drop 同样转交上传并使用当前 Markdown 目录，但当前没有可写笔记时只提示先打开 Markdown，不修改正文。
- **ATT-13（M，修改 `apps/web/src/components/WorkspaceShell.tsx`、`EditorPane.tsx`、`PreviewPane.tsx`、新增 `AttachmentPreview.tsx`/hook，依赖 ATT-11/12/14）**：Shell 统一提供当前 note、当前 note 所在目录、上传回调和资源 URL resolver；Preview 接收 `notePath` 与 `resolveResourceUrl`。渲染 HTML 后只对安全、解析后的附件引用做重写：先以当前笔记目录解析 Markdown 相对路径为 Vault-root-relative path，再识别允许的 Vault 路径并使用 `vaultResourceUrl`；绝不改写 `http(s)`, `data:`（可按 sanitizer 策略拒绝 data）、`blob:`、`#` 或 `javascript:`。优先在 AST/HAST 属性层改写再 stringify；若采用 HTML 后处理，使用严格 URL 解析和属性白名单，不能正则替换任意属性。资源使用浏览器 HTTP 缓存，不创建 blob URL；因此大图不长期占用 JS heap，组件卸载无 revoke 问题。资源端点设置 `Cache-Control: no-cache` 或短缓存以反映外部变更，不能宣称 hash immutable。
- **ATT-14（M，修改 `packages/markdown/src/render.ts`、`Preview.tsx`，依赖 ATT-07）**：保留 remark→rehype-raw→rehype-sanitize→stringify 管线。为 `img` 仅允许 `src`、`alt`、安全尺寸属性（若现有 schema 默认不保留则显式最小放宽），协议白名单只允许空/相对安全路径、`http`/`https`（是否保留外链按现有行为测试），禁 `javascript/data/vbscript` 和事件属性；不要开放 style、onerror、svg script。新增 `renderMarkdown(source, options?)` 的纯 resolver 回调（协议类型镜像），让 `Preview` 在 Sanitize 后将相对路径按当前 note 目录解析为 Vault-root-relative resource path，再重写为编码后的 `/api/v1/vault/resource?path=...`。保留危险 HTML 既有测试并新增相对路径（包括 `../`）、外链、恶意属性测试。
- **ATT-15（M，修改 `apps/web/src/components/FileTree.tsx`、`Sidebar.tsx`、`WorkspaceShell.tsx`，依赖 ATT-11）**：附件行不再 disabled。最小改动：Markdown 仍调用既有 `openFile`，非 Markdown 点击调用 `onOpenAttachment`，显示附件预览/下载面板（图片 `<img src=resourceUrl>`，其他 `a download`，失败显示错误），不创建 EditorSession、不破坏 Tab/保存/relations。目录行增加 `onContextMenu`/可访问菜单“上传到该目录”，菜单选择文件后调用上传 action，并传入该目录的 root-relative POSIX 路径；根目录菜单传空串。不得将菜单目录改写成 `attachments/`。
- **ATT-16（S，修改 `apps/web/src/i18n/*`、CSS/图标所需文件，依赖 ATT-12/15）**：补充中英文键：插入附件、上传附件、拖到此处、粘贴图片、选择文件、上传中、上传成功/失败、文件过大、已存在、目录不存在/无效、打开/下载附件、上传到该目录、请先打开可编辑 Markdown、预览加载失败。所有错误仍经 `errorText` 映射稳定 code，不把服务端绝对路径展示给用户。

### E. 索引、Policy 与文档兼容

- **ATT-17（M，修改 `server/index/service.py`/`server/markdown/wikilinks.py` 仅在测试暴露缺口时，依赖 ATT-01）**：确认新引用按 Markdown 相对路径解析：解析器以当前笔记文件目录为基准，将 `../` 规范化到 Vault-root-relative 后再查找；`![[...]]` 扫描和 Graph 对附件目标不产生 note 节点。默认插入 Markdown 标准链接，不新增 wiki embed 语法；必要时只补回归测试，不改变解析器。
- **ATT-18（S，修改测试/注释，依赖 ATT-03）**：明确不改 `server/policies/engine.py`。补测试调用真实 `PolicyEngine`，`ActionFacts(attachment=True)` 对 Agent/受控写路径仍返回 deny + `attachment_write`；用户直传路径不调用 PolicyEngine。
- **ATT-19（S，修改 `docs/vault-spec.md`、`docs/architecture.md`、`README.md`，依赖全部实现任务）**：记录入口决定目标目录、root-relative `target_directory` 契约、目标目录必须已存在、禁止隐藏/.localnote/symlink/越界、命名、实际落点、相对当前笔记的引用和 resolver、资源 URL、原始 bytes/SHA、上传阈值、流式限制、错误映射、测试门禁和不经 Policy 的用户直传边界；文档明确 `attachments/` 不是唯一落点且不自动建月目录，不能暗示附件可由 Agent 写。

### F. 测试与门禁

- **ATT-20（L，新增/修改 `tests/backend/test_vault_attachments.py`、必要 fixture，依赖 ATT-01~08/17/18）**：只用 `tests/fixtures/vault` 受控副本和 `tmp_path`。真实覆盖：目标为 Vault 根、任意既有普通目录和祖先/同级目录；`target_directory` 的 root-relative POSIX 校验（绝对、`..`、NUL、反斜杠、盘符、UNC、空段、过长、隐藏段、`.localnote`）；不存在目标/中间目录不自动创建；目录 symlink、提交前目录替换和目标 symlink 的二次复核；所有路径攻击与资源 symlink；默认/自定义 max、JSON bytes/BOM/NUL/二进制 round-trip、SHA、同名并发、不覆盖 409、multipart 分块流式（用会记录 read chunk 的 stream，证明未 `.read()` 全量）、临时文件清理、资源端点 URL 编码/Content-Type/上限，以及 watcher 不把临时文件或隐藏段泄露到公开 listing 的回归。
- **ATT-21（L，新增/修改 `tests/frontend/AttachmentUpload.test.tsx`、`MarkdownPreview.test.tsx`、`FileTreeDrag.test.tsx`，依赖 ATT-09~16）**：使用既有 Testing Library/Vitest 写法和 mock Workspace API，真实覆盖 base64 分流与 chunk 编码、10 MiB 分界、toolbar/editor/preview/paste 使用当前 note 目录、目录右键使用被右键目录（含 Vault 根）、目录参数传递、引用插入（图片/普通附件、当前笔记相对路径及 `../`）、附件行打开/下载、资源 URL 编码、sanitize/XSS、上传失败正文不变。
- **ATT-22（S，依赖 ATT-20/21）**：运行 `./scripts/check.sh`；若失败先修复实现/测试原因并记录，不通过时不得以删测试或放宽门禁解决。验收产物包含测试输出、改动文件清单和手工验收记录。

## 4. 技术方案与关键设计决策

| 决策 | 选项 | 选定 | 理由/验收 |
|---|---|---|---|
| 存储布局 | 固定 attachments 月目录 / 入口目标目录 | **入口决定的既有 Vault 目录** | 右键必须真实写入所选目录；工具栏/拖拽/粘贴写当前笔记目录；允许根目录。`attachments/` 不再是唯一合法落点。 |
| 月目录 | 默认自动建月目录 / 仅根目录特殊月目录 / 不自动建 | **不自动建月目录** | 用户目录语义优先，避免右键目录被悄然改变；目标目录必须已存在。若未来需要月目录应另立产品决策，不在本期隐式加入。 |
| 目标目录决定者 | 后端猜测 / 前端按入口决定并传参 | **前端按入口决定，后端只校验执行** | 后端无法可靠知道拖拽、粘贴或右键上下文；显式参数使边界可测。空串表示 Vault 根。 |
| 正文引用 | 相对笔记路径 / Vault 根相对 | **相对当前笔记文件目录的 POSIX 路径** | 与实际文件落点一致；目标在祖先或同级时确定性生成 `../`。resolver 先以 note 目录解析为 Vault-root-relative，再调用资源 API；不把 root-relative API path 直接写入正文。 |
| 嵌入语法 | 标准 Markdown / `![[...]]` | 图片标准 `![alt](...)`，附件标准 `[name](...)`；不新增 wiki 语法 | remark 原生支持，预览可控；现有 `![[...]]` scanner/Graph 保持兼容而非扩张。 |
| 小文件传输 | JSON base64 / 全部 multipart | ≤10 MiB JSON，其余 multipart | 兼容现有通道；前端 chunk 编码避免 btoa 爆栈；大文件不进内存。 |
| 大文件落盘 | 直接目标 / 临时文件后 rename | Service 同目录临时文件 + fsync + no-overwrite 原子提交 | 防崩溃半文件、并发覆盖和 TOCTOU；增量 hash 保持原始摘要。 |
| 资源预览 | 直接相对 src / blob URL / 只读资源 API | 新增 `/vault/resource?path=`，浏览器 HTTP 缓存 | 修复 Vite/API 相对地址裂图；不把大图转 base64，不保留 blob 内存。 |
| sanitize | 删除 sanitizer / 大范围放宽 / 最小 schema | 保留现有链管线，仅放行必要 `img` 属性和安全 URL | 防 XSS；协议与属性白名单可自动测试。 |
| 文件名 | 保留原名 / UUID / 确定性清洗+递增去重 | 安全清洗、保留可读扩展名、`-2` 递增；可用 digest 短后缀减少竞争 | 满足长度与 POSIX 约束，便于用户识别；原始文件名仅作为显示/输入，不决定内容。 |
| Agent 边界 | 复用 Agent 写接口 / 新增用户直传旁路 | 用户直传旁路，Policy 不参与；Policy attachment deny 不变 | 需求明确且避免放开自动写附件；回归测试锁定语义。 |
| 目录创建 | 自动递归创建 / 仅允许已存在目录 | **仅允许目标目录已存在，不自动 mkdir** | 与既有 `create_directory` 语义一致；减少任意目录写入和 watcher 事件风暴；用户先显式创建目录。 |
| 覆盖策略 | 覆盖 / no-overwrite 去重 | **禁止覆盖，冲突 `-2/-3`，竞态最终 409 `already_exists`** | 防止数据丢失；原子提交是最终安全边界。 |

## 5. 公共 API 契约

### 5.1 `POST /api/v1/vault/attachments`（JSON，小文件）

请求 `Content-Type: application/json`：

```json
{"original_name":"pasted image.png","target_directory":"notes/2026","content_base64":"<strict standard base64>"}
```

`original_name` 为非空字符串，仅参与命名；服务器不信任其目录部分。`target_directory` 必须是 root-relative POSIX 路径：空串表示 Vault 根；禁止绝对路径、`.`/`..` 段、NUL、反斜杠、盘符、UNC、空段、以 `.` 开头的路径段和 `.localnote` 段；必须在长度上限内，且对应 Vault 内已存在的非-symlink 目录。**前端决定该字段**：tree-context 发送右键目录，其余入口发送当前笔记目录；后端不默认当前笔记目录。`content_base64` 解码后必须 ≤10 MiB 且 ≤`vault.max_file_bytes`。响应 201：

```json
{"path":"notes/2026/pasted-image-a1b2c3.png","sha256":"sha256:<64 lowercase hex>","byte_length":1234,"content_type":"image/png","operation":"created","original_name":"pasted image.png"}
```

`path` 是 Vault-root-relative 实际落点；`attachments/` 不是必需前缀。目标目录不得因上传自动创建。

### 5.2 `POST /api/v1/vault/attachments/multipart`

请求 `multipart/form-data`：

- `file`：必填 UploadFile，服务端分块读取；
- `original_name`：可选，缺省使用 `file.filename`；不接受目录语义；
- `target_directory`：必填表单字段，空串表示 Vault 根，校验规则与 5.1 完全相同。

响应同 5.1。文件名、MIME 只用于命名和展示，不能绕过内容 bytes 或扩展名安全策略；不根据 MIME 解析/转码文件。

### 5.3 `GET /api/v1/vault/resource?path=...`

`path` 必须是 root-relative POSIX 路径，表示实际落点，例如 `notes/2026/pasted-image.png`；必须拒绝越界、隐藏段、`.localnote` 和 symlink。响应 200 原始 bytes，`Content-Type` 由 Service 的 `mimetypes` 展示推断（未知为 `application/octet-stream`），`Content-Length` 为实际大小，`Content-Disposition: inline`。不返回 JSON，不支持写入；资源不可见路径、symlink、超限或不存在分别映射既有 400/404/413/503。

### 5.4 错误 HTTP 映射

所有 JSON 错误严格为 `{"error":{"code":"...","message":"...","path":null|string}}`：

- 400：`path_traversal`、`symlink_escape`、`invalid_request`、非法文件名/字段/目标目录；
- 404：`not_found`（包括不存在目标目录或资源）；
- 409：`already_exists`（创建不覆盖）、既有 `file_conflict`（仅 PATCH 等更新语义）；
- 413：`file_too_large`；
- 503：`vault_not_configured`、`vault_unavailable`；
- 500：固定 `atomic_write_failed` 或 `internal_error`，不泄漏异常。

现有 `/vault/file` GET/POST/PATCH/DELETE 和 `/vault/file/move` 契约完全不变。新 DTO 必须在 `server/vault/schemas.py` 定义，并逐字段镜像到 `packages/protocol/src/index.ts`，再由 `apps/web/src/api/types.ts` re-export。

## 6. 数据结构与完整数据流

1. 浏览器入口产生 `File`（选择/拖拽）或 Clipboard `image/*` Blob；生成显示 alt 和原始名称。
2. 前端根据入口决定 `target_directory`：文件树右键为所右键目录，其他入口为当前笔记目录（根目录为空串）；无可编辑当前 Markdown 时不发写请求。Workspace 按 `File.size <= 10 MiB` 选择 chunked base64 JSON，否则选择 multipart。
3. API client 带 Vault session 将 `target_directory` 传给新增端点；服务器 DTO/UploadFile 校验字段，路由只编排。
4. `VaultService` 校验目标为 Vault-root-relative POSIX、非隐藏/非`.localnote`、无 symlink 且已存在目录；清洗 basename，落点为目标目录 + basename，不计算或创建 UTC 月目录。
5. JSON bytes 或 multipart stream 写目标目录内随机临时文件；分块计算 SHA-256 和长度，超过 max 立即删除 temp；fsync 后以 no-overwrite 原子提交，提交前后再次复核目录和目标 symlink。
6. 服务返回实际 root-relative `path`、原始 bytes 的 `sha256`/长度、展示 MIME；watcher 只产生既有 create/modify 事件，索引按现有机制处理，不阻塞上传。
7. Workspace 用返回的 root-relative path 与当前 note 文件目录计算 POSIX 相对引用（必要时含 `../`），并在 CodeMirror selection 处插入；不直接改文件、不调用 Policy；既有 dirty/autosave/expected hash 流程负责保存正文。
8. Markdown Preview 按既有 remark/GFM/rehype 管线生成安全 HTML；resolver 以当前 note 目录解析相对引用，规范化为 Vault-root-relative `path`，再转为编码后的 `/api/v1/vault/resource?path=...`。
9. 浏览器按 HTTP 请求原始资源并缓存；普通附件在预览/下载面板使用同一资源 URL。资源读取仍由 VaultService 执行安全复核。
10. 现有 index/wikilink scanner 看到标准相对路径时继续保持其既有解析；附件不会成为 Graph note/tag 节点。

## 7. 前后端交互细节

### 7.1 Store/API

`WorkspaceApi` 新增：

- `uploadAttachmentBase64(args: {originalName:string; contentBase64:string; targetDirectory:string}): Promise<AttachmentUploadResponse>`；
- `uploadAttachmentMultipart(file: File, targetDirectory: string, originalName?: string): Promise<AttachmentUploadResponse>`。

`WorkspaceState` 新增：

- `uploadAndInsertAttachment(file: File, options?: {notePath?: string; targetDirectory?: string; source?: string}): Promise<AttachmentUploadResponse | null>`；
- 可选 `attachmentBusy`/`attachmentError` 视图状态（不得污染 EditorSession 保存状态）。

状态规则：入口适配器必须在调用 action 前确定目标目录；action 对 tree-context 与 note-context 做一致性校验，使用 `vaultGeneration` 防止切换 Vault 后旧响应插入正文；上传期间禁用重复提交；成功后刷新 tree（或等待 watcher），失败只设置附件错误。

### 7.2 组件

- `WorkspaceShell`：给 Toolbar、EditorPane、PreviewPane、FileTree 传当前 session、当前 note 目录、上传回调、资源 URL resolver 和附件打开回调。
- `EditorPane`/`CodeMirrorEditor`：工具栏、文件选择器、selection 插入、drop/paste DOM 事件；保留 Ctrl/Cmd+S。
- `PreviewPane`：传 notePath 和 resolver；预览区 drop 仍使用当前 Markdown 目录。
- `FileTree`：附件可点击；目录 context menu 触发上传并传真实右键目录。Markdown 打开路径仍完全调用原 `onOpen`。
- `AttachmentPreview`：图片展示、非图片下载、加载失败提示；禁止把不可信路径当 HTML。
- `Sidebar`：转发目录上传回调。

### 7.3 i18n

至少增加 `attachment.insert/upload/uploading/uploaded/uploadFailed/fileTooLarge/alreadyExists/invalidDirectory/directoryNotFound/dropHere/pasteImage/uploadToDirectory/open/download/noEditableNote/previewFailed` 的中英文键；错误 code 走现有 `apps/web/src/i18n/errors.ts`，不按服务端 message 拼接 UI。

## 8. 依赖与风险

- **目标目录安全/TOCTOU**：目标参数仅接受 root-relative POSIX；在解析、打开、提交前重复 `lstat`/realpath，并拒绝任意 symlink 目录、隐藏段和`.localnote`；不能声称消除同权限外部攻击者竞态，测试注入替换验证不越界。
- **并发同名**：命名预检查不是保证，最终必须依赖 exclusive atomic create；竞争失败重新选名，达到有限重试返回 409，绝不 overwrite。
- **大文件内存**：multipart 始终 `read(chunk_size)`；资源端点流式返回；JSON 只允许 10 MiB，前端 chunk 编码，不使用 spread/btoa 大数组。FastAPI/Starlette 的 multipart spool 行为需确认，Service 层仍不能依赖其完整内存驻留。
- **XSS**：Markdown 原始 HTML 经既有 sanitizer；只放行必要 img 属性和 URL 协议；资源响应不执行 HTML，未知 MIME 使用 octet-stream；新增恶意 `onerror`, `javascript:`, `data:` 测试。
- **文件名跨平台**：API 永远 POSIX；macOS NFD/大小写不主动归一化为路径事实；清洗 Windows 保留名、尾点空格、冒号、反斜杠；长度按 UTF-8 字节而非 JS/Python 字符。
- **目录写入扩大**：上传可写入与笔记同级、祖先或其他既有普通目录；通过“前端入口决定 + 后端显式校验 + 目标目录必须已存在”限制能力，不允许递归 mkdir。
- **`.localnote` 与 `include_hidden`**：上传端点无论 listing 的 `include_hidden` 值都拒绝隐藏段和`.localnote`；include_hidden 不构成写入授权。watcher 应沿用隐藏过滤并抑制临时文件事件，避免目录/temp/link 的事件风暴。
- **watcher 事件风暴**：临时文件名以 `.localnote-tmp-` 开头并在 listing 中按既有 hidden 规则过滤；必要时让 watcher 忽略服务临时文件，不能修改正文。
- **外部文件增长/资源读取**：Service 在打开/流式读取前检查大小并在读取中累计上限；读取后可复核 symlink；资源不承诺原子快照，变化按现有 Vault 错误/缓存策略处理。
- **索引时序**：附件创建 watcher 事件可能晚于 API 返回；UI 先使用返回路径，树刷新是最终一致，不把 watcher 完成作为上传成功条件。
- **浏览器兼容**：Clipboard `File`、DataTransfer items、FormData、`URLSearchParams` 均有 fallback；粘贴非图片不拦截；文件选择器取消不显示错误。

## 9. 测试与验收方式

### 9.1 后端

- ATT-01/20：命名清洗、字节边界、任意目标目录拼接、并发唯一名；使用 `tmp_path`，不触碰真实用户 Vault。
- ATT-02/20：binary bytes、BOM/NUL/0x00、哈希、超限中途清理、multipart 分块 spy、原子 no-overwrite 和竞争。
- ATT-03/05/06/20：JSON 201、multipart 201、空/非法字段、根目录/既有子目录成功、不存在目录不自动创建、symlink/隐藏/.localnote/越界拒绝、默认/自定义 max 413、错误体字段集合精确匹配。
- ATT-07/20：资源 URL 编码、任意实际落点的 root 路径、相对引用解析、symlink/`.localnote`/隐藏/绝对路径拒绝、Content-Type 和原始响应。
- ATT-17/18/20：`![[attachment]]` 既有扫描/Graph 行为不变；Policy `attachment_write` 仍 deny。
- 每项测试断言 root 外文件未创建/未改变、响应无绝对 root/Traceback/正文泄漏，并断言 watcher/listing 不因 `include_hidden` 暴露受保护段或 temp。

### 9.2 前端

- ATT-10/21：chunk base64、10 MiB 分界、multipart boundary、`target_directory` 传值、错误解析和 URL 每段编码。
- ATT-11/21：四入口目标选择（右键真实目录、其他入口当前 note 目录、根目录为空串）、成功插入图片标准语法/普通链接，目标在 note 外时生成确定性 `../`，上传失败正文保持原值，Vault generation 变更不插入过期响应。
- ATT-12/21：按钮选择、编辑器 drop、预览 drop、截图 paste；文本 paste 保持原行为；只读/非 UTF-8 禁止写入。
- ATT-13/14/21：`img` src 按 note 目录解析和重写、资源 API URL、外链策略、sanitize 移除脚本/事件/危险协议；图片不生成长期 blob URL。
- ATT-15/21：附件行不 disabled 且打开查看/下载；Markdown 行仍走旧 `openFile`；目录右键上传回调传递真实 root-relative 目录。

### 9.3 手工验收

1. 在 Vault 根和任意子目录分别新建/打开笔记；用工具栏、编辑器拖拽、预览区拖拽、剪贴板粘贴上传 PNG 和带中文 emoji 空格长名的普通 PDF/ZIP，确认前三类落在当前笔记目录。
2. 在文件树对 Vault 根和一个既有子目录执行“上传到该目录”，确认文件真实落在被右键目录，而不是 `attachments/`；检查正文引用、预览图片、普通附件下载及树刷新。
3. 对目标位于当前笔记祖先/同级的场景检查引用确定性地含正确数量的 `../`；用外部工具验证实际 root-relative 落点、bytes 与 SHA-256。
4. 重复上传同名文件，确认两个文件均存在且无覆盖；并行两个浏览器上传确认唯一名称或最终 409。
5. 上传略大于 10 MiB、接近 50 MiB、超过 max，确认分流、成功或 413，且失败不遗留 temp。
6. 尝试不存在目录、symlink、`.localnote`、隐藏段、绝对/反斜杠/`..` 路径；确认统一安全错误且不自动创建目录。
7. 通过现有 Agent/Policy 流程尝试附件写入，确认 `attachment_write` 拒绝。
8. 执行 `./scripts/check.sh` 并保存全绿输出。

## 10. 明确假设与需用户确认

1. **日期基准假设**：本期不使用日期目录，因此上传落点不受时区影响；若未来重新引入月目录，需另行指定时区来源。
2. **右键目录裁决（已解决阻断项）**：文件树右键“上传到该目录”必须真实写入所右键的 Vault 目录；工具栏/拖拽/粘贴写入当前笔记目录，根目录笔记写 Vault 根。`attachments/` 不是唯一落点，且不自动建月目录。本项不再作为待确认项。
3. **目标目录假设**：`target_directory` 由前端按入口决定并显式发送；空字符串表示 Vault 根。后端不默认当前笔记目录，仅负责完整安全校验。目标目录必须已存在且不能是 symlink。
4. **目录创建决策**：不允许自动创建不存在的中间目录或目标目录。推荐并选定此方案，因为它与既有 `create_directory` 语义一致、缩小任意目录写入能力并减少 watcher 事件风暴；用户需先显式创建目录。
5. **阈值假设**：10 MiB 按 10 × 1024 × 1024 bytes 解释；`vault.max_file_bytes` 是绝对硬上限，若部署将其设为低于 10 MiB，则 JSON 分流仍以实际服务端上限拒绝。
6. **扩展名/MIME 假设**：图片判断优先 MIME（`image/*`）并辅以安全扩展名；服务端不 sniff、解析或验证图片格式，错误 MIME 只影响插入语法/展示，不改变 bytes。
7. **标准 Markdown 假设**：本期不新增 `![[...]]` 输出；既有 scanner 已支持该语法，且附件在 Graph 中保持非节点。若希望用户可选 wiki embed，需另立 DTO/预览解析需求。
8. **引用与解析决策**：正文始终写相对当前笔记文件目录的 POSIX 路径；目标在当前目录之外时使用规范化 `../` 段，目标在当前目录时写 basename 或其子路径。Preview/index resolver 先以 note 目录解析并规范化为 Vault-root-relative，再执行资源 URL 编码；这与既有 index/wikilink 的 root-relative 解析目标一致，但不把 API path 直接暴露为正文引用。
9. **资源访问假设**：资源端点与既有 Vault API 共用 session/CORS；不新增认证、Range、缩略图或永久 blob 缓存。若需要公开/跨 origin 资源，需另行安全评审。
10. **依赖假设**：当前 FastAPI/Starlette 版本提供可迭代的 `UploadFile` 分块读取；若框架 multipart parser 已在进入路由前把整个文件读入内存，仍需升级/配置 parser 或实现受控 ASGI stream 适配，并在 ATT-20 中证明内存边界。
11. **资源缓存假设**：采用浏览器 HTTP cache，不使用前端 blob URL；外部文件变化的可见性以短缓存/显式 reload 为准，不引入 watcher 推送。
12. **兼容性确认**：保持现有 5 个 Vault 端点语义、`policy attachment_write`、sanitize 管线、≤10 MiB JSON/>10 MiB multipart 回退，不修改既有接口语义；新增上传字段仅属于新增端点。

## 计划完成定义

开发阶段只有在 ATT-01 至 ATT-22 的完成定义全部满足、所有变更仅落在计划允许范围、`./scripts/check.sh` 全绿、手工清单完成且入口目标目录与引用规则均按本 v1.1 裁决验收后，才可将本功能标记为交付。
