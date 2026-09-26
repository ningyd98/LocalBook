# ReadFlow - macOS 阅读采集客户端

ReadFlow 是 macOS 原生阅读采集客户端，提供划词捕获、本地存储与 AI 问答功能。
**它可完全独立运行**（本地 SQLite + 本地 FTS5 检索，不需要服务端、不需要网络）；
与 LocalBook 服务端的同步为**可选**能力。

## 功能特性

- 📖 **划词捕获**: 在任意应用中选中文本，快速保存到本地
- 💾 **本地优先**: SQLite 本地存储，离线可用
- 🔄 **智能同步**: 批量聚合同步，支持冲突检测（可选，需 LocalBook 服务端）
- 🔍 **全文搜索**: 本地 FTS5 全文索引（含中文 2 字子串召回），离线可用
- 🤖 **AI 问答**: 可配置云端大模型 endpoint；未配置或不可达时**降级**而不阻塞本地功能
- 🌐 **零配置发现**: Bonjour 自动发现 LocalBook 服务（可选，仅同步时需要）

## 项目结构

```
ReadFlow/
├── ReadFlow/
│   ├── App/                    # 应用入口
│   │   ├── ReadFlowApp.swift
│   │   └── AppState.swift
│   ├── Capture/                # 划词捕获
│   │   ├── SelectionService.swift
│   │   └── AccessibilityCapture.swift
│   ├── Storage/                # 本地存储
│   │   ├── ReaderDatabase.swift
│   │   └── Models/
│   ├── Sync/                   # 同步引擎
│   │   └── SyncEngine.swift
│   ├── Network/                # 网络层
│   │   ├── LocalBookClient.swift
│   │   ├── BonjourDiscovery.swift
│   │   └── KeychainService.swift
│   ├── UI/                     # 用户界面
│   │   ├── MenuBarView.swift
│   │   └── SettingsView.swift
│   └── Resources/              # 资源文件
├── scripts/                    # 构建、打包与自检脚本
│   ├── build_app.sh
│   └── package_dmg.sh
├── Package.swift               # SPM 依赖管理
└── README.md
```

## 技术栈

- **框架**: SwiftUI + AppKit
- **数据库**: GRDB.swift (SQLite)
- **网络**: Alamofire
- **发现**: Network.framework (Bonjour)
- **依赖管理**: Swift Package Manager

## V1.3 交付状态

- [x] SwiftUI + AppKit 原生应用与菜单栏入口
- [x] GRDB 本地 SQLite 数据库、迁移与 FTS5 全文检索（含中文子串召回）
- [x] Accessibility 划词捕获、来源/上下文记录与本地搜索
- [x] Safari 捕获扩展与 Native Messaging 接口
- [x] Outbox 增量同步、设备配对、冲突处理与 Bonjour 服务发现
- [x] 可选云端 AI 问答；未配置或不可达时降级为本地检索，不阻塞阅读

客户端仍可完全离线运行；LocalBook 服务端只在同步、跨库搜索与服务端 AI 场景下需要。

## 快速开始

> 本节命令均为**实测通过**（macOS 26 / Apple Swift 6.2.4 / **仅 Command Line Tools，无需 Xcode**）。
> ReadFlow 现在**独立运行**：不需要 LocalBook 服务端，也不需要网络。

### 前置要求

- macOS 13.0+
- Swift 6.x 工具链（`xcode-select --install` 装 Command Line Tools 即可，**不需要完整 Xcode**）
- `sqlite3`（系统自带，仅排查问题时用）

### 构建项目

```bash
cd ReadFlow
./scripts/build_app.sh          # 产出 dist/ReadFlow.app
```

脚本会编译并组装 `.app`、执行 ad-hoc 签名，最后跑一遍存储自检（当前输出 `self-check: PASS (66 cases)`；条数随自检用例增长）。

> 若在受限沙箱里直接 `swift build` 报 `sandbox_apply: Operation not permitted`，
> 用 `swift build --disable-sandbox`；`build_app.sh` 已自动包含这层兜底，无需手动处理。

### 运行应用

```bash
open dist/ReadFlow.app
```

首次启动即在本地建库（无需任何服务端）：

```
~/Library/Application Support/ReadFlow/readflow.sqlite     # 200704 bytes
```

构建选项与发布说明见根目录 [`README.md`](../README.md#readflow-阅读采集客户端)。

## 配置

### AI 供应商（可选）

默认**不连任何模型**，AI 功能显示「未配置」是正常状态——离线时本地检索、阅读、笔记全部可用。
要接入云端大模型，在应用内「设置 → AI 供应商」填入 endpoint / model / API Key。
网络不可达时请求会**降级**并标注 `isDegraded`，不会阻塞本地功能。

### Accessibility 权限

ReadFlow 需要 Accessibility 权限来捕获其他应用的选中文本:

1. 打开 **系统设置** > **隐私与安全性** > **辅助功能**
2. 点击 **+** 添加 ReadFlow
3. 启用 ReadFlow

## 开发指南

### 代码规范

- 遵循 Swift API Design Guidelines
- 使用 SwiftLint 进行代码检查
- 所有公共 API 必须包含文档注释

### 提交规范

- feat: 新功能
- fix: 修复 bug
- docs: 文档更新
- refactor: 重构
- test: 测试相关

## 许可证

MIT License

## 相关链接

- [LocalBook 主项目](https://github.com/localbook)
- [开发文档](https://docs.localbook.app)
- [API 文档](https://api.localbook.app)
