"""Rules that turn measurements into a verdict.

The point of this module is that a technician should not have to interpret
RSSI, channel utilisation and retry counters in their head at 8am in a
corridor. Every rule states what was seen, why it matters and what to do.
"""

from __future__ import annotations

from . import network, wifi, wired

CRITICAL = "critical"
WARNING = "warning"
INFO = "info"

RANK = {CRITICAL: 0, WARNING: 1, INFO: 2}


class Finding:
    def __init__(self, severity, title, detail):
        self.severity = severity
        self.title = title
        self.detail = detail

    def __repr__(self):
        return f"<{self.severity}: {self.title}>"


def utilization(facts):
    """Channel load, from our own radio if possible, else from what the AP says."""
    survey_data = facts.get("survey") or {}
    if survey_data.get("busy_pct") is not None:
        return survey_data["busy_pct"], "own radio"

    bssid = ((facts.get("link") or {}).get("bssid") or "").lower()
    if bssid:
        for ap in facts.get("aps") or []:
            if (ap.get("bssid") or "").lower() == bssid and ap.get("util_pct") is not None:
                return ap["util_pct"], "reported by AP"
    return None, None


def analyze(facts):
    """Return findings, most serious first."""
    found = []
    facts["busy_pct"], facts["busy_source"] = utilization(facts)
    link = facts.get("link") or {}
    station = facts.get("station") or {}
    survey_data = facts.get("survey") or {}
    aps = facts.get("aps") or []
    net = facts.get("address") or {}

    if facts.get("medium") == "wired":
        _wired_link(found, link, facts)
        if not link.get("connected"):
            return _sorted(found)
        _addressing(found, facts, net)
        _subnet(found, net)
        _resolution(found, facts)
        _reachability(found, facts)
        _throughput(found, facts)
        return _sorted(found)

    if not link.get("connected"):
        found.append(
            Finding(
                CRITICAL,
                "Not associated with any access point",
                "The adapter is not connected to a network at all.",
            )
        )
        return _sorted(found)

    _coverage(found, link, survey_data)
    _congestion(found, link, facts, aps)
    _quality(found, link, station)
    _roaming(found, link, aps)
    _band(found, link, aps)
    _addressing(found, facts, net)
    _subnet(found, net)
    _resolution(found, facts)
    _reachability(found, facts)
    _beacon_bloat(found, aps)
    _channel_width(found, link)
    _dfs(found, link)
    _throughput(found, facts)
    _capacity(found, link, facts)
    _open_network(found, facts)
    _jitter(found, facts)

    return _sorted(found)


# ---------------------------------------------------------------- rules


def _channel_width(found, link):
    """A wide channel spends the same power over more subcarriers."""
    width = link.get("width_mhz")
    signal = link.get("signal")
    if width is None or signal is None:
        return
    if width < 160 or signal > -70:
        return

    found.append(
        Finding(
            INFO,
            f"{width} MHz channel at {int(signal)} dBm",
            "A channel this wide needs a strong signal: the same power is "
            "spread across more subcarriers, so the margin at the edge of "
            "coverage is thin.",
        )
    )


def _dfs(found, link):
    """5 GHz channels 52-144 are shared with radar."""
    channel = link.get("channel")
    if channel is None or link.get("band") != "5 GHz":
        return
    if not 52 <= channel <= 144:
        return

    found.append(
        Finding(
            INFO,
            f"Channel {channel} is a DFS channel",
            "These channels are shared with radar. If one is detected the AP "
            "must leave the channel, and every client drops for a few seconds.",
        )
    )


def _capacity(found, link, facts):
    """Say when the radio is clearly not the bottleneck."""
    speed = facts.get("speed") or {}
    if not isinstance(speed, dict):
        return
    achieved = speed.get("mbps")
    negotiated = link.get("tx_rate")
    if achieved is None or not negotiated or negotiated < 100:
        return

    ratio = achieved / negotiated
    if ratio >= 0.10:
        return

    found.append(
        Finding(
            INFO,
            f"Throughput is {achieved:g} Mb/s on a {negotiated:g} Mb/s link",
            f"Only {ratio * 100:.0f}% of what this link negotiated, while the "
            "radio itself looks healthy.",
        )
    )


