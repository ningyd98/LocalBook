//
//  AccessibilityCapture.swift
//  ReadFlow
//
//  Created on 2024-09-12.
//

import Foundation
import AppKit
import ApplicationServices

/// 捕获结果
struct CapturedSelection {
    let text: String
    let contextBefore: String
    let contextAfter: String
    let source: CapturedSource
    let location: CapturedLocation
    let capturedAt: Date
}

/// 捕获来源信息
struct CapturedSource {
    let appBundleID: String
    let appName: String
    let windowTitle: String?
    let url: String?
    let filePath: String?
    let pageNumber: Int?
}

/// 捕获位置信息
struct CapturedLocation {
    let selectedRange: CFRange?
    let scrollPosition: CGFloat?
}

class AccessibilityCapture {

    // MARK: - Accessibility Permissions

    static func checkAccessibilityPermission() -> Bool {
        let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true]
        return AXIsProcessTrustedWithOptions(options as CFDictionary)
    }

    static func requestAccessibilityPermission() {
        let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true]
        _ = AXIsProcessTrustedWithOptions(options as CFDictionary)
    }

    // MARK: - Main Capture Method

    /// 捕获当前选中文本及上下文
    func captureSelection() -> CapturedSelection? {
        guard Self.checkAccessibilityPermission() else {
            print("❌ Accessibility permission not granted")
            return nil
        }

        guard let app = NSWorkspace.shared.frontmostApplication else {
            print("❌ No frontmost application")
            return nil
        }

        let pid = app.processIdentifier
        let appElement = AXUIElementCreateApplication(pid)

        // 1. 获取选中文本
        guard let selectedText = getSelectedText(from: appElement) else {
            print("❌ No selected text")
            return nil
        }

        // 2. 获取选中范围
        let selectedRange = getSelectedRange(from: appElement)

        // 3. 获取上下文（前后各 200 字符）
        let context = captureContext(element: appElement, selectedText: selectedText, selectedRange: selectedRange)

        // 4. 获取来源信息
        let source = resolveSource(app: app, appElement: appElement)

        // 5. 获取位置信息
        let location = CapturedLocation(
            selectedRange: selectedRange,
            scrollPosition: nil
        )

        return CapturedSelection(
            text: selectedText,
            contextBefore: context.before,
            contextAfter: context.after,
            source: source,
            location: location,
            capturedAt: Date()
        )
    }

    // MARK: - Text Capture

    private func getSelectedText(from element: AXUIElement) -> String? {
        var selectedText: CFTypeRef?
        let result = AXUIElementCopyAttributeValue(
            element,
            kAXSelectedTextAttribute as CFString,
            &selectedText
        )

        if result == .success, let text = selectedText as? String, !text.isEmpty {
            return text
        }

        // Fallback: 尝试从 focused element 获取
        return getSelectedTextFromFocusedElement(appElement: element)
    }

    private func getSelectedTextFromFocusedElement(appElement: AXUIElement) -> String? {
        var focusedElement: CFTypeRef?
        let focusResult = AXUIElementCopyAttributeValue(
            appElement,
            kAXFocusedUIElementAttribute as CFString,
            &focusedElement
        )

        guard focusResult == .success, let focused = focusedElement else {
            return nil
        }

        var selectedText: CFTypeRef?
        let textResult = AXUIElementCopyAttributeValue(
            focused as! AXUIElement,
            kAXSelectedTextAttribute as CFString,
            &selectedText
        )

        if textResult == .success, let text = selectedText as? String, !text.isEmpty {
            return text
        }

        return nil
    }

    // MARK: - Range Detection

    private func getSelectedRange(from element: AXUIElement) -> CFRange? {
        var selectedRange: CFTypeRef?
        let result = AXUIElementCopyAttributeValue(
            element,
            kAXSelectedTextRangeAttribute as CFString,
            &selectedRange
        )

        if result == .success, let range = selectedRange {
            var cfRange = CFRange()
            if AXValueGetValue(range as! AXValue, .cfRange, &cfRange) {
                return cfRange
            }
        }

        return nil
    }

    // MARK: - Context Capture

    private func captureContext(element: AXUIElement, selectedText: String, selectedRange: CFRange?) -> (before: String, after: String) {
        // 尝试获取完整文本
        guard let fullText = getFullText(from: element) else {
            return ("", "")
        }

        // 如果有 selectedRange，使用精确位置
        if let range = selectedRange {
            return extractContextFromRange(fullText: fullText, range: range)
        }

        // Fallback: 在 fullText 中查找 selectedText
        return extractContextBySearch(fullText: fullText, selectedText: selectedText)
    }

    private func getFullText(from element: AXUIElement) -> String? {
        // 尝试从 focused element 获取完整文本
        var focusedElement: CFTypeRef?
        let focusResult = AXUIElementCopyAttributeValue(
            element,
            kAXFocusedUIElementAttribute as CFString,
            &focusedElement
        )

        if focusResult == .success, let focused = focusedElement {
            var value: CFTypeRef?
            let valueResult = AXUIElementCopyAttributeValue(
                focused as! AXUIElement,
                kAXValueAttribute as CFString,
                &value
            )

            if valueResult == .success, let text = value as? String {
                return text
            }
        }

        // Fallback: 尝试从 app element 获取
        var value: CFTypeRef?
        let result = AXUIElementCopyAttributeValue(
            element,
            kAXValueAttribute as CFString,
            &value
        )

        if result == .success, let text = value as? String {
            return text
        }

        return nil
    }

    private func extractContextFromRange(fullText: String, range: CFRange) -> (before: String, after: String) {
        let beforeStart = max(0, range.location - 200)

        let beforeText: String
        if beforeStart < fullText.count {
            let startIndex = fullText.index(fullText.startIndex, offsetBy: beforeStart)
            let endIndex = fullText.index(fullText.startIndex, offsetBy: min(range.location, fullText.count))
            beforeText = String(fullText[startIndex..<endIndex])
        } else {
            beforeText = ""
        }

        let afterStart = range.location + range.length
        let afterEnd = min(afterStart + 200, fullText.count)

        let afterText: String
        if afterStart < fullText.count {
            let startIndex = fullText.index(fullText.startIndex, offsetBy: afterStart)
            let endIndex = fullText.index(fullText.startIndex, offsetBy: afterEnd)
            afterText = String(fullText[startIndex..<endIndex])
        } else {
            afterText = ""
        }

        return (beforeText, afterText)
    }

    private func extractContextBySearch(fullText: String, selectedText: String) -> (before: String, after: String) {
        guard let range = fullText.range(of: selectedText) else {
            return ("", "")
        }

        let beforeStart = fullText.index(range.lowerBound, offsetBy: -200, limitedBy: fullText.startIndex) ?? fullText.startIndex
        let beforeText = String(fullText[beforeStart..<range.lowerBound])

        let afterEnd = fullText.index(range.upperBound, offsetBy: 200, limitedBy: fullText.endIndex) ?? fullText.endIndex
        let afterText = String(fullText[range.upperBound..<afterEnd])

        return (beforeText, afterText)
    }

    // MARK: - Source Resolution

    private func resolveSource(app: NSRunningApplication, appElement: AXUIElement) -> CapturedSource {
        let bundleID = app.bundleIdentifier ?? "unknown"
        let appName = app.localizedName ?? "Unknown App"

        let windowTitle = getWindowTitle(from: appElement)
        let url = extractURL(bundleID: bundleID, appElement: appElement, windowTitle: windowTitle)
        let filePath = extractFilePath(bundleID: bundleID, windowTitle: windowTitle)
        let pageNumber = extractPageNumber(bundleID: bundleID, appElement: appElement)

        return CapturedSource(
            appBundleID: bundleID,
            appName: appName,
            windowTitle: windowTitle,
            url: url,
            filePath: filePath,
            pageNumber: pageNumber
        )
    }

    private func getWindowTitle(from element: AXUIElement) -> String? {
        var focusedWindow: CFTypeRef?
        let windowResult = AXUIElementCopyAttributeValue(
            element,
            kAXFocusedWindowAttribute as CFString,
            &focusedWindow
        )

        guard windowResult == .success, let window = focusedWindow else {
            return nil
        }

        var title: CFTypeRef?
        let titleResult = AXUIElementCopyAttributeValue(
            window as! AXUIElement,
            kAXTitleAttribute as CFString,
            &title
        )

        if titleResult == .success, let titleString = title as? String {
            return titleString
        }

        return nil
    }

    private func extractURL(bundleID: String, appElement: AXUIElement, windowTitle: String?) -> String? {
        // Safari 和 Chrome 的 URL 需要通过 Extension 获取
        // 这里仅尝试从 window title 解析（某些浏览器会显示 URL）
        if bundleID.contains("Safari") || bundleID.contains("Chrome") || bundleID.contains("Firefox") {
            // Extension 会通过其他途径注入 URL
            return nil
        }

        return nil
    }

    private func extractFilePath(bundleID: String, windowTitle: String?) -> String? {
        // Preview.app, TextEdit.app 等会在 window title 显示文件路径
        guard let title = windowTitle else { return nil }

        if bundleID == "com.apple.Preview" || bundleID == "com.apple.TextEdit" {
            // 尝试从 title 提取文件路径
            // 例如: "document.pdf" 或 "/Users/xxx/document.pdf"
            if title.hasPrefix("/") {
                return title
            } else if title.contains(".") {
                // 相对路径，尝试展开
                let fileManager = FileManager.default
                let expanded = (title as NSString).expandingTildeInPath
                if fileManager.fileExists(atPath: expanded) {
                    return expanded
                }
            }
        }

        return nil
    }

    private func extractPageNumber(bundleID: String, appElement: AXUIElement) -> Int? {
        // Preview.app 可能暴露页码信息（取决于具体实现）
        // 这里返回 nil，实际需要进一步调研 AXUIElement 属性
        return nil
    }
}
