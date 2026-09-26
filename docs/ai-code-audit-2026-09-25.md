# LocalBook / ReadFlow 代码审计与修复记录（2026-09-25）

本轮检查了后端的 AI、Reader、RAG、Vault、索引、恢复和转写调用链，以及网页、编辑器、ReadFlow、浏览器扩展与持续集成。以下逐项记录发现的问题和已落地的处理。仓库在开始时已有大量暂存与未暂存改动；本轮没有重置、提交、删除用户数据或改动正在使用的 Vault。

## 设备连接与 AI 回答

| # | 发现的问题 | 处理位置 |
| --- | --- | --- |
| 1 | 设置接口可被伪造的 `X-Forwarded-For` 影响本地判断 | `server/api/routes/settings.py` 只信任实际连接对端；保留受信任主机和来源检查。 |
| 2 | Reader 配对码可直接换取设备令牌，缺少主人批准 | `server/reader/database.py`、迁移 006、`server/api/routes/settings.py`、网页设置、ReadFlow 设置改为生成 → 本地批准 → 完成配对。 |
| 3 | 公开部署的 Basic 认证占用 `Authorization`，Reader 令牌无法同时发送 | `ReadFlow/ReadFlow/Network/LocalBookClient.swift` 使用独立 Reader 令牌头；`server/reader/router.py` 识别并校验令牌。 |
| 4 | 令牌写入钥匙串失败仍可能报告注册成功；换服务地址可能沿用旧令牌 | `LocalBookClient.swift` 先持久化再确认注册，服务地址变化时清除旧令牌。 |
| 5 | AI 设置或 Vault 切换后 Reader 继续持有旧 AI/检索服务 | `server/runtime.py` 在发布新运行时状态时使 Reader 服务缓存失效。 |
| 6 | Reader 将模型异常伪装成成功的“AI 暂不可用”回答，阻断回退 | `server/reader/service.py` 返回明确的 503；`LocalBookClient.swift` 读取错误正文，ReadFlow 协调器据此回退。空回答也视为失败。 |
| 7 | ReadFlow 的“我的知识库”选择没有传到服务端 | `LocalBookClient.swift`、`server/reader/schemas.py`、`server/reader/service.py` 传递并处理 `scope`。 |
| 8 | 知识库检索的异步调用没有等待结果；命中笔记的路径未传入 AI 工作流，引用会被过滤 | `server/reader/service.py` 等待跨 Reader/Vault 检索，将命中笔记交给工作流读取和校验，并限制引用片段长度。 |
| 9 | 服务端认为客户端预先生成的对话编号必须已存在，首问会失败 | `server/reader/service.py` 允许首次使用的编号，并在成功回答后建立对话。 |
| 10 | 对话没有设备归属；历史读取顺序也不稳定 | `server/reader/database.py`、迁移 007 记录设备归属，校验访问者并按写入顺序提供最近历史；可确定归属的旧会话会继承会话设备。 |
| 11 | 长上下文从头截断可能切掉用户问题 | `server/reader/service.py` 保留问题，优先裁剪历史与上下文。 |
| 12 | “联网”选项没有实际网页搜索提供方，却可能给出普通模型回答 | `server/reader/web_search.py` 增加可选 SearXNG JSON 搜索；Reader 问答只引用搜索结果中可核验的 `[W1]` 标记，ReadFlow 仅通过 LocalBook 执行联网请求。未配置或搜索失败会明确报错，不会把普通聊天标成联网。当前机器仍需提供并配置实际搜索实例。 |
| 13 | 模型引用被错误地统一标成当前阅读来源；非 UUID 引用还会共用列表标识 | `server/reader/service.py` 保留真实引用路径，`ReadFlow/ReadFlow/UI/AIPopover/ConversationView.swift` 按引用行标识展示。 |

## 同步、索引与数据安全