def _open_network(found, facts):
    """An open SSID is normal; an open SSID with no portal is an open door."""
    if facts.get("ap_security") != "open":
        return
    if facts.get("captive"):          # a portal is the access control
        return
    if facts.get("captive") is None:  # unknown, so do not claim anything
        return

    found.append(
        Finding(
            INFO,
            "This network is open and asks nobody to sign in",
            "Traffic is unencrypted and anyone in range can join. Nothing here "
            "proves who did what.",
        )
    )


def _jitter(found, facts):
    """Uneven latency is what ruins calls and video, and it is invisible in an average."""
    internet = facts.get("ping_internet") or {}
    gateway = facts.get("ping_gateway") or {}

    average = internet.get("avg")
    variation = internet.get("jitter")
    if average is None or variation is None:
        return
    if variation < 20 or variation < 0.5 * average:
        return

    if (gateway.get("jitter") or 0) >= 15:
        # Uneven on the first hop. Whether that is the air or the queue behind
        # the AP depends on how healthy the radio itself looks.
        station = facts.get("station") or {}
        retry = station.get("retry_pct") or 0
        busy = facts.get("busy_pct") or 0

        if retry >= 20 or busy >= 70:
            detail = "Latency is as uneven on the first hop as it is further out."
        else:
            detail = ("The first hop is uneven while retries and channel load both "
                      "look ordinary.")
    else:
        detail = "The first hop is steady, so the variation is added beyond it."

    found.append(
        Finding(
            INFO,
            f"Latency is uneven ({variation:.1f} ms of variation "
            f"around {average:.1f} ms)",
            detail,
        )
    )


def _throughput(found, facts):
    """A measurement that did not happen must not look like one that passed."""
    for key, name in (("speed", "Download"), ("upload", "Upload"),
                      ("latency", "HTTP latency")):
        result = facts.get(key)
        if not isinstance(result, dict) or result.get("skipped"):
            continue
        error = result.get("error")
        if not error:
            continue
        if "403" in error or "429" in error:
            detail = "The mirror refused the request."
        else:
            detail = f"The {name.lower()} test did not complete."
        found.append(Finding(INFO, f"{name} could not be measured", detail))
    return


def _wired_link(found, link, facts):
    """Rules that only make sense on a cable."""
    if not link.get("connected"):
        found.append(
            Finding(
                CRITICAL,
                "No link on the cable",
                "The port reports no carrier, so nothing above layer 1 can work.",
            )
        )
        return

    duplex = (link.get("duplex") or "").lower()
    speed = link.get("speed")

    if duplex == "half":
        found.append(
            Finding(
                CRITICAL,
                "Link negotiated half duplex",
                "Half duplex on a modern switched port means the two ends disagree "
                "about the configuration. Throughput and latency will both suffer badly.",
            )
        )

    if speed == 100:
        found.append(
            Finding(
                WARNING,
                "Link negotiated at 100 Mb/s",
                "A gigabit port fell back to 100 Mb/s. Either the switch port is "
                "configured that way, or the cable is damaged and only two pairs work.",
            )
        )

    raw_counters = facts.get("counters") or {}
    counters, checksum_counters = wired.classify(raw_counters)

    if checksum_counters:
        total_checksum = sum(checksum_counters.values())
        found.append(
            Finding(
                INFO,
                f"{total_checksum:,} checksum errors reported by the adapter",
                "Not frame errors: with checksum offload the card records what "
                "it saw on the wire. A few of these is ordinary.",
            )
        )

    if counters:
        total = sum(counters.values())
        worst = ", ".join(f"{name}={value}" for name, value in list(counters.items())[:3])
        packets = facts.get("packets")

        # These are cumulative counters since boot: four errors in four million
        # frames is background noise, not a broken cable.
        if packets:
            per_million = total / (packets / 1_000_000)
            if per_million >= 100:
                severity, headline = CRITICAL, f"{per_million:.0f} frame errors per million"
            elif per_million >= 10:
                severity, headline = WARNING, f"{per_million:.1f} frame errors per million"
            else:
                severity, headline = INFO, f"{total:,} frame errors, a normal background rate"
            detail = (f"{total:,} errors out of {packets:,} frames ({worst}).")
        else:
            severity, headline = WARNING, f"{total:,} physical-layer frame errors"
            detail = (f"Errors the driver attributes to the wire: {worst}. The frame "
                      f"count is not readable, so this is a total, not a rate.")

        found.append(
            Finding(
                severity,
                headline,
                detail,
            )
        )

    tools = facts.get("tools") or {}
    if not (facts.get("lldp") or {}).get("switch") and tools.get("lldpcli", True):
        found.append(
            Finding(
                INFO,
                "The switch did not identify itself",
                "No LLDP or CDP neighbour was received, so the port cannot be mapped "
                "to a named switch port automatically.",
            )
        )


