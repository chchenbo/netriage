"""Tests for the report shell: header, summary and colour handling."""

import io
import json
import os
import re
from contextlib import redirect_stdout
from datetime import datetime

import pytest

from netriage import cli, diagnose, logs, report, util


def facts(findings=None, aps=0, gateway=2.1):
    return {
        "iface": "wlp5s0",
        "medium": "wifi",
        "link": {"connected": True, "ssid": "Home", "bssid": "aa", "signal": -60,
                 "channel": 36, "band": "5 GHz", "width_mhz": 80, "tx_rate": 800.0},
        "survey": {}, "station": {},
        "address": {"ip": "10.0.0.5/24", "gateway": "10.0.0.1"},
        "ping_gateway": {"avg": gateway, "loss": 0, "jitter": 0.3},
        "aps": [{"bssid": str(i)} for i in range(aps)],
        "findings": findings or [],
    }


def capture(func, *args):
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        func(*args)
    return buffer.getvalue()


def test_summary_all_clear():
    out = capture(cli.print_summary, facts(aps=11))
    assert "all clear" in out
    assert "11 APs" in out
    assert "gateway 2.1 ms" in out


def test_summary_counts_warnings_and_criticals():
    warning = [{"severity": "warning", "title": "t", "detail": "d", "action": "a"}]
    critical = [{"severity": "critical", "title": "t", "detail": "d", "action": "a"}]

    assert "1 warning" in capture(cli.print_summary, facts(findings=warning))
    assert "2 critical" in capture(cli.print_summary, facts(findings=critical * 2))


def test_summary_without_gateway_omits_it():
    out = capture(cli.print_summary, facts(gateway=None))
    assert "gateway" not in out


def test_header_hides_banner_when_asked():
    util.enable_color()
    try:
        out = capture(cli._print_header, False, True)
    finally:
        util.disable_color()
    assert "NETRIAGE" not in out
    assert "netriage" in out.lower()


def test_header_is_one_plain_line_when_piped():
    # redirect_stdout gives a StringIO, which is not a tty
    out = capture(cli._print_header, False, False)
    assert out.count("\n") == 1
    assert "netriage" in out
    assert "at" not in out  # the user@host form only appears on a terminal


def test_summary_rule_fits_the_terminal(monkeypatch):
    # section() lives in util, while cli imported rule_width by value
    monkeypatch.setattr(util, "rule_width", lambda: 40)
    monkeypatch.setattr(cli, "rule_width", lambda: 40)
    out = capture(cli.print_summary, facts())
    rule = [line for line in out.splitlines() if set(line.strip()) == {"-"}]
    assert rule and len(rule[0].strip()) == 40


def test_summary_reports_notes_not_all_clear():
    note = [{"severity": "info", "title": "t", "detail": "d", "action": "a"}]
    out = capture(cli.print_summary, facts(findings=note))
    assert "1 note" in out
    assert "all clear" not in out


