"""Tests for the rules that turn measurements into a verdict.

These encode the discriminations that took real field data to work out:
coverage versus congestion versus interference versus a sticky client.
"""

from netriage import diagnose


def wireless(**overrides):
    facts = {
        "medium": "wifi",
        "link": {
            "connected": True,
            "ssid": "SchoolNet",
            "bssid": "aa:bb:cc:dd:ee:01",
            "signal": -58,
            "channel": 36,
            "band": "5 GHz",
        },
        "survey": {"noise": -92, "busy_pct": 15.0},
        "station": {"retry_pct": 4.0},
        "address": {"ip": "10.0.0.5/24", "gateway": "10.0.0.1"},
        "dns_school": {"ok": True, "ms": 4, "server": "10.0.0.1"},
        "dns_public": {"ok": True, "ms": 5, "server": "1.1.1.1"},
        "ping_gateway": {"avg": 2.0, "loss": 0, "jitter": 0.3},
        "ping_internet": {"avg": 6.0, "loss": 0, "jitter": 0.5},
        "captive": False,
        "aps": [],
        "busy_pct": 15.0,
        "busy_source": "own radio",
    }
    facts.update(overrides)
    return facts


def titles(findings):
    return [finding.title for finding in findings]


def test_healthy_link_produces_no_findings():
    assert diagnose.analyze(wireless()) == []


def test_not_associated():
    facts = wireless(link={"connected": False})
    found = diagnose.analyze(facts)
    assert "Not associated with any access point" in titles(found)


def test_coverage_gap():
    """Weak signal, idle channel, clean retries, no better AP to roam to."""
    facts = wireless(
        link={"connected": True, "ssid": "S", "bssid": "aa", "signal": -79,
              "channel": 36, "band": "5 GHz"},
        survey={"noise": -93, "busy_pct": 8.0},
        station={"retry_pct": 7.0},
        busy_pct=8.0,
    )
    found = titles(diagnose.analyze(facts))
    assert any("Very weak signal" in t for t in found)
    assert not any("stuck" in t for t in found)


def test_congestion_not_coverage():
    """Strong signal but the channel is saturated: a stronger signal cannot help."""
    facts = wireless(
        link={"connected": True, "ssid": "S", "bssid": "aa", "signal": -52,
              "channel": 44, "band": "5 GHz"},
        survey={"noise": -94, "busy_pct": 84.0},
        station={"retry_pct": 24.0},
        busy_pct=84.0,
    )
    found = titles(diagnose.analyze(facts))
    assert any("busy" in t for t in found)
    assert not any("weak signal" in t.lower() for t in found)


def test_interference_when_busy_is_low_but_retries_are_high():
    """The subtle one: congestion is not the only reason retries climb."""
    facts = wireless(
        link={"connected": True, "ssid": "S", "bssid": "aa", "signal": -62,
              "channel": 6, "band": "2.4 GHz"},
        survey={"noise": -78, "busy_pct": 22.0},
        station={"retry_pct": 31.0},
        busy_pct=22.0,
    )
    found = titles(diagnose.analyze(facts))
    assert any("signal-to-noise" in t for t in found)
    assert any("High retransmission" in t for t in found)


def test_sticky_client():
    facts = wireless(
        aps=[
            {"bssid": "aa:bb:cc:dd:ee:02", "ssid": "SchoolNet", "signal": -58, "channel": 36},
            {"bssid": "aa:bb:cc:dd:ee:01", "ssid": "SchoolNet", "signal": -76, "channel": 36},
        ],
        link={"connected": True, "ssid": "SchoolNet", "bssid": "aa:bb:cc:dd:ee:01",
              "signal": -76, "channel": 36, "band": "5 GHz"},
    )
    found = titles(diagnose.analyze(facts))
    assert any("stuck" in t for t in found)


def test_internal_dns_failure():
    facts = wireless(
        dns_school={"ok": False, "ms": None, "server": "10.0.0.1"},
        dns_public={"ok": True, "ms": 3, "server": "1.1.1.1"},
    )
    found = titles(diagnose.analyze(facts))
    assert any("School DNS server does not answer" in t for t in found)


def test_external_dns_blocked_is_not_a_failure():
    facts = wireless(
        dns_school={"ok": True, "ms": 4, "server": "10.0.0.1"},
        dns_public={"ok": False, "ms": None, "server": "1.1.1.1"},
    )
    found = diagnose.analyze(facts)
    assert not any(f.severity == diagnose.CRITICAL for f in found)
    assert any("External DNS is blocked" in f.title for f in found)


