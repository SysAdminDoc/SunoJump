#!/usr/bin/env python3
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest

import numpy as np
import soundfile as sf

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES_DIR = ROOT / "tests" / "fixtures"
MANIFEST = FIXTURES_DIR / "manifest.json"


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "generate", FIXTURES_DIR / "generate.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FixtureManifestTests(unittest.TestCase):
    def test_manifest_is_valid_json(self):
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertIn("schema_version", data)
        self.assertIn("generated_fixtures", data)
        self.assertIsInstance(data["generated_fixtures"], list)
        self.assertTrue(len(data["generated_fixtures"]) > 0)

    def test_all_fixtures_have_required_fields(self):
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        required = {"name", "sample_rate", "channels", "duration_sec", "generator", "license"}
        for fixture in data["generated_fixtures"]:
            missing = required - set(fixture.keys())
            self.assertFalse(
                missing,
                f"Fixture {fixture.get('name', '?')} missing fields: {missing}",
            )

    def test_generated_fixtures_produce_valid_audio(self):
        gen = _load_generator()
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        for spec in manifest["generated_fixtures"]:
            generator_fn = gen.GENERATORS.get(spec["generator"])
            self.assertIsNotNone(
                generator_fn, f"Unknown generator: {spec['generator']}"
            )
            audio, sr = generator_fn(spec)
            self.assertEqual(sr, spec["sample_rate"])
            expected_samples = int(spec["sample_rate"] * spec["duration_sec"])
            if audio.ndim == 1:
                self.assertEqual(len(audio), expected_samples)
                self.assertEqual(spec["channels"], 1)
            else:
                self.assertEqual(audio.shape[0], expected_samples)
                self.assertEqual(audio.shape[1], spec["channels"])
            self.assertTrue(np.all(np.isfinite(audio)))

    def test_user_corpus_env_documented(self):
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertIn("user_corpus", data)
        self.assertIn("env_var", data["user_corpus"])
        self.assertEqual(data["user_corpus"]["env_var"], "SUNOJUMP_TEST_CORPUS")

    def test_generated_fixtures_round_trip_through_wav(self):
        gen = _load_generator()
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as td:
            for spec in manifest["generated_fixtures"]:
                audio, sr = gen.GENERATORS[spec["generator"]](spec)
                path = pathlib.Path(td) / f"{spec['name']}.wav"
                sf.write(str(path), audio, sr, subtype="PCM_16")
                read_audio, read_sr = sf.read(str(path), dtype="float64")
                self.assertEqual(read_sr, sr)
                self.assertEqual(read_audio.shape[0], audio.shape[0])

    def test_regression_music_corpus_is_redistributable_stereo_44k_and_48k(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        music = [
            fixture
            for fixture in manifest["generated_fixtures"]
            if fixture["generator"] == "music"
        ]

        self.assertEqual({fixture["sample_rate"] for fixture in music}, {44100, 48000})
        self.assertTrue(all(fixture["channels"] == 2 for fixture in music))
        self.assertTrue(all(fixture["license"] == "CC0-1.0" for fixture in music))
        self.assertTrue(all(type(fixture["regression_seed"]) is int for fixture in music))


if __name__ == "__main__":
    unittest.main()
