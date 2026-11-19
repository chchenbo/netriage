"""Parsing tests for the wireless layer.

Every fixture in tests/fixtures is real captured output, because the failure
mode that matters is a driver or tool changing its format, not a typo in a
regex written against an imagined format.
"""

from pathlib import Path

import pytest

from netriage import media, wifi

FIXTURES = Path(__file__).parent / "fixtures"


def scan_only(payload):
    """Answer `iw ... scan` with the fixture and nmcli with nothing.

    scan() merges both sources, so a mock that answered every command with the
    same iw text would feed that text to the nmcli parser as well.
    """

    def fake(cmd, timeout=10):
        if cmd and cmd[0] == "nmcli":
            return (1, "", "")
        return (0, payload, "")

    return fake


def fixture(name):
    return (FIXTURES / name).read_text()


@pytest.mark.parametrize(
    "freq,channel",
    [(2412, 1), (2437, 6), (2472, 13), (2484, 14), (5180, 36), (5745, 149), (5955, 1)],
)
def test_channel_of(freq, channel):
    assert wifi.channel_of(freq) == channel


def test_band_of():
    assert wifi.band_of(2437) == "2.4 GHz"
    assert wifi.band_of(5180) == "5 GHz"
    assert wifi.band_of(6115) == "6 GHz"


def test_split_nmcli_unescapes_colons():
    assert media.split_nmcli(r"AA\:BB\:CC\:DD\:EE\:FF:MyNet:36:80:WPA2") == [
        "AA:BB:CC:DD:EE:FF",
        "MyNet",
        "36",
        "80",
        "WPA2",
    ]


def test_link_parses_signal_and_width(monkeypatch):
    monkeypatch.setattr(wifi, "run", lambda *a, **k: (0, fixture("iw_link.txt"), ""))
    info = wifi.link("wlp5s0")
    assert info["connected"] is True
    assert info["ssid"] == "MagentaWLAN-P2S3_5GHz"
    assert info["bssid"] == "74:90:bc:a7:15:9f"
    assert info["channel"] == 36
    assert info["signal"] == -67
    assert info["width_mhz"] == 160


def test_link_width_comes_from_tx_not_rx(monkeypatch):
    """rx and tx can differ; the tx value is the one that describes our uplink."""
    payload = (
        "Connected to 74:90:bc:a7:15:9f (on wlp5s0)\n"
        "\tSSID: X\n\tfreq: 5180.0\n\tsignal: -60 dBm\n"
        "\trx bitrate: 1200.9 MBit/s VHT-MCS 9 160MHz\n"
        "\ttx bitrate: 702.0 MBit/s VHT-MCS 8 80MHz\n"
    )
    monkeypatch.setattr(wifi, "run", lambda *a, **k: (0, payload, ""))
    assert wifi.link("wlp5s0")["width_mhz"] == 80


def test_link_without_width_does_not_invent_one(monkeypatch):
    payload = "Connected to 74:90:bc:a7:15:9f (on wlp5s0)\n\tSSID: X\n\tsignal: -60 dBm\n"
    monkeypatch.setattr(wifi, "run", lambda *a, **k: (0, payload, ""))
    assert wifi.link("wlp5s0")["width_mhz"] is None


def test_station_retry_rate(monkeypatch):
    monkeypatch.setattr(wifi, "run", lambda *a, **k: (0, fixture("iw_station.txt"), ""))
    info = wifi.station("wlp5s0")
    assert info["retry_pct"] == 12.0
    assert info["failed_pct"] == 0.0
    assert info["beacon_loss"] == 26


def test_station_without_failure_counter_is_not_zero(monkeypatch):
    payload = "Station aa (on wlp5s0)\n\ttx retries:\t40\n\ttx packets:\t1000\n"
    monkeypatch.setattr(wifi, "run", lambda *a, **k: (0, payload, ""))
    info = wifi.station("wlp5s0")
    assert info["retry_pct"] == 4.0
    assert "failed_pct" not in info


def test_scan_parses_three_access_points(monkeypatch):
    monkeypatch.setattr(wifi, "run", scan_only(fixture("iw_scan.txt")))
    aps = wifi.scan("wlp5s0")
    assert len(aps) == 3
    assert [ap["channel"] for ap in aps] == [36, 13, 6]


def test_scan_reads_bss_load(monkeypatch):
    monkeypatch.setattr(wifi, "run", scan_only(fixture("iw_scan.txt")))
    first = wifi.scan("wlp5s0")[0]
    assert first["stations"] == 12
    assert first["util_pct"] == pytest.approx(14.1, abs=0.1)


def test_scan_decodes_escaped_ssid(monkeypatch):
    monkeypatch.setattr(wifi, "run", scan_only(fixture("iw_scan.txt")))
    second = wifi.scan("wlp5s0")[1]
    assert second["ssid"] == "15112345677的nova 14 Ultra"


def test_scan_detects_transitional_security(monkeypatch):
    monkeypatch.setattr(wifi, "run", scan_only(fixture("iw_scan.txt")))
    first = wifi.scan("wlp5s0")[0]
    assert first["sae"] is True
    assert first["psk"] is True
    assert wifi.security_label(first) == "WPA2/WPA3-PSK"


