import sys
import tempfile
import unittest
from pathlib import Path

from slicebug.cricut.device_plugin import DevicePlugin
from slicebug.cricut.protobufs.Bridge_pb2 import PBInteractionStatus


class DevicePluginRecvIfAvailableTest(unittest.TestCase):
    def test_recv_if_available_sees_message_already_buffered_by_prior_read(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = Path(temp_dir) / "writer.py"
            writer.write_text(
                f"#!{sys.executable}\n"
                "import struct, sys\n"
                "from slicebug.cricut.protobufs.Bridge_pb2 import PBCommonBridge, PBInteractionStatus\n"
                "messages = [\n"
                "    PBCommonBridge(status=PBInteractionStatus.riMatLoaded).SerializeToString(),\n"
                "    PBCommonBridge(status=PBInteractionStatus.riWaitClear).SerializeToString(),\n"
                "]\n"
                "for message in messages:\n"
                "    sys.stdout.buffer.write(struct.pack('<i', len(message)))\n"
                "    sys.stdout.buffer.write(message)\n"
                "sys.stdout.buffer.flush()\n"
            )
            writer.chmod(0o755)

            dev = DevicePlugin(str(writer), b"0" * 16)

            try:
                self.assertEqual(dev.recv().status, PBInteractionStatus.riMatLoaded)

                # The child wrote both messages at once. BufferedReader may have
                # pulled the second message into Python's internal buffer while
                # reading the first one; recv_if_available still needs to see it
                # instead of consulting only the OS fd.
                next_message = dev.recv_if_available(timeout=0)
                self.assertIsNotNone(next_message)
                self.assertEqual(next_message.status, PBInteractionStatus.riWaitClear)
            finally:
                dev.close()


if __name__ == "__main__":
    unittest.main()
