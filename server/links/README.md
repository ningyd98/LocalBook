# server/links — M3 已实现：wikilink / 双链反链（读取型）

PLAN-M3 §5.2/§5.6。解析与分辨只读派生：

- 语法解析在 `server/markdown/wikilinks.py`（`[[Note]]`/`![[Embed]]`/
  `[[Note#Heading]]`/`[[Note^Block]]`/`[[Note|Alias]]`/组合/`://` 外链；
  跳过围栏代码块与行内代码），不触碰 Vault。
- `schemas.py` — `LinkRef` / `NoteLinksResponse` / `BacklinkRef` /
  `BacklinksResponse`（与 `packages/protocol` 镜像）。
- `service.py` — `LinksService` 读索引行：outgoing（resolved/broken/ambiguous +
  确定性目标）与 backlinks（反向推导 + 来源上下文片段）。单篇失败隔离为诊断；
  索引不可用 → 503。

**边界**：当前仍只读、只标记不改写，无断链自动修复或原文编辑触发链接；Graph 查询由 M5 `server/graph` 消费本模块的派生数据，链接模块本身不提供 Graph 写路径。
