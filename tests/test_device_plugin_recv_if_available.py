import json
import os
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from slicebug.cricut.base_plugin import BasePlugin
from slicebug.cricut.device_plugin import DevicePlugin
from slicebug.cricut.protobufs.Bridge_pb2 import (
    PBCommonBridge,
    PBInteractionHandle,
    PBInteractionStatus,
    PBLog,
    PBLogLevel,
)
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


class ShortWriteStdin:
    def __init__(self, max_chunk_size):
        self._max_chunk_size = max_chunk_size
        self.writes = []
        self.flushed = False

    def write(self, data):
        chunk_size = min(len(data), self._max_chunk_size)
        self.writes.append(bytes(data[:chunk_size]))
        return chunk_size

    def flush(self):
        self.flushed = True


class ZeroWriteStdin:
    def write(self, data):
        return 0


class BasePluginRecvBytesTest(unittest.TestCase):
    def test_start_plugin_uses_plugin_directory_as_working_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_path = Path(temp_dir) / "device-common" / "CricutDevice.exe"
            plugin_path.parent.mkdir()
            process = MagicMock()
            process.stderr.readline.return_value = b""
            # Stop the background reader thread immediately on an empty stdout.
            process.stdout.read.return_value = b""

            with patch(
                "slicebug.cricut.base_plugin.subprocess.Popen", return_value=process
            ) as popen:
                plugin = BasePlugin(str(plugin_path))

            self.assertEqual(plugin._path, str(plugin_path))
            self.assertEqual(
                popen.call_args.kwargs["cwd"],
                str(plugin_path.parent.resolve()),
            )
            self.assertEqual(popen.call_args.args[0], [str(plugin_path)])
            self.assertEqual(popen.call_args.kwargs["stderr"], subprocess.PIPE)

    def test_start_plugin_accepts_process_arguments(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_path = Path(temp_dir) / "device-common" / "CricutDevice.exe"
            plugin_path.parent.mkdir()
            process = MagicMock()
            process.stderr.readline.return_value = b""
            # Stop the background reader thread immediately on an empty stdout.
            process.stdout.read.return_value = b""

            with patch(
                "slicebug.cricut.base_plugin.subprocess.Popen", return_value=process
            ) as popen:
                BasePlugin(str(plugin_path), args=["bridge"])

            self.assertEqual(popen.call_args.args[0], [str(plugin_path), "bridge"])

    def test_recv_bytes_keeps_reading_until_full_length_prefixed_message_arrives(self):
        message = b"a protobuf frame larger than one pipe read"
        payload = struct.pack("<i", len(message)) + message

        plugin = BasePlugin.__new__(BasePlugin)
        setattr(
            plugin,
            "_process",
            type("Process", (), {"stdout": ShortReadStdout(payload, 4)})(),
        )
        plugin._start_reader()

        self.assertEqual(plugin.recv_bytes(), message)

    def test_send_bytes_keeps_writing_until_full_length_prefixed_message_is_sent(self):
        message = b"a large encrypted startup frame"
        stdin = ShortWriteStdin(max_chunk_size=5)
        plugin = BasePlugin.__new__(BasePlugin)
        setattr(plugin, "_path", "CricutDevice.exe")
        setattr(plugin, "_process", type("Process", (), {"stdin": stdin})())

        plugin.send_bytes(message)

        self.assertEqual(b"".join(stdin.writes), struct.pack("<i", len(message)) + message)
        self.assertTrue(stdin.flushed)

    def test_send_bytes_errors_when_pipe_accepts_no_bytes(self):
        plugin = BasePlugin.__new__(BasePlugin)
        setattr(plugin, "_path", "CricutDevice.exe")
        setattr(plugin, "_process", type("Process", (), {"stdin": ZeroWriteStdin()})())

        with self.assertRaises(BrokenPipeError):
            plugin.send_bytes(b"message")


class DevicePluginRecvIfAvailableTest(unittest.TestCase):
    def test_recv_if_available_returns_a_message_already_sent_over_the_pipe(self):
        # Two framed messages the helper sends back to back over a real OS pipe
        # (unbuffered, like the bufsize=0 stdout in production). A real pipe is
        # what the old select.select() poll could not handle on Windows.
        mat_loaded = PBCommonBridge(
            status=PBInteractionStatus.riMatLoaded
        ).SerializeToString()
        wait_clear = PBCommonBridge(
            status=PBInteractionStatus.riWaitClear
        ).SerializeToString()
        payload = (
            struct.pack("<i", len(mat_loaded))
            + mat_loaded
            + struct.pack("<i", len(wait_clear))
            + wait_clear
        )

        read_fd, write_fd = os.pipe()
        dev = DevicePlugin.__new__(DevicePlugin)
        setattr(dev, "_path", "CricutDevice.exe")
        setattr(
            dev,
            "_process",
            type("Process", (), {"stdout": os.fdopen(read_fd, "rb", buffering=0)})(),
        )
        try:
            dev._start_reader()
            # Send both frames, then close the write end so the reader hits EOF.
            with os.fdopen(write_fd, "wb", buffering=0) as writer:
                writer.write(payload)

            # Wait for the reader thread to drain the pipe into the queue so the
            # non-blocking poll below is deterministic regardless of thread
            # timing: recv_if_available must surface the second message the
            # helper already sent, which select.select() could not do portably
            # on Windows pipe handles.
            dev._reader_thread.join(timeout=5)

            self.assertEqual(dev.recv().status, PBInteractionStatus.riMatLoaded)
            next_message = dev.recv_if_available(timeout=0)
            self.assertIsNotNone(next_message)
            self.assertEqual(next_message.status, PBInteractionStatus.riWaitClear)
        finally:
            dev._process.stdout.close()

    def test_recv_answers_ping_handle_and_keeps_waiting_for_expected_status(self):
        ping = PBCommonBridge(
            handle=PBInteractionHandle(currentInteraction=PBInteractionStatus.riPing)
        ).SerializeToString()
        start_success = PBCommonBridge(
            status=PBInteractionStatus.riStartSuccess
        ).SerializeToString()
        payload = (
            struct.pack("<i", len(ping))
            + ping
            + struct.pack("<i", len(start_success))
            + start_success
        )
        sent = []

        dev = DevicePlugin.__new__(DevicePlugin)
        setattr(dev, "_path", "CricutDevice.exe")
        setattr(
            dev,
            "_process",
            type("Process", (), {"stdout": ShortReadStdout(payload, 4)})(),
        )
        setattr(dev, "send", lambda message: sent.append(message))
        dev._start_reader()

        response = dev.recv(PBInteractionStatus.riStartSuccess)

        self.assertEqual(response.status, PBInteractionStatus.riStartSuccess)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].status, PBInteractionStatus.riPingReply)
        self.assertTrue(sent[0].HasField("handle"))
        self.assertEqual(sent[0].handle.currentInteraction, 999)

    def test_recv_logs_helper_log_messages_and_keeps_waiting_for_expected_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            debug_log = Path(temp_dir) / "slicebug-debug.log"
            old_debug_log = os.environ.get("SLICEBUG_DEBUG_LOG")
            os.environ["SLICEBUG_DEBUG_LOG"] = str(debug_log)
            try:
                helper_log = PBCommonBridge(
                    status=PBInteractionStatus.riLogMessage,
                    logs=[
                        PBLog(
                            title="Bluetooth",
                            message="Opening device",
                            level=PBLogLevel.DEBUG_LOGLEVEL,
                        )
                    ],
                ).SerializeToString()
                start_success = PBCommonBridge(
                    status=PBInteractionStatus.riStartSuccess
                ).SerializeToString()
                payload = (
                    struct.pack("<i", len(helper_log))
                    + helper_log
                    + struct.pack("<i", len(start_success))
                    + start_success
                )

                dev = DevicePlugin.__new__(DevicePlugin)
                setattr(dev, "_path", "CricutDevice.exe")
                setattr(
                    dev,
                    "_process",
                    type("Process", (), {"stdout": ShortReadStdout(payload, 4)})(),
                )
                dev._start_reader()

                response = dev.recv(PBInteractionStatus.riStartSuccess)

                self.assertEqual(response.status, PBInteractionStatus.riStartSuccess)
                entries = [
                    json.loads(line)
                    for line in debug_log.read_text(encoding="utf-8").splitlines()
                ]
                helper_log_entry = [
                    entry
                    for entry in entries
                    if entry["event"] == "device.recv.log_message"
                ][0]

                self.assertEqual(helper_log_entry["details"]["log_count"], 1)
                self.assertEqual(
                    helper_log_entry["details"]["logs"][0]["fields"]["message"],
                    "Opening device",
                )
                self.assertEqual(
                    helper_log_entry["details"]["message"]["statusName"],
                    "riLogMessage",
                )
            finally:
                if old_debug_log is None:
                    os.environ.pop("SLICEBUG_DEBUG_LOG", None)
                else:
                    os.environ["SLICEBUG_DEBUG_LOG"] = old_debug_log

    def test_recv_times_out_when_device_only_sends_pings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_path = Path(temp_dir) / "device-common" / "CricutDevice.exe"
            bridge_log_path = plugin_path.parent / "logs" / "bridge.log"
            bridge_log_path.parent.mkdir(parents=True)
            bridge_log_path.write_text("native helper clue", encoding="utf-8")

            debug_log = Path(temp_dir) / "slicebug-debug.log"
            old_debug_log = os.environ.get("SLICEBUG_DEBUG_LOG")
            os.environ["SLICEBUG_DEBUG_LOG"] = str(debug_log)
            try:
                ping = PBCommonBridge(
                    handle=PBInteractionHandle(
                        currentInteraction=PBInteractionStatus.riPing
                    )
                ).SerializeToString()
                payload = (
                    struct.pack("<i", len(ping))
                    + ping
                    + struct.pack("<i", len(ping))
                    + ping
                )
                sent = []

                dev = DevicePlugin.__new__(DevicePlugin)
                setattr(dev, "_path", str(plugin_path))
                setattr(
                    dev,
                    "_process",
                    type("Process", (), {"stdout": ShortReadStdout(payload, 4)})(),
                )
                setattr(dev, "send", lambda message: sent.append(message))
                dev._start_reader()

                with patch(
                    "slicebug.cricut.device_plugin.time.monotonic",
                    side_effect=[0.0, 2.0],
                ):
                    with self.assertRaisesRegex(
                        ProtocolError, "kept sending ping frames"
                    ):
                        dev.recv(PBInteractionStatus.riStartSuccess, ping_timeout=1.0)

                self.assertEqual(len(sent), 1)
                self.assertEqual(sent[0].status, PBInteractionStatus.riPingReply)

                entries = [
                    json.loads(line)
                    for line in debug_log.read_text(encoding="utf-8").splitlines()
                ]
                timeout = [
                    entry
                    for entry in entries
                    if entry["event"] == "device.recv.ping_timeout"
                ][0]
                first_bridge_log = timeout["details"]["bridge_log"]["candidates"][0]

                self.assertTrue(first_bridge_log["exists"])
                self.assertEqual(
                    first_bridge_log["path"], str(bridge_log_path.resolve())
                )
                self.assertEqual(first_bridge_log["tail"], "native helper clue")
            finally:
                if old_debug_log is None:
                    os.environ.pop("SLICEBUG_DEBUG_LOG", None)
                else:
                    os.environ["SLICEBUG_DEBUG_LOG"] = old_debug_log

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
                dev._start_reader()

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
