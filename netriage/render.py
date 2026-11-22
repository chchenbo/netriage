"""Everything that turns a facts dict into terminal text.

Split out of cli.py so the command layer stays about commands, and so the
rendering can be tested without a parser or a network.
"""

from __future__ import annotations

import getpass
import os
import socket
import sys
import textwrap
from datetime import datetime

from . import __version__, diagnose, wifi, wired
from .logo import LOGO
from .util import (BOLD, CYAN, DIM, GREEN, RED, RESET, YELLOW, bar, fmt_pct,
                   fmt_rssi, rule_width, section)


def print_facts(facts):
    if facts.get("medium") == "wired":
        _print_wired_link(facts)
    else:
        _print_wireless_link(facts)


def _print_wired_link(facts):
    link = facts.get("link") or {}
    section(f"LINK   {facts.get('iface', '?')}   (wired)")
    if not link.get("connected"):
        print(f"  {RED}no carrier{RESET}  {DIM}cable unplugged, or the switch port is down{RESET}")
        return

    speed = link.get("speed")
    speed_text = f"{speed} Mb/s" if speed else "unknown"
    print(f"  {'Speed':<20}{speed_text}   {(link.get('duplex') or '?')} duplex")
    if link.get("autoneg"):
        print(f"  {'Auto-negotiation':<20}{link['autoneg']}")

    raw_counters = facts.get("counters") or {}
    counters, checksum = wired.classify(raw_counters)
    tools = facts.get("tools") or {}
    if not tools.get("ethtool", True) and not raw_counters:
        print(f"  {'Frame errors':<20}{DIM}unknown (ethtool not installed){RESET}")
    elif counters:
        total = sum(counters.values())
        print(f"  {'Frame errors':<20}{RED}{total:,}{RESET}")
        for name, value in sorted(counters.items())[:4]:
            print(f"    {DIM}{name}: {value:,}{RESET}")
    else:
        print(f"  {'Frame errors':<20}{GREEN}none{RESET}")

    if checksum:
        total_checksum = sum(checksum.values())
        print(f"  {'Checksum errors':<20}{total_checksum:,}"
              f"   {DIM}(not frame errors){RESET}")

    neighbour = facts.get("lldp") or {}
    if neighbour.get("switch") or neighbour.get("port"):
        # only show what the switch actually told us
        parts = [part for part in (neighbour.get("switch"), neighbour.get("port"))
                 if part]
        if neighbour.get("vlan"):
            parts.append(f"VLAN {neighbour['vlan']}")

        label = f"  {'LLDP':<20}"
        if len(label) + len(" / ".join(parts)) <= rule_width():
            print(f"{label}{GREEN}{' / '.join(parts)}{RESET}")
        else:
            # a switch description can be a whole sentence; put it on its own line
            indent = " " * len(label)
            print(f"{label}{GREEN}{parts[0]}{RESET}")
            for part in parts[1:]:
                print(f"{indent}{GREEN}{part}{RESET}")
    elif not tools.get("lldpcli", True):
        print(f"  {'LLDP':<20}{DIM}unknown (lldpcli not installed){RESET}")
    else:
        print(f"  {'LLDP':<20}{DIM}no neighbour (unmanaged switch, or LLDP disabled){RESET}")


