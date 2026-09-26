# ReadFlow Extension Icons

此目录存放 Safari Extension 所需的图标资源。

## 所需尺寸

- `icon-16.png`: 16x16 像素（工具栏图标）
- `icon-32.png`: 32x32 像素（Retina 工具栏）
- `icon-48.png`: 48x48 像素（扩展管理器）
- `icon-128.png`: 128x128 像素（扩展商店）

## 生成方法

### 方法 1: 使用 sips（macOS 自带）

如果你有高分辨率的应用图标：

```bash
# 从 1024x1024 的图标生成
sips -Z 16 app-icon.png --out icon-16.png
sips -Z 32 app-icon.png --out icon-32.png
sips -Z 48 app-icon.png --out icon-48.png
sips -Z 128 app-icon.png --out icon-128.png
```

### 方法 2: 使用 iconutil（从 .icns）

```bash
# 从 macOS .icns 文件提取
iconutil -c iconset ReadFlow.icns -o ReadFlow.iconset/
cp ReadFlow.iconset/icon_16x16.png icons/icon-16.png
cp ReadFlow.iconset/icon_32x32.png icons/icon-32.png
cp ReadFlow.iconset/icon_48x48.png icons/icon-48.png
cp ReadFlow.iconset/icon_128x128.png icons/icon-128.png
```

### 方法 3: 使用在线工具

访问 [favicon.io](https://favicon.io/) 或类似工具生成多尺寸图标。

## 临时占位符

在开发阶段，可以使用纯色占位符：

```bash
# 生成蓝色占位符图标
for size in 16 32 48 128; do
  sips -z $size $size -s format png /System/Library/CoreServices/CoreTypes.bundle/Contents/Resources/BookmarkIcon.icns --out icon-$size.png 2>/dev/null
done
```

## 设计建议

- **简洁**: 16x16 需要清晰识别，避免过多细节
- **品牌**: 使用与 App 一致的配色方案
- **对比**: 确保在浅色和深色工具栏都能看清
- **透明**: 使用透明背景 PNG
