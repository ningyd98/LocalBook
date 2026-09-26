//
//  ReadFlowTests.swift
//  ReadFlowTests
//
//  Created on 2024-09-12.
//
//  Rewritten 2026-09-13 from XCTest to swift-testing.
//
//  Why: Command Line Tools ship **no XCTest module** (`import XCTest` →
//  `no such module 'XCTest'`), so this target was unbuildable and only added
//  noise to every `swift build --build-tests` / `swift test`. It is *not* the
//  primary verification route — that is the `--self-test` CLI, which needs no
//  framework — but an unbuildable target is worse than none, so it now uses the
//  framework this toolchain does provide.
//
//  HARD CONSTRAINT for this file: **no Foundation types, no `import Foundation`.**
//  `_Testing_Foundation.framework` ships without a usable `Modules/` (dangling
//  symlink), so a test file that mentions `UUID`, `Data`, `JSONEncoder` … fails
//  with `no such module '_Testing_Foundation'`. Anything needing Foundation is
//  built by a helper **inside the module** (`SelfTestFixtures`) or asserted
//  through the CLI outlets — the assertions below only pass `String` in.
//
//  Measured under CLT only (see docs/standalone-build.md §4.6 for the flags'
//  roles: `-plugin-path` is required by bare `swiftc`, supplied automatically by
//  SwiftPM; `-F` + the link-time `-rpath` are required in both cases):
//
//      FW=/Library/Developer/CommandLineTools/Library/Developer/Frameworks
//      swift test --disable-sandbox \
//        -Xswiftc -F -Xswiftc "$FW" \
//        -Xlinker -F -Xlinker "$FW" -Xlinker -rpath -Xlinker "$FW"
//

import Testing
@testable import ReadFlow

@Test("the CJK index transform is one shared SQL function")
func ftsTransformFunctionIsShared() {
    // Triggers and queries must call the same function or indexing and searching
    // can drift apart. Plain `String` comparison — no Foundation.
    #expect(ReaderDatabase.ftsTransformFunction == "readflow_fts_transform")
}

@Test("the content gate treats empty, blank and format scalars as absent")
func contentGateRejectsInvisibleStrings() {
    // The nil-vs-placeholder distinction rests on this (C26/C27).
    #expect(Citation.hasContent("真标题"))
    #expect(!Citation.hasContent(""))
    #expect(!Citation.hasContent(" \t\n"))
    #expect(!Citation.hasContent("\u{2060}"))   // word joiner — survives trimming
    #expect(!Citation.hasContent("\u{FEFF}"))   // BOM
}

@Test("a display title is never empty, for any input")
func displayTitleIsNeverEmpty() {
    let nilTitle = SelfTestFixtures.nilValuedCitation()
    let blankTitle = SelfTestFixtures.blankTitleCitation()
    #expect(!nilTitle.displayTitle.isEmpty)
    #expect(!blankTitle.displayTitle.isEmpty)
    #expect(nilTitle.displayTitle == blankTitle.displayTitle)
}

@Test("the round-trip detector flags the shape it is meant to catch")
func detectorHasTeeth() {
    // Same predicate the CLI outlets use. If this ever stops flagging the
    // poisoned citation, the A30 evidence chain is vacuous — and this test says
    // so rather than staying green.
    #expect(SentinelGuardSelfTest.violations(in: [SelfTestFixtures.nilValuedCitation()]).isEmpty)
    #expect(!SentinelGuardSelfTest.violations(in: [SelfTestFixtures.poisonedCitation()]).isEmpty)
}

@Test("AI self-tests default to an isolated shared database")
func aiSelfTestsDoNotOpenTheUserStore() {
    #expect(ReaderDatabase.shouldUseInMemorySharedStore(
        arguments: ["ReadFlow", "--self-test", "success"],
        databasePathOverride: nil
    ))
    #expect(!ReaderDatabase.shouldUseInMemorySharedStore(
        arguments: ["ReadFlow", "--self-test", "success"],
        databasePathOverride: "/tmp/readflow-isolated.sqlite"
    ))
    #expect(!ReaderDatabase.shouldUseInMemorySharedStore(
        arguments: ["ReadFlow"],
        databasePathOverride: nil
    ))
}
