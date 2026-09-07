# LocalNote Server M5 实施计划：Graph 派生与可视化

> 阶段：M5；依赖 M1 Vault、M2 Workspace、M3 Metadata/Links/Search、M4 SQLite 派生索引。
> 本文件是规划交付物，不是实现代码。路由已确定并生效为 `mf/gpt-6-astra`，不重新询问 provider/model。
> 规划阶段不得修改 `PLAN.md`、`PLAN-M1.md`、`PLAN-M2.md`、`PLAN-M3.md` 或 `PLAN-M4.md`。

## 1. 目标与完成标准

### 1.1 目标

M5 在 M4 SQLite 派生索引之上提供只读知识图谱和可视化：

1. 从 M4 的 `notes`、`tags`、`links`、`backlinks` 查询时计算 Note/Tag 节点和 link/tag 边。
2. 提供全局图、局部图和 tag 过滤 REST API，拥有稳定 DTO、稳定 ID、确定性排序、分页/截断和错误契约。
3. 将 `packages/graph` 从 placeholder 实现为 Graphology + Sigma.js 组件，启用 Ribbon Graph，支持 Global/Local、主题、缩放、拖拽、点击联动和 WebGL 降级。
4. 图始终是可删除、可重建的派生数据；不得写 Markdown 正文、frontmatter、附件或用户 Vault。
5. 图/index 故障只影响图及既有 metadata/links/search 的 index 读取，不得阻断 health、AI、Vault 或 editor。

### 1.2 可验收成功标准

- `python -m pytest -q`、前端 Vitest、typecheck、build、`compileall`、`./scripts/check.sh` 全部退出码 0；既有 M1–M4 测试保持通过，实际数量以运行结果为准（当前记录约后端 301、前端 45）。
- 仅将既有 Ribbon Graph disabled 断言改为 enabled；不得通过删除或放宽测试来“修复”回归。
- `/api/v1/graph` 返回完整 Note/Tag 节点和 link/tag 边；`/graph/local/{note}` 支持深度与方向；tag 过滤、分页、最大节点/边上限、截断元数据均可验证。
- resolved、broken、ambiguous、重复 basename、中文、Emoji、空格和嵌套路径均有 fixture 和断言；broken 不伪造目标节点，ambiguous 保留 candidates。
- Index 未 ready/数据库不可用时 Graph 返回 HTTP 503 `index_unavailable`；health、AI、Vault file read/write 和 editor 仍正常。
- 图 API 不产生任何 SQLite 写入；删除 `.localnote` 并从 Vault rebuild 后，图结果可复现，所有 Markdown/附件 hash 不变。
- 前端真实导入 Graphology/Sigma 并能构建；WebGL 初始化失败显示可操作的列表/统计降级，不显示空白或假成功。

## 2. 基线、约束与实施前检查

开发 Agent 必须先读取并以实际代码为准：

- `PLAN-M1.md`–`PLAN-M4.md`、`docs/architecture.md`、`docs/development-roadmap.md`、`README.md`；
- `server/index/{schema,db,service,errors,schemas}.py`；
- `server/links/service.py`、`server/search/service.py`、`server/vault/{service,lifecycle}.py`；
- `server/api/{main,dependencies}.py`、`server/api/routes/{links,search,index}.py`、`server/config.py`；
- `packages/graph`、`packages/protocol/src`、`packages/workspace/src`、`apps/web/src`、`tests/backend`、`tests/frontend`。

实施前记录：

```bash
pwd
python3 --version
python -m pytest -q
pnpm install --frozen-lockfile
pnpm --filter @localnote/web test -- --run
pnpm --filter @localnote/protocol typecheck
pnpm --filter @localnote/workspace typecheck
pnpm --filter @localnote/web typecheck
pnpm --filter @localnote/web build
python -m compileall server
./scripts/check.sh
```

若基线失败，先记录失败而不把它归因于 M5。核对实际 M4 schema、RLock、`IndexDatabase` 公开查询能力、`DerivedIndexService` 生命周期、FastAPI DI/lifespan 和前端 package scripts。后端不得新增第三方依赖；前端只允许 Graphology/Sigma.js 及必要的类型依赖。

## 3. 范围与明确决策

### 3.1 M5 实现范围