def _coverage(found, link, survey_data):
    signal = link.get("signal")
    if signal is None:
        return

    if signal <= -78:
        found.append(
            Finding(
                CRITICAL,
                f"Very weak signal ({signal} dBm)",
                "At this level the link is below the usable threshold; expect drops "
                "and retransmissions even when the network 'works'.",
            )
        )
    elif signal <= -70:
        found.append(
            Finding(
                WARNING,
                f"Weak signal ({signal} dBm)",
                "Enough to associate, not enough for stable throughput.",
            )
        )

    noise = survey_data.get("noise")
    if noise is not None:
        snr = signal - noise
        if snr < 15:
            found.append(
                Finding(
                    CRITICAL,
                    f"Low signal-to-noise ratio ({snr} dB)",
                    f"Signal {signal} dBm against a noise floor of {noise} dBm.",
                )
            )
        elif snr < 25:
            found.append(
                Finding(
                    WARNING,
                    f"Marginal signal-to-noise ratio ({snr} dB)",
                    f"Signal {signal} dBm, noise {noise} dBm.",
                )
            )


def _congestion(found, link, facts, aps):
    busy = facts.get("busy_pct")
    if busy is not None:
        if busy >= 70:
            found.append(
                Finding(
                    CRITICAL,
                    f"Channel is {busy:.0f}% busy",
                    "The channel is saturated by traffic, not by distance. A stronger "
                    "signal will not fix this.",
                )
            )
        elif busy >= 50:
            found.append(
                Finding(
                    WARNING,
                    f"Channel is {busy:.0f}% busy",
                    "Heavy but not saturated. Performance will degrade at peak times.",
                )
            )

    channel = link.get("channel") or 0
    on_two_four = (link.get("band") or "2.4 GHz") == "2.4 GHz" and channel <= 14

    # Channel 1 exists on 2.4 GHz and on 6 GHz; counting both would invent a
    # crowded 2.4 GHz channel that is not there.
    if channel and on_two_four:
        same = [ap for ap in aps
                if ap.get("channel") == channel
                and (ap.get("band") or "2.4 GHz") == "2.4 GHz"]
        if len(same) >= 4:
            found.append(
                Finding(
                    WARNING,
                    f"{len(same)} access points share 2.4 GHz channel {channel}",
                    "Co-channel interference: everyone waits for everyone else.",
                )
            )
        if channel not in (1, 6, 11):
            found.append(
                Finding(
                    INFO,
                    f"2.4 GHz is on channel {channel}",
                    "Only 1, 6 and 11 are non-overlapping in the 2.4 GHz band.",
                )
            )


