# server/search — M3 已实现：关键词子串搜索（非 FTS）

PLAN-M3 §5.5。`SearchService` 在内存派生索引上做大小写不敏感**子串**匹配：

- 多词 AND（拆空白）、文件名>标题>tags 加权、固定 score→path 排序；
- snippet 为命中词附近窗口的**纯文本**（React 以文本渲染并高亮，禁止当作
  HTML 注入）；单篇读取失败跳过并计入 `skipped_notes`（降级）；
- 空白/超长/含控制字符查询 → 400 `invalid_request`；索引不可用 → 503
  `index_unavailable`；搜索只读、无副作用、不产生 query 正文日志。

**边界**：非 FTS、无分词/相关性重排/语义；中文分词与 10k 笔记基准属于
M4（SQLite FTS）。
