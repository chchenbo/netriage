"""Layer 3 checks: addressing, name resolution, reachability, throughput."""

from __future__ import annotations

import re
import ssl
import time
import urllib.request

from .util import have, run

# Cloudflare refuses the request with 403 above roughly 80 MB, so ask for a
# size it will serve and fall back to a smaller one before giving up.
DEFAULT_DOWNLOADS = (
    "https://speed.cloudflare.com/__down?bytes=50000000",
    "https://speed.cloudflare.com/__down?bytes=25000000",
    "https://speed.cloudflare.com/__down?bytes=10000000",
)
DEFAULT_UPLOAD = "https://speed.cloudflare.com/__up"
# What the HTTP time is measured against. The first one that answers is used
# and the rest are never touched, so a blocked or retired site costs one
# attempt and does not turn into a false fault.
HTTP_TARGETS = (
    "https://www.google.com/generate_204",
    "https://www.wikipedia.org/",
    "https://www.youtube.com/",
)
DEFAULT_CAPTIVE = "http://connectivitycheck.gstatic.com/generate_204"


# ---------------------------------------------------------------- addressing


def is_link_local(ip):
    """169.254.0.0/16 and fe80::/10: the interface has no configuration."""
    if not ip:
        return False
    host = ip.split("/")[0].lower()
    return host.startswith("169.254.") or host.startswith("fe80:")


def address(iface):
    """IPv4 address and default gateway bound to *iface*."""
    info = {"iface": iface}
    code, out, _ = run(["ip", "-4", "-o", "addr", "show", "dev", iface])
    match = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+/\d+)", out)
    if match:
        info["ip"] = match.group(1)

    code, out, _ = run(["ip", "-4", "route", "show", "dev", iface, "default"])
    match = re.search(r"default via (\d+\.\d+\.\d+\.\d+)", out)
    if match:
        info["gateway"] = match.group(1)
    return info


def dns_servers(iface):
    servers = []
    code, out, _ = run(["resolvectl", "dns", iface], timeout=5)
    if code == 0:
        servers += re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", out)

    if not servers:
        code, out, _ = run(["nmcli", "-g", "IP4.DNS", "device", "show", iface], timeout=5)
        if code == 0:
            servers += [line.strip() for line in out.splitlines() if re.match(r"^\d", line.strip())]

    if not servers:
        try:
            with open("/etc/resolv.conf", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("nameserver"):
                        servers.append(line.split()[1])
        except OSError:
            pass

    return [s for s in dict.fromkeys(servers) if s and not s.startswith("127.0.0.53")]


# ---------------------------------------------------------------- probing


def ping(iface, target, count=4):
    """Loss and round-trip statistics. Returns None when unreachable."""
    code, out, _ = run(["ping", "-c", str(count), "-i", "0.2", "-W", "2", "-I", iface, target], timeout=15)
    if not out:
        return None

    loss = re.search(r"(\d+)% packet loss", out)
    stats = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)", out)
    result = {
        "target": target,
        "loss": int(loss.group(1)) if loss else None,
        "min": float(stats.group(1)) if stats else None,
        "avg": float(stats.group(2)) if stats else None,
        "max": float(stats.group(3)) if stats else None,
        "jitter": float(stats.group(4)) if stats else None,
    }
    if code != 0 and result["loss"] is None:
        return None
    return result


def resolve(server, name, timeout=4):
    """Ask one resolver."""
    if not have("dig"):
        return {"ok": None, "ms": None, "server": server, "reason": "dig not installed"}

    code, out, err = run(
        ["dig", "+time=%d" % timeout, "+tries=1", f"@{server}", name], timeout=timeout + 4
    )
    if code == 127:
        return {"ok": None, "ms": None, "server": server, "status": "unavailable",
                "reason": "dig not installed"}

    query_ms = re.search(r"Query time:\s*(\d+) msec", out)
    millis = int(query_ms.group(1)) if query_ms else None

    # "did not answer" and "answered but said no" are different problems with
    # different owners, so the status is kept, not collapsed into a boolean.
    status_match = re.search(r"status:\s*([A-Z]+)", out)
    status = status_match.group(1) if status_match else ""
    if not status:
        return {"ok": None, "ms": millis, "server": server,
                "status": "timeout" if code != 0 else "no-answer",
                "reason": err or "no reply"}

    if status == "NOERROR":
        answered = "ANSWER: " in out
        return {"ok": bool(answered), "ms": millis, "server": server,
                "status": "ok" if answered else "empty"}

    return {"ok": False, "ms": millis, "server": server,
            "status": {"NXDOMAIN": "nxdomain", "REFUSED": "refused",
                       "SERVFAIL": "servfail"}.get(status, status.lower())}


