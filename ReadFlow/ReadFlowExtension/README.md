# ReadFlow Safari Extension

Safari 浏览器扩展，用于捕获网页选中文本并发送到 ReadFlow macOS 应用。

## 功能

- ✅ 监听网页文本选择（mouseup 和 keyboard selection）
- ✅ 捕获选中文本、URL、标题
- ✅ 提取上下文（前后各 200 字符）
- ✅ 记录滚动位置
- ✅ 通过 Native Messaging 与 macOS App 通信
- ✅ localStorage 作为 fallback 存储

## 文件结构

```
ReadFlowExtension/
├── manifest.json       # Extension 配置
├── content.js          # 内容脚本（注入页面）
├── background.js       # 后台脚本（处理消息）
├── readflow-native-host.sh # Native Messaging 主机包装器（开发安装用）
├── icons/              # 图标资源
│   ├── icon-16.png
│   ├── icon-32.png
│   ├── icon-48.png
│   └── icon-128.png
└── README.md
```

## 安装步骤

### 1. 生成图标

```bash
# 在 ReadFlow 项目根目录执行
cd ReadFlowExtension
mkdir -p icons

# 使用 sips 从 App 图标生成多尺寸版本
# (假设 App 图标在 ReadFlow/Assets.xcassets/AppIcon.appiconset/)
# 或使用在线工具生成 16x16, 32x32, 48x48, 128x128 PNG
```

### 2. 配置 Native Messaging

创建 Native Messaging 配置文件：

```bash
# 创建配置目录
mkdir -p ~/Library/Application\ Support/Mozilla/NativeMessagingHosts/

# 创建配置文件
cat > ~/Library/Application\ Support/Mozilla/NativeMessagingHosts/com.readflow.capture.json << 'EOF'
{
  "name": "com.readflow.capture",
  "description": "ReadFlow Native Messaging Host",
  "path": "/Applications/ReadFlow.app/Contents/Resources/readflow-native-host",
  "type": "stdio",
  "allowed_extensions": ["readflow@localbook.app"]
}
EOF
```

### 3. 在 Safari 中加载扩展

1. 打开 Safari → Preferences → Advanced
2. 勾选 "Show Develop menu in menu bar"
3. 打开 Develop → Allow Unsigned Extensions
4. 打开 Develop → Show Extension Builder
5. 点击 "+" 添加扩展
6. 选择 `ReadFlowExtension` 目录
7. 点击 "Install" 安装扩展

### 4. 授予权限

Safari 会提示授予以下权限：
- 访问所有网站 (`<all_urls>`)
- Native Messaging

## 使用方法

1. 在任意网页选中文本
2. 松开鼠标（或结束键盘选择）
3. Extension 自动捕获并发送到 ReadFlow App
4. 查看浏览器控制台确认消息：
   - "ReadFlow: Selection captured successfully"

## 消息格式

### Content Script → Background Script

```javascript
{
  "type": "selection",
  "text": "选中的文本",
  "url": "https://example.com/page",
  "title": "Page Title",
  "contextBefore": "...前 200 字符",
  "contextAfter": "后 200 字符...",
  "scrollY": 1234.5,
  "timestamp": "2024-01-15T10:30:00.000Z"
}
```

### Background Script → Native App

通过 `browser.runtime.connectNative()` 建立连接，使用 `postMessage()` 发送 JSON。

## Fallback 机制

如果 Native Messaging 连接失败，消息会保存到 `localStorage`：

```javascript
localStorage.getItem('readflow_selections') // Array<Message>
```

Native App 可通过 Safari 的 LocalStorage 路径读取：
```
~/Library/Safari/LocalStorage/safari-extension_<extension-id>_0.localstorage
```

## 调试

### 查看 Content Script 日志

1. 打开任意网页
2. 右键 → Inspect Element
3. 切换到 Console 面板
4. 选中文本，查看日志输出

### 查看 Background Script 日志

1. 打开 Safari → Develop → Show Extension Builder
2. 选择 ReadFlow 扩展
3. 点击 "Inspect Background Page"
4. 查看 Console 面板

### 测试 Native Messaging

```bash
# 查看 Native Messaging 配置
cat ~/Library/Application\ Support/Mozilla/NativeMessagingHosts/com.readflow.capture.json

# 测试 Native App 可执行性
/Applications/ReadFlow.app/Contents/MacOS/ReadFlow --version
```

## 已知限制

1. **Safari 14+ 支持**：Manifest V2 需要 Safari 14 或更高版本。
2. **Unsigned Extension**：开发加载时需要在 Develop 菜单启用 Allow Unsigned Extensions。
3. **安装路径**：Native Messaging 配置示例假定应用已复制到 `/Applications/ReadFlow.app`。
4. **Sandboxing**：Safari Extension 运行在沙盒中，不能直接访问 ReadFlow 的 SQLite 文件。

Native Messaging 主机已经由 `ReadFlow.app/Contents/Resources/readflow-native-host` 提供：
它读取长度前缀 JSON、通过 `DistributedNotificationCenter` 转交 GUI 实例，再返回
`{"success":true}` 确认。GUI 端把通知转换为 `CapturedSelection` 并显示浮动工具栏；
Native Messaging 不可用时，扩展仍保留最近 50 条浏览器本地缓存供诊断。

参考文档：
- [Safari App Extensions](https://developer.apple.com/documentation/safariservices/safari_app_extensions)
- [Native Messaging](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/Native_messaging)
