import select
import time

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
        super().__init__(path)
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
        while self._is_ping_request(message):
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
                log_debug(
                    "device.recv.ping_timeout",
                    expected=int(expect) if expect is not None else None,
                    ping_count=ping_count,
                    elapsed_seconds=elapsed,
                    ping_timeout_seconds=ping_timeout,
                    message=describe_protobuf(message),
                )
                raise ProtocolError(
                    "CricutDevice kept sending ping frames and never reported "
                    f"the expected startup status after {elapsed:.1f}s "
                    f"({ping_count} pings). This usually means the Design Space "
                    "device helper is stuck while scanning or opening the cutter. "
                    "Close Design Space, make sure the cutter is awake and not "
                    "connected to another computer, then try again."
                )
            self.send(self._ping_reply())
            message = self._recv()

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
    def _ping_reply():
        return PBCommonBridge(
            handle=PBInteractionHandle(currentInteraction=999),
            status=PBInteractionStatus.riPingReply,
        )

    def recv_if_available(self, timeout=0):
        if self._process.stdout is None:
            return None
        ready, _, _ = select.select([self._process.stdout], [], [], timeout)
        if not ready:
            return None
        return self.recv()
