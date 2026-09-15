# gpumon

A minimal cluster GPU overview over SSH. One line per node, one circle per GPU.

```
  gpumon  24 nodes · 09:41:07 · 0.9s

       0 1 2 3 4 5 6 7

  001  ● ● ● ● ○ ○ ○ ○   4/8   47%    18/192 GB
  002  ○ ○ ○ ○ ○ ○ ○ ○   0/8    0%     0/192 GB
  003  ● ● ● ● ○ ● ○ ●   6/8    0%    71/192 GB
  004  ● ● ● ● ● ● ● ●   8/8   91%   146/192 GB
  005  ○ ○ ○ ○ ○ ● ○ ○   0/8    0%     0/168 GB
  006  ✕  unreachable

  ○ 20 free   ● 27 busy   ● 1 faulty   ✕ 1 nodes down
  ○ idle   ● <50%   ● ≥50%   ● fault
```

| symbol | meaning |
|---|---|
| **○** grey | GPU idle |
| **●** green | GPU in use, under 50% utilisation |
| **●** bright / dark green | GPU in use, 50% utilisation or more |
| **●** red | GPU faulty (no telemetry, uncorrected ECC errors, driver error) |
| **✕** red | GPU slot missing, or node unreachable |

Busy GPUs use one green hue at two lightness levels: on a dark terminal the
busier GPU is the brighter one, on a light terminal it is the darker one.

The palette follows the terminal background, live. At start the terminal is
asked for its background colour (OSC 11). In the live view gpumon also turns
on colour-scheme notifications (mode 2031, supported by Ghostty, kitty,
WezTerm, foot, iTerm2 and others), so switching the system theme recolours
the running view immediately; terminals without that mode are re-asked every
few seconds. To pin a palette, set `GPUMON_THEME=light` or `dark`, or pass
`--theme`.

Each column is a physical GPU ID, so a broken card shows up in its own slot.

## Install

One line, no clone:

```sh
curl -fsSL https://raw.githubusercontent.com/mrzzmrzz/gpumon/main/install.sh | sh
```

This puts a single executable at `~/.local/bin/gpumon` (set `GPUMON_BIN` to
change it). Python 3.8+ with the standard library is all it needs, plus `ssh`
locally and `nvidia-smi` on the nodes.

To update, run the same line again. To remove, delete the file.

## Usage

```sh
gpumon discover        # find nodes once, cache them
gpumon                 # live view, refreshes every 2 s, q to quit
gpumon -w 5            # slower refresh
gpumon --once          # one snapshot (automatic when output is piped)
gpumon --temps         # add the hottest GPU of each node
gpumon -a              # number nodes 001, 002, ... instead of hostnames
gpumon --theme light   # force the light-background palette
gpumon -p 'gpu-\d+'    # only hosts matching a regex
gpumon hosts           # print the cached node list
gpumon deploy          # install gpumon + the node list on every cached node
```

## Keys in live view

| key | action |
|---|---|
| `j` / `k`, arrows | scroll one node |
| `space`, PgDn / PgUp | scroll one page |
| `g` / `G` | top / bottom |
| `r` | refresh now |
| `q` | quit |

Every node is probed on its own background loop, so a slow node never holds
up the others and keys work at any time. A node that fails a probe keeps
showing its last good reading; it is marked down only after failing
continuously for about 15 seconds (five refresh intervals if that is longer).
A probe that times out drops that node's multiplexed ssh connection so the
next one reconnects from scratch.

## Installing on the whole cluster

```sh
gpumon deploy
```

copies the script and the cached host list to every cached node over ssh, in
parallel. On each node it lands in `/usr/local/bin/gpumon` when that is
writable, otherwise `~/.local/bin/gpumon`. Nothing is fetched from the
internet by the nodes. After that `gpumon` works from any node, as long as
the nodes can ssh to each other without a password.

## How nodes are discovered

`gpumon discover` never asks you for a host list. It collects every hostname
the local machine already knows about:

- `Host` entries in `~/.ssh/config` (wildcards skipped, `Include` followed)
- names in `/etc/hosts` (loopback entries skipped)
- unhashed names in `~/.ssh/known_hosts`

then probes them all in parallel with non-interactive SSH (`BatchMode=yes`,
short connect timeout). A host is a cluster node if the login works without a
password and `nvidia-smi` exists there. Bare IP addresses are ignored.

The result is written to `~/.cache/gpumon/hosts` as plain text, one host per
line. Edit it by hand to add or remove nodes, or re-run `discover` to rebuild
it. A cache older than 7 days prints a reminder. Set `GPUMON_CACHE` to move the
file.

`-H host1 host2` and `-f hosts.txt` bypass the cache for one-off checks.

## Cost

Each refresh runs one `nvidia-smi` query per node over ssh, in parallel. In
the live view ssh connections are multiplexed (`ControlMaster`), so after the
first refresh there is no key exchange and no new sshd login on the nodes per
poll; the masters exit by themselves shortly after you quit. As a rough
guide, on a cluster of around 50 nodes a refresh takes about a second of wall
time and well under half a CPU-second on the machine running gpumon. The
remote query costs each node about a tenth of a second of system time. The
refresh interval is clamped to at least 1 s.

## What counts as busy

A GPU is busy when it uses more than 1 GiB of memory or more than 5%
utilisation. Tune with `--mem-threshold` and `--util-threshold`.

## Privacy

Only hostnames and per-GPU counters (utilisation, memory, temperature, ECC
count) leave the nodes. No process names, users, command lines or addresses
are collected or shown.
