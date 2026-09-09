// swift-tools-version: 6.0
import PackageDescription

// No dependencies. The helper plays one fixed file; anything it links is
// surface it does not need.
let package = Package(
    name: "JarvisAckHelper",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(name: "JarvisAckHelper", path: "Sources/JarvisAckHelper")
    ]
)