| # | 发现的问题 | 处理位置 |
| --- | --- | --- |
| 14 | ReadFlow 新建来源未同步进入 outbox | `ReadFlow/ReadFlow/Storage/ReaderDatabase.swift` 同一事务写来源与 outbox，并按创建顺序取出。 |
| 15 | 拉取单条应用失败仍推进游标 | `ReadFlow/ReadFlow/Sync/SyncEngine.swift` 在失败或未知操作时停止，保留游标以便重试。 |
| 16 | 清除连接后 Basic 凭据仍留在内存 | `LocalBookClient.swift` 清除内存配置；地址切换时也清理不再适用的认证。 |
| 17 | 向量检索超过 512 条候选时缓存映射丢失 | `server/rag/vector/sqlite.py` 使用每次调用的完整结果映射。 |
| 18 | 恢复流程将读取故障当作“文件不存在”，可能错误回滚 | `server/recovery/service.py` 只对确实不存在的路径使用该分支，其他读取错误终止预检。 |
| 19 | Reader 拉取 limit 可为负数或无上界 | `server/reader/router.py` 将范围限制在 1–100。 |
| 20 | 非 Markdown 附件事件没有刷新 basename 映射，链接解析可能过时 | `server/index/service.py` 更新附件 basename 映射。 |
| 21 | 启动时全量索引可能长期阻塞 HTTP 监听 | `server/vault/lifecycle.py`、`server/runtime.py` 先建立 watcher 与服务，再进行受同步保护的后台扫描。 |

## Vault、编辑器与转写

| # | 发现的问题 | 处理位置 |
| --- | --- | --- |
| 22 | 无效 Range 请求可能漏关文件句柄 | `server/api/routes/vault.py` 在错误路径关闭资源。 |
| 23 | 垃圾桶清理/恢复一次处理无界数量 | `server/vault/trash.py` 分批、有界处理。 |
| 24 | 垃圾桶符号链接可能让清理越过 Vault | `server/vault/trash.py` 验证根路径并安全处理符号链接。 |
| 25 | 本地转写超时仅结束父进程，子进程可能继续占资源 | `server/transcription/service.py` 结束转写进程组。 |
| 26 | 网页转写期间切换笔记，结果可能插入新笔记 | `apps/web/src/components/WorkspaceShell.tsx` 将请求结果绑定到发起时的路径。 |
| 27 | 任务列表识别器将较短的围栏结束符视为有效 | `packages/markdown/src/render.ts` 修正围栏匹配。 |
| 28 | 清除文本格式漏掉斜体 | `packages/editor/src/formatting.ts` 补齐清除规则。 |
| 29 | 浏览器扩展后台日志记录了捕获文本 | `ReadFlow/ReadFlowExtension/background.js` 移除该日志。 |
| 30 | 输入校验日志可能包含原始用户内容 | `server/api/main.py` 只记录安全的错误类型和数量。 |

## 工程检查

| # | 发现的问题 | 处理位置 |
| --- | --- | --- |
| 31 | CI 引用不存在的 Xcode 工程、Python 依赖审计命令缺失 | `.github/workflows/ci.yml` 改为 Swift Package 构建，并从锁文件导出依赖供 `pip-audit` 使用。 |

## 继续审计增补

