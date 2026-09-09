# LocalNote 附件上传 — 开发日志（ATT-01 … ATT-22）

- 依据：`PLAN-ATTACHMENTS.md` v1.1（用户已确认，未修改）
- 开发者：Develop Agent
- 基线：后端 pytest `757 passed / 3 skipped`（开发前实测）
- 状态图例：完成 / 部分完成 / 未完成

## 基线记录

- [x] 开发前后端基线实测：`757 passed, 3 skipped, 2 warnings in 32.90s`。
- [x] 开发后后端：`830 passed, 3 skipped`（+73 新增用例）。
- [x] 开发前前端：`25 files / 159 tests passed`。
- [x] 开发后前端：`26 files / 194 tests passed`（+35 新增用例）。

## 逐项状态

| 编号 | 状态 | 改动文件 | 完成定义 | 测试命令与结果 |
|---|---|---|---|---|
| ATT-01 | 完成 | `server/vault/attachments.py`（新增） | 纯函数命名/目标目录校验：空格、中文、emoji、超长、无扩展名、点文件、危险字符、Windows 保留名、UTF-8 字节边界、digest 提示后缀、`-2/-3` 递增 | `pytest tests/backend/test_vault_attachments.py -k "basename or attachment_name or target_directory"` 全通过 |
| ATT-02 | 完成 | `server/vault/atomic_write.py`（`atomic_create_stream`/`iter_chunks`/`ReplayableStream`） | 同目录临时文件 0600/O_EXCL/O_NOFOLLOW、1 MiB 分块、增量 SHA-256、超限清理、no-overwrite 提交、流异常清理 | `pytest -k "atomic_create_stream or iter_chunks or commit_races"` 全通过 |
| ATT-03 | 完成 | `server/vault/service.py`（`upload_attachment_bytes/stream`、`_resolve_upload_directory`） | 目标目录 root-relative 校验+必须已存在+非 symlink、不自动建目录、命名碰撞重试、目录/目标提交前后复核 | `pytest -k "upload"` 全通过 |
| ATT-04 | 完成 | `server/vault/schemas.py`、`server/api/main.py`（CORS + HEAD）、`server/vault/errors.py`（未新增码） | 错误体 `{"error":{"code","message","path"}}`；400/404/409/413/422/503 映射；不泄漏 root/异常 | `pytest -k "error or leak or 413"` 全通过 |
| ATT-05 | 完成 | `server/vault/schemas.py`、`server/api/routes/vault.py` | `AttachmentUploadRequest/Response`（`extra=forbid`）、`POST /vault/attachments` 201 | `pytest -k "json_upload"` 全通过 |
| ATT-06 | 完成 | `server/api/routes/vault.py`、`server/vault/service.py` | `POST /vault/attachments/multipart`；路由只读 `UploadFile` 有界分块，Service 流式落盘 | `pytest -k "multipart"` 全通过 |
| ATT-07 | 完成 | `server/api/routes/vault.py`、`server/vault/service.py` | `GET /vault/resource?path=` 只读、Content-Type/Length/Disposition、无 Range、超限 413、HEAD 复用元数据 | `pytest -k "resource"` 全通过 |
| ATT-08 | 完成 | `server/config.py`（`ATTACHMENT_JSON_MAX_BYTES`）、`server/api/main.py`（CORS HEAD） | 10 MiB 协议常量，`max_file_bytes` 语义不变 | `pytest tests/backend/test_config.py` 全通过 |
| ATT-09 | 完成 | `packages/protocol/src/index.ts`、`apps/web/src/api/types.ts` | DTO/错误码镜像 + 路径解析纯函数 | `pnpm --filter @localnote/protocol typecheck` 通过 |
| ATT-10 | 完成 | `apps/web/src/api/client.ts` | base64/multipart 上传 + `vaultResourceUrl`（URLSearchParams 编码） | `AttachmentUpload.test.tsx` 全通过 |
| ATT-11 | 完成 | `packages/workspace/src/{types,store}.ts` | 上传 action、入口决定 target、分块 base64、引用插入（含 `../`）、失败不改正文、vaultGeneration 守卫 | `AttachmentUpload.test.tsx` 26 用例全通过 |
| ATT-12 | 完成 | `packages/editor/src/CodeMirrorEditor.tsx`、`apps/web/src/components/{EditorPane,AttachmentToolbar}.tsx` | 工具栏选择器、编辑器 drop/paste（文本粘贴不拦截）、selection 插入 | `AttachmentUpload.test.tsx` + `App.test.tsx` 全通过 |
| ATT-13 | 完成 | `apps/web/src/components/{WorkspaceShell,PreviewPane,AttachmentPreview}.tsx` | Shell 统一传 note/目录/resolver/上传回调；附件预览面板；无 blob URL | `AttachmentUpload.test.tsx` shell 用例全通过 |
| ATT-14 | 完成 | `packages/markdown/src/{render,Preview}.tsx` | 保留 remark→rehype-raw→sanitize→stringify；HAST 属性层 resolver；禁危险协议/属性 | `MarkdownPreview.test.tsx` 6 用例全通过 |
| ATT-15 | 完成 | `apps/web/src/components/{FileTree,Sidebar,WorkspaceShell}.tsx` | 附件行可点击打开/下载；目录右键上传到真实目录；Markdown 仍走 openFile | `FileTreeDrag.test.tsx` 12 用例全通过 |
| ATT-16 | 完成 | `apps/web/src/i18n/{zh-CN,en-US,errors}.ts`、`apps/web/src/styles.css` | 中英文附件键 + 稳定错误码映射（不展示服务端路径） | `pnpm --filter @localnote/web typecheck` + 组件用例通过 |
| ATT-17 | 完成 | 仅测试（`tests/backend/test_vault_attachments.py`） | 附件不成为索引/Graph 节点；相对引用不破坏既有 wikilink 解析 | `pytest -k "graph_node or relative_attachment"` 全通过 |
| ATT-18 | 完成 | 仅测试 | `attachment_write` 对 Agent 仍 deny；用户直传不调用 PolicyEngine | `pytest -k "policy_attachment or does_not_invoke_policy"` 全通过 |
| ATT-19 | 完成 | `docs/vault-spec.md` §8.1、`docs/architecture.md` M9、`README.md` | 记录入口→目录、契约、命名、引用/resolver、资源 URL、阈值、错误映射、Policy 边界 | 文档评审（人工） |
| ATT-20 | 完成 | `tests/backend/test_vault_attachments.py`（新增 73 用例） | 目录/symlink/越界/隐藏/.localnote/命名/流式/竞态/资源/listing/watcher 全覆盖 | `pytest tests/backend/test_vault_attachments.py -q` → `73 passed` |
| ATT-21 | 完成 | `tests/frontend/AttachmentUpload.test.tsx`（新增 26）、`MarkdownPreview.test.tsx`（+5）、`FileTreeDrag.test.tsx`（+4）、`tests/frontend/setup.ts`（File.arrayBuffer polyfill） | 分块编码/阈值分流/四入口 target/引用插入/附件打开/URL 编码/sanitize/失败不改正文 | `pnpm --filter @localnote/web test` → `194 passed` |
| ATT-22 | 完成 | 验收运行 | `./scripts/check.sh` 全绿 | 见下方「门禁输出」 |