- 后端 `server/graph` schema/service/REST/DI/测试；
- `packages/protocol` Graph 类型；
- `packages/graph` Graphology model、Sigma renderer、样式、WebGL fallback、exports；
- `apps/web` API client、Graph panel/controls、Ribbon、theme、active note/tag 联动；
- workspace graph 状态及竞态保护；
- fixture、后端/前端 mock 测试、必要的 README/架构/roadmap M5 状态更新。

### 3.2 不做

AI、embedding、rerank、语义/vector search、Properties 编辑或 writeback、Markdown 正文写回、diff/undo/policy/history/recovery、Agent/Scheduler/WebSocket、多用户/云/远程数据库、真实用户 Vault 操作、Heading/Block/Property 节点、布局持久化、自动修复 broken/ambiguous、无上限全图、第三方布局服务。

### 3.3 节点和边的决定

只实现两类节点：

- `note`：M4 `notes` 中的 Markdown，含 path/title/basename；
- `tag`：M4 `tags` 中的折叠 tag，大小写归并，保留确定性 display label。

边：

- `link`：source Note → resolved target Note；保留 `raw/kind/display/section/block/context`；
- `tag`：Note → Tag；
- backlink 作为反向查询和 local BFS 语义，默认不重复输出一条 wire edge；若实现独立 `backlink` edge，必须保持唯一 ID、DTO 和测试一致。`GraphEdgeType` 保留该扩展值。

Heading/Block 只作为 link 元数据，不升级为节点。web link 默认不进入本地图、不暴露完整远程 URL；embed 依 M4 的 resolved 结果处理，但不把附件升级为 Note 节点。

### 3.4 查询时计算决策

M5 默认不新增 graph 表、不新增 migration。M4 表已经表达首期图所需信息；GraphService 通过受控只读查询在请求时构造 DTO，图 DTO/Graphology 实例可随时丢弃。不得直接暴露连接给 route，不得在 `server/graph` 中 `INSERT/UPDATE/DELETE`。

只有性能证据证明 JOIN/聚合无法达标时，才允许独立 additive graph view；必须有 migration/version、全量重建、删除后恢复、回滚/兼容测试，并在开发报告标为计划偏差。M4 既有表和语义不得改写。

## 4. 有序任务、依赖、文件与完成定义

|编号|任务|依赖|关键文件|完成定义|
|---|---|---|---|---|
|M5-01|基线与 schema 勘验|M4|`server/index/*`、测试|记录真实测试数量、M4 列/锁/DI；保护文件无 diff|
|M5-02|模型与 wire 契约定稿|01|`server/graph/schemas.py`、`packages/protocol/src/index.ts`|固定节点/边/ID/排序/分页/深度/错误并写测试断言|
|M5-03|Graph 查询投影|02|`server/graph/{__init__,service}.py`、可选 index helper|global/local/tag 只读投影；ready gate、参数绑定、确定性输出、上限通过|
|M5-04|路由和 DI|03|`server/api/routes/graph.py`、`dependencies.py`、`main.py`|三个端点、Pydantic query 校验、404/400/503 契约、与其他路由隔离|
|M5-05|后端 fixture/API 矩阵|03/04|`tests/fixtures/vault`、`tests/backend/test_graph*.py`|覆盖中文/Emoji/重名/broken/ambiguous/分页/重建/hash/失败隔离|
|M5-06|Protocol/client|04|`packages/protocol/src/index.ts`、`apps/web/src/api/{client,types}.ts`|逐段 path 编码、URLSearchParams、ApiError status/code/path 保留；fetch mock|
|M5-07|Graphology/Sigma 基础|06|`packages/graph/{package.json,README.md,src/*}`|DTO 转图实例、稳定 ID/样式、mount/resize/destroy、WebGL fallback 可构建|
|M5-08|Workspace 状态|07|`packages/workspace/src/{types,store,index}.ts`|global/local/tag loading/error/empty/unavailable、requestVersion 丢弃旧响应|
|M5-09|Web UI 接线|08|`apps/web/src/{App.tsx,styles.css}`、`components/{Ribbon,GraphPanel,GraphControls}.tsx`|Graph enabled、视图切换、深度/limit/filter、主题、点击联动、无障碍|
|M5-10|前端测试|07–09|`tests/frontend/Graph*.test.*`、`M5Matrix.test.tsx`|全 mock fetch；交互、竞态、fallback、cleanup、回归通过|
|M5-11|性能/安全/重建|03–10|新增 limits/rebuild 测试|最大限制、参数绑定、无写入、hash 一致、故障隔离、大图降级有证据|
|M5-12|文档与最终门禁|05/10/11|README/docs、脚本按需|所有验收命令 0；列出偏差、依赖、未决项、保护文件未改|

