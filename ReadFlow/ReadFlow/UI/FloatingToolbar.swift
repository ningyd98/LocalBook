import Cocoa
import SwiftUI

/**
 FloatingToolbar - 划词后显示的浮动工具栏

 功能：
 - borderless, nonactivatingPanel 样式
 - floating level（浮动在所有窗口之上）
 - 透明背景
 - 四个操作按钮：保存、AI、搜索、笔记
 */
class FloatingToolbar: NSPanel {

    // MARK: - Properties

    private let stackView: NSStackView
    private var currentSelection: CapturedSelection?
    private let database: ReaderDatabase
    private var feedbackWindow: NSWindow?

    // 按钮引用
    private var saveButton: NSButton?
    private var aiButton: NSButton?
    private var searchButton: NSButton?
    private var noteButton: NSButton?

    // MARK: - Singleton

    static let shared = FloatingToolbar()

    // MARK: - Initialization

    private init() {
        // 初始化数据库。`ReaderDatabase.shared` degrades to an in-memory store
        // instead of trapping, so no `try!` here: a toolbar must never be the
        // thing that crashes the app.
        self.database = ReaderDatabase.shared

        // `stackView` is a `let`, so it has to be assigned before super.init;
        // it is configured below.
        self.stackView = NSStackView()

        // 创建 Panel
        super.init(
            contentRect: NSRect(x: 0, y: 0, width: 240, height: 48),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )

        // Panel 样式
        self.level = .floating
        self.isOpaque = false
        self.backgroundColor = .clear
        self.hasShadow = true
        self.collectionBehavior = [.canJoinAllSpaces, .stationary]

        // 创建内容视图
        let contentView = NSView(frame: self.contentRect(forFrameRect: self.frame))
        contentView.wantsLayer = true
        contentView.layer?.backgroundColor = NSColor.controlBackgroundColor.withAlphaComponent(0.95).cgColor
        contentView.layer?.cornerRadius = 8
        contentView.layer?.shadowColor = NSColor.black.cgColor
        contentView.layer?.shadowOpacity = 0.3
        contentView.layer?.shadowOffset = CGSize(width: 0, height: -2)
        contentView.layer?.shadowRadius = 8

        // 创建按钮容器
        self.stackView.distribution = .fillEqually
        self.stackView.spacing = 8
        self.stackView.orientation = .horizontal
        self.stackView.edgeInsets = NSEdgeInsets(top: 8, left: 12, bottom: 8, right: 12)

        // 添加按钮
        self.saveButton = addButton(icon: "star.fill", tooltip: "保存高亮", action: #selector(saveHighlight))
        self.aiButton = addButton(icon: "brain.head.profile", tooltip: "AI 分析", action: #selector(askAI))
        self.searchButton = addButton(icon: "magnifyingglass", tooltip: "搜索相关内容", action: #selector(search))
        self.noteButton = addButton(icon: "note.text", tooltip: "添加笔记", action: #selector(addNote))

        // 布局
        contentView.addSubview(stackView)
        stackView.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([
            stackView.topAnchor.constraint(equalTo: contentView.topAnchor),
            stackView.bottomAnchor.constraint(equalTo: contentView.bottomAnchor),
            stackView.leadingAnchor.constraint(equalTo: contentView.leadingAnchor),
            stackView.trailingAnchor.constraint(equalTo: contentView.trailingAnchor)
        ])

        self.contentView = contentView

        // 监听点击外部关闭
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(windowDidResignKey),
            name: NSWindow.didResignKeyNotification,
            object: nil
        )
    }

    // MARK: - Button Creation

    private func addButton(icon: String, tooltip: String, action: Selector) -> NSButton {
        let button = NSButton()
        button.bezelStyle = .regularSquare
        button.isBordered = false
        button.wantsLayer = true
        button.layer?.cornerRadius = 6
        button.toolTip = tooltip

        // SF Symbol 图标
        if let image = NSImage(systemSymbolName: icon, accessibilityDescription: tooltip) {
            let config = NSImage.SymbolConfiguration(pointSize: 18, weight: .medium)
            button.image = image.withSymbolConfiguration(config)
        }

        button.imagePosition = .imageOnly
        button.target = self
        button.action = action

        // 悬停效果
        button.layer?.backgroundColor = NSColor.clear.cgColor

        // 添加到 stack view
        stackView.addArrangedSubview(button)

        // 设置尺寸
        button.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([
            button.widthAnchor.constraint(equalToConstant: 48),
            button.heightAnchor.constraint(equalToConstant: 32)
        ])

        return button
    }

    // MARK: - Public Methods

    /// 在指定位置显示工具栏
    func show(at point: NSPoint, for selection: CapturedSelection) {
        self.currentSelection = selection

        // 调整位置（确保不超出屏幕）
        var adjustedPoint = point
        if let screen = NSScreen.main {
            let screenFrame = screen.visibleFrame
            let toolbarWidth = self.frame.width
            let toolbarHeight = self.frame.height

            // 调整 X 坐标
            if adjustedPoint.x + toolbarWidth > screenFrame.maxX {
                adjustedPoint.x = screenFrame.maxX - toolbarWidth - 10
            }
            if adjustedPoint.x < screenFrame.minX {
                adjustedPoint.x = screenFrame.minX + 10
            }

            // 调整 Y 坐标（工具栏显示在选中文字上方）
            adjustedPoint.y += 30
            if adjustedPoint.y + toolbarHeight > screenFrame.maxY {
                adjustedPoint.y = point.y - toolbarHeight - 10
            }
        }

        self.setFrameOrigin(adjustedPoint)
        self.orderFrontRegardless()

        // 3秒后自动隐藏（如果没有交互）
        DispatchQueue.main.asyncAfter(deadline: .now() + 3.0) { [weak self] in
            if self?.isVisible == true && !self!.isKeyWindow {
                self?.orderOut(nil)
            }
        }
    }

    /// 隐藏工具栏
    func hide() {
        self.orderOut(nil)
    }

    // MARK: - Actions

    @objc private func saveHighlight() {
        guard let selection = currentSelection else { return }

        let startTime = Date()

        do {
            // 1. 解析或创建 Source
            let source = try resolveSource(from: selection.source)

            // 2. 创建 Highlight
            let highlight = Highlight(
                id: UUID(),
                sourceID: source.id,
                selectedText: selection.text,
                contextBefore: selection.contextBefore,
                contextAfter: selection.contextAfter,
                page: selection.source.pageNumber,
                createdAt: selection.capturedAt
            )

            // 3. 保存到数据库（包含 Outbox）
            try database.createHighlight(highlight)

            let duration = Date().timeIntervalSince(startTime)
            print("✅ Highlight saved in \(Int(duration * 1000))ms")

            // 4. UI 反馈
            showCheckmark()

            // 5. 后台触发同步
            // Coalesce this write with any other local changes and let the real
            // SyncEngine perform push/pull when a bearer token is configured.
            SyncEngine.shared.triggerSync()

            // 6. 隐藏工具栏
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
                self?.hide()
            }

        } catch {
            print("❌ Failed to save highlight: \(error)")
            showError("保存失败")
        }
    }

    @objc private func askAI() {
        guard let selection = currentSelection else { return }
        let request = AIRequest(
            query: selection.text,
            context: [selection.contextBefore, selection.contextAfter]
                .filter { !$0.isEmpty }
                .joined(separator: "\n"),
            sourceID: nil
        )
        let coordinator = AICoordinator.make(
            cloudConfiguration: nil,
            localbookProvider: LocalBookAIProvider()
        )
        Task {
            do {
                let outcome = try await coordinator.ask(request)
                await MainActor.run {
                    self.showFeedback(outcome.response.text)
                    self.hide()
                }
            } catch {
                await MainActor.run {
                    self.showError("AI 请求失败")
                    self.hide()
                }
            }
        }
    }

    @objc private func search() {
        guard let selection = currentSelection else { return }
        Task {
            do {
                let response = try await LocalBookClient.shared.search(query: selection.text, limit: 5)
                let message = response.results.isEmpty
                    ? "未找到相关内容"
                    : "找到 \(response.total) 条相关内容"
                await MainActor.run {
                    self.showFeedback(message)
                    self.hide()
                }
            } catch {
                await MainActor.run {
                    self.showError("请先配置并连接 LocalBook")
                    self.hide()
                }
            }
        }
    }

    @objc private func addNote() {
        guard let selection = currentSelection else { return }
        do {
            let source = try resolveSource(from: selection.source)
            let note = Note(
                sourceID: source.id,
                highlightID: nil,
                content: selection.text,
                createdAt: selection.capturedAt
            )
            try database.createNote(note)
            showFeedback("✓ 笔记已保存")
            SyncEngine.shared.triggerSync()
            hide()
        } catch {
            showError("笔记保存失败")
        }
    }

    // MARK: - Helper Methods

    private func resolveSource(from capturedSource: CapturedSource) throws -> Source {
        // 尝试通过 URL 或 filePath 查找现有 Source
        if capturedSource.url != nil {
            // 搜索是否已存在该 URL 的 Source
            // 暂时简化：直接创建新 Source
        } else if capturedSource.filePath != nil {
            // 搜索是否已存在该文件路径的 Source
        }

        // 创建新 Source
        let source = Source(
            id: UUID(),
            type: determineSourceType(from: capturedSource),
            title: capturedSource.windowTitle ?? "Untitled",
            url: capturedSource.url,
            filePath: capturedSource.filePath,
            createdAt: Date(),
            metadata: SourceMetadata(
                domain: capturedSource.url.flatMap { URL(string: $0)?.host },
                appBundleID: capturedSource.appBundleID,
                appName: capturedSource.appName
            )
        )

        try database.createSource(source)
        return source
    }

    private func determineSourceType(from capturedSource: CapturedSource) -> Source.SourceType {
        if capturedSource.url != nil {
            return .web
        }

        if let filePath = capturedSource.filePath {
            switch (filePath as NSString).pathExtension.lowercased() {
            case "pdf":
                return .pdf
            case "md", "markdown":
                return .markdown
            default:
                // .epub and everything else has no dedicated case yet.
                return .document
            }
        }

        // The Reader wire contract accepts document/markdown/web/pdf. A
        // selection from a generic desktop app is stored as a document source.
        return .document
    }

    /// 显示 ✓ 已保存反馈
    private func showCheckmark() {
        showFeedback("✓ 已保存")
    }

    /// 显示错误反馈
    private func showError(_ message: String) {
        showFeedback("✗ \(message)")
    }

    /// 显示反馈消息
    private func showFeedback(_ message: String) {
        // 关闭之前的反馈窗口
        feedbackWindow?.close()

        // 创建反馈窗口
        let feedbackRect = NSRect(x: 0, y: 0, width: 120, height: 40)
        let window = NSWindow(
            contentRect: feedbackRect,
            styleMask: [.borderless],
            backing: .buffered,
            defer: false
        )

        window.level = .floating
        window.isOpaque = false
        window.backgroundColor = .clear
        window.hasShadow = true

        // 创建内容视图
        let contentView = NSView(frame: feedbackRect)
        contentView.wantsLayer = true
        contentView.layer?.backgroundColor = NSColor.controlBackgroundColor.withAlphaComponent(0.95).cgColor
        contentView.layer?.cornerRadius = 8

        // 创建文本标签
        let label = NSTextField(labelWithString: message)
        label.font = NSFont.systemFont(ofSize: 14, weight: .medium)
        label.alignment = .center
        label.textColor = .labelColor

        contentView.addSubview(label)
        label.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([
            label.centerXAnchor.constraint(equalTo: contentView.centerXAnchor),
            label.centerYAnchor.constraint(equalTo: contentView.centerYAnchor)
        ])

        window.contentView = contentView

        // 位置：在工具栏中央上方
        let toolbarCenter = NSPoint(
            x: self.frame.origin.x + self.frame.width / 2 - feedbackRect.width / 2,
            y: self.frame.origin.y + self.frame.height + 10
        )
        window.setFrameOrigin(toolbarCenter)

        window.orderFrontRegardless()
        self.feedbackWindow = window

        // 1秒后自动关闭
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) {
            window.close()
        }
    }

    // MARK: - Notifications

    @objc private func windowDidResignKey(_ notification: Notification) {
        // 当失去焦点时延迟隐藏
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { [weak self] in
            if self?.isKeyWindow == false {
                self?.hide()
            }
        }
    }
}
