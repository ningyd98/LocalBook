//
//  SelfTestFixtures.swift
//  ReadFlow
//
//  Citation fixtures built **inside the module**, for callers that cannot use
//  Foundation themselves.
//
//  Why this exists: `ReadFlowTests` runs on swift-testing, and the Command Line
//  Tools' `_Testing_Foundation.framework` has no usable `Modules/` — so a test
//  file that mentions `UUID` (or any other Foundation type) fails to compile
//  with `no such module '_Testing_Foundation'`. Constructing a `Citation`
//  requires a `UUID`, so the construction lives here, where Foundation is
//  available, and the test file only ever handles the resulting values.
//
//  Scope note: this is a fixture builder, not production behaviour — nothing in
//  the app calls it. The primary verification route remains the `--self-test`
//  CLI (see `AISelfTest`, `SentinelGuardSelfTest`, `DatabaseInitSelfTest`), which
//  needs no test framework at all.
//

import Foundation

enum SelfTestFixtures {

    /// The good case: a citation whose optional fields are genuinely absent.
    static func nilValuedCitation() -> Citation {
        Citation(sourceID: UUID(), title: nil, snippet: nil, url: nil)
    }

    /// A title that is blank rather than nil — must render like the nil case.
    static func blankTitleCitation() -> Citation {
        Citation(sourceID: UUID(), title: "   ", snippet: nil, url: nil)
    }

    /// The failure mode A30 exists to catch: a placeholder that got persisted
    /// into `title`/`snippet` instead of staying nil. The detector must flag it.
    static func poisonedCitation() -> Citation {
        Citation(sourceID: UUID(), title: "无标题来源", snippet: "", url: nil)
    }
}
