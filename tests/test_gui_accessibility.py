#!/usr/bin/env python3
import unittest

from PyQt6.QtWidgets import QApplication, QListWidgetItem

from render_results import (
    BatchResult,
    OutputValidation,
    RenderErrorCode,
    RenderResult,
    RenderState,
)
from sunojump import MainWindow, ROLE_INPUT, ROLE_OUTPUT
import verifiers


class GuiAccessibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()

    def tearDown(self):
        self.window.close()

    def _assert_accessible(self, widget):
        self.assertTrue(widget.accessibleName(), widget)
        self.assertTrue(widget.accessibleDescription(), widget)

    def test_primary_controls_have_accessible_names_and_descriptions(self):
        controls = [
            self.window.file_list,
            self.window.btn_browse,
            self.window.btn_remove,
            self.window.btn_clear,
            self.window.btn_render_preview,
            self.window.btn_compare,
            self.window.btn_play_orig,
            self.window.btn_play_proc,
            self.window.btn_open_log,
            self.window.preset_combo,
            self.window.btn_save_preset,
            self.window.btn_load_preset,
            self.window.spectral_scan_check,
            self.window.meta_check,
            self.window.format_combo,
            self.window.output_dir,
            self.window.btn_browse_output,
            self.window.btn_process,
            self.window.btn_cancel,
        ]

        for widget in controls:
            self._assert_accessible(widget)

        for row in self.window.param_rows.values():
            self._assert_accessible(row.check)
            self._assert_accessible(row.slider)

    def test_visible_scope_disclaims_platform_outcomes(self):
        text = self.window.scope_label.text()
        self.assertIn("Rights-owned audio only", text)
        self.assertIn("do not predict platform outcomes", text)
        self.assertIn("do not predict or guarantee", self.window.scope_label.toolTip())

    def test_verifier_state_renders_verbatim_in_gui_session_log(self):
        result = verifiers.VerifierResult(
            adapter="sunojump.constellation",
            adapter_version="1",
            metric="landmark_overlap",
            state=verifiers.VerifierState.UNAVAILABLE,
            reason="input_too_short",
            coverage={"compared_seconds": 1.0},
        )
        rendered = verifiers.format_verifier_result(result)
        self.window._log(rendered)
        self.assertIn(rendered, self.window.log_box.toPlainText())

    @staticmethod
    def _validated_output():
        return OutputValidation(
            input_sha256="a" * 64,
            output_sha256="b" * 64,
            output_bytes=100,
            sample_rate_hz=8000,
            channels=1,
            frames=8000,
            duration_seconds=1.0,
            peak=0.25,
            hashes_distinct=True,
            decoder="soundfile",
        )

    def test_file_rows_render_typed_terminal_states(self):
        item = QListWidgetItem("song.wav")
        item.setData(ROLE_INPUT, "song.wav")
        self.window.file_list.addItem(item)
        cases = (
            (
                RenderResult(
                    state=RenderState.SUCCEEDED,
                    input_path="song.wav",
                    output_path="song_sj.wav",
                    validation=self._validated_output(),
                    effective_seed=42,
                ),
                "DONE",
                "song_sj.wav",
            ),
            (
                RenderResult(
                    state=RenderState.PARTIAL,
                    input_path="song.wav",
                    output_path="song_sj.wav",
                    error_code=RenderErrorCode.SIDECAR_WRITE_FAILED,
                    validation=self._validated_output(),
                ),
                "PARTIAL",
                "song_sj.wav",
            ),
            (
                RenderResult(
                    state=RenderState.FAILED,
                    input_path="song.wav",
                    error_code=RenderErrorCode.DECODE_FAILED,
                ),
                "FAILED",
                None,
            ),
            (
                RenderResult(
                    state=RenderState.CANCELLED,
                    input_path="song.wav",
                    error_code=RenderErrorCode.CANCELLED,
                ),
                "CANCELLED",
                None,
            ),
        )
        for result, label, output in cases:
            with self.subTest(state=result.state):
                self.window._on_file_done(0, result)
                self.assertTrue(item.text().startswith(label), item.text())
                self.assertEqual(item.data(ROLE_OUTPUT), output)
                self.assertIn(result.state.value, item.toolTip())
                if result.effective_seed is not None:
                    self.assertIn("seed:42", item.toolTip())

    def test_batch_failure_and_cancellation_never_show_complete_or_100(self):
        failed = RenderResult(
            state=RenderState.FAILED,
            input_path="bad.wav",
            error_code=RenderErrorCode.DECODE_FAILED,
        )
        cancelled = RenderResult(
            state=RenderState.CANCELLED,
            input_path="later.wav",
            error_code=RenderErrorCode.CANCELLED,
        )
        for result, expected_label in (
            (BatchResult.from_results([failed], 1.0), "Failed"),
            (BatchResult.from_results([cancelled], 1.0), "Cancelled"),
        ):
            with self.subTest(state=result.state):
                self.window.progress.setValue(73)
                self.window._on_all_done(result)
                self.assertEqual(
                    self.window.render_status_label.text(),
                    expected_label,
                )
                self.assertLess(self.window.progress.value(), 100)
                self.assertNotEqual(
                    self.window.render_status_label.text(),
                    "Complete",
                )

    def test_only_succeeded_batch_sets_complete_and_100(self):
        succeeded = RenderResult(
            state=RenderState.SUCCEEDED,
            input_path="song.wav",
            output_path="song_sj.wav",
            validation=self._validated_output(),
        )
        self.window.progress.setValue(99)
        self.window._on_all_done(BatchResult.from_results([succeeded], 1.0))
        self.assertEqual(self.window.render_status_label.text(), "Complete")
        self.assertEqual(self.window.progress.value(), 100)

    def test_tab_order_follows_visual_workflow(self):
        order = self.window._tab_order_widgets

        self.assertEqual(order[0], self.window.btn_browse)
        self.assertEqual(order[1], self.window.btn_remove)
        self.assertEqual(order[2], self.window.btn_clear)
        self.assertEqual(order[3], self.window.file_list)
        self.assertIn(self.window.btn_process, order)
        self.assertLess(order.index(self.window.preset_combo), order.index(self.window.btn_process))
        self.assertLess(order.index(self.window.output_dir), order.index(self.window.btn_process))


class VisualAccessibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()

    def tearDown(self):
        self.window.close()

    @staticmethod
    def _relative_luminance(hex_color):
        hex_color = hex_color.lstrip('#')
        r, g, b = (int(hex_color[i:i+2], 16) / 255.0 for i in (0, 2, 4))
        def linearize(c):
            return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
        return 0.2126 * linearize(r) + 0.7152 * linearize(g) + 0.0722 * linearize(b)

    @staticmethod
    def _contrast_ratio(l1, l2):
        lighter = max(l1, l2)
        darker = min(l1, l2)
        return (lighter + 0.05) / (darker + 0.05)

    def test_primary_text_on_base_meets_wcag_aa(self):
        from sunojump import C
        bg_lum = self._relative_luminance(C['base'])
        text_lum = self._relative_luminance(C['text'])
        ratio = self._contrast_ratio(text_lum, bg_lum)
        self.assertGreaterEqual(ratio, 4.5, f"text/base contrast {ratio:.1f} < 4.5")

    def test_subtext_on_panel_meets_wcag_aa_large(self):
        from sunojump import C
        bg_lum = self._relative_luminance(C['panel'])
        sub_lum = self._relative_luminance(C['subtext'])
        ratio = self._contrast_ratio(sub_lum, bg_lum)
        self.assertGreaterEqual(ratio, 3.0, f"subtext/panel contrast {ratio:.1f} < 3.0")

    def test_accent_on_base_meets_wcag_aa_large(self):
        from sunojump import C
        bg_lum = self._relative_luminance(C['base'])
        accent_lum = self._relative_luminance(C['accent'])
        ratio = self._contrast_ratio(accent_lum, bg_lum)
        self.assertGreaterEqual(ratio, 3.0, f"accent/base contrast {ratio:.1f} < 3.0")

    def test_minimum_window_size_allows_reasonable_layout(self):
        min_size = self.window.minimumSize()
        self.assertGreaterEqual(min_size.width(), 1060)
        self.assertGreaterEqual(min_size.height(), 780)
        self.assertIsNotNone(self.window.btn_process)
        self.assertIsNotNone(self.window.file_list)
        self.assertIsNotNone(self.window.preset_combo)

    def test_disabled_controls_not_hidden(self):
        self.window.btn_cancel.setEnabled(False)
        self.assertFalse(self.window.btn_cancel.isHidden())
        self.assertFalse(self.window.btn_cancel.isEnabled())
        self.window.btn_open_log.setEnabled(False)
        self.assertFalse(self.window.btn_open_log.isHidden())

    def test_clear_logs_button_has_accessibility(self):
        self.assertTrue(self.window.btn_clear_logs.accessibleName())
        self.assertTrue(self.window.btn_clear_logs.accessibleDescription())


if __name__ == '__main__':
    unittest.main()