def test_log_dir_is_stable_and_private(tmp_path, monkeypatch):
    """Not <home>/netriage: that collides with the command name in shells."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("SUDO_USER", raising=False)
    path = util.log_dir()
    assert "netriage" in path
    assert path.endswith("logs"), path
    assert oct(os.stat(path).st_mode & 0o777) == "0o700"
    assert (tmp_path / "netriage").exists() is False


def test_save_numbers_files_per_day(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, "log_dir", lambda: str(tmp_path))
    first = logs.save({}, "quick")
    second = logs.save({}, "quick")

    stamp = datetime.now().strftime("%Y%m%d")
    assert os.path.basename(first) == f"netriage-{stamp}-0001.json"
    assert os.path.basename(second) == f"netriage-{stamp}-0002.json"


def test_save_records_which_command(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, "log_dir", lambda: str(tmp_path))
    path = logs.save({"iface": "wlp5s0"}, "scan")
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    assert payload["command"] == "scan"
    assert payload["iface"] == "wlp5s0"


def test_latest_picks_the_highest_sequence(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, "log_dir", lambda: str(tmp_path))
    for _ in range(3):
        logs.save({}, "quick")
    assert logs.latest().endswith("-0003.json")


def test_new_day_restarts_at_one(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, "log_dir", lambda: str(tmp_path))
    (tmp_path / "netriage-20250101-0007.json").write_text("{}", encoding="utf-8")
    assert logs.next_index(str(tmp_path), "20250101") == 8
    assert logs.next_index(str(tmp_path), "20250102") == 1


def test_footer_merges_time_and_destination(monkeypatch, tmp_path):
    monkeypatch.setattr(util, "user_home", lambda: str(tmp_path))
    path = str(tmp_path / "netriage_logs" / "netriage-20261114-0002.json")
    line = cli._footer(10, path)

    assert line.startswith("cooked for 10s")
    assert "record saved at ~/netriage_logs/netriage-20261114-0002.json" in line


def test_footer_shows_a_tilde_path(monkeypatch, tmp_path):
    monkeypatch.setattr(util, "user_home", lambda: str(tmp_path))
    path = str(tmp_path / "netriage" / "netriage_logs" / "netriage-20261114-0008.json")
    assert "~/netriage/netriage_logs/netriage-20261114-0008.json" in cli._footer(6, path)


def test_footer_fits_a_long_path_into_the_rule(monkeypatch, tmp_path):
    """A wide terminal used to print a 130-character footer line."""
    monkeypatch.setattr(util, "user_home", lambda: str(tmp_path))
    deep = tmp_path / ("x" * 40) / ("y" * 40)
    path = str(deep / "netriage-20261114-0008.json")

    lines = cli._footer(6, path).splitlines()
    assert all(len(line) <= cli.rule_width() for line in lines), lines
    assert lines[0].startswith("cooked for")
    assert lines[-1].startswith("record saved at")


def test_footer_stays_on_one_line_when_it_fits(monkeypatch, tmp_path):
    monkeypatch.setattr(util, "user_home", lambda: str(tmp_path))
    path = str(tmp_path / "Documents" / "log" / "netriage-0008.json")

    lines = cli._footer(6, path).splitlines()
    assert len(lines) == 1, lines
    assert lines[0].endswith(".json"), "the filename must never be split"


def test_no_report_line_overhangs_the_rule():
    """Every line must stay within the width of the section rule."""
    from netriage import diagnose

    facts = {
        "iface": "wlp5s0", "medium": "wifi",
        "link": {"connected": True, "ssid": "MagentaWLAN-P2S3_5GHz",
                 "bssid": "74:90:bc:a7:15:9f", "signal": -69, "channel": 36,
                 "band": "5 GHz", "width_mhz": 160},
        "survey": {}, "station": {"retry_pct": 17.8},
        "address": {"ip": "192.168.2.201/24", "gateway": "192.168.2.1"},
        "dns_servers": ["192.168.2.1"],
        "dns_school": {"ok": True, "ms": 1, "server": "192.168.2.1"},
        "dns_public": {"ok": True, "ms": 4, "server": "1.1.1.1"},
        "ping_gateway": {"avg": 2.6, "loss": 0, "jitter": 0.7},
        "ping_internet": {"avg": 11.0, "loss": 0, "jitter": 7.1},
        "captive": False, "busy_pct": None, "busy_source": None,
        "speed": {"mbps": 9.5, "peak": 44.2},
        "latency": {"avg": 53.4, "jitter": 0.8},
        "aps": [
            {"bssid": "74:90:bc:a7:15:a2", "ssid": "", "channel": 36, "signal": -73},
            {"bssid": "20:37:f0:c4:75:a3", "ssid": "", "channel": 6, "signal": -80},
            {"bssid": "3c:a6:2f:6f:ee:e7", "ssid": "HomeNet", "channel": 1, "signal": -88},
        ],
    }
    findings = [f.__dict__ for f in diagnose.analyze(facts)]
    facts["findings"] = findings

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        cli.print_facts(facts)
        cli.print_network(facts)
        cli.print_findings(findings)
        cli.print_scan(facts["aps"])
        cli.print_summary(facts)

    width = cli.rule_width()
    overlong = [line for line in buffer.getvalue().splitlines() if len(line) > width]
    assert not overlong, f"{len(overlong)} line(s) exceed {width}: {overlong}"


def finding(severity):
    return {"severity": severity, "title": "Something is wrong",
            "detail": "EXPLANATION", "action": "DO THIS"}


def test_findings_are_one_line_by_default():
    """The heading is the answer; the rest is reference material."""
    for severity in ("info", "warning", "critical"):
        out = capture(cli.print_findings, [finding(severity)])
        assert "EXPLANATION" not in out
        assert "DO THIS" not in out
        assert "Something is wrong" in out




def test_run_ends_with_a_blank_line(monkeypatch):
    """Otherwise the shell prompt sits on top of the last report line."""
    def stub(iface, medium, do_speed=True, do_scan=True, offline=False,
             on_phase=None):
        return {
            "iface": iface, "medium": "wifi",
            "link": {"connected": True, "ssid": "S", "bssid": "aa", "signal": -69,
                     "channel": 36, "band": "5 GHz"},
            "survey": {}, "station": {"retry_pct": 4.0},
            "address": {"ip": "10.0.0.5/24", "gateway": "10.0.0.1"},
            "dns_servers": ["10.0.0.1"],
            "dns_school": {"ok": True, "ms": 1, "server": "10.0.0.1"},
            "dns_public": {"ok": True, "ms": 4, "server": "1.1.1.1"},
            "ping_gateway": {"avg": 2.3, "loss": 0, "jitter": 0.2},
            "ping_internet": {"avg": 5.0, "loss": 0, "jitter": 0.8},
            "captive": False, "aps": [], "busy_pct": None, "busy_source": None,
            "speed": {"mbps": 1.0, "peak": 1.0}, "latency": {"avg": 5.0, "jitter": 0.1},
            "findings": [],
        }

    monkeypatch.setattr(cli, "collect", stub)
    monkeypatch.setattr(cli, "resolve_interface",
                        lambda a, wireless_only=False: ("wlp5s0", "wifi"))
    monkeypatch.setattr(cli, "_exists", lambda iface: True)
    monkeypatch.setattr(cli, "_report_path",
                        lambda kind: "/tmp/netriage_logs/netriage-20261114-0001.json")
    monkeypatch.setattr(cli, "_save", lambda facts, kind, path=None: path or "")

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        cli.cmd_quick(cli.build_parser().parse_args([]))
    assert buffer.getvalue().endswith("\n\n")


def render_screen(raw):
    """A tiny terminal: enough of CR, LF, erase-line and cursor-up to check layout."""
    rows = [[]]
    row = col = 0
    index = 0
    while index < len(raw):
        char = raw[index]
        if char == "\x1b" and raw[index + 1:index + 2] == "[":
            end = index + 2
            while end < len(raw) and not raw[end].isalpha():
                end += 1
            final = raw[end]
            if final == "K":
                del rows[row][col:]
            elif final == "A":
                row = max(0, row - 1)
                col = min(col, len(rows[row]))
            index = end + 1
            continue
        if char == "\n":
            row += 1
            while len(rows) <= row:
                rows.append([])
            col = 0
        elif char == "\r":
            col = 0
        else:
            while len(rows[row]) < col:
                rows[row].append(" ")
            if col < len(rows[row]):
                rows[row][col] = char
            else:
                rows[row].append(char)
            col += 1
        index += 1
    return ["".join(line).rstrip() for line in rows]


def test_the_report_starts_below_the_identity_line(monkeypatch, tmp_path):
    """Exactly one blank line between the identity line and the report."""
    monkeypatch.setattr(cli, "resolve_interface",
                        lambda a, wireless_only=False: ("wlp5s0", "wifi"))
    monkeypatch.setattr(cli, "_exists", lambda iface: True)
    monkeypatch.setattr(cli, "_report_path", lambda kind: str(tmp_path / "r.json"))
    monkeypatch.setattr(cli, "_save", lambda facts, kind, path=None: path or "")
    monkeypatch.setattr(cli, "collect", lambda *a, **k: {
        "iface": "wlp5s0", "medium": "wifi",
        "link": {"connected": True, "ssid": "S", "bssid": "aa", "signal": -60,
                 "channel": 36, "band": "5 GHz"},
        "station": {}, "survey": {}, "address": {}, "findings": [],
    })

    class FakeTTY(io.StringIO):
        def isatty(self):
            return True

    stream = FakeTTY()
    real_stdout = cli.sys.stdout
    cli.sys.stdout = stream
    try:
        cli.cmd_quick(cli.build_parser().parse_args([]))
    finally:
        cli.sys.stdout = real_stdout

    screen = render_screen(stream.getvalue())
    identity = next(i for i, line in enumerate(screen) if line.startswith("ran by "))
    assert screen[identity + 1] == "", f"expected one blank line, got {screen[identity + 1]!r}"
    assert screen[identity + 2].startswith("LINK")




def aps_for_scan():
    return [
        {"bssid": "74:90:bc:a7:15:9f", "ssid": "MagentaWLAN-P2S3_5GHz", "channel": 104,
         "signal": -58, "util_pct": 8.0, "stations": 9},
        {"bssid": "1a:25:2a:98:55:f6", "ssid": "151******77的nova 14 Ultra", "channel": 13,
         "signal": -84, "util_pct": 0.0, "stations": 1},
        {"bssid": "aa:bb:cc:dd:ee:ff", "ssid": "Short", "channel": 1, "signal": -90},
    ]


def test_connected_access_point_is_marked():
    """In a list of fifteen, you need to find your own AP instantly."""
    out = capture(cli.print_scan, aps_for_scan(), "74:90:bc:a7:15:9f")
    rows = [line for line in out.splitlines() if "dBm" in line]
    marked = [row for row in rows if row.startswith("* ")]
    assert len(marked) == 1
    assert "74:90:BC:A7:15:9F" in marked[0].upper()


def test_long_ssid_is_trimmed_to_the_rule():
    out = capture(cli.print_scan, aps_for_scan(), None)
    width = cli.rule_width()
    overlong = [line for line in out.splitlines() if len(line) > width]
    assert not overlong, overlong
    assert "\u2026" in out, "a trimmed SSID should show an ellipsis"


def test_marker_keeps_the_columns_aligned():
    marked = capture(cli.print_scan, aps_for_scan(), "74:90:bc:a7:15:9f").splitlines()
    plain = capture(cli.print_scan, aps_for_scan(), None).splitlines()
    for left, right in zip(marked, plain):
        if "dBm" in left:
            assert left[2:] == right[2:], (left, right)


def test_identity_uses_the_human_under_sudo(monkeypatch):
    """getpass() reports root under sudo; the person is SUDO_USER."""
    monkeypatch.setenv("SUDO_USER", "ubuntu")

    class FakeTTY(io.StringIO):
        def isatty(self):
            return True

    fake = FakeTTY()
    real = cli.sys.stdout
    cli.sys.stdout = fake
    try:
        cli._print_header(False)
    finally:
        cli.sys.stdout = real

    out = fake.getvalue()
    assert "ubuntu@" in out
    assert "root@" not in out


def test_collect_measures_upload(monkeypatch):
    """The report can only print it if collect actually measured it."""
    from netriage import media, network, wired

    monkeypatch.setattr(media, "kind_of", lambda iface: "wired")
    monkeypatch.setattr(wired, "link", lambda iface: {"connected": True, "speed": 1000,
                                                     "duplex": "full"})
    monkeypatch.setattr(wired, "counters", lambda iface: {})
    monkeypatch.setattr(wired, "lldp", lambda iface: {})
    monkeypatch.setattr(network, "address",
                        lambda iface: {"ip": "10.0.0.5/24", "gateway": "10.0.0.1"})
    monkeypatch.setattr(network, "dns_servers", lambda iface: ["10.0.0.1"])
    monkeypatch.setattr(network, "ping", lambda iface, target, count=4:
                        {"avg": 1.0, "loss": 0, "jitter": 0.1})
    monkeypatch.setattr(network, "resolve", lambda server, name, timeout=4:
                        {"ok": True, "ms": 3, "server": server})
    monkeypatch.setattr(network, "captive", lambda iface: {"intercepted": False, "url": ""})
    monkeypatch.setattr(network, "download",
                        lambda seconds=8: {"mbps": 100.0, "peak": 110.0})
    monkeypatch.setattr(network, "upload",
                        lambda: {"mbps": 20.0, "seconds": 2.0, "bytes": 5_000_000})
    monkeypatch.setattr(network, "latency_ms", lambda: {"avg": 5.0, "jitter": 0.5})

    facts = cli.collect("enp3s0", "wired")
    assert facts["upload"]["mbps"] == 20.0
    assert facts["speed"]["mbps"] == 100.0
    assert facts["portal_url"] == ""


def test_collect_skips_upload_when_speed_is_off(monkeypatch):
    from netriage import media, network, wired

    monkeypatch.setattr(media, "kind_of", lambda iface: "wired")
    monkeypatch.setattr(wired, "link", lambda iface: {"connected": True})
    monkeypatch.setattr(wired, "counters", lambda iface: {})
    monkeypatch.setattr(wired, "lldp", lambda iface: {})
    monkeypatch.setattr(network, "address",
                        lambda iface: {"ip": "10.0.0.5/24", "gateway": "10.0.0.1"})
    monkeypatch.setattr(network, "dns_servers", lambda iface: ["10.0.0.1"])
    monkeypatch.setattr(network, "ping", lambda iface, target, count=4: None)
    monkeypatch.setattr(network, "resolve", lambda server, name, timeout=4: {"ok": None})
    monkeypatch.setattr(network, "captive", lambda iface: {"intercepted": None, "url": ""})

    facts = cli.collect("enp3s0", "wired", do_speed=False)
    assert facts["upload"] == {"skipped": True}


def five_runs(tmp_path, monkeypatch):
    import json as json_module

    from netriage import logs

    folder = tmp_path / "netriage" / "netriage_logs"
    folder.mkdir(parents=True)
    for index, signal in enumerate([-71, -70, -68, -64, -60], 1):
        (folder / f"netriage-20261116-{index:04d}.json").write_text(json_module.dumps({
            "timestamp": f"2026-11-16T09:0{index}:00",
            "label": "302",
            "link": {"signal": signal, "channel": 36, "tx_rate": 780,
                     "ssid": "School"},
            "busy_pct": 12.0,
            "station": {"retry_pct": 13.0 - index},
            "speed": {"mbps": 250 + index * 8},
            "upload": {"mbps": 12 + index},
            "ping_gateway": {"avg": 2.6, "loss": 0},
            "ping_internet": {"avg": 5.5, "loss": 0},
            "findings": [],
        }), encoding="utf-8")

    monkeypatch.setattr(logs, "log_dir", lambda: str(folder))
    return folder


def test_history_is_quiet_when_it_shows_everything(tmp_path, monkeypatch):
    five_runs(tmp_path, monkeypatch)
    out = capture(cli.cmd_history, cli.build_parser().parse_args(["history"]))
    assert "showing" not in out


def test_history_shows_every_run_by_default(tmp_path, monkeypatch):
    five_runs(tmp_path, monkeypatch)
    out = capture(cli.cmd_history, cli.build_parser().parse_args(["history"]))
    for number in range(1, 6):
        assert str(number) in out
    assert "showing" not in out


def test_scan_explains_the_star():
    aps = [{"bssid": "aa:bb:cc:dd:ee:01", "ssid": "Mine", "channel": 44, "signal": -58}]
    out = capture(cli.print_scan, aps, "aa:bb:cc:dd:ee:01")
    assert "* is the access point you are connected to" in out


def test_scan_has_no_legend_without_a_match():
    aps = [{"bssid": "aa:bb:cc:dd:ee:02", "ssid": "Other", "channel": 44, "signal": -58}]
    out = capture(cli.print_scan, aps, "aa:bb:cc:dd:ee:01")
    assert "connected to" not in out


def test_history_rows_stay_inside_the_rule(tmp_path, monkeypatch):
    five_runs(tmp_path, monkeypatch)
    out = capture(cli.cmd_history, cli.build_parser().parse_args(["history"]))
    width = cli.rule_width()
    overlong = [line for line in out.splitlines() if len(line) > width]
    assert not overlong, overlong


def test_history_names_the_network(tmp_path, monkeypatch):
    """Runs happen on different SSIDs; the table has to say which."""
    five_runs(tmp_path, monkeypatch)
    out = capture(cli.cmd_history, cli.build_parser().parse_args(["history"]))
    assert "ssid" in out
    assert "School" in out


def test_history_ssid_keeps_both_ends():
    """Truncating the tail would make 5 GHz and 2.4 GHz indistinguishable."""
    short = util.shorten_middle("MagentaWLAN-P2S3_5GHz", 13)
    other = util.shorten_middle("MagentaWLAN-P2S3_2.4GHz", 13)
    assert len(short) == 13
    assert short != other
    assert short.endswith("5GHz") or "5GH" in short


def test_history_columns_are_still_two_spaces_apart(tmp_path, monkeypatch):
    five_runs(tmp_path, monkeypatch)
    out = capture(cli.cmd_history, cli.build_parser().parse_args(["history"]))
    row = [line for line in out.splitlines() if line.lstrip().startswith("4  ")][0]
    fields = [part for part in re.split(r" {2,}", row.strip()) if part]
    assert len(fields) == 4, fields      # id when signal ssid


def test_history_spells_a_long_ssid_in_full(tmp_path, monkeypatch):
    """The network name is the point of the table; it must not be cut short."""
    import json as json_module

    from netriage import logs

    folder = tmp_path / "netriage" / "netriage_logs"
    folder.mkdir(parents=True)
    (folder / "netriage-20261116-0001.json").write_text(json_module.dumps({
        "timestamp": "2026-11-16T09:00:00", "label": "home",
        "link": {"ssid": "MagentaWLAN-P2S3_2.4GHz", "signal": -71},
        "findings": [],
    }), encoding="utf-8")
    monkeypatch.setattr(logs, "log_dir", lambda: str(folder))

    out = capture(cli.cmd_history, cli.build_parser().parse_args(["history"]))
    assert "MagentaWLAN-P2S3_2.4GHz" in out


def test_history_seconds_are_shown(tmp_path, monkeypatch):
    five_runs(tmp_path, monkeypatch)
    out = capture(cli.cmd_history, cli.build_parser().parse_args(["history"]))
    assert "09:01:00" in out


def test_history_id_column_does_not_shift_with_a_marker(tmp_path, monkeypatch):
    """Colour escapes must not be counted when padding the flag column."""
    import json as json_module

    from netriage import logs

    folder = tmp_path / "netriage" / "netriage_logs"
    folder.mkdir(parents=True)
    for number, findings in ((1, []), (2, [{"severity": "critical", "title": "c"}])):
        (folder / f"netriage-20261116-{number:04d}.json").write_text(json_module.dumps({
            "timestamp": f"2026-11-16T09:0{number}:00", "label": "x",
            "link": {"ssid": "Net", "signal": -60}, "findings": findings,
        }), encoding="utf-8")
    monkeypatch.setattr(logs, "log_dir", lambda: str(folder))

    out = capture(cli.cmd_history, cli.build_parser().parse_args(["history"]))
    dates = [line.index("11-16") for line in out.splitlines() if "11-16" in line]
    assert len(dates) == 2 and len(set(dates)) == 1, out


def test_history_explains_the_markers(tmp_path, monkeypatch):
    """A marker nobody can decode is decoration."""
    import json as json_module

    from netriage import logs

    folder = tmp_path / "netriage" / "netriage_logs"
    folder.mkdir(parents=True)
    (folder / "netriage-20261116-0001.json").write_text(json_module.dumps({
        "timestamp": "2026-11-16T09:00:00", "label": "x",
        "link": {"ssid": "Net", "signal": -60},
        "findings": [{"severity": "warning", "title": "w"}],
    }), encoding="utf-8")
    monkeypatch.setattr(logs, "log_dir", lambda: str(folder))

    out = capture(cli.cmd_history, cli.build_parser().parse_args(["history"]))
    assert "! = a warning" in out


def test_history_has_no_legend_without_markers(tmp_path, monkeypatch):
    five_runs(tmp_path, monkeypatch)
    out = capture(cli.cmd_history, cli.build_parser().parse_args(["history"]))
    assert "! = a warning" not in out


def saved_run_payload(**overrides):
    payload = {
        "timestamp": "2026-11-15T19:56:51", "label": None, "command": "quick",
        "version": "0.14.1", "user": "ubuntu",
        "iface": "wlp5s0", "medium": "wifi",
        "link": {"connected": True, "ssid": "School", "bssid": "aa:bb",
                 "signal": -59, "channel": 104, "width_mhz": 80, "band": "5 GHz",
                 "tx_rate": 585.0},
        "station": {"retry_pct": 4.0}, "survey": {}, "busy_pct": None,
        "busy_source": None,
        "address": {"ip": "10.0.0.5/24", "gateway": "10.0.0.1"},
        "dns_servers": ["10.0.0.1"],
        "ping_gateway": {"avg": 2.0, "loss": 0, "jitter": 0.4},
        "ping_internet": {"avg": 4.7, "loss": 0, "jitter": 0.7},
        "dns_school": {"ok": True, "ms": 2, "server": "10.0.0.1"},
        "dns_public": {"ok": True, "ms": 3, "server": "1.1.1.1"},
        "captive": False, "portal_url": "",
        "speed": {"mbps": 252.2, "peak": 316.1}, "upload": {"mbps": 18.0},
        "latency": {"avg": 52.9, "jitter": 14.4}, "aps": [], "findings": [],
    }
    payload.update(overrides)
    return payload


def two_saved_runs(tmp_path, monkeypatch):
    import json as json_module

    from netriage import logs

    folder = tmp_path / "netriage" / "netriage_logs"
    folder.mkdir(parents=True)
    for number, stamp in ((1, "2026-11-15T18:00:00"), (2, "2026-11-15T19:56:51")):
        (folder / f"netriage-20261115-{number:04d}.json").write_text(
            json_module.dumps(saved_run_payload(timestamp=stamp)), encoding="utf-8")
    monkeypatch.setattr(logs, "log_dir", lambda: str(folder))
    return folder


def test_history_shows_the_year(tmp_path, monkeypatch):
    five_runs(tmp_path, monkeypatch)
    out = capture(cli.cmd_history, cli.build_parser().parse_args(["history"]))
    assert "2026-11-16 09:01:00" in out


def test_history_keeps_a_long_ssid_with_the_year_shown(tmp_path, monkeypatch):
    """The unit moved to the header to pay for the year; names must still fit."""
    import json as json_module

    from netriage import logs

    folder = tmp_path / "netriage" / "netriage_logs"
    folder.mkdir(parents=True)
    (folder / "netriage-20261116-0001.json").write_text(json_module.dumps({
        "timestamp": "2026-11-16T09:00:00", "label": None,
        "link": {"ssid": "MagentaWLAN-P2S3_5GHz", "signal": -59}, "findings": [],
    }), encoding="utf-8")
    monkeypatch.setattr(logs, "log_dir", lambda: str(folder))

    out = capture(cli.cmd_history, cli.build_parser().parse_args(["history"]))
    assert "MagentaWLAN-P2S3_5GHz" in out
    row = [line for line in out.splitlines() if "Magenta" in line][0]
    assert len(row) <= cli.rule_width()


def test_wired_report_has_no_access_point_section(monkeypatch, tmp_path):
    facts = {
        "medium": "wired", "iface": "enp3s0", "aps": [],
        "link": {"connected": True, "speed": 1000, "duplex": "full"},
        "address": {"ip": "10.0.0.5/24", "gateway": "10.0.0.1"},
        "ping_gateway": {"avg": 2.0, "loss": 0, "jitter": 0.2},
        "dns_school": {"ok": True, "ms": 3, "server": "10.0.0.1"},
        "dns_public": {"ok": True, "ms": 3, "server": "1.1.1.1"},
        "ping_internet": {"avg": 4.0, "loss": 0}, "captive": False,
        "findings": [],
    }
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        cli.print_facts(facts)
        cli.print_summary(facts)
    text = buffer.getvalue()
    assert "NEARBY" not in text
    assert "1000 Mb/s" in text
    assert "0 APs" not in text


def test_scan_marks_the_connected_access_point(monkeypatch):
    from netriage import wifi

    monkeypatch.setattr(wifi, "scan", lambda iface: [
        {"bssid": "aa:bb", "ssid": "Mine", "channel": 44, "signal": -58},
        {"bssid": "cc:dd", "ssid": "Other", "channel": 44, "signal": -70},
    ])
    monkeypatch.setattr(wifi, "link", lambda iface: {"bssid": "aa:bb"})
    monkeypatch.setattr(cli, "resolve_interface",
                        lambda args, wireless_only=False: ("wlp5s0", "wifi"))
    monkeypatch.setattr(cli, "_exists", lambda iface: True)
    out = capture(cli.cmd_scan, cli.build_parser().parse_args(["scan"]))
    assert "* " in out
    assert "the access point you are connected to" in out


def test_summary_counts_notes_alongside_warnings():
    """A verdict that ignores the notes hides half of what was printed."""
    findings = [
        {"severity": "warning", "title": "a", "detail": "", "action": ""},
        {"severity": "info", "title": "b", "detail": "", "action": ""},
        {"severity": "info", "title": "c", "detail": "", "action": ""},
    ]
    out = capture(cli.print_summary, {"findings": findings, "aps": [], "medium": "wifi"})
    assert "1 warning, 2 notes" in out


def test_summary_leads_with_critical():
    findings = [
        {"severity": "critical", "title": "a", "detail": "", "action": ""},
        {"severity": "warning", "title": "b", "detail": "", "action": ""},
        {"severity": "info", "title": "c", "detail": "", "action": ""},
    ]
    out = capture(cli.print_summary, {"findings": findings, "aps": [], "medium": "wifi"})
    assert "1 critical, 1 warning, 1 note" in out


def test_lldp_line_shows_no_dangling_placeholder():
    facts = {"medium": "wired", "iface": "eth0",
             "link": {"connected": True, "speed": 1000, "duplex": "full"},
             "lldp": {"switch": "Switch12", "port": "", "vlan": ""},
             "counters": {}}
    out = capture(cli.print_facts, facts)
    assert "Switch12" in out
    assert " / ?" not in out
    assert out.count("Switch12") == 1


def test_lldp_description_wraps_instead_of_overhanging():
    """Real switches name ports with whole sentences."""
    facts = {"medium": "wired", "iface": "eth0",
             "link": {"connected": True, "speed": 1000, "duplex": "full"},
             "lldp": {"switch": "Switch12-KellerUnten",
                      "port": "POI 1 - Infoscreen EG Primary", "vlan": ""},
             "counters": {}}
    out = capture(cli.print_facts, facts)
    assert all(len(line) <= cli.rule_width() for line in out.splitlines())
    assert "Switch12-KellerUnten" in out
    assert "POI 1 - Infoscreen EG Primary" in out


def test_short_lldp_stays_on_one_line():
    facts = {"medium": "wired", "iface": "eth0",
             "link": {"connected": True, "speed": 1000, "duplex": "full"},
             "lldp": {"switch": "Switch-B", "port": "Gi1/0/12", "vlan": "120"},
             "counters": {}}
    out = capture(cli.print_facts, facts)
    line = [row for row in out.splitlines() if "LLDP" in row][0]
    assert "Switch-B / Gi1/0/12 / VLAN 120" in line


def test_short_download_sample_is_labelled():
    """On a fast link the payload can finish in under a second."""
    facts = {"captive": False, "address": {"ip": "10.0.0.5/24", "gateway": "10.0.0.1"},
             "ping_gateway": {"avg": 0.2, "loss": 0, "jitter": 0.1},
             "ping_internet": {"avg": 1.2, "loss": 0, "jitter": 0.0},
             "dns_servers": ["10.0.0.1"],
             "dns_school": {"ok": True, "ms": 1, "server": "10.0.0.1"},
             "dns_public": {"ok": True, "ms": 3, "server": "1.1.1.1"},
             "upload": {"mbps": 200.0}, "latency": {"avg": 77.0, "jitter": 2.0},
             "speed": {"mbps": 375.6, "peak": 616.5, "seconds": 0.68}}
    out = capture(cli.print_network, facts)
    assert "0.7s sample" in out
    assert all(len(line) <= cli.rule_width() for line in out.splitlines())


def test_long_download_sample_has_no_note():
    facts = {"captive": False, "address": {"ip": "10.0.0.5/24", "gateway": "10.0.0.1"},
             "ping_gateway": {"avg": 0.2, "loss": 0, "jitter": 0.1},
             "ping_internet": {"avg": 1.2, "loss": 0, "jitter": 0.0},
             "dns_servers": ["10.0.0.1"],
             "dns_school": {"ok": True, "ms": 1, "server": "10.0.0.1"},
             "dns_public": {"ok": True, "ms": 3, "server": "1.1.1.1"},
             "upload": {"mbps": 200.0}, "latency": {"avg": 77.0, "jitter": 2.0},
             "speed": {"mbps": 10.6, "peak": 15.5, "seconds": 4.9}}
    assert "sample" not in capture(cli.print_network, facts)


def test_accent_is_pinned_when_truecolor_is_available(monkeypatch):
    """ANSI 36 is whatever the theme says; the brand colour should not be."""
    monkeypatch.setenv("COLORTERM", "truecolor")
    util.enable_color()
    try:
        assert util.CYAN == "\033[38;2;145;198;203m"
    finally:
        monkeypatch.delenv("COLORTERM", raising=False)
        util.disable_color()      # tests run without colour by default


def test_accent_falls_back_to_ansi_cyan(monkeypatch):
    monkeypatch.delenv("COLORTERM", raising=False)
    util.enable_color()
    try:
        assert util.CYAN == "\033[36m"
    finally:
        util.disable_color()


def test_unassociated_interface_measures_nothing(monkeypatch):
    """Probes would travel over another route and report its speed."""
    from netriage import media, network, wifi

    monkeypatch.setattr(media, "kind_of", lambda iface: "wifi")
    monkeypatch.setattr(wifi, "link", lambda iface: {"connected": False})

    def boom(*a, **k):
        raise AssertionError("measured over a link that is not up")

    for name in ("ping", "download", "upload", "resolve", "latency_ms", "captive"):
        monkeypatch.setattr(network, name, boom)

    facts = cli.collect("wlan0", "wifi")
    assert facts["speed"] == {"skipped": True}
    assert facts["upload"] == {"skipped": True}
    assert facts["dns_school"] is None
    assert facts["why_not_measured"]


def test_unassociated_report_says_so_instead_of_dashes():
    facts = {"medium": "wifi", "iface": "wlan0", "address": {},
             "link": {"connected": False}, "dns_school": None, "ping_internet": None,
             "why_not_measured": "not associated"}
    out = capture(cli.print_network, facts)
    assert "nothing above the link could be tested" in out
    assert "IPv4" not in out


def test_no_carrier_measures_nothing(monkeypatch):
    """A dead port still has a default route, and it is not this one."""
    from netriage import media, network, wired

    monkeypatch.setattr(media, "kind_of", lambda iface: "wired")
    monkeypatch.setattr(wired, "link", lambda iface: {"connected": False})
    monkeypatch.setattr(wired, "lldp", lambda iface: {})
    monkeypatch.setattr(wired, "counters", lambda iface: {})
    monkeypatch.setattr(network, "address", lambda iface: {})

    def boom(*a, **k):
        raise AssertionError("measured without carrier")

    for name in ("ping", "download", "upload", "resolve", "latency_ms", "captive"):
        monkeypatch.setattr(network, name, boom)

    facts = cli.collect("enp3s0", "wired")
    assert facts["speed"] == {"skipped": True}
    assert facts["dns_school"] is None
    assert facts["why_not_measured"] == "not associated"


def test_associated_without_address_measures_nothing(monkeypatch):
    from netriage import media, network, wifi

    monkeypatch.setattr(media, "kind_of", lambda iface: "wifi")
    monkeypatch.setattr(wifi, "link", lambda iface: {"connected": True})
    monkeypatch.setattr(wifi, "station", lambda iface: {})
    monkeypatch.setattr(wifi, "survey", lambda iface, freq=None: {})
    monkeypatch.setattr(network, "address", lambda iface: {})
    monkeypatch.setattr(network, "dns_servers", lambda iface: [])

    def boom(*a, **k):
        raise AssertionError("measured without an address")

    for name in ("ping", "download", "upload", "resolve", "latency_ms", "captive"):
        monkeypatch.setattr(network, name, boom)

    facts = cli.collect("wlan0", "wifi", do_scan=False)
    assert facts["why_not_measured"] == "no address"
    assert facts["speed"] == {"skipped": True}


def test_control_characters_never_reach_the_terminal():
    from netriage import wifi

    assert wifi.printable("\x1b]0;owned\x07School") == "]0;ownedSchool"
    facts = {"medium": "wifi", "iface": "wlan0",
             "link": {"connected": True, "ssid": "\x1b]0;owned\x07School",
                      "bssid": "aa", "signal": -50, "channel": 36, "band": "5 GHz"},
             "station": {}, "survey": {}}
    out = capture(cli.print_facts, facts)
    assert "\x1b]" not in out
    assert "\x07" not in out


def test_missing_ethtool_reads_as_unknown():
    facts = {"medium": "wired", "iface": "eth0",
             "link": {"connected": True, "speed": 1000, "duplex": "full"},
             "lldp": {}, "counters": {}, "tools": {"ethtool": False, "lldpcli": True}}
    out = capture(cli.print_facts, facts)
    assert "unknown (ethtool not installed)" in out
    assert "none" not in out


def test_missing_lldpcli_reads_as_unknown():
    facts = {"medium": "wired", "iface": "eth0",
             "link": {"connected": True, "speed": 1000, "duplex": "full"},
             "lldp": {}, "counters": {}, "tools": {"ethtool": True, "lldpcli": False}}
    out = capture(cli.print_facts, facts)
    assert "unknown (lldpcli not installed)" in out


def test_history_does_not_echo_control_characters(tmp_path, monkeypatch):
    import json as json_module

    from netriage import logs

    folder = tmp_path / "netriage" / "netriage_logs"
    folder.mkdir(parents=True)
    (folder / "netriage-20261116-0001.json").write_text(json_module.dumps({
        "timestamp": "2026-11-16T09:00:00",
        "link": {"ssid": "\x1b]0;owned\x07School", "signal": -60}, "findings": [],
    }), encoding="utf-8")
    monkeypatch.setattr(logs, "log_dir", lambda: str(folder))

    out = capture(cli.cmd_history, cli.build_parser().parse_args(["history"]))
    assert "\x1b]" not in out
    assert "\x07" not in out


def test_markdown_escapes_a_hostile_ssid():
    facts = {"medium": "wifi", "iface": "wlan0", "timestamp": "2026-11-16T09:00:00",
             "link": {"ssid": "a|b`c", "bssid": "aa"}, "address": {}, "findings": []}
    text = report.markdown(facts, "/tmp/x.json")
    assert "a\\|b" in text
    assert "| SSID | a\\|b'c |" in text


def test_report_uses_unknown_not_zero():
    facts = {"medium": "wired", "iface": "eth0", "timestamp": "2026-11-16T09:00:00",
             "link": {"connected": True, "speed": 1000, "duplex": "full"},
             "lldp": {}, "counters": {}, "tools": {"ethtool": False},
             "address": {"ip": "10.0.0.5/24", "gateway": "10.0.0.1"},
             "findings": []}
    text = report.markdown(facts, "/tmp/x.json")
    assert "unknown (ethtool not installed)" in text
    assert "| Frame errors | 0 |" not in text


def test_report_lists_the_measurements():
    facts = {"medium": "wifi", "iface": "wlan0", "timestamp": "2026-11-16T09:00:00",
             "link": {"connected": True, "ssid": "S", "signal": -55, "channel": 36,
                      "band": "5 GHz", "tx_rate": 585.0},
             "station": {"retry_pct": 4.0}, "survey": {}, "busy_pct": 18.0,
             "address": {"ip": "10.0.0.5/24", "gateway": "10.0.0.1"},
             "ping_gateway": {"avg": 2.0, "loss": 0, "jitter": 0.2},
             "ping_internet": {"avg": 4.0, "loss": 0, "jitter": 0.3},
             "dns_school": {"ok": True, "ms": 3, "server": "10.0.0.1", "status": "ok"},
             "dns_public": {"ok": True, "ms": 3, "server": "1.1.1.1", "status": "ok"},
             "speed": {"mbps": 100.0, "peak": 120.0}, "upload": {"mbps": 20.0},
             "latency": {"avg": 50.0, "jitter": 2.0}, "aps": [], "findings": []}
    text = report.markdown(facts, "/tmp/x.json")
    assert "## Measurements" in text
    for label in ("Signal", "TX retries", "Download", "Upload", "HTTP RTT"):
        assert label in text


def test_report_says_not_tested_when_skipped():
    facts = {"medium": "wifi", "iface": "wlan0", "timestamp": "2026-11-16T09:00:00",
             "link": {"connected": True, "ssid": "S"}, "address": {}, "findings": [],
             "speed": {"skipped": True}, "upload": {"skipped": True}}
    assert "not tested" in report.markdown(facts, "/tmp/x.json")


def test_redact_masks_identifiers():
    from netriage import util

    sample = {"user": "ubuntu", "label": "room 2.14",
              "link": {"ssid": "BCS-IOT", "bssid": "7e:83:c2:4f:84:b6", "signal": -49},
              "address": {"ip": "10.26.142.7/16", "gateway": "10.26.9.1"}}
    masked = util.redact(sample)
    assert masked["link"]["bssid"] == "7e:83:c2:xx:xx:xx"
    assert masked["link"]["ssid"] == "redacted"
    assert masked["address"]["ip"] == "10.26.x.x/16"
    assert masked["link"]["signal"] == -49          # numbers survive


def test_missing_ping_is_unknown_not_filtered():
    facts = {"captive": False, "address": {"ip": "10.0.0.5/24", "gateway": "10.0.0.1"},
             "dns_servers": ["10.0.0.1"], "tools": {"ping": False},
             "ping_gateway": {"avg": 2.0, "loss": 0, "jitter": 0.2},
             "ping_internet": None,
             "dns_school": {"ok": True, "ms": 3, "server": "10.0.0.1", "status": "ok"},
             "dns_public": {"ok": True, "ms": 3, "server": "1.1.1.1", "status": "ok"},
             "speed": {"skipped": True}, "upload": {"skipped": True}}
    out = capture(cli.print_network, facts)
    assert "unknown (ping not installed)" in out
    assert "commonly filtered" not in out


def test_scan_does_not_show_a_two_point_four_table_for_six_ghz_only():
    aps = [{"bssid": "aa:bb:cc:dd:ee:01", "ssid": "BCS-6E", "channel": 1,
            "band": "6 GHz", "signal": -55}]
    out = capture(cli.print_scan, aps, "aa:bb:cc:dd:ee:01")
    assert "2.4 GHz channel use" not in out


def test_redact_scrubs_the_final_document():
    """user and label are added after collect, so redaction must be last."""
    from netriage import util

    facts = {"user": "tech", "command": "quick", "version": "1",
             "link": {"ssid": "BCS-WiFi", "bssid": "74:90:bc:a7:15:9f"}}
    masked = util.redact(facts)
    assert masked["user"] == "redacted"
    assert masked["link"]["ssid"] == "redacted"
    assert masked["command"] == "quick"


def test_redact_text_masks_wpa_cli_fields():
    from netriage import util

    text = "ssid=BCS-WiFi\nidentity=student@school.de\nwpa_state=COMPLETED\n"
    masked = util.redact_text(text)
    assert "BCS-WiFi" not in masked
    assert "student@school.de" not in masked
    assert "COMPLETED" in masked


def test_scan_redact_masks_every_access_point():
    from netriage import util

    aps = [{"bssid": "74:90:bc:a7:15:9f", "ssid": "BCS-WiFi", "channel": 36,
            "signal": -55}]
    masked = util.redact(aps)
    assert masked[0]["ssid"] == "redacted"
    assert masked[0]["bssid"] == "74:90:bc:xx:xx:xx"
    assert masked[0]["signal"] == -55

def test_compare_takes_run_numbers(tmp_path, monkeypatch):
    from netriage import logs

    folder = tmp_path / "logs"
    folder.mkdir()
    for index in (1, 2):
        (folder / f"netriage-20261116-000{index}.json").write_text(json.dumps({
            "timestamp": f"2026-11-16T09:0{index}:00",
            "medium": "wifi", "iface": "wlan0",
            "link": {"ssid": "S", "signal": -60 + index, "channel": 36,
                     "band": "5 GHz", "bssid": "aa"},
            "findings": [],
        }), encoding="utf-8")
    monkeypatch.setattr(logs, "log_dir", lambda: str(folder))

    out = capture(cli.cmd_compare,
                  cli.build_parser().parse_args(["compare", "1", "2"]))
    assert "COMPARE" in out or "before" in out


def test_compare_reports_an_unknown_number(tmp_path, monkeypatch, capsys):
    from netriage import logs

    monkeypatch.setattr(logs, "log_dir", lambda: str(tmp_path))
    code = cli.cmd_compare(cli.build_parser().parse_args(["compare", "999", "1"]))
    assert code == 2
    assert "No run numbered 999" in capsys.readouterr().err


def test_json_is_refused_where_it_is_not_produced():
    code = cli.main(["history", "--json"])
    assert code == 2


def test_offline_never_reaches_the_internet(monkeypatch):
    """--offline must not ping 1.1.1.1, resolve through it, or probe a portal."""
    from netriage import media, network, wifi

    monkeypatch.setattr(media, "kind_of", lambda iface: "wifi")
    monkeypatch.setattr(wifi, "link", lambda iface: {
        "connected": True, "ssid": "S", "bssid": "aa", "signal": -55,
        "channel": 36, "band": "5 GHz"})
    monkeypatch.setattr(wifi, "station", lambda iface: {})
    monkeypatch.setattr(wifi, "survey", lambda iface, freq=None: {})
    monkeypatch.setattr(network, "address",
                        lambda iface: {"ip": "10.0.0.5/24", "gateway": "10.0.0.1"})
    monkeypatch.setattr(network, "dns_servers", lambda iface: ["10.0.0.1"])

    def boom(*a, **k):
        raise AssertionError("touched the internet while offline")

    monkeypatch.setattr(network, "captive", boom)
    monkeypatch.setattr(network, "download", boom)
    monkeypatch.setattr(network, "upload", boom)
    monkeypatch.setattr(network, "latency_ms", boom)
    monkeypatch.setattr(network, "ping",
                        lambda iface, target, count=4: (
                            {"avg": 2.0, "loss": 0, "jitter": 0.1}
                            if target != "1.1.1.1" else boom()))
    monkeypatch.setattr(network, "resolve",
                        lambda server, name, timeout=4: (
                            boom() if server == "1.1.1.1"
                            else {"ok": True, "ms": 3, "server": server,
                                  "status": "ok"}))

    facts = cli.collect("wlan0", "wifi", do_speed=False, offline=True)
    assert facts["ping_internet"] is None
    assert facts["dns_public"] is None
    assert facts["captive"] is None
    assert facts["offline"] is True


def test_offline_report_says_not_tested():
    facts = {"medium": "wifi", "iface": "wlan0", "offline": True,
             "address": {"ip": "10.0.0.5/24", "gateway": "10.0.0.1"},
             "dns_servers": ["10.0.0.1"],
             "ping_gateway": {"avg": 2.0, "loss": 0, "jitter": 0.2},
             "ping_internet": None, "dns_school": None, "dns_public": None,
             "captive": None, "speed": {"skipped": True}, "upload": {"skipped": True},
             "latency": None, "tools": {}}
    out = capture(cli.print_network, facts)
    assert out.count("not tested (offline)") >= 3
    assert "no ICMP reply" not in out


def test_report_accepts_a_run_id(tmp_path, monkeypatch):
    from netriage import logs

    (tmp_path / "netriage-20261116-0001.json").write_text(json.dumps({
        "timestamp": "2026-11-16T09:00:00", "medium": "wifi", "iface": "wlan0",
        "link": {"ssid": "S", "signal": -60}, "findings": [],
    }), encoding="utf-8")
    monkeypatch.setattr(logs, "log_dir", lambda: str(tmp_path))
    out = capture(cli.cmd_report, cli.build_parser().parse_args(["report", "1"]))
    assert "diagnostic report" in out.lower()


def test_report_rejects_an_unknown_id(tmp_path, monkeypatch, capsys):
    from netriage import logs

    monkeypatch.setattr(logs, "log_dir", lambda: str(tmp_path))
    code = cli.cmd_report(cli.build_parser().parse_args(["report", "999"]))
    assert code == 2
    assert "No run numbered 999" in capsys.readouterr().err




def test_the_dhcp_resolver_is_not_called_school():
    """The tool is not for one school; that label was a leftover."""
    facts = {"captive": False, "address": {"ip": "10.0.0.5/24", "gateway": "10.0.0.1"},
             "dns_servers": ["10.0.0.1"],
             "dns_local": {"ok": True, "ms": 3, "server": "10.0.0.1", "status": "ok"},
             "dns_public": {"ok": True, "ms": 4, "server": "1.1.1.1", "status": "ok"},
             "ping_gateway": {"avg": 2.0, "loss": 0, "jitter": 0.2},
             "ping_internet": {"avg": 4.0, "loss": 0, "jitter": 0.3},
             "speed": {"skipped": True}, "upload": {"skipped": True}}
    out = capture(cli.print_network, facts)
    assert "DNS local" in out
    assert "school" not in out.lower()


def test_old_reports_with_dns_school_still_render():
    facts = {"captive": False, "address": {"ip": "10.0.0.5/24", "gateway": "10.0.0.1"},
             "dns_servers": ["10.0.0.1"],
             "dns_school": {"ok": True, "ms": 3, "server": "10.0.0.1", "status": "ok"},
             "dns_public": {"ok": True, "ms": 4, "server": "1.1.1.1", "status": "ok"},
             "ping_gateway": {"avg": 2.0, "loss": 0, "jitter": 0.2},
             "ping_internet": {"avg": 4.0, "loss": 0, "jitter": 0.3},
             "speed": {"skipped": True}, "upload": {"skipped": True}}
    assert "DNS local" in capture(cli.print_network, facts)


def test_logs_prefer_documents_when_it_exists(tmp_path, monkeypatch):
    """A technician should be able to find the file and attach it."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("NETRIAGE_DATA_DIR", raising=False)
    monkeypatch.delenv("SUDO_USER", raising=False)
    (tmp_path / "Documents").mkdir()

    path = util.log_dir()
    assert path == str(tmp_path / "Documents" / "netriage-log")
    assert oct(os.stat(path).st_mode & 0o777) == "0o700"


