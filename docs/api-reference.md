# LocalNote API 参考

本文件是 [`README.md`](../README.md) 的细节附录：**端点一览、错误契约与环境变量全表**。
面向「已经跑起来，想知道具体调什么」的读者；想先了解项目本身请看 README。

所有接口前缀 `/api/v1`，请求与响应均为 JSON。统一错误体：

```json
{"error": {"code": "…", "message": "…", "path": "…|null"}}
```

AI 结构化错误额外携带 `meta: {prompt_version, model}`。

---

## Vault Core（M1）

| 方法/路径 | 说明 | 主要错误 |
|---|---|---|
| `GET /api/v1/vault/files?path=&recursive=&include_hidden=` | 列表（`.localnote` 永远过滤） | 400/404/503 |
| `GET /api/v1/vault/file?path=` | 读取（base64 + sha256 + content_type） | 400/404/413/503 |
| `POST /api/v1/vault/file` | 创建（父目录须存在） | 400/404/409/413/503 |
| `PATCH /api/v1/vault/file` | 原子更新（必须 `expected_sha256`） | 400/404/409/413/503 |
| `DELETE /api/v1/vault/file` | 删除（必须 `expected_sha256`） | 400/404/409/503 |
| `POST /api/v1/vault/file/move` | 移动/重命名（不覆盖目标） | 400/404/409/500/503 |
| `POST /api/v1/vault/directory` | 新建目录（逐级补建，父目录须存在） | 400/404/409/503 |
| `GET /api/v1/vault/resource?path=` | 只读原始 bytes（图片预览/附件下载） | 400/404/413/503 |

示例：

```bash
curl -fsS 'http://127.0.0.1:3780/api/v1/vault/files?recursive=true'
curl -fsS --get 'http://127.0.0.1:3780/api/v1/vault/file' \
  --data-urlencode 'path=中文/😀 note.md'
curl -fsS -X POST 'http://127.0.0.1:3780/api/v1/vault/file' \
  -H 'content-type: application/json' \
  -d '{"path":"notes/new.md","content_base64":"IyBIZWxsbwo="}'
# 用上一步读到的 sha256 做原子更新：
curl -fsS -X PATCH 'http://127.0.0.1:3780/api/v1/vault/file' \
  -H 'content-type: application/json' \
  -d '{"path":"notes/new.md","content_base64":"IyBVcGRhdGVkCg==","expected_sha256":"sha256:<hash>"}'
```

错误示例：`GET ...?path=../outside.md` → 400 `path_traversal`；root 未配置 →
503 `vault_not_configured`；hash 过期 → 409 `file_conflict`。

## 附件上传（M9）

| 方法/路径 | 说明 | 主要错误 |
|---|---|---|
| `POST /api/v1/vault/attachments` | 用户直传附件（JSON `content_base64`，≤10 MiB），201 | 400/404/409/413/422/503 |
| `POST /api/v1/vault/attachments/multipart` | 用户直传附件（multipart 流式，>10 MiB），201 | 400/404/409/413/422/503 |

四个入口与落点（`target_directory` 由前端按入口决定，空串 = Vault 根）：

- 文件树目录右键「上传到该目录」→ **被右键的那个目录**；
- 工具栏「插入附件」、编辑器/预览区拖拽、剪贴板粘贴图片 → 当前笔记所在目录
  （根笔记为空串）。

要点：目标目录**必须已存在**（不自动建 `attachments/YYYY-MM/` 或任何中间
目录，`attachments/` 不是唯一落点）；禁止覆盖（同名 `-2`/`-3` 递增 + 原子
no-overwrite 兜底，竞争最终 409 `already_exists`）；隐藏段、`.localnote`、
symlink、越界路径一律拒绝；正文引用是相对当前笔记的 POSIX 路径（必要时
`../`）；预览经 `/api/v1/vault/resource?path=...` 读取并由 sanitize 管线
保护；上传是用户直传旁路，不经过 Policy，`attachment_write` 对 Agent 仍
永久拒绝。详见 [`vault-spec.md`](./vault-spec.md) §8.1。

## 回收站（软删除）

