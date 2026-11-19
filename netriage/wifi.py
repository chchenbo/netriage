"""Wireless facts, read from the kernel through iw and nmcli.

Everything here is parsing. No policy and no verdicts: those live in
diagnose.py so they can be read and changed on their own.
"""

from __future__ import annotations

import re

from .media import split_nmcli
from .util import have, run


# ---------------------------------------------------------------- helpers


def proc_wireless(iface):
    """Fallback source for signal and noise when the driver lacks survey."""
    try:
        with open("/proc/net/wireless", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return {}

    for line in lines:
        parts = line.split()
        if len(parts) < 4 or not parts[0].endswith(":"):
            continue
        if parts[0].rstrip(":") != iface:
            continue
        # Layout: name: status link_quality level noise ...
        # Index 2 is a quality score, not dBm; level and noise follow it.
        if len(parts) < 5:
            continue
        try:
            level = float(parts[3].rstrip("."))
            noise = float(parts[4].rstrip("."))
        except ValueError:
            continue
        info = {}
        if level > -200:
            info["signal"] = int(round(level))
        if noise > -200:
            info["noise"] = int(round(noise))
        return info
    return {}


def channel_of(freq):
    try:
        freq = int(float(freq))
    except (TypeError, ValueError):
        return 0
    if freq == 2484:
        return 14
    if 2412 <= freq <= 2472:
        return (freq - 2407) // 5
    if 5000 <= freq <= 5900:
        return (freq - 5000) // 5
    if 5925 <= freq <= 7125:
        # 6 GHz: channel 1 is centred on 5955 MHz, so the offset is
        # 5950 + 5*channel with no additional +1.
        return (freq - 5950) // 5
    return 0


def band_of(freq):
    try:
        freq = int(float(freq))
    except (TypeError, ValueError):
        return "?"
    if freq < 2500:
        return "2.4 GHz"
    if freq < 5925:
        return "5 GHz"
    return "6 GHz"


def have_iw():
    """The tool is useless without iw; fail early with a clear message."""
    return have("iw")


# ---------------------------------------------------------------- link


def link(iface):
    """Current association: SSID, BSSID, frequency, signal, negotiated rate."""
    info = {"iface": iface, "connected": False}
    code, out, _ = run(["iw", "dev", iface, "link"], timeout=6)
    if code != 0 or "Not connected" in out:
        return info

    info["connected"] = True
    info["bssid"] = _first(r"Connected to ([0-9a-fA-F:]{17})", out)
    info["ssid"] = _first(r"SSID:\s*(.+)", out)
    freq = _first(r"freq:\s*([\d.]+)", out)
    info["freq"] = float(freq) if freq else 0.0
    info["channel"] = channel_of(info["freq"])
    info["band"] = band_of(info["freq"])

    signal = _first(r"signal:\s*(-?\d+)", out)
    info["signal"] = int(signal) if signal else None

    rate = _first(r"tx bitrate:\s*([\d.]+)", out)
    info["tx_rate"] = float(rate) if rate else None

    # Attribute the width to a specific line: rx and tx can differ, and Wi-Fi 6
    # prints the bandwidth as "HE-BW 80" rather than "80MHz".
    info["width_mhz"] = _line_width(out, "tx bitrate:") or _line_width(out, "rx bitrate:")
    return info


def _line_width(output, prefix):
    """Channel width from the bitrate line that starts with *prefix*."""
    for line in output.splitlines():
        if line.strip().startswith(prefix):
            match = re.search(r"(\d+)\s*MHz", line) or re.search(r"BW\s+(\d+)", line)
            return int(match.group(1)) if match else None
    return None


def station(iface):
    """Driver counters for the current association: retries and failures."""
    info = {}
    code, out, _ = run(["iw", "dev", iface, "station", "dump"], timeout=6)
    if code != 0:
        return info

    retries = _first(r"tx retries:\s*(\d+)", out)
    failed = _first(r"tx failed:\s*(\d+)", out)
    packets = _first(r"tx packets:\s*(\d+)", out)
    beacon = _first(r"beacon loss:\s*(\d+)", out)
    signal = _first(r"signal:\s*(-?\d+)", out)

    # _first returns "" when a line is absent, so test truthiness, not identity.
    if retries:
        info["tx_retries"] = int(retries)
    if failed:
        info["tx_failed"] = int(failed)
    if packets:
        info["tx_packets"] = int(packets)
    if beacon:
        info["beacon_loss"] = int(beacon)
    if signal:
        info["signal"] = int(signal)

    if info.get("tx_packets"):
        if "tx_retries" in info:
            info["retry_pct"] = round(100.0 * info["tx_retries"] / info["tx_packets"], 1)
        # Report a failure rate only when the driver actually exposes the
        # counter, otherwise 0% would read as "no failures" rather than
        # "not measured".
        if "tx_failed" in info:
            info["failed_pct"] = round(100.0 * info["tx_failed"] / info["tx_packets"], 1)
    return info


def survey(iface, freq=None):
    """Channel utilisation and noise floor, with graceful degradation."""
    info = {}

    code, out, _ = run(["iw", "dev", iface, "survey", "dump"], timeout=8)
    blocks = [b for b in out.split("Survey data from") if b.strip()] if code == 0 else []

    chosen = None
    if blocks:
        if freq:
            for block in blocks:
                block_freq = _first(r"frequency:\s*(\d+)", block)
                if block_freq and abs(int(block_freq) - int(freq)) < 100:
                    chosen = block
                    break
        if chosen is None:
            for block in blocks:
                if "[in use]" in block:
                    chosen = block
                    break
        if chosen is None:
            def active_time(block):
                value = _first(r"channel active time:\s*(\d+)", block)
                return int(value) if value else 0
            chosen = max(blocks, key=active_time)

    if chosen is not None:
        noise = _first(r"noise:\s*(-?\d+)", chosen)
        active = _first(r"channel active time:\s*(\d+)", chosen)
        busy = _first(r"channel busy time:\s*(\d+)", chosen)
        rx_time = _first(r"channel receive time:\s*(\d+)", chosen)
        tx_time = _first(r"channel transmit time:\s*(\d+)", chosen)

        if noise:
            info["noise"] = int(noise)
        if active and int(active) > 0:
            info["active_ms"] = int(active)
            if busy:
                info["busy_pct"] = round(100.0 * int(busy) / int(active), 1)
            if rx_time:
                info["rx_pct"] = round(100.0 * int(rx_time) / int(active), 1)
            if tx_time:
                info["tx_pct"] = round(100.0 * int(tx_time) / int(active), 1)

    fallback = proc_wireless(iface)
    if "noise" not in info and "noise" in fallback:
        info["noise"] = fallback["noise"]
    if "signal" not in info and "signal" in fallback:
        info["proc_signal"] = fallback["signal"]
    return info


# ---------------------------------------------------------------- scan


def unique(aps, connected_bssid=None):
    """One entry per access point."""
    best = {}
    for ap in aps or []:
        key = (ap.get("bssid") or "").lower() or f"?{len(best)}"

        def score(item):
            connected = (item.get("bssid") or "").lower() == (connected_bssid or "").lower()
            return (connected, item.get("util_pct") is not None,
                    item.get("source") != "nmcli")

        current = best.get(key)
        if current is None or score(ap) > score(current):
            best[key] = ap
    return list(best.values())


def trust_live_signal(aps, link):
    """Take the connected BSSID's signal from the link, not from the scan."""
    bssid = (link or {}).get("bssid")
    signal = (link or {}).get("signal")
    if not bssid or signal is None:
        return aps
    for ap in aps:
        if (ap.get("bssid") or "").lower() == bssid.lower():
            ap["signal"] = float(signal)
    return aps


def scan(iface, timeout=25):
    """Visible access points, merged from iw and nmcli."""
    aps = _scan_iw(iface, timeout)
    # BSSIDs are case-insensitive: iw prints lower case, nmcli upper case, and
    # a case-sensitive comparison listed every access point twice.
    seen = {(ap["bssid"] or "").lower() for ap in aps}
    for extra in _scan_nmcli(rescan=len(aps) < 3):
        bssid = (extra.get("bssid") or "").lower()
        if bssid and bssid in seen:
            continue
        seen.add(bssid)
        aps.append(extra)
    return aps


def _scan_iw(iface, timeout):
    # "flush" asks the driver to drop its cached BSS list first; without it a
    # card that is associated often answers with the network it is on and
    # nothing else.
    code, out, _ = run(["iw", "dev", iface, "scan", "flush"], timeout=timeout)
    if code != 0 or not out.strip().startswith("BSS"):
        code, out, _ = run(["iw", "dev", iface, "scan"], timeout=timeout)
    if code != 0:
        return []

    aps, current = [], None
    for raw in out.splitlines():
        line = raw.rstrip()
        if line.startswith("BSS "):
            if current:
                aps.append(current)
            current = {"bssid": _first(r"BSS ([0-9a-fA-F:]{17})", line), "ssid": ""}
            continue
        if current is None:
            continue
        stripped = line.strip()
        if stripped.startswith("freq:"):
            current["freq"] = float(_first(r"freq:\s*([\d.]+)", stripped) or 0)
        elif stripped.startswith("signal:"):
            current["signal"] = float(_first(r"signal:\s*(-?[\d.]+)", stripped) or 0)
        elif stripped.startswith("SSID:"):
            current["ssid"] = printable(unescape_ssid(stripped[5:].strip()))
        elif "station count:" in stripped:
            current["stations"] = int(_first(r"station count:\s*(\d+)", stripped) or 0)
        elif "channel utilisation:" in stripped:
            value = _first(r"channel utilisation:\s*(\d+)", stripped)
            if value:
                current["util_pct"] = round(int(value) * 100.0 / 255, 1)
        elif stripped.startswith("RSN:"):
            current["rsn"] = True
        elif stripped.startswith("WPA:"):
            current["wpa"] = True
        elif "IEEE 802.1X" in stripped:
            current["enterprise"] = True
        elif "Authentication suites" in stripped:
            # "PSK SAE" is one line, so these are two ifs, not elif.
            if "SAE" in stripped:
                current["sae"] = True
            if "PSK" in stripped:
                current["psk"] = True
            if "OWE" in stripped:
                current["owe"] = True
        elif stripped.startswith("capability:"):
            # "ESS Privacy" with no RSN and no WPA element is WEP
            current["privacy"] = "Privacy" in stripped

    for ap in aps + ([current] if current else []):
        # WEP advertises the privacy bit and nothing else
        if ap.pop("privacy", False) and not (ap.get("rsn") or ap.get("wpa")
                                             or ap.get("owe")):
            ap["wep"] = True

    if current:
        aps.append(current)

    for ap in aps:
        ap.setdefault("freq", 0.0)
        ap.setdefault("signal", 0.0)
        ap["channel"] = channel_of(ap["freq"])
        ap["band"] = band_of(ap["freq"])
    return [ap for ap in aps if ap["channel"]]


def _scan_nmcli(rescan=False):
    """Second opinion that works without root, at the cost of BSS Load data."""
    if rescan:
        # ask NetworkManager for a fresh sweep; it is cheap and silent if busy
        run(["nmcli", "device", "wifi", "rescan"], timeout=10)

    code, out, _ = run(
        ["nmcli", "-t", "-f", "BSSID,SSID,CHAN,SIGNAL,SECURITY", "device", "wifi", "list"],
        timeout=25,
    )
    if code != 0:
        return []

    aps = []
    for line in out.splitlines():
        parts = split_nmcli(line)
        if len(parts) < 4:
            continue
        quality = parts[3]
        try:
            rssi = int(round(int(quality) / 2.0 - 100))
        except ValueError:
            rssi = 0
        channel = int(parts[2]) if parts[2].isdigit() else 0
        aps.append(
            {
                "bssid": parts[0],
                "ssid": parts[1],
                "channel": channel,
                "freq": 0.0,
                "band": "2.4 GHz" if 0 < channel <= 14 else "5 GHz" if channel else "?",
                "signal": rssi,
                "security": parts[4] if len(parts) > 4 else "",
                "source": "nmcli",
            }
        )
    return aps


def normalize_security(raw):
    """Turn nmcli's free-form security text into one stable label."""
    text = (raw or "").strip()
    if not text or text in ("--", "open", "none"):
        return "open"

    # Both protect the link even though neither is WPA; calling them "open"
    # would tell the user their traffic is unprotected when it is not.
    upper = text.upper()
    if "OWE" in upper:
        return "OWE"
    if "WEP" in upper:
        return "WEP"

    enterprise = "802.1X" in text
    if "WPA3" in text and "WPA2" in text:
        base = "WPA2/WPA3"
    elif "WPA3" in text:
        base = "WPA3"
    elif "WPA2" in text:
        base = "WPA2"
    elif "WPA" in text:
        base = "WPA"
    elif "SAE" in upper:
        base = "WPA3"          # SAE is the WPA3 handshake under its own name
    else:
        return text
    return f"{base}-802.1X" if enterprise else f"{base}-PSK"


def security_label(ap):
    """Human-readable security, which decides how credentials are supplied."""
    if ap.get("security"):
        return normalize_security(ap["security"])
    if ap.get("owe"):
        return "OWE"
    if ap.get("wep"):
        return "WEP"
    if ap.get("sae") and ap.get("psk"):
        base = "WPA2/WPA3"
    elif ap.get("sae"):
        base = "WPA3"
    elif ap.get("rsn"):
        base = "WPA2"
    elif ap.get("wpa"):
        base = "WPA"
    else:
        return "open"

    if ap.get("enterprise"):
        return f"{base}-802.1X"
    return f"{base}-PSK"


def band_of_ap(ap):
    """The band an access point belongs to, from whatever the scan recorded."""
    label = ap.get("band")
    if label:
        return label
    freq = ap.get("freq")
    if freq:
        try:
            return band_of(float(freq))
        except (TypeError, ValueError):
            pass
    channel = ap.get("channel") or 0
    # without a frequency this is a guess, and only a safe one below 14
    return "2.4 GHz" if 0 < channel <= 14 else ""


def channel_load(aps, band=None):
    """Group access points by channel and summarise each one."""
    table = {}
    for ap in aps:
        if band and band_of_ap(ap) != band:
            continue
        channel = ap.get("channel") or 0
        if not channel:
            continue
        entry = table.setdefault(
            channel,
            {"channel": channel, "count": 0, "best": -200.0, "util": [], "stations": 0},
        )
        entry["count"] += 1
        entry["best"] = max(entry["best"], ap.get("signal", -200.0))
        if "util_pct" in ap:
            entry["util"].append(ap["util_pct"])
        entry["stations"] += ap.get("stations", 0)
    for entry in table.values():
        entry["util_avg"] = round(sum(entry["util"]) / len(entry["util"]), 1) if entry["util"] else None
    return table


def printable(text):
    """Drop control characters from an SSID."""
    return "".join(ch for ch in text if ch.isprintable() or ch == " ")


def unescape_ssid(text):
    """Turn iw's byte escapes back into characters."""
    if "\\x" not in text:
        return text

    out = bytearray()
    index = 0
    while index < len(text):
        if text.startswith("\\x", index) and index + 3 < len(text):
            try:
                out.append(int(text[index + 2:index + 4], 16))
                index += 4
                continue
            except ValueError:
                pass
        out.extend(text[index].encode("utf-8"))
        index += 1
    return out.decode("utf-8", errors="replace")


def _first(pattern, text, default=""):
    found = re.search(pattern, text)
    return found.group(1).strip() if found else default
