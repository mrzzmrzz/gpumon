# gpumon

A minimal cluster GPU overview over SSH. One line per node, one circle per GPU.

```
  gpumon  57 nodes · 12:13:19 · 0.8s

       0 1 2 3 4 5 6 7

  001  ● ● ● ● ○ ○ ○ ○   4/8   47%    18/640 GB
  002  ○ ○ ○ ○ ○ ○ ○ ○   0/8    0%     0/640 GB
  003  ● ● ● ● ○ ● ○ ●   6/8    0%   232/640 GB
  004  ● ● ● ● ● ● ● ●   8/8   91%   487/640 GB
  005  ○ ○ ○ ○ ○ ● ○ ○   0/8    0%     0/560 GB
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
busier GPU is the brighter one, on a light terminal it is the darker one. The
background is detected automatically (OSC 11 query, then `COLORFGBG`). If
your terminal does not answer, set it once with `export GPUMON_THEME=light`
or pass `--theme light`.

Each column is a physical GPU ID, so a broken card shows up in its own slot.

## Install

One line, no clone:

```sh
curl -fsSL https://raw.githubusercontent.com/mrzzmrzz/server-monitor/main/install.sh | sh
```

This puts a single executable at `~/.local/bin/gpumon` (set `GPUMON_BIN` to
change it). Python 3.8+ with the standard library is all it needs, plus `ssh`
locally and `nvidia-smi` on the nodes.

To update, run the same line again. To remove, delete the file.

## Usage

```sh
gpumon discover        # find nodes once, cache them
gpumon                 # show the cluster
gpumon -w 5            # live view, redraws in place
gpumon --temps         # add the hottest GPU of each node
gpumon -a              # number nodes 001, 002, ... instead of hostnames
gpumon --theme light   # force the light-background palette
gpumon -p 'gpu-\d+'    # only hosts matching a regex
gpumon hosts           # print the cached node list
```

## Keys in live view

| key | action |
|---|---|
| `j` / `k`, arrows | scroll one node |
| `space`, PgDn / PgUp | scroll one page |
| `g` / `G` | top / bottom |
| `r` | refresh now |
| `q` | quit |

Probing runs on a background thread, so keys work at any time, including
while a refresh is in flight.

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

## What counts as busy

A GPU is busy when it uses more than 1 GiB of memory or more than 5%
utilisation. Tune with `--mem-threshold` and `--util-threshold`.

## Privacy

Only hostnames and per-GPU counters (utilisation, memory, temperature, ECC
count) leave the nodes. No process names, users, command lines or addresses
are collected or shown.
