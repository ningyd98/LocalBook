# LocalNote Server M2 计划：Workspace / Editor / Preview / Tabs / Split Pane

> 阶段：M2（依赖 M1 Vault Core；M1 已完成）  
> 性质：供开发 Agent 逐项执行、测试 Agent 验收和审计 Agent 复核的详细实施计划。  
> 本阶段唯一新增规划文件为 `PLAN-M2.md`；规划阶段不得创建实现代码，不得修改 `PLAN.md` 或 `PLAN-M1.md`。

## 1. 目标与可验收成功标准

### 1.1 总目标

在 M1 的安全 REST Vault 入口之上，把 Web 首页扩展为可工作的本地 Markdown 工作台：用户可以查看过滤后的 Vault 文件树，打开多个 Markdown 文件，在 CodeMirror 6 中编辑源码，同时在只读预览区看到 Standard Markdown 渲染结果；内容通过 M1 的 base64 + SHA-256 API 原样读写，自动保存和 Ctrl-S 均带 `expected_sha256`，外部修改时不静默覆盖。

前端数据流固定为：

```text
GET /api/v1/vault/files?recursive=true
  -> file tree state
click file
  -> GET /api/v1/vault/file?path=...
  -> base64 decode -> UTF-8 editor session (base hash retained)
edit
  -> local current text / dirty tab
  -> debounce ~800ms OR Ctrl-S
  -> UTF-8 encode -> base64 PATCH + expected_sha256(base/current server hash)
  -> updated hash becomes new base on 2xx
  -> 409 file_conflict => conflict state; reload or keep-local (never overwrite)
```

Markdown 文件是唯一事实源。预览是展示层派生结果，不向后端写回 HTML、AST、规范化换行或其他序列化结果。

### 1.2 可验收成功标准（全部为 M2 完成门槛）

1. `pnpm install --frozen-lockfile` 后，`pnpm --filter @localnote/web typecheck`、Vitest、生产构建均通过；现有 M1 后端 **166 个用例**保持通过（实际数量以仓库测试收集输出为准，不减少既有覆盖）。
2. Web 有明确的 Ribbon：文件、搜索、图谱、AI 四个入口；本阶段只有“文件”工作台真实可用，Search/Graph/AI 入口为明确占位，不触发对应未实现服务。
3. Sidebar 通过 `GET /api/v1/vault/files?recursive=true` 加载文件树：目录可展开/收起，文件可点击打开，仅展示可编辑 Markdown/允许展示的文件并过滤 `.localnote`（至少过滤任意路径 segment 以 `.` 开头的派生目录，`.localnote` 永远不展示）；加载失败、未配置、不可用均显示可理解的降级状态和重试入口。
4. 点击 Markdown 文件会读取一次 GET，tab 激活；已打开文件再次点击不重复丢弃编辑内容。多个文件可同时打开；tab 可切换、关闭；脏 tab 有稳定的脏标记，关闭脏 tab 要求确认或提供取消，不得静默丢失。
5. 主区使用 `react-resizable-panels` 实现源码/预览并排 split，可拖动调整并保留合理最小尺寸；窄屏或组件不可用时有明确降级布局，不导致编辑器不可用。M2 不实现持久化布局要求，可在 store 中保留 split ratio。
6. CodeMirror 6 显示可编辑源码，启用 Markdown 语言支持/高亮、基础明暗主题切换；输入立即更新当前 session 与 tab dirty 状态。编辑器不做自动格式化、标题改写、换行统一或 wikilink 转换。
7. 预览使用 `unified/remark/rehype` 在前端展示 Standard Markdown：标题、强调、列表、表格、围栏代码、引用、HTML、图片均有可验收输出；使用 GFM 表格；HTML/URL/图片经过安全处理。`[[wiki]]` 在 M2 只作为纯文本或安全占位展示，不解析双链、不建立 links/backlinks。
8. 初次 GET 返回的 `sha256` 是编辑 session 的 base hash。自动保存采用约 800ms 防抖；手动 Ctrl-S 立即触发保存并避免重复并发保存。PATCH 必须带 `expected_sha256`，成功后以响应 hash 作为新 base，并把状态设为 `saved`。
9. 保存状态至少可观察为 `saved`、`saving`、`conflict`、`error`；409 且错误 code 为 `file_conflict` 时显示外部修改提示，提供：`reload`（重新 GET，丢弃本地改动并明确确认/提示）和 `keep local`（保留内存文本，停止自动覆盖，不再 PATCH，直到用户 reload 或明确重试策略）。不提供无 expected hash 的强制覆盖按钮。
10. base64/UTF-8 处理明确：API body 始终是原始 bytes 的 base64；编辑器只对合法 UTF-8 文本启用编辑。未改动文件不能因打开/预览而写回；UTF-8 BOM 和换行应在未编辑时不变；编辑后只把当前 TS 字符串编码为 UTF-8，不由预览管线反序列化。
11. 未配置 Vault、Vault unavailable、网络错误、空树、文件读取失败、文件不存在、预览失败都显示可理解状态；状态页原有 Server/AI 行为不回归。API 不可用时禁止使用假数据冒充成功。
12. 测试完全 mock `fetch`，不访问真实用户 Vault、后端网络、oMLX 或文件系统；覆盖文件树、展开/过滤、打开、编辑、自动保存并断言 `expected_sha256`、409、tabs、split、未配置/降级。E2E 只预留脚本/目录和后续入口，不作为 M2 必过门槛。

## 2. 范围与边界

### 2.1 Must 实现

