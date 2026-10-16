import CoreGraphics
import Foundation

/// Reports the macOS screen lock being lifted, but only after a lock that really happened.
/// Any process can post the distributed notifications, so neither is taken at its word: the
/// session dictionary, which WindowServer owns, has to agree with both.
final class SystemScreenLock {
    private let onUnlock: () -> Void
    private var sawLock = false
    private var observers: [NSObjectProtocol] = []

    init(onUnlock: @escaping () -> Void) {
        self.onUnlock = onUnlock
        let center = DistributedNotificationCenter.default()
        observers = [
            center.addObserver(forName: .init("com.apple.screenIsLocked"), object: nil, queue: .main) { [weak self] _ in
                self?.whenSession(isLocked: true) { [weak self] in self?.sawLock = true }
            },
            center.addObserver(forName: .init("com.apple.screenIsUnlocked"), object: nil, queue: .main) { [weak self] _ in
                guard let self, self.sawLock else { return }
                self.whenSession(isLocked: false) { [weak self] in
                    guard let self, self.sawLock else { return }
                    self.sawLock = false
                    self.onUnlock()
                }
            },
        ]
    }

    deinit {
        observers.forEach(DistributedNotificationCenter.default().removeObserver)
    }

    static var isLocked: Bool {
        let session = CGSessionCopyCurrentDictionary() as? [String: Any]
        return session?["CGSSessionScreenIsLocked"] as? Bool ?? false
    }

    /// The notifications can arrive a few milliseconds before WindowServer updates the
    /// session dictionary, so it gets two seconds to agree before one is written off.
    private func whenSession(isLocked expected: Bool, retries: Int = 20, then action: @escaping () -> Void) {
        if Self.isLocked == expected {
            action()
        } else if retries > 0 {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) { [weak self] in
                self?.whenSession(isLocked: expected, retries: retries - 1, then: action)
            }
        }
    }
}
