import json
import os
import platform
import sys
import threading
import traceback
from datetime import datetime, timezone

try:
    from google.protobuf.json_format import MessageToDict
except Exception:  # pragma: no cover - protobuf is a runtime dependency
    MessageToDict = None


_LOCK = threading.Lock()
_REDACTED_KEYS = {
    "authData",
    "auth_data",
    "key",
    "request_key",
    "settings8",
    "settings8_raw",
    "cricutdevice_request_key",
}


def enable_debug_logging():
    os.environ["SLICEBUG_DEBUG"] = "1"


def _default_debug_path():
    if os.environ.get("SLICEBUG_DEBUG") not in {"1", "true", "TRUE", "yes", "YES"}:
        return None

    executable_dir = os.path.dirname(os.path.abspath(sys.executable))
    return os.path.join(executable_dir, "slicebug-debug.log")


def debug_log_path():
    configured = os.environ.get("SLICEBUG_DEBUG_LOG")
    if configured:
        return configured
    return _default_debug_path()


def debug_enabled():
    return debug_log_path() is not None


def log_debug(event, **details):
    path = debug_log_path()
    if path is None:
        return

    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "event": event,
        "details": _redact(details),
    }

    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with _LOCK:
            with open(path, "a", encoding="utf-8") as log_file:
                log_file.write(json.dumps(entry, sort_keys=True, default=str))
                log_file.write("\n")
    except Exception:
        # Debug logging must never change slicebug behavior.
        pass


def log_exception(event, error):
    log_debug(
        event,
        error_type=type(error).__name__,
        error=str(error),
        traceback="".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        ),
    )


def describe_protobuf(message):
    description = {
        "type": message.DESCRIPTOR.full_name,
    }

    if hasattr(message, "status"):
        status = int(message.status)
        description["status"] = status
        description["statusName"] = _enum_name(message, "status", status)

    if hasattr(message, "interaction"):
        interaction = int(message.interaction)
        description["interaction"] = interaction
        description["interactionName"] = _enum_name(message, "interaction", interaction)

    if MessageToDict is not None:
        try:
            description["fields"] = _redact(
                MessageToDict(
                    message,
                    preserving_proto_field_name=True,
                    use_integers_for_enums=False,
                )
            )
        except Exception as err:
            description["fieldsError"] = str(err)

    return description


def _enum_name(message, field_name, value):
    field = message.DESCRIPTOR.fields_by_name.get(field_name)
    if field is None or field.enum_type is None:
        return None
    enum_value = field.enum_type.values_by_number.get(value)
    return enum_value.name if enum_value is not None else None


def _redact(value):
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if key in _REDACTED_KEYS:
                redacted[key] = "[redacted]"
            else:
                redacted[key] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value
