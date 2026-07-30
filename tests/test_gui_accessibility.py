#!/usr/bin/env python3
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QBoxLayout, QListWidgetItem

from batch_manifest import BatchManifestStore
from c2pa_provenance import C2PAInspection, ManifestStoreEvidence
from render_results import (
    BatchResult,
    OutputValidation,
    RenderErrorCode,
    RenderResult,
    RenderState,
)
import sunojump
from sunojump import MainWindow, ROLE_INPUT, ROLE_JOB_ID, ROLE_OUTPUT
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
            self.window.btn_resume_batch,
            self.window.btn_retry_failed,
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

    @staticmethod
    def _present_c2pa():
        return C2PAInspection(
            status="present_unvalidated",
            container="wav",
            manifest_stores=(
                ManifestStoreEvidence(
                    location="riff:C2PA",
                    sha256="a" * 64,
                    size_bytes=128,
                ),
            ),
            message="manifest located; validation not performed",
        )

    def test_c2pa_cancel_preserves_source_and_starts_no_render(self):
        with mock.patch.object(
            sunojump,
            "inspect_c2pa",
            return_value=self._present_c2pa(),
        ), mock.patch.object(
            self.window,
            "_confirm_c2pa_output_without_credentials",
            return_value=False,
        ) as confirm:
            result = self.window._authorize_source_provenance(
                ["signed.wav"],
                self.window._get_params(),
            )

        self.assertIsNone(result)
        confirm.assert_called_once()
        self.assertEqual(
            self.window.render_status_label.text(),
            "Provenance protected",
        )
        self.assertIn(
            "source Content Credentials were preserved",
            self.window.log_box.toPlainText(),
        )

    def test_c2pa_continue_returns_explicit_allow_policy(self):
        with mock.patch.object(
            sunojump,
            "inspect_c2pa",
            return_value=self._present_c2pa(),
        ), mock.patch.object(
            self.window,
            "_confirm_c2pa_output_without_credentials",
            return_value=True,
        ):
            result = self.window._authorize_source_provenance(
                ["signed.wav"],
                self.window._get_params(),
            )

        self.assertEqual(result["c2pa_policy"], "allow-removal")

    def test_c2pa_inspection_failure_is_visible_and_fails_closed(self):
        failed = C2PAInspection(
            status="inspection_failed",
            container="wav",
            message="truncated RIFF chunk header",
        )
        with mock.patch.object(
            sunojump,
            "inspect_c2pa",
            return_value=failed,
        ), mock.patch.object(
            sunojump.QMessageBox,
            "critical",
        ) as critical:
            result = self.window._authorize_source_provenance(
                ["broken.wav"],
                self.window._get_params(),
            )

        self.assertIsNone(result)
        critical.assert_called_once()
        self.assertEqual(
            self.window.render_status_label.text(),
            "Provenance check failed",
        )

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
        item.setData(ROLE_JOB_ID, "job-song")
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
                self.window._on_file_done("job-song", result)
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

    def test_stale_job_result_cannot_attach_to_replacement_row(self):
        original = QListWidgetItem("song.wav")
        original.setData(ROLE_INPUT, "song.wav")
        original.setData(ROLE_JOB_ID, "retired-job")
        self.window.file_list.addItem(original)
        self.window.file_list.takeItem(0)

        replacement = QListWidgetItem("song.wav")
        replacement.setData(ROLE_INPUT, "song.wav")
        replacement.setData(ROLE_OUTPUT, None)
        replacement.setData(ROLE_JOB_ID, "replacement-job")
        self.window.file_list.addItem(replacement)
        stale = RenderResult(
            state=RenderState.SUCCEEDED,
            input_path="song.wav",
            output_path="stale-output.wav",
            validation=self._validated_output(),
        )

        self.window._on_file_done("retired-job", stale)

        self.assertIsNone(replacement.data(ROLE_OUTPUT))
        self.assertTrue(replacement.text().startswith("song.wav"))
        self.assertIn("Ignored stale result", self.window.log_box.toPlainText())

    def test_retry_failed_loads_only_failed_manifest_jobs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            first = temp_path / "first.wav"
            second = temp_path / "second.wav"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            output_dir = temp_path / "output"
            store = BatchManifestStore.create(
                temp_path / "batch.sunojump-batch.json",
                app_version=sunojump.VERSION,
                output_dir=output_dir,
                config=self.window._get_params(),
                jobs=[
                    {
                        "id": "failed-job",
                        "input_path": str(first),
                        "effective_seed": 10,
                    },
                    {
                        "id": "pending-job",
                        "input_path": str(second),
                        "effective_seed": 11,
                    },
                ],
            )
            store.finish_job(
                "failed-job",
                state="failed",
                error_code="decode_failed",
                message="invalid test input",
            )

            with (
                mock.patch.object(
                    sunojump.QFileDialog,
                    "getOpenFileName",
                    return_value=(str(store.path), ""),
                ),
                mock.patch.object(
                    self.window,
                    "_start_gui_batch",
                ) as start_batch,
            ):
                self.window._load_batch_manifest("failed")

            self.assertEqual(self.window.file_list.count(), 1)
            item = self.window.file_list.item(0)
            self.assertEqual(item.data(ROLE_JOB_ID), "failed-job")
            self.assertEqual(item.data(ROLE_INPUT), str(first.resolve()))
            jobs = start_batch.call_args.args[0]
            self.assertEqual([job["id"] for job in jobs], ["failed-job"])
            self.assertEqual(
                start_batch.call_args.args[2],
                str(output_dir.resolve()),
            )

    def test_stale_preview_artifacts_wait_for_worker_exit_before_cleanup(self):
        class FakeWorker:
            def isRunning(self):
                return True

        preview_dir = Path(tempfile.mkdtemp(prefix="sunojump-stale-test-"))
        output_path = preview_dir / "stale.wav"
        sidecar_path = preview_dir / "stale.sidecar.json"
        output_path.write_bytes(b"audio")
        sidecar_path.write_text("{}\n", encoding="utf-8")
        worker = FakeWorker()
        self.window.preview_worker = worker
        self.window._preview_tempdir = str(preview_dir)
        self.window._preview_job_id = "removed-job"
        self.window._preview_run_id = "old-run"
        stale = RenderResult(
            state=RenderState.SUCCEEDED,
            input_path="removed.wav",
            output_path=str(output_path),
            validation=self._validated_output(),
        )

        self.window._on_preview_done("removed-job", "old-run", stale)

        self.assertTrue(output_path.exists())
        self.assertTrue(sidecar_path.exists())
        self.window._on_preview_thread_finished(worker)
        self.assertFalse(output_path.exists())
        self.assertFalse(sidecar_path.exists())

    def test_one_cancel_control_covers_all_render_modes(self):
        class FakeWorker:
            def __init__(self):
                self.cancelled = False

            def isRunning(self):
                return True

            def cancel(self):
                self.cancelled = True

        for attribute, set_running, expected_text in (
            ("worker", self.window._set_processing_ui, "Cancel"),
            (
                "preview_worker",
                self.window._set_preview_running_ui,
                "Cancel",
            ),
            (
                "compare_worker",
                self.window._set_compare_running_ui,
                "Cancel",
            ),
        ):
            with self.subTest(mode=attribute):
                fake = FakeWorker()
                setattr(self.window, attribute, fake)
                set_running(True)
                self.assertTrue(self.window.btn_cancel.isEnabled())
                self.assertEqual(self.window.btn_cancel.text(), expected_text)

                self.window._on_cancel()

                self.assertTrue(fake.cancelled)
                self.assertFalse(self.window.btn_cancel.isEnabled())
                setattr(self.window, attribute, None)
                set_running(False)

    def test_close_stays_open_until_worker_exit_before_temp_cleanup(self):
        class FakeWorker:
            def __init__(self):
                self.running = True
                self.cancelled = False

            def isRunning(self):
                return self.running

            def cancel(self):
                self.cancelled = True

            def wait(self, _milliseconds):
                return not self.running

        class FakeCloseEvent:
            def __init__(self):
                self.accepted = False
                self.ignored = False

            def accept(self):
                self.accepted = True

            def ignore(self):
                self.ignored = True

        preview_dir = Path(tempfile.mkdtemp(prefix="sunojump-close-test-"))
        (preview_dir / "active.tmp").write_text("active", encoding="utf-8")
        fake = FakeWorker()
        self.window.preview_worker = fake
        self.window._preview_tempdir = str(preview_dir)

        first_event = FakeCloseEvent()
        self.window.closeEvent(first_event)

        self.assertTrue(first_event.ignored)
        self.assertFalse(first_event.accepted)
        self.assertTrue(fake.cancelled)
        self.assertTrue(preview_dir.exists())
        self.assertIn("Close paused", self.window.log_box.toPlainText())

        fake.running = False
        second_event = FakeCloseEvent()
        self.window.closeEvent(second_event)

        self.assertTrue(second_event.accepted)
        self.assertFalse(second_event.ignored)
        self.assertFalse(preview_dir.exists())

    def test_tab_order_follows_visual_workflow(self):
        order = self.window._tab_order_widgets

        self.assertEqual(order[0], self.window.btn_browse)
        self.assertEqual(order[1], self.window.btn_remove)
        self.assertEqual(order[2], self.window.btn_clear)
        self.assertEqual(order[3], self.window.btn_resume_batch)
        self.assertEqual(order[4], self.window.btn_retry_failed)
        self.assertEqual(order[5], self.window.file_list)
        self.assertIn(self.window.btn_process, order)
        self.assertLess(order.index(self.window.preset_combo), order.index(self.window.btn_process))
        self.assertLess(order.index(self.window.output_dir), order.index(self.window.btn_process))

    def test_queue_delete_and_alt_arrow_reorder_work_from_keyboard(self):
        for name in ("first.wav", "second.wav", "third.wav"):
            self.window._append_item(name)
        middle = self.window.file_list.item(1)
        middle.setSelected(True)
        self.window.file_list.setCurrentItem(middle)

        QTest.keyClick(
            self.window.file_list,
            Qt.Key.Key_Down,
            Qt.KeyboardModifier.AltModifier,
        )
        self.assertEqual(
            [
                self.window.file_list.item(index).data(ROLE_INPUT)
                for index in range(self.window.file_list.count())
            ],
            ["first.wav", "third.wav", "second.wav"],
        )
        self.assertIn(
            "Moved 1 selected item(s) down",
            self.window.log_box.toPlainText(),
        )

        QTest.keyClick(
            self.window.file_list,
            Qt.Key.Key_Delete,
        )
        self.assertEqual(self.window.file_list.count(), 2)
        self.assertNotIn(
            "second.wav",
            [
                self.window.file_list.item(index).data(ROLE_INPUT)
                for index in range(self.window.file_list.count())
            ],
        )

    def test_primary_actions_expose_window_shortcuts(self):
        shortcuts = {
            self.window.btn_browse: "Ctrl+O",
            self.window.btn_resume_batch: "Ctrl+R",
            self.window.btn_retry_failed: "Ctrl+Shift+R",
            self.window.btn_render_preview: "Ctrl+P",
            self.window.btn_compare: "Ctrl+Shift+P",
            self.window.btn_save_preset: "Ctrl+S",
            self.window.btn_load_preset: "Ctrl+Shift+O",
            self.window.btn_process: "Ctrl+Return",
            self.window.btn_cancel: "Esc",
        }
        for button, expected in shortcuts.items():
            self.assertEqual(
                button.shortcut().toString(),
                expected,
            )
            self.assertIn("Shortcut:", button.toolTip())

    def test_slider_accessibility_announces_displayed_units(self):
        for key, suffix in (
            ("pitch_range", " st"),
            ("tempo_range", "%"),
            ("noise_level", " dB"),
            ("reencode_bitrate", " kbps"),
        ):
            with self.subTest(key=key):
                row = self.window.param_rows[key]
                self.assertIn(
                    row.val_label.text(),
                    row.slider.accessibleName(),
                )
                self.assertIn(suffix, row.slider.accessibleName())
                self.assertIn(
                    row.val_label.text(),
                    row.slider.accessibleDescription(),
                )


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

    def test_minimum_window_size_enables_scaled_desktops(self):
        min_size = self.window.minimumSize()
        self.assertLessEqual(min_size.width(), 560)
        self.assertLessEqual(min_size.height(), 360)
        self.assertIsNotNone(self.window.main_scroller)
        self.assertTrue(self.window.main_scroller.widgetResizable())
        self.assertIsNotNone(self.window.btn_process)
        self.assertIsNotNone(self.window.file_list)
        self.assertIsNotNone(self.window.preset_combo)

    def test_compact_layout_has_no_horizontal_overflow_at_scale_matrix(self):
        original_font = QApplication.font()
        try:
            for scale in (1.0, 1.5, 2.0):
                with self.subTest(scale=scale):
                    font = QFont(original_font)
                    font.setPointSizeF(original_font.pointSizeF() * scale)
                    QApplication.setFont(font)
                    with tempfile.TemporaryDirectory() as temp_dir:
                        settings = QSettings(
                            str(Path(temp_dir) / "session.ini"),
                            QSettings.Format.IniFormat,
                        )
                        window = MainWindow(settings=settings)
                        try:
                            window.setStyleSheet(sunojump.STYLE)
                            window.setLayoutDirection(
                                Qt.LayoutDirection.RightToLeft
                            )
                            long_labels = {
                                window.btn_browse:
                                    "[!! Browse for audio files !!]",
                                window.btn_remove:
                                    "[!! Remove selected audio files !!]",
                                window.btn_resume_batch:
                                    "[!! Resume interrupted batch !!]",
                                window.btn_retry_failed:
                                    "[!! Retry failed batch jobs !!]",
                                window.btn_process:
                                    "[!! Process every queued file !!]",
                            }
                            for widget, text in long_labels.items():
                                widget.setText(text)
                            window.scope_label.setText(
                                "[!! Rights-owned audio material only !!]\n"
                                "[!! Local metrics never predict external "
                                "platform outcomes !!]"
                            )
                            window.resize(700, 520)
                            window.show()
                            QApplication.processEvents()

                            self.assertTrue(window._responsive_compact)
                            self.assertEqual(
                                window._workspace_layout.direction(),
                                QBoxLayout.Direction.TopToBottom,
                            )
                            self.assertEqual(
                                window.main_scroller
                                .horizontalScrollBar()
                                .maximum(),
                                0,
                            )
                            self.assertTrue(
                                all(
                                    row._compact
                                    for row in window.param_rows.values()
                                )
                            )
                            for widget, text in long_labels.items():
                                required = (
                                    max(
                                        widget.fontMetrics()
                                        .horizontalAdvance(line)
                                        for line in widget.text().splitlines()
                                    )
                                    + widget.iconSize().width()
                                    + 48
                                )
                                self.assertEqual(
                                    widget.text().replace("\n", " "),
                                    text,
                                )
                                self.assertGreaterEqual(
                                    widget.width(),
                                    required,
                                    widget.objectName() or text,
                                )
                                self.assertGreaterEqual(
                                    widget.height(),
                                    (
                                        len(widget.text().splitlines())
                                        * widget.fontMetrics().lineSpacing()
                                        + 16
                                    ),
                                    widget.objectName() or text,
                                )
                            queue_buttons = (
                                window.btn_browse,
                                window.btn_remove,
                                window.btn_clear,
                                window.btn_resume_batch,
                                window.btn_retry_failed,
                            )
                            for first, second in zip(
                                queue_buttons,
                                queue_buttons[1:],
                            ):
                                first_bottom = first.mapTo(
                                    window._content_root,
                                    first.rect().bottomLeft(),
                                ).y()
                                second_top = second.mapTo(
                                    window._content_root,
                                    second.rect().topLeft(),
                                ).y()
                                self.assertLess(
                                    first_bottom,
                                    second_top,
                                    f"{first.text()} overlaps "
                                    f"{second.text()}",
                                )
                            window.main_scroller.ensureWidgetVisible(
                                window.btn_process
                            )
                            QApplication.processEvents()
                            viewport = window.main_scroller.viewport()
                            position = window.btn_process.mapTo(
                                viewport,
                                window.btn_process.rect().center(),
                            )
                            self.assertTrue(
                                viewport.rect().contains(position),
                                position,
                            )
                        finally:
                            window.close()
        finally:
            QApplication.setFont(original_font)

    def test_stylesheet_exposes_visible_keyboard_focus(self):
        for selector in (
            "QPushButton:focus",
            "QListWidget:focus",
            "QComboBox:focus",
            "QLineEdit:focus",
            "QCheckBox:focus",
            "QSlider:focus",
            "QTextEdit:focus",
        ):
            self.assertIn(selector, sunojump.STYLE)

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
