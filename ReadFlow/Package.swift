// swift-tools-version: 5.9

import PackageDescription

let package = Package(
    name: "ReadFlow",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "ReadFlow", targets: ["ReadFlow"]),
    ],
    dependencies: [
        .package(url: "https://github.com/groue/GRDB.swift.git", from: "6.24.0"),
        .package(url: "https://github.com/Alamofire/Alamofire.git", from: "5.8.0"),
    ],
    targets: [
        .executableTarget(
            name: "ReadFlow",
            dependencies: [
                .product(name: "GRDB", package: "GRDB.swift"),
                .product(name: "Alamofire", package: "Alamofire"),
            ],
            path: "ReadFlow",
            exclude: ["Resources"]
        ),
        // The target is intentionally small and Foundation-free because the
        // Command Line Tools' `_Testing_Foundation` module is incomplete. The
        // older XCTest-based `Tests/ReaderDatabaseTests.swift` remains an orphan
        // outside all target paths; CLI self-tests cover persistence assertions.
        .testTarget(
            name: "ReadFlowTests",
            dependencies: ["ReadFlow"],
            path: "ReadFlowTests"
        ),
    ]
)
