import AppKit

final class AppDelegate: NSObject, NSApplicationDelegate {
    private let controller = LockController()
    private var statusItemController: StatusItemController?

    func applicationDidFinishLaunching(_ notification: Notification) {
        SecretStore.prepareDirectories()
        statusItemController = StatusItemController(controller: controller)
        let panic = ProcessInfo.processInfo.environment["SZPONTLOCK_PANIC_TIMEOUT"] ?? "off"
        SecretStore.log("LAUNCHED (panic timeout: \(panic), auto-arm: \(Preferences.autoArmLabel(Preferences.autoArmMinutes)))")

        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            if !SecretStore.hasSecret { self.runFirstLaunchSetup() }
            self.controller.promptForAccessibilityIfNeeded()
        }
    }

    private func runFirstLaunchSetup() {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "Welcome to SzpontLock"
        alert.informativeText = """
            SzpontLock lives in the menu bar. Arm it and the screen stays on and the mouse \
            keeps working, but the keyboard quietly becomes a password prompt: type your \
            sequence and the watchdog stands down without ever showing itself. Type \
            anything else and the machine locks, records five seconds of whoever is at the \
            keyboard, and swallows all input until Touch ID or the sequence releases it.

            Set an unlock sequence to get started.
            """
        alert.addButton(withTitle: "Set Unlock Sequence…")
        alert.addButton(withTitle: "Later")
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        controller.setSecret()
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        // Quitting while armed or locked hands the machine back, so refuse. The menu's
        // Quit asks for a fingerprint and disarms first, which is what gets it past here.
        guard controller.state == .idle else { return .terminateCancel }
        return .terminateNow
    }

    func applicationWillTerminate(_ notification: Notification) {
        controller.disarm()
        SecretStore.log("QUIT")
    }
}
