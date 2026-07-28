"""The tray applet: icon reflects the state, menu drives it — a port of the macOS
``StatusItemController``.

Universal across desktops via Qt6's ``QSystemTrayIcon`` (StatusNotifierItem / XEmbed):
works on XFCE, KDE and GNOME (with an AppIndicator extension). The three state icons
are drawn as monochrome padlock/eye shapes tinted per state, matching the macOS
SF-Symbol look (open padlock / orange eye / red padlock) rather than garish colour
emoji.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QDesktopServices,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import preferences
from .controller import LockState
from .secretstore import SecretStore


def _icon_for(state: LockState) -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    if state == LockState.IDLE:
        color = QColor("#DDDDDD")
    elif state == LockState.ARMED:
        color = QColor("#FF9500")
    else:
        color = QColor("#FF3B30")

    stroke = QPen(color, 6)
    stroke.setCapStyle(Qt.RoundCap)
    stroke.setJoinStyle(Qt.RoundJoin)

    if state == LockState.ARMED:
        # An eye: two almond arcs plus a filled pupil.
        stroke.setWidth(5)
        painter.setPen(stroke)
        painter.setBrush(Qt.NoBrush)
        painter.drawArc(10, 18, 44, 28, 20 * 16, 140 * 16)
        painter.drawArc(10, 46, 44, 28, 200 * 16, 140 * 16)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(26, 26, 12, 12)
    else:
        # A padlock: shackle arc (open and tilted when idle, closed when locked)
        # over a rounded body.
        painter.setPen(stroke)
        painter.setBrush(Qt.NoBrush)
        if state == LockState.IDLE:
            painter.drawArc(12, 8, 26, 30, 60 * 16, 200 * 16)
        else:
            painter.drawArc(18, 8, 28, 32, 0, 180 * 16)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(16, 30, 32, 26, 6, 6)

    painter.end()
    return QIcon(pixmap)


class StatusItem:
    def __init__(self) -> None:
        self._controller = None
        self._tray = QSystemTrayIcon()
        self._menu = QMenu()
        self._build_menu()
        self._tray.setContextMenu(self._menu)
        self._menu.aboutToShow.connect(self._on_menu_open)
        self._tray.setToolTip("SzpontLock")

    def bind(self, controller) -> None:
        self._controller = controller
        self.apply(controller.state)
        self._tray.show()

    def notify(self, title: str, message: str) -> None:
        """A non-blocking tray balloon. Used for messages that fire while the keyboard
        is grabbed (disarm/quit feedback), where a modal QMessageBox would be
        keyboard-dead under the grab and may fail to take focus on X11."""
        self._tray.showMessage(
            title, message, QSystemTrayIcon.Information, 6000
        )

    # -- menu -------------------------------------------------------------

    def _build_menu(self) -> None:
        self._state_action = QAction("Idle")
        self._state_action.setEnabled(False)
        self._menu.addAction(self._state_action)
        self._menu.addSeparator()

        self._arm_action = QAction("Arm Watchdog")
        self._arm_action.triggered.connect(self._toggle_arm)
        self._menu.addAction(self._arm_action)

        self._lock_action = QAction("Lock Now")
        self._lock_action.triggered.connect(self._lock_now)
        self._menu.addAction(self._lock_action)

        self._menu.addSeparator()

        self._auto_arm_menu = self._menu.addMenu("Auto-Arm When Idle")
        self._auto_arm_group = QActionGroup(self._menu)
        self._auto_arm_group.setExclusive(True)
        self._auto_arm_actions = {}
        for minutes in preferences.AUTO_ARM_OPTIONS:
            action = QAction(preferences.auto_arm_label(minutes), self._auto_arm_menu)
            action.setCheckable(True)
            action.triggered.connect(
                lambda _checked=False, m=minutes: self._select_auto_arm(m)
            )
            self._auto_arm_group.addAction(action)
            self._auto_arm_menu.addAction(action)
            self._auto_arm_actions[minutes] = action

        self._secret_action = QAction("Set Unlock Sequence…")
        self._secret_action.triggered.connect(self._set_secret)
        self._menu.addAction(self._secret_action)

        captures_action = QAction("Open Recordings Folder")
        captures_action.triggered.connect(self._open_captures)
        self._menu.addAction(captures_action)

        log_action = QAction("Open Event Log")
        log_action.triggered.connect(self._open_log)
        self._menu.addAction(log_action)

        self._menu.addSeparator()

        quit_action = QAction("Quit SzpontLock")
        quit_action.triggered.connect(self._quit)
        self._menu.addAction(quit_action)

    def apply(self, state: LockState) -> None:
        self._tray.setIcon(_icon_for(state))

        selected = preferences.auto_arm_minutes()
        for minutes, action in self._auto_arm_actions.items():
            action.setChecked(minutes == selected)
        self._auto_arm_menu.setTitle(
            f"Auto-Arm When Idle: {preferences.auto_arm_label(selected)}"
        )

        if state == LockState.IDLE:
            self._state_action.setText("Idle")
            self._arm_action.setText("Arm Watchdog")
            self._arm_action.setEnabled(True)
            self._lock_action.setEnabled(True)
            self._secret_action.setEnabled(True)
        elif state == LockState.ARMED:
            self._state_action.setText("Armed - watching for keystrokes")
            self._arm_action.setText("Disarm Watchdog")
            self._arm_action.setEnabled(True)
            self._lock_action.setEnabled(True)
            self._secret_action.setEnabled(False)
        else:
            self._state_action.setText("LOCKED")
            self._arm_action.setText("Locked")
            self._arm_action.setEnabled(False)
            self._lock_action.setEnabled(False)
            self._secret_action.setEnabled(False)

    def _on_menu_open(self) -> None:
        if self._controller is not None:
            self.apply(self._controller.state)

    # -- actions ----------------------------------------------------------

    def _toggle_arm(self) -> None:
        if self._controller is None:
            return
        state = self._controller.state
        if state == LockState.IDLE:
            self._controller.arm()
        elif state == LockState.ARMED:
            self._controller.request_disarm()  # fingerprint/sequence, not a free click

    def _lock_now(self) -> None:
        if self._controller is not None:
            self._controller.lock_now()

    def _set_secret(self) -> None:
        if self._controller is not None:
            self._controller.set_secret()

    def _select_auto_arm(self, minutes: int) -> None:
        preferences.set_auto_arm_minutes(minutes)
        if self._controller is not None:
            self._controller.restart_auto_arm_timer()
        SecretStore.log(f"AUTO-ARM set to {preferences.auto_arm_label(minutes)}")
        if self._controller is not None:
            self.apply(self._controller.state)

    def _open_captures(self) -> None:
        QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(SecretStore.capture_directory()))
        )

    def _open_log(self) -> None:
        SecretStore.prepare_directories()
        QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(SecretStore.support_directory))
        )

    def _quit(self) -> None:
        if self._controller is None:
            QApplication.quit()
            return

        def granted(ok: bool) -> None:
            if ok:
                QApplication.quit()

        self._controller.request_quit(granted)