- `packages/ui`：可访问的基础组件和状态/按钮/布局样式。
- `packages/editor`：CodeMirror 6 React 封装，Markdown language、主题、受控变更回调、销毁与只读/错误状态。
- `packages/workspace`：Zustand workspace store、tabs、editor sessions、file tree、save/conflict 状态、树/文件 API 编排所需纯逻辑。
- `packages/markdown`：仅前端展示层的 unified pipeline，remark-parse + remark-gfm + remark-rehype + rehype-sanitize + rehype-stringify；只读 Preview 组件/函数。
- `packages/protocol`：镜像 M1 Vault 文件树、read、mutation、error DTO 以及前端所需严格 union 类型。
- Web：Tailwind 接入、`react-resizable-panels`、CodeMirror 6、Zustand 接入，重做 `App.tsx` 为工作台并保留既有状态信息能力。
- 后端 minor：确认/修正 `GET /api/v1/vault/files?recursive=true` 满足前端契约；如确有需要，可加只读 `GET /api/v1/vault/tree`，但不得做 Markdown 服务端解析/改写。优先复用现有 files endpoint，避免重复契约。
- 前端 Vitest/RTL 测试和必要的 M1 API 集成回归测试。

### 2.2 明确不实现（M2+ 边界）

- Search/FTS、搜索实际逻辑或搜索结果；Ribbon 搜索仅占位。
- Backlinks、Outgoing Links、wikilink 解析/索引；`[[wiki]]` 只纯文本/安全占位。
- Tags/Properties/frontmatter 索引和 metadata 服务。
- Graph 数据、Graph 渲染；Ribbon 图谱仅占位。
- Command Palette、Quick Open、完整 Hotkeys 系统（Ctrl-S 是本阶段保存必需的单一快捷键，不扩展为快捷键平台）。
- AI chat/write、Agent、Scheduler、WebSocket，以及任何 AI 写路径。
- server/markdown/bytes.py parser 化；后端不能引入 Markdown AST 或把预览 HTML 写回正文。
- 服务端 Markdown 改写、格式化、换行规范化、frontmatter 修改、附件上传/编辑。
- Diff/Undo/History/Recovery/Policy；冲突只提供 reload/keep-local，不实现恢复系统。
- Graph package 的运行时实现；仅保留 placeholder。

## 3. 现状基线与实施前检查

开发 Agent 必须先读取并以当前代码为准：`PLAN-M1.md`、`docs/development-roadmap.md`、`docs/architecture.md`、`server/api/routes/vault.py`、`server/vault/schemas.py`、`apps/web/src/api/client.ts`、`apps/web/src/api/types.ts`、`apps/web/src/App.tsx`、`apps/web/package.json`、`packages/{ui,editor,markdown,workspace,graph,protocol}`、`tests/backend`、`tests/frontend`、`README.md`、根 `package.json`。

已知基线：

- `apps/web/src/App.tsx` 目前是 Phase 0 状态页；实际路径为 `apps/web/src/App.tsx`（不是 `apps/web/App.tsx`）。
- `client.ts` 只有 health/AI GET；`protocol/src/index.ts` 只有 health/AI 类型。
- 五个前端领域包为无入口的 placeholder；workspace/editor/markdown/ui 需补 `package.json` 的 `main`/`types`/exports 与 `src/index.ts`。
- M1 Vault API 已提供：`GET /files`、`GET /file`、POST create、PATCH update、DELETE、POST move；读取响应字段为 `path/content_base64/byte_length/sha256/content_type`，更新错误为统一 `error.code`。
- M1 的 `FileWriteRequest` 允许 schema 层缺省 `expected_sha256`，但业务必须拒绝为 `expected_hash_required`；前端一律发送 hash。
- 当前前端测试位于 `tests/frontend/App.test.tsx`，fetch 全 mock，Vitest 使用 jsdom。

实施前先执行基线命令并记录退出码；若基线失败先诊断，不得把失败归因于 M2。

## 4. 有序任务清单

按依赖顺序实施；同一任务可拆分文件，但不得改变公共契约而不在开发报告中记录。