## 门禁输出（ATT-22）

`./scripts/check.sh`（2026-08-16，exit 0）：

```
== backend pytest ==
830 passed, 3 skipped, 2 warnings in 32.86s
== typecheck (@localnote/protocol) ==   OK
== typecheck (@localnote/graph) ==      OK
== typecheck (@localnote/web) ==        OK
== frontend tests (vitest run) ==
 Test Files  26 passed (26)
      Tests  194 passed (194)
== frontend build ==
✓ built in 3.60s
== all checks passed ==
```

新增/扩展测试文件与用例数：

| 文件 | 用例数 | 说明 |
|---|---|---|
| `tests/backend/test_vault_attachments.py` | 73 | 命名/目录安全/流式/竞态/资源/Policy/索引兼容 |
| `tests/frontend/AttachmentUpload.test.tsx` | 26 | 四入口/分流/引用插入/URL 编码/sanitize/失败不改正文 |
| `tests/frontend/MarkdownPreview.test.tsx` | +5（共 6） | resolver、`../`、危险协议与属性 |
| `tests/frontend/FileTreeDrag.test.tsx` | +4（共 12） | 目录右键上传、附件行打开/下载 |

后端基线 757 → 830（+73），前端 159 → 194（+35），均高于基线通过数。

## 偏差记录