def test_dns_not_measured_is_reported_as_unknown():
    facts = wireless(
        dns_school={"ok": None, "ms": None, "server": "10.0.0.1", "reason": "dig not installed"},
        dns_public={"ok": None, "ms": None, "server": "1.1.1.1"},
    )
    found = diagnose.analyze(facts)
    assert not any(f.severity == diagnose.CRITICAL for f in found)
    assert any("not measured" in f.title for f in found)


def test_slow_first_hop_is_critical():
    facts = wireless(ping_gateway={"avg": 346.0, "loss": 0, "jitter": 128.0})
    found = diagnose.analyze(facts)
    assert any(f.severity == diagnose.CRITICAL and "First hop" in f.title for f in found)


def test_flat_16_reported():
    facts = wireless(address={"ip": "10.26.142.7/16", "gateway": "10.26.9.1"})
    found = titles(diagnose.analyze(facts))
    assert any("/16" in t for t in found)


def test_beacon_bloat_from_shared_suffix():
    facts = wireless(
        aps=[
            {"bssid": f"{prefix}:4f:bf:1f", "ssid": name, "channel": 1, "signal": -55}
            for prefix, name in [
                ("7e:83:c2", "BCS-IOT"),
                ("74:83:c2", "BCS_OwnDevice"),
                ("82:83:c2", "<hidden>"),
                ("7a:83:c2", "BCS-Guest"),
                ("86:83:c2", "BCS-Teacher"),
            ]
        ]
    )
    found = titles(diagnose.analyze(facts))
    assert any("may share one radio" in t for t in found)


def wired_healthy(**overrides):
    facts = {
        "medium": "wired",
        "link": {"connected": True, "speed": 1000, "duplex": "full", "autoneg": "on"},
        "lldp": {"switch": "Switch-B", "port": "Gi1/0/12", "vlan": "120"},
        "counters": {},
        "address": {"ip": "10.0.0.5/24", "gateway": "10.0.0.1"},
        "dns_school": {"ok": True, "ms": 4, "server": "10.0.0.1"},
        "dns_public": {"ok": True, "ms": 5, "server": "1.1.1.1"},
        "ping_gateway": {"avg": 0.4, "loss": 0, "jitter": 0.1},
        "ping_internet": {"avg": 6.0, "loss": 0, "jitter": 0.3},
        "aps": [],
        "captive": False,
    }
    facts.update(overrides)
    return facts


def test_healthy_wired_link():
    assert diagnose.analyze(wired_healthy()) == []


def test_wired_no_carrier():
    found = titles(diagnose.analyze(wired_healthy(link={"connected": False})))
    assert "No link on the cable" in found


def test_wired_duplex_mismatch_and_cable_errors():
    found = titles(
        diagnose.analyze(
            wired_healthy(
                link={"connected": True, "speed": 100, "duplex": "half"},
                counters={"rx_crc_errors": 42},
                lldp={},
            )
        )
    )
    assert any("half duplex" in t for t in found)
    assert any("100 Mb/s" in t for t in found)
    assert any("physical-layer" in t for t in found)
    assert any("did not identify itself" in t for t in found)


def test_wired_rules_never_mention_access_points():
    found = titles(
        diagnose.analyze(wired_healthy(link={"connected": True, "speed": 100, "duplex": "half"}))
    )
    assert not any("access point" in t.lower() for t in found)


def test_blocked_mirror_is_reported_as_a_note():
    """An unmeasured throughput must not be presented as a clean result."""
    facts = wireless(speed={"error": "HTTPError: HTTP Error 403: Forbidden"})
    found = diagnose.analyze(facts)
    notes = [f for f in found if f.severity == diagnose.INFO]
    assert any("could not be measured" in f.title for f in notes)
    assert not any(f.severity == diagnose.CRITICAL for f in found)


def test_successful_throughput_adds_nothing():
    facts = wireless(speed={"mbps": 104.8, "peak": 116.4}, upload={"mbps": 18.0})
    assert not any("could not be measured" in f.title for f in diagnose.analyze(facts))


def test_skipped_throughput_adds_nothing():
    facts = wireless(speed={"skipped": True}, upload={"skipped": True})
    assert not any("could not be measured" in f.title for f in diagnose.analyze(facts))