开发 Agent 必须按顺序执行；如果实际代码需要偏差，记录原因、影响、替代方案，不静默改设计。

## 5. 后端设计

### 5.1 Pydantic DTO

建议 `extra="forbid"`，字段如下；实际实现可用同语义的命名，但 Python/TS 必须一致：

```python
class GraphNode(BaseModel):
    id: str                         # opaque stable ID
    type: Literal["note", "tag"]
    label: str
    path: str | None = None         # note only
    title: str | None = None        # note only
    tag: str | None = None          # tag only
    tag_folded: str | None = None   # tag only

class GraphEdge(BaseModel):
    id: str
    source: str
    target: str                     # empty only for broken edge with no target
    type: Literal["link", "backlink", "tag"]
    directed: bool = True
    raw: str | None = None
    resolved_path: str | None = None
    section: str | None = None
    block: str | None = None
    broken: bool = False
    ambiguous: bool = False
    candidates: list[str] = []
    context: str | None = None

class GraphPage(BaseModel):
    limit: int
    offset: int
    next_offset: int | None
    total_nodes: int
    total_edges: int
    truncated: bool

class GraphResponse(BaseModel):
    model: Literal["note-tag-v1"]
    scope: Literal["global", "local", "tag"]
    root: str | None
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    page: GraphPage
    generated_at: datetime
```

### 5.2 稳定 ID、排序和异常

- Note：`note:` + UTF-8 百分号编码的 root-relative POSIX path；
- Tag：`tag:` + UTF-8 百分号编码的 `tag_folded`；display 为 `(casefold, display)` 的确定性最小值；
- link：`link:` + source path + M4 `seq`，同一 source 的重复 link 保留；
- tag edge：`tag:` + note ID + tag ID；
- nodes 按 `(type, id)`，edges 按 `(type, source, target, id)` 排序；先排序候选集再分页；截断时移除引用未返回节点的正常边。

resolved link 输出 source/target Note。broken 保留 source 和 `broken=true`，不造虚拟目标。ambiguous 保留 M4 `candidates`，若有确定 `resolved_path` 可显示目标但必须 `ambiguous=true`；否则按 dangling broken 视觉处理。所有 candidate 排序稳定。

### 5.3 REST

```http
GET /api/v1/graph?limit=500&offset=0&tag=工作&include_broken=true
GET /api/v1/graph/local/{note:path}?depth=1&direction=both&limit=500&tag=工作
GET /api/v1/graph/tag/{tag}?limit=500
```

`limit` 默认 500、最大 2000；`offset >= 0`；local `depth` 默认 1、最大 3；`direction=both|outgoing|incoming`；`include_broken` 默认 true；tag 以 casefold 比较。空/控制字符/超长值为 400 `invalid_request`。本计划使用稳定排序的 offset 分页，不使用不透明 cursor。

Global 包含孤立 Note、Tag、link/tag edge；tag route 等价于 tag 过滤的 tag scope。Local 从 root 做 BFS：depth 0 返回 root 与其 tags；depth 1 默认，incoming 来自 M4 backlinks，outgoing 来自 links，both 合并；邻居和其 tags 加入 scope。root 不存在 404 `not_found`。过滤后不得有正常边引用不存在节点。

GraphService 必须调用 `index.assert_ready()`，使用 M4 的受控 helper 或在同一 RLock 内的最小参数化 SELECT。不得通过 VaultService 读正文，不得改变 M4 readiness。M4 rebuild/事件更新期间复用锁，查询等待或安全返回，不读半状态；Graph 无 watcher callback。

错误继续复用 `_vault_error_response` 和 M1 形状：