def _print_wireless_link(facts):
    link = facts.get("link") or {}
    station = facts.get("station") or {}
    survey_data = facts.get("survey") or {}

    section(f"LINK   {facts.get('iface', '?')}   (wireless)")
    if not link.get("connected"):
        print(f"  {RED}not associated{RESET}")
        return

    print(f"  {'SSID':<18}{wifi.printable(link.get('ssid') or '')}")
    print(f"  {'BSSID':<18}{link.get('bssid')}")
    width = f"  {link['width_mhz']} MHz" if link.get("width_mhz") else ""
    print(f"  {'Band':<18}{link.get('band')}  channel {link.get('channel')}{width}")
    noise = survey_data.get("noise")
    noise_text = f"noise {noise} dBm" if noise is not None else f"{DIM}noise n/a{RESET}"
    print(f"  {'Signal':<18}{fmt_rssi(link.get('signal'))}"
          f"   {noise_text}   SNR {_snr(link, survey_data)}")
    if facts.get("ap_security"):
        print(f"  {'Security':<18}{facts['ap_security']}")
    if link.get("tx_rate"):
        print(f"  {'Negotiated':<18}{link['tx_rate']:.0f} Mbps")

    busy = facts.get("busy_pct")
    if busy is not None:
        source = facts.get("busy_source") or ""
        print(f"  {'Channel busy':<18}{fmt_pct(busy)}  {bar(busy, 100)}  {DIM}({source}){RESET}")
    elif facts.get("scan_source") == "nmcli":
        print(f"  {'Channel busy':<18}{DIM}n/a - run with sudo to read it from the AP{RESET}")
    elif facts.get("aps"):
        print(f"  {'Channel busy':<18}{DIM}n/a - the card and the AP both stayed silent{RESET}")

    if station.get("retry_pct") is not None:
        failed = station.get("failed_pct")
        failed_text = f"   failed {failed:.1f}%" if failed is not None else ""
        print(f"  {'TX retries':<18}{fmt_pct(station['retry_pct'])}{failed_text}")


def _snr(link, survey_data):
    signal = link.get("signal")
    noise = survey_data.get("noise")
    if signal is None or noise is None:
        return "-"
    if noise >= signal:
        return "-"
    return f"{signal - noise} dB"


def _lat_short(stats):
    if not stats:
        return "-"
    return (f"{stats.get('avg')} ms   loss {stats.get('loss')}%   "
            f"jitter {stats.get('jitter')} ms")


def print_network(facts):
    net = facts.get("address") or {}
    section("NETWORK")

    # Nothing was probed because the interface is not carrying traffic; say so
    # instead of printing a column of dashes that looks like a measurement.
    if facts.get("why_not_measured"):
        if net.get("ip"):
            ipv4 = net["ip"]
            print(f"  {'IPv4':<18}{RED}{ipv4}{RESET}   "
                  f"{DIM}(link-local: no DHCP lease){RESET}")
        reason = facts["why_not_measured"]
        print(f"  {DIM}{facts.get('iface') or 'this interface'}: {reason}; "
              f"nothing above the link could be tested{RESET}")
        return

    print(f"  {'IPv4':<18}{net.get('ip', '-')}")
    print(f"  {'Gateway':<18}{net.get('gateway', '-')}")
    servers = facts.get("dns_servers") or []
    if servers:
        print(_wrap(", ".join(servers), "  DNS               ", " " * 20))
    else:
        print(f"  {'DNS':<18}-")

    gateway = facts.get("ping_gateway")
    if gateway:
        print(f"  {'Gateway RTT':<18}{_lat(gateway)}")
    internet = facts.get("ping_internet")
    tools = facts.get("tools") or {}
    if facts.get("offline") and internet is None:
        print(f"  {'Internet RTT':<18}{DIM}not tested (offline){RESET}")
    elif internet:
        line = f"  {'Internet RTT':<18}{_lat(internet)}"
        print(line if len(line) <= rule_width() else
              f"  {'Internet RTT':<18}{_lat_short(internet)}")
    elif not tools.get("ping", True):
        print(f"  {'Internet RTT':<18}{DIM}unknown (ping not installed){RESET}")
    else:
        print(f"  {'Internet RTT':<18}{YELLOW}no ICMP reply{RESET}"
              f"   {DIM}(commonly filtered on school networks){RESET}")

    school = facts.get("dns_local") or facts.get("dns_school")
    public = facts.get("dns_public")
    if school:
        print(f"  {'DNS local':<18}{_res(school)}")
    if facts.get("offline") and public is None:
        print(f"  {'DNS public':<18}{DIM}not tested (offline){RESET}")
    elif public:
        print(f"  {'DNS public':<18}{_res(public, filtered_ok=bool(school and school.get('ok')))}")

    captive = facts.get("captive")
    if captive is None:
        note = "not tested (offline)" if facts.get("offline") else "unknown"
        print(f"  {'Portal':<18}{DIM}{note}{RESET}")
    elif captive:
        print(f"  {'Portal':<18}{RED}intercepted{RESET}")
        target = facts.get("portal_url") or ""
        if target:
            # the redirect target names the appliance doing the intercepting
            print(_wrap(target, "                     ", "                     ", break_long=True))
    else:
        print(f"  {'Portal':<18}{GREEN}none{RESET}")

    up = facts.get("upload")
    if isinstance(up, dict) and up.get("skipped"):
        print(f"  {'Upload':<18}{DIM}skipped{RESET}")
    elif up and "mbps" in up:
        print(f"  {'Upload':<18}{up['mbps']} Mbps")
    elif up and up.get("error"):
        print(f"  {'Upload':<18}{RED}failed{RESET}")
        print(_wrap(up["error"], " " * 20, " " * 20))

    speed = facts.get("speed")
    skipped = isinstance(speed, dict) and speed.get("skipped")
    if skipped:
        print(f"  {'Download':<18}{DIM}skipped{RESET}")
    elif speed and "mbps" in speed:
        # On a fast link the payload can finish in well under a second, and
        # slow start then dominates the average. Say so rather than hide it.
        took = speed.get("seconds")
        note = f"   {DIM}({took:.1f}s sample){RESET}" if took and took < 2 else ""
        print(f"  {'Download':<18}{speed['mbps']} Mbps   peak {speed['peak']}{note}")
    elif speed and speed.get("error"):
        print(f"  {'Download':<18}{RED}failed{RESET}")
        print(_wrap(speed["error"], " " * 20, " " * 20))

    latency = facts.get("latency")
    if latency and latency.get("avg") is not None:
        reached, total = latency.get("reached"), latency.get("total")
        if reached is not None and reached < total:
            # the narrower wording: the long form runs past the rule width
            note = f"   {DIM}(TLS · {reached}/{total} sites answered){RESET}"
        else:
            note = f"   {DIM}(full request, TLS){RESET}"
        print(f"  {'HTTP RTT':<18}{latency['avg']} ms   jitter {latency['jitter']} ms{note}")
    elif not skipped:
        print(f"  {'HTTP RTT':<18}{DIM}n/a{RESET}")