| 编号 | 任务 | 依赖 | 产出与完成定义 | 预计关键文件 |
|---|---|---|---|---|
| M2-01 | 基线、包入口与依赖锁定 | M1 | 读取现状；为领域包建立 TS 入口和 exports；加入精确/兼容版本依赖；`pnpm install --frozen-lockfile` 可复现 | `package.json`, `pnpm-lock.yaml`, 各包 `package.json`, 各包 `src/index.ts` |
| M2-02 | protocol Vault DTO | M2-01 | TS 类型完整镜像 M1 files/read/mutation/error，编译可用，禁止 runtime HTTP/业务逻辑 | `packages/protocol/src/index.ts`, `README.md`（仅更新 M2 已实现边界） |
| M2-03 | API client Vault 方法 | M2-02 | 类型化 `fetchVaultFiles/read/write`；统一解析 error body；网络、HTTP、409 保留 status/code/path；请求 base64 严格 | `apps/web/src/api/client.ts`, `apps/web/src/api/types.ts`, `apps/web/src/api/errors.ts`（可选） |
| M2-04 | UI primitives 与 Tailwind | M2-01/M2-02 | Button、IconButton、Badge/Status、Panel、EmptyState、TreeRow、Tab 等基础组件；键盘焦点、ARIA、明暗 tokens；Tailwind build 正常 | `packages/ui/src/*`, `apps/web/tailwind.config.ts`, `apps/web/postcss.config.js`, `apps/web/src/index.css` |
| M2-05 | markdown 展示管线 | M2-02 | 纯函数 `renderMarkdown` 或 Preview 组件；Standard Markdown/GFM；sanitize；不可变输入；渲染失败返回安全错误/纯文本降级 | `packages/markdown/src/render.ts`, `src/Preview.tsx`, `src/index.ts` |
| M2-06 | CodeMirror editor 封装 | M2-02 | `CodeMirrorEditor` mount/update/destroy；Markdown language；light/dark theme；受控 `value/onChange`；Ctrl-S 回调；read-only/error fallback | `packages/editor/src/CodeMirrorEditor.tsx`, `src/theme.ts`, `src/index.ts` |
| M2-07 | Workspace Zustand store | M2-02/M2-03 | file tree、open tabs、active tab、session、dirty/save/conflict/error 状态和 action；无 FS 访问；请求竞态可识别 | `packages/workspace/src/store.ts`, `src/types.ts`, `src/selectors.ts`, `src/index.ts` |
| M2-08 | 文件树与 Ribbon | M2-03/M2-04/M2-07 | 启动/刷新加载 recursive tree；目录展开/收起；`.localnote`/隐藏过滤；点击文件打开；未配置/不可用/空树/网络错误状态；Ribbon 四占位 | `apps/web/src/components/Ribbon.tsx`, `Sidebar.tsx`, `FileTree.tsx`, `WorkspaceStatus.tsx` |
| M2-09 | Tabs、编辑区与 split | M2-04/M2-05/M2-06/M2-07 | tabs 切换/关闭/脏标记；源码与预览并排；未加载/读取失败/UTF-8 不可编辑状态；主题切换 | `apps/web/src/components/TabBar.tsx`, `EditorPane.tsx`, `PreviewPane.tsx`, `WorkspaceShell.tsx` |
| M2-10 | autosave、Ctrl-S、冲突 UI | M2-03/M2-06/M2-07/M2-09 | 800ms debounce；同 tab 串行保存；PATCH expected hash；saved/saving/conflict/error；409 reload/keep-local；卸载取消 timer/request | `packages/workspace/src/saveController.ts`, `apps/web/src/components/ConflictBanner.tsx`, `App.tsx` |
| M2-11 | 后端轻量契约确认 | M2-03/M2-08 | 首选不改后端；若 files endpoint 不满足 recursive/过滤约定，只做 `GET /vault/tree` 或 DTO 小修；M1 tests 全绿；严禁 parser/改写 | `server/api/routes/vault.py`, `server/vault/schemas.py`, `tests/backend/test_vault_api.py`（仅必要时） |
| M2-12 | 前端测试与可选 E2E 入口 | M2-05–M2-10 | Vitest/RTL 覆盖矩阵；fetch timer mock；不接真实 FS；E2E 目录/README 仅预留而非必过 | `tests/frontend/*.test.tsx`, `tests/frontend/mocks.ts`, `apps/web/src/test/*`, `e2e/README.md`（可选） |
| M2-13 | 文档、构建、范围审计 | M2-11/M2-12 | 更新 README/roadmap/architecture 的 M2 实际状态（不改本计划设计）；完整命令通过；搜索确认 M3+ 禁止项未实现；报告偏差 | `README.md`, `docs/development-roadmap.md`, `docs/architecture.md` |

## 5. 技术方案与关键设计决策

### 5.1 依赖与版本策略

在 Node 22、pnpm 9.15.0、React 18.3、TypeScript 5.7、Vite 5 的现有约束下，建议锁定以下兼容版本（可使用同主版本的最新安全补丁，但开发报告必须记录实际 lockfile 版本）：

| 依赖 | 建议版本 | 使用位置 | 约束 |
|---|---:|---|---|
| `tailwindcss` | `3.4.17` | web devDependency | 与 PostCSS/Vite 现有链路兼容；不升级 Tailwind v4 |
| `postcss` / `autoprefixer` | `8.4.49` / `10.4.20` | web devDependency | 仅用于 Tailwind 构建 |
| `zustand` | `5.0.3` | workspace + web | store 不含 HTTP/FS 副作用 |
| `react-resizable-panels` | `2.1.7` | web | split 仅布局，不承担保存状态 |
| `@codemirror/state` | `6.5.2` | editor | CM6 核心 |
| `@codemirror/view` | `6.36.5` | editor | DOM/editor view |
| `@codemirror/lang-markdown` | `6.3.1` | editor | Markdown 高亮/语言 |
| `@codemirror/commands` | `6.8.1` | editor | basic setup/标准命令 |
| `@codemirror/theme-one-dark` | `6.1.2` | editor | dark 基础主题（light 使用默认主题） |
| `unified` | `11.0.5` | markdown | pipeline |
| `remark-parse` | `11.0.0` | markdown | Markdown parse |
| `remark-gfm` | `4.0.0` | markdown | 表格/删除线等 GFM |
| `remark-rehype` | `11.1.1` | markdown | mdast -> hast |
| `rehype-sanitize` | `6.0.0` | markdown | HTML/URL 安全清洗 |
| `rehype-stringify` | `10.0.1` | markdown | 只生成展示 HTML 字符串 |

包之间使用 workspace protocol：`web -> ui/editor/markdown/workspace/protocol`；editor/markdown/ui 不可依赖 web；protocol 不依赖任何包；graph 不加入 M2 runtime。若锁文件现有解析版本不同，以兼容性测试结果为准，不能静默升级 React/Vite/TypeScript 大版本。

### 5.2 CodeMirror 6 集成

`@localnote/editor` 提供单一组件，建议接口：

```ts
export interface CodeMirrorEditorProps {
  value: string;
  onChange: (value: string) => void;
  onSave?: () => void;
  theme: "light" | "dark";
  readOnly?: boolean;
  ariaLabel?: string;
  className?: string;
}
```

实现规则：

