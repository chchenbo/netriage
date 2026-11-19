"""Long-running sampling: walk around, or sit still and wait for the fault.

Intermittent Wi-Fi problems are the hardest to catch because they look fine
when you arrive. This mode records everything with a timestamp so the moment
of failure is still there afterwards.
"""

from __future__ import annotations

import csv
import os
import statistics
import time
from datetime import datetime

from . import network, wifi

COLUMNS = [
    "time",
    "connected",
    "ssid",
    "rssi",
    "bssid",
    "channel",
    "busy_pct",
    "retry_pct",
    "gw_ms",
    "gw_loss",
    "net_ms",
    "net_loss",
]


def log_dir():
    from .util import log_dir as shared_log_dir

    return shared_log_dir()


def sample(iface, offline=False):
    """One observation. Cheap enough to run every second."""
    link = wifi.link(iface)
    survey_data = wifi.survey(iface, link.get("freq")) if link.get("connected") else {}
    station = wifi.station(iface) if link.get("connected") else {}
    net = network.address(iface)

    # While the link is down these would travel over another route and record
    # that route's latency as if it belonged to this connection.
    if link.get("connected"):
        gateway = (network.ping(iface, net["gateway"], count=2)
                   if net.get("gateway") else None)
        internet = None if offline else network.ping(iface, "1.1.1.1", count=2)
    else:
        gateway = internet = None

    return {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rssi": link.get("signal"),
        "bssid": link.get("bssid", ""),
        "channel": link.get("channel", 0),
        "busy_pct": survey_data.get("busy_pct"),
        "retry_pct": station.get("retry_pct"),
        "gw_ms": (gateway or {}).get("avg"),
        "gw_loss": (gateway or {}).get("loss"),
        "net_ms": (internet or {}).get("avg"),
        "net_loss": (internet or {}).get("loss"),
        "_connected": link.get("connected", False),
        "_ssid": link.get("ssid", ""),
    }


def _fits(text, width=70):
    return text if len(text) <= width else "\u2026" + text[-(width - 1):]


def run(iface, minutes=10.0, interval=5.0, out=None, offline=False, redact=False):
    from .util import short_path

    path = out or os.path.join(log_dir(), f"watch-{datetime.now():%Y%m%d-%H%M%S}.csv")

    def explain(where, reason):
        # the reason is the useful half, so the path is what gets shortened
        from .util import rule_width

        shown = short_path(where)
        text = f"Cannot write to {shown}: {reason}"
        excess = len(text) - (rule_width() - 2)
        if excess > 0:
            keep = max(8, len(shown) - excess - 1)
            shown = shown[:keep] + "\u2026"
            text = f"Cannot write to {shown}: {reason}"
        print(text)

    if os.path.isdir(path):
        explain(path, "it is a directory")
        return [], path

    parent = os.path.dirname(os.path.abspath(path))
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError as exc:
        explain(parent, exc.strerror)
        return [], path
    deadline = time.time() + minutes * 60
    rows = []

    print(f"Recording to {_fits(short_path(path))}")
    span = f"{minutes * 60:.0f}s" if minutes < 1 else f"{minutes:.0f} min"
    print(f"Sampling every {interval:.0f}s for {span}. Ctrl+C stops early.")
    print()

    try:
        handle = open(path, "w", newline="", encoding="utf-8")
    except OSError as exc:
        # a writable directory does not mean a writable file in it
        explain(path, exc.strerror)
        return [], path

    with handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()

        try:
            while time.time() < deadline:
                row = sample(iface, offline=offline)
                rows.append(row)
                record = {key: row.get(key) for key in COLUMNS}
                record["connected"] = "yes" if row.get("_connected") else "no"
                record["ssid"] = row.get("_ssid") or ""
                if redact:
                    record["ssid"] = "redacted" if record["ssid"] else ""
                    record["bssid"] = ("redacted" if record["bssid"] else "")
                writer.writerow(record)
                handle.flush()
                _print_row(row)
                time.sleep(max(0.2, interval))
        except KeyboardInterrupt:
            print("\nStopped.")

    print()
    _summary(rows, path)
    return rows, path


def _print_row(row):
    def cell(value, unit="", fmt="{}"):
        return "-" if value is None else fmt.format(value) + unit

    rssi = cell(row["rssi"])
    channel = cell(row["channel"])
    loss = cell(row["net_loss"], "%")
    latency = cell(row["net_ms"], "ms", "{:.0f}")
    busy = cell(row["busy_pct"], "%", "{:.0f}")
    flag = "" if row["_connected"] else "  <-- DOWN"

    if not row["_connected"]:
        # a sample taken while disconnected has no link quality to report
        rssi = channel = loss = latency = busy = "-"

    print(f"{row['time'][11:]}  rssi {rssi:>4}  ch {channel:>3}  "
          f"busy {busy:>4}  loss {loss:>4}  rtt {latency:>6}{flag}")


def _summary(rows, path):
    if not rows:
        print("No samples recorded.")
        return

    rssi = [row["rssi"] for row in rows if row["rssi"] is not None]
    latencies = [row["net_ms"] for row in rows if row["net_ms"] is not None]
    losses = [row["net_loss"] for row in rows if row["net_loss"] is not None]
    gaps = sum(1 for row in rows if not row["_connected"])
    drops = 0
    previous_up = True
    for row in rows:
        if previous_up and not row["_connected"]:
            drops += 1
        previous_up = bool(row["_connected"])

    roams = 0
    previous = None
    for row in rows:
        if row["bssid"] and previous and row["bssid"] != previous:
            roams += 1
        if row["bssid"]:
            previous = row["bssid"]

    print("=" * 52)
    print(f"Samples          {len(rows)}")
    first, last = rows[0]["time"], rows[-1]["time"]
    try:
        span = datetime.strptime(last, "%Y-%m-%d %H:%M:%S") - datetime.strptime(
            first, "%Y-%m-%d %H:%M:%S"
        )
        duration = f"{span.total_seconds() / 60:.1f} min ({first[11:]} to {last[11:]})"
    except ValueError:
        duration = f"{len(rows)} samples"
    print(f"Duration         {duration}")
    if rssi:
        print(f"Signal           min {min(rssi)} / avg {statistics.mean(rssi):.0f} / max {max(rssi)} dBm")
    if latencies:
        print(f"Latency          avg {statistics.mean(latencies):.0f} ms / "
              f"worst {max(latencies):.0f} ms")
    if losses:
        print(f"Packet loss      avg {statistics.mean(losses):.1f}% / worst {max(losses)}%")
    print(f"Disconnected     {gaps} of {len(rows)} samples")
    print(f"Drops            {drops}")
    print(f"AP changes       {roams}")
    from .util import short_path as _short

    print(f"CSV              {_fits(_short(path))}")
    print("=" * 52)

    if drops:
        print("The link dropped. Search the CSV for the first row after the gap and")
        print("correlate it with the time you saw the failure.")
    if roams > 3:
        print("Frequent AP changes: the client is roaming aggressively or ping-ponging.")
