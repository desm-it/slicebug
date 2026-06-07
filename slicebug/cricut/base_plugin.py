import struct
import subprocess


class BasePlugin:
    def __init__(self, path):
        self._path = path
        self._process = subprocess.Popen(
            self._path,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            bufsize=0,
        )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        self._process.terminate()
        try:
            self._process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=2)
        if self._process.stdin is not None:
            self._process.stdin.close()
        if self._process.stdout is not None:
            self._process.stdout.close()

    def send_bytes(self, message):
        message_len = struct.pack("<i", len(message))
        self._process.stdin.write(message_len)
        self._process.stdin.write(message)
        self._process.stdin.flush()

    def _read_exactly(self, size):
        if self._process.stdout is None:
            raise EOFError("Plugin stdout is not available")
        chunks = []
        bytes_read = 0
        while bytes_read < size:
            chunk = self._process.stdout.read(size - bytes_read)
            if not chunk:
                raise EOFError(
                    f"Plugin stdout closed while reading message: "
                    f"expected {size} bytes, got {bytes_read}"
                )
            chunks.append(chunk)
            bytes_read += len(chunk)
        return b"".join(chunks)

    def recv_bytes(self):
        message_len_encoded = self._read_exactly(4)
        (message_len,) = struct.unpack("<i", message_len_encoded)
        return self._read_exactly(message_len)