| # | 发现的问题 | 处理位置 |
| --- | --- | --- |
| 32 | 当前 DeepSeek Chat Completions 不接受结构化请求的 `json_schema` 格式；默认高强度思考还可能占满短输出预算 | `server/ai/adapters/openai_compatible.py` 对官方 DeepSeek 端点直接使用 `json_object`、在提示中给出结构，并关闭各条短输出调用链的思考模式；`server/api/dependencies.py` 给知识库回答传入输出结构；`ReadFlow/ReadFlow/AI/CloudAIProvider.swift` 也让官方端点的短时交互直接生成最终回答。截断、过滤或空回答明确报错。 |
| 33 | 当前笔记问答只显示引用数量，无法查看来源；切换笔记后仍可看到上一笔记的回答 | `apps/web/src/components/AIPanel.tsx` 展示可打开的引用路径、标题与片段，按发起时的笔记路径隔离回答和错误。 |
| 34 | 联网引用在 ReadFlow 的两个展示位置只有文字，无法打开网页 | `ReadFlow/ReadFlow/UI/AIPopover/AIPopoverView.swift`、`ConversationView.swift` 将有效的 HTTP(S) 地址展示为可点击链接。 |
| 35 | Reader 能力声明没有反映联网搜索是否配置 | `server/reader/service.py` 在能力响应中按实际搜索服务状态设置 `web_search`。 |
| 36 | 笔记问答要求模型返回路径却没有提供路径；引用只校验路径而不核对引文 | `server/ai/workflows.py` 在上下文中提供准确路径，并只保留引文确实出现在对应笔记上下文中的引用；`server/ai/prompts/chat.md` 明确要求逐字引用并更新提示版本。 |
| 37 | 结构化问答/摘要允许模型返回全空白正文并被视为成功 | `server/ai/schemas.py` 将全空白答案和摘要判为无效输出。 |
| 38 | Reader“我的知识”把整句自然语言问题交给全词匹配搜索；明明有笔记却得到零命中 | `server/reader/service.py` 保留精确匹配优先，在零命中时用有界的英文词和中文片段扩展检索并合并排序；`tests/backend/test_reader_knowledge_retrieval.py` 先复现引用丢失，再验证修复。 |
| 39 | “我的知识”零命中仍调用模型，可能返回无依据的事实答案 | `server/reader/service.py` 在没有检索证据时明确告知无可引用笔记，不调用模型；同一回归文件先验证旧行为失败，再验证新行为。 |
| 40 | ReadFlow 命令行 AI 自检启动时打开日常数据库，可能触发迁移 | `ReadFlow/ReadFlow/Storage/ReaderDatabase.swift` 在未指定数据库路径的 `--self-test` 中默认使用内存库；`ReadFlow/ReadFlowTests/ReadFlowTests.swift` 先验证旧代码不具备该入口，再验证隔离选择。 |
| 41 | RAG 状态请求与后台索引、历史记录共享 SQLite 连接，但各服务锁互不相通；并发时状态请求出现 `cannot start a transaction within a transaction`，RAG 初始化还可能在索引事务中执行建表语句 | `server/index/db.py` 在连接层统一串行化事务、读写和显式建表访问；`server/rag/vector/sqlite.py` 使用共享连接锁初始化 RAG 表。`tests/backend/test_index_db.py` 的事务冲突、未提交读取和建表交叉用例均先在旧代码下失败，再在修复后通过。 |
| 42 | 默认检查只收集 `tests/backend`，遗漏 RAG、Reader 与集成目录，导致启动竞争问题没有被主检查拦住 | `pyproject.toml` 扩大默认测试目录；CI 现有的 `scripts/check.sh` 因而覆盖这些目录。 |
| 43 | 每次知识库问答和 AI 工作流生成前都重复请求模型目录；模型已明确选择时这次串行网络请求没有必要，自动选择也会反复发现 | `server/ai/capabilities.py` 对带稳定地址的 HTTP 服务明确选择直接使用已保存的模型 ID，对其他适配器保留模型发现与校验，对 `auto` 的目录结果按服务地址和凭据指纹缓存 30 秒；`server/api/dependencies.py` 与 `server/ai/workflows.py` 共用该路径，覆盖知识库回答和笔记摘要等动作。`apps/web/src/components/RagPanel.tsx` 展示检索、向量检索、重排和模型生成耗时。 |
| 44 | 异步知识库问答在模型生成前直接运行同步检索，嵌入与重排等待会堵住同一服务进程的其他异步请求；笔记摘要等工作流也会在异步请求中同步读取整篇笔记 | `server/rag/service.py` 将检索放入工作线程；`server/ai/workflows.py` 将笔记读取、上下文裁剪和关联建议的检索放入工作线程。两项事件循环并发回归测试先在旧代码下失败，修复后通过。 |

## 第一阶段验收

使用独立的合成笔记 Vault 和当前保存的 AI/RAG 配置进行验收，未连接真实用户笔记库，也未重启现有服务。结果不含 API 密钥或原始私人笔记内容。

| 范围 | 结果 |
| --- | --- |
| 项目主检查 `scripts/check.sh` | Python 全目录 1366 通过、5 跳过（含 RAG、Reader、集成目录）；前端 454 通过、3 跳过；严格改动行 Ruff、全工作区类型检查与网页生产构建通过。`git diff --check` 通过。 |
| RAG 启动并发复查 | 修复前两项状态接口测试反复出现 SQLite 嵌套事务错误；修复后 RAG 独立目录 279 通过、2 跳过，RAG/Reader/集成合跑 363 通过、2 跳过，再由扩大后的项目主检查确认通过。 |
| ReadFlow | 依照仓库记录给 Swift CLI 补充 Testing 框架参数后，5 个测试通过；本地 stub 下云端成功、401、坏响应、429 和弹窗真实发送自检通过，后者 7 个断言通过并核对了落库归属。 |
| 真实模型及检索 | 保存的 DeepSeek `deepseek-flash` 连接成功；合成笔记问答、摘要、标签均为 HTTP 200；问答原文引用通过路径和引文校验。保存的本地 `Qwen3-Embedding-0.6B` 与重排配置对 2 篇合成笔记建索引，嵌入 2 个片段、无降级；RAG 回答包含 SQLite 结论、2 个有效来源、0 个无效引用。 |
| Reader | 临时设备未经主人批准注册为 403，批准后为 200；“当前内容”与“我的知识”均用真实模型返回有效答案，后者产生 2 个有效笔记引用；未配置的联网搜索明确返回 501。 |
| 网页 | 临时后端与网页实例中打开合成笔记，AI 助手显示正确问答和可打开的原文引用；摘要、标签可见；网页端重建知识索引后得到带 `[S1]` 和可打开来源的 RAG 答案。验收实例已关闭。 |

