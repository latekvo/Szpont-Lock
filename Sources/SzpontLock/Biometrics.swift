import AppKit
import LocalAuthentication

/// Touch ID unlock. Biometrics only - falling back to the account password would
/// let anyone who knows the login password walk straight through the lockdown.
final class BiometricSession {
    private var context: LAContext?

    static var isAvailable: Bool {
        var error: NSError?
        return LAContext().canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: &error)
    }

    var isRunning: Bool { context != nil }

    /// `completion` runs on the main queue: `true` on a successful fingerprint, otherwise
    /// `false` plus a message to show (or `nil` when the user simply cancelled).
    func authenticate(reason: String, completion: @escaping (Bool, String?) -> Void) {
        guard context == nil else { return }

        let context = LAContext()
        context.localizedCancelTitle = "Cancel"
        self.context = context

        var error: NSError?
        guard context.canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: &error) else {
            self.context = nil
            completion(false, error?.localizedDescription ?? "Touch ID unavailable")
            return
        }

        context.evaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, localizedReason: reason) { [weak self] success, evalError in
            DispatchQueue.main.async {
                guard let self, self.context === context else { return }
                self.context = nil
                completion(success, success ? nil : Self.message(for: evalError))
            }
        }
    }

    /// Tears down a prompt that is still on screen - used when the sequence gets typed
    /// while the panel is up, and to cap how long the panel stays open.
    func cancel() {
        context?.invalidate()
        context = nil
    }

    private static func message(for error: Error?) -> String? {
        guard let error = error as? LAError else { return error?.localizedDescription }
        switch error.code {
        case .userCancel, .appCancel, .systemCancel: return nil
        case .authenticationFailed: return "Fingerprint not recognised"
        case .biometryLockout: return "Touch ID locked out - use the sequence"
        default: return error.localizedDescription
        }
    }
}
