// swift-tools-version:5.10
import PackageDescription

let package = Package(
    name: "Offload",
    platforms: [.macOS(.v14)],
    targets: [
        .target(name: "OffloadCore"),
        .executableTarget(name: "Offload", dependencies: ["OffloadCore"]),
        // Self-check instead of a test target: XCTest and swift-testing both need Xcode.
        .executableTarget(name: "OffloadSelfTest", dependencies: ["OffloadCore"]),
    ]
)
