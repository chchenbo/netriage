"""Command line entry point.

    netriage                    one full pass plus a verdict
    netriage scan               nearby access points and channel use
    netriage watch --minutes 10 record while the fault happens
    netriage report             markdown write-up of a saved run
    netriage compare            diff two saved runs
    netriage history            the saved runs, as a table

    python3 -m netriage         the same program without the launcher
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import socket
import time
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from . import diagnose, media, network, render, report, soak as soakmod, util, wifi, wired
from . import __version__
from . import compare as compare_module
from . import logs
from .render import (_print_header, _plural, print_facts, print_findings,
                     print_network, print_scan, print_summary)
from .util import (BOLD, DIM, RED, RESET, YELLOW, CookTimer, have, rule_width,
                   section, short_path)

DNS_NAME = "www.google.com"

# Reading and writing saved runs lives in logs.py; these names keep the
# command layer readable without repeating the module prefix everywhere.
_report_path = logs.report_path
_save = logs.save
_latest = logs.latest
_read_report = logs.read
_recent_reports = logs.recent
_resolve_run = logs.resolve_run


# ---------------------------------------------------------------- collection


def collect(iface, medium=None, do_speed=True, do_scan=True, offline=False,
            on_phase=None):
    """Gather everything measurable on one interface, whatever its medium."""
    medium = medium or media.kind_of(iface)

    # How long each phase took, so a slow run can be explained rather than
    # guessed at. It goes into the debug bundle, never into the report.
    timings = {}
    _last = [time.monotonic()]

    def phase(name):
        now = time.monotonic()
        timings[phase.name] = round(now - _last[0], 1)
        _last[0] = now
        phase.name = name
        if on_phase:
            on_phase(name)

    phase.name = "start"

    facts = {
        "iface": iface,
        "medium": medium,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }

    if medium == "wired":
        facts["link"] = wired.link(iface)
        facts["lldp"] = wired.lldp(iface) if facts["link"].get("connected") else {}
        facts["counters"] = wired.counters(iface) if facts["link"].get("connected") else {}
        facts["packets"] = wired.packets(iface) if facts["link"].get("connected") else None
        facts["aps"] = []
        link = facts["link"]
    else:
        link = wifi.link(iface)
        facts["link"] = link
        facts["station"] = wifi.station(iface) if link.get("connected") else {}
        facts["survey"] = wifi.survey(iface, link.get("freq")) if link.get("connected") else {}

        if do_scan:
            phase("scanning")
            facts["aps"] = wifi.scan(iface)
            aps_now = facts.get("aps") or []
            facts["scan_source"] = (
                "nmcli" if any(ap.get("source") == "nmcli" for ap in aps_now) else "iw"
            )

        facts["aps"] = wifi.unique(
            wifi.trust_live_signal(facts.get("aps") or [], link), link.get("bssid"))

        bssid = (link.get("bssid") or "").lower()
        for ap in facts.get("aps") or []:
            if (ap.get("bssid") or "").lower() == bssid:
                facts["ap_security"] = wifi.security_label(ap)
                break

    facts["tools"] = {tool: have(tool)
                      for tool in ("iw", "ethtool", "lldpcli", "dig", "ping", "curl")}
    facts["address"] = network.address(iface)
    facts["dns_servers"] = network.dns_servers(iface)

    link_up = bool(link.get("connected"))
    address = (facts["address"] or {})
    has_ip = bool(address.get("ip")) and not network.is_link_local(address["ip"])

    if not (link_up and has_ip):
        # Probes would travel over another interface; the address stays,
        # because a link-local one is the most useful fact in the run.
        facts["dns_servers"] = []
        facts["ping_gateway"] = None
        facts["ping_internet"] = None
        facts["captive"] = None
        facts["portal_url"] = ""
        facts["dns_local"] = None
        facts["dns_school"] = None     # kept for reports saved before 0.36
        facts["dns_public"] = None
        facts["speed"] = {"skipped": True}
        facts["upload"] = {"skipped": True}
        facts["latency"] = None
        if not link_up:
            facts["why_not_measured"] = "not associated"
        elif network.is_link_local(address.get("ip")):
            facts["why_not_measured"] = "no DHCP lease"
        else:
            facts["why_not_measured"] = "no address"
        facts["timings"] = {name: value for name, value in timings.items()
                            if name != "start"}
        facts["findings"] = [f.__dict__ for f in diagnose.analyze(facts)]
        return facts

    phase("probing network")
    net = facts["address"]
    servers = facts["dns_servers"]

    # These are independent network round trips; running them together saves a
    # few seconds on every run.
    with ThreadPoolExecutor(max_workers=max(5, len(servers) + 3)) as pool:
        gateway_job = (
            pool.submit(network.ping, iface, net["gateway"], 4)
            if net.get("gateway") else None
        )
        internet_job = (None if offline
                        else pool.submit(network.ping, iface, "1.1.1.1", 4))
        captive_job = None if offline else pool.submit(network.captive, iface)
        # every configured resolver is tested, not just the first: one dead
        # server behind three working ones is still a fault
        school_job = (
            pool.submit(lambda: [network.resolve(server, DNS_NAME)
                                 for server in servers])
            if servers else None
        )
        public_job = (None if offline
                      else pool.submit(network.resolve, "1.1.1.1", DNS_NAME))

        facts["ping_gateway"] = gateway_job.result() if gateway_job else None
        facts["ping_internet"] = internet_job.result() if internet_job else None
        portal = captive_job.result() if captive_job else None
        results = school_job.result() if school_job else []
        facts["dns_servers_tested"] = results
        facts["dns_local"] = next(
            (r for r in results if r.get("ok")), results[0] if results else None)
        facts["dns_school"] = facts["dns_local"]
        facts["dns_public"] = public_job.result() if public_job else None

    facts["captive"] = portal["intercepted"] if portal else None
    facts["portal_url"] = portal["url"] if portal else ""
    facts["offline"] = bool(offline)

    if do_speed:
        phase("measuring speed")
        try:
            facts["speed"] = network.download(seconds=5)
        except Exception as exc:  # noqa: BLE001
            facts["speed"] = {"error": str(exc)}
        try:
            facts["upload"] = network.upload()
        except Exception as exc:  # noqa: BLE001
            facts["upload"] = {"error": str(exc)}
        facts["latency"] = network.latency_ms()
    else:
        facts["speed"] = {"skipped": True}
        facts["upload"] = {"skipped": True}
        facts["latency"] = None

    timings[phase.name] = round(time.monotonic() - _last[0], 1)
    facts["timings"] = {name: value for name, value in timings.items() if name != "start"}
    facts["findings"] = [f.__dict__ for f in diagnose.analyze(facts)]
    return facts


# ---------------------------------------------------------------- commands


def _exists(iface):
    """Whether the kernel knows this interface."""
    if os.environ.get("NETRIAGE_IGNORE_SYSFS") == "1":
        return True
    base = "/sys/class/net"
    if not os.path.isdir(base):
        return True
    return os.path.exists(os.path.join(base, iface))


def resolve_interface(args, wireless_only=False):
    """Pick the interface the user asked for, or the sensible default."""
    explicit = getattr(args, "iface", None)
    if wireless_only:
        if explicit:
            # honour it or fail; substituting another card would test
            # something the user did not ask about
            kind = media.kind_of(explicit)
            return (explicit, kind) if kind == "wifi" else (explicit, kind or "unknown")
        return media.pick("wifi")
    if explicit:
        return media.pick(explicit=explicit)
    if getattr(args, "wired", False):
        return media.pick("wired")
    if getattr(args, "wifi", False):
        return media.pick("wifi")
    return media.pick()


def cmd_quick(args):
    _apply_defaults(args)
    iface, medium = resolve_interface(args)
    if not iface:
        print("No usable network interface found. Try: ip -br link", file=sys.stderr)
        return 2

    if not _exists(iface):
        print(f"netriage: no such interface: {iface}\n"
              f"           Available: {', '.join(sorted(media.all_interfaces())) or 'none'}",
              file=sys.stderr)
        return 2

    json_mode = getattr(args, "json", False)
    _print_header(json_mode, getattr(args, "redact", False))

    timer = CookTimer(enabled=not json_mode)
    timer.start()
    try:
        facts = collect(iface, medium,
                        do_speed=not getattr(args, "offline", False),
                        offline=getattr(args, "offline", False),
                        on_phase=timer.set_label)
    finally:
        timer.stop()

    # Everything the run knows goes in before printing, so --json and the saved
    # file are the same document.
    facts["command"] = "netriage"
    facts["version"] = __version__
    facts["user"] = os.environ.get("SUDO_USER") or getpass.getuser()
    # Redaction happens last, on the finished document, so nothing added
    # afterwards can leak the identity.
    if getattr(args, "redact", False):
        facts = util.redact(facts)

    if json_mode:
        print(json.dumps(facts, indent=2, ensure_ascii=False, default=str))
    else:
        print_facts(facts)
        print_network(facts)
        print_findings(facts["findings"])
        if facts.get("medium") != "wired":
            print_scan(facts.get("aps") or [],
                       connected_bssid=(facts.get("link") or {}).get("bssid"),
                       iface=facts.get("iface"))
        print_summary(facts)
        print()

    path = _report_path("quick")
    if getattr(args, "debug", False):
        from . import debug as debug_module

        debug_path = path.replace(".json", "-debug.txt")
        bundle = debug_module.bundle(iface, facts)
        if getattr(args, "redact", False):
            bundle = util.redact_text(bundle)
            # the machine and the operator are named in uname and the parsed
            # result, and neither is a pattern a regex can guess
            for secret in (getpass.getuser(), socket.gethostname(),
                           socket.gethostname().split(".")[0]):
                if secret and len(secret) > 2:
                    bundle = bundle.replace(secret, "redacted")
        with open(debug_path, "w", encoding="utf-8") as handle:
            handle.write(bundle)
        facts["debug_file"] = debug_path
    _save(facts, "quick", path)

    if not json_mode:
        print(f"{DIM}{_footer(timer.seconds, path)}{RESET}")
        if getattr(args, "debug", False):
            print(f"{DIM}debug bundle: "
                  f"{short_path(path.replace('.json', '-debug.txt'))}{RESET}")
        # keep the shell prompt off the last line of the report
        print()

    critical = sum(1 for f in facts["findings"] if f["severity"] == diagnose.CRITICAL)
    return 1 if critical else 0


def _stamp_of(facts):
    return (facts.get("timestamp") or "").replace("T", " ")


def cmd_compare(args):
    """compare [A] [B] - two runs by number or file, or the last two."""
    _apply_defaults(args)
    tokens = list(getattr(args, "files", []) or [])

    if len(tokens) == 1:
        # resolve first: "no run numbered 404" says more than "give two runs"
        found, problem = _resolve_run(tokens[0])
        if problem:
            print(problem, file=sys.stderr)
            return 2
        print("Give two runs to compare (numbers or files), or none for the "
              "last two.", file=sys.stderr)
        return 2

    picked = []
    if not tokens:
        pairs = list(reversed(_recent_reports(2)))    # oldest first
        if len(pairs) < 2:
            print(f"Need two saved runs to compare; found {len(pairs)}. "
                  f"Run 'netriage' first.", file=sys.stderr)
            return 2
        for path in pairs:
            facts, problem = _read_report(path)
            if problem:
                print(problem, file=sys.stderr)
                return 2
            picked.append((logs.number_of(path) or "?", facts))
    else:
        for token in tokens:
            found, problem = _resolve_run(token)
            if problem:
                print(problem, file=sys.stderr)
                return 2
            number = logs.number_of(found[0]) or "?"
            picked.append((number, found[1]))

    if args.redact:
        picked = [(number, util.redact(facts)) for number, facts in picked]

    if len(picked) > 2:
        compare_module.trend(picked)
        return 0

    # Arguments are a set, not a sequence: the older run is the one to compare
    # against. `compare 40 37` and `compare 37 40` mean the same thing.
    if _stamp_of(picked[0][1]) > _stamp_of(picked[1][1]):
        picked.reverse()

    before, after = picked[0][1], picked[1][1]
    compare_module.render(before, after)
    colour, summary = compare_module.summarise(before, after)
    print()
    print(f"  {colour}{BOLD}{summary}{RESET}")
    return 0


def cmd_history(args):
    """Every saved run as a table. The list is short; filtering is ls + jq."""
    found, problems = logs.entries(with_problems=True)
    if not found:
        print("No saved runs yet. Run: netriage")
        for path, why in problems[:3]:
            print(f"  {os.path.basename(path)}: {why}")
        return 2

    section(f"HISTORY   {_plural(len(found), 'run', 'runs')}")
    print(f"  {DIM}{'':>2}{'id':>4}  {'when':<19}  {'dBm':>4}  ssid{RESET}")

    seen_marks = set()
    for number, path, facts in found:
        stamp = facts.get("timestamp") or "?"
        date, _, clock = stamp.partition("T")
        clock = clock[:8]

        signal = util.read_metric(facts, ("link", "signal"))
        severities = {f.get("severity") for f in facts.get("findings") or []}
        # Always two visible columns, colour applied around them: padding a
        # string that already contains escapes does not pad what you see.
        if diagnose.CRITICAL in severities:
            flag, flag_colour = "!!", RED
            seen_marks.add("critical")
        elif diagnose.WARNING in severities:
            flag, flag_colour = " !", YELLOW
            seen_marks.add("warning")
        else:
            flag, flag_colour = "  ", ""

        # the unit lives in the header, which buys the year its space back
        if facts.get("medium") == "wired":
            signal_text = "-"        # a cable has no signal level
        elif signal is not None:
            signal_text = f"{int(round(signal))}"
        else:
            signal_text = "n/a"
        when = f"{date} {clock}"                  # YYYY-MM-DD HH:MM:SS

        # two spaces between every column, so nothing reads as one field
        common = f"{number:>4}  {when:<19}  {signal_text:>4}  "
        visible = f"  {flag}{common}"
        painted = f"  {flag_colour}{flag}{RESET}{common}" if flag_colour else visible

        # The network name comes last so it can use whatever room is left,
        # which is usually enough to print it in full.
        room = max(8, rule_width() - len(visible))
        link = facts.get("link") or {}
        if facts.get("medium") == "wired":
            # "n/a -" says nothing; the port speed is what the run was about
            speed = link.get("speed")
            raw_ssid = f"wired {speed} Mb/s" if speed else "wired"
        else:
            raw_ssid = link.get("ssid") or "-"
        if getattr(args, "redact", False):
            raw_ssid = "redacted"
        ssid = util.shorten_middle(wifi.printable(raw_ssid), room)
        print(painted + ssid)

    print()
    if seen_marks:
        print(f"  {DIM}! = a warning, !! = a critical finding{RESET}")

    if problems:
        # a numbering that skips two ids needs an explanation, not silence
        print(f"  {DIM}{len(problems)} saved report(s) could not be read, "
              f"so the ids skip them:{RESET}")
        for path, why in problems[:4]:
            print(f"    {DIM}{os.path.basename(path)}: {why}{RESET}")
        if len(problems) > 4:
            print(f"    {DIM}... and {len(problems) - 4} more{RESET}")
        if any(why == "permission denied" for _, why in problems):
            print(f"  {DIM}these were written by sudo before 0.32.0. "
                  f"Repair them with:{RESET}")
            print(f"  {DIM}  sudo chown -R \"$USER\" ~/.local/share/netriage{RESET}")

    if len(found) > 1:
        print(f"  {DIM}compare any two by id: netriage compare "
              f"{found[0][0]} {found[-1][0]}{RESET}")
    print()
    return 0


def cmd_scan(args):
    _apply_defaults(args)
    if args.wired:
        print("scan needs a radio; run it without --wired.", file=sys.stderr)
        return 2
    iface, medium = resolve_interface(args, wireless_only=True)
    if not iface:
        print("No wireless interface found.", file=sys.stderr)
        return 2
    if not _exists(iface):
        print(f"No such interface: {iface}", file=sys.stderr)
        return 2
    if medium != "wifi":
        print(f"{iface} is not a wireless interface; scanning needs a radio.",
              file=sys.stderr)
        return 2

    link = wifi.link(iface)
    aps = wifi.trust_live_signal(wifi.scan(iface), link)
    if args.redact:
        aps = util.redact(aps)
    if args.json:
        print(json.dumps(aps, indent=2, ensure_ascii=False))
    else:
        print_scan(aps, connected_bssid=link.get("bssid"), iface=iface)

    return 0


def cmd_watch(args):
    _apply_defaults(args)
    if args.wired:
        print("watch needs a radio; run it without --wired.", file=sys.stderr)
        return 2
    iface, medium = resolve_interface(args, wireless_only=True)
    if not iface:
        print("No wireless interface found.", file=sys.stderr)
        return 2
    if medium != "wifi":
        print(f"{iface} is not a wireless interface; watch needs a radio.",
              file=sys.stderr)
        return 2
    _rows, path = soakmod.run(iface, minutes=args.minutes, interval=args.interval,
                              out=args.out, offline=args.offline,
                              redact=args.redact)
    # a directory named by --out is not a recording; only a real file counts
    return 0 if os.path.isfile(path) else 2


def cmd_read(args):
    """read ID - the saved run again, in the shape of a live one."""
    _apply_defaults(args)
    picked, problem = _resolve_run(getattr(args, "run", ""))
    if problem:
        print(problem, file=sys.stderr)
        return 2
    path, facts = picked
    if args.redact:
        facts = util.redact(facts)

    render.print_replay(logs.number_of(path) or "?", facts)
    print_facts(facts)
    print_network(facts)
    print_findings(facts.get("findings") or [])
    if facts.get("aps"):
        print_scan(facts["aps"],
                   connected_bssid=(facts.get("link") or {}).get("bssid"),
                   iface=facts.get("iface"))
    print_summary(facts)
    print()
    return 0


def cmd_report(args):
    """report [FILE|ID] - Markdown for one saved run, newest by default."""
    _apply_defaults(args)
    token = getattr(args, "file", None)
    if token:
        picked, problem = _resolve_run(token)
        if problem:
            print(problem, file=sys.stderr)
            return 2
        path, facts = picked
    else:
        path = _latest()
        if not path:
            print("No saved run found. Run 'netriage' first.", file=sys.stderr)
            return 2
        facts, problem = _read_report(path)
        if problem:
            print(problem, file=sys.stderr)
            return 2
    if getattr(args, "redact", False):
        facts = util.redact(facts)

    print(report.markdown(facts, path))
    return 0


# ---------------------------------------------------------------- storage

def _footer(seconds, path):
    """How long it took and where the record went."""
    first = f"cooked for {seconds}s"
    shown = short_path(path)
    second = f"record saved at {shown}"
    combined = f"{first} · {second}"

    if len(combined) <= rule_width():
        return combined

    if len(second) > rule_width():
        room = rule_width() - len("record saved at ")
        shown = util.shorten_middle(shown, room)
        second = f"record saved at {shown}"
        combined = f"{first} · {second}"
        if len(combined) <= rule_width():
            return combined

    return f"{first}\n{second}"


# ---------------------------------------------------------------- entry


def _set_data_dir(value):
    """Apply --data-dir, and remember it so later commands agree."""
    if value.strip().lower() == "default":
        util.remember_data_dir(None)
        os.environ.pop("NETRIAGE_DATA_DIR", None)
        print(f"default location restored: {short_path(util.log_dir())}")
        print(f"{DIM}reports and recordings will be written there from now on{RESET}")
        return 0

    path = os.path.expanduser(value)
    if not os.path.isabs(path):
        print(f"--data-dir needs an absolute path, got {value!r}", file=sys.stderr)
        return 2

    existed = os.path.isdir(path)
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
    except OSError as exc:
        print(f"Cannot use {path}: {exc.strerror}", file=sys.stderr)
        return 2

    # sudo would leave a root-owned directory the human cannot write to, and
    # their own runs would fail there. Only the one we just created is claimed;
    # a directory that already existed belongs to whoever made it.
    if not existed:
        util.claim_to_user(path)
        util.claim_to_user(os.path.dirname(path))

    if not os.access(path, os.W_OK):
        print(f"Cannot write to {path}", file=sys.stderr)
        return 2

    os.environ["NETRIAGE_DATA_DIR"] = path
    if util.remember_data_dir(path) is None:
        print(f"reports and recordings go to {short_path(path)}")
        print(f"{YELLOW}could not save the setting: {short_path(util.config_dir())} "
              f"is not writable{RESET}")
        print(f"{DIM}this run uses it; pass --data-dir again next time{RESET}")
        return None

    print(f"reports and recordings now go to {short_path(path)}")
    print(f"{DIM}remembered; every later command uses it, with or without sudo{RESET}")
    return None


def _global_options():
    """Options that work before and after the command name."""
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--iface", metavar="NAME", default=argparse.SUPPRESS,
                        help="interface to test (default: the one in use)")
    media = parent.add_mutually_exclusive_group()
    media.add_argument("--wired", action="store_true", default=argparse.SUPPRESS,
                       help="test the cable instead of the radio")
    media.add_argument("--wifi", action="store_true", default=argparse.SUPPRESS,
                       help="test the radio instead of the cable")
    parent.add_argument("--offline", action="store_true", default=argparse.SUPPRESS,
                        help="never touch the internet: no speed test, no public DNS, "
                             "no captive portal check")
    parent.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="print JSON instead of text (the default run and scan)")
    parent.add_argument("--data-dir", metavar="PATH", default=argparse.SUPPRESS,
                        help="where reports are kept (absolute path; remembered; "
                             "'default' forgets it)")
    parent.add_argument("--redact", action="store_true", default=argparse.SUPPRESS,
                        help="mask addresses, names and SSIDs before printing or saving")
    parent.add_argument("--debug", action="store_true", default=argparse.SUPPRESS,
                        help=argparse.SUPPRESS)
    parent.add_argument("--version", action="version",
                        version=f"netriage {__version__}\npackage  "
                                f"{os.path.dirname(__file__)}\npython   {sys.executable}")
    return parent


_GLOBAL_DEFAULTS = {
    "data_dir": None,
    "iface": None, "wired": False, "wifi": False,
    "offline": False, "json": False, "redact": False,
    "debug": False,
}


def _apply_defaults(args):
    """Fill in the flags the parser suppressed, so any caller sees them."""
    for name, fallback in _GLOBAL_DEFAULTS.items():
        if not hasattr(args, name):
            setattr(args, name, fallback)
    return args


def build_parser():
    common = _global_options()
    parser = argparse.ArgumentParser(
        prog="netriage",
        parents=[common],
        description="Field network diagnostics: wired and wireless",
    )
    parser.set_defaults(func=cmd_quick)
    sub = parser.add_subparsers(dest="command")
    names = []

    scan = sub.add_parser("scan", parents=[common],
                          help="nearby access points and channel use")
    scan.set_defaults(func=cmd_scan)
    names.append("scan")

    watch = sub.add_parser("watch", parents=[common],
                           help="sample over time to catch an intermittent fault")
    watch.add_argument("--minutes", type=float, default=10,
                       help="how long to record, in minutes (default 10)")
    watch.add_argument("--interval", type=float, default=5,
                       help="seconds between samples (default 5)")
    watch.add_argument("--out", metavar="FILE",
                       help="CSV path (default: alongside the reports, watch-*.csv)")
    watch.set_defaults(func=cmd_watch)
    names.append("watch")

    read = sub.add_parser("read", parents=[common],
                          help="re-print a saved run: netriage read 42")
    read.add_argument("run", metavar="ID",
                      help="run id from netriage history")
    read.set_defaults(func=cmd_read)
    names.append("read")

    report = sub.add_parser("report", parents=[common],
                            help="markdown write-up of a saved run")
    report.add_argument("file", nargs="?", metavar="FILE",
                        help="saved run to render (default: the newest)")
    report.set_defaults(func=cmd_report)
    names.append("report")

    cmp_cmd = sub.add_parser("compare", parents=[common],
                             help="diff two saved runs, or the last two")
    cmp_cmd.add_argument("files", nargs="*", metavar="FILE",
                         help="two runs to diff, or three or more for a trend table")
    cmp_cmd.set_defaults(func=cmd_compare)
    names.append("compare")

    hist = sub.add_parser("history", parents=[common], help="saved runs as a table")
    hist.set_defaults(func=cmd_history)
    names.append("history")

    # built from what was registered, so a new command cannot be left out
    parser.epilog = ("With no command, netriage measures and prints a verdict. "
                     f"Commands: {', '.join(names)}.")
    return parser


# Commands that only read files, so they work on any platform.
_READ_ONLY_COMMANDS = (cmd_compare, cmd_history, cmd_read, cmd_report)


def main(argv=None):
    try:
        code = _main(argv)
        # Flush here, where a closed pipe can still be handled; the
        # interpreter's exit-time flush would print a warning and exit 120.
        try:
            sys.stdout.flush()
        except BrokenPipeError:
            _silence_stdout()
        return code
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130
    except BrokenPipeError:
        _silence_stdout()
        return 0


def _silence_stdout():
    """Point stdout at devnull so a closed pipe cannot fail again at exit."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except (OSError, ValueError):
        pass