def test_elevated_retries_are_a_note_not_a_silence():
    """Between 10% and 20% the link is not broken, but it is not healthy either."""
    quiet = diagnose.analyze(wireless(station={"retry_pct": 9.9}))
    assert not any("etransmission" in f.title for f in quiet)

    note = diagnose.analyze(wireless(station={"retry_pct": 17.0}))
    assert any(f.severity == diagnose.INFO and "elevated" in f.title for f in note)

    warned = diagnose.analyze(wireless(station={"retry_pct": 20.0}))
    assert any(f.severity == diagnose.WARNING for f in warned)


def test_uneven_latency_is_reported_and_located():
    upstream = diagnose.analyze(
        wireless(
            ping_gateway={"avg": 5.0, "loss": 0, "jitter": 4.9},
            ping_internet={"avg": 29.6, "loss": 0, "jitter": 25.3},
        )
    )
    found = [f for f in upstream if "uneven" in f.title]
    assert found and "beyond it" in found[0].detail

    local = diagnose.analyze(
        wireless(
            ping_gateway={"avg": 18.0, "loss": 0, "jitter": 22.0},
            ping_internet={"avg": 30.0, "loss": 0, "jitter": 24.0},
        )
    )
    found = [f for f in local if "uneven" in f.title]
    assert found and "first hop" in found[0].detail


def test_steady_latency_is_not_reported():
    found = diagnose.analyze(wireless())
    assert not any("uneven" in f.title for f in found)


def test_wide_channel_at_weak_signal_is_flagged():
    narrow = diagnose.analyze(wireless(link={"connected": True, "ssid": "S", "bssid": "aa",
                                             "signal": -71, "channel": 36, "band": "5 GHz",
                                             "width_mhz": 80}))
    assert not any("MHz channel at" in f.title for f in narrow)

    wide = diagnose.analyze(wireless(link={"connected": True, "ssid": "S", "bssid": "aa",
                                           "signal": -71, "channel": 36, "band": "5 GHz",
                                           "width_mhz": 160}))
    assert any("160 MHz channel" in f.title for f in wide)


def test_wide_channel_with_a_strong_signal_is_fine():
    strong = diagnose.analyze(wireless(link={"connected": True, "ssid": "S", "bssid": "aa",
                                             "signal": -55, "channel": 36, "band": "5 GHz",
                                             "width_mhz": 160}))
    assert not any("MHz channel at" in f.title for f in strong)


def test_dfs_channel_is_flagged_as_a_note():
    """Radar-eviction drops look like random faults if nobody mentions DFS."""
    dfs = diagnose.analyze(wireless(link={"connected": True, "ssid": "S", "bssid": "aa",
                                          "signal": -60, "channel": 104, "band": "5 GHz",
                                          "width_mhz": 80}))
    assert any("DFS channel" in f.title for f in dfs)
    assert all(f.severity != diagnose.CRITICAL for f in dfs)


def test_non_dfs_channels_are_quiet():
    for channel in (36, 44, 48, 149, 157):
        found = diagnose.analyze(wireless(link={"connected": True, "ssid": "S", "bssid": "aa",
                                                "signal": -60, "channel": channel,
                                                "band": "5 GHz", "width_mhz": 80}))
        assert not any("DFS" in f.title for f in found), channel


def test_unknown_utilisation_is_not_treated_as_idle():
    """An AP that reports nothing must not outrank one we know is quiet."""
    aps = [
        {"bssid": "a1", "ssid": "Known", "channel": 1, "signal": -53, "util_pct": 18.0},
        {"bssid": "b1", "ssid": "", "channel": 11, "signal": -80},
    ]
    ranked = diagnose.recommend_channels(aps, "2.4 GHz")
    channels = [channel for _, _, channel in ranked]
    assert channels.index(1) < channels.index(11)


def test_equal_channels_are_ordered_by_number():
    aps = [
        {"bssid": "a1", "ssid": "", "channel": 11, "signal": -80},
        {"bssid": "b1", "ssid": "", "channel": 1, "signal": -80},
    ]
    channels = [channel for _, _, channel in diagnose.recommend_channels(aps, "2.4 GHz")]
    # ch 6 has no access points at all, so it legitimately comes first
    assert channels.index(1) < channels.index(11), "ties must be stable and explainable"


def test_low_throughput_on_a_healthy_radio_points_upstream():
    """Strong signal, low retries, a tenth of the rate: the radio is not it."""
    facts = wireless(
        link={"connected": True, "ssid": "BCS-IOT", "bssid": "aa", "signal": -49,
              "channel": 36, "band": "5 GHz", "width_mhz": 40, "tx_rate": 400.0},
        station={"retry_pct": 6.0},
        survey={"noise": -94, "busy_pct": 36.0},
        busy_pct=36.0,
        speed={"mbps": 10.8, "peak": 17.8},
    )
    found = [f for f in diagnose.analyze(facts) if "Mb/s on a" in f.title]
    assert found, "a tenth of the negotiated rate deserves a note"
    assert "10.8" in found[0].title and "400" in found[0].title


