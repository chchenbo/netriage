"""Interface discovery and selection, for both media.

Kept separate from wifi.py and wired.py so neither has to know about the
other. Nothing here parses wireless or wired details; it only answers
"which interfaces exist, what kind are they, and which one should be tested".
"""

from __future__ import annotations

import glob
import os

from .util import run


def split_nmcli(line):
    """Split an nmcli -t line on unescaped colons."""
    parts, current, index = [], "", 0
    while index < len(line):
        char = line[index]
        if char == "\\" and index + 1 < len(line):
            current += line[index + 1]
            index += 2
            continue
        if char == ":":
            parts.append(current)
            current = ""
        else:
            current += char
        index += 1
    parts.append(current)
    return parts


def carrier(iface):
    try:
        with open(f"/sys/class/net/{iface}/carrier", encoding="ascii") as handle:
            return handle.read().strip() == "1"
    except OSError:
        return False


def is_wireless(iface):
    return bool(glob.glob(f"/sys/class/net/{iface}/wireless"))


def _by_type(kind):
    found = []
    code, out, _ = run(["nmcli", "-t", "-f", "DEVICE,TYPE", "device", "status"])
    if code == 0:
        for line in out.splitlines():
            parts = split_nmcli(line)
            if len(parts) >= 2 and parts[1] == kind:
                found.append(parts[0])
    if found:
        return found

    try:
        names = sorted(os.listdir("/sys/class/net"))
    except OSError:
        names = []          # not Linux: nothing to enumerate
    for iface in names:
        if iface == "lo":
            continue
        if kind == "wifi" and is_wireless(iface):
            found.append(iface)
        elif kind == "ethernet" and not is_wireless(iface):
            found.append(iface)
    return found


def wireless_interfaces():
    return _by_type("wifi")


def wired_interfaces():
    return [iface for iface in _by_type("ethernet")
            if not iface.startswith(("docker", "veth", "br-", "virbr", "tun", "tap"))]


def kind_of(iface):
    return "wifi" if is_wireless(iface) else "wired"


def pick(kind=None, explicit=None):
    """Choose an interface and report which medium it belongs to."""
    if explicit:
        return explicit, kind_of(explicit)

    if kind == "wired":
        candidates = wired_interfaces()
        for iface in candidates:
            if carrier(iface):
                return iface, "wired"
        return (candidates[0], "wired") if candidates else (None, None)

    if kind == "wifi":
        candidates = wireless_interfaces()
        for iface in candidates:
            if carrier(iface):
                return iface, "wifi"
        return (candidates[0], "wifi") if candidates else (None, None)

    for iface in wired_interfaces():
        if carrier(iface):
            return iface, "wired"
    for iface in wireless_interfaces():
        if carrier(iface):
            return iface, "wifi"
    for iface in wired_interfaces() + wireless_interfaces():
        return iface, kind_of(iface)
    return None, None


def all_interfaces():
    """Every interface the kernel knows about, for error messages."""
    try:
        return [name for name in os.listdir("/sys/class/net") if name != "lo"]
    except OSError:
        return []
