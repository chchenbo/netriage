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
