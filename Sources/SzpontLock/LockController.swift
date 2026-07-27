import AppKit
import ApplicationServices
import AVFoundation
import CoreGraphics
import Foundation

enum LockState {
    case idle
    case armed
    case locked
}

/// The watchdog state machine: idle -> armed -> locked -> idle.
final class LockController {
    /// Keystrokes in the first moments after arming are ignored, so that arming
    /// from the keyboard-navigated menu does not instantly trip the watchdog.
    private let armGrace: TimeInterval = 1.5

    /// Escape hatch: with `SZPONTLOCK_PANIC_TIMEOUT=<seconds>` in the environment,
    /// lockdown releases itself after that many seconds. Off unless set, and always
    /// announced on the lock screen so it can never be silently in effect.
    private let panicTimeout: TimeInterval = {
        guard let raw = ProcessInfo.processInfo.environment["SZPONTLOCK_PANIC_TIMEOUT"],
              let seconds = Double(raw), seconds > 0 else { return 0 }
        return seconds
    }()

    private var panicTimer: Timer?

    private(set) var state: LockState = .idle {
        didSet { onStateChange?(state) }
    }

    var onStateChange: ((LockState) -> Void)?

    /// How long a Touch ID panel may stay up. While it is up the shield has to duck
    /// below `coreautha`, so the prompt is capped rather than left open indefinitely.
    private let biometricPromptLimit: TimeInterval = 25

    private var tap: EventTap!
    private let overlay = LockOverlay()
    private let assertion = DisplayAssertion()
    private let recorder = CameraRecorder()
    private let biometrics = BiometricSession()
    private let matchQueue = DispatchQueue(label: "com.szpont.lock.match")
    private var biometricTimer: Timer?
    private var autoLockTimer: Timer?

    /// `kCGAnyInputEventType` (0xFFFFFFFF) happens to collide with
    /// `.tapDisabledByUserInput`, which is why this initialiser is non-nil.
    private static let anyInputEvent = CGEventType(rawValue: ~0)

    private var armedAt = Date.distantPast
    private var buffer = ""

    /// Set synchronously inside the tap callback the instant the trap is sprung.
    /// `trip()` itself has to be deferred to the next run-loop turn (it shows windows and
    /// starts the camera, far too slow for the callback), and without this flag every
    /// keystroke in that gap would still see `.armed` and be passed through.
    private var isTripping = false

    init() {
        tap = EventTap { [weak self] type, event in
            guard let self else { return event }
            return self.handle(type: type, event: event)
        }
        overlay.state.onTouchID = { [weak self] in self?.requestTouchID() }
        restartAutoLockTimer()
    }

    // MARK: - Auto-lock on inactivity