def _lat(result):
    if result.get("avg") is None:
        return f"{RED}unreachable{RESET}"
    return f"{result['avg']:.1f} ms   loss {result['loss']}%   jitter {result.get('jitter') or 0:.1f} ms"


def _res(result, filtered_ok=False):
    if result.get("ok") is None:
        return f"{DIM}n/a{RESET}   {DIM}{result.get('reason') or 'not measured'}{RESET}"
    if result.get("ok"):
        return f"{GREEN}ok{RESET}   {result.get('ms')} ms"
    if filtered_ok:
        return f"{YELLOW}filtered{RESET}   {DIM}expected on filtered networks{RESET}"
    return f"{RED}failed{RESET}   {result.get('server')}"


def print_summary(facts):
    """One closing line: verdict, scale of the environment, and first hop."""
    findings = facts.get("findings") or []
    critical = sum(1 for f in findings if f["severity"] == diagnose.CRITICAL)
    warnings = sum(1 for f in findings if f["severity"] == diagnose.WARNING)

    notes = sum(1 for f in findings if f["severity"] == diagnose.INFO)

    parts = []
    if critical:
        parts.append(f"{critical} critical")
    if warnings:
        parts.append(f"{warnings} warning" + ("s" if warnings > 1 else ""))
    if notes:
        parts.append(f"{notes} note" + ("s" if notes > 1 else ""))

    if critical:
        colour = RED
    elif warnings:
        colour = YELLOW
    elif notes:
        colour = CYAN
    else:
        colour = GREEN
    head = ", ".join(parts) if parts else "all clear"

    if facts.get("medium") == "wired":
        speed = (facts.get("link") or {}).get("speed")
        extra = [f"{speed} Mb/s" if speed else "cable"]
    else:
        extra = [f"{len(wifi.unique(facts.get('aps'), (facts.get('link') or {}).get('bssid')))} APs"]

    latency = (facts.get("ping_gateway") or {}).get("avg")
    if latency is not None:
        extra.append(f"gateway {latency:.1f} ms")

    # Same shape as every other section: cyan title, rule, then the values.
    section("RESULT")
    pad = " " * max(2, 14 - len(head))
    print(f"  {colour}{BOLD}{head}{RESET}{pad}{DIM}{' · '.join(extra)}{RESET}")


