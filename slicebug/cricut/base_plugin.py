import hashlib
import struct
import subprocess
import threading
from pathlib import Path

from slicebug.debug import log_debug


class BasePlugin:
    def __init__(self, path, args=None):
        self._path = path
        self._args = list(args or [])
        plugin_cwd = str(Path(path).resolve().parent)
        command = [self._path, *self._args]
        log_debug("plugin.start", path=path, args=self._args, cwd=plugin_cwd)
        self._process = subprocess.Popen(
            command,
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
        # The frame is a little-endian int32 length prefix followed by the body.
        # We build it as a single buffer and write it in full: with bufsize=0 the
        # child's stdin is a raw stream whose write() is allowed to short-write,
        # so a single unchecked write() can silently truncate a large frame. This
        # bites large frames (the ~5552-byte encrypted cut startup message) on
        # platforms with small pipe buffers (Windows) while working on macOS,
        # leaving the native helper waiting forever for the rest of the frame.
        frame = struct.pack("<i", len(message)) + message
        log_debug(
            "plugin.send_bytes",
            path=getattr(self, "_path", None),
            byte_count=len(message),
            frame_bytes=len(frame),
            payload_sha256=hashlib.sha256(message).hexdigest()[:16],
        )
        written = self._write_all(frame)
        self._process.stdin.flush()
        if written != len(frame):
            log_debug(
                "plugin.send_bytes.incomplete",
                path=getattr(self, "_path", None),
                expected=len(frame),
                written=written,
            )

    def _write_all(self, data):
        stdin = self._process.stdin
        if stdin is None:
            raise BrokenPipeError("Plugin stdin is not available")
        view = memoryview(data)
        total = 0
        short_writes = 0
        while total < len(view):
            written = stdin.write(view[total:])
            if written is None:
                raise BlockingIOError("Plugin stdin write would block")
            if written == 0:
                raise BrokenPipeError("Plugin stdin accepted 0 bytes")
            if written < len(view) - total:
                short_writes += 1
            total += written
        if short_writes:
            log_debug(
                "plugin.send_bytes.short_writes",
                path=getattr(self, "_path", None),
                short_writes=short_writes,
                total=total,
            )
        return total

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
