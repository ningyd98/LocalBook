//
//  AppDelegate.swift
//  ReadFlow
//
//  Created on 2024-09-12.
//

import Cocoa

class AppDelegate: NSObject, NSApplicationDelegate {

    private var statusItem: NSStatusItem?
    private let selectionService = SelectionService.shared
    var openSettingsWindow: (() -> Void)?

    func applicationDidFinishLaunching(_ aNotification: Notification) {
        // Safari's native-messaging host uses the same executable in a short-lived
        // stdin/stdout mode. It posts the selection to the already-running GUI
        // instance and exits without opening another window.
        if CommandLine.arguments.contains(NativeMessagingHost.flag) {
            exit(Int32(NativeMessagingHost.run()))
        }

        // Headless self-check path: run the storage checks and exit before any
        // UI state is touched (in particular before the accessibility prompt).
        if CommandLine.arguments.contains(StorageSelfCheck.flag) {
            exit(StorageSelfCheck.run())
        }

        // Headless UI snapshot (`--render-ui <dir>`). Renders the SwiftUI surfaces
        // to PNG through `ImageRenderer`, which needs no window, no display and no
        // logged-in session — the three things this environment does not have.
        // Exits before any real UI state is created, like the outlets above.
        if let index = CommandLine.arguments.firstIndex(of: UISnapshot.flag) {
            let directory = CommandLine.arguments.count > index + 1
                ? CommandLine.arguments[index + 1]
                : FileManager.default.currentDirectoryPath + "/ui-snapshots"
            exit(UISnapshot.run(outputDir: directory))
        }

        // Headless AI/attribution self-test (`--self-test <scenario>`). Same
        // reasoning as above: exit before touching UI or prompting for
        // accessibility, so the outlet stays usable from a plain shell — and
        // `exit` rather than falling through, so an automated run does not also
        // launch the app (which would open a window and initialise the shared
        // store as a side effect of asking a question about it).
        if let index = CommandLine.arguments.firstIndex(of: AISelfTest.flag) {
            let scenario = CommandLine.arguments.count > index + 1
                ? CommandLine.arguments[index + 1]
                : ""

            // Bare `--self-test` (no scenario) is intercepted here so the help
            // text lists the **union** of scenarios, including the one dispatched
            // from this file. Without this, the no-argument path falls through to
            // `AISelfTest`'s default branch, which advertises only its own twelve
            // — and the thirteenth, `search-query`, would stay invisible to
            // anyone reading help output or grepping the scripts rather than this
            // dispatcher. An undiscoverable instrument is close to no instrument.
            if scenario.isEmpty {
                // Built from `AISelfTest.knownScenarios` rather than re-listed, so
                // adding a scenario there cannot leave this help text stale.
                let scenarios = AISelfTest.knownScenarios
                    + ["\(SearchQuerySelfTest.scenario) <text>",
                       "\(PopoverLivePathSelfTest.scenario) <text>"]
                let scenarioHelp = "self-test: known scenarios: \(scenarios.joined(separator: ", "))\n"
                let scenarioNote = "self-test: note — `\(SearchQuerySelfTest.scenario)` takes a query argument "
                    + "and is dispatched separately; the others are handled by AISelfTest.\n"
                FileHandle.standardError.write(Data(scenarioHelp.utf8))
                FileHandle.standardError.write(Data(scenarioNote.utf8))
                FileHandle.standardError.write(
                    Data("self-test: FAIL — no scenario given\n".utf8)
                )
                exit(2)
            }

            // `search-query` is dispatched here rather than inside `AISelfTest`
            // because it needs a *query argument* and lives in its own file
            // (AISelfTest is owned by another member and stays untouched). It
            // must be handled before the switch below, whose `default` exits 2.
            if scenario == SearchQuerySelfTest.scenario {
                let query = CommandLine.arguments.count > index + 2
                    ? CommandLine.arguments[index + 2]
                    : nil
                exit(SearchQuerySelfTest.run(query: query))
            }

            // `popover-send` likewise takes an argument and lives in its own file.
            // It drives the *live* answering path — the same `sendQuery` the
            // popover's send button calls — which is what makes it evidence about
            // the product rather than about the coordinator component.
            if scenario == PopoverLivePathSelfTest.scenario {
                let query = CommandLine.arguments.count > index + 2
                    ? CommandLine.arguments[index + 2]
                    : nil
                exit(PopoverLivePathSelfTest.run(query: query))
            }

            exit(AISelfTest.run(scenario: scenario))
        }

        // Unknown member of the headless-verification flag family is a hard
        // error (contract C34.2).
        //
        // Scope is deliberately narrow: only `--self-*` arguments are rejected.
        // A GUI app normally receives unrelated arguments from the system and
        // from double-clicking in Finder, so rejecting every unknown argument
        // would break ordinary launches — measured before this guard: an
        // unknown *scenario* already exits 2, but a typo like
        // `--self-tets roundtrip-nil` used to fall through and launch the GUI,
        // which a verification script cannot distinguish from "the check ran
        // and passed".
        let unknownFamilyFlags = CommandLine.arguments.dropFirst().filter { argument in
            argument.hasPrefix("--self-")
                && argument != AISelfTest.flag
                && argument != StorageSelfCheck.flag
        }
        if !unknownFamilyFlags.isEmpty {
            let names = unknownFamilyFlags.joined(separator: ", ")
            FileHandle.standardError.write(Data("ReadFlow: unknown option \(names)\n".utf8))
            let knownOptions = "known options: \(StorageSelfCheck.flag), "
                + "\(AISelfTest.flag) <scenario>, "
                + "\(AISelfTest.flag) \(SearchQuerySelfTest.scenario) <text>\n"
            FileHandle.standardError.write(Data(knownOptions.utf8))
            exit(2)
        }

        // 设置菜单栏图标
        setupStatusItem()

        // 检查 Accessibility 权限
        checkAccessibilityPermission()

        // 注册全局快捷键（Command + Shift + C）
        registerGlobalHotkey()

        // The stdout line is the launch evidence the frozen contract names in
        // A10, so it stays. It is *also* mirrored to stderr with the resolved
        // bundle identity, because stdout is block-buffered when the app is not
        // attached to a tty: a supervisor (CI, `build_app.sh --smoke`) must be
        // able to grep a marker that is guaranteed to have been flushed.
        print("✅ ReadFlow launched successfully")
        AppDelegate.log(
            "✅ ReadFlow launched successfully "
            + "pid=\(ProcessInfo.processInfo.processIdentifier) "
            + "bundle=\(Bundle.main.bundleIdentifier ?? "<none>") "
            + "executable=\(Bundle.main.executableURL?.lastPathComponent ?? "<none>")"
        )
    }

