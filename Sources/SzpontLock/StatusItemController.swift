import AppKit

/// The menu bar applet: icon reflects the state, menu drives it.
final class StatusItemController: NSObject, NSMenuDelegate {
    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let controller: LockController
    private let menu = NSMenu()

    private let stateItem = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    private let armItem = NSMenuItem(title: "Arm Watchdog", action: nil, keyEquivalent: "")
    private let lockNowItem = NSMenuItem(title: "Lock Now", action: nil, keyEquivalent: "")
    private let secretItem = NSMenuItem(title: "Set Unlock Sequence…", action: nil, keyEquivalent: "")
    private let autoLockItem = NSMenuItem(title: "Auto-Lock When Idle", action: nil, keyEquivalent: "")
    private let autoLockMenu = NSMenu()

    init(controller: LockController) {
        self.controller = controller
        super.init()
        buildMenu()
        statusItem.menu = menu
        menu.delegate = self
        controller.onStateChange = { [weak self] state in self?.apply(state) }
        apply(controller.state)
    }

    private func buildMenu() {
        stateItem.isEnabled = false
        menu.addItem(stateItem)
        menu.addItem(.separator())

        armItem.target = self
        armItem.action = #selector(toggleArm)
        menu.addItem(armItem)

        lockNowItem.target = self
        lockNowItem.action = #selector(lockNow)
        menu.addItem(lockNowItem)

        menu.addItem(.separator())

        for minutes in Preferences.autoLockOptions {
            let item = NSMenuItem(title: Preferences.autoLockLabel(minutes),
                                  action: #selector(selectAutoLock(_:)),
                                  keyEquivalent: "")
            item.target = self
            item.tag = minutes
            autoLockMenu.addItem(item)
        }
        autoLockItem.submenu = autoLockMenu
        menu.addItem(autoLockItem)

        secretItem.target = self
        secretItem.action = #selector(setSecret)
        menu.addItem(secretItem)

        let capturesItem = NSMenuItem(title: "Open Recordings Folder", action: #selector(openCaptures), keyEquivalent: "")
        capturesItem.target = self
        menu.addItem(capturesItem)

        let logItem = NSMenuItem(title: "Open Event Log", action: #selector(openLog), keyEquivalent: "")
        logItem.target = self
        menu.addItem(logItem)

        menu.addItem(.separator())

        let quitItem = NSMenuItem(title: "Quit SzpontLock", action: #selector(quit), keyEquivalent: "q")
        quitItem.target = self
        menu.addItem(quitItem)
    }

    private func apply(_ state: LockState) {
        statusItem.button?.image = Self.icon(for: state)

        let selected = Preferences.autoLockMinutes
        for item in autoLockMenu.items {
            item.state = (item.tag == selected) ? .on : .off
        }
        autoLockItem.title = "Auto-Lock When Idle: \(Preferences.autoLockLabel(selected))"

        switch state {
        case .idle:
            stateItem.title = "Idle"
            armItem.title = "Arm Watchdog"
            armItem.isEnabled = true
            lockNowItem.isEnabled = true
            secretItem.isEnabled = true
        case .armed:
            stateItem.title = "Armed - watching for keystrokes"
            armItem.title = "Disarm Watchdog"
            armItem.isEnabled = true
            lockNowItem.isEnabled = true
            secretItem.isEnabled = false
        case .locked:
            stateItem.title = "LOCKED"
            armItem.title = "Locked"
            armItem.isEnabled = false
            lockNowItem.isEnabled = false
            secretItem.isEnabled = false
        }
    }

    private static func icon(for state: LockState) -> NSImage? {
        let symbol: String
        let tint: NSColor?
        switch state {
        case .idle:
            symbol = "lock.open"
            tint = nil
        case .armed:
            symbol = "eye.fill"
            tint = .systemOrange
        case .locked:
            symbol = "lock.fill"
            tint = .systemRed
        }

        guard let base = NSImage(systemSymbolName: symbol, accessibilityDescription: "SzpontLock \(state)") else {
            return nil
        }
        var configuration = NSImage.SymbolConfiguration(pointSize: 14, weight: .medium)
        if let tint {
            configuration = configuration.applying(NSImage.SymbolConfiguration(paletteColors: [tint]))
        }
        let image = base.withSymbolConfiguration(configuration) ?? base
        image.isTemplate = (tint == nil)
        return image
    }

    // MARK: - Actions

    @objc private func toggleArm() {
        switch controller.state {
        case .idle: controller.arm()
        case .armed: controller.disarm()
        case .locked: break
        }
    }

    @objc private func lockNow() {
        controller.lockNow()
    }

    @objc private func setSecret() {
        controller.setSecret()
    }

    @objc private func selectAutoLock(_ sender: NSMenuItem) {
        Preferences.autoLockMinutes = sender.tag
        controller.restartAutoLockTimer()
        SecretStore.log("AUTO-LOCK set to \(Preferences.autoLockLabel(sender.tag))")
        apply(controller.state)
    }

    @objc private func openCaptures() {
        NSWorkspace.shared.open(SecretStore.captureDirectory())
    }

    @objc private func openLog() {
        SecretStore.prepareDirectories()
        NSWorkspace.shared.open(SecretStore.supportDirectory)
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }

    func menuWillOpen(_ menu: NSMenu) {
        apply(controller.state)
    }
}
