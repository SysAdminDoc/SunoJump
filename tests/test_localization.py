#!/usr/bin/env python3
from pathlib import Path
import tempfile
import unittest

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtWidgets import QApplication

from localization import (
    active_layout_direction,
    active_locale,
    configure_locale,
    requested_locale_from_argv,
    tr,
    without_locale_args,
)
from sunojump import MainWindow, _build_cli_parser


class LocalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        configure_locale("en", self.app)

    def test_locale_fallback_and_pluralization_keep_english_unchanged(self):
        translator = configure_locale("en-US", self.app)

        self.assertEqual(translator.resolved_locale, "en")
        self.assertEqual(tr("Process All"), "Process All")
        self.assertEqual(tr("{n} file", n=1), "1 file")
        self.assertEqual(tr("{n} file", n=2), "2 files")

    def test_locale_argument_helpers_preserve_qt_arguments(self):
        tokens = ["app", "--locale", "qps-ploc", "-platform", "offscreen"]

        self.assertEqual(requested_locale_from_argv(tokens), "qps-ploc")
        self.assertEqual(
            without_locale_args(tokens),
            ["app", "-platform", "offscreen"],
        )

    def test_pseudo_locale_translates_gui_status_error_and_cli_help(self):
        configure_locale("qps-ploc", self.app)

        self.assertEqual(active_locale(), "qps-ploc")
        self.assertEqual(
            active_layout_direction(),
            Qt.LayoutDirection.RightToLeft,
        )
        self.assertTrue(tr("Process All").startswith("⟦!!"))
        self.assertIn("2", tr("{n} file", n=2))
        self.assertTrue(
            tr("No supported audio files found.").startswith("⟦!!")
        )
        help_text = _build_cli_parser().format_help()
        self.assertIn("--locale LOCALE", help_text)
        self.assertIn("⟦!!", help_text)
        self.assertIn("--preset-file", help_text)
        self.assertIn("--profile", help_text)
        self.assertIn("--result-format", help_text)
        self.assertIn("--spectrogram", help_text)

    def test_pseudo_rtl_compact_gui_has_no_horizontal_clipping(self):
        configure_locale("qps-ploc", self.app)
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = QSettings(
                str(Path(temp_dir) / "session.ini"),
                QSettings.Format.IniFormat,
            )
            window = MainWindow(settings=settings)
            try:
                window.resize(700, 520)
                window.show()
                self.app.processEvents()
                window._set_render_state("Failed")
                window._update_file_count()
                self.app.processEvents()

                self.assertEqual(
                    window.layoutDirection(),
                    Qt.LayoutDirection.RightToLeft,
                )
                for widget in (
                    window.btn_browse,
                    window.btn_resume_batch,
                    window.btn_process,
                    window.render_status_label,
                    window.queue_activity_label,
                ):
                    self.assertTrue(widget.text().startswith("⟦!!"), widget)
                self.assertEqual(
                    window.main_scroller.horizontalScrollBar().maximum(),
                    0,
                )
                for button in (
                    window.btn_browse,
                    window.btn_remove,
                    window.btn_resume_batch,
                    window.btn_retry_failed,
                    window.btn_process,
                ):
                    required = (
                        max(
                            button.fontMetrics().horizontalAdvance(line)
                            for line in button.text().splitlines()
                        )
                        + button.iconSize().width()
                        + 48
                    )
                    self.assertGreaterEqual(button.width(), required, button.text())
            finally:
                window.close()


if __name__ == "__main__":
    unittest.main()
