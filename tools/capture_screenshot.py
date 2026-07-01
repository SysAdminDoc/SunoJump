#!/usr/bin/env python3
"""Capture a screenshot of the SunoJump main window for README.

Requires a running display. Captures at DPI-aware resolution.
Exits non-zero if the version in the window title doesn't match VERSION.
"""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

try:
    ctypes.windll.user32.SetProcessDPIAware()
except (AttributeError, OSError):
    pass

sys.path.insert(0, str(ROOT))


def capture(output_path: str | None = None) -> int:
    from sunojump import VERSION, APP_NAME

    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QTimer
    except ImportError:
        print("PyQt6 required for screenshot capture.", file=sys.stderr)
        return 2

    app = QApplication(sys.argv[:1])

    from sunojump import MainWindow, STYLE
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    win = MainWindow()
    win.show()

    dest = Path(output_path) if output_path else ROOT / "screenshot.png"

    _result = [0]

    def do_capture():
        screen = win.screen()
        if screen is None:
            print("No screen available.", file=sys.stderr)
            _result[0] = 1
            app.quit()
            return
        title = win.windowTitle()
        expected = f"{APP_NAME} v{VERSION}"
        if expected not in title:
            print(
                f"Version mismatch: window title '{title}' "
                f"does not contain '{expected}'",
                file=sys.stderr,
            )
            _result[0] = 1
            app.quit()
            return
        pixmap = screen.grabWindow(int(win.winId()))
        pixmap.save(str(dest), "PNG")
        print(f"Screenshot saved: {dest} ({pixmap.width()}x{pixmap.height()})")
        app.quit()

    QTimer.singleShot(500, do_capture)
    app.exec()
    return _result[0]


if __name__ == "__main__":
    output = sys.argv[1] if len(sys.argv) > 1 else None
    raise SystemExit(capture(output))
