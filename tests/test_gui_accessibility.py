#!/usr/bin/env python3
import unittest

from PyQt6.QtWidgets import QApplication

from sunojump import MainWindow


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
