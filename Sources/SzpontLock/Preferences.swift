import Foundation

enum Preferences {
    private static let autoLockKey = "autoLockMinutes"

    /// Minutes of system-wide input inactivity before the machine locks itself.
    /// Zero disables it. Defaults to 5.
    static var autoLockMinutes: Int {
        get {
            guard UserDefaults.standard.object(forKey: autoLockKey) != nil else { return 5 }
            return UserDefaults.standard.integer(forKey: autoLockKey)
        }
        set { UserDefaults.standard.set(newValue, forKey: autoLockKey) }
    }

    /// Offered in the menu bar; 0 is "Off".
    static let autoLockOptions = [0, 1, 5, 10, 15, 30]

    static func autoLockLabel(_ minutes: Int) -> String {
        minutes == 0 ? "Off" : "\(minutes) min"
    }

    /// Length of the clip recorded when the watchdog trips.
    static let recordingSeconds: TimeInterval = 5
}
