"""Cross-cutting runtime concerns: secret resolution, retries, the result cache
and the call ledger. Shared by every vendor pack."""

import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import threading
import time

import requests

from .common import bytes_to_tensor, comfy_directory, tensor_to_png_bytes

PACK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SECRET_SOURCES = (".env", "environment", "node widget")
RETRY_STATUSES = (429, 500, 502, 503, 504)

# dotenvx writes every encrypted value with this prefix and leaves the file otherwise
# a plain .env, so the same parser reads both and only the marked values need help.
ENCRYPTED_PREFIX = "encrypted:"
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# ComfyUI Desktop inherits the desktop session's PATH rather than a login shell's, so a
# Homebrew or ~/.local install can be invisible to `which`.
DOTENVX_FALLBACKS = (
    "/usr/local/bin/dotenvx",
    "/opt/homebrew/bin/dotenvx",
    "~/.local/bin/dotenvx",
    "~/.dotenvx/dotenvx",
    "C:/Program Files/dotenvx/dotenvx.exe",
)


# --------------------------------------------------------------------------- .env

_ENV_CACHE = {"stamp": None, "values": {}, "notes": []}

# One-shot log lines, so a re-read of .env does not reprint what was already said.
_announced = set()


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


def keys_file_for(path):
    """dotenvx keeps the private keys in a .env.keys beside the .env it encrypted."""
    return os.path.join(os.path.dirname(os.path.abspath(path)), ".env.keys")


def dotenvx_binary(config):
    """Absolute path to the dotenvx executable, or "" when it is not installed."""
    configured = (config.get("STRANGE_PETS_DOTENVX_BIN")
                  or os.environ.get("STRANGE_PETS_DOTENVX_BIN", "")).strip()
    if configured:
        expanded = os.path.expanduser(configured)
        return expanded if os.path.isfile(expanded) else (shutil.which(configured) or "")
    found = shutil.which("dotenvx")
    if found:
        return found
    for candidate in DOTENVX_FALLBACKS:
        expanded = os.path.expanduser(candidate)
        if os.path.isfile(expanded):
            return expanded
    return ""


def dotenvx_enabled(config):
    value = (config.get("STRANGE_PETS_DOTENVX")
             or os.environ.get("STRANGE_PETS_DOTENVX", "")).strip().lower()
    return value not in ("0", "false", "off", "no")


