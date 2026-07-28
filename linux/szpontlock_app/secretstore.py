"""On-disk state: the salted hash of the unlock sequence, plus where captures go.

A faithful port of the macOS ``SecretStore``. The hashing scheme is identical byte
for byte — salt (32 random bytes), then ``SHA256(salt + secret)`` stretched through
100_000 rounds of ``SHA256(salt + current)`` — so a ``config.json`` written here has
the same shape the macOS build writes (``salt``/``hash`` base64, ``length`` int).
For any ordinary (ASCII / NFC single-code-point) sequence a secret set on one
platform verifies on the other; the one edge is ``length``, which counts Unicode
code points here versus Swift grapheme clusters, so a sequence built from combining
characters could store a different ``length`` across platforms.

Storage lives under ``$XDG_DATA_HOME/SzpontLock`` (``~/.local/share/SzpontLock``),
the Linux analogue of ``~/Library/Application Support/SzpontLock``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Number of stretch rounds. Matches the macOS build exactly (~50 ms per attempt);
# never run ``matches`` on a latency-sensitive thread (see the grab pump).
_STRETCH_ROUNDS = 100_000


def _data_home() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )
    return Path(base)


class SecretStore:
    """Namespaced like the macOS ``enum SecretStore`` — a bag of module functions
    with a small in-process cache of the parsed config."""

    support_directory: Path = _data_home() / "SzpontLock"
    _fallback_captures: Path = support_directory / "Captures"
    _config_url: Path = support_directory / "config.json"
    _log_url: Path = support_directory / "events.log"

    _cached: Optional[dict] = None

    # -- directories ------------------------------------------------------

    @classmethod
    def prepare_directories(cls) -> None:
        cls.support_directory.mkdir(parents=True, exist_ok=True)

    @classmethod
    def _desktop_dir(cls) -> Optional[Path]:
        """The user's Desktop, honouring an xdg-user-dirs override, else ~/Desktop."""
        try:
            out = subprocess.run(
                ["xdg-user-dir", "DESKTOP"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if out.returncode == 0:
                p = out.stdout.strip()
                if p:
                    return Path(p)
        except (OSError, subprocess.SubprocessError):
            pass
        home = Path(os.path.expanduser("~"))
        cand = home / "Desktop"
        return cand if cand.is_dir() else None

    @classmethod
    def capture_directory(cls) -> Path:
        """Recordings go to the Desktop so an intrusion is impossible to miss, with a
        fallback to Application Support when the Desktop is missing or unwritable —
        evidence in a less obvious place beats no evidence at all."""
        desktop = cls._desktop_dir()
        if desktop is not None and cls.is_writable(desktop):
            return desktop
        cls._fallback_captures.mkdir(parents=True, exist_ok=True)
        return cls._fallback_captures

    @staticmethod
    def is_writable(directory: Path) -> bool:
        probe = directory / ".szpontlock-write-probe"
        try:
            probe.write_bytes(b"")
            try:
                probe.unlink()
            except OSError:
                pass
            return True
        except OSError:
            return False

    # -- config -----------------------------------------------------------

    @classmethod
    def _load(cls) -> Optional[dict]:
        if cls._cached is not None:
            return cls._cached
        try:
            raw = cls._config_url.read_bytes()
            obj = json.loads(raw)
            # Decode base64 Data fields into bytes, matching Swift's JSONEncoder.
            config = {
                "salt": base64.b64decode(obj["salt"]),
                "hash": base64.b64decode(obj["hash"]),
                "length": int(obj["length"]),
            }
        except (OSError, ValueError, KeyError):
            return None
        cls._cached = config
        return config

    @classmethod
    def has_secret(cls) -> bool:
        return cls._load() is not None

    @classmethod
    def secret_length(cls) -> int:
        config = cls._load()
        return config["length"] if config else 0

    @classmethod
    def set_secret(cls, secret: str) -> None:
        cls.prepare_directories()
        salt = secrets.token_bytes(32)
        config = {
            "salt": salt,
            "hash": cls._digest(secret, salt),
            "length": len(secret),
        }
        payload = json.dumps(
            {
                "salt": base64.b64encode(config["salt"]).decode("ascii"),
                "hash": base64.b64encode(config["hash"]).decode("ascii"),
                "length": config["length"],
            }
        ).encode("utf-8")
        # Write atomically, then clamp to 0600 — the salt+hash are all that guards
        # the sequence, so they are owner-only just like the macOS build.
        tmp = cls._config_url.with_suffix(".json.tmp")
        tmp.write_bytes(payload)
        os.chmod(tmp, 0o600)
        os.replace(tmp, cls._config_url)
        cls._cached = config

    @classmethod
    def matches(cls, candidate: str) -> bool:
        """Constant-time check of a candidate against the stored sequence.

        Deliberately slow (see ``_digest``) — never call this from the keyboard-grab
        pump, or a keystroke would block on ~50 ms of hashing."""
        config = cls._load()
        if config is None or len(candidate) != config["length"]:
            return False
        computed = cls._digest(candidate, config["salt"])
        return hmac.compare_digest(computed, config["hash"])

    @staticmethod
    def _digest(secret: str, salt: bytes) -> bytes:
        current = hashlib.sha256(salt + secret.encode("utf-8")).digest()
        for _ in range(_STRETCH_ROUNDS):
            current = hashlib.sha256(salt + current).digest()
        return current

    # -- event log --------------------------------------------------------

    @classmethod
    def log(cls, message: str) -> None:
        cls.prepare_directories()
        # Match the macOS ISO8601DateFormatter output (trailing "Z", not "+00:00").
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = f"[{stamp}] {message}\n"
        try:
            with cls._log_url.open("a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError:
            pass

    # -- test hook --------------------------------------------------------

    @classmethod
    def _reset_for_testing(cls, base: Path) -> None:
        """Point the store at a scratch directory and clear the cache (tests only)."""
        cls.support_directory = base
        cls._fallback_captures = base / "Captures"
        cls._config_url = base / "config.json"
        cls._log_url = base / "events.log"
        cls._cached = None
