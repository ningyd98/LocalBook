# @localnote/graph

M5（PLAN-M5 §6.2）图可视化包：把 `/api/v1/graph*` 的 GraphResponse 转换为
Graphology 实例并在浏览器用 Sigma.js 渲染，WebGL 不可用时降级为可操作的
列表/统计视图。

## 固定依赖（锁定版本）

- `graphology@0.26.0`
- `sigma@3.0.3`
- `graphology-layout@0.6.1`（>1200 节点的 circular 布局）
- `graphology-layout-forceatlas2@0.10.1`（≤1200 节点的 force-atlas 布局）

Sigma/布局**只在挂载 effect 内动态 import**：包模块本身无副作用，jsdom /
无 WebGL 环境不会因导入而崩溃；Web 构建时它们进入独立 chunk。

## 导出

- `buildGraphologyGraph(response)` — 纯函数：节点 key = 后端稳定 ID；边
  key = 后端边 ID（link ID 含 M4 `seq`，重复 link 不合并）；`multi:true`
  keeps every backend edge distinct。`target === ""` 的 dangling（broken/
  ambiguous 无目标）边无法入图，被跳过并计入 `graphStats`——**绝不伪造
  目标节点**。
- `graphStats` / `protocolNodes` — 统计与 id→协议节点索引。
- `SigmaGraph` — React 组件：创建/缩放/平移/拖拽、节点点击回调
  （`onNodeClick`）、theme、unmount 时 `kill()` + ResizeObserver 清理；
  构造失败或无 WebGL 显示 `GraphFallback` 与原因，不显示空白/假成功。
- `GraphFallback` — 统计（Notes/Tags/Edges/Broken/Ambiguous）+ 可滚动
  Note（打开）与 Tag（过滤）列表。
- `styles.ts` — light/dark 色板与 legend 条目；Note 圆形主色、Tag 方形辅色、
  link 实线、tag 虚线、ambiguous 橙点线、broken 红虚线；不只靠颜色。
- `types.ts` — Graphology attribute 类型。

**大图策略**：>800 节点提高 label 阈值、>1000 关闭常驻 hover 标签并开启
`hideEdgesOnMove`；前端不做自动无限加载（load-more 由调用方控制）。
