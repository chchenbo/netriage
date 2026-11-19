"""The Markdown write-up of one saved run.

Kept out of cli.py so the document can be tested without a terminal, and so
the command layer stays about commands.
"""

from __future__ import annotations

from . import wifi
from .util import short_path


def cell(text):
    """Keep a value from breaking the table or starting a code span."""
    cleaned = wifi.printable(str(text if text is not None else "-"))
    return cleaned.replace("|", "\\|").replace("`", "'")


def value(text, unit=""):
    """A missing measurement is unknown, never zero."""
    if text is None or text == "":
        return "unknown"
    if isinstance(text, bool):
        return "yes" if text else "no"
    if isinstance(text, float):
        return f"{text:g}{unit}" if unit else f"{text:g}"
    return f"{text}{unit}"


def measurement_rows(facts):
    """Every number the run produced, so the document stands on its own."""
    link = facts.get("link") or {}
    station = facts.get("station") or {}
    survey = facts.get("survey") or {}
    gateway = facts.get("ping_gateway") or {}
    internet = facts.get("ping_internet") or {}
    speed = facts.get("speed") or {}
    upload = facts.get("upload") or {}
    latency = facts.get("latency") or {}
    school = facts.get("dns_local") or facts.get("dns_school") or {}
    public = facts.get("dns_public") or {}
    tools = facts.get("tools") or {}
    offline = facts.get("offline")

    def skipped_or(number, source, unit=""):
        if isinstance(source, dict) and source.get("skipped"):
            return "not tested (offline)" if offline else "not tested"
        if isinstance(source, dict) and source.get("error"):
            return "unknown (test failed)"
        return value(number, unit)

    rows = []
    if facts.get("medium") == "wired":
        rows += [
            ("Speed", value(link.get("speed"), " Mb/s")),
            ("Duplex", value(link.get("duplex"))),
            ("Frame errors", ("unknown (ethtool not installed)"
                              if not tools.get("ethtool", True)
                              else value(sum((facts.get("counters") or {}).values())
                                         if facts.get("counters") else 0))),
        ]
    else:
        rows += [
            ("Signal", value(link.get("signal"), " dBm")),
            ("Channel", value(link.get("channel"))),
            ("Width", value(link.get("width_mhz"), " MHz")),
            ("Noise floor", value(survey.get("noise"), " dBm")),
            ("Channel busy", value(facts.get("busy_pct"), "%")),
            ("TX retries", value(station.get("retry_pct"), "%")),
            ("TX failed", value(station.get("failed_pct"), "%")),
            ("Negotiated", value(link.get("tx_rate"), " Mb/s")),
        ]

    school_text = "unknown"
    if school.get("ok"):
        school_text = value(school.get("ms"), " ms")
    else:
        school_text = value({"timeout": "no reply",
                             "refused": "refused",
                             "nxdomain": "NXDOMAIN",
                             "servfail": "SERVFAIL",
                             "unavailable": "not measured"}
                            .get((school.get("status") or "").lower()), "unknown")

    rows += [
        ("Gateway RTT", value(gateway.get("avg"), " ms")),
        ("Gateway loss", value(gateway.get("loss"), "%")),
        ("Internet RTT", "not tested (offline)" if offline and not internet
         else value(internet.get("avg"), " ms")),
        ("Internet loss", value(internet.get("loss"), "%")),
        ("Internet jitter", value(internet.get("jitter"), " ms")),
        ("DNS (local)", school_text),
        ("DNS (public)", "not tested (offline)" if offline and not public
         else value(public.get("ms"), " ms") if public.get("ok") else "unknown"),
        ("Download", skipped_or(speed.get("mbps"), speed, " Mb/s")),
        ("Download peak", skipped_or(speed.get("peak"), speed, " Mb/s")),
        ("Upload", skipped_or(upload.get("mbps"), upload, " Mb/s")),
        ("HTTP RTT", value(latency.get("avg"), " ms")),
        ("Access points seen", value(len(facts.get("aps") or []))),
    ]
    return rows


def _command(facts):
    """What ran. Records from before 0.48 say 'quick', a command that is gone."""
    name = facts.get("command") or "netriage"
    return "netriage" if name == "quick" else name


def markdown(facts, path):
    link = facts.get("link") or {}
    net = facts.get("address") or {}
    medium = facts.get("medium", "wifi")
    lines = [
        f"# {('Wired' if medium == 'wired' else 'Wi-Fi')} diagnostic report",
        "",
        f"- Generated: {facts.get('timestamp')}",
        f"- Interface: `{facts.get('iface')}`",
        f"- Command: {_command(facts)}",
        f"- netriage: {facts.get('version', 'unknown')}",
        f"- Run by: {facts.get('user', 'unknown')}",
    ]
    lines += [
        f"- Source: `{short_path(path)}`",
        "",
        "## Connection",
        "",
        "| Field | Value |",
        "|---|---|",
    ]

    if medium == "wired":
        lldp = facts.get("lldp") or {}
        tools = facts.get("tools") or {}
        if not tools.get("lldpcli", True):
            switch = "unknown (lldpcli not installed)"
            vlan = "unknown"
        else:
            switch = f"{cell(lldp.get('switch'))} / {cell(lldp.get('port'))}"
            vlan = cell(lldp.get("vlan"))
        lines += [
            f"| Speed | {value(link.get('speed'), ' Mb/s')} {link.get('duplex', '')} |",
            f"| Switch | {switch} |",
            f"| VLAN | {vlan} |",
        ]
    else:
        lines += [
            f"| SSID | {cell(link.get('ssid'))} |",
            f"| BSSID | {cell(link.get('bssid'))} |",
            f"| Band | {value(link.get('band'))} / channel {value(link.get('channel'))} |",
            f"| Security | {value(facts.get('ap_security'))} |",
        ]

    lines += [
        f"| IPv4 | {value(net.get('ip'))} |",
        f"| Gateway | {value(net.get('gateway'))} |",
        "",
        "## Measurements",
        "",
        "| Measurement | Value |",
        "|---|---|",
    ]
    for name, text in measurement_rows(facts):
        lines.append(f"| {name} | {text} |")

    lines += ["", "## Findings", ""]
    findings = facts.get("findings") or []
    if not findings:
        lines.append("No findings were raised by the rules.")
    for finding in findings:
        lines.append(f"### [{finding['severity'].upper()}] {cell(finding['title'])}")
        lines.append("")
        lines.append(cell(finding["detail"]))
        lines.append("")
    return "\n".join(lines)
