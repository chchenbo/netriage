"""Reading back the reports that were written to disk.

Runs are stored one JSON file per invocation, named by date and sequence, so
the file order is already the chronological order.
"""

from __future__ import annotations

import getpass
import glob
import json
import os
from datetime import datetime

from . import __version__
from .util import log_dir, short_path


def paths():
    """Every saved report, oldest first."""
    return sorted(glob.glob(os.path.join(log_dir(), "netriage-*.json")))


def load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def entries(with_problems=False):
    """(number, path, facts) oldest first."""
    found, problems = [], []
    for number, path in enumerate(paths(), 1):
        try:
            facts = load(path)
        except PermissionError:
            problems.append((path, "permission denied"))
            continue
        except OSError as exc:
            problems.append((path, exc.strerror or "cannot be read"))
            continue
        except ValueError:
            problems.append((path, "not valid JSON"))
            continue
        found.append((number, path, facts))
    return (found, problems) if with_problems else found


def number_of(path):
    """The run id for a path, or None when it is not one of ours."""
    for number, candidate in enumerate(paths(), 1):
        if os.path.abspath(candidate) == os.path.abspath(path):
            return number
    return None


def by_number(number):
    """One saved run by its number, or None."""
    for candidate, path, facts in entries():
        if candidate == number:
            return path, facts
    return None


# ------------------------------------------------------------ writing

def next_index(directory, stamp):
    """Next free sequence number for today: netriage-YYYYMMDD-0001.json."""
    highest = 0
    for path in glob.glob(os.path.join(directory, f"netriage-{stamp}-*.json")):
        suffix = os.path.basename(path)[:-5].rsplit("-", 1)[-1]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return highest + 1


def report_path(kind="quick"):
    """Where the next report for this command would go."""
    directory = log_dir()
    stamp = datetime.now().strftime("%Y%m%d")
    return os.path.join(directory,
                        f"netriage-{stamp}-{next_index(directory, stamp):04d}.json")


def save(facts, kind="quick", path=None):
    path = path or report_path(kind)
    payload = dict(facts)
    payload.setdefault("command", kind)
    payload.setdefault("version", __version__)
    payload.setdefault("user", os.environ.get("SUDO_USER") or getpass.getuser())

    # write-then-rename: a crash mid-write must not leave a half report that
    # looks like a corrupt one
    temporary = path + ".part"
    handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    # Under sudo the file would be root-owned and mode 0600, and the human
    # could not read it back: `netriage history` would silently skip every run
    # made with sudo. Hand the file to whoever invoked us.
    _give_to_invoking_user(handle)
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False, default=str)
    os.replace(temporary, path)
    return path


def _invoking_user():
    """(uid, gid) of the human behind sudo, or None."""
    if os.geteuid() != 0:
        return None
    name = os.environ.get("SUDO_USER")
    if not name or name == "root":
        return None
    try:
        import pwd

        entry = pwd.getpwnam(name)
    except (KeyError, ImportError):
        return None
    return entry.pw_uid, entry.pw_gid


def _give_to_invoking_user(handle):
    owner = _invoking_user()
    if owner is None:
        return
    try:
        os.fchown(handle, *owner)
    except OSError:
        pass


def read(path):
    """(facts, None) or (None, message)."""
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle), None
    except OSError as exc:
        return None, f"Cannot read {short_path(path)}: {exc.strerror}"
    except ValueError:
        return None, f"{short_path(path)} is not a report written by netriage."


def latest():
    """The newest report that can actually be read."""
    for path in reversed(paths()):
        try:
            load(path)
        except (OSError, ValueError):
            continue
        return path
    return None


def recent(count):
    """The *count* newest report paths, newest first."""
    return list(reversed(paths()))[:count]


def resolve_run(token):
    """A saved run from either a run number or a file name."""
    text = str(token).strip()
    if text.isdigit():
        found = by_number(int(text))
        if not found:
            newest = len(paths())
            hint = (f" The newest run is {newest}." if newest else
                    " There are no saved runs yet.")
            return None, f"No run numbered {text}.{hint}"

        return found, None

    path = text
    if not os.path.isabs(path) and not os.path.exists(path):
        # a bare file name should still find the report directory
        candidate = os.path.join(log_dir(), path)
        if os.path.exists(candidate):
            path = candidate
    facts, problem = read(path)
    if problem:
        return None, problem
    return (path, facts), None