- index 不可用：503 `{"error":{"code":"index_unavailable","message":"...","path":null}}`；
- local root/tag 不存在：404 `not_found`，path 为安全的相对输入；
- 参数非法：400 `invalid_request`；
- 未处理错误：500 `internal_error`，不泄漏 traceback/root/正文。

### 5.4 M4 整合和隔离

生命周期保持：Vault 初始化 → `.localnote`/index.db 打开迁移 → rebuild → 绑定 watcher。Graph DI 复用 `app.state.index_service`。Graph failure 不改变 `get_vault_service`、health、AI 和 editor。手动 `/api/v1/index/rebuild` 成功后下次 Graph 请求即可看到新投影。不得删除、改写 M4 表或修改既有 links/search/metadata DTO。

## 6. 前端设计

### 6.1 Protocol/client/store

`packages/protocol/src/index.ts` 增加严格镜像：

```ts
export type GraphNodeType = "note" | "tag";
export type GraphEdgeType = "link" | "backlink" | "tag";
export interface GraphNode { id:string; type:GraphNodeType; label:string; path:string|null; title:string|null; tag:string|null; tag_folded:string|null; }
export interface GraphEdge { id:string; source:string; target:string; type:GraphEdgeType; directed:boolean; raw:string|null; resolved_path:string|null; section:string|null; block:string|null; broken:boolean; ambiguous:boolean; candidates:string[]; context:string|null; }
export interface GraphPage { limit:number; offset:number; next_offset:number|null; total_nodes:number; total_edges:number; truncated:boolean; }
export interface GraphResponse { model:"note-tag-v1"; scope:"global"|"local"|"tag"; root:string|null; nodes:GraphNode[]; edges:GraphEdge[]; page:GraphPage; generated_at:string; }
```

增加 `fetchGraph(query)`、`fetchLocalGraph(note, query)`、`fetchTagGraph(tag, query)`。path 逐 segment `encodeURIComponent`，query 统一 `URLSearchParams`。`ApiError` 必须保留 HTTP status、code、path。Workspace graph slice 至少有 `idle/loading/ready/empty/error/unavailable`、scope、activeNote、depth、direction、limit、tag、response、requestVersion；新请求使旧响应失效。

### 6.2 Graphology/Sigma

`packages/graph` exports：

- DTO 与 Graphology attributes 的纯函数转换；
- `createGraphologyGraph(response)`，保证重复边 key 唯一且 attributes 携带 broken/ambiguous/candidates；
- Note/Tag/link/tag/broken/ambiguous style tokens；
- `SigmaGraph` React 组件，负责 renderer 创建、container resize、zoom/pan/drag、节点点击、theme 和 cleanup；
- legend/fallback 组件或等价 exports。

Node ID 必须与后端一致。Note 用圆形/主色，Tag 用方形/菱形/辅助色；link 实线、tag 虚线；broken 红色虚线；ambiguous 橙色 marker/线型。不得只靠颜色表达状态，应提供 legend、线型、文本/aria 标签。Sigma 必须在 effect 创建并在 unmount `kill/destroy`，清理 ResizeObserver/listener。

挂载前探测 WebGL，Sigma 构造异常或 context 不可用时渲染 fallback：节点/边统计、可滚动列表、Note 打开和 Tag 过滤仍可用，并显示原因。大于建议 1000 节点时关闭常驻 labels/昂贵 hover，保留交互和截断提示；前端不自动无限加载。

### 6.3 App UX

Ribbon Graph enabled；AI 继续 disabled；Files/Search 不回归。GraphPanel 提供 Global/Local/Tag、refresh、depth、direction、limit、tag filter、loading/empty/error/unavailable 和 truncated/load-more 状态。Note click 调用 workspace `openFile` 并切回 Files；Tag click 调整过滤；theme 读取 workspace light/dark。所有文本 React 渲染，不使用 `dangerouslySetInnerHTML`。

## 7. 目录树

