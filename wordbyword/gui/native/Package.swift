// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "WordByWord",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "WordByWord", targets: ["WordByWord"])],
    targets: [
        .executableTarget(name: "WordByWord"),
        .testTarget(name: "WordByWordTests", dependencies: ["WordByWord"])
    ],
    swiftLanguageModes: [.v5]
)
