import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from episode_shots import media_key, inspect_asset
from episode_timeline import fingerprint
from episode import verify_media, write, read


class AnalysisTests(unittest.TestCase):
    def test_same_basename_different_source(self):
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory)/"a"/"episode.mp4", Path(directory)/"b"/"episode.mp4"
            for p in (a, b): p.parent.mkdir(); p.write_bytes(b"fixture")
            self.assertNotEqual(fingerprint(media_key(a)), fingerprint(media_key(b)))

    def test_changed_media_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)/"source.mp4"; p.write_bytes(b"first")
            analysis = {"mediaKeys": {str(p): media_key(p)}, "assets": {}}
            p.write_bytes(b"changed source")
            with self.assertRaisesRegex(ValueError, "Source media changed"):
                verify_media(analysis)

    @patch("episode_shots.tool", return_value="ffmpeg")
    @patch("episode_shots.probe", return_value={"duration": "254016000000"})
    @patch("episode_shots.run", return_value=bytes([20, 30, 40, 255])*10)
    def test_opaque_video_rejected(self, *_):
        with self.assertRaisesRegex(ValueError, "transparency"):
            inspect_asset("opaque.mp4", "unused.png")

    def test_atomic_unicode_job_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)/"plan.json"; data = {"name": "Ce ai în plan", "ticks": "9007199254740993"}
            write(p, data); self.assertEqual(read(p), data)
            self.assertFalse(p.with_suffix(".tmp").exists())


if __name__ == "__main__":
    unittest.main()
