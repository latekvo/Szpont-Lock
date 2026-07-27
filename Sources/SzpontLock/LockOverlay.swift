import AppKit
import SwiftUI

final class OverlayWindow: NSWindow {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }
}

/// Observable state driving the lock screen. Everything here is written from the main queue.
final class LockUIState: ObservableObject {
    @Published var typedCount = 0
    @Published var message = "Touch ID, or type your unlock sequence"
    @Published var isAuthenticating = false
    @Published var photoTaken = false
    @Published var lockedAt = Date()
    var onTouchID: () -> Void = {}
}

/// One opaque, shield-level window per display, rebuilt if displays change.
final class LockOverlay {
    /// Above everything, including the menu bar, the Dock and notification banners.
    static var shieldLevel: NSWindow.Level {
        NSWindow.Level(rawValue: Int(CGShieldingWindowLevel()))
    }

    /// The Touch ID panel is drawn by `coreautha` at level 1000, in its own process, so
    /// no amount of window raising on our side can lift it over the shield. Instead the
    /// shield ducks to just below it while a prompt is up - still over normal windows,
    /// the Dock and the menu bar, but low enough to let the panel through.
    static let biometricPromptLevel = NSWindow.Level(rawValue: 999)

    let state = LockUIState()
    private var windows: [OverlayWindow] = []
    private var observer: NSObjectProtocol?
    private var biometricPromptVisible = false

    var isVisible: Bool { !windows.isEmpty }

    func show() {
        guard windows.isEmpty else { return }
        state.lockedAt = Date()
        state.typedCount = 0
        state.photoTaken = false
        buildWindows()

        observer = NotificationCenter.default.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            self?.rebuildWindows()
        }

        NSApp.activate(ignoringOtherApps: true)
    }

    func hide() {
        if let observer {
            NotificationCenter.default.removeObserver(observer)
            self.observer = nil
        }
        for window in windows { window.orderOut(nil) }
        windows.removeAll()
    }

    /// Ducks below / restores above the Touch ID panel. See `biometricPromptLevel`.
    func setBiometricPromptVisible(_ visible: Bool) {
        biometricPromptVisible = visible
        let level = visible ? Self.biometricPromptLevel : Self.shieldLevel
        for window in windows {
            window.level = level
            window.orderFrontRegardless()
        }
    }

    private func rebuildWindows() {
        for window in windows { window.orderOut(nil) }
        windows.removeAll()
        buildWindows()
    }

    private func buildWindows() {
        for (index, screen) in NSScreen.screens.enumerated() {
            let window = OverlayWindow(
                contentRect: screen.frame,
                styleMask: [.borderless],
                backing: .buffered,
                defer: false
            )
            window.level = biometricPromptVisible ? Self.biometricPromptLevel : Self.shieldLevel
            window.backgroundColor = .black
            window.isOpaque = true
            window.hasShadow = false
            window.ignoresMouseEvents = false
            window.collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary, .ignoresCycle]
            window.setFrame(screen.frame, display: true)

            // Only the primary display gets the interactive panel; the rest are just blackout.
            let root = LockScreenView(state: state, showsPanel: index == 0)
            window.contentView = NSHostingView(rootView: root)
            window.orderFrontRegardless()
            if index == 0 { window.makeKey() }
            windows.append(window)
        }
    }
}

struct LockScreenView: View {
    @ObservedObject var state: LockUIState
    let showsPanel: Bool

    var body: some View {
        ZStack {
            RadialGradient(
                colors: [Color(white: 0.11), .black],
                center: .center,
                startRadius: 40,
                endRadius: 900
            )
            .ignoresSafeArea()

            if showsPanel {
                panel
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var panel: some View {
        VStack(spacing: 22) {
            Image(systemName: "lock.fill")
                .font(.system(size: 54, weight: .light))
                .foregroundStyle(Color(red: 0.95, green: 0.32, blue: 0.32))

            VStack(spacing: 8) {
                Text("LOCKED")
                    .font(.system(size: 30, weight: .semibold, design: .rounded))
                    .tracking(6)
                    .foregroundStyle(.white)

                Text(state.message)
                    .font(.system(size: 14, weight: .regular))
                    .foregroundStyle(Color(white: 0.62))
            }

            keystrokeDots

            Button {
                state.onTouchID()
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: "touchid")
                    Text(state.isAuthenticating ? "Waiting for Touch ID…" : "Unlock with Touch ID")
                }
                .font(.system(size: 13, weight: .medium))
                .padding(.horizontal, 18)
                .padding(.vertical, 9)
                .background(Color(white: 0.16), in: Capsule())
                .foregroundStyle(.white)
            }
            .buttonStyle(.plain)
            .disabled(state.isAuthenticating)
            .opacity(state.isAuthenticating ? 0.55 : 1)

            footer
        }
        .padding(48)
    }

    private var keystrokeDots: some View {
        HStack(spacing: 9) {
            ForEach(0..<12, id: \.self) { index in
                Circle()
                    .fill(index < min(state.typedCount, 12) ? Color.white : Color(white: 0.25))
                    .frame(width: 9, height: 9)
            }
        }
        .frame(height: 12)
        .animation(.easeOut(duration: 0.12), value: state.typedCount)
    }

    private var footer: some View {
        VStack(spacing: 4) {
            Text("Locked at \(state.lockedAt.formatted(date: .omitted, time: .standard))")
            if state.photoTaken {
                Label("Photo captured", systemImage: "camera.fill")
            }
        }
        .font(.system(size: 11))
        .foregroundStyle(Color(white: 0.42))
        .padding(.top, 10)
    }
}
