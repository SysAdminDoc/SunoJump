#!/usr/bin/env python3
"""Capture a screenshot of the SunoJump main window for README.

Requires a running display. Captures at DPI-aware resolution.
Exits non-zero if the version in the window title doesn't match VERSION.
"""
from __future__ import annotations

import argparse
import ctypes
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

try:
    ctypes.windll.user32.SetProcessDPIAware()
except (AttributeError, OSError):
    pass

sys.path.insert(0, str(ROOT))


def capture(
    output_path: str | None = None,
    *,
    width: int | None = None,
    height: int | None = None,
    font_scale: float = 1.0,
    rtl: bool = False,
    pseudo: bool = False,
    keyboard_focus: bool = False,
) -> int:
    from sunojump import VERSION, APP_NAME

    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QPoint, QSettings, Qt, QTimer
    except ImportError:
        print("PyQt6 required for screenshot capture.", file=sys.stderr)
        return 2

    app = QApplication(sys.argv[:1])
    if font_scale != 1.0:
        font = app.font()
        if font.pointSizeF() > 0:
            font.setPointSizeF(font.pointSizeF() * font_scale)
        elif font.pixelSize() > 0:
            font.setPixelSize(round(font.pixelSize() * font_scale))
        app.setFont(font)

    from sunojump import MainWindow, STYLE
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    state_dir = tempfile.TemporaryDirectory(
        prefix="sunojump-screenshot-state-"
    )
    settings = QSettings(
        str(Path(state_dir.name) / "session.ini"),
        QSettings.Format.IniFormat,
    )
    win = MainWindow(settings=settings)
    if width is not None or height is not None:
        win.resize(width or win.width(), height or win.height())
    if rtl:
        win.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    if pseudo:
        expansions = {
            win.btn_browse: "[!! Browse for audio files !!]",
            win.btn_remove: "[!! Remove selected audio files !!]",
            win.btn_resume_batch: "[!! Resume interrupted batch !!]",
            win.btn_retry_failed: "[!! Retry failed batch jobs !!]",
            win.btn_process: "[!! Process every queued file !!]",
        }
        for widget, text in expansions.items():
            widget.setText(text)
        win.scope_label.setText(
            "[!! Rights-owned audio material only !!]\n"
            "[!! Local metrics never predict external platform outcomes !!]"
        )
    win._update_responsive_layout(win.width() - 20)
    win.show()
    if keyboard_focus:
        win.btn_browse.setFocus(Qt.FocusReason.TabFocusReason)

    dest = Path(output_path) if output_path else ROOT / "screenshot.png"

    _result = [0]

    def do_capture():
        if keyboard_focus:
            button_position = win.btn_browse.mapTo(
                win._content_root,
                QPoint(0, 0),
            )
            win.main_scroller.verticalScrollBar().setValue(
                max(0, button_position.y() - 120)
            )
            app.processEvents()
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
    state_dir.cleanup()
    return _result[0]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Capture a verified SunoJump GUI screenshot.",
    )
    parser.add_argument("output", nargs="?")
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--font-scale", type=float, default=1.0)
    parser.add_argument("--rtl", action="store_true")
    parser.add_argument("--pseudo", action="store_true")
    parser.add_argument("--focus", action="store_true")
    options = parser.parse_args()
    if options.font_scale <= 0:
        parser.error("--font-scale must be greater than zero")
    raise SystemExit(capture(
        options.output,
        width=options.width,
        height=options.height,
        font_scale=options.font_scale,
        rtl=options.rtl,
        pseudo=options.pseudo,
        keyboard_focus=options.focus,
    ))
