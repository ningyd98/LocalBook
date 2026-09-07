# server/graph — M5 已实现：只读、查询时计算的 Note/Tag 图投影

PLAN-M5 §3/§5。`GraphService` 在**请求时**从 M4 派生索引
（`.localnote/index.db` 的 notes/tags/links 表，经
`DerivedIndexService.graph_snapshot` 单次加锁一致读取）构造图 DTO：

- `schemas.py` — wire 契约 `GraphNode`/`GraphEdge`/`GraphPage`/
  `GraphResponse`（`extra="forbid"`，与 `packages/protocol` 镜像）。
- `service.py` — `GraphService.global_graph/local_graph/tag_graph`：
  纯 Python 组装作用域节点/边 → 确定性排序 → offset 分页/截断；参数
  语义校验（limit≤2000、offset≥0、depth≤3、direction 枚举、空/控制字符/
  超长输入 → 400 `invalid_request`）；root/tag 不存在 → 404 `not_found`；
  index 未 ready → 503 `index_unavailable`。
- 无任何 `INSERT/UPDATE/DELETE`；不写 Markdown、frontmatter、附件或
  `.localnote` 之外的任何文件。图随时可丢弃重算。

**边界与语义（固定契约）**：

- 只有两类节点：`note`（notes 行）与 `tag`（tags casefold 键）；Heading/
  Block 仅作 link 元数据；附件与 web 链接不成为节点。
- 稳定 ID：`note:<pct(path)>`、`tag:<pct(folded)>`、
  `link:<pct(source)>#<seq>`、`tag:<pct(note)>#<pct(folded)>`；pct 对每个
  UTF-8 字节百分号编码。重复 link（同一 source 的多次出现）靠 `seq` 保持
  唯一边。
- broken：保留 source 边、`broken=true`、`target=""`，不造虚拟目标；
  ambiguous：保留 M4 `candidates`（确定性顺序），有确定 resolved **note**
  目标时仍输出边且 `ambiguous=true`，否则为 dangling（空 target）。
- backlink **不作为独立 wire 边**输出（`GraphEdgeType` 保留该扩展值），仅
  作为 local scope BFS 的 incoming 遍历（由同一 links 快照反推）。
- local BFS：depth 0 = root+其 tags；direction both/outgoing/incoming；
  root 与 `tag=` 过滤不匹配时保留 root（PLAN-M5 §10.2 取舍）。
- 分页：nodes 按 `(type,id)` 排序后切片；truncated 页只携带端点都在本页
  的边；单响应边数硬上限 `max_edges`（GraphSettings，默认 2000）。
- 读一致性：快照三条 SELECT 在同一 RLock 持有内完成，rebuild/事件更新期间
  查询等待或读到全前/全后状态，不读半状态（PLAN-M5 §5.4）。

示例：

```bash
curl -fsS 'http://127.0.0.1:3780/api/v1/graph?limit=500'
curl -fsS 'http://127.0.0.1:3780/api/v1/graph/local/中文%20note.md?depth=1'
curl -fsS 'http://127.0.0.1:3780/api/v1/graph/tag/工作?limit=100'
```