def _quality(found, link, station):
    retry = station.get("retry_pct")
    failed = station.get("failed_pct")
    if retry is not None and retry >= 35:
        found.append(
            Finding(
                CRITICAL,
                f"Extremely high retransmission rate ({retry}%)",
                "More than a third of frames need resending. Usable throughput collapses "
                "and latency rises far beyond what the signal strength suggests.",
            )
        )
    elif retry is not None and retry >= 20:
        found.append(
            Finding(
                WARNING,
                f"High retransmission rate ({retry}%)",
                "The driver is resending a fifth of its frames, which halves usable "
                "throughput and hides behind an apparently healthy signal.",
            )
        )
    if retry is not None and 10 <= retry < 20:
        found.append(
            Finding(
                INFO,
                f"Retransmission rate is elevated ({retry}%)",
                "One frame in eight is being resent. Not a failure, but it costs "
                "throughput and adds jitter to everything on this link.",
            )
        )
    if failed is not None and failed >= 5:
        found.append(
            Finding(
                WARNING,
                f"{failed}% of transmitted frames failed outright",
                "Frames that exhaust retries indicate the link budget is exhausted.",
            )
        )
    # beacon_loss is cumulative with no time base; judging it produced false
    # alarms. Intermittent drops belong to `watch`.


def _roaming(found, link, aps):
    """Detect a client sitting on a far AP while a closer one is available."""
    ssid = link.get("ssid")
    current = link.get("signal")
    bssid = (link.get("bssid") or "").lower()
    if not ssid or current is None:
        return

    neighbours = [
        ap
        for ap in aps
        if ap.get("ssid") == ssid and ap.get("bssid", "").lower() != bssid
    ]
    if not neighbours:
        return

    best = max(neighbours, key=lambda ap: ap.get("signal", -200))
    if best.get("signal", -200) > current + 10:
        found.append(
            Finding(
                WARNING,
                "Client is stuck on a distant access point",
                f"Current AP {current} dBm, but the same SSID is {best.get('signal'):.0f} dBm "
                f"on {best.get('bssid')} (channel {best.get('channel')}).",
            )
        )


def _band(found, link, aps):
    channel = link.get("channel") or 0
    if (link.get("band") or "2.4 GHz") != "2.4 GHz" or channel > 14:
        return

    ssid = link.get("ssid")
    five = [
        ap
        for ap in aps
        if ap.get("ssid") == ssid and 0 < ap.get("channel", 0) and ap["channel"] > 14
    ]
    if five:
        best = max(five, key=lambda ap: ap.get("signal", -200))
        them, ours = best.get("signal"), link.get("signal")
        if them is not None and ours is not None and them < ours - 12:
            # A 5 GHz radio 12 dB weaker is not an upgrade; recommending it
            # would trade interference for a link that barely holds.
            found.append(
                Finding(
                    INFO,
                    "5 GHz is in range but much weaker",
                    f"Best 5 GHz signal is {them:.0f} dBm against {ours} dBm on "
                    f"2.4 GHz.",
                )
            )
        else:
            found.append(
                Finding(
                    INFO,
                    "The same SSID is available on 5 GHz",
                    f"Best 5 GHz signal is {them:.0f} dBm against {ours} dBm on "
                    f"2.4 GHz.",
                )
            )
    elif link.get("signal") is not None and link["signal"] < -70:
        found.append(
            Finding(
                INFO,
                "No 5 GHz coverage with this SSID in range",
                "Only 2.4 GHz is visible from this position.",
            )
        )


def _addressing(found, facts, net):
    if net.get("ip") and network.is_link_local(net["ip"]):
        found.append(
            Finding(
                CRITICAL,
                f"No DHCP lease - the address {net['ip']} is link-local",
                "169.254.0.0/16 is the range a machine gives itself when no DHCP "
                "server answered. It cannot reach anything outside this cable.",
            )
        )
        return

    if not net.get("ip"):
        found.append(
            Finding(
                CRITICAL,
                "No IPv4 address obtained",
                "Association succeeded but DHCP did not deliver a lease.",
            )
        )
    elif not net.get("gateway"):
        found.append(
            Finding(
                WARNING,
                "Address assigned but no default route",
                f"{net['ip']} without a default gateway.",
            )
        )


