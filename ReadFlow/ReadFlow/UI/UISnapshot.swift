//
//  UISnapshot.swift
//  ReadFlow
//
//  `--render-ui <dir>` — renders the app's SwiftUI surfaces to PNG files.
//
//  Why it exists: this project could verify behaviour exhaustively but had never
//  *seen* its own interface. Screenshots of the running app come back solid black
//  and the accessibility API returns -25211 in this environment, so every claim
//  about the UI was a claim about derived strings rather than about pixels.
//
//  `ImageRenderer` rasterises a SwiftUI view without a window, a display or a
//  logged-in session, which is exactly what is missing here. The output is a
//  real rendering of the real views, so a designer (or a reviewer) can look at
//  the interface and iterate — and the images become evidence that a human can
//  check, instead of a string that only its author can interpret.
//
//  It is a *snapshot*, not a visual regression test: nothing here asserts what
//  the pixels should be. It exists so that a person can see them.
//
//  Exit: 0 when every surface rendered, 1 when any render produced no image.
//

import AppKit
import SwiftUI

enum UISnapshot {

    static let flag = "--render-ui"

    /// One surface: a name, how big the window would be, and the view itself.
    private struct Surface {
        let name: String
        let width: CGFloat
        let height: CGFloat
        let make: () -> AnyView
    }