边界：上述真实调用都只使用合成笔记。现有 3780/5173 服务保持运行，没有部署或重启，因此尚未声称当前日常实例已应用本轮代码。联网搜索接入代码具备，但这台机器未配置 SearXNG 实例，无法验收真实网页搜索。ReadFlow 的原生图形界面没有在真实设备配对环境中手工走查，已完成 Swift 测试与命令行活路径自检。

慢响应优化补充（2026-09-26 复测）：项目主检查在追加两项模型目录回归用例前为 1368 通过、5 跳过，网页 454 通过、3 跳过；新增用例及并发回归定向检查 10 通过，RAG 面板定向检查 8 通过；类型检查、改动行 Ruff、生产构建与 `git diff --check` 均通过。用 2 篇临时合成笔记和当前保存的真实模型、嵌入及重排配置复测：索引重建 0.191 秒；首次知识库问答 3.125 秒（检索 1.991 秒，其中重排 1.968 秒；模型生成 1.131 秒），第二次问答 1.357 秒（检索 0.194 秒、模型生成 1.160 秒）；摘要 0.938 秒，均为 HTTP 200 且无降级。这些数值只代表两篇合成笔记和当时的服务状态，不能代替实际大知识库的耗时。现有 3780 服务的只读健康检查为 HTTP 200、0.094 秒；3780/5173 实例没有重启。网页把包含编码与向量库扫描的 `embedding_ms` 正确标为“向量检索”，并说明检索时间是总耗时。

同日 ReadFlow Swift 包再次编译并通过 5 项测试。当前 Command Line Tools 的默认 SDK 与编译器版本不一致，普通 `swift test` 还会碰到隔离编译缓存及 Testing 框架查找问题；复测改用已安装的 macOS 15.4 SDK、临时模块缓存，并显式加入 Testing 框架的编译和运行时路径。未更改 Swift 源码或日常数据库。

进一步定位：25 篇合成笔记的只检索请求中，开启重排为 0.665 秒（重排 0.636 秒），关闭重排为 0.030 秒；此对比没有云端生成调用。对现有知识库仅只读检查衍生索引统计：376 篇文档、4964 个片段、1024 维向量；当前安装没有 NumPy，加速内核为 Python。在只读加载其向量 BLOB 后，用合成查询向量跑纯 Python 点积约 0.230 秒，加载 BLOB 约 0.114 秒。这说明当前机器上向量扫描有可测量开销，但该探针没有发送私人笔记内容，也不能推断日常问答的总耗时。

用 20 篇较长合成笔记进一步测量时，开启重排的只检索请求为 3.869 秒，其中重排 3.816 秒；关闭重排为 0.076 秒。长片段显著放大本地重排开销。当前修复保证这段等待不阻塞其他异步请求；若要继续缩短单次问答，还需在有标注的问题集上验证缩短重排输入或减少重排候选的召回与引用质量，再调整默认值。

自检隔离补充：第一次运行 ReadFlow 命令行自检时，日志显示全局初始化打开了现有 ReadFlow 数据库并执行迁移入口；自检场景本身使用临时数据库。由于此前没有数据库快照，无法证明初始化是否写过 schema。没有执行重置、删除或用户内容写入；随后已修复默认隔离，并在后续自检中显式使用内存数据库。

## 下一步优化顺序

1. 在现有 3780/5173 实例上做受控上线：先确认其进程、真实 Vault 路径和当前健康状态，保留可回滚代码与配置快照；上线后用只读笔记验证问答、摘要、RAG、Reader 配对及引用路径。当前实例没有重启或部署，本轮验收仍以临时 Vault 为准。
2. 为 Reader“我的知识”建立有标注的中英文问题集，记录命中率、正确引用率、无依据拒答率和延迟；再决定是否把目前的有界词项扩展替换为共享的混合检索能力。
3. 配置并验证实际 SearXNG 搜索实例后，再开放 ReadFlow 联网问答的可用声明；目前未配置时明确返回 501。
4. 在真实 ReadFlow 图形界面完成设备批准、首次提问、历史切换与失败回退走查；将可稳定自动化的路径收入 macOS CI。
5. 消除网页构建中约 1.13 MB 的主 JavaScript 包告警，按实际首屏加载路径拆分模块；将当前跳过的浏览器级测试纳入可重复的验收环境。
