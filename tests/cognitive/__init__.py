"""Cognitive tests for HEEPidermis sessions.

Each test is a self-contained Tk state machine that renders fullscreen,
collects per-trial events and returns a JSON-serializable
:class:`tests.cognitive.base.TestResult`.

* Standalone: ``python tests/run_test.py <test> [options]``
* From the GUI (later): instantiate the test class with a config, call
  ``run(parent=...)`` with an ``on_finish`` callback, or call ``run()`` and
  let it block, then read ``test.result``.
"""

from .base import CognitiveTest, TestConfig, TestResult
from .stroop import COLOR_POOL, StroopConfig, StroopTest
from .stroop_stress import StroopStressConfig, StroopStressTest
from .voice_test import VoiceTestConfig, VoiceTest

# Registry the GUI can iterate over to offer every available test.
TESTS = {
    "stroop": StroopTest,
    "stroop-stress": StroopStressTest,
    "voice-test": VoiceTest,
}

__all__ = ["CognitiveTest", "TestConfig", "TestResult",
           "StroopConfig", "StroopTest", "StroopStressConfig", "StroopStressTest",
           "VoiceTestConfig", "VoiceTest",
           "COLOR_POOL", "TESTS"]