    /// Unbuffered diagnostic line on stderr.
    static func log(_ message: String) {
        FileHandle.standardError.write(Data("[ReadFlow] \(message)\n".utf8))
    }

    private func setupStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)

        if let button = statusItem?.button {
            // 使用 SF Symbol
            if let image = NSImage(systemSymbolName: "book.fill", accessibilityDescription: "ReadFlow") {
                button.image = image
            }
            button.action = #selector(statusItemClicked)
            button.target = self
        }
    }

    @objc private func statusItemClicked() {
        // 显示菜单
        let menu = NSMenu()

        menu.addItem(NSMenuItem(title: "捕获选中文本", action: #selector(captureSelection), keyEquivalent: "c"))
        menu.addItem(NSMenuItem.separator())
        menu.addItem(NSMenuItem(title: "设置...", action: #selector(openSettings), keyEquivalent: ","))
        menu.addItem(NSMenuItem.separator())
        menu.addItem(NSMenuItem(title: "退出 ReadFlow", action: #selector(quit), keyEquivalent: "q"))

        statusItem?.menu = menu
        statusItem?.button?.performClick(nil)

        // 清除菜单（避免下次点击时重复显示）
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) {
            self.statusItem?.menu = nil
        }
    }

    private func checkAccessibilityPermission() {
        if !selectionService.checkAccessibilityPermission() {
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) {
                self.selectionService.requestAccessibilityPermission()
            }
        }
    }

    private func registerGlobalHotkey() {
        // 注册 Command + Shift + C 快捷键
        // 使用 Carbon Event Manager 或第三方库（如 HotKey）
        // 此处为简化实现，仅通过菜单项触发

        NSEvent.addGlobalMonitorForEvents(matching: .keyDown) { [weak self] event in
            // Command + Shift + C
            if event.modifierFlags.contains([.command, .shift]) && event.keyCode == 8 {
                self?.captureSelection()
            }
        }
    }

    @objc private func captureSelection() {
        if let selection = selectionService.captureSelection() {
            print("✅ Captured: \(selection.text.prefix(50))...")
        } else {
            print("⚠️ No text selected")
        }
    }

    @objc private func openSettings() {
        if let openSettingsWindow {
            openSettingsWindow()
        } else {
            AppDelegate.log("settings window is not ready yet")
        }
    }

    @objc private func quit() {
        NSApplication.shared.terminate(nil)
    }

    func applicationWillTerminate(_ aNotification: Notification) {
        AppDelegate.log("terminated")
    }

    func applicationSupportsSecureRestorableState(_ app: NSApplication) -> Bool {
        return true
    }
}