def _wrap(text, first="  ", rest="  ", break_long=False):
    """Fill to the width of the section rule, so nothing overhangs it."""
    return textwrap.fill(
        text,
        width=rule_width(),
        initial_indent=first,
        subsequent_indent=rest,
        break_long_words=break_long,
        break_on_hyphens=False,
    )


def print_findings(findings):
    section("DIAGNOSIS")
    if not findings:
        print(f"  {GREEN}{BOLD}ALL CLEAR{RESET}  {GREEN}Measurements are all within normal range.{RESET}")
        return

    for index, finding in enumerate(findings, 1):
        colour = {diagnose.CRITICAL: RED, diagnose.WARNING: YELLOW, diagnose.INFO: CYAN}[
            finding["severity"]
        ]
        label = f"[{finding['severity'].upper()}] "
        hanging = "  " + " " * len(label)
        # One line per finding by default: the heading is the answer, and the
        # rest is reference material in the saved report.
        print(f"{colour}{BOLD}{_wrap(label + finding['title'], '  ', hanging)}{RESET}")


def _clean_ssid(raw):
    """Some APs broadcast control bytes; never print them to a terminal."""
    if not raw:
        return f"{DIM}<hidden>{RESET}"
    cleaned = "".join(ch if ch.isprintable() else "?" for ch in raw).strip()
    if not cleaned or set(cleaned) <= {"?"}:
        return f"{DIM}<garbage SSID>{RESET}"
    return cleaned


def _rssi_cell(signal, width=12):
    """Pad on the plain text, then colour, so columns stay aligned."""
    if signal is None:
        return fmt_rssi(None) + " " * max(0, width - 3)
    rounded = int(round(signal))
    plain = f"{rounded} dBm"
    return fmt_rssi(rounded) + " " * max(0, width - len(plain))


def _fit_ssid(text, width):
    """Trim a long SSID with an ellipsis so the row stays inside the rule."""
    if len(text) <= width:
        return text
    return text[: max(1, width - 1)] + "\u2026"


def _plural(count, singular="AP", plural="APs"):
    return f"{count} {singular if count == 1 else plural}"