```text
server/graph/
  __init__.py                 # [NEW] exports
  schemas.py                  # [NEW] Graph DTO
  service.py                  # [NEW] read-only M4 projection
  README.md                   # [NEW/EDIT] boundary and examples
server/api/routes/graph.py    # [NEW]
server/api/dependencies.py    # [EDIT] get_graph_service
server/api/main.py            # [EDIT] register router
server/index/service.py       # [EDIT only if minimal controlled query helper needed]

packages/protocol/src/index.ts                         # [EDIT] Graph types
packages/graph/package.json                            # [EDIT] Graphology/Sigma deps/scripts
packages/graph/src/{types,model,styles,SigmaGraph,index}.ts(x) # [NEW]
packages/graph/README.md                                # [EDIT] remove placeholder
packages/workspace/src/{types,store,index}.ts           # [EDIT] graph slice/API
apps/web/src/api/{client,types}.ts                      # [EDIT]
apps/web/src/components/{Ribbon,GraphPanel,GraphControls}.tsx # [EDIT/NEW]
apps/web/src/{App.tsx,styles.css}                       # [EDIT]

tests/backend/test_graph_service.py                    # [NEW]
tests/backend/test_graph_api.py                        # [NEW]
tests/backend/test_graph_limits.py                     # [NEW]
tests/backend/test_graph_rebuild.py                    # [NEW]
tests/frontend/{GraphClient,GraphModel,GraphPanel,M5Matrix}.test.* # [NEW]
tests/frontend/M3Matrix.test.tsx                        # [EDIT only Graph assertion]
```

不改：`PLAN.md`、`PLAN-M1.md`、`PLAN-M2.md`、`PLAN-M3.md`、`PLAN-M4.md`、`server/vault` 的 FS 安全边界、Markdown bytes、既有 M4 schema 语义。新增 fixture 和临时目录只能位于允许的 fixture/tmp 范围。

## 8. 风险与降级矩阵

|风险|处理|
|---|---|
|M4 schema/API 漂移|M5-01 实测；优先受控 helper；schema mismatch 只返回 index_unavailable，不自动迁移|
|重建/事件竞态|复用 M4 RLock 和 `assert_ready`；UI requestVersion 丢弃旧响应|
|broken/ambiguous|不伪造目标；flags/candidates 保留；红/橙视觉和列表说明|
|中文/Emoji/空格|UTF-8 segment encoding、casefold tag、稳定 ID；fixture 覆盖|
|重复 link/tag|edge ID 含 seq 或两端 ID；不依 Graphology key 合并逻辑边|
|大图|后端 limit/depth/max cap、offset、truncated；前端 label 简化和 load-more|
|WebGL 不可用|Sigma try/catch + fallback 列表/统计/打开/过滤|
|依赖冲突|只在 graph 包锁定 Graphology/Sigma；不新增 Python 依赖|
|XSS/隐私|React 文本渲染、参数绑定、web link 默认不入图、错误不泄漏 root/stack|
|后端图失败影响编辑|Graph 独立 DI/错误映射；回归测试 health/AI/Vault/editor|
|额外 graph 表复杂度|默认查询时计算；物化 view 必须有性能证据、版本、重建和审计|

## 9. 测试矩阵与验收

### 9.1 后端

1. schema projection：Note/Tag、孤立 Note、同 tag 大小写、display 确定；
2. links：resolved、self-anchor、section/block、重复 source seq；
3. broken：source edge、`broken=true`、无虚拟 Note；
4. ambiguous：flag、candidate 顺序、确定 resolved；
5. web/embed：行为依 M4 固定，web 不创建远程节点；
6. local BFS：depth 0/1/2、incoming/outgoing/both、tag edges、missing 404；
7. API：中文/Emoji/空格路径、tag query、limit/offset/depth/direction validation；
8. limits：最大值、truncated、无 dangling 正常 edge、稳定重复请求；
9. failure isolation：fake unavailable → Graph 503，health/AI/Vault 仍工作；
10. rebuild：删除 `.localnote`、重建、Graph snapshot 相同、文件 hash 不变；
11. security/concurrency：参数绑定、无绝对路径/stack 泄漏、rebuild/query 锁一致。

### 9.2 前端

1. client global/local/tag URL、逐段编码、query、ApiError；
2. Graphology 稳定 node/edge key、attributes、broken/ambiguous styles；
3. GraphPanel loading/empty/error/unavailable/truncated；
4. Global/Local/Tag 切换、depth/limit/direction/filter、旧响应竞态；
5. Note click/openFile、Tag click/filter、theme/legend/accessibility；
6. Sigma mock 创建/resize/destroy；WebGL 无 context/构造异常 fallback；
7. Ribbon Graph enabled、AI disabled、Files/Search 回归；
8. 全部 fetch mock，禁止真实后端、网络、FS。

