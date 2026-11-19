"""Wired link facts: negotiated speed, duplex, error counters, switch identity.

The useful evidence on a cable is different from wireless: there is no
signal, only whether the pairs negotiate correctly, whether frames arrive
intact, and whether the switch is willing to identify itself.
"""

from __future__ import annotations

import re

from .media import carrier
from .util import have, run


def link(iface):
    """Negotiated link parameters for a wired port."""
    info = {"iface": iface, "connected": carrier(iface)}

    if have("ethtool"):
        code, out, _ = run(["ethtool", iface], timeout=6)
        if code == 0:
            speed = re.search(r"Speed:\s*(\d+)Mb/s", out)
            duplex = re.search(r"Duplex:\s*(\w+)", out)
            autoneg = re.search(r"Auto-negotiation:\s*(\w+)", out)
            detected = re.search(r"Link detected:\s*(\w+)", out)

            info["connected"] = bool(detected and detected.group(1) == "yes")
            info["speed"] = int(speed.group(1)) if speed else None
            info["duplex"] = duplex.group(1).lower() if duplex else None
            info["autoneg"] = autoneg.group(1).lower() if autoneg else None
            return info

    # sysfs exposes the same numbers without ethtool, but as -1 when down
    try:
        with open(f"/sys/class/net/{iface}/speed", encoding="ascii") as handle:
            speed = int(handle.read().strip())
        info["speed"] = speed if speed > 0 else None
    except (OSError, ValueError):
        info["speed"] = None

    try:
        with open(f"/sys/class/net/{iface}/duplex", encoding="ascii") as handle:
            info["duplex"] = handle.read().strip().lower()
    except OSError:
        info["duplex"] = None
    return info


def counters(iface):
    """Physical-layer error counters from the driver."""
    if not have("ethtool"):
        return {}

    code, out, _ = run(["ethtool", "-S", iface], timeout=6)
    if code != 0:
        return {}

    stats = {}
    for line in out.splitlines():
        match = re.match(r"\s*([\w.-]+):\s*(\d+)\s*$", line)
        if not match:
            continue
        name, value = match.group(1), int(match.group(2))
        if re.search(r"err|crc|collision|drop|overrun|fifo|miss", name, re.I):
            if value:
                stats[name] = value
    return stats


def classify(counters):
    """Split driver counters into wire faults and checksum complaints."""
    physical, checksum = {}, {}
    for name, value in (counters or {}).items():
        if "checksum" in name.lower():
            checksum[name] = value
        else:
            physical[name] = value
    return physical, checksum


def packets(iface):
    """How many frames have crossed this port, for turning counts into rates."""
    total = 0
    found = False
    for name in ("rx_packets", "tx_packets"):
        try:
            with open(f"/sys/class/net/{iface}/statistics/{name}", encoding="utf-8") as handle:
                total += int(handle.read().strip())
                found = True
        except (OSError, ValueError):
            continue
    return total if found else None


def lldp(iface):
    """Who is on the other end of the cable, and on which port."""
    if not have("lldpcli"):
        return {}

    code, out, _ = run(["lldpcli", "show", "neighbors"], timeout=6)
    if code != 0 or not out.strip():
        return {}

    # lldpcli prints every port; on a multi-port machine the first block may
    # belong to a different interface than the one being tested.
    section = ""
    for block in out.split("Interface:"):
        if block.strip().startswith(iface):
            section = block
            break
    if section:
        out = section

    name = _first(r"SysName:\s*(\S+)", out) or _first(r"ChassisID:\s*\S+\s+(\S+)", out)
    port_id = _first(r"PortID:\s*(\S+)\s+(\S+)", out, "", group=2)
    port_descr = _first(r"PortDescr:\s*(.+)", out)
    vlan = _first(r"VLAN:\s*(\d+)", out)

    # A switch that never named its ports sends "PortID: local Port", which
    # leaves a word that looks like a truncation. Fall back to the description.
    if port_id.lower() in _PORT_NOISE:
        port_id = ""

    # PortID is the name you would type into the switch, so prefer it; the
    # description is only a fallback for switches that never named the port.
    port = port_id or port_descr
    if not name and not port:
        return {}
    return {"switch": name, "port": port, "vlan": vlan}


_PORT_NOISE = {"port", "local", "ifname", "mac", "ifalias", "agent", "unknown"}


def _first(pattern, text, default="", group=1):
    found = re.search(pattern, text)
    return found.group(group).strip() if found else default
