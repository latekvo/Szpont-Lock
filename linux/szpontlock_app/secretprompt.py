"""Two-step secure prompt for setting the unlock sequence — the macOS
``SecretPrompt``. Only ever runs while idle (arming needs a secret and the menu
item is disabled once armed), so the keyboard is not grabbed and the dialog gets
input normally.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QInputDialog, QLineEdit, QMessageBox, QWidget


class SecretPrompt:
    @staticmethod
    def run(parent: Optional[QWidget] = None) -> Optional[str]:
        first = SecretPrompt._ask(
            parent,
            "Set unlock sequence",
            "Type the sequence that will end lockdown mode.\n\n"
            "It is matched as you type - no Return needed. Avoid Control and Super "
            "combinations; those are ignored during lockdown.",
        )
        if first is None:
            return None
        if len(first) < 4:
            SecretPrompt._warn(
                parent, "Too short", "Use at least 4 characters. Nothing was changed."
            )
            return None

        second = SecretPrompt._ask(
            parent, "Confirm unlock sequence", "Type the same sequence again."
        )
        if second is None:
            return None
        if first != second:
            SecretPrompt._warn(
                parent, "Sequences do not match", "Nothing was changed."
            )
            return None
        return first

    @staticmethod
    def _ask(parent: Optional[QWidget], title: str, message: str) -> Optional[str]:
        text, ok = QInputDialog.getText(
            parent, title, message, QLineEdit.Password
        )
        if not ok:
            return None
        return text if text else None

    @staticmethod
    def _warn(parent: Optional[QWidget], title: str, message: str) -> None:
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(title)
        box.setText(title)
        box.setInformativeText(message)
        box.exec()
