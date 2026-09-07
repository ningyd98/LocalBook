# server/metadata — M3 已实现：frontmatter/Properties 只读解析

PLAN-M3 §5.1。`MetadataService` 经 `VaultService.read_bytes` 读当前 bytes →
`server/markdown/frontmatter.py` 解析（BOM/CRLF 读取归一、`---` 首行分隔、
`yaml.safe_load`、未知字段逐字保留、tags 规范化展示 + casefold 索引键、
失败诊断）。文件不存在 → 404；YAML 非法/未闭合/非 UTF-8 → HTTP 200 +
结构化 `frontmatter_status`/`parse_error`，单篇隔离。

**边界**：M3 只读解析，**绝不写回**文件；属性编辑 UI 与写回/一致性处理在
未来里程碑（Diff/Undo/Policy 边界）。
