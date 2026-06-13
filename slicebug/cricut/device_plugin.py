import hashlib
import os
import select
import time
from pathlib import Path

from cryptography.hazmat.primitives.padding import PKCS7
from cryptography.hazmat.primitives.ciphers import Cipher
from cryptography.hazmat.primitives.ciphers.algorithms import AES
from cryptography.hazmat.primitives.ciphers.modes import ECB

from slicebug.cricut.base_plugin import BasePlugin
from slicebug.cricut.protobufs.Bridge_pb2 import (
    PBCommonBridge,
    PBInteractionHandle,
    PBInteractionStatus,
)
from slicebug.debug import describe_protobuf, log_debug
from slicebug.exceptions import ProtocolError


class DevicePlugin(BasePlugin):
    def __init__(self, path, request_key):
        super().__init__(path, args=["bridge"])
        self._request_key = request_key

    def _encrypt_request(self, message):
        cipher = Cipher(AES(self._request_key), ECB())
        encryptor = cipher.encryptor()
        padder = PKCS7(128).padder()
        padded = padder.update(message.SerializeToString()) + padder.finalize()
        return encryptor.update(padded) + encryptor.finalize()

    def send(self, message):
        log_debug("device.send", message=describe_protobuf(message))
        self.send_bytes(self._encrypt_request(message))

    def _recv(self):
        message_bytes = self.recv_bytes()
        message = PBCommonBridge.FromString(message_bytes)
        log_debug(
            "device.recv",
            byte_count=len(message_bytes),
            message=describe_protobuf(message),
        )
        return message

    def recv(self, expect=None, ping_timeout=None):
        message = self._recv()
        ping_started_at = None
        ping_count = 0

        while True:
            if self._is_log_message(message):
                self._log_helper_message(message)
                message = self._recv()
                continue

            if self._is_ping_request(message):
                ping_count += 1
                now = time.monotonic()
                if ping_started_at is None:
                    ping_started_at = now
                elapsed = now - ping_started_at
                log_debug(
                    "device.recv.ping",
                    message=describe_protobuf(message),
                    ping_count=ping_count,
                    elapsed_seconds=elapsed,
                    ping_timeout_seconds=ping_timeout,
                )
                if ping_timeout is not None and elapsed >= ping_timeout:
                    bridge_log = self._bridge_log_details()
                    log_debug(
                        "device.recv.ping_timeout",
                        expected=int(expect) if expect is not None else None,
                        ping_count=ping_count,
                        elapsed_seconds=elapsed,
                        ping_timeout_seconds=ping_timeout,
                        message=describe_protobuf(message),
                        bridge_log=bridge_log,
                    )
                    bridge_log_hint = ""
                    if bridge_log["candidates"]:
                        bridge_log_hint = (
                            f" Native helper log checked: "
                            f"{bridge_log['candidates'][0]['path']}."
                        )
                    raise ProtocolError(
                        "CricutDevice kept sending ping frames and never reported "
                        f"the expected startup status after {elapsed:.1f}s "
                        f"({ping_count} pings). This usually means the Design Space "
                        "device helper is stuck while scanning or opening the cutter. "
                        "Close Design Space, make sure the cutter is awake and not "
                        "connected to another computer, then try again."
                        f"{bridge_log_hint}"
                    )
                self.send(self._ping_reply())
                message = self._recv()
                continue

            break

        if (expect is not None) and (message.status != expect):
            log_debug(
                "device.recv.unexpected_status",
                expected=int(expect),
                received=int(message.status),
                message=describe_protobuf(message),
            )
            raise ProtocolError(
                f"incorrect message status: expected {expect}, got {message.status}; "
                f"message: {describe_protobuf(message)}"
            )

        return message

    @staticmethod
    def _is_ping_request(message):
        return (
            message.HasField("handle")
            and message.handle.currentInteraction == PBInteractionStatus.riPing
        )

    @staticmethod
    def _is_log_message(message):
        return (
            message.status == PBInteractionStatus.riLogMessage
            or message.interaction == PBInteractionStatus.riLogMessage
        )

    @staticmethod
    def _log_helper_message(message):
        log_debug(
            "device.recv.log_message",
            message=describe_protobuf(message),
            logs=[describe_protobuf(log) for log in message.logs],
            log_count=len(message.logs),
        )

    @staticmethod
    def _ping_reply():
        return PBCommonBridge(
            handle=PBInteractionHandle(currentInteraction=999),
            status=PBInteractionStatus.riPingReply,
        )

    def _bridge_log_details(self):
        try:
            plugin_dir = Path(self._path).resolve().parent
        except Exception:
            plugin_dir = Path(str(getattr(self, "_path", ""))).parent

        candidates = [
            plugin_dir / "logs" / "bridge.log",
            plugin_dir / "bridge.log",
        ]
        return {"candidates": [self._describe_bridge_log(path) for path in candidates]}

    @staticmethod
    def _describe_bridge_log(path):
        details = {"path": str(path), "exists": path.exists()}
        if not path.exists():
            return details

        try:
            stat = path.stat()
            details.update(
                {
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                }
            )
            sha256 = hashlib.sha256()
            with path.open("rb") as bridge_log:
                while chunk := bridge_log.read(1024 * 1024):
                    sha256.update(chunk)
                bridge_log.seek(max(0, stat.st_size - 4096), os.SEEK_SET)
                tail = bridge_log.read()
            details["sha256"] = sha256.hexdigest()
            details["tail"] = tail.decode("utf-8", errors="replace")
            details["tail_truncated"] = stat.st_size > len(tail)
        except Exception as error:
            details["error"] = f"{type(error).__name__}: {error}"
        return details

    def recv_if_available(self, timeout=0):
        if self._process.stdout is None:
            return None
        ready, _, _ = select.select([self._process.stdout], [], [], timeout)
        if not ready:
            return None
        return self.recv()
