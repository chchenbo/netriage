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

**After `pipx install`** the command is in `~/.local/bin`:

```bash
pipx ensurepath                     # adds it, then open a new terminal
rehash                              # zsh: forget the cached list of commands
ls -l ~/.local/bin/netriage         # the binary is there either way
```

**After `install.sh`** the command is in `/usr/local/bin`, which is on every
PATH including sudo's:

```bash
rehash
export PATH="/usr/local/bin:$PATH"
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

**After a pipx install** the link is missing. pipx puts the command in
`~/.local/bin`, and sudo does not look there:

```bash
sudo ln -sf "$(command -v netriage)" /usr/local/bin/netriage
```

`command -v` must print something first. If it prints nothing,
`pipx install netriage` did not finish; run it again.

**After install.sh** the install went to your user, not the system. sudo resets
`HOME` and `PATH`, so it cannot see `~/.local/bin`:

```bash
cd ~/netriage-src && sudo bash install.sh
```

---

## Two copies are installed

`pipx install` and `install.sh` do not know about each other. Check which one
you are running:

```bash
netriage --version
# package  /usr/local/lib/python3.12/dist-packages/netriage   <- install.sh
# package  ~/.local/share/pipx/venvs/netriage/lib/...         <- pipx

readlink -f "$(command -v netriage)"
# ~/.local/bin/netriage  -> pipx, and the link in /usr/local/bin points there
# /usr/local/lib/...     -> install.sh wrote it directly
```

`PATH` decides which one wins, and `/usr/local/bin` usually comes before
`~/.local/bin`. Remove the one you do not want:

```bash
pipx uninstall netriage && sudo rm -f /usr/local/bin/netriage    # pipx
cd ~/netriage-src && sudo bash uninstall.sh                      # install.sh
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