def captive(iface, url=DEFAULT_CAPTIVE):
    """Whether something intercepts HTTP, and where it points."""
    code, out, _ = run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}|%{redirect_url}",
         "--max-time", "6", "--interface", iface, url],
        timeout=12,
    )
    if code != 0 or not out:
        return {"intercepted": None, "url": ""}

    status, _, redirect = out.partition("|")
    status = status.strip()
    redirect = redirect.strip()
    if status in ("204", "200") and not redirect:
        return {"intercepted": False, "url": ""}
    return {"intercepted": True, "url": redirect}


# ---------------------------------------------------------------- throughput


def _open(url, timeout=12):
    from . import __version__

    request = urllib.request.Request(url, headers={"User-Agent": f"netriage/{__version__}"})
    return urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context())


def download(urls=None, seconds=8.0, chunk=65536, on_sample=None):
    """Stream a download, reporting instantaneous Mbps through *on_sample*."""
    errors = []
    for url in urls or DEFAULT_DOWNLOADS:
        try:
            return _download_once(url, seconds, chunk, on_sample)
        except Exception as exc:  # noqa: BLE001 - try the next mirror
            errors.append(f"{type(exc).__name__}: {exc}")
    raise RuntimeError("; ".join(errors) or "no download url")


def _download_once(url, seconds, chunk, on_sample):
    started = time.time()
    window_start = started
    window_bytes = 0
    total = 0
    peak = 0.0

    with _open(url) as response:
        while True:
            buffer = response.read(chunk)
            if not buffer:
                break
            total += len(buffer)
            window_bytes += len(buffer)
            now = time.time()
            if now - window_start >= 0.25:
                instant = window_bytes * 8 / (now - window_start) / 1e6
                peak = max(peak, instant)
                if on_sample:
                    on_sample(instant)
                window_start, window_bytes = now, 0
            if now - started >= seconds:
                break

    elapsed = max(time.time() - started, 1e-6)
    average = total * 8 / elapsed / 1e6
    # the final partial window never reaches 0.25 s, so it is not in peak;
    # a peak below the average is nonsense on the page
    return {
        "url": url,
        "mbps": round(average, 1),
        "peak": round(max(peak, average), 1),
        "seconds": round(elapsed, 2),
    }


def upload(url=DEFAULT_UPLOAD, seconds=8.0):
    """Time an upload of a fixed payload, growing it once if the link is fast."""
    # Two attempts at most, and the second only on a link fast enough that the
    # first finished before it could be timed.
    sizes = (4_000_000, 12_000_000)
    best = None
    for size in sizes:
        result = _upload_once(url, size)
        best = result
        if result["seconds"] >= 0.8:
            break
    return best


def _upload_once(url, size, timeout=40):
    from . import __version__

    payload = b"\0" * size
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "User-Agent": f"netriage/{__version__}",
            "Content-Type": "application/octet-stream",
        },
    )
    started = time.time()
    with urllib.request.urlopen(request, timeout=timeout,
                                context=ssl.create_default_context()) as response:
        response.read(64)
    elapsed = max(time.time() - started, 1e-6)
    return {
        "mbps": round(size * 8 / elapsed / 1e6, 1),
        "seconds": round(elapsed, 2),
        "bytes": size,
    }


def _probe(url, samples, timeout=6):
    """Timings in ms for one destination; empty means it never answered."""
    timings = []
    for _ in range(samples):
        begin = time.time()
        try:
            with _open(url, timeout=timeout) as response:
                response.read(64)
            timings.append((time.time() - begin) * 1000)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.15)
    return timings


def latency_ms(samples=3):
    """HTTP round-trip time, using the first destination that answers.

    The first attempt is short-tempered: a site that does not answer within a
    few seconds is not the one to measure, so the next is tried. Only the
    destination that worked gets the remaining samples.
    """
    for url in HTTP_TARGETS:
        timings = _probe(url, 1, timeout=4)
        if timings:
            break
    else:
        return None

    if samples > 1:
        timings += _probe(url, samples - 1)

    average = sum(timings) / len(timings)
    jitter = 0.0
    if len(timings) > 1:
        jitter = sum(abs(a - b) for a, b in zip(timings, timings[1:])) / (len(timings) - 1)
    return {"avg": round(average, 1), "jitter": round(jitter, 1)}
