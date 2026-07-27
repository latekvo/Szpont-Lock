import CoreGraphics
import Foundation

/// A session-wide CGEventTap that can suppress events.
///
/// The tap is created with a full event mask so a single tap can serve both the
/// "watch for the first keystroke" phase and the "swallow everything" phase; the
/// handler decides per event. Requires Accessibility permission.
final class EventTap {
    /// Return `nil` to swallow the event, or the (possibly modified) event to pass it on.
    typealias Handler = (CGEventType, CGEvent) -> CGEvent?

    private var port: CFMachPort?
    private var source: CFRunLoopSource?
    private let handler: Handler

    init(handler: @escaping Handler) {
        self.handler = handler
    }

    var isRunning: Bool { port != nil }

    @discardableResult
    func start() -> Bool {
        guard port == nil else { return true }

        let callback: CGEventTapCallBack = { _, type, event, refcon in
            guard let refcon else { return Unmanaged.passUnretained(event) }
            let tap = Unmanaged<EventTap>.fromOpaque(refcon).takeUnretainedValue()
            return tap.process(type: type, event: event)
        }

        guard let newPort = CGEvent.tapCreate(
            tap: .cgSessionEventTap,
            place: .headInsertEventTap,
            options: .defaultTap,
            eventsOfInterest: ~CGEventMask(0),
            callback: callback,
            userInfo: Unmanaged.passUnretained(self).toOpaque()
        ) else {
            return false
        }

        let newSource = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, newPort, 0)
        CFRunLoopAddSource(CFRunLoopGetMain(), newSource, .commonModes)
        // Modal panels (the Touch ID prompt among them) spin a run loop mode that is not
        // part of commonModes; without this the tap would stop suppressing input there.
        CFRunLoopAddSource(CFRunLoopGetMain(), newSource, CFRunLoopMode("NSModalPanelRunLoopMode" as CFString))
        CGEvent.tapEnable(tap: newPort, enable: true)

        port = newPort
        source = newSource
        return true
    }

    func stop() {
        if let port {
            CGEvent.tapEnable(tap: port, enable: false)
            CFMachPortInvalidate(port)
        }
        if let source {
            CFRunLoopRemoveSource(CFRunLoopGetMain(), source, .commonModes)
            CFRunLoopRemoveSource(CFRunLoopGetMain(), source, CFRunLoopMode("NSModalPanelRunLoopMode" as CFString))
        }
        port = nil
        source = nil
    }

    private func process(type: CGEventType, event: CGEvent) -> Unmanaged<CGEvent>? {
        // macOS disables a tap whose callback is too slow, or on certain user input.
        // Re-arming here is what keeps the lockdown from silently falling open.
        if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
            if let port { CGEvent.tapEnable(tap: port, enable: true) }
            return nil
        }
        guard let result = handler(type, event) else { return nil }
        return Unmanaged.passUnretained(result)
    }
}

extension CGEvent {
    /// The characters this key event would produce under the current layout.
    var typedCharacters: String {
        var length = 0
        var buffer = [UniChar](repeating: 0, count: 8)
        keyboardGetUnicodeString(maxStringLength: 8, actualStringLength: &length, unicodeString: &buffer)
        guard length > 0 else { return "" }
        return String(utf16CodeUnits: buffer, count: length)
    }

    var keyCode: Int64 { getIntegerValueField(.keyboardEventKeycode) }
}

enum KeyCode {
    static let returnKey: Int64 = 36
    static let keypadEnter: Int64 = 76
    static let delete: Int64 = 51
    static let escape: Int64 = 53
}
