import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

from slicebug.cricut.base_plugin import BasePlugin
from slicebug.cricut.device_plugin import DevicePlugin
from slicebug.cricut.protobufs.Bridge_pb2 import PBCommonBridge, PBInteractionStatus
from slicebug.exceptions import ProtocolError


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

    def test_recv_unexpected_status_writes_debug_log(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            debug_log = Path(temp_dir) / "slicebug-debug.log"
            old_debug_log = os.environ.get("SLICEBUG_DEBUG_LOG")
            os.environ["SLICEBUG_DEBUG_LOG"] = str(debug_log)
            try:
                message = PBCommonBridge(
                    status=PBInteractionStatus.riError
                ).SerializeToString()
                payload = struct.pack("<i", len(message)) + message

                dev = DevicePlugin.__new__(DevicePlugin)
                setattr(dev, "_path", "CricutDevice.exe")
                setattr(
                    dev,
                    "_process",
                    type("Process", (), {"stdout": ShortReadStdout(payload, 4)})(),
                )

                with self.assertRaisesRegex(ProtocolError, "expected 2, got 0"):
                    dev.recv(PBInteractionStatus.riStartSuccess)

                entries = [
                    json.loads(line)
                    for line in debug_log.read_text(encoding="utf-8").splitlines()
                ]
                unexpected = [
                    entry
                    for entry in entries
                    if entry["event"] == "device.recv.unexpected_status"
                ][0]

                self.assertEqual(unexpected["details"]["expected"], 2)
                self.assertEqual(unexpected["details"]["received"], 0)
                self.assertEqual(
                    unexpected["details"]["message"]["statusName"],
                    "riError",
                )
            finally:
                if old_debug_log is None:
                    os.environ.pop("SLICEBUG_DEBUG_LOG", None)
                else:
                    os.environ["SLICEBUG_DEBUG_LOG"] = old_debug_log


if __name__ == "__main__":
    unittest.main()
