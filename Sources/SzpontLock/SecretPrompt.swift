import AppKit

/// Two-step secure prompt for setting the unlock sequence.
enum SecretPrompt {
    static func run() -> String? {
        guard let first = ask(
            title: "Set unlock sequence",
            message: "Type the sequence that will end lockdown mode.\n\nIt is matched as you type - no Return needed. Avoid ⌘ and ⌃ combinations; those are ignored during lockdown."
        ) else { return nil }

        guard first.count >= 4 else {
            warn(title: "Too short", message: "Use at least 4 characters. Nothing was changed.")
            return nil
        }

        guard let second = ask(title: "Confirm unlock sequence", message: "Type the same sequence again.") else {
            return nil
        }

        guard first == second else {
            warn(title: "Sequences do not match", message: "Nothing was changed.")
            return nil
        }

        return first
    }

    private static func ask(title: String, message: String) -> String? {
        NSApp.activate(ignoringOtherApps: true)

        let field = NSSecureTextField(frame: NSRect(x: 0, y: 0, width: 280, height: 24))
        field.placeholderString = "Unlock sequence"

        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message
        alert.accessoryView = field
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        alert.window.initialFirstResponder = field

        guard alert.runModal() == .alertFirstButtonReturn else { return nil }
        let value = field.stringValue
        return value.isEmpty ? nil : value
    }

    private static func warn(title: String, message: String) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message
        alert.alertStyle = .warning
        alert.runModal()
    }
}
