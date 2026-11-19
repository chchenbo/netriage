"""Tests for comparing two saved runs."""

import io
from contextlib import redirect_stdout

from netriage import compare


def capture(func, *args):
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        func(*args)
    return buffer.getvalue()


def facts(signal=-71, busy=12.0, retry=13.1, down=258.5, up=12.0, rate=780,
          channel=36, width=160, ssid="School", loss=0, stamp="2026-11-14T21:07:48"):
    return {
        "timestamp": stamp,
        "label": "living-room",
        "link": {"connected": True, "ssid": ssid, "bssid": "aa",
                 "signal": signal, "channel": channel, "width_mhz": width,
                 "band": "5 GHz", "tx_rate": rate},
        "busy_pct": busy,
        "station": {"retry_pct": retry},
        "speed": {"mbps": down, "peak": down},
        "upload": {"mbps": up},
        "ping_gateway": {"avg": 2.6, "loss": loss, "jitter": 0.7},
        "ping_internet": {"avg": 5.5, "loss": loss, "jitter": 1.0},
    }


def table(before, after):
    return {row["name"]: row for row in compare.rows(before, after)}


def test_improvement_is_labelled_better():
    rows = table(facts(signal=-71), facts(signal=-60))
    assert rows["Signal"]["verdict"] == "better"
    assert rows["Signal"]["change"] == "+11"


def test_regression_is_labelled_worse():
    rows = table(facts(signal=-60), facts(signal=-75))
    assert rows["Signal"]["verdict"] == "worse"


def test_lower_is_better_for_retries():
    rows = table(facts(retry=13.1), facts(retry=4.0))
    assert rows["Retries"]["verdict"] == "better"


def test_tiny_difference_is_same():
    rows = table(facts(signal=-60.0), facts(signal=-60.2))
    assert rows["Signal"]["verdict"] == "same"


def test_negotiated_rate_is_shown_but_not_judged():
    """Narrowing the channel lowers it deliberately; that is not a regression."""
    rows = table(facts(rate=780), facts(rate=585))
    assert rows["Negotiated"]["verdict"] == ""
    assert rows["Negotiated"]["change"] == "-195"


def test_missing_measurement_is_not_a_change():
    before = facts()
    after = facts()
    del after["upload"]
    row = table(before, after)["Upload"]
    assert row["after"] == "n/a"
    assert row["verdict"] == "n/a"


def test_context_reports_what_was_changed():
    changes = dict((name, (old, new)) for name, old, new in
                   compare.context(facts(channel=36, width=160), facts(channel=104, width=80)))
    assert changes["channel"] == ("36", "104")
    assert changes["width"] == ("160 MHz", "80 MHz")
    assert "ssid" not in changes


def test_summary_counts():
    _, text = compare.summarise(facts(signal=-71, retry=13.1), facts(signal=-60, retry=4.0))
    assert "improved" in text


def test_render_stays_inside_the_rule():
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        compare.render(facts(), facts(channel=104, width=80))
    from netriage.util import rule_width

    width = rule_width()
    for line in buffer.getvalue().splitlines():
        assert len(line) <= width + 20, line   # allow for escape codes


def test_different_media_are_called_out():
    before = facts()
    after = facts()
    after["medium"] = "wired"
    assert any("different media" in note for note in compare.mismatches(before, after))


def test_different_networks_are_called_out():
    notes = compare.mismatches(facts(ssid="School"), facts(ssid="School-Guest"))
    assert any("different networks" in note for note in notes)


def test_matching_runs_produce_no_warning():
    assert compare.mismatches(facts(), facts(signal=-60)) == []


def trend_entries():
    def one(number, stamp, ssid, signal, retry, down, up, rtt):
        return (number, {
            "timestamp": stamp, "label": None,
            "link": {"ssid": ssid, "signal": signal, "tx_rate": 360.0},
            "station": {"retry_pct": retry},
            "speed": {"mbps": down, "peak": down * 1.4},
            "upload": {"mbps": up},
            "ping_gateway": {"avg": rtt, "loss": 0, "jitter": 4.0},
            "ping_internet": {"avg": rtt + 2, "loss": 0, "jitter": 3.0},
        })

    return [
        one(28, "2026-11-16T09:19:46", "BCS-IOT", -50, 5.1, 10.8, 8.5, 6.2),
        one(33, "2026-11-16T09:31:58", "BCS-Guest", -48, 7.0, 6.4, 5.5, 7.9),
        one(34, "2026-11-16T09:34:48", "BCS-Guest", -49, 8.0, 6.3, 13.4, 55.5),
    ]


def test_trend_lists_every_run():
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        compare.trend(trend_entries())
    text = buffer.getvalue()

    assert "TREND   3 runs" in text
    for number in ("28", "33", "34"):
        assert number in text
    assert "BCS-IOT" in text and "BCS-Guest" in text


def test_trend_keeps_the_decimals():
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        compare.trend(trend_entries())
    assert "10.8" in buffer.getvalue()
    assert "55.5" in buffer.getvalue()


def test_trend_rows_stay_inside_the_rule():
    from netriage.util import rule_width

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        compare.trend(trend_entries())
    width = rule_width()
    overlong = [line for line in buffer.getvalue().splitlines() if len(line) > width]
    assert not overlong, overlong


def test_render_names_the_network_not_a_label():
    from netriage import compare

    before = {"timestamp": "2026-11-16T15:24:21", "medium": "wifi",
              "link": {"ssid": "MagentaWLAN-5G", "signal": -52, "channel": 132}}
    after = {"timestamp": "2026-11-18T11:06:08", "medium": "wifi",
             "link": {"ssid": "BCS-IOT", "signal": -60, "channel": 36}}

    out = capture(compare.render, before, after)
    assert "MagentaWLAN-5G" in out
    assert "BCS-IOT" in out


def test_a_cable_is_named_by_its_speed():
    from netriage import compare

    wired = {"timestamp": "2026-11-16T15:24:21", "medium": "wired",
             "link": {"speed": 1000, "duplex": "full"}}
    out = capture(compare.render, wired, wired)
    assert "wired 1000 Mb/s" in out


def test_runs_with_nothing_in_common_are_not_compared():
    """A cable run and a radio run share no measurement."""
    wired = {"timestamp": "2026-11-19T11:49:43", "medium": "wired",
             "link": {"speed": 1000, "duplex": "full"}, "counters": {}}
    wireless = {"timestamp": "2026-11-19T11:51:32", "medium": "wifi",
                "link": {"ssid": "BCS-IOT", "signal": -63, "channel": 60}}

    out = capture(compare.render, wired, wireless)
    assert "nothing to compare" in out
    assert "no measurable change" not in out