def _subnet(found, net):
    """A /16 or wider puts 65k devices in one broadcast domain."""
    ip = net.get("ip") or ""
    if "/" not in ip:
        return
    try:
        prefix = int(ip.split("/")[1])
    except (IndexError, ValueError):
        return
    if network.is_link_local(ip):
        return          # 169.254/16 is defined by RFC 3927, not a design choice
    if prefix <= 16:
        found.append(
            Finding(
                WARNING,
                f"Subnet is /{prefix} - one very large broadcast domain",
                f"{ip} shares a layer-2 domain with up to {2 ** (32 - prefix) - 2:,} "
                "addresses. ARP and broadcast traffic grow with every device.",
            )
        )


def _resolution(found, facts):
    """Name resolution, told apart by what the server actually said."""
    school = facts.get("dns_local") or facts.get("dns_school")
    public = facts.get("dns_public")
    if not school:
        return

    status = (school.get("status") or "").lower()
    if not status:
        # Reports saved before the status field existed only carry ok.
        status = ("unavailable" if school.get("ok") is None
                  else "ok" if school.get("ok") else "timeout")

    if status == "unavailable":
        found.append(
            Finding(
                INFO,
                "Name resolution was not measured",
                school.get("reason") or "the resolver test could not run.",
            )
        )
        return

    if public and public.get("status") == "unavailable":
        public = None

    if status in ("timeout", "no-answer"):
        if public and public.get("ok"):
            found.append(
                Finding(
                    CRITICAL,
                    "School DNS server does not answer",
                    f"{school.get('server')} gave no reply at all, while 1.1.1.1 "
                    f"resolved the same name.",
                )
            )
        else:
            found.append(
                Finding(
                    CRITICAL,
                    "Name resolution fails on both resolvers",
                    "Neither the internal nor the public resolver replied.",
                )
            )
        return

    if status == "refused":
        found.append(
            Finding(
                WARNING,
                "DNS server refused the query",
                f"{school.get('server')} is alive and answered REFUSED.",
            )
        )
        return

    if status == "servfail":
        found.append(
            Finding(
                WARNING,
                "DNS server returned SERVFAIL",
                f"{school.get('server')} answered but could not complete the lookup.",
            )
        )
        return

    if status == "nxdomain":
        found.append(
            Finding(
                INFO,
                "Resolver answered NXDOMAIN for the test name",
                f"{school.get('server')} replied normally; the name it was asked "
                f"about does not exist.",
            )
        )
        return

    if status == "empty":
        found.append(
            Finding(
                INFO,
                "Resolver answered but returned no records",
                f"{school.get('server')} said NOERROR with an empty answer.",
            )
        )
        return

    if school.get("ok"):
        tested = facts.get("dns_servers_tested") or []
        # A server that answered NXDOMAIN or REFUSED is alive; only silence
        # counts as dead here.
        dead = [r.get("server") for r in tested
                if (r.get("status") or "").lower() in ("timeout", "no-answer")]
        if dead and len(dead) < len(tested):
            found.append(
                Finding(
                    WARNING,
                    f"{len(dead)} of {len(tested)} resolvers did not answer",
                    "At least one configured server is dead: " + ", ".join(dead),
                )
            )

    if public and not public.get("ok") and school.get("ok"):
        found.append(
            Finding(
                INFO,
                "External DNS is blocked, internal DNS works",
                "The school resolver answered; 1.1.1.1 did not. On a filtered "
                "network that is expected.",
            )
        )

    if school.get("ms") and school["ms"] > 200:
        found.append(
            Finding(
                WARNING,
                f"Slow DNS answers ({school['ms']} ms)",
                "Every new connection pays this cost before any data moves.",
            )
        )


