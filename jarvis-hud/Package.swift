// swift-tools-version: 6.0
import PackageDescription

// No external dependencies, by design. The HUD draws four fields of JSON;
// anything it pulls in is surface it does not need.
let package = Package(
    name: "JarvisHUD",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "JarvisHUD",
            path: "Sources/JarvisHUD"
        ),
        .testTarget(
            name: "JarvisHUDTests",
            dependencies: ["JarvisHUD"],
            path: "Tests/JarvisHUDTests"
        ),
    ]
)
