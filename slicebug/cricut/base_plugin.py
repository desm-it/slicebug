import struct
import subprocess
import threading
from pathlib import Path

from slicebug.debug import log_debug


class BasePlugin:
    def __init__(self, path):
        self._path = path
        plugin_cwd = str(Path(path).resolve().parent)
        log_debug("plugin.start", path=path, cwd=plugin_cwd)
        self._process = subprocess.Popen(
            self._path,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            cwd=plugin_cwd,
        )
        self._stderr_thread = threading.Thread(
            target=self._log_stderr,
            name="slicebug-plugin-stderr",
            daemon=True,
        )
        self._stderr_thread.start()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        log_debug("plugin.close", path=self._path)
        self._process.terminate()
        try:
            self._process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            log_debug("plugin.close.timeout", path=self._path)
            self._process.kill()
            self._process.wait(timeout=2)
        if self._process.stdin is not None:
            self._process.stdin.close()
        if self._process.stdout is not None:
            self._process.stdout.close()
        if self._process.stderr is not None:
            self._process.stderr.close()

    def _log_stderr(self):
        if self._process.stderr is None:
            return
        for line in iter(self._process.stderr.readline, b""):
            log_debug(
                "plugin.stderr",
                path=getattr(self, "_path", None),
                line=line.decode(errors="replace").rstrip(),
            )

    def send_bytes(self, message):
        log_debug(
            "plugin.send_bytes",
            path=getattr(self, "_path", None),
            byte_count=len(message),
        )
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
                log_debug(
                    "plugin.read_eof",
                    path=getattr(self, "_path", None),
                    expected=size,
                    bytes_read=bytes_read,
                )
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
        log_debug(
            "plugin.recv_bytes",
            path=getattr(self, "_path", None),
            byte_count=message_len,
        )
        return self._read_exactly(message_len)
