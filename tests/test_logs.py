"""Reading saved reports back off disk."""

import json
import os

import pytest

from netriage import logs


def write(folder, name, payload):
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_entries_are_numbered_from_one(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, "log_dir", lambda: str(tmp_path))
    write(tmp_path, "netriage-20261116-0001.json", {"timestamp": "2026-11-16T09:00:00"})
    write(tmp_path, "netriage-20261116-0002.json", {"timestamp": "2026-11-16T10:00:00"})

    found = logs.entries()
    assert [number for number, _, _ in found] == [1, 2]
    assert found[0][2]["timestamp"] == "2026-11-16T09:00:00"


def test_a_broken_file_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, "log_dir", lambda: str(tmp_path))
    write(tmp_path, "netriage-20261116-0001.json", {"timestamp": "a"})
    (tmp_path / "netriage-20261116-0002.json").write_text("{not json", encoding="utf-8")
    write(tmp_path, "netriage-20261116-0003.json", {"timestamp": "c"})

    found = logs.entries()
    assert len(found) == 2
    assert [number for number, _, _ in found] == [1, 3]


def test_empty_directory_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, "log_dir", lambda: str(tmp_path))
    assert logs.entries() == []


def test_sudo_run_writes_a_file_the_user_can_read(tmp_path, monkeypatch):
    """Root-owned 0600 reports made `netriage history` skip every sudo run."""
    from netriage import logs

    monkeypatch.setattr(logs, "log_dir", lambda: str(tmp_path))
    chowned = []
    monkeypatch.setattr(logs.os, "geteuid", lambda: 0)
    monkeypatch.setattr(logs.os, "fchown", lambda fd, uid, gid: chowned.append((uid, gid)))
    monkeypatch.setenv("SUDO_USER", "chenbo")
    monkeypatch.setattr(logs, "_invoking_user", lambda: (1000, 1000))

    path = logs.save({"timestamp": "2026-11-18T09:00:00"}, "quick")
    assert chowned == [(1000, 1000)]
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"


def test_no_sudo_means_no_chown(tmp_path, monkeypatch):
    from netriage import logs

    monkeypatch.setattr(logs, "log_dir", lambda: str(tmp_path))
    monkeypatch.setattr(logs, "_invoking_user", lambda: None)
    called = []
    monkeypatch.setattr(logs.os, "fchown", lambda *a: called.append(a))

    logs.save({"timestamp": "2026-11-18T09:00:00"}, "quick")
    assert called == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read mode 000")
def test_unreadable_reports_are_counted_not_hidden(tmp_path, monkeypatch):
    """A gap in the ids must be explained, not silently skipped."""
    from netriage import logs

    monkeypatch.setattr(logs, "log_dir", lambda: str(tmp_path))
    for index in (1, 2, 3):
        path = tmp_path / f"netriage-20261118-000{index}.json"
        path.write_text(json.dumps({"timestamp": "2026-11-18T09:00:00"}), encoding="utf-8")
    (tmp_path / "netriage-20261118-0002.json").chmod(0o000)

    found, problems = logs.entries(with_problems=True)
    assert [number for number, _, _ in found] == [1, 3]
    assert len(problems) == 1
    assert problems[0][1] == "permission denied"