@pytest.mark.parametrize(
    "raw,label",
    [
        ("WPA2 WPA3", "WPA2/WPA3-PSK"),
        ("WPA2", "WPA2-PSK"),
        ("WPA2 802.1X", "WPA2-802.1X"),
        ("--", "open"),
        ("", "open"),
    ],
)
def test_normalize_security(raw, label):
    assert wifi.normalize_security(raw) == label


def test_channel_load_counts_and_averages(monkeypatch):
    monkeypatch.setattr(wifi, "run", scan_only(fixture("iw_scan.txt")))
    table = wifi.channel_load(wifi.scan("wlp5s0"))
    assert table[36]["count"] == 1
    assert table[13]["count"] == 1
    assert table[36]["util_avg"] == pytest.approx(14.1, abs=0.1)


def test_download_urls_stay_under_the_mirror_cap():
    """Cloudflare answers 403 above roughly 80 MB; the default used to ask 100."""
    from netriage import network

    assert network.DEFAULT_DOWNLOADS
    for url in network.DEFAULT_DOWNLOADS:
        size = int(url.rsplit("bytes=", 1)[1])
        assert size <= 50_000_000, url


def test_channel_load_infers_a_missing_band():
    aps = [{"channel": 1, "freq": 5955.0}, {"channel": 1}, {"channel": 6}]
    table = wifi.channel_load(aps, band="2.4 GHz")
    assert table[1]["count"] == 1, "the 6 GHz AP on channel 1 must not count"
    assert table[6]["count"] == 1


def test_wep_and_owe_are_not_called_open():
    assert wifi.normalize_security("WEP") == "WEP"
    assert wifi.normalize_security("OWE") == "OWE"
    assert wifi.normalize_security("--") == "open"


def test_wep_and_owe_detected_from_a_scan(monkeypatch):
    sample = (
        "BSS aa:bb:cc:dd:ee:01(on wlan0)\n\tfreq: 5180\n\tsignal: -60.00 dBm\n"
        "\tSSID: WepNet\n\tcapability: ESS Privacy (0x0011)\n"
        "BSS aa:bb:cc:dd:ee:02(on wlan0)\n\tfreq: 5180\n\tsignal: -61.00 dBm\n"
        "\tSSID: OweNet\n\tRSN:\t * Version: 1\n"
        "\t\t * Authentication suites: OWE\n"
    )
    monkeypatch.setattr(wifi, "run", scan_only(sample))
    labels = {ap["ssid"]: wifi.security_label(ap) for ap in wifi.scan("wlan0")}
    assert labels["WepNet"] == "WEP"
    assert labels["OweNet"] == "OWE"


def test_scan_merges_both_sources(monkeypatch):
    """A card that answers with its own BSS must not hide the neighbours."""
    iw_text = (
        "BSS 74:90:bc:a7:15:9f(on wlo1)\n\tfreq: 5520\n\tsignal: -83.00 dBm\n"
        "\tSSID: MagentaWLAN-P2S3_5GHz\n"
    )
    nmcli_text = (
        "74\\:90\\:bc\\:a7\\:15\\:9f:MagentaWLAN-P2S3_5GHz:104:34:WPA2 WPA3\n"
        "AA\\:BB\\:CC\\:DD\\:EE\\:01:MagentaWLAN-P2S3:6:62:WPA2 WPA3\n"
    )

    def fake(cmd, timeout=10):
        if cmd and cmd[0] == "nmcli":
            return (0, "" if cmd[-1] == "rescan" else nmcli_text, "")
        return (0, iw_text, "")

    monkeypatch.setattr(wifi, "run", fake)
    aps = wifi.scan("wlo1")
    bssids = [ap["bssid"].lower() for ap in aps]
    assert len(aps) == 2, bssids
    assert "aa:bb:cc:dd:ee:01" in bssids
    assert bssids.count("74:90:bc:a7:15:9f") == 1


def test_the_connected_ap_keeps_the_live_signal():
    """nmcli's quality-to-dBm conversion made one AP look like two."""
    aps = [{"bssid": "7e:83:c2:4f:c1:46", "signal": -72.0},
           {"bssid": "aa:bb:cc:dd:ee:ff", "signal": -80.0}]
    link = {"bssid": "7E:83:C2:4F:C1:46", "signal": -63}

    wifi.trust_live_signal(aps, link)
    assert aps[0]["signal"] == -63.0
    assert aps[1]["signal"] == -80.0


def test_the_same_access_point_is_not_listed_twice(monkeypatch):
    """iw prints lower case BSSIDs, nmcli upper case; one AP is still one AP."""
    iw_text = ("BSS 74:90:bc:a7:15:9f(on wlo1)\n\tfreq: 5520\n\tsignal: -81.00 dBm\n"
               "\tSSID: MagentaWLAN-P2S3_5GHz\n")
    nm_text = ("74\\:90\\:BC\\:A7\\:15\\:9F:MagentaWLAN-P2S3_5GHz:104:25:WPA2 WPA3\n"
               "74\\:90\\:BC\\:A7\\:15\\:A2::104:30:WPA2 WPA3\n")

    def fake(cmd, timeout=10):
        if cmd and cmd[0] == "nmcli":
            return (0, "" if cmd[-1] == "rescan" else nm_text, "")
        return (0, iw_text, "")

    monkeypatch.setattr(wifi, "run", fake)
    aps = wifi.scan("wlo1")
    assert len(aps) == 2, [ap["bssid"] for ap in aps]
    assert len({ap["bssid"].lower() for ap in aps}) == 2
