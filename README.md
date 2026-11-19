# netriage

**A field network check that prints a verdict, not a wall of numbers.**

Someone says *"the Wi-Fi is broken in room 2.14"*. You have a laptop, no
context, and ten minutes. `netriage` takes one pass of measurements and says
which of the usual things is wrong: coverage, congestion, retries, DHCP, DNS,
or a link that is fine and a ceiling somewhere else.

![netriage running](https://raw.githubusercontent.com/chchenbo/netriage/main/docs/demo.gif)

Linux only, standard library only, Python 3.8+. It reads `iw`, `ethtool`,
`nmcli`, `ping`, `dig` and `lldpcli` — there is nothing to measure on another
platform.

## Install

```bash
sudo apt install -y iw ethtool lldpd dnsutils curl iputils-ping network-manager
sudo systemctl enable --now lldpd

git clone https://github.com/chchenbo/netriage netriage-src
cd netriage-src
sudo bash install.sh
```

That puts the command in `/usr/local/bin`, so from any directory:

```bash
netriage
```

`sudo netriage` adds what only root can read — the access point's own load
figures and client counts — and the report says so on the line it applies to:

```
  Channel busy      n/a - run with sudo to read it from the AP
```

## Use

```bash
netriage                      # what is wrong with this link, right now?
netriage scan                 # what is around me, and how busy are the channels?
netriage watch --minutes 20   # is the fault intermittent? when does it drop?
netriage read 12              # what did run 12 look like?
netriage report 12            # the same run as Markdown, for a ticket
netriage compare              # did the change actually help?
netriage history              # the saved runs
```

**→ [GUIDE.md](GUIDE.md)** — every command, every flag, and what each number
means.
**→ [TROUBLESHOOTING.md](TROUBLESHOOTING.md)** — what to do when the command
is not found, or a report looks wrong.

## What it will not do

It measures and it states what it measured. It does not tell you what to
change: it does not know the building, the budget or the policy, and a generic
recommendation is worse than none.

A value it could not read shows as `unknown`, never as zero. An interface that
cannot route is not measured at all, because the probes would silently travel
over a different one.

## License

MIT
