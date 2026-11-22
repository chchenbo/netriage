"""Tests for the throughput and portal probes."""

from netriage import network


class FakeResponse:
    def __init__(self, payload=b"ok"):
        self.payload = payload

    def read(self, size):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_upload_scales_the_payload_once(monkeypatch):
    """A fixed size is either too small to time or far too slow."""
    sizes = []

    def fake(url, size, timeout=40):
        sizes.append(size)
        return {"mbps": 10.0, "seconds": 0.2, "bytes": size}

    monkeypatch.setattr(network, "_upload_once", fake)
    result = network.upload()

    assert sizes == [4_000_000, 12_000_000], "a fast link should get a bigger sample"
    assert result["bytes"] == 12_000_000


def test_upload_stops_when_the_first_sample_is_slow_enough(monkeypatch):
    sizes = []

    def fake(url, size, timeout=40):
        sizes.append(size)
        return {"mbps": 2.0, "seconds": 3.0, "bytes": size}

    monkeypatch.setattr(network, "_upload_once", fake)
    network.upload()
    assert sizes == [4_000_000]


def test_captive_reports_the_target(monkeypatch):
    monkeypatch.setattr(
        network, "run",
        lambda *a, **k: (0, "302|https://login.example.edu/portal", ""),
    )
    result = network.captive("wlp5s0")
    assert result["intercepted"] is True
    assert result["url"] == "https://login.example.edu/portal"


def test_captive_is_clean_on_204(monkeypatch):
    monkeypatch.setattr(network, "run", lambda *a, **k: (0, "204|", ""))
    assert network.captive("wlp5s0") == {"intercepted": False, "url": ""}


def test_captive_unknown_without_output(monkeypatch):
    monkeypatch.setattr(network, "run", lambda *a, **k: (1, "", "no route"))
    assert network.captive("wlp5s0")["intercepted"] is None


def test_resolve_keeps_the_dns_status(monkeypatch):
    monkeypatch.setattr(network, "have", lambda tool: True)

    monkeypatch.setattr(network, "run", lambda *a, **k: (
        0, ";; ->>HEADER<<- opcode: QUERY, status: REFUSED, id: 1\n"
           ";; Query time: 2 msec\n", ""))
    result = network.resolve("10.0.0.1", "example.com")
    assert result["status"] == "refused"
    assert result["ok"] is False


def test_resolve_reports_nxdomain(monkeypatch):
    monkeypatch.setattr(network, "have", lambda tool: True)
    monkeypatch.setattr(network, "run", lambda *a, **k: (
        0, ";; ->>HEADER<<- opcode: QUERY, status: NXDOMAIN, id: 1\n"
           ";; Query time: 1 msec\n", ""))
    assert network.resolve("10.0.0.1", "nope")["status"] == "nxdomain"


def test_resolve_reports_silence(monkeypatch):
    monkeypatch.setattr(network, "have", lambda tool: True)
    monkeypatch.setattr(network, "run", lambda *a, **k: (9, "", "timed out"))
    result = network.resolve("10.0.0.1", "example.com")
    assert result["status"] == "timeout"
    assert result["ok"] is None


def test_resolve_reports_success(monkeypatch):
    monkeypatch.setattr(network, "have", lambda tool: True)
    monkeypatch.setattr(network, "run", lambda *a, **k: (
        0, ";; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1\n"
           ";; ANSWER: 1\n;; Query time: 4 msec\nANSWER: 1\n", ""))
    result = network.resolve("10.0.0.1", "example.com")
    assert result["ok"] is True and result["status"] == "ok"




def test_the_first_destination_that_answers_is_used(monkeypatch):
    """A blocked site costs one attempt and changes nothing else."""
    calls = []

    def fake(url, samples, timeout=6):
        calls.append((url, samples))
        return [40.0] * samples if "wikipedia" in url else []

    monkeypatch.setattr(network, "_probe", fake)
    result = network.latency_ms()

    assert result["avg"] == 40.0
    assert "wikipedia" in calls[-1][0]
    assert sum(1 for url, _ in calls if "wikipedia" in url) == 2


def test_a_healthy_first_destination_is_measured_alone(monkeypatch):
    calls = []

    def fake(url, samples, timeout=6):
        calls.append((url, samples))
        return [30.0] * samples

    monkeypatch.setattr(network, "_probe", fake)
    result = network.latency_ms()

    assert result["avg"] == 30.0
    assert len({url for url, _ in calls}) == 1, "only one destination is touched"
    assert sum(samples for _, samples in calls) == 3, "three samples in total"


def test_nothing_answering_gives_no_value(monkeypatch):
    monkeypatch.setattr(network, "_probe", lambda url, samples, timeout=6: [])
    assert network.latency_ms() is None
