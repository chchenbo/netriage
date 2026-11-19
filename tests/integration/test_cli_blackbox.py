"""Black-box tests: the real CLI, fake tools, no mocking of the code under test."""

from __future__ import annotations

import json

from .conftest import run_cli


def test_a_normal_run_prints_a_report(fake_box):
    code, out, err = run_cli(fake_box, "--wifi")
    assert code in (0, 1), err
    assert "LINK" in out and "NETWORK" in out
    assert "Traceback" not in err


def test_scan_without_iw_falls_back_to_nmcli(fake_box):
    code, out, err = run_cli(fake_box, "scan", remove=("iw",))
    assert "Traceback" not in err, err
    assert "NEARBY" in out


def test_history_works_on_an_empty_home(fake_box):
    code, out, err = run_cli(fake_box, "history")
    assert code == 2
    assert "No saved runs yet" in out


def test_json_output_is_the_saved_document(fake_box):
    code, out, err = run_cli(fake_box, "--json", "--offline", "--wifi")
    printed = json.loads(out)
    logs = sorted((fake_box["home"] / ".local" / "share" / "netriage" / "logs").glob("*.json"))
    saved = json.loads(logs[0].read_text())
    assert set(printed) == set(saved), "printed JSON must match the saved file"


def test_unassociated_radio_measures_nothing(fake_box):
    """wlan0 with no association must not report some other path's speed."""
    iw = fake_box["bin"] / "iw"
    iw.write_text('#!/bin/sh\necho "Not connected."\n', encoding="utf-8")
    iw.chmod(0o755)

    code, out, err = run_cli(fake_box, "--offline", "--wifi")
    assert "Traceback" not in err, err
    assert "nothing above the link could be tested" in out
    assert "Download" not in out


def test_redact_masks_the_report(fake_box):
    code, out, err = run_cli(fake_box, "--redact", "--offline", "--wifi")
    assert "aa:bb:cc:xx:xx:xx" in out
    assert "192.168.1.20" not in out


def test_version_needs_no_hardware(fake_box):
    code, out, err = run_cli(fake_box, "--version")
    assert code == 0
    assert "netriage" in out and "package" in out


def test_redact_also_covers_saved_json_and_debug(fake_box):
    """The saved file and the debug bundle are the ones that get shared."""
    code, out, err = run_cli(fake_box, "--redact", "--debug",
                             "--offline", "--wifi")
    logs = sorted((fake_box["home"] / ".local" / "share" / "netriage" / "logs").glob("*.json"))
    saved = logs[0].read_text()
    assert "192.168.1.20" not in saved
    assert "aa:bb:cc:dd:ee:ff" not in saved.lower()

    bundles = sorted((fake_box["home"] / ".local" / "share" / "netriage" / "logs").glob("*-debug.txt"))
    assert bundles, err
    text = bundles[0].read_text()
    assert "192.168.1.20" not in text
    assert "School" not in text


def test_compare_accepts_run_ids(fake_box):
    """Runs are numbered; compare should take ids as well as file names."""
    run_cli(fake_box, "--offline", "--wifi")
    run_cli(fake_box, "--offline", "--wifi")

    code, out, err = run_cli(fake_box, "compare", "1", "2")
    assert code == 0, err
    assert "before" in out and "after" in out


def test_scan_redact_hides_the_network(fake_box):
    code, out, err = run_cli(fake_box, "scan", "--redact")
    assert "School" not in out
    assert "aa:bb:cc:xx:xx:xx" in out


def test_offline_runs_without_touching_the_internet(fake_box):
    """--offline must finish, not crash on a job that was never submitted."""
    code, out, err = run_cli(fake_box, "--offline", "--wifi", "--offline")
    assert "Traceback" not in err, err
    assert code in (0, 1)
    assert "not tested (offline)" in out
    assert "no ICMP reply" not in out


def test_report_accepts_a_run_id(fake_box):
    run_cli(fake_box, "--offline", "--wifi")
    code, out, err = run_cli(fake_box, "report", "1")
    assert code == 0, err
    assert "diagnostic report" in out.lower()
