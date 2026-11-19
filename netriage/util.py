"""Small helpers: command execution, colour, number formatting.

Standard library only, so the tool runs on a fresh Ubuntu box with nothing
installed beyond `iw`, `iproute2` and `dnsutils`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

# ---------------------------------------------------------------- colour

_COLOUR_NAMES = ("RESET", "BOLD", "DIM", "RED", "GREEN", "YELLOW", "BLUE", "CYAN")
_USE_COLOR = sys.stdout.isatty()

# The accent: a cyan-blue that reads the same everywhere instead of whatever
# cyan the terminal theme happens to define for ANSI 36.
ACCENT_RGB = (145, 198, 203)


def _accent():
    """True colour when the terminal says it can, ANSI cyan otherwise."""
    colourterm = os.environ.get("COLORTERM", "").lower()
    if colourterm in ("truecolor", "24bit"):
        red, green, blue = ACCENT_RGB
        return f"\033[38;2;{red};{green};{blue}m"
    return "\033[36m"


def _apply_color(enabled):
    """Set the constants and push them into every loaded netriage module."""
    global RESET, BOLD, DIM, RED, GREEN, YELLOW, BLUE, CYAN
    RESET = "\033[0m" if enabled else ""
    BOLD = "\033[1m" if enabled else ""
    DIM = "\033[2m" if enabled else ""
    RED = "\033[31m" if enabled else ""
    GREEN = "\033[32m" if enabled else ""
    YELLOW = "\033[33m" if enabled else ""
    BLUE = "\033[34m" if enabled else ""
    CYAN = _accent() if enabled else ""

    for name, module in list(sys.modules.items()):
        if name != "netriage" and not name.startswith("netriage."):
            continue
        for attribute in _COLOUR_NAMES:
            if hasattr(module, attribute):
                setattr(module, attribute, globals()[attribute])


_apply_color(_USE_COLOR)


def disable_color():
    global _USE_COLOR
    _USE_COLOR = False
    _apply_color(False)


def enable_color():
    global _USE_COLOR
    _USE_COLOR = True
    _apply_color(True)


def have(command):
    """True when *command* is on PATH."""
    return shutil.which(command) is not None


def run(cmd, timeout=10):
    """Run a command and never raise. Returns (rc, stdout, stderr)."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except FileNotFoundError:
        return 127, "", "command not found"
    except OSError as exc:
        return 1, "", str(exc)


# ---------------------------------------------------------------- format


def bar(value, maximum, width=20, fill="#", empty="."):
    if maximum <= 0:
        return empty * width
    filled = int(round(width * max(0.0, min(1.0, value / maximum))))
    return fill * filled + empty * (width - filled)


def fmt_rssi(rssi):
    """Colour an RSSI value by how usable it is."""
    if rssi is None:
        return f"{DIM}n/a{RESET}"
    text = f"{rssi} dBm"
    if rssi >= -60:
        return f"{GREEN}{text}{RESET}"
    if rssi >= -70:
        return f"{YELLOW}{text}{RESET}"
    return f"{RED}{text}{RESET}"


def fmt_pct(value):
    if value is None:
        return f"{DIM}n/a{RESET}"
    text = f"{value:.0f}%"
    if value < 40:
        return f"{GREEN}{text}{RESET}"
    if value < 65:
        return f"{YELLOW}{text}{RESET}"
    return f"{RED}{text}{RESET}"


RULE_WIDTH = 72


def rule_width():
    """How wide to draw the section rules."""
    try:
        columns = shutil.get_terminal_size((80, 24)).columns
    except OSError:
        columns = 80
    return max(30, min(RULE_WIDTH, columns - 2))


def section(title):
    """A heading in the accent colour, with a rule sized to the terminal."""
    print()
    print(f"{BOLD}{CYAN}{title}{RESET}")
    print(f"{DIM}{'-' * max(len(title), rule_width())}{RESET}")


# ---------------------------------------------------------------- paths