def _main(argv=None):
    args = _apply_defaults(build_parser().parse_args(argv))

    if getattr(args, "data_dir", None):
        code = _set_data_dir(args.data_dir)
        if code is not None:
            return code
        if args.func is cmd_quick and args.command is None:
            # "set the directory" and "measure something" are different
            # actions; a bare --data-dir only does the first
            return 0

    if os.environ.get("NO_COLOR"):
        util.disable_color()

    if args.json and args.func not in (cmd_quick, cmd_scan):
        print(f"--json is not produced by '{args.command or 'this command'}'; "
              f"it applies to the default run and to scan.", file=sys.stderr)
        return 2

    # These only read files; they are useful on any machine, including a Mac
    # that is looking at reports copied from the laptop.
    if args.func in _READ_ONLY_COMMANDS:
        return args.func(args)

    if sys.platform == "darwin":
        print(
            "netriage: this tool reads Linux wireless interfaces and runs on "
            "Linux; start it on the machine you are testing.",
            file=sys.stderr,
        )
        return 2

    wireless_only = args.func in (cmd_scan, cmd_watch)
    _, medium = resolve_interface(args, wireless_only=wireless_only)

    # scan has a working alternative (nmcli); only the commands that read link
    # details really need iw.
    needs_iw = args.func in (cmd_quick, cmd_watch)
    if medium == "wifi" and needs_iw and not wifi.have_iw():
        print(
            "netriage: 'iw' is missing and this test needs the wireless card.\n"
            "           Install it:  sudo apt install iw\n"
            "           Or test the cable:  netriage --wired",
            file=sys.stderr,
        )
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
