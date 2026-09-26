//
//  SelectionService.swift
//  ReadFlow
//
//  Created on 2024-09-12.
//

import Foundation
import AppKit
import Combine

/// 选中文本服务，整合 Accessibility 捕获和 Safari Extension 消息
class SelectionService: ObservableObject {
    static let shared = SelectionService()

    private let accessibilityCapture = AccessibilityCapture()
    private var extensionMessageHandler: SafariExtensionMessageHandler?

    @Published var lastCapturedSelection: CapturedSelection?

    private init() {
        setupExtensionMessageHandler()
    }

    // MARK: - Public API

    /// 捕获当前选中文本（通过 Accessibility API）
    /// 捕获成功后自动显示 FloatingToolbar
    func captureSelection() -> CapturedSelection? {
        guard let selection = accessibilityCapture.captureSelection() else {
            return nil
        }

        // 更新 published 属性
        DispatchQueue.main.async {
            self.lastCapturedSelection = selection
        }

        // 显示 FloatingToolbar
        DispatchQueue.main.async {
            // 获取鼠标位置
            let mouseLocation = NSEvent.mouseLocation
            FloatingToolbar.shared.show(at: mouseLocation, for: selection)
        }

        return selection
    }

    /// 检查 Accessibility 权限
    func checkAccessibilityPermission() -> Bool {
        return AccessibilityCapture.checkAccessibilityPermission()
    }

    /// 请求 Accessibility 权限
    func requestAccessibilityPermission() {
        AccessibilityCapture.requestAccessibilityPermission()
    }

    // MARK: - Safari Extension Integration

    private func setupExtensionMessageHandler() {
        extensionMessageHandler = SafariExtensionMessageHandler()
        extensionMessageHandler?.startListening { [weak self] message in
            self?.handleExtensionMessage(message)
        }
    }

    /// 处理来自 Safari Extension 的选中文本消息
    /// 自动显示 FloatingToolbar
    func handleExtensionMessage(_ message: SafariExtensionMessage) {
        let selection = CapturedSelection(
            text: message.text,
            contextBefore: message.contextBefore ?? "",
            contextAfter: message.contextAfter ?? "",
            source: CapturedSource(
                appBundleID: "com.apple.Safari",
                appName: "Safari",
                windowTitle: message.title,
                url: message.url,
                filePath: nil,
                pageNumber: nil
            ),
            location: CapturedLocation(
                selectedRange: nil,
                scrollPosition: message.scrollY
            ),
            capturedAt: Date()
        )

        DispatchQueue.main.async {
            self.lastCapturedSelection = selection

            // 显示 FloatingToolbar（在鼠标位置）
            let mouseLocation = NSEvent.mouseLocation
            FloatingToolbar.shared.show(at: mouseLocation, for: selection)
        }
    }
}

// MARK: - Safari Extension Message Handler

/// Safari Extension 消息格式
struct SafariExtensionMessage: Codable {
    let type: String
    let text: String
    let url: String
    let title: String
    let contextBefore: String?
    let contextAfter: String?
    let scrollY: CGFloat?
}

final class SafariExtensionMessageHandler {
    /// The native messaging host posts this distributed notification after it
    /// receives a WebExtension message. Distributed notifications keep the
    /// bridge local to the user's login session and require no network socket.
    static let notificationName = Notification.Name("com.localbook.readflow.selection")

    private var observer: NSObjectProtocol?

    func startListening(onMessage: @escaping (SafariExtensionMessage) -> Void) {
        guard observer == nil else { return }
        observer = DistributedNotificationCenter.default.addObserver(
            forName: Self.notificationName,
            object: nil,
            queue: .main
        ) { notification in
            guard let userInfo = notification.userInfo,
                  let data = try? JSONSerialization.data(withJSONObject: userInfo),
                  let message = try? JSONDecoder().decode(SafariExtensionMessage.self, from: data)
            else { return }
            onMessage(message)
        }
    }

    deinit {
        if let observer {
            DistributedNotificationCenter.default.removeObserver(observer)
        }
    }
}

/// Minimal Chromium/Safari native-messaging host implementation. The extension
/// sends a length-prefixed JSON frame; this mode forwards it to the GUI process
/// through the distributed-notification bridge and acknowledges the frame.
enum NativeMessagingHost {
    static let flag = "--native-messaging-host"

    static func run(
        input: FileHandle = .standardInput,
        output: FileHandle = .standardOutput
    ) -> Int {
        while let header = readExactly(input, count: 4) {
            let bytes = [UInt8](header)
            let length = Int(bytes[0])
                | (Int(bytes[1]) << 8)
                | (Int(bytes[2]) << 16)
                | (Int(bytes[3]) << 24)
            guard length > 0, length <= 1_048_576,
                  let payload = readExactly(input, count: length),
                  let object = try? JSONSerialization.jsonObject(with: payload),
                  let dictionary = object as? [String: Any]
            else {
                return 2
            }

            let userInfo = dictionary.reduce(into: [AnyHashable: Any]()) {
                $0[AnyHashable($1.key)] = $1.value
            }
            DistributedNotificationCenter.default.post(
                name: SafariExtensionMessageHandler.notificationName,
                object: nil,
                userInfo: userInfo
            )
            writeResponse(["success": true], to: output)
        }
        return 0
    }

    private static func readExactly(_ handle: FileHandle, count: Int) -> Data? {
        var data = Data()
        while data.count < count {
            do {
                guard let chunk = try handle.read(upToCount: count - data.count),
                      !chunk.isEmpty else { return nil }
                data.append(chunk)
            } catch {
                return nil
            }
        }
        return data
    }

    private static func writeResponse(_ response: [String: Any], to handle: FileHandle) {
        guard let payload = try? JSONSerialization.data(withJSONObject: response) else { return }
        let length = UInt32(payload.count)
        var frame = Data([
            UInt8(length & 0xff),
            UInt8((length >> 8) & 0xff),
            UInt8((length >> 16) & 0xff),
            UInt8((length >> 24) & 0xff),
        ])
        frame.append(payload)
        handle.write(frame)
    }
}