def _reachability(found, facts):
    gateway = facts.get("ping_gateway")
    if gateway and gateway.get("avg") is not None:
        if gateway["avg"] >= 300:
            found.append(
                Finding(
                    CRITICAL,
                    f"First hop takes {gateway['avg']:.0f} ms",
                    f"Reaching the default gateway costs {gateway['avg']:.0f} ms with "
                    f"{gateway['loss']}% loss. Everything past it inherits that latency.",
                )
            )
        elif gateway["avg"] >= 100:
            found.append(
                Finding(
                    WARNING,
                    f"Slow first hop ({gateway['avg']:.0f} ms)",
                    "A healthy access point answers in single-digit milliseconds.",
                )
            )

    if gateway and gateway.get("loss") and gateway["loss"] >= 20:
        found.append(
            Finding(
                CRITICAL,
                f"Packet loss to the gateway ({gateway['loss']}%)",
                "Losing packets to the first hop means the problem is in the radio "
                "link, not the internet uplink.",
            )
        )

    internet = facts.get("ping_internet")
    if internet and internet.get("loss") and internet["loss"] >= 5:
        found.append(
            Finding(
                WARNING,
                f"Packet loss to the internet ({internet['loss']}%)",
                f"Average latency {internet.get('avg')} ms, jitter {internet.get('jitter')} ms.",
            )
        )

    # A router answering ICMP slower than something behind it is deprioritising
    # ping, not adding latency. Worth saying only when the number looks alarming.
    if (gateway and internet
            and gateway.get("avg") and internet.get("avg")
            and gateway["avg"] >= 20
            and internet["avg"] * 2 < gateway["avg"]):
        found.append(
            Finding(
                INFO,
                f"The gateway answers slower than the internet ({gateway['avg']:.0f} ms vs {internet['avg']:.0f} ms)",
                "Traffic to the internet passes through the gateway, so it cannot "
                "really be faster. The router is deprioritising ICMP replies.",
            )
        )

    if facts.get("captive"):
        found.append(
            Finding(
                CRITICAL,
                "HTTP traffic is being intercepted",
                "A portal or transparent proxy answered instead of the expected 204.",
            )
        )


def _beacon_bloat(found, aps):
    """Many SSIDs on one radio means many beacons per second."""
    # Vendors increment either the first or the last octet; group both ways.
    # One radio is one device on one channel, so the channel is part of the key.
    by_tail, by_head = {}, {}
    for ap in aps:
        bssid = (ap.get("bssid") or "").lower()
        if len(bssid) != 17:
            continue
        name = ap.get("ssid") or "<hidden>"
        channel = ap.get("channel") or 0
        by_tail.setdefault((bssid[-8:], channel), set()).add(name)
        by_head.setdefault((bssid[:14], channel), set()).add(name)

    worst = max(list(by_tail.items()) + list(by_head.items()),
                key=lambda item: len(item[1]), default=None)
    if worst and len(worst[1]) >= 4:
        (suffix, _channel), names = worst
        found.append(
            Finding(
                INFO,
                f"{len(names)} SSIDs may share one radio (…{suffix})",
                "The BSSIDs share a prefix and sit on one channel, which usually "
                "means one access point serving several networks. Each SSID costs "
                "about ten beacons per second: " + ", ".join(sorted(names)),
            )
        )


def _sorted(found):
    return sorted(found, key=lambda item: RANK[item.severity])


# ---------------------------------------------------------------- advice


def recommend_channels(aps, band="2.4 GHz"):
    """Rank channels by how crowded they are, best first."""
    table = wifi.channel_load(aps, band=band)
    # 2.4 GHz only has three non-overlapping channels. Recommending 2 or 3
    # would be worse advice than recommending nothing.
    candidates = (1, 6, 11) if band == "2.4 GHz" else [36, 40, 44, 48, 52, 56, 60, 64,
                                                         100, 104, 108, 112, 116, 120,
                                                         124, 128, 132, 136, 140, 149,
                                                         153, 157, 161, 165]
    scored = []
    for channel in candidates:
        entry = table.get(channel)
        count = entry["count"] if entry else 0
        util = entry["util_avg"] if entry and entry["util_avg"] is not None else None
        scored.append((count, util, channel))

    # An AP that reported no utilisation is not an idle AP. Unknown ranks
    # behind known-quiet at the same population, and ties fall back to the
    # channel number so the order is stable and explainable.
    scored.sort(key=lambda row: (row[0], row[1] is None, row[1] or 0.0, row[2]))
    return scored