1. `useRef<HTMLDivElement>` 创建 `EditorView`，`EditorState.create` 初始 doc 为 `value`，扩展 `markdown()`、`basicSetup`/必要 commands、`EditorView.updateListener`。
2. 外部 `value` 变化只有在与当前 doc 不同且不是本次本地回调来源时 dispatch full-document change；避免 React effect 与 CM listener 循环。
3. `onChange` 只上报用户输入的完整字符串；组件卸载必须 `view.destroy()`；更新 theme 用 compartment reconfigure，不重建文档。
4. `Mod-s`/`Ctrl-s` 通过 keymap 调用 `onSave` 并 `preventDefault`；不实现其他全局 hotkeys。
5. light 使用明确的 `EditorView.theme`，dark 使用 one-dark；高对比度焦点、selection、滚动区域必须可见。readOnly 使用 `EditorState.readOnly.of(true)`/`EditorView.editable.of(false)`。
6. 不把 Markdown 解析/预览逻辑塞入 editor 包；editor 只负责源码编辑。

### 5.3 Zustand workspace store 结构

Store 只管理 UI/session 状态和可注入的 API action；HTTP 调用由 `api/client.ts` 或传入的 service 完成，禁止 store 直接访问 `Path`、File System 或后端以外的数据源。

推荐核心类型：

```ts
type LoadState = "idle" | "loading" | "ready" | "error" | "not_configured" | "unavailable";
type SaveState = "saved" | "saving" | "conflict" | "error";
type EncodingState = "utf8" | "invalid_utf8";

interface FileTreeState {
  entries: VaultFileEntry[];
  expandedPaths: Set<string>; // 或 string[]，序列化/测试更推荐 string[]
  status: LoadState;
  error: WorkspaceError | null;
}

interface EditorSession {
  path: string;
  content: string;
  baseSha256: string;
  baseContentBase64: string; // 可选但建议保留，用于未改动保真判断/诊断
  byteLength: number;
  encoding: EncodingState;
  dirty: boolean;
  saveState: SaveState;
  error: WorkspaceError | null;
  conflict: ConflictInfo | null;
  requestVersion: number;
}

interface WorkspaceTab {
  path: string;
  title: string;
  dirty: boolean;
  loading: boolean;
  error: WorkspaceError | null;
}

interface WorkspaceState {
  tree: FileTreeState;
  tabs: WorkspaceTab[];
  activePath: string | null;
  sessions: Record<string, EditorSession>;
  theme: "light" | "dark";
  splitRatio: number;
  actions: {
    loadTree(): Promise<void>;
    toggleDirectory(path: string): void;
    openFile(path: string): Promise<void>;
    activateTab(path: string): void;
    closeTab(path: string, confirm?: () => boolean): boolean;
    updateContent(path: string, content: string): void;
    save(path?: string, reason?: "auto" | "manual"): Promise<void>;
    reloadConflict(path: string): Promise<void>;
    keepLocal(path: string): void;
    setTheme(theme: "light" | "dark"): void;
    setSplitRatio(ratio: number): void;
  };
}
```

实现要点：

- 不把 `Set` 直接放入持久化层；M2 可只在内存 store 使用 `string[]`，不要求 localStorage 持久化。
- `tabs` 顺序为打开顺序；关闭 active tab 后激活邻近 tab，关闭最后一个显示空白状态。
- `sessions[path]` 与 tab path 一一对应；同一路径打开请求去重，旧请求不能覆盖新请求。
- `dirty` 由 `content` 的 UTF-8 bytes hash 是否等于 `baseSha256` 或显式用户编辑语义决定；推荐编辑后立即 true，保存成功后 false。即便用户编辑回原文，仍可把 dirty 设 false（以编码后的 bytes hash 与 base 比较）但不得提前写回。
- save action 必须串行化每个 path；保存期间的再次输入进入 pending dirty，前一次成功后以最新内容再次防抖保存。不要并发 PATCH 同一文件。
- `keepLocal` 仅改变冲突 UI/自动保存闸门，不更新 base hash，不调用 PATCH；“重试保存”必须先重新 GET 建立新 base，或未来 M3+ 提供显式 diff/merge，M2 不自动合并。

### 5.4 文件树策略

1. 启动 workspace 时调用 `fetchVaultFiles({ recursive: true })`。传 `recursive=true` 明确写入 URL；后端已过滤 `.localnote` 时前端仍做 defense-in-depth 过滤。
2. 统一接受 flat `entries`：按 `path` segment 组装树；目录由 `kind: "directory"` 提供，若后端只返回文件则可推导中间目录但不可把未知条目当文件。排序按 path，目录可置前但规则必须固定并测试。
3. 只允许打开 `kind=file` 且路径以 `.md`/`.markdown`（大小写策略固定为小写比较）结尾的文件；其他附件暂不进入编辑器，可显示为 disabled/unsupported，不发 read。`.localnote`、隐藏派生内容、目录均不可打开。
4. Sidebar 状态区分：`loading`、`not_configured`/HTTP 503 code、`unavailable`、`error`、`ready + empty`。不配置时显示配置提示而非空树假成功；失败保留 retry。
5. 展开状态只影响 UI；点击文件先 `openFile` 再激活，读取失败应保留 tab 的错误态但不伪造 session 内容。

### 5.5 预览管线与安全边界

`@localnote/markdown` 的 `renderMarkdown(source: string): string` 采用：

```text
unified()
  .use(remarkParse)
  .use(remarkGfm)
  .use(remarkRehype, { allowDangerousHtml: true })
  .use(rehypeSanitize, strictOrDocumentSchema)
  .use(rehypeStringify)
```

推荐组件内部使用 `processor.run`/`process` 的异步安全封装，渲染结果用 `dangerouslySetInnerHTML` 仅注入已经 sanitize 的本地结果；更稳妥的是返回 `ReactNode`/受控 HTML renderer，开发 Agent 必须避免未经清洗字符串注入 DOM。

