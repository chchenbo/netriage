"""Compare two saved runs, so "did it get better" is a measurement."""

from __future__ import annotations

from . import util
from .util import CYAN, DIM, GREEN, RED, RESET, YELLOW, section

# (name, path, unit, higher-is-better, judge). Negotiated is unjudged:
# a narrower channel lowers it on purpose.
METRICS = [
    ("Signal", ("link", "signal"), "dBm", True, True),
    ("Channel busy", ("busy_pct",), "%", False, True),
    ("Retries", ("station", "retry_pct"), "%", False, True),
    ("Upload", ("upload", "mbps"), "Mb/s", True, True),
    ("Download", ("speed", "mbps"), "Mb/s", True, True),
    ("Negotiated", ("link", "tx_rate"), "Mb/s", True, False),
    ("Gateway RTT", ("ping_gateway", "avg"), "ms", False, True),
    ("Internet RTT", ("ping_internet", "avg"), "ms", False, True),
    ("Gateway loss", ("ping_gateway", "loss"), "%", False, True),
    ("Internet loss", ("ping_internet", "loss"), "%", False, True),
]

CONTEXT = [
    ("ssid", ("link", "ssid")),
    ("channel", ("link", "channel")),
    ("width", ("link", "width_mhz")),
    ("ap", ("link", "bssid")),
]


def read(facts, path):
    """Follow a tuple of keys, returning None rather than raising."""
    value = facts
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def value_of(facts, path):
    raw = read(facts, path)
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    return None


def _format(number, unit):
    if number is None:
        return "n/a"
    text = f"{number:.6g}"
    if not unit:
        return text
    return f"{text}{unit}" if unit == "%" else f"{text} {unit}"


def _change(before, after, higher_is_better, judge=True):
    if before is None or after is None:
        return "", "n/a"
    delta = after - before
    tolerance = max(0.5, abs(before) * 0.03)
    if abs(delta) < tolerance:
        return "", "same"
    if not judge:
        return f"{delta:+.6g}", ""
    improved = delta > 0 if higher_is_better else delta < 0
    return f"{delta:+.6g}", "better" if improved else "worse"


def comparable(before, after):
    """Whether the two runs share any measurement at all."""
    return any(value_of(before, path) is not None
               and value_of(after, path) is not None
               for _, path, *_ in METRICS)


def rows(before, after):
    """One tuple per metric that at least one of the runs reported."""
    out = []
    for name, path, unit, higher_is_better, judge in METRICS:
        old = value_of(before, path)
        new = value_of(after, path)
        if old is None and new is None:
            continue
        change, verdict = _change(old, new, higher_is_better, judge)
        out.append({
            "name": name,
            "before": _format(old, unit),
            "after": _format(new, unit),
            "change": change,
            "verdict": verdict,
        })
    return out


def context(before, after):
    """Settings that changed, which usually explain the numbers."""
    out = []
    for name, path in CONTEXT:
        old = read(before, path)
        new = read(after, path)
        if old is None and new is None:
            continue
        if str(old) == str(new):
            continue
        if name == "width":
            old, new = f"{old} MHz" if old else "n/a", f"{new} MHz" if new else "n/a"
        out.append((name, str(old if old is not None else "n/a"),
                    str(new if new is not None else "n/a")))
    return out


def _stamp(facts):
    return (facts.get("timestamp") or "?").replace("T", " ")


def _who(facts):
    """Which network this run was on: the SSID, or the port for a cable."""
    link = facts.get("link") or {}
    if facts.get("medium") == "wired":
        speed = link.get("speed")
        return f"wired {speed} Mb/s" if speed else "wired"
    return link.get("ssid") or "-"