def user_home():
    """The home of the human who invoked us, even under sudo."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and sudo_user != "root":
        try:
            import pwd

            return pwd.getpwnam(sudo_user).pw_dir
        except (KeyError, ImportError):
            pass
    return os.path.expanduser("~")


def claim_to_user(path):
    """Under sudo, hand a file or directory to the human who invoked us."""
    if os.geteuid() != 0:
        return
    name = os.environ.get("SUDO_USER")
    if not name or name == "root":
        return
    try:
        import pwd

        entry = pwd.getpwnam(name)
        os.chown(path, entry.pw_uid, entry.pw_gid)
    except (KeyError, ImportError, OSError):
        pass


def config_dir():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(user_home(), ".config")
    return os.path.join(base, "netriage")


def config_path():
    return os.path.join(config_dir(), "config.json")


def configured_data_dir():
    """The directory remembered by --data-dir, or None."""
    try:
        with open(config_path(), encoding="utf-8") as handle:
            value = json.load(handle).get("data_dir")
    except (OSError, ValueError):
        return None
    return value if isinstance(value, str) and value else None


def remember_data_dir(path):
    """Store the default location. *path* is None to forget it."""
    target = config_path()
    folder = os.path.dirname(target)
    try:
        os.makedirs(folder, mode=0o700, exist_ok=True)
    except OSError:
        return None
    claim_to_user(folder)

    if path is None:
        try:
            os.remove(target)
        except OSError:
            pass
        return None

    try:
        handle = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    except OSError:
        return None
    claim_to_user_fd = getattr(os, "fchown", None)
    if os.geteuid() == 0 and claim_to_user_fd:
        name = os.environ.get("SUDO_USER")
        if name and name != "root":
            try:
                import pwd

                entry = pwd.getpwnam(name)
                os.fchown(handle, entry.pw_uid, entry.pw_gid)
            except (KeyError, ImportError, OSError):
                pass
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        json.dump({"data_dir": path}, stream, indent=2)
    return path


def log_dir():
    """Where reports and recordings are written."""
    override = os.environ.get("NETRIAGE_DATA_DIR") or configured_data_dir()
    if override:
        base = os.path.expanduser(override)
    else:
        documents = os.path.join(user_home(), "Documents")
        base = (os.path.join(documents, "netriage-log")
                if os.path.isdir(documents)
                else os.path.join(user_home(), ".local", "share", "netriage", "logs"))

    path = base
    # a config left root-owned by an older sudo run becomes readable again
    _claim_config()
    _migrate_older_logs(path)

    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        # another user on the same machine must not read the saved SSIDs
        os.chmod(path, 0o700)
    except OSError:
        pass

    sudo_user = os.environ.get("SUDO_USER")
    if os.geteuid() == 0 and sudo_user and sudo_user != "root":
        try:
            import pwd

            entry = pwd.getpwnam(sudo_user)
            for directory in (path,):
                os.chown(directory, entry.pw_uid, entry.pw_gid)
            # reports written by an older version are still root-owned, and
            # the human cannot read them; this run can repair them
            for name in os.listdir(path):
                try:
                    os.chown(os.path.join(path, name), entry.pw_uid, entry.pw_gid)
                except OSError:
                    continue
        except (KeyError, ImportError, OSError):
            pass
    return path


def _claim_config():
    if os.geteuid() != 0:
        return
    for path in (config_dir(), config_path()):
        if os.path.exists(path):
            claim_to_user(path)


def _migrate_older_logs(target):
    """Move reports from the places earlier versions used, once."""
    if os.path.isdir(target) and os.listdir(target):
        return
    home = user_home()
    for legacy in (os.path.join(home, ".local", "share", "netriage", "logs"),
                   os.path.join(home, "netriage", "netriage_logs")):
        if legacy == target or not os.path.isdir(legacy):
            continue
        try:
            os.makedirs(target, exist_ok=True)
            for name in os.listdir(legacy):
                os.replace(os.path.join(legacy, name), os.path.join(target, name))
            os.rmdir(legacy)
        except OSError:
            continue
        break


def short_path(path):
    """Write the home directory as ~, which is shorter and familiar."""
    home = user_home()
    if home and path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


# ---------------------------------------------------------------- progress


class CookTimer:
    """A live "cooking for Ns..." line that erases itself when the work ends."""

    def __init__(self, label="cooking", stream=None, enabled=True):
        self.label = label
        self.stream = stream or sys.stdout
        self.enabled = enabled and self.stream.isatty()
        self._start = 0.0
        self._phase_start = 0.0
        self._stop = threading.Event()
        self._thread = None

    def set_label(self, label):
        """Switch phase; the counter restarts, so it describes this step."""
        self.label = label
        self._phase_start = time.monotonic()
        if self.enabled:
            self._draw()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
        return False

    def start(self):
        self._start = time.monotonic()
        self._phase_start = self._start
        if not self.enabled:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop.wait(0.2):
            self._draw()

    def _draw(self):
        seconds = self.phase_seconds
        # "cooking for 0s" reads as broken; drop the counter for the first
        # second and just show that work is happening.
        line = f"{self.label}..." if seconds == 0 else f"{self.label} for {seconds}s..."
        self.stream.write("\r" + line + "\033[K")
        self.stream.flush()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self.enabled:
            # Erase the progress line, then reclaim the blank line the header
            # left for it, so the report starts where the header ended and the
            # footer never gains a stray empty row.
            self.stream.write("\r\033[K\033[A\r\033[K")
            self.stream.flush()

    @property
    def seconds(self):
        """Total elapsed time, used for the closing summary."""
        if not self._start:
            return 0
        return int(round(time.monotonic() - self._start))

    @property
    def phase_seconds(self):
        """Elapsed time in the current phase, used for the live line."""
        if not self._phase_start:
            return 0
        return int(round(time.monotonic() - self._phase_start))


# ---------------------------------------------------------------- redaction

_MAC = re.compile(r"\b([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b")
_IPV4 = re.compile(r"\b(\d{1,3})\.(\d{1,3})\.\d{1,3}\.\d{1,3}\b")


def _mask_mac(match):
    parts = match.group(0).split(":")
    return ":".join(parts[:3] + ["xx", "xx", "xx"])


def _mask_ip(match):
    return f"{match.group(1)}.{match.group(2)}.x.x"


def redact(value, key=""):
    """Mask MACs, addresses and names so a report can leave the building."""
    if isinstance(value, dict):
        return {name: redact(item, name) for name, item in value.items()}
    if isinstance(value, list):
        return [redact(item, key) for item in value]
    if isinstance(value, str):
        if key in ("ssid", "label", "switch"):
            return "redacted"
        if key == "user":
            return "redacted"
        text = _MAC.sub(_mask_mac, value)
        text = _IPV4.sub(_mask_ip, text)
        return text
    return value


# Anything that names a network or a person, in either "Key: value" (iw,
# NetworkManager) or "key=value" (wpa_cli status) form.
_SECRET_LINES = re.compile(
    r"(?im)^(\s*(?:SSID|ssid|identity|anonymous_identity|domain_suffix_match)"
    r"\s*[:=]\s*).*$"
)


def redact_text(text):
    """Mask identifiers inside raw tool output."""
    if not text:
        return text
    text = _IPV4.sub(_mask_ip, text)
    text = _MAC.sub(_mask_mac, text)
    text = _SECRET_LINES.sub(r"\1redacted", text)
    return text


def read_metric(facts, path):
    """A number from a nested report, or None when it is not a number."""
    value = facts
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return None


def shorten(text, width):
    if len(text) <= width:
        return text
    return text[: max(1, width - 1)] + "\u2026"


def shorten_middle(text, width):
    """Keep both ends: "MagentaWLAN-P2S3_5GHz" and "..._2.4GHz" differ at the tail."""
    if len(text) <= width:
        return text
    head = max(1, (width - 1) // 2)
    tail = max(1, width - 1 - head)
    return text[:head] + "\u2026" + text[-tail:]
