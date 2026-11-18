// swift-tools-version:6.0
// The generated client for the Monoshiri API (iOS + macOS).
//
// Versions are pinned exactly and recorded in Package.resolved. They are the
// newest releases that build with Swift 6.0 (the toolchain used for the Linux
// check) and were each published more than seven days before being added (the
// repository's supply-chain rule). Dependency updates go through Package.resolved.
import PackageDescription

let package = Package(
    name: "MonoshiriAPI",
    platforms: [
        .iOS(.v17),
        // macOS lets the package compile on Linux CI with the same minimum SDK
        // level as the app it serves.
        .macOS(.v14),
    ],
    products: [
        .library(name: "MonoshiriAPI", targets: ["MonoshiriAPI"])
    ],
    dependencies: [
        .package(
            url: "https://github.com/apple/swift-openapi-generator",
            exact: "1.10.4"  // 2026-01-13
        ),
        .package(
            url: "https://github.com/apple/swift-openapi-runtime",
            exact: "1.10.1"  // 2026-02-24
        ),
        .package(
            url: "https://github.com/apple/swift-openapi-urlsession",
            exact: "1.2.0"  // 2025-09-10
        ),
    ],
    targets: [
        .target(
            name: "MonoshiriAPI",
            dependencies: [
                .product(name: "OpenAPIRuntime", package: "swift-openapi-runtime"),
                .product(name: "OpenAPIURLSession", package: "swift-openapi-urlsession"),
            ],
            plugins: [
                .plugin(
                    name: "OpenAPIGenerator",
                    package: "swift-openapi-generator"
                )
            ]
        )
    ]
)
