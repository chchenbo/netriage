"""Tests for the debug bundle, which exists so a field failure is reproducible."""

import io
import os
from contextlib import redirect_stdout

from netriage import cli, debug


def test_bundle_names_the_environment(monkeypatch):
    monkeypatch.setattr(debug, "have", lambda tool: False)
    monkeypatch.setattr(debug, "run", lambda *a, **k: (127, "", "not found"))

    text = debug.bundle("wlp5s0", {"iface": "wlp5s0", "medium": "wifi"})
    assert "netriage debug bundle" in text
    assert "wlp5s0" in text
    assert "MISSING" in text


def test_bundle_includes_interface_commands(monkeypatch):
    monkeypatch.setattr(debug, "have", lambda tool: True)
    monkeypatch.setattr(debug, "run", lambda *a, **k: (0, "RAW OUTPUT", ""))

    text = debug.bundle("wlp5s0", None)
    for expected in ("iw link wlp5s0", "iw station dump wlp5s0",
                     "ethtool -S wlp5s0", "nmcli device show wlp5s0"):
        assert expected in text, expected
    assert "RAW OUTPUT" in text


def test_bundle_records_stderr(monkeypatch):
    monkeypatch.setattr(debug, "have", lambda tool: True)
    monkeypatch.setattr(debug, "run", lambda *a, **k: (1, "", "permission denied"))

    text = debug.bundle(None, None)
    assert "stderr: permission denied" in text


def test_missing_tool_is_not_an_error(monkeypatch):
    monkeypatch.setattr(debug, "have", lambda tool: tool != "ethtool")
    monkeypatch.setattr(debug, "run", lambda *a, **k: (0, "out", ""))

    text = debug.bundle("enp3s0", None)
    assert "ethtool not installed" in text


def test_debug_flag_writes_a_bundle(monkeypatch, tmp_path):
    def stub_collect(iface, medium, do_speed=True, do_scan=True, offline=False,
                     on_phase=None):
        return {
            "iface": iface, "medium": "wifi",
            "link": {"connected": True, "ssid": "S", "bssid": "aa", "signal": -60,
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

    report = str(tmp_path / "netriage-20261114-0001.json")
    monkeypatch.setattr(cli, "collect", stub_collect)
    monkeypatch.setattr(cli, "resolve_interface",
                        lambda a, wireless_only=False: ("wlp5s0", "wifi"))
    monkeypatch.setattr(cli, "_exists", lambda iface: True)
    monkeypatch.setattr(cli, "_report_path", lambda kind: report)
    monkeypatch.setattr(cli, "_save", lambda facts, kind, path=None: path or report)
    monkeypatch.setattr(debug, "have", lambda tool: False)
    monkeypatch.setattr(debug, "run", lambda *a, **k: (127, "", "not found"))

    with redirect_stdout(io.StringIO()):
        cli.cmd_quick(cli.build_parser().parse_args(["--debug"]))

    debug_path = report.replace(".json", "-debug.txt")
    assert os.path.exists(debug_path)
    with open(debug_path, encoding="utf-8") as handle:
        assert "netriage debug bundle" in handle.read()
