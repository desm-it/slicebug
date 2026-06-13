import unittest
from types import SimpleNamespace
from unittest.mock import patch

from slicebug.cli.cut import make_start_message
from slicebug.cricut.protobufs.Bridge_pb2 import PBInteractionStatus, PBLogLevel


class CutStartMessageTest(unittest.TestCase):
    def test_start_message_matches_design_space_envelope_on_macos(self):
        config = SimpleNamespace(
            keys=SimpleNamespace(settings8_raw=b"settings8-from-bootstrap")
        )

        with patch("slicebug.cli.cut.platform.system", return_value="Darwin"):
            message = make_start_message(config, PBInteractionStatus.riMATCUT)

        self.assertEqual(message.interaction, PBInteractionStatus.riMATCUT)
        self.assertEqual(message.logId, "DEVICE")
        self.assertEqual(message.logLevel, PBLogLevel.ERROR_LOGLEVEL)
        self.assertNotIn(b"\xc8\x08", message.SerializeToString())
        self.assertEqual(message.authData.settings8, "settings8-from-bootstrap")

    def test_start_message_requests_verbose_helper_logs_on_windows(self):
        config = SimpleNamespace(
            keys=SimpleNamespace(settings8_raw=b"settings8-from-bootstrap")
        )

        with patch("slicebug.cli.cut.platform.system", return_value="Windows"):
            message = make_start_message(config, PBInteractionStatus.riMATCUT)

        self.assertEqual(message.interaction, PBInteractionStatus.riMATCUT)
        self.assertEqual(message.logId, "DEVICE")
        self.assertEqual(message.logLevel, PBLogLevel.VERBOSE_LOGLEVEL)
        self.assertIn(b"\xc8\x08\x05", message.SerializeToString())


if __name__ == "__main__":
    unittest.main()
