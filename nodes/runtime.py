"""Cross-cutting runtime concerns: secret resolution, retries, the result cache
and the call ledger. Shared by every vendor pack."""

import hashlib
import json
import os
import random
import threading
import time

import requests

from .common import bytes_to_tensor, comfy_directory, tensor_to_png_bytes

PACK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SECRET_SOURCES = (".env", "environment", "node widget")
RETRY_STATUSES = (429, 500, 502, 503, 504)


# --------------------------------------------------------------------------- .env

_ENV_CACHE = {"stamp": None, "values": {}}


def env_file_paths():
    """Where we look for a .env, most authoritative first."""
    paths = []
    override = os.environ.get("STRANGE_PETS_ENV", "").strip()
    if override:
        paths.append(os.path.expanduser(override))
    paths.append(os.path.join(PACK_DIR, ".env"))
    paths.append(os.path.join(os.path.expanduser("~"), ".strange-pets", ".env"))
    return paths


def parse_env_file(path):
    values = {}
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            name, _, value = line.partition("=")
            name, value = name.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if name:
                values[name] = value
    return values


def dotenv_values():
    """Merged contents of every .env we can find; earlier paths win. Re-read when
    a file's mtime changes, so editing .env takes effect without restarting."""
    paths = env_file_paths()
    stamp = []
    for path in paths:
        try:
            stamp.append((path, os.path.getmtime(path)))
        except OSError:
            stamp.append((path, None))
    if _ENV_CACHE["stamp"] == stamp:
        return _ENV_CACHE["values"]

    merged = {}
    for path in reversed(paths):
        try:
            if os.path.isfile(path):
                merged.update(parse_env_file(path))
        except OSError:
            continue
    _ENV_CACHE["stamp"] = stamp
    _ENV_CACHE["values"] = merged
    return merged


_announced = set()


def secret_sources(name, widget_value=""):
    """Every source holding a value for `name`, in precedence order."""
    return [
        (".env", (dotenv_values().get(name) or "").strip()),
        ("environment", os.environ.get(name, "").strip()),
        ("node widget", (widget_value or "").strip()),
    ]


def resolve_secret(name, widget_value=""):
    """Return (value, source). Precedence: .env > environment > node widget.

    The .env file is the pack's own deliberate, gitignored store, so it wins over an
    ambient variable you may have set months ago. The widget comes last because it is
    the insecure option and its value can arrive from a downloaded workflow rather
    than from you — but it still works when nothing else is set.
    """
    found = secret_sources(name, widget_value)
    for source, value in found:
        if not value:
            continue
        if (name, source) not in _announced:
            _announced.add((name, source))
            print("[strange-pets] {} resolved from {}".format(name, source))
        shadowed = [s for s, v in found if v and s != source]
        if shadowed and (name, source, tuple(shadowed)) not in _announced:
            _announced.add((name, source, tuple(shadowed)))
            print("[strange-pets] note: {} is also set in {} — {} wins".format(
                name, " and ".join(shadowed), source))
        return value, source
    return "", "none"


def setting(name, default=""):
    """A non-secret config value, same precedence as secrets minus the widget."""
    value = (dotenv_values().get(name) or "").strip() or os.environ.get(name, "").strip()
    return value or default


def setting_bool(name, default=True):
    value = setting(name, "").lower()
    if not value:
        return default
    return value not in ("0", "false", "off", "no")


def setting_int(name, default):
    try:
        return int(setting(name, ""))
    except ValueError:
        return default


# --------------------------------------------------------------------------- retries

def retry_after_seconds(response, fallback):
    header = response.headers.get("retry-after") if response is not None else None
    if header:
        try:
            return max(0.0, float(header))
        except (TypeError, ValueError):
            pass
    return fallback


def request_with_retry(send, label="request", attempts=None):
    """Call `send()` (returning a Response), retrying 429s and 5xx with backoff.

    Stability documents its limit as 150 requests / 10 seconds; BFL does not document
    a 429 at all but can still throttle, so both packs retry defensively.
    """
    attempts = attempts or max(1, setting_int("STRANGE_PETS_RETRIES", 3))
    delay = 1.0
    for attempt in range(1, attempts + 1):
        try:
            response = send()
        except requests.RequestException:
            if attempt >= attempts:
                raise
            wait = delay + random.uniform(0, 0.4)
            print("[strange-pets] {} connection error; retrying in {:.1f}s ({}/{})".format(
                label, wait, attempt, attempts - 1))
            time.sleep(wait)
            delay = min(delay * 2, 30.0)
            continue

        if response.status_code not in RETRY_STATUSES or attempt >= attempts:
            return response

        wait = retry_after_seconds(response, delay) + random.uniform(0, 0.4)
        print("[strange-pets] {} returned HTTP {}; retrying in {:.1f}s ({}/{})".format(
            label, response.status_code, wait, attempt, attempts - 1))
        response.close()
        time.sleep(wait)
        delay = min(delay * 2, 30.0)
    return response


# --------------------------------------------------------------------------- cache

def cache_directory():
    configured = setting("STRANGE_PETS_CACHE_DIR", "")
    if configured:
        return os.path.expanduser(configured)
    return os.path.join(comfy_directory("output"), "strange-pets-cache")


def cache_enabled():
    return setting_bool("STRANGE_PETS_CACHE", True)


def cache_key(provider, endpoint, payload):
    blob = json.dumps({"provider": provider, "endpoint": endpoint, "payload": payload},
                      sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _cache_paths(key):
    directory = os.path.join(cache_directory(), key[:2])
    return os.path.join(directory, key + ".png"), os.path.join(directory, key + ".json")


def cache_get(key):
    image_path, _ = _cache_paths(key)
    try:
        with open(image_path, "rb") as handle:
            return bytes_to_tensor(handle.read())
    except (OSError, ValueError):
        return None


def cache_put(key, image, meta):
    image_path, meta_path = _cache_paths(key)
    try:
        os.makedirs(os.path.dirname(image_path), exist_ok=True)
        with open(image_path, "wb") as handle:
            handle.write(tensor_to_png_bytes(image))
        with open(meta_path, "w") as handle:
            json.dump(meta, handle, indent=2)
    except OSError as exc:
        print("[strange-pets] could not write cache entry: {}".format(exc))


def cache_stats():
    root = cache_directory()
    count = 0
    size = 0
    for base, _, files in os.walk(root):
        for name in files:
            if name.endswith(".png"):
                count += 1
                try:
                    size += os.path.getsize(os.path.join(base, name))
                except OSError:
                    pass
    return {"directory": root, "entries": count, "bytes": size}


def cache_purge():
    root = cache_directory()
    removed = 0
    for base, _, files in os.walk(root):
        for name in files:
            if name.endswith((".png", ".json")):
                try:
                    os.remove(os.path.join(base, name))
                    removed += 1
                except OSError:
                    pass
    return removed


# --------------------------------------------------------------------------- ledger

_LEDGER = {"entries": [], "lock": threading.Lock()}


def record_call(provider, endpoint, cost=None, input_mp=None, output_mp=None, cached=False):
    with _LEDGER["lock"]:
        _LEDGER["entries"].append({
            "provider": provider, "endpoint": endpoint, "cost": cost,
            "input_mp": input_mp, "output_mp": output_mp, "cached": cached,
            "at": time.time(),
        })


def ledger_entries():
    with _LEDGER["lock"]:
        return list(_LEDGER["entries"])


def ledger_reset():
    with _LEDGER["lock"]:
        count = len(_LEDGER["entries"])
        _LEDGER["entries"] = []
    return count