| 方法/路径 | 说明 |
|---|---|
| `GET /api/v1/trash` | 列出回收站条目（原路径、删除时间、剩余天数） |
| `POST /api/v1/trash` | 把文件或**整个文件夹**移入回收站（`expected_sha256` 守卫文件） |
| `POST /api/v1/trash/{id}/restore` | 恢复（可选 `rename_if_occupied`） |
| `DELETE /api/v1/trash/{id}` | 彻底删除单项 |
| `DELETE /api/v1/trash` | 清空回收站 |

条目落在服务自有的 `.localnote/trash/`（`<uuid>/<原名>`，移动而非复制），
默认保留 **30 天**（`LOCALNOTE_VAULT__TRASH_RETENTION_DAYS`，1–3650）。
`DELETE /vault/file` 保留为**字节级永久删除**原语。

## Metadata / Links / Search / Index（M3 契约 + M4 SQLite 底层）

| 方法/路径 | 说明 | 主要错误 |
|---|---|---|
| `GET /api/v1/metadata/{note}`（或 `?path=`） | frontmatter 解析（Vault 实时读；未知字段保留、tags 规范化）；解析失败为 HTTP 200 + 结构化 `parse_error` | 404/503 |
| `GET /api/v1/links/{note}`（或 `?path=`） | outgoing 链接（从 SQLite `links` 读；resolved/broken/ambiguous + broken_count） | 404/503 |
| `GET /api/v1/backlinks/{note}`（或 `?path=`） | 反链（从 SQLite `backlinks` 读；来源标题 + 上下文片段） | 404/503 |
| `GET /api/v1/search?q=` | 搜索：FTS5 MATCH 主路径 + 中文/Emoji/FTS 不可用时的子串降级（AND、snippet 纯文本、degraded 计数） | 400/503 |
| `POST /api/v1/index/rebuild` | 全量重建 SQLite 派生索引（事务内清空+重扫） | 503 |

`{note}` 使用 FastAPI `:path` 转换器；前端按路径段做 `encodeURIComponent`，
中文/Emoji/空格/嵌套路径均可。索引故障仅使上述端点与 Graph 503，不影响
health/Vault 读写/编辑器。

## Graph（M5，只读）

| 方法/路径 | 说明 | 主要错误 |
|---|---|---|
| `GET /api/v1/graph?limit=&offset=&tag=&include_broken=` | 全局 Note/Tag 图（可选 tag 过滤，casefold） | 400/503 |
| `GET /api/v1/graph/local/{note}?depth=&direction=&tag=&include_broken=` | root BFS 局部图（depth≤3；incoming 由 links 反推） | 400/404/503 |
| `GET /api/v1/graph/tag/{tag}`（或 `?tag=`） | tag 作用域图（与全局 tag 过滤同语义） | 400/404/503 |

响应：`{model:"note-tag-v1",scope,root,nodes,edges,page:{limit,offset,next_offset,
total_nodes,total_edges,truncated},generated_at}`。nodes 按 `(type,id)`、edges 按
`(type,source,target,id)` 确定性排序；截断页只含端点在本页的边；broken 边
`target=""`。图只读：不产生任何 SQLite 写入、不写正文。

## AI 状态与只读 workflow（Phase 0 + M6）

`GET /api/v1/ai/status` 三态均 HTTP 200：`not_configured` / `offline` /
`connected`（发现模型时返回 ID），并携带 `active_profile_id` /
`active_profile_name` / `active_profile_kind`。安全默认：端点回显剥离凭据与
query；错误不含堆栈/响应体。

| 方法/路径 | 用途 | 关键语义 |
|---|---|---|
| `POST /api/v1/ai/chat` | 受限上下文问答 | Vault 未配置时允许空上下文 |
| `POST /api/v1/ai/summarize` | 总结笔记 | 输出严格结构化并回显 prompt 元数据 |
| `POST /api/v1/ai/tags` | 建议标签 | 只返回建议，不写 frontmatter |
| `POST /api/v1/ai/related` | 推荐相关笔记 | 先由 RAG/FTS、links/graph 缩小 allow-list |
| `POST /api/v1/ai/extract_todos` | 提取待办 | 输出严格结构化建议 |
| `POST /api/v1/ai/classify` | 分类笔记 | 输出严格结构化建议 |

