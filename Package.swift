// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "SzpontLock",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "SzpontLock",
            path: "Sources/SzpontLock",
            linkerSettings: [
                .linkedFramework("AppKit"),
                .linkedFramework("AVFoundation"),
                .linkedFramework("LocalAuthentication"),
                .linkedFramework("IOKit"),
            ]
        )
    ]
)