def test_servers_fall_back_to_the_data_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("NETRIAGE_DATA_DIR", raising=False)
    monkeypatch.delenv("SUDO_USER", raising=False)
    # no Documents folder: a server, not a desktop
    assert util.log_dir() == str(tmp_path / ".local/share/netriage/logs")


def test_the_data_directory_can_be_overridden(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("NETRIAGE_DATA_DIR", str(tmp_path / "elsewhere"))
    assert util.log_dir() == str(tmp_path / "elsewhere")


def test_old_reports_are_migrated_once(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("NETRIAGE_DATA_DIR", raising=False)
    monkeypatch.delenv("SUDO_USER", raising=False)
    old = tmp_path / ".local/share/netriage/logs"
    old.mkdir(parents=True)
    (old / "netriage-20261116-0001.json").write_text("{}", encoding="utf-8")
    (tmp_path / "Documents").mkdir()

    util.log_dir()
    assert (tmp_path / "Documents/netriage-log/netriage-20261116-0001.json").exists()
    assert not old.exists()


def test_data_dir_is_remembered_and_used_afterwards(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("NETRIAGE_DATA_DIR", raising=False)
    monkeypatch.delenv("SUDO_USER", raising=False)
    target = tmp_path / "elsewhere"

    assert cli.main(["--data-dir", str(target), "history"]) == 2   # empty, but valid

    # a later command arrives without the flag
    (target / "netriage-20261118-0001.json").write_text(json.dumps({
        "timestamp": "2026-11-18T09:00:00", "medium": "wifi",
        "link": {"ssid": "S", "signal": -52}, "findings": []}), encoding="utf-8")
    out = capture(cli.cmd_history, cli.build_parser().parse_args(["history"]))
    assert "1 run" in out


def test_data_dir_rejects_a_relative_path(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    code = cli.main(["--data-dir", "relative/dir", "history"])
    assert code == 2
    assert "absolute" in capsys.readouterr().err


def test_data_dir_default_forgets_the_override(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("NETRIAGE_DATA_DIR", raising=False)
    monkeypatch.delenv("SUDO_USER", raising=False)
    target = tmp_path / "elsewhere"

    cli.main(["--data-dir", str(target), "history"])
    assert util.configured_data_dir() == str(target)

    cli.main(["--data-dir", "default", "history"])
    assert util.configured_data_dir() is None


def test_sudo_can_set_the_data_dir_and_the_user_can_read_it(tmp_path, monkeypatch):
    """A root-owned config would make the next non-sudo run fall back silently."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("NETRIAGE_DATA_DIR", raising=False)
    monkeypatch.setenv("SUDO_USER", "chenbo")
    monkeypatch.setattr(util, "user_home", lambda: str(tmp_path))
    monkeypatch.setattr(util.os, "geteuid", lambda: 0)

    claimed = []
    monkeypatch.setattr(util, "claim_to_user", lambda path: claimed.append(path))
    monkeypatch.setattr(util.os, "fchown", lambda fd, uid, gid: claimed.append("fd"))

    target = tmp_path / "elsewhere"
    assert cli._set_data_dir(str(target)) is None
    assert util.configured_data_dir() == str(target)
    assert claimed, "the config must be handed to the invoking user"


def test_a_bare_data_dir_only_configures(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("NETRIAGE_DATA_DIR", raising=False)
    monkeypatch.delenv("SUDO_USER", raising=False)
    measured = []
    monkeypatch.setattr(cli, "collect", lambda *a, **k: measured.append(1) or {})

    assert cli.main(["--data-dir", str(tmp_path / "store")]) == 0
    assert measured == [], "setting a directory must not start a measurement"
    assert util.configured_data_dir() == str(tmp_path / "store")


def test_a_data_dir_created_by_sudo_belongs_to_the_user(tmp_path, monkeypatch):
    """Otherwise the human cannot write into a directory sudo made for them."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("NETRIAGE_DATA_DIR", raising=False)
    monkeypatch.setenv("SUDO_USER", "chenbo")
    monkeypatch.setattr(util, "user_home", lambda: str(tmp_path))
    monkeypatch.setattr(util.os, "geteuid", lambda: 0)

    claimed = []
    monkeypatch.setattr(util, "claim_to_user", lambda path: claimed.append(path))
    monkeypatch.setattr(util.os, "fchown", lambda *a: None)

    target = tmp_path / "Desktop" / "netriage-log"
    assert cli._set_data_dir(str(target)) is None
    assert str(target) in claimed


def test_an_existing_data_dir_is_left_alone(tmp_path, monkeypatch):
    """A directory that already existed belongs to whoever made it."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("NETRIAGE_DATA_DIR", raising=False)
    monkeypatch.setenv("SUDO_USER", "chenbo")
    monkeypatch.setattr(util, "user_home", lambda: str(tmp_path))
    monkeypatch.setattr(util.os, "geteuid", lambda: 0)

    claimed = []
    monkeypatch.setattr(util, "claim_to_user", lambda path: claimed.append(path))
    monkeypatch.setattr(util.os, "fchown", lambda *a: None)

    existing = tmp_path / "srv"
    existing.mkdir()
    assert cli._set_data_dir(str(existing)) is None
    assert str(existing) not in claimed


def test_history_describes_a_wired_run_by_its_speed(tmp_path, monkeypatch):
    """`n/a  -` for a cable run says nothing at all."""
    from netriage import logs

    (tmp_path / "netriage-20261118-0001.json").write_text(json.dumps({
        "timestamp": "2026-11-18T22:52:41", "medium": "wired",
        "link": {"speed": 1000, "duplex": "full"}, "findings": [],
    }), encoding="utf-8")
    monkeypatch.setattr(logs, "log_dir", lambda: str(tmp_path))

    out = capture(cli.cmd_history, cli.build_parser().parse_args(["history"]))
    assert "wired 1000 Mb/s" in out
    assert "n/a" not in out


def test_a_link_local_address_is_not_measured_through(monkeypatch):
    """169.254.x.x means no lease: the probes would use another interface."""
    from netriage import media, network, wired

    monkeypatch.setattr(media, "kind_of", lambda iface: "wired")
    monkeypatch.setattr(wired, "link", lambda iface: {
        "connected": True, "speed": 1000, "duplex": "full"})
    monkeypatch.setattr(wired, "lldp", lambda iface: {})
    monkeypatch.setattr(wired, "counters", lambda iface: {})
    monkeypatch.setattr(wired, "packets", lambda iface: 1000)
    monkeypatch.setattr(network, "address",
                        lambda iface: {"ip": "169.254.194.176/16"})
    monkeypatch.setattr(network, "dns_servers", lambda iface: [])

    def boom(*a, **k):
        raise AssertionError("measured over a link-local interface")

    for name in ("ping", "download", "upload", "resolve", "latency_ms", "captive"):
        monkeypatch.setattr(network, name, boom)

    facts = cli.collect("enp4s0", "wired")
    assert facts["why_not_measured"] == "no DHCP lease"
    assert facts["speed"] == {"skipped": True}
    # the address itself is the most useful fact in the run
    assert facts["address"]["ip"] == "169.254.194.176/16"


def test_link_local_is_explained_and_not_called_a_wide_subnet():
    facts = {"medium": "wired", "iface": "enp4s0",
             "address": {"ip": "169.254.194.176/16"},
             "link": {"connected": True, "speed": 1000}, "findings": []}
    findings = [f.__dict__ for f in diagnose.analyze(facts)]
    titles = [f["title"] for f in findings]
    assert any("link-local" in t for t in titles)
    assert not any("broadcast domain" in t for t in titles)


def test_read_reprints_a_saved_run(tmp_path, monkeypatch):
    from netriage import logs

    (tmp_path / "netriage-20261118-0001.json").write_text(json.dumps({
        "timestamp": "2026-11-18T22:52:41", "medium": "wired", "iface": "eth0",
        "version": "0.42.0", "link": {"connected": True, "speed": 1000,
                                      "duplex": "full"},
        "address": {"ip": "10.0.0.5/24", "gateway": "10.0.0.1"},
        "ping_gateway": {"avg": 0.2, "loss": 0, "jitter": 0.0},
        "speed": {"skipped": True}, "upload": {"skipped": True},
        "findings": [],
    }), encoding="utf-8")
    monkeypatch.setattr(logs, "log_dir", lambda: str(tmp_path))

    out = capture(cli.cmd_read, cli.build_parser().parse_args(["read", "1"]))
    assert "replay of run 1" in out
    assert "no measurement" in out
    assert "LINK   eth0" in out
    assert "1000 Mb/s" in out


def test_read_without_an_id_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(["read"])
    assert exc.value.code == 2


def test_read_takes_exactly_one_argument():
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(["read", "1", "2"])
    assert exc.value.code == 2


def test_read_reports_an_unknown_id(tmp_path, monkeypatch, capsys):
    from netriage import logs

    monkeypatch.setattr(logs, "log_dir", lambda: str(tmp_path))
    code = cli.cmd_read(cli.build_parser().parse_args(["read", "999"]))
    assert code == 2
    assert "No run numbered 999" in capsys.readouterr().err


def test_read_honours_redact(tmp_path, monkeypatch):
    from netriage import logs

    (tmp_path / "netriage-20261118-0001.json").write_text(json.dumps({
        "timestamp": "2026-11-18T22:52:41", "medium": "wifi", "iface": "wlan0",
        "link": {"connected": True, "ssid": "BCS Campus", "bssid": "74:90:bc:a7:15:9f",
                 "signal": -52, "channel": 36, "band": "5 GHz"},
        "address": {}, "findings": [], "aps": [],
    }), encoding="utf-8")
    monkeypatch.setattr(logs, "log_dir", lambda: str(tmp_path))

    out = capture(cli.cmd_read,
                  cli.build_parser().parse_args(["read", "1", "--redact"]))
    assert "BCS Campus" not in out
    assert "74:90:bc:xx:xx:xx" in out


def test_help_lists_every_command_it_accepts(capsys):
    """The epilog was hard-coded and fell behind when `read` was added."""
    parser = cli.build_parser()
    parser.print_help()
    text = capsys.readouterr().out

    declared = {name for name in parser._subparsers._group_actions[0].choices}
    for name in declared:
        assert name in text, f"{name} is accepted but not mentioned in --help"


def test_an_unwritable_config_dir_is_a_message_not_a_traceback(tmp_path, monkeypatch):
    """A read-only home must not break --data-dir."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("NETRIAGE_DATA_DIR", raising=False)
    monkeypatch.delenv("SUDO_USER", raising=False)
    monkeypatch.setattr(util.os, "makedirs",
                        lambda *a, **k: (_ for _ in ()).throw(PermissionError("denied")))

    target = tmp_path / "store"
    code = cli.main(["--data-dir", str(target), "history"])
    assert code in (0, 2)          # it must not raise


def test_remember_returns_none_when_it_cannot_write(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(util.os, "makedirs",
                        lambda *a, **k: (_ for _ in ()).throw(PermissionError("denied")))
    assert util.remember_data_dir(str(tmp_path / "x")) is None


def test_a_merged_scan_does_not_say_it_was_nmcli_only():
    """The hint used to fire whenever any AP came from nmcli, which is always."""
    from netriage import render

    aps = [{"bssid": "aa:bb", "ssid": "Mine", "channel": 36, "band": "5 GHz",
            "signal": -60, "util_pct": 8.0},
           {"bssid": "cc:dd", "ssid": "", "channel": 36, "band": "5 GHz",
            "signal": -70, "source": "nmcli"}]
    out = capture(render.print_scan, aps, "aa:bb", "wlo1")
    assert "NetworkManager" not in out


def test_an_nmcli_only_scan_says_so_without_asking_root_for_sudo(monkeypatch):
    from netriage import render

    monkeypatch.setattr(render.os, "geteuid", lambda: 0)
    aps = [{"bssid": "cc:dd", "ssid": "Mine", "channel": 36, "band": "5 GHz",
            "signal": -70, "source": "nmcli"}]
    out = capture(render.print_scan, aps, "cc:dd", "wlo1")
    assert "NetworkManager" in out
    assert "run with sudo" not in out


def test_a_saved_scan_is_never_printed_twice():
    """Records saved before the merge fix hold both cases of one BSSID."""
    from netriage import render

    aps = [{"bssid": "74:90:bc:a7:15:9f", "ssid": "5G", "channel": 104,
            "band": "5 GHz", "signal": -81, "util_pct": 15.0},
           {"bssid": "74:90:BC:A7:15:9F", "ssid": "5G", "channel": 104,
            "band": "5 GHz", "signal": -81}]
    out = capture(render.print_scan, aps, "74:90:bc:a7:15:9f", "wlo1")
    assert "2 access points" not in out
    assert "1 access point" in out
    assert "util   15%" in out, "the row with the AP's own figures wins"


def test_a_report_never_names_a_command_that_does_not_exist():
    from netriage import report

    facts = {"command": "quick", "medium": "wired", "timestamp": "x", "findings": []}
    assert "- Command: netriage" in report.markdown(facts, "/tmp/x.json")


def test_each_phase_is_timed(monkeypatch):
    """A slow run has to be explainable, not guessed at."""
    monkeypatch.setattr(cli.media, "kind_of", lambda iface: "wired")
    monkeypatch.setattr(cli.wired, "link", lambda iface: {"connected": True,
                                                          "speed_mbps": 1000,
                                                          "duplex": "full"})
    monkeypatch.setattr(cli.wired, "lldp", lambda iface: {})
    monkeypatch.setattr(cli.wired, "counters", lambda iface: {})
    monkeypatch.setattr(cli.wired, "packets", lambda iface: None)
    monkeypatch.setattr(cli.network, "ping", lambda iface, target, count=4: {
        "avg": 0.2, "loss": 0, "jitter": 0.0, "min": 0.1, "max": 0.3})
    monkeypatch.setattr(cli.network, "resolve", lambda server, name, timeout=4: {
        "ok": True, "ms": 3, "server": server, "status": "ok"})
    monkeypatch.setattr(cli.network, "address", lambda iface: {
        "ip": "10.0.0.2/24", "gateway": "10.0.0.1"})

    facts = cli.collect("eth0", do_speed=False, do_scan=False, offline=True)
    timings = facts.get("timings")
    assert isinstance(timings, dict)
    assert timings, "at least the probing phase must be timed"
    assert all(isinstance(v, float) for v in timings.values())


def test_a_tunnel_is_never_picked_automatically(monkeypatch):
    """Traffic over tailscale0 leaves by another interface; measuring it lies."""
    from netriage import media

    monkeypatch.setattr(media, "_by_type",
                        lambda kind: ["tailscale0", "enp4s0"] if kind == "ethernet" else [])
    monkeypatch.setattr(media, "is_physical", lambda name: name == "enp4s0")
    monkeypatch.setattr(media, "is_wireless", lambda name: False)
    monkeypatch.setattr(media, "carrier", lambda name: True)

    iface, medium = media.pick()
    assert iface == "enp4s0"
    assert medium == "wired"
    assert media.kind_of("tailscale0") == "virtual"


def test_a_tunnel_is_reported_not_measured():
    facts = cli.collect("tailscale0", medium="virtual", do_speed=False,
                        do_scan=False, offline=True)
    assert facts["why_not_measured"] == "virtual interface"
    assert facts["speed"] == {"skipped": True}
    assert facts["latency"] is None
    titles = [f["title"] for f in facts["findings"]]
    assert any("tunnel" in title for title in titles), titles


def test_a_tunnel_run_says_so_and_does_not_claim_cable(monkeypatch):
    from netriage import render

    facts = cli.collect("tailscale0", medium="virtual", do_speed=False,
                        do_scan=False, offline=True)
    out = capture(render.print_facts, facts) + capture(render.print_summary, facts)
    assert "tunnel" in out
    assert "cable" not in out