workflow 的模型未配置/离线返回 503，非法模型输出返回 502，能力缺失返回
`capability_unavailable`。详见 [`ai-architecture.md`](./ai-architecture.md)。

## 多供应商 AI 档案

| 方法/路径 | 用途 | 关键语义 |
|---|---|---|
| `GET /api/v1/settings/ai/profiles` | 读取档案库 | `{revision, active_profile_id, profiles[]}`；密钥只回显 `api_key_set` |
| `POST /api/v1/settings/ai/profiles` | 新增/编辑档案 | `{provider, expected_revision, activate=true}`；`api_key` 省略=保留已存密钥、`""`=清除 |
| `POST /api/v1/settings/ai/profiles/activate` | 一键切换 | 幂等（切换当前档案不自增 revision）；revision 过期 ⇒ 409 `settings_conflict` |
| `POST /api/v1/settings/ai/profiles/delete` | 删除档案 | 当前生效项 ⇒ 409 `ai_profile_active`；最后一个 ⇒ 409 `ai_profile_last` |
| `POST /api/v1/settings/ai/profiles/test` | 只探测不写盘 | 复用该档案（或当前生效档案）的已存密钥 |

- 档案字段：`id`（小写 slug）、`name`、`kind`（`omlx` / `openai` /
  `openai-compatible` / `custom`）、`base_url`、`api_key`、`chat_model`、`temperature`、
  `max_output_tokens`、`request_timeout_seconds`、`connect_timeout_seconds`。
- 扁平字段（`ai.base_url` / `chat_model` / `api_key` / 超时等）始终是「当前生效
  配置」，激活档案时从档案投影而来；`PATCH /api/v1/settings` 编辑的正是当前生效
  档案，两处视图不会分叉。
- 上限 30 个档案（超出 422 `ai_profiles_full`）；任何响应都不回显密钥，`/settings/*`
  仍限本机可信页面。

## 本地优先 RAG（M14）

| 方法/路径 | 说明 |
|---|---|
| `POST /api/v1/rag/search` | 仅检索，不调用 Chat（返回融合后的 chunk 命中） |
| `POST /api/v1/rag/query` | 完整链：检索 → EvidencePack → 生成 → 引用校验 |
| `POST /api/v1/rag/index/rebuild` | 重建 RAG 向量/FTS 索引（派生数据） |
| `GET /api/v1/rag/index/status` | 索引状态、`vector_kernel`、degraded 原因 |
| `PATCH /api/v1/settings/rag` | RAG 配置（`extra="forbid"`，未知键 422） |
| `POST /api/v1/settings/rag/reranker/test` | 重排端点探活（只探测不写盘） |

链路：Markdown 感知分块 → 独立 embedding provider → SQLite 向量索引 →
FTS5 + 向量混合检索（RRF，`k=60`）→ 可选 Link/Graph 第三路（**默认关闭**）→
可选重排 → EvidencePack → 已有 AI Provider 生成 → 引用校验（伪造 `[S99]` 被删除
并记入 `invalid_citations`）。RAG 故障返回 503 `rag_unavailable`，其余端点不受影响。
详见 [`rag-architecture.md`](./rag-architecture.md)。

## 受控 Agent / Job / History（M7）

| 方法/路径 | 说明 |
|---|---|
| `POST /api/v1/jobs` | 创建 Agent job（默认只生成 Level 1 preview，不落盘） |
| `GET /api/v1/jobs` / `GET /api/v1/jobs/{id}` | 列出/读取 job |
| `POST /api/v1/jobs/{id}/accept` / `reject` | 接受（唯一写入路径）/ 拒绝 preview |
| `GET /api/v1/history` / `GET /api/v1/history/{id}` | Journal 审计 |
| `POST /api/v1/history/{id}/undo` | 逆序回滚 |

## Scheduler（M8）