def print_scan(aps, connected_bssid=None, iface=None):
    aps = wifi.unique(aps, connected_bssid)
    section(f"NEARBY   {_plural(len(aps), 'access point', 'access points')}")
    if not aps:
        print(f"  {DIM}scan needs root, or no networks in range{RESET}")
        return

    limit = 20
    width = rule_width()
    mine = (connected_bssid or "").lower()
    ranked = sorted(aps, key=lambda ap: ap.get("signal", -200), reverse=True)
    marked = False
    for ap in ranked[:limit]:
        ssid = _clean_ssid(ap.get("ssid"))
        # Columns after the SSID are ranked by value: load first, client count
        # last. On a long row the SSID keeps enough room to stay recognisable.
        loaded = ""
        if ap.get("util_pct") is not None:
            loaded = f"  util {ap['util_pct']:>4.0f}%"
        tail = f"  {ap['stations']:>2} st" if ap.get("stations") else ""

        marker = "* " if mine and ap.get("bssid", "").lower() == mine else "  "
        marked = marked or marker == "* "
        # marker + rssi cell + " ch " + channel + gap + bssid + space
        budget = width - (2 + 12 + 4 + 3 + 2 + 17 + 1)

        # Fit what fits, in order of value: the name, then the load, then the
        # client count. A short SSID keeps all three; a long one loses the tail
        # first and only then gets trimmed.
        if len(ssid) + len(loaded) + len(tail) <= budget:
            loaded += tail
        elif len(ssid) + len(loaded) <= budget:
            pass
        else:
            ssid = _fit_ssid(ssid, max(8, budget - len(loaded)))

        print(f"{marker}{_rssi_cell(ap.get('signal'))} ch {ap.get('channel'):>3}  "
              f"{ap.get('bssid', ''):<17} {ssid}{loaded}")

    if marked:
        print(f"  {DIM}* is the access point you are connected to{RESET}")

    if len(ranked) == 1:
        # One BSS is almost always a card answering about its own network
        # rather than a neighbourhood with a single access point in it.
        print()
        if os.geteuid() == 0:
            note = ("only one access point was visible, even as root: the driver "
                    "reported nothing else, so this is not a privilege problem")
        else:
            note = ("only one access point was visible; a card can answer with "
                    f"just the network it is on. Try:  sudo iw dev "
                    f"{iface or 'wlan0'} scan flush")
        print(f"{DIM}{_wrap(note, '  ', '  ')}{RESET}")

    if len(ranked) > limit:
        print(f"  {DIM}... {len(ranked) - limit} weaker APs not shown "
              f"(the channel table below counts all {len(ranked)}){RESET}")

    # The list is merged from both sources, so "nmcli appears" is not news.
    # Say it only when iw contributed nothing at all.
    if not any(ap.get("source") != "nmcli" for ap in aps):
        print()
        if os.geteuid() == 0:
            note = ("the list came from NetworkManager: no per-AP load or "
                    "client counts")
        else:
            note = ("scanned via NetworkManager: run with sudo for per-AP load "
                    "and client counts")
        print(f"  {DIM}{_wrap(note, '  ', '  ')}{RESET}")

    # Only talk about 2.4 GHz when there is something on 2.4 GHz to compare.
    if not any(wifi.band_of_ap(ap) == "2.4 GHz" for ap in aps):
        return

    table = wifi.channel_load(aps, band="2.4 GHz")
    print()
    print(f"  {BOLD}2.4 GHz channel use{RESET}")
    for channel in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13):
        entry = table.get(channel)
        if not entry:
            continue
        print(f"    ch {channel:>2}  {entry['count']:>2} {'AP ' if entry['count'] == 1 else 'APs'}  "
              f"{bar(entry['count'], 6, 12)}")

    best = diagnose.recommend_channels(aps, "2.4 GHz")[:3]
    # non-breaking space keeps "(1 AP)" from splitting across a wrapped line
    picks = ", ".join(
        f"ch {channel} ({count}\u00a0{'AP' if count == 1 else 'APs'})" for count, _, channel in best
    )
    print()
    print(f"{GREEN}{_wrap('Least crowded 2.4 GHz: ' + picks)}{RESET}")


def print_replay(run, facts):
    """The banner line for a saved run: which one, when, and with what."""
    stamped = (facts.get("timestamp") or "?").replace("T", " ")
    version = facts.get("version") or "?"
    print()
    print(f"{DIM}replay of run {run}{RESET}   {stamped}   "
          f"{DIM}netriage {version}, no measurement{RESET}")


def _print_header(json_mode, redact=False):
    """Wordmark and identity on a terminal; a plain line when piped."""
    if json_mode:
        return

    if not sys.stdout.isatty():
        print(f"netriage {__version__}  {datetime.now():%Y-%m-%d %H:%M:%S}")
        return

    print(f"{CYAN}{LOGO}{RESET}")
    print()
    # sudo makes getpass() report root; the human who ran it is SUDO_USER,
    # which is also whose home the report is written to.
    if redact:
        identity = "redacted@redacted"
    else:
        user = os.environ.get("SUDO_USER") or getpass.getuser()
        identity = f"{user}@{socket.gethostname().split('.')[0]}"
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"{DIM}ran by {identity} at {stamp}{RESET}  {CYAN}netriage {__version__}{RESET}")
    print()  # the progress line draws here, clear of the identity line