def mismatches(before, after):
    """Ways the two runs are not comparable, in plain words."""
    notes = []
    old_medium = before.get("medium") or ""
    new_medium = after.get("medium") or ""
    if old_medium != new_medium:
        notes.append(f"different media: {old_medium or '?'} and {new_medium or '?'}")

    old_ssid = read(before, ("link", "ssid"))
    new_ssid = read(after, ("link", "ssid"))
    if old_ssid and new_ssid and old_ssid != new_ssid:
        notes.append(f"different networks: {old_ssid} and {new_ssid}")
    return notes


def render(before, after):
    section("COMPARE")
    print(f"  {DIM}before{RESET}  {_stamp(before)}  {_who(before)}")
    print(f"  {DIM}after {RESET}  {_stamp(after)}  {_who(after)}")

    notes = mismatches(before, after)
    if notes:
        print()
        for note in notes:
            print(f"  {YELLOW}! {note}{RESET}")

    if not comparable(before, after):
        # a wired run against a wireless one has nothing in common; a table of
        # "n/a -> 119.2" and a verdict of "no measurable change" is a lie
        print()
        print(f"  {YELLOW}these runs share no measurement, so there is nothing "
              f"to compare.{RESET}")
        print(f"  {DIM}Compare two runs of the same medium.{RESET}")
        return

    changed = context(before, after)
    if changed:
        print()
        for name, old, new in changed:
            print(f"  {DIM}{name:<8}{RESET}{old} {DIM}->{RESET} {new}")

    print()
    print(f"  {DIM}{'metric':<16}{'before':>13}{'after':>13}{'change':>9}{RESET}")

    for row in rows(before, after):
        colour = {"better": GREEN, "worse": RED}.get(row["verdict"], DIM)
        change = row["change"] or "\u00b7"
        print(f"  {row['name']:<16}{row['before']:>13}{row['after']:>13}"
              f"{change:>9}  {colour}{row['verdict']}{RESET}")


def trend(entries):
    """Several runs side by side, for "what happened across these seven"."""
    section(f"TREND   {len(entries)} runs")
    print(f"  {DIM}{'id':>4}  {'when':<11}  {'ssid':<12}  {'dBm':>4}  "
          f"{'retry':>5}  {'down':>5}  {'up':>5}  {'rtt':>5}{RESET}")

    for number, facts in entries:
        link = facts.get("link") or {}
        signal = link.get("signal")
        retry = read(facts, ("station", "retry_pct"))
        down = read(facts, ("speed", "mbps"))
        up = read(facts, ("upload", "mbps"))
        rtt = read(facts, ("ping_gateway", "avg"))

        stamp = (facts.get("timestamp") or "?").replace("T", " ")
        when = stamp[5:16] if len(stamp) >= 16 else stamp
        ssid = util.shorten((link.get("ssid") or "-"), 16)

        def cell(value, unit=""):
            if value is None:
                return "-"
            # three significant digits: 10.8, 6.3, 288, -50
            return f"{value:.3g}" + unit

        print(f"  {number:>4}  {when:<11}  {ssid:<12}  "
              f"{cell(signal):>4}  "
              f"{(f'{retry:.1f}%' if retry is not None else '-'):>5}  "
              f"{cell(down):>5}  {cell(up):>5}  {cell(rtt):>5}")

    print()
    print(f"  {DIM}dBm = signal, retry = TX retries, down/up = Mb/s, rtt = gateway ms{RESET}")


def summarise(before, after):
    """A one line verdict: how many metrics improved and how many regressed."""
    scored = rows(before, after)
    better = sum(1 for row in scored if row["verdict"] == "better")
    worse = sum(1 for row in scored if row["verdict"] == "worse")
    if not better and not worse:
        return DIM, "no measurable change"
    if better and not worse:
        return GREEN, f"{better} measurement{'s' if better > 1 else ''} improved"
    if worse and not better:
        return RED, f"{worse} measurement{'s' if worse > 1 else ''} regressed"
    if better > worse:
        return GREEN, f"{better} improved, {worse} regressed"
    if worse > better:
        return RED, f"{worse} regressed, {better} improved"
    return CYAN, f"{better} improved, {worse} regressed"