| 方法/路径 | 说明 |
|---|---|
| `GET /api/v1/scheduler/status` | 状态、时区、任务、`network_exposure_warning` |
| `POST /api/v1/scheduler/run/{task}` | 手动触发（仅三个稳定任务；`confirm=false` 永远只生成 preview） |
| `GET /api/v1/scheduler/runs` | run 审计（trigger/status/error/started/finished） |
| `POST /api/v1/scheduler/recovery/{run_id}` | `diagnose`（默认）/ `rollback_if_safe`（hash guard）/ `retry_preview` |

---

## 环境变量全表

| 变量 | 默认 | 说明 |
|---|---|---|
| `LOCALNOTE_HOST` / `LOCALNOTE_SERVER__HOST` | `127.0.0.1` | 后端监听地址（默认仅回环） |
| `LOCALNOTE_PORT` / `LOCALNOTE_SERVER__PORT` | `3780` | 后端端口 |
| `LOCALNOTE_SERVER__CORS_ORIGINS` | `["http://127.0.0.1:5173","http://localhost:5173"]` | JSON 数组显式白名单 |
| `LOCALNOTE_SERVER__SETTINGS_TRUSTED_HOSTS` | `[]` | 额外允许访问 `/api/v1/settings` 的 Host（JSON 数组，如 `["note.example.com"]`）。仅在经反向代理（保留公网 Host）访问时才需要；回环对端校验仍然生效 |
| `LOCALNOTE_VAULT__ROOT` / `LOCALNOTE_VAULT_ROOT` | 空 | Vault 根目录（必须已存在）。未配置 ⇒ `Not configured`，Vault API 503 |
| `LOCALNOTE_VAULT__WATCHER_ENABLED` / `..._VAULT_WATCHER_ENABLED` | `true` | 是否启动 watcher（`false` ⇒ `disabled`） |
| `LOCALNOTE_VAULT__WATCHER_DEBOUNCE_MS` | `200` | watcher 事件去抖窗口（ms） |
| `LOCALNOTE_VAULT__MAX_FILE_BYTES` | `52428800` | 单文件读写上限（超出 ⇒ 413 `file_too_large`） |
| `LOCALNOTE_VAULT__TRASH_RETENTION_DAYS` | `30` | 回收站保留天数（1–3650） |
| `LOCALNOTE_AI__BASE_URL` / `LOCALNOTE_OMLX_BASE_URL` | `http://127.0.0.1:8000/v1` | oMLX OpenAI 兼容地址；置空 ⇒ `not_configured` |
| `LOCALNOTE_AI__API_KEY` | 空 | 需要认证的 OpenAI 兼容服务使用；以 `Authorization: Bearer <key>` 发送。仅写入（接口只回显 `api_key_set`），保存在实例配置文件（0600） |
| `LOCALNOTE_AI__CONNECT_TIMEOUT_SECONDS` | `0.5` | AI 探测连接超时 |
| `LOCALNOTE_AI__REQUEST_TIMEOUT_SECONDS` | `60` | AI 生成超时。本地模型一次推理常需数秒到数十秒；早期默认 `2.0` 会让 `/ai/status` 显示 connected 而每次生成都返回 `ai_timeout`，故放宽到 60（上限 120） |
| `LOCALNOTE_AI__ACTIVE_PROFILE_ID` | `default` | 生效的 AI 供应商档案 id。运行期请在界面或用 `POST /settings/ai/profiles/activate` 切换；环境变量只在启动时读取一次 |
| `LOCALNOTE_AI__USE_ENV_PROXY` / `LOCALNOTE_RAG__USE_ENV_PROXY` | `0` | 出站客户端是否解析 `HTTP_PROXY`/`NO_PROXY`（默认关闭，避免 IPv6 字面量导致的 `InvalidURL`） |
| `LOCALNOTE_SCHEDULER__ENABLED` | `true` | Scheduler 总开关（关闭后 status 可见、run 返回 409 `scheduler_disabled`） |
| `LOCALNOTE_SCHEDULER__TIMEZONE` | `UTC` | 任务时区（IANA 名称，如 `Asia/Shanghai`；非法即配置错误） |
| `LOCALNOTE_SCHEDULER__DAILY_CRON` / `WEEKLY_CRON` | `0 23 * * *` / `0 20 * * 0` | Daily/Weekly cron（严格 5 字段 int/`*`/`*/step`） |
| `LOCALNOTE_SCHEDULER__INDEX_CHECK_ENABLED` | `false` | 可选只读 index consistency 周期任务（interval） |
| `LOCALNOTE_SCHEDULER__INDEX_CHECK_INTERVAL_HOURS` | `24` | index 校验间隔（小时） |
| `LOCALNOTE_SCHEDULER__LEVEL2_AUTO_ENABLED` / `LEVEL2_AUTO_ACTIONS` | `false` / `[]` | Level 2 tag-only 自动执行开关/白名单（只能 `add_tags`/`remove_tags`；开启必须有非空白名单） |
| `LOCALNOTE_SCHEDULER__JOB_TIMEOUT_SECONDS` | `300` | 单次 run 超时（超时标记 `timed_out`/`recovery_required`，不杀写线程） |
| `LOCALNOTE_SCHEDULER__STALE_RUN_AFTER_SECONDS` | `3600` | 孤儿 running run 阈值 |
| `LOCALNOTE_HISTORY__RETENTION_DAYS` | `30` | History/run retention（只删终态过期派生行） |
| `LOCALNOTE_HISTORY__CLEANUP_ENABLED` / `CLEANUP_INTERVAL_HOURS` | `true` / `24` | retention 清理开关/间隔 |
| `LOCALNOTE_HISTORY__MAX_SCHEDULER_RUNS` | `1000` | scheduler_runs 保留上限 |
| `LOCALNOTE_INDEX__AUTO_REBUILD` | `false` | index consistency 发现不一致时允许 rebuild（只写派生库） |
| `LOCALNOTE_INDEX__NOTE_TEXT_CAP` | `1000000` | 索引单篇正文保留上限（字符） |
| `LOCALNOTE_INDEX__DB_FILENAME` | `index.db` | `.localnote/` 内派生库文件名（M4） |
| `LOCALNOTE_INDEX__FTS_TOKENIZER` | `unicode61` | FTS5 tokenizer（M4；中文短查询走子串降级） |
| `LOCALNOTE_INDEX__JOURNAL_MODE` | `WAL` | SQLite journal_mode（M4） |
| `LOCALNOTE_INDEX__SYNCHRONOUS` | `NORMAL` | SQLite synchronous（M4） |
| `LOCALNOTE_INDEX__BUSY_TIMEOUT_MS` | `5000` | SQLite busy_timeout（M4） |
| `LOCALNOTE_GRAPH__DEFAULT_LIMIT` / `MAX_LIMIT` | `500` / `2000` | Graph 节点页默认/上限（M5） |
| `LOCALNOTE_GRAPH__DEFAULT_DEPTH` / `MAX_DEPTH` | `1` / `3` | local BFS 默认/最大深度（M5） |
| `LOCALNOTE_GRAPH__DEFAULT_INCLUDE_BROKEN` | `true` | 是否默认输出 dangling broken/ambiguous 边（M5） |
| `LOCALNOTE_GRAPH__MAX_EDGES` | `2000` | 单响应边上限（超出置 `truncated`，M5） |
| `VITE_PORT` / `VITE_API_PROXY_TARGET` | `5173` / `http://127.0.0.1:3780` | Vite 端口 / `/api` proxy 目标 |

规则：嵌套变量优先于扁平别名（如 `LOCALNOTE_VAULT__ROOT` 与
`LOCALNOTE_VAULT_ROOT` 同时设置时取嵌套值）。

## 稳定错误码

`vault_not_configured`、`vault_unavailable`、`path_traversal`、
`symlink_escape`、`file_conflict`、`expected_hash_required`、
`file_too_large`、`watcher_unavailable`、`index_unavailable`、
`settings_local_only`、`settings_conflict`、`ai_profile_active`、
`ai_profile_last`、`ai_profiles_full`、`capability_unavailable`、
`scheduler_disabled`、`recovery_required`、`rag_unavailable`、
`internal_error`。
