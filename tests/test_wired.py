"""Parsing tests for the wired layer."""

from pathlib import Path

import pytest

from netriage import wired

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return (FIXTURES / name).read_text()


def test_link_reads_speed_and_duplex(monkeypatch):
    monkeypatch.setattr(wired, "have", lambda cmd: True)
    monkeypatch.setattr(wired, "carrier", lambda iface: True)
    monkeypatch.setattr(wired, "run", lambda *a, **k: (0, fixture("ethtool.txt"), ""))

    info = wired.link("enp3s0")
    assert info["connected"] is True
    assert info["speed"] == 1000
    assert info["duplex"] == "full"
    assert info["autoneg"] == "on"


def test_link_without_carrier(monkeypatch):
    monkeypatch.setattr(wired, "have", lambda cmd: True)
    monkeypatch.setattr(wired, "carrier", lambda iface: False)
    payload = fixture("ethtool.txt").replace("Link detected: yes", "Link detected: no")
    monkeypatch.setattr(wired, "run", lambda *a, **k: (0, payload, ""))

    info = wired.link("enp3s0")
    assert info["connected"] is False


def test_counters_only_reports_non_zero_errors(monkeypatch):
    monkeypatch.setattr(wired, "have", lambda cmd: True)
    payload = (
        "NIC statistics:\n"
        "     rx_packets: 123456\n"
        "     rx_errors: 0\n"
        "     rx_crc_errors: 7\n"
        "     tx_packets: 234567\n"
        "     tx_errors: 0\n"
        "     collisions: 0\n"
    )
    monkeypatch.setattr(wired, "run", lambda *a, **k: (0, payload, ""))
    assert wired.counters("enp3s0") == {"rx_crc_errors": 7}


def test_counters_absent_without_ethtool(monkeypatch):
    monkeypatch.setattr(wired, "have", lambda cmd: False)
    assert wired.counters("enp3s0") == {}


def test_lldp_identifies_switch_port_and_vlan(monkeypatch):
    monkeypatch.setattr(wired, "have", lambda cmd: True)
    monkeypatch.setattr(wired, "run", lambda *a, **k: (0, fixture("lldp_neighbors.txt"), ""))
    neighbour = wired.lldp("enp3s0")
    assert neighbour["switch"] == "Switch-B"
    assert neighbour["port"] == "Gi1/0/12"
    assert neighbour["vlan"] == "120"


def test_lldp_returns_nothing_when_silent(monkeypatch):
    monkeypatch.setattr(wired, "have", lambda cmd: True)
    monkeypatch.setattr(wired, "run", lambda *a, **k: (0, "", ""))
    assert wired.lldp("enp3s0") == {}


@pytest.mark.parametrize("missing", ["ethtool", "lldpcli"])
def test_missing_tools_do_not_crash(monkeypatch, missing):
    monkeypatch.setattr(wired, "have", lambda cmd: cmd != missing)
    monkeypatch.setattr(wired, "carrier", lambda iface: True)
    monkeypatch.setattr(wired, "run", lambda *a, **k: (127, "", "not found"))
    assert isinstance(wired.link("enp3s0"), dict)
    assert wired.lldp("enp3s0") == {}


def test_unnamed_switch_port_falls_back_to_the_description(monkeypatch):
    """A switch that never named its ports sends "PortID: local Port"."""
    output = (
        "Interface:    eth0\n"
        "  Chassis:\n"
        "    SysName:      Switch12-KellerUnten\n"
        "  Port:\n"
        "    PortID:       local Port\n"
        "    PortDescr:    GigabitEthernet1/0/12\n"
    )
    monkeypatch.setattr(wired, "have", lambda cmd: True)
    monkeypatch.setattr(wired, "run", lambda *a, **k: (0, output, ""))

    neighbour = wired.lldp("eth0")
    assert neighbour["switch"] == "Switch12-KellerUnten"
    assert neighbour["port"] == "GigabitEthernet1/0/12"


def test_unnamed_port_without_a_description_is_blank(monkeypatch):
    output = (
        "Interface:    eth0\n"
        "  Chassis:\n"
        "    SysName:      Switch12-KellerUnten\n"
        "  Port:\n"
        "    PortID:       local Port\n"
    )
    monkeypatch.setattr(wired, "have", lambda cmd: True)
    monkeypatch.setattr(wired, "run", lambda *a, **k: (0, output, ""))

    assert wired.lldp("eth0")["port"] == ""


def test_checksum_counters_are_not_frame_errors():
    counters = {"rx_tcp_checksum_errors": 35, "rx_crc_errors": 2}
    physical, checksum = wired.classify(counters)
    assert physical == {"rx_crc_errors": 2}
    assert checksum == {"rx_tcp_checksum_errors": 35}