- 标题、强调、列表、表格、代码块、引用按 CommonMark/GFM 输出。
- `allowDangerousHtml` 只允许进入 sanitizer；禁止 script、事件属性、javascript: URL、危险 style；图片仅保留安全 `src`（相对链接可显示但不发请求到后端之外的任意 API；必要时对协议白名单为 http/https/相对路径）。
- `[[wiki]]` 不运行链接插件；默认作为文本显示。若 sanitizer/HTML 转换产生特殊字符，必须安全转义；不将其转换为可点击内部链接。
- 图片/HTML 仅展示；预览永不调用 PATCH/POST，永不把输出写回 editor content。
- 处理失败时展示“Preview unavailable”并提供源码仍可编辑；不能因预览失败阻塞保存。

### 5.6 API、base64 与 UTF-8

`packages/protocol` 为 TS 权威镜像（Python `server/vault/schemas.py` 仍是后端权威）：

```ts
export type VaultFileKind = "file" | "directory";
export interface VaultFileEntry { path: string; kind: VaultFileKind; size: number | null; sha256: string | null; }
export interface VaultFileTreeResponse { root: string; entries: VaultFileEntry[]; generated_at: string; }
export interface FileReadResponse { path: string; content_base64: string; byte_length: number; sha256: string; content_type: string | null; }
export interface FileMutationResponse { path: string; sha256: string | null; byte_length: number | null; operation: "created" | "updated" | "deleted" | "moved"; }
export type VaultErrorCode = "vault_not_configured" | "vault_unavailable" | "path_traversal" | "symlink_escape" | "not_found" | "already_exists" | "file_conflict" | "expected_hash_required" | "invalid_request" | "file_too_large" | "not_a_file" | "not_a_directory" | "atomic_write_failed" | "watcher_unavailable" | "internal_error";
export interface VaultErrorBody { error: { code: VaultErrorCode | string; message: string; path: string | null }; }
```

请求方法建议：

```ts
fetchVaultFiles(options?: { recursive?: boolean; includeHidden?: boolean }): Promise<VaultFileTreeResponse>
fetchVaultFile(path: string): Promise<FileReadResponse>
patchVaultFile(path: string, contentBase64: string, expectedSha256: string): Promise<FileMutationResponse>
```

编码实现固定：

- 浏览器读取 `content_base64` 时使用严格 base64 decoder（可用 `atob` + `Uint8Array`），再 `new TextDecoder("utf-8", { fatal: true })`；不吞掉非法 UTF-8。
- 非 UTF-8 文件显示 `invalid_utf8`/不可编辑降级，保留元数据，不 PATCH；不能用替换字符写回破坏原 bytes。M2 的 Standard Markdown 编辑范围是 UTF-8。
- 编码保存用 `TextEncoder` 得到原始 UTF-8 bytes，再 base64；不得通过 `btoa(string)` 直接处理 Unicode，也不得改变 CRLF/LF。
- 如果初始 base64 bytes 有 UTF-8 BOM，decode 后应保留 BOM（即 content 字符串含 `\uFEFF`）并在未修改时禁止触发保存；用户编辑后按当前字符串编码，计划中不做 BOM 自动策略改变。开发测试必须覆盖 Unicode/Emoji、CRLF、无末尾换行和 BOM 未修改保真。
- API client 不把 response body 当错误 message 直接展示；解析统一 `ApiError { status, code, message, path }`，message 可安全展示但不显示 root 绝对路径。

### 5.7 自动保存与冲突时序

以每个 `EditorSession.baseSha256` 为基准，严格执行：

```text
open GET -> content_base64, sha256 = base
user input -> content=current, dirty=true, status=saved (或 pending)
wait 800ms after last input
  -> if dirty && encoding=utf8 && not conflict && no save in flight: save
save:
  status=saving
  encode current string to UTF-8/base64
  PATCH {path, content_base64, expected_sha256: base}
  2xx -> response.sha256 becomes base; dirty=false; status=saved
  409 error.code=file_conflict -> status=conflict; stop autosave; show reload/keep-local
  4xx/5xx/network -> status=error; preserve content/base/dirty; show retry; no retry storm
```

手动 Ctrl-S：取消/刷新 debounce timer，若状态 conflict 则不发送请求并提示先 reload；若 saving 则标记 pending manual，不并发；否则立即走同一 save action。成功响应只接受对应 path + requestVersion；过时请求响应不得覆盖较新的 session。

竞态及组件生命周期：

- `setTimeout` 约 800ms（允许测试注入 delay）；unmount/close 前清理 timer。
- React StrictMode 下不得重复保存；save controller 要求幂等/去重。
- 自动保存失败不丢内容；error 可手动 retry，仍带保存当时的 base hash。409 后 retry 不得直接沿用旧 hash 强写。
- reload conflict 重新 GET 当前服务器 bytes/hash，替换本地 session，dirty=false、status=saved；必须由用户明确点击并在 UI 文案说明丢弃本地修改。
- keep local 保留 current 和旧 base 仅供诊断，状态停在 conflict；不覆盖服务器、不继续定时 PATCH。

### 5.8 主题、布局与降级

- `theme` 是 workspace state 的 `light|dark`，通过 root class/data attribute 同时作用于 Tailwind、editor、preview；初始 light，可提供一个明确的 theme toggle。
- Tailwind content glob 必须覆盖 `apps/web/src/**/*.{ts,tsx}` 与 `packages/*/src/**/*.{ts,tsx}`；不要依赖运行时拼接无法扫描的 class 名。
- `ResizablePanelGroup direction="horizontal"` 包含源码和预览；Sidebar 可作为固定/可隐藏区域，M2 不强制第三列 resizable。设置 `minSize` 防止不可见；split error 使用 stacked source-first fallback。
- 空白工作区显示打开文件提示；未配置、不可用、读取错误、UTF-8 不支持和预览错误分别显示，不用同一个“空白”掩盖故障。
- Ribbon 占位按钮设置 `aria-disabled`/disabled 与“将在 M3/M5/M6 实现”说明；不得点击后发不存在 API。

