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
            self.window.watermark_scan_check,
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

    def test_tab_order_follows_visual_workflow(self):
        order = self.window._tab_order_widgets

        self.assertEqual(order[0], self.window.btn_browse)
        self.assertEqual(order[1], self.window.btn_remove)
        self.assertEqual(order[2], self.window.btn_clear)
        self.assertEqual(order[3], self.window.file_list)
        self.assertIn(self.window.btn_process, order)
        self.assertLess(order.index(self.window.preset_combo), order.index(self.window.btn_process))
        self.assertLess(order.index(self.window.output_dir), order.index(self.window.btn_process))


if __name__ == '__main__':
    unittest.main()
