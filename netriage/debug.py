"""Collect the raw material a bug report needs.

The parser is the part of this tool that breaks, and a screenshot never shows
what the driver actually returned. This dumps the commands we read, verbatim,
so a failure in the field can be reproduced at a desk.
"""

from __future__ import annotations

import platform

from . import __version__
from .util import have, run

# Read-only, and cheap enough to run before someone gives up waiting.
COMMANDS = [
    ("uname", ["uname", "-a"]),
    ("os-release", ["sh", "-c", "cat /etc/os-release 2>/dev/null"]),
    ("links", ["ip", "-br", "link"]),
    ("addresses", ["ip", "-br", "addr"]),
    ("routes", ["ip", "route"]),
    ("rfkill", ["rfkill", "list"]),
    ("pci network devices", ["sh", "-c", "lspci -nnk 2>/dev/null | grep -A3 -i -E 'network|wireless'"]),
    ("usb network devices", ["sh", "-c", "lsusb 2>/dev/null | grep -i -E 'wireless|wlan|802.11'"]),
]


def interface_commands(iface):
    """Commands whose output is specific to one interface."""
    return [
        ("iw dev", ["iw", "dev"]),
        (f"iw link {iface}", ["iw", "dev", iface, "link"]),
        (f"iw station dump {iface}", ["iw", "dev", iface, "station", "dump"]),
        (f"iw survey dump {iface}", ["iw", "dev", iface, "survey", "dump"]),
        (f"iw info {iface}", ["iw", "dev", iface, "info"]),
        (f"ethtool {iface}", ["ethtool", iface]),
        (f"ethtool -S {iface}", ["ethtool", "-S", iface]),
        (f"ethtool -i {iface}", ["ethtool", "-i", iface]),
        (f"nmcli device show {iface}", ["nmcli", "device", "show", iface]),
        (f"wpa_cli status {iface}", ["wpa_cli", "-i", iface, "status"]),
        (f"resolvectl dns {iface}", ["resolvectl", "dns", iface]),
        (f"lldpcli {iface}", ["lldpcli", "show", "neighbors", "ports", iface]),
    ]


def _section(title):
    return f"\n{'=' * 70}\n{title}\n{'=' * 70}\n"


def bundle(iface=None, facts=None):
    """A plain-text report: environment first, then every raw output."""
    parts = [
        _section("netriage debug bundle"),
        f"netriage     {__version__}\n"
        f"python       {platform.python_version()} ({platform.python_implementation()})\n"
        f"kernel       {platform.release()}\n"
        f"machine      {platform.machine()}\n"
        f"interface    {iface or '-'}\n",
        _section("tool availability"),
    ]

    for tool in ("ip", "iw", "ethtool", "nmcli", "wpa_cli", "dig", "ping",
                 "curl", "lldpcli", "resolvectl", "rfkill"):
        parts.append(f"{tool:<12}{'ok' if have(tool) else 'MISSING'}\n")

    commands = list(COMMANDS)
    if iface:
        commands += interface_commands(iface)

    for label, argv in commands:
        if not have(argv[0]) and argv[0] != "sh":
            parts.append(_section(f"{label}  -  {argv[0]} not installed"))
            continue
        code, out, err = run(argv, timeout=15)
        parts.append(_section(f"{label}  -  exit {code}"))
        parts.append((out or "<no output>") + "\n")
        if err:
            parts.append(f"stderr: {err}\n")

    if facts:
        parts.append(_section("parsed result"))
        for key in sorted(facts):
            parts.append(f"{key:<14}{facts[key]!r}\n")

    return "".join(parts)
