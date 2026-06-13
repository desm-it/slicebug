import unittest
from types import SimpleNamespace

from slicebug.cli.cut import make_start_message
from slicebug.cricut.protobufs.Bridge_pb2 import PBInteractionStatus


class CutStartMessageTest(unittest.TestCase):
    def test_start_message_matches_design_space_envelope(self):
        config = SimpleNamespace(
            keys=SimpleNamespace(settings8_raw=b"settings8-from-bootstrap")
        )

        message = make_start_message(config, PBInteractionStatus.riMATCUT)

        self.assertEqual(message.interaction, PBInteractionStatus.riMATCUT)
        self.assertEqual(message.logId, "DEVICE")
        self.assertEqual(message.authData.settings8, "settings8-from-bootstrap")


if __name__ == "__main__":
    unittest.main()
