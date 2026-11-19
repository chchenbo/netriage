"""The command x flag matrix, run for real in the fake box.

Every command is exercised with every flag that applies to it, plus the error
paths. The assertions are deliberately shallow - no traceback, the right exit
code, and no identity left behind by --redact - because those are the failures
that reach a user.
"""

from __future__ import annotations

import itertools
import json

import pytest

from .conftest import run_cli

# What the fake box reports, and therefore what must never survive --redact.
SECRETS = ("School", "aa:bb:cc:dd:ee:ff", "192.168.1.20")


@pytest.fixture
def seeded(fake_box):
    """Two saved runs, so report/compare/history have something to read."""
    run_cli(fake_box, "--offline", "--wifi")
    run_cli(fake_box, "--offline", "--wifi")
    return fake_box


def assert_clean(code, out, err, allowed=(0, 1)):
    assert "Traceback" not in err, err
    assert code in allowed, f"exit {code}\n{out}\n{err}"


# --------------------------------------------------------------- every command

COMMANDS = [
    ["--offline", "--wifi"],
    ["scan"],
    ["history"],
    ["report"],
    ["compare"],
]


@pytest.mark.parametrize("command", COMMANDS, ids=lambda c: c[0])
def test_commands_never_traceback(seeded, command):
    code, out, err = run_cli(seeded, *command)
    assert_clean(code, out, err)
    assert out.strip()


@pytest.mark.parametrize("flag", ["--redact", "--offline"])
@pytest.mark.parametrize("command", COMMANDS, ids=lambda c: c[0])
def test_flags_never_traceback(seeded, command, flag):
    code, out, err = run_cli(seeded, flag, *command)
    assert_clean(code, out, err)


@pytest.mark.parametrize("command", COMMANDS, ids=lambda c: c[0])
def test_help_works_for_every_command(seeded, command):
    code, out, err = run_cli(seeded, command[0], "--help")
    assert code == 0, err
    assert "usage:" in out


# --------------------------------------------------------------- redaction

REDACTED = [
    ["--offline", "--wifi"],
    ["scan"],
    ["history"],
    ["report"],
    ["compare"],
]


@pytest.mark.parametrize("command", REDACTED, ids=lambda c: c[0])
def test_redact_leaves_no_identity_anywhere(seeded, command):
    code, out, err = run_cli(seeded, "--redact", *command)
    assert_clean(code, out, err)
    for secret in SECRETS:
        assert secret not in out, f"{secret!r} survived --redact in {command[0]}"


def test_redact_covers_the_saved_file_and_the_csv(seeded):
    logs = seeded["home"] / ".local" / "share" / "netriage" / "logs"
    for stale in list(logs.glob("*.json")) + list(logs.glob("*.csv")):
        stale.unlink()                      # only judge what the redacted run wrote

    run_cli(seeded, "--redact", "--offline", "--wifi")
    run_cli(seeded, "--redact", "watch", "--minutes", "0", "--interval", "1")

    for path in itertools.chain(logs.glob("*.json"), logs.glob("*.csv")):
        text = path.read_text(encoding="utf-8")
        for secret in SECRETS:
            assert secret not in text, f"{secret!r} in {path.name}"


# --------------------------------------------------------------- refusals

REFUSALS = [
    (["scan", "--wired"], "--wired"),
    (["watch", "--wired"], "--wired"),
    (["history", "--json"], "--json"),
    (["report", "--json"], "--json"),
    (["compare", "--json"], "--json"),
    (["report", "999"], "No run numbered"),
    (["report", "missing.json"], "Cannot read"),
    (["compare", "1"], "Give two runs"),
    (["compare", "404"], "No run numbered"),
    (["nonsense"], "invalid choice"),
]


@pytest.mark.parametrize("argv,needle", REFUSALS, ids=[r[0][0] + str(r[1])[:8] for r in REFUSALS])
def test_refusals_are_clean(seeded, argv, needle):
    code, out, err = run_cli(seeded, *argv)
    assert "Traceback" not in err, err
    assert code == 2, f"expected a refusal, got {code}"
    assert needle in (out + err), f"{needle!r} not in output"


def test_watch_refuses_an_output_directory(seeded):
    target = seeded["tmp"] / "outdir"
    target.mkdir()
    code, out, err = run_cli(seeded, "watch", "--minutes", "0", "--out", str(target))
    assert "Traceback" not in err, err
    assert code == 2
    assert "is a directory" in out


# --------------------------------------------------------------- json contract

def test_json_is_produced_where_promised(seeded):
    for command in (["--json", "--offline", "--wifi"], ["--json", "scan"]):
        code, out, err = run_cli(seeded, *command)
        assert_clean(code, out, err)
        json.loads(out)          # a real document, not text


def test_json_is_refused_where_not_promised(seeded):
    for name in ("history", "report", "compare", "watch"):
        code, out, err = run_cli(seeded, name, "--json")
        assert code == 2, name
        assert "--json" in err
