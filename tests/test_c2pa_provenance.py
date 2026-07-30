#!/usr/bin/env python3
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest

from mutagen.id3 import GEOB, ID3

from c2pa_provenance import inspect_c2pa


MANIFEST = b"\x00\x00\x00\x18jumbsynthetic-c2pa"


def _riff_chunk(chunk_id, data):
    padding = b"\x00" if len(data) & 1 else b""
    return chunk_id + struct.pack("<I", len(data)) + data + padding


def _write_riff(path, *chunks):
    body = b"WAVE" + b"".join(chunks)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def _aiff_chunk(chunk_id, data):
    padding = b"\x00" if len(data) & 1 else b""
    return chunk_id + struct.pack(">I", len(data)) + data + padding


def _ogg_page(payload, *, serial=7, header_type=2, sequence=0):
    segments = []
    remaining = len(payload)
    while remaining >= 255:
        segments.append(255)
        remaining -= 255
    segments.append(remaining)
    header = (
        b"OggS"
        + bytes([0, header_type])
        + struct.pack("<QII", 0, serial, sequence)
        + b"\x00\x00\x00\x00"
        + bytes([len(segments)])
        + bytes(segments)
    )
    return header + payload


class C2PAProvenanceTests(unittest.TestCase):
    def test_riff_c2pa_chunk_is_hashed_without_reading_audio_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "signed.wav"
            _write_riff(
                path,
                _riff_chunk(b"fmt ", b"\x01\x00" + b"\x00" * 14),
                _riff_chunk(b"data", b"\x00" * 32),
                _riff_chunk(b"C2PA", MANIFEST),
            )

            inspection = inspect_c2pa(path)

        self.assertTrue(inspection.present)
        self.assertEqual(inspection.container, "wav")
        self.assertEqual(
            inspection.manifest_stores[0].sha256,
            hashlib.sha256(MANIFEST).hexdigest(),
        )
        self.assertEqual(
            inspection.manifest_stores[0].location,
            "riff:C2PA",
        )
        self.assertEqual(
            inspection.to_dict()["validation"],
            "not_performed",
        )

    def test_plain_riff_reports_absent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "plain.wav"
            _write_riff(path, _riff_chunk(b"data", b"\x00" * 8))

            inspection = inspect_c2pa(path)

        self.assertEqual(inspection.status, "absent")
        self.assertFalse(inspection.manifest_stores)

    def test_truncated_riff_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "truncated.wav"
            path.write_bytes(
                b"RIFF"
                + struct.pack("<I", 1000)
                + b"WAVE"
                + b"C2PA"
                + struct.pack("<I", 100)
                + b"short"
            )

            inspection = inspect_c2pa(path)

        self.assertTrue(inspection.failed)
        self.assertIn("exceeds source size", inspection.message)

    def test_aiff_c2pa_chunk_is_detected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "signed.aiff"
            body = b"AIFF" + _aiff_chunk(b"C2PA", MANIFEST)
            path.write_bytes(
                b"FORM" + struct.pack(">I", len(body)) + body
            )

            inspection = inspect_c2pa(path)

        self.assertTrue(inspection.present)
        self.assertEqual(inspection.container, "aiff")
        self.assertEqual(
            inspection.manifest_stores[0].location,
            "aiff:C2PA",
        )

    def test_id3_geob_accepts_current_and_legacy_media_types(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            for index, media_type in enumerate((
                "application/c2pa",
                "application/x-c2pa-manifest-store",
            )):
                with self.subTest(media_type=media_type):
                    path = Path(temp_dir) / f"signed-{index}.mp3"
                    tags = ID3()
                    tags.add(GEOB(
                        encoding=3,
                        mime=media_type,
                        filename="manifest.c2pa",
                        desc="Content Credentials",
                        data=MANIFEST,
                    ))
                    tags.save(path)

                    inspection = inspect_c2pa(path)

                    self.assertTrue(inspection.present)
                    self.assertIn(
                        media_type,
                        inspection.manifest_stores[0].location,
                    )

    def test_ogg_c2pa_logical_stream_is_detected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "signed.ogg"
            c2pa_stream = _ogg_page(
                b"\x00c2pa" + MANIFEST,
                serial=10,
            )
            audio_bos = _ogg_page(
                b"\x01vorbis",
                serial=20,
            )
            audio_data = _ogg_page(
                b"audio",
                serial=20,
                header_type=0,
                sequence=1,
            )
            path.write_bytes(c2pa_stream + audio_bos + audio_data)

            inspection = inspect_c2pa(path)

        self.assertTrue(inspection.present)
        self.assertEqual(inspection.container, "ogg")
        self.assertEqual(
            inspection.manifest_stores[0].location,
            "ogg:stream-10",
        )
        self.assertEqual(
            inspection.manifest_stores[0].sha256,
            hashlib.sha256(MANIFEST).hexdigest(),
        )

    def test_adjacent_manifest_store_is_recorded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "source.flac"
            path.write_bytes(b"fLaC")
            path.with_suffix(".c2pa").write_bytes(MANIFEST)

            inspection = inspect_c2pa(path)

        self.assertTrue(inspection.present)
        self.assertEqual(
            inspection.manifest_stores[0].location,
            "adjacent:.c2pa",
        )


if __name__ == "__main__":
    unittest.main()
