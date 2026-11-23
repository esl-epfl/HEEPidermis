"""Exercise the actual subprocess boundary without playing audible audio."""

import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from gui.tests.common.audio import _write_wav
from gui.tests.common.media import MediaPlayer


class MediaTests(unittest.TestCase):
    def test_stopping_reaps_the_player(self):
        player = MediaPlayer()
        self.addCleanup(player.close)
        self.assertTrue(player._launch([sys.executable, "-c", "import time; time.sleep(30)"]))
        process = player.process
        player.stop()
        self.assertIsNotNone(process.poll())
        self.assertFalse(player.playing)

    @unittest.skipUnless(shutil.which("ffplay"), "ffplay is optional")
    def test_local_clip_plays_with_installed_ffplay(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, SDL_AUDIODRIVER="dummy"):
            path = Path(directory) / "silence.wav"
            _write_wav(path, [0.0] * 2205)
            player = MediaPlayer()
            try:
                self.assertTrue(player.play(path))
                self.assertEqual(player.process.wait(timeout=10), 0)
                self.assertFalse(player.failed)
            finally:
                player.close()
