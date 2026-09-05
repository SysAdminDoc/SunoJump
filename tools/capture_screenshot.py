#!/usr/bin/env python3
"""Capture a screenshot of the SunoJump main window for README.

Requires a running display. Captures at DPI-aware resolution.
Exits non-zero if the version in the window title doesn't match VERSION.
"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

if sys.platform.startswith("win") and "QT_QPA_FONTDIR" not in os.environ:
    windows_fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    if windows_fonts.is_dir():
        os.environ["QT_QPA_FONTDIR"] = str(windows_fonts)

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
    scene: str = "empty",
) -> int:
    from localization import configure_locale
    from sunojump import VERSION, APP_NAME

    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QPoint, QSettings, Qt, QTimer
    except ImportError:
        print("PyQt6 required for screenshot capture.", file=sys.stderr)
        return 2

    app = QApplication(sys.argv[:1])
    configure_locale("qps-ploc" if pseudo else "en", app)
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
    _prepare_marketing_scene(win, Path(state_dir.name), scene)
    if width is not None or height is not None:
        win.resize(width or win.width(), height or win.height())
    if rtl or pseudo:
        win.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    win._update_responsive_layout(win.width() - 20)
    win.show()
    if keyboard_focus:
        win.btn_browse.setFocus(Qt.FocusReason.TabFocusReason)

    dest = Path(output_path) if output_path else ROOT / "screenshot.png"

    _result = [0]

    def do_capture():
        if scene == "pipeline":
            win.param_scroller.verticalScrollBar().setValue(
                win.param_scroller.verticalScrollBar().maximum()
            )
            app.processEvents()
        elif scene == "evidence":
            win.main_scroller.verticalScrollBar().setValue(
                win.main_scroller.verticalScrollBar().maximum()
            )
            app.processEvents()
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


def _prepare_marketing_scene(win, state_dir: Path, scene: str) -> None:
    if scene == "empty":
        return

    demo_dir = state_dir / "audio"
    demo_dir.mkdir()
    names = (
        "midnight-drive.wav",
        "vocal-sketch.wav",
        "ambient-loop.wav",
    )
    paths = []
    for index, name in enumerate(names, start=1):
        path = demo_dir / name
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(2)
            audio.setsampwidth(2)
            audio.setframerate(48_000)
            audio.writeframes(b"\0\0\0\0" * (2_400 + index * 100))
        paths.append(path)
        win._append_item(str(path))

    assignments = (
        "Gentle",
        "Moderate",
        "Custom snapshot",
    )
    for row, assignment in enumerate(assignments):
        win.file_list.item(row).setText(
            f"READY    {names[row]}  [Preset: {assignment}]"
        )

    win.file_list.setCurrentRow(0)
    win.file_list.item(0).setSelected(True)
    win.preset_combo.setCurrentText("Moderate")
    win.preview_offset_spin.setValue(42.5)
    win.worker_count_spin.setValue(2)
    win.output_dir.setText(r"C:\Audio\SunoJump Renders")
    win.spectrogram_check.setChecked(True)
    win.loudness_report_check.setChecked(True)
    win.signal_report_check.setChecked(True)
    win._update_file_count()
    win._update_preview_ui()
    win._set_queue_notice(
        "3 files ready. Select a track to listen before rendering."
    )
    win.log_box.setPlainText(
        "[14:32:08] Preview rendered from 42.5s with seed 20260905\n"
        "[14:32:11] Moderate comparison ready for listening\n"
        "[14:32:13] Output names reserved without replacing existing files"
    )

    if scene == "evidence":
        win.file_list.item(0).setText(
            "DONE     midnight-drive.wav  | audio + 3 reports"
        )
        win.file_list.item(1).setText(
            "DONE     vocal-sketch.wav  | audio + 3 reports"
        )
        win.file_list.item(2).setText(
            "DONE     ambient-loop.wav  | audio + 3 reports"
        )
        win._set_queue_notice(
            "Batch complete. 3 audio files and 9 report artifacts verified."
        )
        win.log_box.setPlainText(
            "[14:34:21] Batch complete with 3 verified outputs\n"
            "[14:34:21] Audio and sidecar hashes verified before publication\n"
            "[14:34:22] All selected report formats saved\n"
            "[14:34:22] Batch manifest closed with reproducible seeds"
        )
        win.progress.setValue(100)
        win._set_render_state("Complete")


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
    parser.add_argument(
        "--scene",
        choices=("empty", "workspace", "pipeline", "evidence"),
        default="empty",
        help="Populate a deterministic marketing scene before capture.",
    )
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
        scene=options.scene,
    ))