### 9.3 命令

```bash
uv sync --dev
pnpm install --frozen-lockfile
python -m pytest -q
pnpm --filter @localnote/protocol typecheck
pnpm --filter @localnote/workspace typecheck
pnpm --filter @localnote/graph typecheck
pnpm --filter @localnote/web typecheck
pnpm --filter @localnote/web test -- --run
pnpm --filter @localnote/web build
python -m compileall server
./scripts/check.sh
```

脚本 `check.sh` 如增加 graph typecheck，必须保持既有顺序和失败即停语义。可选 smoke 只能使用 `tests/fixtures/vault` 的副本/tmp_path：

```bash
LOCALNOTE_VAULT__ROOT="$PWD/tests/fixtures/vault" python -m uvicorn server.api.main:app --host 127.0.0.1 --port 3780
curl -fsS http://127.0.0.1:3780/api/v1/health
curl -fsS 'http://127.0.0.1:3780/api/v1/graph?limit=100'
curl -fsS 'http://127.0.0.1:3780/api/v1/graph/local/%E4%B8%AD%E6%96%87%20note.md?depth=1'
```

最终报告必须包含实际退出码、实际测试数量、新增文件、依赖版本、Graph DTO/ID/排序、M4 锁/重建证据、性能限制、fallback、hash 证据和所有偏差。

## 10. 假设、未决事项与 M6 入口

### 10.1 假设

- M4 表和 `DerivedIndexService` 的只读语义保持兼容；links 行已有 target/raw/kind/resolved/broken/ambiguous/candidates/context。
- 单用户本地回环场景使用同步 REST，不需要权限、WebSocket 或多连接数据库。
- 默认 global limit 500/max 2000、local depth 1/max 3 足以作为 M5 门槛，可通过已有 `IndexSettings` 增加受约束配置但不得改变默认。
- 浏览器可能没有 WebGL，fallback 是正式成功路径。
- Graph 布局仅 UI 内存状态，不需要复现任何特定产品布局。

### 10.2 未决事项（只能在开发报告明确取舍）

- backlink 是否作为独立 wire edge（推荐：仅作反向 traversal，保留 union 扩展）；
- offset 是否改 opaque cursor（推荐 offset）；
- `include_broken` 默认是否 true（推荐 true）；
- web link 是否 external node（推荐不显示）；
- index helper 还是 GraphService 内最小 SELECT（不得泄露 connection）；
- root 与 tag filter 不匹配时保留 root 还是空图（推荐保留 root 并提示 filter）；
- 是否需要 graph materialized view（默认不做，必须以基准证据触发）。

### 10.3 M6 入口

M6 可在 M5 审计通过后讨论 AI/embedding/rerank/语义检索、Heading/Block/Property 节点或受控 graph context；每项须另有 schema、容量、ID、编辑/权限和故障边界方案。M5 的 GraphResponse 可作为只读上下文，但任何未来 AI 都不得绕过 policy/diff/undo 边界写图或写正文。

## 11. 开发与审计交付格式

开发 Agent 返回：

1. M5-01–M5-12 完成表、关键文件和偏差；
2. 实际 DTO、ID、排序、分页、filter/depth、示例响应；
3. 查询时计算或 materialized view 的最终选择及 migration/rebuild 证据；
4. broken/ambiguous/中文/Emoji/重名、锁、503 隔离、hash 不变证据；
5. Graphology/Sigma 版本、生命周期 cleanup、主题、样式、fallback 和大图策略；
6. 前后端实际测试数量、所有命令退出码；
7. 保护文件未改、未新增 Python 依赖、未实现所有非目标能力的确认。

审计 Agent 独立检查：需求符合性、计划执行完整性、DTO/API 一致性、M4 只读与故障隔离、ID/排序/边界、安全、测试覆盖、文档、依赖和保护文件 diff。阻断/重要问题不得静默忽略；审计不通过时按编排协议回开发修复，最多两轮。

**最终原则：图可以删除、重建、重新布局；它永远不是 Markdown 的替代品，也不能成为编辑器保存路径的前置依赖。**