## 6. 公共接口与数据结构清单

### 6.1 API client 公共接口

- `fetchHealth(): Promise<HealthResponse>`（保留）。
- `fetchAIStatus(): Promise<AIStatusResponse>`（保留）。
- `fetchVaultFiles({ recursive, includeHidden }): Promise<VaultFileTreeResponse>`。
- `fetchVaultFile(path): Promise<FileReadResponse>`。
- `patchVaultFile({ path, contentBase64, expectedSha256 }): Promise<FileMutationResponse>`。
- `ApiError` 扩展 `status?: number`, `code?: VaultErrorCode|string`, `path?: string|null`；HTTP 409 可通过 `code === "file_conflict"` 判断。

### 6.2 Editor session / workspace 接口

见 §5.3。必须保持 `baseSha256`、当前 `content`、`dirty`、`saveState`、`encoding`、`conflict` 分开，不以 tab label 猜测状态；路径均为 M1 返回的 root-relative POSIX 字符串。

### 6.3 冲突 DTO

```ts
export interface ConflictInfo {
  code: "file_conflict";
  path: string;
  message: string;
  baseSha256: string;
  detectedAt: string;
}
export interface WorkspaceError {
  kind: "network" | "api" | "decode" | "preview" | "unknown";
  code?: string;
  status?: number;
  message: string;
  path?: string;
}
```

### 6.4 UI 包接口

基础组件至少包括 `Button`、`IconButton`、`Badge`、`Panel`、`EmptyState`、`StatusBanner`、`TreeRow`、`Tab`/`TabBar`；组件接收 className/children，使用原生 button semantics、`aria-label`、`aria-selected`、`role=tab/treeitem` 等可访问属性。UI 包不得 import API client 或 Zustand。

## 7. 目录树与关键文件用途

目标目录（具体命名可微调，但职责不可混淆）：

```text
apps/web/
  package.json                 # M2 web 依赖与 scripts
  tailwind.config.ts           # content、主题 tokens
  postcss.config.js            # Tailwind/PostCSS
  src/
    App.tsx                    # 页面装配、health/AI 保留、workspace shell
    main.tsx                   # React 入口与全局 CSS
    index.css                  # Tailwind layers + app tokens
    api/
      client.ts                # typed REST calls；不触碰 FS
      types.ts                 # protocol re-export
    components/
      Ribbon.tsx               # 文件/搜索/图谱/AI 入口
      WorkspaceShell.tsx       # 整体布局与状态装配
      Sidebar.tsx              # 文件树容器/加载降级
      FileTree.tsx             # flat entries -> tree、展开/点击
      TabBar.tsx               # tabs、dirty、close
      EditorPane.tsx           # editor 包适配、save state
      PreviewPane.tsx          # markdown Preview 适配
      ConflictBanner.tsx       # 409 reload/keep-local
      WorkspaceStatus.tsx      # 空白/未配置/不可用/错误
    test/                      # web-local test helpers（如需要）
packages/protocol/
  src/index.ts                 # Vault DTO、error union、既有 AI/health DTO
packages/ui/
  src/{Button,IconButton,Badge,Panel,EmptyState,StatusBanner,TreeRow,Tab,index}.tsx
packages/editor/
  src/CodeMirrorEditor.tsx     # CM6 lifecycle/controlled wrapper
  src/theme.ts                  # light/dark compartments
  src/index.ts
packages/markdown/
  src/render.ts                # unified pipeline + safe fallback
  src/Preview.tsx              # read-only preview
  src/index.ts
packages/workspace/
  src/types.ts                 # session/tab/tree/save types
  src/store.ts                 # Zustand state/actions
  src/selectors.ts             # derived selectors/tree helpers
  src/saveController.ts         # debounce/serial save orchestration
  src/index.ts
packages/graph/
  README.md                    # 继续 placeholder；不得加入 M2 graph runtime
tests/frontend/
  setup.ts                     # 保留 cleanup/fetch un-stub
  mocks.ts                     # typed fetch responses/helpers
  App.test.tsx                 # 首页与工作台集成覆盖
  Workspace.test.tsx           # tree/open/edit/save/conflict/degraded
  MarkdownPreview.test.tsx     # Markdown/sanitize/wiki 展示
  Editor.test.tsx              # CM wrapper lifecycle/theme/save
  WorkspaceStore.test.ts       # store action/race/dirty 状态
  Split.test.tsx               # resizable panels smoke/accessibility
e2e/README.md                 # 可选预留，M2 不接真实服务
```

后端仅在契约不满足时触碰：`server/api/routes/vault.py`、`server/vault/schemas.py` 和对应 `tests/backend/test_vault_api.py`；不得修改 `server/markdown/bytes.py` 为 parser。

## 8. 测试矩阵与验收命令

### 8.1 前端单元/组件矩阵

