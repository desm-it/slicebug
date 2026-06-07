import struct
import sys
import tempfile
import unittest
from pathlib import Path

from slicebug.cricut.base_plugin import BasePlugin
from slicebug.cricut.device_plugin import DevicePlugin
from slicebug.cricut.protobufs.Bridge_pb2 import PBInteractionStatus


class ShortReadStdout:
    def __init__(self, payload, max_chunk_size):
        self._payload = payload
        self._max_chunk_size = max_chunk_size
        self._offset = 0

    def read(self, size):
        if self._offset >= len(self._payload):
            return b""
        chunk_size = min(size, self._max_chunk_size, len(self._payload) - self._offset)
        chunk = self._payload[self._offset : self._offset + chunk_size]
        self._offset += chunk_size
        return chunk


class BasePluginRecvBytesTest(unittest.TestCase):
    def test_recv_bytes_keeps_reading_until_full_length_prefixed_message_arrives(self):
        message = b"a protobuf frame larger than one pipe read"
        payload = struct.pack("<i", len(message)) + message

        plugin = BasePlugin.__new__(BasePlugin)
        setattr(
            plugin,
            "_process",
            type("Process", (), {"stdout": ShortReadStdout(payload, 4)})(),
        )

        self.assertEqual(plugin.recv_bytes(), message)


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
