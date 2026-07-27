import Foundation

enum Preferences {
    private static let autoArmKey = "autoArmMinutes"

    /// Minutes of system-wide input inactivity before the watchdog arms itself.
    /// Zero disables it. Defaults to 5.
    static var autoArmMinutes: Int {
        get {
            guard UserDefaults.standard.object(forKey: autoArmKey) != nil else { return 5 }
            return UserDefaults.standard.integer(forKey: autoArmKey)
        }
        set { UserDefaults.standard.set(newValue, forKey: autoArmKey) }
    }

    /// Offered in the menu bar; 0 is "Off".
    static let autoArmOptions = [0, 1, 5, 10, 15, 30]

    static func autoArmLabel(_ minutes: Int) -> String {
        minutes == 0 ? "Off" : "\(minutes) min"
    }

    /// Length of the clip recorded when the watchdog trips.
    static let recordingSeconds: TimeInterval = 5
}