def dotenvx_decrypt(path, names, config, notes):
    """Return plaintext values for `names` by shelling out to `dotenvx get`.

    dotenvx merges the process environment over the file and lets the environment win,
    which is the reverse of this pack's precedence. So the names being decrypted are
    removed from the child's environment — what comes back is the file's own value,
    and the .env > environment ordering still holds afterwards.
    """
    binary = dotenvx_binary(config)
    if not binary:
        notes.append(
            "{}: {} value(s) are dotenvx-encrypted, but no dotenvx executable was found. "
            "Install it from https://dotenvx.com, point STRANGE_PETS_DOTENVX_BIN at it, or "
            "run `dotenvx decrypt` to store the file in plaintext.".format(path, len(names)))
        return {}

    child = {name: value for name, value in os.environ.items() if name not in names}
    try:
        result = subprocess.run(
            [binary, "get", "--format", "json", "-f", path],
            capture_output=True, text=True, timeout=30, env=child,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        notes.append("{}: could not run {}: {}".format(path, binary, exc))
        return {}

    complaint = ANSI_RE.sub("", result.stderr or "").strip()[:300]
    try:
        payload = json.loads(result.stdout or "{}")
    except ValueError:
        notes.append("{}: dotenvx returned no JSON. {}".format(path, complaint))
        return {}

    plain, failed = {}, []
    for name in names:
        value = str(payload.get(name) or "").strip()
        if value and not value.startswith(ENCRYPTED_PREFIX):
            plain[name] = value
        else:
            failed.append(name)
    if failed:
        # dotenvx exits 0 and echoes the ciphertext back when it cannot decrypt, so the
        # ciphertext is what would otherwise be sent to the API as a key.
        notes.append(
            "{}: dotenvx could not decrypt {}. The private key comes from {} or from "
            "DOTENV_PRIVATE_KEY in the environment. {}".format(
                path, ", ".join(sorted(failed)), keys_file_for(path), complaint))
    if plain:
        notes.append("{}: decrypted {} value(s) via {}.".format(path, len(plain), binary))
    return plain


def dotenv_values():
    """Merged contents of every .env we can find; earlier paths win. Re-read when a
    file's mtime changes, so editing .env takes effect without restarting.

    Values written by `dotenvx encrypt` are decrypted here, so the rest of the pack
    never sees a ciphertext and precedence is unchanged by how the file is stored.
    """
    paths = env_file_paths()
    stamp = []
    for path in paths:
        for candidate in (path, keys_file_for(path)):
            try:
                stamp.append((candidate, os.path.getmtime(candidate)))
            except OSError:
                stamp.append((candidate, None))
    if _ENV_CACHE["stamp"] == stamp:
        return _ENV_CACHE["values"]

    merged = {}
    notes = []
    for path in reversed(paths):
        try:
            if not os.path.isfile(path):
                continue
            values = parse_env_file(path)
        except OSError:
            continue

        encrypted = sorted(name for name, value in values.items()
                           if value.startswith(ENCRYPTED_PREFIX))
        if encrypted:
            config = dict(merged)
            config.update(values)
            if dotenvx_enabled(config):
                values.update(dotenvx_decrypt(os.path.abspath(path), encrypted, config, notes))
            else:
                notes.append("{}: {} encrypted value(s) ignored; STRANGE_PETS_DOTENVX is off.".format(
                    path, len(encrypted)))
            # Whatever is still ciphertext is dropped rather than passed on as a key.
            values = {name: value for name, value in values.items()
                      if not value.startswith(ENCRYPTED_PREFIX)}
        merged.update(values)

    _ENV_CACHE["stamp"] = stamp
    _ENV_CACHE["values"] = merged
    _ENV_CACHE["notes"] = notes
    for note in notes:
        if ("dotenvx", note) not in _announced:
            _announced.add(("dotenvx", note))
            print("[strange-pets] {}".format(note))
    return merged


def dotenv_notes():
    """What the last .env read had to say about encrypted values, for API Key Status."""
    dotenv_values()
    return list(_ENV_CACHE["notes"])


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


def transport_advice(exc):
    """Say what a transport failure probably is, since the raw exception does not.

    These endpoints upload a whole image, so they are the first thing a flaky or
    inspected connection breaks — and the failure looks alarming while meaning nothing
    was generated and nothing was charged.
    """
    text = str(exc)
    if "BAD_RECORD_MAC" in text or "DECRYPTION_FAILED" in text or "WRONG_VERSION" in text:
        return (
            "\n\nA TLS 'bad record mac' means the encrypted stream was corrupted in transit: "
            "the request never arrived intact, so nothing was generated and nothing was "
            "charged. It is almost always local rather than the API's doing — antivirus or a "
            "VPN inspecting HTTPS, or an unstable link. Worth trying, roughly in order: a "
            "different network; exempting the API host from HTTPS/SSL scanning; and sending a "
            "smaller image, since Fit Image To Endpoint Limits can cut the upload by an order "
            "of magnitude and the upload is what breaks."
        )
    if isinstance(exc, requests.Timeout):
        return ("\n\nThe request timed out rather than being refused. Nothing was charged. "
                "Raise STRANGE_PETS_TRANSPORT_RETRIES, or send a smaller image.")
    if isinstance(exc, requests.ConnectionError):
        return ("\n\nThe connection failed before a reply arrived, so nothing was generated "
                "or charged. Check connectivity, then any proxy or firewall between you and "
                "the API.")
    return ""


def request_with_retry(send, label="request", attempts=None):
    """Call `send()` (returning a Response), retrying 429s and 5xx with backoff.

    Stability documents its limit as 150 requests / 10 seconds; BFL does not document
    a 429 at all but can still throttle, so both packs retry defensively.

    A **transport** fault — TLS, DNS, a reset connection — gets its own, larger budget.
    It is a property of the link rather than of the request, so redialling is far more
    likely to succeed than another attempt against a 429 is, and it costs nothing when
    it fails because no request ever completed.
    """
    status_budget = attempts or max(1, setting_int("STRANGE_PETS_RETRIES", 3))
    transport_budget = max(status_budget, setting_int("STRANGE_PETS_TRANSPORT_RETRIES", 5))
    status_tries = transport_tries = 0
    delay = 1.0

    while True:
        try:
            response = send()
        except requests.RequestException as exc:
            transport_tries += 1
            if transport_tries >= transport_budget:
                raise
            wait = delay + random.uniform(0, 0.4)
            print("[strange-pets] {} transport error ({}); retrying in {:.1f}s ({}/{})".format(
                label, type(exc).__name__, wait, transport_tries, transport_budget - 1))
            time.sleep(wait)
            delay = min(delay * 2, 30.0)
            continue

        status_tries += 1
        if response.status_code not in RETRY_STATUSES or status_tries >= status_budget:
            return response

        wait = retry_after_seconds(response, delay) + random.uniform(0, 0.4)
        print("[strange-pets] {} returned HTTP {}; retrying in {:.1f}s ({}/{})".format(
            label, response.status_code, wait, status_tries, status_budget - 1))
        response.close()
        time.sleep(wait)
        delay = min(delay * 2, 30.0)


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
