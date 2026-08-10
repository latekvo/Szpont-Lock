"""Entry point: an offscreen render (no display needed) or the Qt6/X11 tray applet.

    python -m szpontlock_app                          # launch the tray applet
    SZPONTLOCK_RENDER=lock  python -m szpontlock_app   # render the lock screen to PNG
    SZPONTLOCK_RENDER=icons python -m szpontlock_app   # render the three tray icons
    SZPONTLOCK_RENDER_OUT=/path.png  ...               # override the output path
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    what = os.environ.get("SZPONTLOCK_RENDER")
    if what:
        from .render import run

        out = os.environ.get("SZPONTLOCK_RENDER_OUT", f"/tmp/szpontlock-{what}.png")
        return run(what, out)

    from .app import run_app

    return run_app()


if __name__ == "__main__":
    sys.exit(main())