def test_healthy_throughput_is_not_flagged():
    facts = wireless(
        link={"connected": True, "ssid": "S", "bssid": "aa", "signal": -59,
              "channel": 36, "band": "5 GHz", "width_mhz": 80, "tx_rate": 650.0},
        speed={"mbps": 288.8, "peak": 315.5},
    )
    assert not any("Mb/s on a" in f.title for f in diagnose.analyze(facts))


def test_slow_internet_on_a_fast_link_is_a_note_not_a_fault():
    facts = wireless(
        link={"connected": True, "ssid": "S", "bssid": "aa", "signal": -59,
              "channel": 36, "band": "5 GHz", "tx_rate": 650.0},
        speed={"mbps": 45.0, "peak": 50.0},
    )
    found = [f for f in diagnose.analyze(facts) if "Mb/s on a" in f.title]
    assert found and found[0].severity == diagnose.INFO


def test_jitter_numbers_are_rounded():
    """20.384 ms reads like precision the measurement does not have."""
    facts = wireless(
        ping_gateway={"avg": 6.7, "loss": 0, "jitter": 1.9},
        ping_internet={"avg": 18.743, "loss": 0, "jitter": 20.384},
    )
    title = [f.title for f in diagnose.analyze(facts) if "uneven" in f.title][0]
    assert "20.4 ms" in title and "18.7 ms" in title
    assert "20.384" not in title


def test_capacity_note_covers_a_150_mbs_link():
    """150 Mb/s is not a slow link; 6 Mb/s on it is still worth a note."""
    facts = wireless(
        link={"connected": True, "ssid": "Guest", "bssid": "aa", "signal": -48,
              "channel": 36, "band": "5 GHz", "width_mhz": 40, "tx_rate": 150.0},
        speed={"mbps": 6.4, "peak": 12.5},
    )
    assert any("Mb/s on a" in f.title for f in diagnose.analyze(facts))


def test_open_network_without_a_portal_is_noted():
    facts = wireless(ap_security="open", captive=False)
    found = [f for f in diagnose.analyze(facts) if "open" in f.title]
    assert found and found[0].severity == diagnose.INFO


def test_open_network_with_a_portal_is_not_noted():
    facts = wireless(ap_security="open", captive=True)
    assert not [f for f in diagnose.analyze(facts) if "asks nobody" in f.title]


def test_secure_network_is_not_noted():
    facts = wireless(ap_security="WPA2-PSK", captive=False)
    assert not [f for f in diagnose.analyze(facts) if "asks nobody" in f.title]


def test_uneven_latency_is_reported_without_a_remedy():
    """The tool states what it measured; it does not tell IT what to change."""
    healthy_air = diagnose.analyze(wireless(
        ping_gateway={"avg": 55.5, "loss": 0, "jitter": 32.9},
        ping_internet={"avg": 57.6, "loss": 0, "jitter": 36.2},
        station={"retry_pct": 8.0}, busy_pct=46.0,
        survey={"noise": -94, "busy_pct": 46.0},
    ))
    finding = [f for f in healthy_air if "uneven" in f.title][0]
    assert not hasattr(finding, "action")


def dns_facts(school, public=None, tested=None):
    return wireless(
        dns_school=school,
        dns_public=public or {"ok": True, "ms": 3, "server": "1.1.1.1", "status": "ok"},
        dns_servers_tested=tested or [school],
    )


def test_refused_is_not_called_unreachable():
    found = diagnose.analyze(dns_facts(
        {"ok": False, "ms": 2, "server": "10.0.0.1", "status": "refused"}))
    titles = [f.title for f in found]
    assert "DNS server refused the query" in titles
    assert not any("does not answer" in t for t in titles)


def test_nxdomain_is_a_probe_problem_not_a_fault():
    found = diagnose.analyze(dns_facts(
        {"ok": False, "ms": 1, "server": "10.0.0.1", "status": "nxdomain"}))
    assert not any(f.severity == diagnose.CRITICAL for f in found)
    assert any("NXDOMAIN" in f.title for f in found)


