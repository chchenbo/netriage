# Troubleshooting

Every entry here happened on a real machine. One cause, one fix.

The installer checks all of this itself. If it printed `this shell not yet` but
`a new terminal ok`, you only need a new terminal.

---

## `netriage: command not found`

Installed, but your shell does not look there.

```bash
command -v netriage                 # nothing? then it is not on PATH
echo $PATH | tr ':' '\n' | grep local
```

```bash
rehash                              # forget cached command paths
export PATH="/usr/local/bin:$PATH"  # system install
export PATH="$HOME/.local/bin:$PATH" # per-user install
```

Put that line in `~/.zshrc` or `~/.bashrc` to make it stick.
`sudo bash install.sh` avoids the question: `/usr/local/bin` is on every PATH,
including sudo's.

---

## Typing `netriage` does nothing, and the folder changes

You are in a directory that contains a **folder** called `netriage`, and your
shell has `AUTO_CD`. zsh sees a name that is not a command, sees a directory
with that name, and enters it. Nothing is printed.

```bash
pwd                                 # did it change to .../netriage?
```

**Fix:** the checkout must not be called `netriage`. `push.sh` copies it to
`~/netriage-src` for this reason:

```bash
mv ~/netriage ~/netriage-src 2>/dev/null || true
rehash
```

---

## `netriage --version` shows a package path I did not install

```
package  /home/you/netriage/netriage      <- a checkout, not the install
```

`python3 -m netriage` puts the current directory first on `sys.path`. The
installed command does not: it is a script with a shebang and an explicit
`sys.path` entry, so it always loads the installed copy.

---

## `netriage history` is empty although runs happened

Before 0.32.0 a `sudo netriage` run wrote a report owned by root, mode 0600.
The human could not read it, and `history` skipped it.

```bash
sudo netriage                       # repairs ownership, then records
netriage history
```

Or by hand:

```bash
sudo chown -R "$USER" ~/Documents/netriage-log
```

Since 0.33.0 `history` says so instead of staying silent, and lists the reports
it could not read.

---

## `sudo netriage` says command not found

You installed for your user only. sudo resets `HOME` and `PATH`, so it cannot
see `~/.local/bin`.

```bash
cd ~/netriage-src && sudo bash install.sh
```

---

## `scan` shows only one access point

Many drivers answer `iw dev wlan0 scan` with just the BSS you are associated
with. netriage asks again with `scan flush` and merges NetworkManager's list,
but a card can still refuse.

```bash
sudo iw dev wlan0 scan flush | grep -c '^BSS'
nmcli -t -f BSSID,SSID device wifi list
```

If both show one, the neighbourhood really is that empty.

---

## `unknown (ethtool not installed)` and friends

netriage never invents a value. A missing tool shows as `unknown`, never as
`none` or `0`.

```bash
sudo apt install -y iw ethtool lldpd dnsutils curl iputils-ping network-manager
sudo systemctl enable --now lldpd
```

---

## `--json` is refused on `history`, `report`, `compare`, `watch`

By design. Only the default run and `scan` produce JSON; the others write text,
Markdown or CSV. A script that pipes them into `jq` should fail loudly.

---

## Anything else

```bash
sudo netriage --debug --redact
```

writes `netriage-YYYYMMDD-NNNN-debug.txt` next to the report: every tool's
availability, the raw output, and the parsed result with addresses masked.
Attach that to the issue.
