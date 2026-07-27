import AppKit

/// A single white flash across every display.
///
/// This announces that the watchdog armed itself while nobody was at the machine, so that
/// whoever sits down knows their next keystrokes go to a password prompt rather than into
/// whatever app is focused. It is a notification, not a lock: the windows are click-through
/// and the machine stays completely usable underneath.
final class FlashOverlay {
    private static let rampDuration: TimeInterval = 0.08
    private static let fadeDuration: TimeInterval = 0.28

    private var windows: [NSWindow] = []

    func flash() {
        guard windows.isEmpty else { return }   // already flashing

        for screen in NSScreen.screens {
            let window = NSWindow(
                contentRect: screen.frame,
                styleMask: [.borderless],
                backing: .buffered,
                defer: false
            )
            window.level = NSWindow.Level(rawValue: Int(CGShieldingWindowLevel()))
            window.backgroundColor = .white
            window.isOpaque = false
            window.hasShadow = false
            window.alphaValue = 0
            window.ignoresMouseEvents = true
            window.collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary, .ignoresCycle]
            window.setFrame(screen.frame, display: true)
            window.orderFrontRegardless()
            windows.append(window)
        }

        NSAnimationContext.runAnimationGroup({ context in
            context.duration = Self.rampDuration
            for window in windows { window.animator().alphaValue = 1 }
        }, completionHandler: { [weak self] in
            guard let self else { return }
            NSAnimationContext.runAnimationGroup({ context in
                context.duration = Self.fadeDuration
                for window in self.windows { window.animator().alphaValue = 0 }
            }, completionHandler: { [weak self] in
                self?.dismiss()
            })
        })
    }

    private func dismiss() {
        for window in windows { window.orderOut(nil) }
        windows.removeAll()
    }
}