def test_silence_is_still_critical():
    found = diagnose.analyze(dns_facts(
        {"ok": None, "ms": None, "server": "10.0.0.1", "status": "timeout"}))
    assert any(f.severity == diagnose.CRITICAL and "does not answer" in f.title
               for f in found)


def test_missing_dig_is_not_a_dns_failure():
    found = diagnose.analyze(dns_facts(
        {"ok": None, "ms": None, "server": "10.0.0.1", "status": "unavailable",
         "reason": "dig not installed"}))
    assert not any(f.severity == diagnose.CRITICAL for f in found)
    assert any("not measured" in f.title for f in found)


def test_one_dead_resolver_of_several_is_reported():
    tested = [{"ok": True, "ms": 3, "server": "10.0.0.1", "status": "ok"},
              {"ok": False, "ms": None, "server": "10.0.0.2", "status": "timeout"}]
    found = diagnose.analyze(dns_facts(tested[0], tested=tested))
    assert any("1 of 2 resolvers" in f.title for f in found)


def test_six_ghz_is_not_treated_as_two_point_four():
    """Channel 1 on 6 GHz is not the crowded 2.4 GHz channel 1."""
    facts = wireless(
        link={"connected": True, "ssid": "BCS-6E", "bssid": "aa", "signal": -55,
              "channel": 1, "band": "6 GHz", "tx_rate": 1200.0},
        aps=[{"bssid": f"aa:bb:cc:dd:ee:{i:02x}", "ssid": "BCS-6E", "channel": 1,
              "band": "6 GHz", "signal": -55} for i in range(6)],
    )
    titles = [f.title for f in diagnose.analyze(facts)]
    assert not any("share 2.4 GHz" in t for t in titles)
    assert not any("5 GHz" in t for t in titles)


def test_beacon_bloat_needs_one_channel():
    """Same first octets on different channels are different radios."""
    aps = []
    for index, channel in enumerate((1, 6, 11, 36, 44)):
        aps.append({"bssid": f"{index:02x}:aa:bb:cc:dd:ee", "ssid": f"SSID{index}",
                    "channel": channel, "signal": -55})
    titles = [f.title for f in diagnose.analyze(wireless(aps=aps))]
    assert not any("share one radio" in t for t in titles)


def test_nxdomain_is_not_counted_as_a_dead_resolver():
    tested = [{"ok": False, "ms": 1, "server": "10.0.0.1", "status": "nxdomain"},
              {"ok": True, "ms": 3, "server": "10.0.0.2", "status": "ok"}]
    found = diagnose.analyze(dns_facts(tested[1], tested=tested))
    assert not any("resolvers did not answer" in f.title for f in found)


def test_missing_lldpcli_is_not_a_finding():
    facts = wired_healthy(lldp={})
    facts["tools"] = {"lldpcli": False}
    assert not any("did not identify itself" in f.title
                   for f in diagnose.analyze(facts))


def test_checksum_errors_do_not_count_as_frame_errors():
    """A healthy port must not be called faulty for checksum offload noise."""
    facts = wired_healthy(counters={"rx_tcp_checksum_errors": 35}, packets=8_000_000)
    found = diagnose.analyze(facts)
    frame = [f for f in found if "frame errors" in f.title.lower()]
    assert not frame, [f.title for f in frame]
    assert any("checksum errors" in f.title for f in found)


def test_a_router_that_deprioritises_ping_is_explained():
    """The gateway cannot really be slower than something behind it."""
    facts = wireless(
        ping_gateway={"avg": 45.0, "loss": 0, "jitter": 12.0},
        ping_internet={"avg": 8.0, "loss": 0, "jitter": 1.0},
    )
    found = diagnose.analyze(facts)
    assert any("slower than the internet" in f.title for f in found)


def test_a_normal_gateway_figure_is_not_explained_away():
    facts = wireless(
        ping_gateway={"avg": 2.9, "loss": 0, "jitter": 4.6},
        ping_internet={"avg": 1.2, "loss": 0, "jitter": 0.1},
    )
    assert not any("slower than the internet" in f.title
                   for f in diagnose.analyze(facts))


def _http_facts(reached):
    names = ("google", "wikipedia", "youtube")
    return {
        "medium": "wifi",
        "link": {"connected": True, "signal": -55, "ssid": "x"},
        "address": {"ip": "10.0.0.2/24", "gateway": "10.0.0.1"},
        "latency": {
            "avg": 8.0 if reached else None, "jitter": 1.0,
            "reached": reached, "total": 3,
            "targets": [{"name": n, "ok": i < reached}
                        for i, n in enumerate(names)],
        },
    }


