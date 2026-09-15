#!/usr/bin/env python3
"""gpumon - a minimal cluster GPU overview over SSH.

One line per node, one circle per GPU:

    ● busy   ○ idle        green = healthy, red = faulty / missing / unreachable

Usage:
    gpumon              show GPU usage on cached hosts
    gpumon discover     find passwordless-SSH hosts with GPUs and cache them
    gpumon hosts        print the cached host list

Only hostnames and GPU counters are collected. No users, no processes,
no IPs, no command lines.
"""

import argparse
import concurrent.futures as cf
import glob
import os
import re
import socket
import subprocess
import sys
import time

CACHE = os.environ.get("GPUMON_CACHE") or os.path.join(
    os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "gpumon", "hosts")
STALE_DAYS = 7

QUERY = ",".join([
    "index",
    "utilization.gpu",
    "memory.used",
    "memory.total",
    "temperature.gpu",
    "ecc.errors.uncorrected.volatile.total",
])
NVSMI = f"nvidia-smi --query-gpu={QUERY} --format=csv,noheader,nounits"
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
            "-o", "LogLevel=ERROR"]

IPV4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
SKIP_NAMES = {"localhost", "localhost.localdomain", "broadcasthost"}


# ---------------------------------------------------------------- colours --

class Style:
    def __init__(self, enabled):
        self.on = enabled

    def _c(self, code, s):
        return f"\033[{code}m{s}\033[0m" if self.on else str(s)

    def green(self, s):  return self._c("32", s)
    def bgreen(self, s): return self._c("1;32", s)
    def red(self, s):    return self._c("31", s)
    def bred(self, s):   return self._c("1;31", s)
    def yellow(self, s): return self._c("33", s)
    def dim(self, s):    return self._c("2", s)
    def bold(self, s):   return self._c("1", s)