    @MainActor
    static func run(outputDir: String) -> Int32 {
        let directory = URL(fileURLWithPath: outputDir, isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        } catch {
            log("render-ui: FAIL — cannot create \(directory.path): \(error)")
            return 1
        }

        // A private, seeded store, so the search surface has something to show and
        // the run cannot touch the user's own library.
        let workDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("readflow-ui-snapshot-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: workDir) }
        var database: ReaderDatabase?
        var seededHighlight: UUID?
        var seededSource: UUID?
        do {
            try FileManager.default.createDirectory(at: workDir, withIntermediateDirectories: true)
            let seeded = try seed(at: workDir.appendingPathComponent("readflow.sqlite"))
            database = seeded.database
            seededHighlight = seeded.highlightID
            seededSource = seeded.sourceID
        } catch {
            log("render-ui: note — seeding failed (\(error)); the search surface will render empty")
        }

        let appState = AppState()
        appState.isConnected = true
        appState.connectionStatus = "已连接"
        appState.localBookURL = "http://127.0.0.1:3780"
        appState.lastSyncTime = "今天 12:40"
        appState.pendingSyncCount = 3

        var surfaces: [Surface] = [
            Surface(name: "01-search-empty", width: 560, height: 440) {
                AnyView(SearchWindowView(database: database))
            },
            Surface(name: "02-search-results", width: 560, height: 440) {
                AnyView(SearchWindowView(database: database, initialQuery: "练习"))
            },
            Surface(name: "03-search-no-match", width: 560, height: 440) {
                AnyView(SearchWindowView(database: database, initialQuery: "不存在的词组zzz"))
            },
            Surface(name: "04-settings", width: 640, height: 460) {
                AnyView(SettingsView().environmentObject(appState))
            },
            Surface(name: "05-ai-provider", width: 640, height: 520) {
                AnyView(AIProviderSettingsPane())
            },
            Surface(name: "05b-localbook", width: 640, height: 560) {
                AnyView(LocalBookSettingsPane())
            },
            Surface(name: "06-menu-bar", width: 340, height: 430) {
                AnyView(MenuBarView().environmentObject(appState))
            },
        ]
        // Only rendered when the private store exists: falling back to the shared
        // store would make a read-only snapshot write into the user's library.
        if let database, let seededHighlight, let seededSource {
            // Real ids from the seeded store: the popover opens a conversation
            // against the highlight it was invoked for, and a fabricated id would
            // only exercise the foreign-key failure path.
            surfaces.append(Surface(name: "07-popover", width: 400, height: 560) {
                AnyView(AIPopoverView(highlightID: seededHighlight,
                                      sourceID: seededSource,
                                      context: "深度学习需要长期刻意练习才能形成能力",
                                      storage: ConversationStorage(database: database)))
            })
        }

        var rendered = 0
        for surface in surfaces {
            let view = surface.make()
                .frame(width: surface.width, height: surface.height)
                .background(Color(nsColor: .windowBackgroundColor))

            guard let image = rasterize(view, width: surface.width, height: surface.height) else {
                log("render-ui: FAIL — \(surface.name) produced no image "
                    + "(\(Int(surface.width))×\(Int(surface.height)))")
                continue
            }
            let url = directory.appendingPathComponent("\(surface.name).png")
            guard writePNG(image, to: url) else {
                log("render-ui: FAIL — could not write \(url.path)")
                continue
            }
            rendered += 1
            log("render-ui: OK   — \(surface.name)  \(image.width)×\(image.height)  \(url.lastPathComponent)")
        }

        log("render-ui: \(rendered)/\(surfaces.count) surfaces rendered into \(directory.path)")
        return rendered == surfaces.count ? 0 : 1
    }

    // MARK: - Helpers

    @MainActor
    private static func seed(at url: URL) throws -> (database: ReaderDatabase,
                                                     highlightID: UUID,
                                                     sourceID: UUID) {
        let database = try ReaderDatabase(path: url.path)

        let web = Source(type: .web, title: "设计参考 · 排版与节奏", url: "https://example.com/typography")
        let book = Source(type: .pdf, title: "深度工作 · 注意力与练习", url: nil)
        try database.createSource(web)
        try database.createSource(book)

        let first = Highlight(
            sourceID: web.id,
            selectedText: "深度学习需要长期刻意练习才能形成能力",
            contextBefore: "注意力的稀缺性决定了",
            contextAfter: "，而不是靠一次性的冲刺。"
        )
        try database.createHighlight(first)
        try database.createHighlight(Highlight(
            sourceID: book.id,
            selectedText: "阅读是一种能力，但需要练习",
            contextBefore: "",
            contextAfter: ""
        ))
        try database.createHighlight(Highlight(
            sourceID: web.id,
            selectedText: "行高与字距决定了长文的可读性，留白不是浪费",
            contextBefore: "在版式设计中，",
            contextAfter: "。"
        ))
        return (database, first.id, web.id)
    }

    /// Rasterises a SwiftUI view through AppKit, offscreen.
    ///
    /// `ImageRenderer` was tried first and rejected: it draws SwiftUI's own
    /// primitives but renders controls as placeholders (the search field came
    /// back as a yellow "unsupported" bar) and skips lazily-laid-out content such
    /// as `List`/`ScrollView` rows entirely — so the first snapshot showed a
    /// window frame with nothing in it. An `NSHostingView` performs a real
    /// AppKit layout and draw pass, which is what actually renders the interface.
    @MainActor
    private static func rasterize<V: View>(_ view: V, width: CGFloat, height: CGFloat) -> CGImage? {
        let hosting = NSHostingView(rootView: view)
        hosting.frame = CGRect(x: 0, y: 0, width: width, height: height)

        // An offscreen window gives the hierarchy a real window to lay out in —
        // orderFront would flash a window at the user, so it is never ordered in.
        let window = NSWindow(contentRect: hosting.frame,
                              styleMask: [.borderless],
                              backing: .buffered,
                              defer: false)
        window.contentView = hosting
        window.appearance = NSAppearance(named: .aqua)      // deterministic: light
        window.layoutIfNeeded()
        hosting.layoutSubtreeIfNeeded()

        // Let SwiftUI settle its published state before the draw pass.
        RunLoop.current.run(until: Date().addingTimeInterval(0.25))
        hosting.layoutSubtreeIfNeeded()

        guard let rep = hosting.bitmapImageRepForCachingDisplay(in: hosting.bounds) else { return nil }
        hosting.cacheDisplay(in: hosting.bounds, to: rep)
        return rep.cgImage
    }

    private static func writePNG(_ image: CGImage, to url: URL) -> Bool {
        let rep = NSBitmapImageRep(cgImage: image)
        guard let data = rep.representation(using: .png, properties: [:]) else { return false }
        do {
            try data.write(to: url)
            return true
        } catch {
            return false
        }
    }

    private static func log(_ message: String) {
        FileHandle.standardError.write(Data("[ReadFlow] \(message)\n".utf8))
    }
}
