"""A fake Linux box for black-box tests.

The unit tests replace modules, so they cannot catch "measured through the
wrong interface" or "printed zero when the tool is missing". These tests run
the real CLI as a subprocess against fake binaries on PATH.
"""

from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# Each fake prints canned output and ignores most arguments: enough for the
# parser, and nothing that needs a real radio.
FAKES = {
    "ip": {
        ("-4", "-o", "addr"): "2: wlan0    inet 192.168.1.20/24 brd "
                              "192.168.1.255 scope global wlan0\n",
        ("-4", "-o", "addr", "show", "dev", "wlan0"):
            "2: wlan0    inet 192.168.1.20/24 brd 192.168.1.255 scope global wlan0\n",
        ("-4", "-o", "addr", "show", "dev", "enp3s0"):
            "3: enp3s0    inet 192.168.2.30/24 brd 192.168.2.255 scope global enp3s0\n",
        ("-4", "route"): "default via 192.168.1.1 dev wlan0 proto dhcp\n",
        ("-4", "route", "show", "dev", "wlan0", "default"):
            "default via 192.168.1.1 dev wlan0 proto dhcp\n",
        ("-4", "route", "show", "dev", "enp3s0", "default"):
            "default via 192.168.2.1 dev enp3s0 proto dhcp\n",
        ("-br", "link"): "wlan0  UP  00:11:22:33:44:55 <BROADCAST,MULTICAST,UP>\n",
        ("-br", "addr"): "wlan0  UP  192.168.1.20/24\n",
        ("route",): "default via 192.168.1.1\n",
    },
    "iw": {
        ("dev", "wlan0", "link"): (
            "Connected to aa:bb:cc:dd:ee:ff (on wlan0)\n"
            "\tSSID: School\n\tfreq: 5180.0\n\tsignal: -55 dBm\n"
            "\trx bitrate: 780.0 MBit/s VHT-MCS 7 80MHz\n"
            "\ttx bitrate: 780.0 MBit/s VHT-MCS 7 80MHz\n"
        ),
        ("dev", "wlan0", "station", "dump"): (
            "Station aa:bb:cc:dd:ee:ff (on wlan0)\n\ttx retries:\t40\n"
            "\ttx failed:\t0\n\ttx packets:\t1000\n"
        ),
        ("dev", "wlan0", "survey", "dump"): "",
        ("dev", "wlan0", "scan"): (
            "BSS aa:bb:cc:dd:ee:ff(on wlan0)\n\tfreq: 5180\n\tsignal: -55.00 dBm\n"
            "\tSSID: School\n\tRSN:\t * Version: 1\n"
        ),
        ("dev", "wlan0", "info"): "",
        ("dev",): "phy#0\n\tInterface wlan0\n",
        ("list",): "",
    },
    "ethtool": {
        ("enp3s0",): (
            "Settings for enp3s0:\n\tSpeed: 1000Mb/s\n\tDuplex: Full\n"
            "\tAuto-negotiation: on\n\tLink detected: yes\n"
        ),
        ("-S", "enp3s0"): "NIC statistics:\n     rx_packets: 1000\n     rx_errors: 0\n",
        ("-i", "enp3s0"): "driver: e1000e\n",
    },
    "nmcli": {
        ("-t", "-f", "DEVICE,TYPE", "device", "status"): "wlan0:wifi\nenp3s0:ethernet\n",
        ("-t", "-f", "IP4.DNS", "device", "show"): "192.168.1.1\n",
        ("-t", "-f", "NAME,DEVICE", "connection", "show", "--active"): "School:wlan0\n",
    },
    "ping": {
        ("192.168.1.1",): "4 packets transmitted, 4 received, 0% packet loss\n"
                          "rtt min/avg/max/mdev = 1.0/2.0/3.0/0.5 ms\n",
        ("1.1.1.1",): "4 packets transmitted, 4 received, 0% packet loss\n"
                      "rtt min/avg/max/mdev = 4.0/5.0/6.0/0.5 ms\n",
    },
    "dig": {
        ("@192.168.1.1",): ";; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1\n"
                           ";; Query time: 3 msec\n;; ANSWER: 1\n",
        ("@1.1.1.1",): ";; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1\n"
                       ";; Query time: 3 msec\n;; ANSWER: 1\n",
    },
    "curl": {(): "204|"},
    "lldpcli": {("show", "neighbors"): ""},
    "wpa_cli": {("-i", "wlan0", "status"): "wpa_state=COMPLETED\nkey_mgmt=WPA2-PSK\n"},
    "journalctl": {(): ""},
    "rfkill": {("list",): "0: phy0: Wireless LAN\n\tSoft blocked: no\n\tHard blocked: no\n"},
}

SKELETON = """#!/bin/sh
case \"$*\" in
{arms}
  *) echo \"\" ;;
esac
"""


def _write_binary(folder: Path, name: str, outputs: dict):
    """A shell script that answers each argument list with canned output."""
    arms = []
    for argv, text in outputs.items():
        pattern = " ".join(argv) if argv else "*"
        body = text.replace('"', '\\"')
        arms.append(f'  "{pattern}") printf \'%s\\n\' \"{body}\" ;;')
    path = folder / name
    path.write_text(SKELETON.format(arms="\n".join(arms)), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def fake_box(tmp_path):
    """A PATH full of fake tools, a private HOME, and no network."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    home = tmp_path / "home"
    home.mkdir()

    for name, outputs in FAKES.items():
        _write_binary(bin_dir, name, outputs)

    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(home),
        "PYTHONPATH": str(REPO),
        "COLUMNS": "100",
        # the fake box invents wlan0/enp3s0; on Linux the real /sys would
        # rightly say they do not exist
        "NETRIAGE_IGNORE_SYSFS": "1",
        "LANG": "C.UTF-8",
    }
    return {"bin": bin_dir, "home": home, "env": env, "tmp": tmp_path}


def run_cli(box, *args, remove=()):
    """Run the real CLI in the fake box and return (code, stdout, stderr)."""
    for name in remove:
        target = box["bin"] / name
        if target.exists():
            target.unlink()
    env = dict(box["env"])
    env["PATH"] = f"{box['bin']}:/usr/bin:/bin"
    # The CLI refuses to run on Darwin; pretend to be Linux so the same code
    # path is exercised wherever the tests are run.
    runner = (
        "import sys; sys.platform='linux'; "
        "from netriage.cli import main; raise SystemExit(main())"
    )
    proc = subprocess.run(
        [sys.executable, "-c", runner, *args],
        cwd=str(box["tmp"]),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc.returncode, proc.stdout, proc.stderr
