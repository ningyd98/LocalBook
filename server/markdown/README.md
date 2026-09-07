# server/markdown — M1 bytes 边界（已实现最小形态）

M1 **没有 Markdown parser/AST**。本包只提供原始 bytes 与 hash 的工具：

- `bytes.py` — `sha256_bytes` / `byte_length` / `snapshot`；文件 I/O 仍在
  `VaultService`（`server/vault/service.py`）。
- 未知 Obsidian 语法、frontmatter、callout、嵌入、脚注、HTML、代码块、
  BOM、LF/CRLF、非 UTF-8 一律原样保留（由 Vault bytes 层保证）。

M3 入口：parser/serializer 选型（未知语法逐字节保留原则不变），
metadata/properties/wikilink 解析。禁止把本 bytes 层改成 parser/serializer
来“顺手”实现 M3（见 PLAN-M1.md §10.4）。