| 区域 | 场景 | 断言 |
|---|---|---|
| protocol/client | files/read/patch 成功 | URL/query、method/body、类型字段正确 |
| client errors | 503 not configured、网络失败、409 | `ApiError.status/code/path` 保留，消息安全 |
| tree | recursive flat entries、嵌套目录 | 展开/收起、目录/文件排序、点击只读 Markdown |
| tree filter | `.localnote/state.json`、隐藏目录、附件 | 派生目录不显示，不发送错误 read |
| open | 点击文件 | GET path 正确，tab/session/baseSha256 建立，重复点击去重 |
| editor | 输入 Unicode/Markdown | content 更新、dirty/脏标记、预览同步、未自动格式化 |
| autosave | fake timers 等待 800ms | PATCH 一次，body 的 `expected_sha256` 等于 GET hash，UTF-8 base64 正确 |
| manual save | Ctrl-S | 立即 PATCH，取消 debounce，不重复并发 |
| save success | 2xx mutation | 新 sha256 成为 base，saved、dirty false |
| conflict | PATCH 返回 409 `file_conflict` | conflict banner；不再自动 PATCH；reload 丢本地并 GET；keep local 保留内容 |
| error | 500/network | error 状态，内容不丢，手动 retry 可用 |
| tabs | 多开、切换、关 tab、脏关闭 | active 路径、脏标记、关闭确认、空白态正确 |
| split | panel render/resize | source+preview 可见，min size/fallback，访问属性存在 |
| preview | headings/emphasis/list/table/fence/quote/HTML/image | 正确展示；script/onerror/javascript 被清洗；wiki 不成为内部链接 |
| encoding | BOM/CRLF/Emoji/非法 UTF-8 | 未改动不写；UTF-8 正常编辑；非法 UTF-8 只读降级不 PATCH |
| degraded | 503/empty/read failure/preview failure | 分别显示 not configured/unavailable/empty/error，核心 UI 不崩 |
| regression | 原有 status page | Server/AI 文案和 Refresh 行为继续通过 |

测试 mock 规范：

- `fetch` mock 按 method + URL + body 路由，`Response.json()` 返回 M1 形状；不要只 `mockResolvedValue` 而失去请求断言。
- 使用 `vi.useFakeTimers()` 测试 debounce，并在结束时 `runOnlyPendingTimers`/restore；测试 setup 继续 cleanup/un-stub globals。
- 409 response 必须是 `{ error: { code: "file_conflict", message, path } }`，断言前端没有第二次 PATCH。
- 以真实 `TextEncoder` 计算预期 base64；检查 Unicode 不被 `btoa` 错误处理。
- CodeMirror 测试至少验证 role/aria label、初始文档、输入回调、Ctrl-S 回调与卸载；若 jsdom 对完整 layout 支持不足，隔离 view 测试并对 resizable 组件做 smoke mock，但不得删掉应用级 source/preview 验证。

### 8.2 后端与构建门禁

从仓库根目录执行并记录每条退出码：

```bash
python3 --version
uv sync --dev
pnpm install --frozen-lockfile
python -m pytest -q                         # M1 既有 166 用例保持通过
pnpm --filter @localnote/protocol typecheck
pnpm --filter @localnote/ui typecheck      # 若包配置提供该脚本
pnpm --filter @localnote/editor typecheck
pnpm --filter @localnote/markdown typecheck
pnpm --filter @localnote/workspace typecheck
pnpm --filter @localnote/web typecheck
pnpm --filter @localnote/web test -- --run
pnpm --filter @localnote/web build
python -m compileall server
./scripts/check.sh
```

若各包没有独立 `typecheck`，根门禁必须至少通过 web 依赖引用后的统一 `tsc --noEmit`；不要因脚本缺失而跳过包源码类型检查。前端 build 后检查 `apps/web/dist` 产物存在且没有 unresolved import。

### 8.3 受控手工/API smoke（可选但建议）

仅使用 `tests/fixtures/vault` 的临时副本，并把环境变量指向该副本：

```bash
LOCALNOTE_VAULT__ROOT="$PWD/tests/fixtures/vault" \
  python -m uvicorn server.api.main:app --host 127.0.0.1 --port 3780
curl -fsS 'http://127.0.0.1:3780/api/v1/vault/files?recursive=true'
curl -fsS --get 'http://127.0.0.1:3780/api/v1/vault/file' --data-urlencode 'path=notes/example.md'
```

启动 Web 后人工验收：加载树 → 展开目录 → 打开两个 Markdown → 输入 Unicode → 等待约 800ms → 检查服务器 bytes/hash → 外部修改 fixture → 输入并等待保存 → UI 显示 conflict → reload 丢弃本地 / 重新打开并 keep local。不得操作真实用户 Vault；smoke 结束比较 fixture 外 sentinel hash。

## 9. 依赖、风险与降级

| 风险 | 影响 | 降级/处理 |
|---|---|---|
| CM6 与 React 受控循环 | 光标跳动、重复 onChange、重复保存 | EditorView ref + transaction guard + compartment；组件测试；不使用每次 render 重建 view |
| unified/rehype XSS | Markdown HTML/图片执行脚本 | `rehype-sanitize` 严格 schema；安全 URL；预览失败回退纯文本；不把未清洗 HTML 注入 DOM |
| 非 UTF-8/BOM | 编辑后 bytes 损坏 | fatal decoder；非 UTF-8 只读；未编辑不 PATCH；BOM 作为字符串内容保留并测试 |
| base64 Unicode | `btoa` 异常或数据截断 | TextEncoder/Uint8Array 自行编码；请求测试断言真实 payload |
| 自动保存竞态 | 旧响应覆盖新内容/外部修改被覆盖 | per-path serial queue、requestVersion、expected hash、409 闸门；不做 last-write-wins |
| debounce 与卸载 | 内存泄漏、关闭 tab 后仍写入 | timer cleanup、AbortController/请求版本；save controller 单测 |
| M1 files 响应不含目录 | 树无法展开 | 使用已有 `kind: directory`；必要时前端推导目录并保守处理；如契约实在不足才加 tree endpoint |
| API 503/网络中断 | 工作台不可用 | 明确 not configured/unavailable/error，retry；不使用假数据；已打开内存内容仍可查看 |
| resizable-panels jsdom | 测试不稳定 | 组件边界 mock + 应用 source/preview 集成断言；生产构建必须真实包通过 |
| Tailwind content 漏扫 | 线上无样式 | glob 覆盖 app 与 packages；build 后检查关键 class/人工 smoke |
| 依赖体积/版本冲突 | 安装或 build 失败 | 遵循 Node 22/pnpm 9；锁版本；不升级大版本；记录实际 lockfile |
| HTML/图片外部请求 | 隐私/网络副作用 | 仅展示，不做代理；可通过 sanitizer/协议限制；文档说明 Markdown image 的浏览器请求行为 |
| macOS Unicode/NFD | path 显示和点击不一致 | 以 API 返回 POSIX path 为 key，不自行 normalization/rename；测试中文/Emoji/空格 |
| M1 后端变更回归 | Vault 安全性退化 | M2 默认不改后端；任何改动必须跑完整 166 用例和路径安全测试 |

