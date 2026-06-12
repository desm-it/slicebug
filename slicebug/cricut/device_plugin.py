import select

from cryptography.hazmat.primitives.padding import PKCS7
from cryptography.hazmat.primitives.ciphers import Cipher
from cryptography.hazmat.primitives.ciphers.algorithms import AES
from cryptography.hazmat.primitives.ciphers.modes import ECB

from slicebug.cricut.base_plugin import BasePlugin
from slicebug.cricut.protobufs.Bridge_pb2 import PBCommonBridge
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

    def recv(self, expect=None):
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

    def recv_if_available(self, timeout=0):
        if self._process.stdout is None:
            return None
        ready, _, _ = select.select([self._process.stdout], [], [], timeout)
        if not ready:
            return None
        return self.recv()