    /// Called at launch and whenever the interval changes.
    func restartAutoLockTimer() {
        autoLockTimer?.invalidate()
        autoLockTimer = nil
        guard Preferences.autoLockMinutes > 0 else { return }
        autoLockTimer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
            self?.checkInactivity()
        }
    }

    private func checkInactivity() {
        let minutes = Preferences.autoLockMinutes
        guard minutes > 0, state != .locked else { return }
        // Bail out silently on anything that would put a dialog on screen unprompted.
        guard SecretStore.hasSecret, AXIsProcessTrusted() else { return }

        guard let anyInput = Self.anyInputEvent else { return }
        let idle = CGEventSource.secondsSinceLastEventType(.hidSystemState, eventType: anyInput)
        guard idle >= Double(minutes) * 60 else { return }

        SecretStore.log("AUTO-LOCK after \(Int(idle))s idle (threshold \(minutes)m)")
        lockNow()
    }

    // MARK: - Commands

    func arm() {
        guard state == .idle else { return }
        guard ensureAccessibilityPermission() else { return }
        guard SecretStore.hasSecret else {
            presentAlert(
                title: "No unlock sequence set",
                message: "Set an unlock sequence before arming the watchdog, otherwise Touch ID would be the only way back in."
            )
            setSecret()
            return
        }
        primePermissions()

        guard tap.start() else {
            presentAlert(
                title: "Could not capture input",
                message: "SzpontLock failed to install its event tap. Confirm it is enabled under System Settings › Privacy & Security › Accessibility, then try again."
            )
            return
        }

        assertion.acquire(reason: "SzpontLock watchdog armed")
        armedAt = Date()
        state = .armed
        SecretStore.log("ARMED")
    }

    func disarm() {
        guard state == .armed else { return }
        isTripping = false
        tap.stop()
        assertion.release()
        state = .idle
        SecretStore.log("DISARMED")
    }

    /// Manual panic lock - skips the watchdog phase and goes straight to lockdown.
    func lockNow() {
        switch state {
        case .locked:
            return
        case .armed:
            trip(reason: "manual")
        case .idle:
            arm()
            guard state == .armed else { return }
            trip(reason: "manual")
        }
    }

    func setSecret() {
        guard state != .locked else { return }
        guard let secret = SecretPrompt.run() else { return }
        do {
            try SecretStore.setSecret(secret)
            SecretStore.log("SECRET SET (length \(secret.count))")
            presentAlert(title: "Unlock sequence saved", message: "\(secret.count) characters. Only its hash is stored on disk.")
        } catch {
            presentAlert(title: "Could not save", message: error.localizedDescription)
        }
    }

    // MARK: - Event handling

    private func handle(type: CGEventType, event: CGEvent) -> CGEvent? {
        switch state {
        case .idle:
            return event

        case .armed:
            if isTripping { return passesThroughDuringLockdown(type) ? event : nil }
            guard type == .keyDown, Date().timeIntervalSince(armedAt) >= armGrace else { return event }
            isTripping = true
            DispatchQueue.main.async { [weak self] in self?.trip(reason: "keystroke") }
            return nil // the keystroke that trips the trap never reaches anything

        case .locked:
            if type == .keyDown {
                // Read the event here: it is not valid once this callback returns.
                let code = event.keyCode
                let characters = event.typedCharacters
                let flags = event.flags
                DispatchQueue.main.async { [weak self] in
                    self?.consumeKey(code: code, characters: characters, flags: flags)
                }
            }
            return passesThroughDuringLockdown(type) ? event : nil
        }
    }

    /// Left-button mouse events survive lockdown so the Touch ID button stays clickable.
    /// They cannot reach anything else - the shield covers every display.
    private func passesThroughDuringLockdown(_ type: CGEventType) -> Bool {
        switch type {
        case .mouseMoved, .leftMouseDown, .leftMouseUp, .leftMouseDragged:
            return true
        default:
            return false
        }
    }

    private func consumeKey(code: Int64, characters: String, flags: CGEventFlags) {
        guard state == .locked else { return }

        switch code {
        case KeyCode.escape, KeyCode.returnKey, KeyCode.keypadEnter:
            buffer = ""
        case KeyCode.delete:
            buffer = String(buffer.dropLast())
        default:
            guard !flags.contains(.maskCommand), !flags.contains(.maskControl) else { return }
            guard !characters.isEmpty else { return }
            buffer += characters
            if buffer.count > 256 { buffer = String(buffer.suffix(256)) }
        }

        overlay.state.typedCount = buffer.count

        let length = SecretStore.secretLength
        guard length > 0, buffer.count >= length else { return }
        let candidate = String(buffer.suffix(length))
        matchQueue.async {
            guard SecretStore.matches(candidate) else { return }
            DispatchQueue.main.async { [weak self] in self?.unlock(via: "sequence") }
        }
    }

    // MARK: - Transitions

    private func trip(reason: String) {
        guard state == .armed else { return }
        isTripping = true
        state = .locked
        buffer = ""
        assertion.acquire(reason: "SzpontLock locked")
        overlay.show()
        SecretStore.log("TRIPPED (\(reason))")

        overlay.state.message = idleLockMessage
        if panicTimeout > 0 {
            panicTimer = Timer.scheduledTimer(withTimeInterval: panicTimeout, repeats: false) { [weak self] _ in
                self?.unlock(via: "panic timeout")
            }
        }

        let seconds = Int(Preferences.recordingSeconds)
        overlay.state.captureNote = "Recording \(seconds)s…"
        recorder.record(duration: Preferences.recordingSeconds, into: SecretStore.captureDirectory()) { [weak self] outcome in
            guard let self else { return }
            switch outcome {
            case .saved(let url):
                self.overlay.state.captureNote = "Recording saved to \(url.deletingLastPathComponent().lastPathComponent)"
                SecretStore.log("RECORDED \(url.path)")
            case .failed:
                self.overlay.state.captureNote = "Recording failed"
                SecretStore.log("RECORDING FAILED")
            case .discarded:
                SecretStore.log("RECORDING DISCARDED (unlocked before the clip finished)")
            }
        }

        // Offer the fingerprint immediately; the button is there for retries.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
            self?.requestTouchID()
        }
    }

    private func requestTouchID() {
        guard state == .locked, !biometrics.isRunning else { return }
        guard BiometricSession.isAvailable else {
            overlay.state.message = "Touch ID unavailable - type your unlock sequence"
            return
        }

        overlay.state.isAuthenticating = true
        overlay.setBiometricPromptVisible(true)

        biometricTimer = Timer.scheduledTimer(withTimeInterval: biometricPromptLimit, repeats: false) { [weak self] _ in
            self?.biometrics.cancel()
        }

        biometrics.authenticate(reason: "unlock SzpontLock") { [weak self] success, failureMessage in
            guard let self else { return }
            self.endBiometricPrompt()
            if success {
                self.unlock(via: "Touch ID")
            } else if let failureMessage {
                self.overlay.state.message = failureMessage
                SecretStore.log("TOUCH ID FAILED: \(failureMessage)")
            } else {
                self.overlay.state.message = self.idleLockMessage
            }
        }
    }

    /// Restores full shielding and clears prompt bookkeeping.
    private func endBiometricPrompt() {
        biometricTimer?.invalidate()
        biometricTimer = nil
        overlay.state.isAuthenticating = false
        overlay.setBiometricPromptVisible(false)
    }

    private var idleLockMessage: String {
        panicTimeout > 0
            ? "Safety auto-unlock in \(Int(panicTimeout))s - Touch ID or type your sequence"
            : "Touch ID, or type your unlock sequence"
    }

    private func unlock(via method: String) {
        guard state == .locked else { return }
        panicTimer?.invalidate()
        panicTimer = nil
        // The sequence may have been typed while a Touch ID panel was still up.
        biometrics.cancel()
        endBiometricPrompt()
        // Unlocking inside the 5s window means the owner answered their own trap, so the
        // half-finished clip is of them, not an intruder. Drop it.
        recorder.cancel()
        isTripping = false
        buffer = ""
        overlay.hide()
        tap.stop()
        assertion.release()
        state = .idle
        SecretStore.log("UNLOCKED via \(method)")
    }

    // MARK: - Permissions

    /// Shows the system Accessibility prompt (which also registers the app in the
    /// Privacy list) without the extra explanatory alert that `arm()` shows.
    func promptForAccessibilityIfNeeded() {
        guard !AXIsProcessTrusted() else { return }
        let key = kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String
        _ = AXIsProcessTrustedWithOptions([key: true] as CFDictionary)
    }

    @discardableResult
    private func ensureAccessibilityPermission() -> Bool {
        let key = kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String
        let trusted = AXIsProcessTrustedWithOptions([key: true] as CFDictionary)
        if !trusted {
            presentAlert(
                title: "Accessibility permission required",
                message: "SzpontLock needs Accessibility access to watch for and suppress input.\n\nEnable it under System Settings › Privacy & Security › Accessibility, then arm again."
            )
        }
        return trusted
    }

    /// Ask for camera and Desktop access up front, so neither TCC prompt appears
    /// mid-lockdown where the shield would hide it and the tap would swallow the clicks.
    private func primePermissions() {
        if CameraRecorder.authorizationStatus() == .notDetermined {
            CameraRecorder.requestAccess { _ in }
        }
        if let desktop = FileManager.default.urls(for: .desktopDirectory, in: .userDomainMask).first {
            SecretStore.isWritable(desktop)
        }
    }

    private func presentAlert(title: String, message: String) {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message
        alert.alertStyle = .warning
        alert.runModal()
    }
}