## 10. 明确假设、未决事项与 M3 入口

### 10.1 假设

1. M1 API 已部署并保持 `GET /api/v1/vault/files?recursive=true`、`GET /file`、`PATCH /file` 的字段/错误契约；前端不绕过 API 访问本地 FS。
2. Vault Markdown 编辑范围是合法 UTF-8；非 UTF-8 附件或正文在 M2 只读降级，不引入二进制编辑器。
3. `expected_sha256` 是带 `sha256:` 前缀的小写规范 hash；client 原样使用服务返回值，不自行重算后替换 base（可重算用于诊断）。
4. M2 默认不持久化 tabs/session 到 localStorage，刷新丢失内存编辑内容是已知限制；只保证单次页面生命周期内的 tabs 和自动保存。
5. 默认单用户、本机回环 API；M2 不增加鉴权、多用户锁、远端同步或 WebSocket。
6. 预览运行在浏览器，Markdown source 可能包含 HTML/图片，安全 sanitizer 优先于完整 HTML 保真；source 本身始终不改。
7. Ribbon 的 Search/Graph/AI 是占位 UI，不代表能力已连接；M3/M5/M6 再定义真实协议。
8. M1 已确认 `.localnote` 永远被后端过滤；前端 defense-in-depth 不改变服务端安全边界。

### 10.2 未决事项（开发 Agent 不得静默决定）

- 是否将 `.md` 以外的 `.markdown` 文件开放编辑（本计划建议支持 `.md` 与 `.markdown`，需在 UI/测试固定）。
- Tab 关闭脏内容使用浏览器 `confirm` 还是自定义 Dialog（两者都必须可取消；推荐 UI Dialog 便于 RTL）。
- split ratio 是否在 sessionStorage 持久化（非 M2 成功标准，默认仅内存）。
- M1 是否需要新增 `/api/v1/vault/tree`；优先不新增，只有 flat files 无法可靠表达目录时才补只读 endpoint。
- sanitizer 对相对图片、HTML table/style 的允许白名单；安全默认优先，需通过安全测试固定。
- BOM 编辑策略：本计划要求保留 BOM 字符语义并测试未编辑 round-trip；若实现选择单独保存 BOM flag，必须在开发报告说明，不能悄悄删除。
- 是否允许预览图片加载外部 http(s)；默认可以在浏览器展示但应有协议限制/文档告警，禁止 javascript/data 等危险协议。

### 10.3 M3 入口

M3 在 M2 完整门禁通过后开始：以 M2 保留的原始 `content`/`baseSha256` 和只读预览边界为输入，实现 frontmatter/Properties 解析（未知字段保留）、wikilink/backlink/outgoing link 解析与索引、关键词搜索 UI。M3 必须继续把 parser 作为展示/派生层，不改写 M1 `server/markdown/bytes.py`，也不能让索引故障阻塞编辑和保存。Graph 数据/渲染仍留至 M5，FTS/SQLite 仍留至 M4。

## 11. 文档、审计与交付格式

### 11.1 文档更新

M2 开发完成后（不是本计划编制阶段）更新：

- `README.md`：标记 M2 工作台实际完成项、启动方式、降级状态、编辑/冲突语义和 M2+ 禁止项。
- `docs/development-roadmap.md`：M2 标记完成，写明门槛与 M3 入口。
- `docs/architecture.md`：补充 Web Workspace → Vault REST → M1 bytes 的数据流、前端预览为展示层、冲突保护和故障隔离。
- 包 README：移除对应 placeholder，明确 public exports；`packages/graph` 仍保持 M5 placeholder。

规划阶段不得修改上述文件；本阶段计划只写入 `PLAN-M2.md`。

### 11.2 开发 Agent 交付格式

完成后必须返回：

1. M2-01 至 M2-13 每项完成/未完成、实际文件路径和偏差；
2. protocol DTO/API client 的实际 JSON 契约；
3. CodeMirror 生命周期、Markdown language、主题与 Ctrl-S 实现说明；
4. Zustand store 状态/action、tab 关闭和请求竞态策略；
5. 预览 sanitizer、wiki 占位和 source 不写回证明；
6. 自动保存时序、实际 debounce 值、expected hash 和 409 reload/keep-local 行为；
7. UTF-8/base64/BOM/CRLF 测试结果；
8. 前端测试、后端 166 用例、typecheck/build/compileall 的命令、退出码和覆盖摘要；
9. 任何后端改动、依赖版本、未决事项和风险；
10. 明确确认未实现 Search/FTS/Links/Tags/Properties index/Graph/AI/Agent/Scheduler/WebSocket/服务端 parser/SQLite/Diff/Undo，并确认未修改 `PLAN.md` / `PLAN-M1.md`。

### 11.3 计划完成定义

本规划任务的完成定义是：本文件以 UTF-8 写入工作区根目录 `PLAN-M2.md`，正文包含目标、范围、逐项任务、设计决策、公共结构、目录职责、风险降级、测试矩阵、验收命令、假设、未决事项及 M3 入口；规划阶段不创建任何实现代码，不修改 `PLAN.md` 或 `PLAN-M1.md`。