1. **新增运行依赖 `python-multipart`**
   - 偏差：`pyproject.toml` 原本没有 `python-multipart`，FastAPI 无法注册 multipart 路由（计划 ATT-06 要求 multipart 端点）。
   - 理由：Starlette/FastAPI 解析 `multipart/form-data` 的必需依赖；没有它无法实现 ATT-06。
   - 影响：仅新增一个运行依赖并更新 `uv.lock`；不改变任何既有依赖版本。
   - 建议：保留（属于计划 ATT-06 的最小必要条件）。

2. **multipart 的 `target_directory` 在字段缺失时按 Vault 根处理**
   - 偏差：计划 5.2 写“必填表单字段”，实现为 `Form()` 默认 `""`。
   - 理由：浏览器 multipart 编码器（含 Starlette TestClient/前端 `FormData`）不会提交空字符串字段，因此“空串表示根”与“字段缺失”在协议层不可区分；若强制必填则根目录上传会 422。
   - 影响：仅影响“字段完全缺失”的请求，语义等同空串=根；非法非空值仍被 400 拒绝。
   - 建议：保留，并在 `docs/vault-spec.md` §8.1 记录。

3. **`packages/markdown` 新增 `@localnote/protocol` 依赖与 `@types/hast` 开发依赖**
   - 偏差：计划 ATT-14 要求 render 层做 resolver 回调，需要协议里的路径解析纯函数与 HAST 类型。
   - 理由：避免在 markdown 包内重复实现路径规范化；HAST 类型来自既有 remark/rehype 依赖树。
   - 影响：仅新增 workspace 内部依赖与类型依赖，无运行时新增第三方包。

4. **resolver 支持 root-relative（`/notes/x.png`）引用**
   - 偏差：计划 §5.1 只描述“相对引用”，未明确 `/` 开头引用。
   - 理由：Markdown 中 `/notes/x.png` 若不被解析会在预览中裂图；按 Vault-root-relative 处理与既有 index/wikilink 的 root-relative 语义一致。
   - 影响：不改变 `..` 越界拒绝与危险协议拒绝；仍不把 API path 直接写入正文。
   - 建议：保留。

5. **`File.arrayBuffer` 测试 polyfill**
   - 偏差：`tests/frontend/setup.ts` 新增 jsdom 缺失的 `File.prototype.arrayBuffer`。
   - 理由：上传路径用 `File.arrayBuffer()` 读取 bytes；jsdom 未实现该标准 API。
   - 影响：仅测试环境；生产浏览器原生支持。

6. **上传前对目标目录内既有名字做 symlink 扫描**
   - 偏差：计划 ATT-03 只要求“目标路径与 symlink 在解析前/打开前/提交前复核”。
   - 理由：实现中若把 symlink 当作“名字已被占用”跳过，会在 `foo.txt` 为 symlink 时静默写入 `foo-2.txt`（测试暴露）；必须把 symlink 视为安全错误而非重命名。
   - 影响：目标目录内存在任意 symlink 时上传被 400 拒绝（保守但安全），既有 Vault 读契约对 symlink 同样拒绝。
   - 建议：保留。

7. **watcher 显式丢弃隐藏段/临时文件事件**
   - 偏差：计划风险节要求“必要时让 watcher 忽略服务临时文件”。
   - 理由：上传用 `.localnote-tmp-*` 暂存，watcher 原本只过滤 `.localnote` 路径；隐藏段过滤可避免索引抖动。
   - 影响：watcher 不再向消费者派发任何隐藏路径事件（与 listing 的隐藏规则一致）。

8. **CodeMirror 拖拽/粘贴改用原生 DOM 监听**
   - 偏差：`CodeMirrorEditor` 的 drop/dragover/paste 从 `EditorView.domEventHandlers` 改为 `contentDOM.addEventListener`。
   - 理由：实测 `domEventHandlers` 的 drop/paste 在 jsdom 下不触发（无法自动化验收），原生监听在浏览器与 jsdom 均可靠。
   - 影响：行为等价；监听器在视图销毁时显式移除；Ctrl/Cmd+S 仍走原 `domEventHandlers` 分支（未改动）。
