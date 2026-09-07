# server/index — M4 已实现：SQLite 派生库 + FTS5 全文搜索

PLAN-M4 §5。`DerivedIndexService` 以 `.localnote/index.db`（SQLite，默认
`index.db`）为存储：notes/tags/properties/links/backlinks/notes_fts 全为
**派生数据**（删 `.localnote` 后可经 `VaultService.list_tree/read_bytes`
完整重建，正文零改动）：

- `errors.py` — `IndexUnavailable`（继承 `VaultError`，503 `index_unavailable`）。
- `schemas.py` — `NoteIndexEntry`（单篇行）/ `IndexRebuildResponse`（REST
  契约，不变）/ `SearchCandidate`（索引 → 搜索服务的内部行契约）。
- `schema.py` — `SCHEMA_VERSION=1`、单语句 DDL 表、`MIGRATIONS`
  （0→1 引导；v1→v2 起逐版本升级）；`notes_fts` FTS5 DDL 由 db.py 探测后创建。
- `db.py` — `IndexDatabase`：单连接 + RLock（service 持有）、PRAGMA
  （WAL / synchronous=NORMAL / foreign_keys=ON / busy_timeout=5000）、
  `transaction()`（BEGIN IMMEDIATE / COMMIT / ROLLBACK）、版本化 migration
  runner（不可迁移/未来版本/损坏 → 删库重建）、FTS5 可用性探测与
  tokenizer 回退。
- `service.py` — SQLite-backed `rebuild()`（单事务清空 + 重扫，失败回滚）、
  `handle_event()` 增量 upsert/delete/move（与 rebuild 同一 RLock 串行，
  PLAN-M4 §5.6）、`entry/entries/outgoing_for/backlink_sources/tags_for`
  全部改从 SQLite 读（Links/Metadata/Search 调用面不变）；
  `fts_search`/`substring_search` 供 SearchService 使用。

**FTS 与中文选型（PLAN-M4 §5.4 实测结论）**：`unicode61` 不拆分中文、
`trigram` 最小 token 3 字 → 采用双路径：英文/数字/可 tokenize 词走 FTS5
MATCH（bm25 列权重 title>basename>tags>body），中文（任意长度）/Emoji/
非 ASCII/符号查询与 FTS 不可命中/不可用时降级到 M3 关键词子串路径
（`search_folded` 列 = Python casefold 语料，SQL `INSTR` 扫描）。

**边界**：索引只写 `.localnote/` 派生库，永不写回正文；`VaultService`
仍是唯一 FS 门面；故障只影响 metadata/links/search/graph（503），不影响
health/AI/Vault 读写/编辑器。**M5（已实现，PLAN-M5）**：`service.py` 增补
只读 Graph 查询面 — `note_exists`/`note_title`/`note_tags`/
`graph_snapshot`/`graph_totals`，全部在同一 RLock 内读取 notes/tags/links，
零写入；`graph_snapshot` 三查询单次持锁返回冻结 dataclass 快照
（`server/index/schemas.py` 的 `GraphSnapshot`/`GraphTotals`），供
`server/graph/service.py` 查询时计算图 DTO；既有 M1–M4 方法与写路径零改动。
`server/markdown/bytes.py` 仍是 bytes 边界。
