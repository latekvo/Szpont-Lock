import CryptoKit
import Foundation

/// On-disk state: the salted hash of the unlock sequence, plus where captures go.
enum SecretStore {
    private struct Config: Codable {
        var salt: Data
        var hash: Data
        var length: Int
    }

    static let supportDirectory: URL = {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        return base.appendingPathComponent("SzpontLock", isDirectory: true)
    }()

    static let capturesDirectory = supportDirectory.appendingPathComponent("Captures", isDirectory: true)
    private static let configURL = supportDirectory.appendingPathComponent("config.json")
    private static let logURL = supportDirectory.appendingPathComponent("events.log")

    private static var cached: Config?

    static func prepareDirectories() {
        try? FileManager.default.createDirectory(at: capturesDirectory, withIntermediateDirectories: true)
    }

    private static func load() -> Config? {
        if let cached { return cached }
        guard let data = try? Data(contentsOf: configURL),
              let config = try? JSONDecoder().decode(Config.self, from: data) else { return nil }
        cached = config
        return config
    }

    static var hasSecret: Bool { load() != nil }

    /// Length of the stored sequence, or 0 if none is set.
    static var secretLength: Int { load()?.length ?? 0 }

    static func setSecret(_ secret: String) throws {
        prepareDirectories()
        var salt = Data(count: 32)
        _ = salt.withUnsafeMutableBytes { SecRandomCopyBytes(kSecRandomDefault, 32, $0.baseAddress!) }
        let config = Config(salt: salt, hash: digest(of: secret, salt: salt), length: secret.count)
        try JSONEncoder().encode(config).write(to: configURL, options: [.atomic])
        try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: configURL.path)
        cached = config
    }

    /// Constant-time check of a candidate against the stored sequence.
    ///
    /// Deliberately slow (see `digest`) - never call this from the event-tap
    /// callback, or macOS will disable the tap for being unresponsive.
    static func matches(_ candidate: String) -> Bool {
        guard let config = load(), candidate.count == config.length else { return false }
        let computed = digest(of: candidate, salt: config.salt)
        guard computed.count == config.hash.count else { return false }
        var difference: UInt8 = 0
        for (a, b) in zip(computed, config.hash) { difference |= a ^ b }
        return difference == 0
    }

    private static func digest(of secret: String, salt: Data) -> Data {
        var hasher = SHA256()
        hasher.update(data: salt)
        hasher.update(data: Data(secret.utf8))
        // Stretch a little: a short sequence is guessable, so make each guess cost something.
        var current = Data(hasher.finalize())
        for _ in 0..<100_000 {
            var round = SHA256()
            round.update(data: salt)
            round.update(data: current)
            current = Data(round.finalize())
        }
        return current
    }

    static func log(_ message: String) {
        prepareDirectories()
        let stamp = ISO8601DateFormatter().string(from: Date())
        let line = "[\(stamp)] \(message)\n"
        guard let data = line.data(using: .utf8) else { return }
        if let handle = try? FileHandle(forWritingTo: logURL) {
            defer { try? handle.close() }
            _ = try? handle.seekToEnd()
            try? handle.write(contentsOf: data)
        } else {
            try? data.write(to: logURL)
        }
    }
}