def natural_key(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def local_names():
    h = socket.gethostname()
    return {h, h.split(".")[0], "localhost"}


# ------------------------------------------------------------ candidates --
# Everything the local machine already knows about. No network access here.

def _read_lines(path):
    try:
        with open(os.path.expanduser(path)) as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if line:
                    yield line
    except OSError:
        return


def candidates_ssh_config(path="~/.ssh/config"):
    for line in _read_lines(path):
        key, _, rest = line.partition(" ")
        key = key.lower()
        if key == "include":
            for pat in rest.split():
                pat = os.path.expanduser(pat)
                if not os.path.isabs(pat):
                    pat = os.path.join(os.path.expanduser("~/.ssh"), pat)
                for inc in sorted(glob.glob(pat)):
                    yield from candidates_ssh_config(inc)
        elif key == "host":
            for name in rest.split():
                if not any(c in name for c in "*?!"):
                    yield name


def candidates_etc_hosts(path="/etc/hosts"):
    for line in _read_lines(path):
        addr, *names = line.split()
        if addr.startswith("127.") or addr == "::1":
            continue
        for name in names:
            if not name.startswith("ip6-"):
                yield name


def candidates_known_hosts(path="~/.ssh/known_hosts"):
    for line in _read_lines(path):
        if line.startswith("@"):
            line = line.split(None, 1)[1]
        field = line.split()[0]
        if field.startswith("|"):          # hashed entry, name not recoverable
            continue
        for name in field.split(","):
            m = re.match(r"^\[(.+)\]:\d+$", name)
            yield m.group(1) if m else name


def gather_candidates(pattern):
    rx = re.compile(pattern) if pattern else None
    seen = []
    for name in (*candidates_ssh_config(), *candidates_etc_hosts(), *candidates_known_hosts()):
        if name in seen or name in SKIP_NAMES or IPV4.match(name) or ":" in name:
            continue
        if rx and not rx.fullmatch(name):
            continue
        seen.append(name)
    return sorted(seen, key=natural_key)


# ------------------------------------------------------------------ cache --

def read_cache(path=CACHE):
    hosts, stamp = [], None
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("# discovered:"):
                    stamp = line.split(":", 1)[1].strip()
                elif line and not line.startswith("#"):
                    hosts.append(line)
    except OSError:
        pass
    return hosts, stamp


def write_cache(hosts, path=CACHE):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("# gpumon host cache - one host per line, edit freely\n")
        f.write("# rebuild with: gpumon discover\n")
        f.write(f"# discovered: {time.strftime('%Y-%m-%d %H:%M')}\n")
        for h in hosts:
            f.write(h + "\n")


def cache_age_days(stamp):
    try:
        t = time.mktime(time.strptime(stamp, "%Y-%m-%d %H:%M"))
        return (time.time() - t) / 86400
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- probing --

def run_on(host, command, timeout):
    if host in local_names():
        cmd = ["sh", "-c", command]
    else:
        cmd = ["ssh", *SSH_OPTS, "-o", f"ConnectTimeout={timeout}", host, command]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
    except subprocess.TimeoutExpired:
        return None


def probe_membership(host, timeout):
    """Return 'gpu', 'nogpu' or 'unreachable'."""
    r = run_on(host, "command -v nvidia-smi >/dev/null && echo gpu || echo nogpu", timeout)
    if r is None or r.returncode == 255:
        return host, "unreachable"
    if r.returncode != 0:
        return host, "unreachable"
    return host, r.stdout.strip() or "nogpu"


def probe_gpus(host, timeout):
    """Return (host, gpus, error). gpus maps GPU index -> dict."""
    r = run_on(host, NVSMI, timeout)
    if r is None:
        return host, {}, "timeout"
    if r.returncode == 255:
        return host, {}, "unreachable"

    # a wedged GPU is reported on stderr, e.g.
    #   Unable to determine the device handle for GPU5: 0000:BC:00.0: Unknown Error
    gpus = {}
    for m in re.finditer(r"GPU(\d+):\s*(?:[0-9A-Fa-f:.]+:\s*)?(.+)$", r.stderr, re.M):
        gpus[int(m.group(1))] = {"fault": m.group(2).strip().lower(), "util": 0, "used": 0, "total": 0, "temp": None}

    for line in r.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6 or not parts[0].isdigit():
            continue
        idx, util, used, total, temp, ecc = parts[:6]
        g = {"fault": None, "temp": None}
        try:
            g["util"], g["used"], g["total"] = int(util), int(used), int(total)
        except ValueError:                        # "[N/A]" / "ERR!" from a sick GPU
            g.update(util=0, used=0, total=0, fault="no telemetry")
        if temp.isdigit():
            g["temp"] = int(temp)
        if ecc.isdigit() and int(ecc) > 0:
            g["fault"] = f"ecc x{ecc}"
        gpus[int(idx)] = g

    if not gpus:
        err = (r.stderr or r.stdout).strip().splitlines()
        return host, {}, (err[-1] if err else f"exit {r.returncode}")[:60]
    return host, gpus, None


def parallel(fn, hosts, args):
    with cf.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = [ex.submit(fn, h, args.timeout) for h in hosts]
        out = {r[0]: r for r in (f.result() for f in cf.as_completed(futs))}
    return [out[h] for h in hosts]


# --------------------------------------------------------------- rendering --

def gb(mib):
    return mib / 1024.0


def render(results, st, args, elapsed):
    lines = []
    busy = free = bad = missing = down = 0
    names = [f"{i:03d}" for i in range(1, len(results) + 1)] if args.anon else [h for h, _, _ in results]
    width = max((len(n) for n in names), default=4)
    slots = args.gpus or max((max(g) + 1 for _, g, e in results if not e), default=8)

    for name, (host, gpus, err) in zip(names, results):
        label = st.bold(name.ljust(width))
        if err:
            down += 1
            lines.append(f"  {label}  {st.bred('✕')}  {st.red(err)}")
            continue

        dots, notes = [], []
        n_busy = used_sum = tot_sum = util_sum = n_ok = 0
        for i in range(slots):
            g = gpus.get(i)
            if g is None:
                missing += 1
                dots.append(st.bred("✕"))
                notes.append(st.red(f"gpu{i}: missing"))
            elif g["fault"]:
                bad += 1
                dots.append(st.bred("●"))
                notes.append(st.red(f"gpu{i}: {g['fault']}"))
            else:
                n_ok += 1
                used_sum += g["used"]; tot_sum += g["total"]; util_sum += g["util"]
                if g["used"] >= args.mem_threshold or g["util"] >= args.util_threshold:
                    busy += 1; n_busy += 1
                    dots.append(st.bgreen("●"))
                else:
                    free += 1
                    dots.append(st.green("○"))

        detail = st.dim(f"{util_sum // n_ok:3d}%  {gb(used_sum):4.0f}/{gb(tot_sum):.0f} GB") if n_ok else ""
        if args.temps:
            temps = [g["temp"] for g in gpus.values() if g["temp"] is not None]
            if temps:
                hi = max(temps)
                detail += "  " + (st.yellow(f"{hi}°C") if hi >= args.hot else st.dim(f"{hi}°C"))
        if notes:
            detail += "  " + "  ".join(notes)
        lines.append(f"  {label}  {' '.join(dots)}   {n_busy}/{slots}  {detail}")

    out = []
    stamp = time.strftime("%H:%M:%S")
    out += ["", f"  {st.bold('gpumon')}  {st.dim(f'{len(results)} nodes · {stamp} · {elapsed:.1f}s')}", ""]
    out += ["  " + " " * width + "  " + st.dim(" ".join(str(i % 10) for i in range(slots))), ""]
    out += lines
    summary = f"  {st.bgreen('●')} {busy} busy   {st.green('○')} {free} free"
    if bad:
        summary += f"   {st.bred('●')} {bad} faulty"
    if missing:
        summary += f"   {st.bred('✕')} {missing} missing"
    if down:
        summary += f"   {st.bred('✕')} {down} nodes down"
    out += ["", summary, ""]
    if args.watch and st.on:
        # redraw in place: home, overwrite each line, wipe whatever is left below
        return "\033[H" + "\033[K\n".join(out) + "\033[K\033[J"
    return "\n".join(out)


# ------------------------------------------------------------------ screen --

class Screen:
    """Alternate screen buffer with hidden cursor, restored on exit."""

    def __init__(self, enabled):
        self.on = enabled

    def __enter__(self):
        if self.on:
            sys.stdout.write("\033[?1049h\033[?25l\033[H\033[2J")
            sys.stdout.flush()
        return self

    def __exit__(self, *exc):
        if self.on:
            sys.stdout.write("\033[?25h\033[?1049l")
            sys.stdout.flush()


def wait_or_quit(seconds):
    """Sleep for `seconds`; return True early if the user presses q."""
    if not sys.stdin.isatty():
        time.sleep(seconds)
        return False
    import select
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        deadline = time.time() + seconds
        while True:
            left = deadline - time.time()
            if left <= 0:
                return False
            r, _, _ = select.select([sys.stdin], [], [], left)
            if r and sys.stdin.read(1) in ("q", "Q", "\x03"):
                return True
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


# ------------------------------------------------------------------- main --

def parse_args():
    p = argparse.ArgumentParser(prog="gpumon", description=__doc__.split("\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog=__doc__.split("Usage:")[1])
    p.add_argument("command", nargs="?", choices=["show", "discover", "hosts"], default="show")
    src = p.add_argument_group("hosts")
    src.add_argument("-H", "--hosts", nargs="+", metavar="HOST", help="use these hosts, ignore the cache")
    src.add_argument("-f", "--hosts-file", metavar="FILE", help="use hosts from FILE, ignore the cache")
    src.add_argument("-p", "--pattern", metavar="REGEX",
                     help="only consider hostnames matching REGEX (e.g. 'node\\d+')")
    p.add_argument("-w", "--watch", type=float, metavar="SEC", help="refresh every SEC seconds in place (q to quit)")
    p.add_argument("-t", "--timeout", type=int, default=5, help="ssh connect timeout per host (s)")
    p.add_argument("-j", "--jobs", type=int, default=32, help="parallel ssh connections")
    p.add_argument("--gpus", type=int, default=8, metavar="N", help="GPU slots per node (default 8)")
    p.add_argument("--mem-threshold", type=int, default=1024, metavar="MiB",
                   help="memory used above which a GPU is busy (default 1024)")
    p.add_argument("--util-threshold", type=int, default=5, metavar="PCT",
                   help="utilisation above which a GPU is busy (default 5)")
    p.add_argument("--temps", action="store_true", help="show hottest GPU per node")
    p.add_argument("--hot", type=int, default=80, help="temperature to highlight (°C)")
    p.add_argument("-a", "--anon", action="store_true",
                   help="number nodes 001, 002, ... instead of showing hostnames")
    p.add_argument("--no-color", action="store_true")
    return p.parse_args()


def discover(args, st):
    cands = gather_candidates(args.pattern)
    if not cands:
        sys.exit("gpumon: no candidate hosts in ~/.ssh/config, /etc/hosts or ~/.ssh/known_hosts")
    print(f"\n  {st.bold('gpumon discover')}  {st.dim(f'probing {len(cands)} candidates')}", flush=True)
    t0 = time.time()
    results = parallel(probe_membership, cands, args)
    good = [h for h, s in results if s == "gpu"]
    nogpu = [h for h, s in results if s == "nogpu"]
    dead = [h for h, s in results if s == "unreachable"]
    write_cache(good)
    print()
    for h, s in results:
        mark = {"gpu": st.bgreen("●"), "nogpu": st.dim("○"), "unreachable": st.red("✕")}[s]
        print(f"  {mark} {h}" + (st.dim(f"  {s}") if s != "gpu" else ""))
    print(f"\n  {st.bgreen('●')} {len(good)} gpu nodes   {st.dim('○')} {len(nogpu)} without gpu   "
          f"{st.red('✕')} {len(dead)} unreachable   {st.dim(f'{time.time() - t0:.1f}s')}")
    print(f"  {st.dim('cached to')} {CACHE}\n")
    return good


def resolve_hosts(args, st):
    if args.hosts:
        hosts = args.hosts
    elif args.hosts_file:
        hosts = [l for l in _read_lines(args.hosts_file)]
    else:
        hosts, stamp = read_cache()
        if not hosts:
            hosts = discover(args, st)
        else:
            age = cache_age_days(stamp)
            if age is not None and age > STALE_DAYS:
                print(st.dim(f"  host cache is {age:.0f} days old - run `gpumon discover` to refresh"))
    if args.pattern:
        rx = re.compile(args.pattern)
        hosts = [h for h in hosts if rx.fullmatch(h)]
    if not hosts:
        sys.exit("gpumon: no hosts (run `gpumon discover`, or pass -H / -f)")
    return sorted(dict.fromkeys(hosts), key=natural_key)


def main():
    import signal
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    args = parse_args()
    st = Style(sys.stdout.isatty() and not args.no_color)
    try:
        if args.command == "discover":
            discover(args, st)
            return
        hosts = resolve_hosts(args, st)
        if args.command == "hosts":
            print("\n".join(hosts))
            return
        if not args.watch:
            t0 = time.time()
            results = parallel(probe_gpus, hosts, args)
            print(render(results, st, args, time.time() - t0), flush=True)
            return
        with Screen(st.on):
            while True:
                t0 = time.time()
                results = parallel(probe_gpus, hosts, args)
                print(render(results, st, args, time.time() - t0), end="" if st.on else "\n", flush=True)
                if wait_or_quit(args.watch):
                    break
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
