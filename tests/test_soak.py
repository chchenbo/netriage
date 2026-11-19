"""Tests for the soak recorder."""

import csv
import os

import pytest

from netriage import soak


def test_missing_output_directory_is_created(tmp_path):
    target = tmp_path / "absent" / "deep" / "run.csv"
    soak.run("wlan0", minutes=0, interval=1, out=str(target))
    assert target.parent.is_dir()


def test_unwritable_output_directory_is_a_message(tmp_path, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    rows, _ = soak.run("wlan0", minutes=0, interval=1,
                       out=str(blocker / "run.csv"))
    assert rows == []
    assert "Cannot write" in capsys.readouterr().out


def test_csv_carries_connection_state(tmp_path, monkeypatch):
    monkeypatch.setattr(soak, "sample", lambda iface, offline=False: {
        "time": "2026-11-16 09:00:00", "_connected": False, "_ssid": "",
        "rssi": None, "bssid": "", "channel": None, "busy_pct": None,
        "retry_pct": None, "gw_ms": None, "gw_loss": None,
        "net_ms": None, "net_loss": None,
    })
    target = str(tmp_path / "run.csv")
    rows, path = soak.run("wlan0", minutes=0.01, interval=1, out=target)

    with open(path, newline="", encoding="utf-8") as handle:
        reader = list(csv.DictReader(handle))
    assert "connected" in reader[0]
    assert reader[0]["connected"] == "no"


def test_disconnected_sample_records_no_latency(monkeypatch):
    from netriage import network, wifi

    monkeypatch.setattr(wifi, "link", lambda iface: {"connected": False})
    monkeypatch.setattr(wifi, "survey", lambda iface, freq=None: {})
    monkeypatch.setattr(wifi, "station", lambda iface: {})
    monkeypatch.setattr(network, "address", lambda iface: {})

    def boom(*a, **k):
        raise AssertionError("pinged while disconnected")

    monkeypatch.setattr(network, "ping", boom)
    row = soak.sample("wlan0")
    assert row["net_ms"] is None


def test_soak_rows_fit_the_rule():
    import io
    from contextlib import redirect_stdout

    for connected in (True, False):
        row = {"time": "2026-11-16 09:00:00", "_connected": connected, "_ssid": "S",
               "rssi": -55 if connected else None, "bssid": "aa", "channel": 36,
               "busy_pct": 18.0, "retry_pct": 4.0, "gw_ms": 2.0, "gw_loss": 0,
               "net_ms": 4.0, "net_loss": 0}
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            soak._print_row(row)
        line = buffer.getvalue().rstrip("\n")
        assert len(line) <= 72, line


def test_watch_redacts_the_csv(tmp_path, monkeypatch):
    monkeypatch.setattr(soak, "sample", lambda iface, offline=False: {
        "time": "2026-11-16 09:00:00", "_connected": True, "_ssid": "BCS-WiFi",
        "rssi": -55, "bssid": "74:90:bc:a7:15:9f", "channel": 36, "busy_pct": 10.0,
        "retry_pct": 2.0, "gw_ms": 2.0, "gw_loss": 0, "net_ms": 4.0, "net_loss": 0,
    })
    target = str(tmp_path / "run.csv")
    soak.run("wlan0", minutes=0, interval=1, out=target, redact=True)
    text = open(target, encoding="utf-8").read()
    assert "BCS-WiFi" not in text
    assert "74:90:bc:a7:15:9f" not in text


def test_output_path_that_is_a_directory_is_reported(tmp_path, capsys):
    folder = tmp_path / "adir"
    folder.mkdir()
    rows, path = soak.run("wlan0", minutes=0, interval=1, out=str(folder))
    assert rows == []
    assert "is a directory" in capsys.readouterr().out


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_unwritable_directory_is_a_message_not_a_traceback(tmp_path, capsys):
    """A writable parent does not imply a writable file inside it."""
    locked = tmp_path / "private"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        rows, _ = soak.run("wlan0", minutes=0, interval=1,
                           out=str(locked / "x.csv"))
    finally:
        locked.chmod(0o700)
    assert rows == []
    assert "Cannot write" in capsys.readouterr().out
