#!/usr/bin/env python3
"""
HW6 - Live Migration Dashboard
Advanced Cloud Computing 2026

Dependencies (pip)
------------------
  Python 3.9+ (stdlib only besides rich)

  pip3 install rich

  Rocky Linux 10 (PEP 668 / system Python):

    pip3 install rich --break-system-packages

  setup_main.sh installs this automatically on Host-A.

Prerequisites
-------------
  - Run on Host-A (servera) as root or a user that can virsh + ssh to B/C
  - /etc/hw6/cluster.conf exists (from setup_main.sh)
  - Passwordless SSH: root@servera / serverb / serverc

Features
--------
  - Real-time KVM host monitoring (allocated vs actual host CPU/MEM)
  - Migration downtime from virsh domjobinfo
  - 2D Bin Packing (FFD algorithm) placement planning
  - virsh live migration orchestration
  - rich-based TUI (terminal UI)

Usage
-----
  python3 migration_dashboard.py   (Linux only — Host-A Rocky)

Keys: [r] Bin Packing  [c] Consolidate (C Idle)  [l] Load balance  [m] Migrate  [q] Quit

Platform
--------
  Not supported on native Windows (needs termios/tty + virsh over SSH).
  Run on Host-A, or from Windows: WSL / ssh root@servera 'cd ACC && python3 migration_dashboard.py'
"""

import subprocess
import threading
import time
import re
import sys
import select

if sys.platform == "win32":
    print(
        "migration_dashboard.py must run on Linux (Host-A Rocky).\n"
        "  Windows Python lacks the 'termios' module (Unix TUI keyboard input).\n"
        "\n"
        "  Run on servera after setup_main.sh:\n"
        "    python3 migration_dashboard.py\n"
        "\n"
        "  From this PC, use SSH instead:\n"
        "    ssh root@<Host-A-IP>\n"
        "    cd /path/to/ACC && python3 migration_dashboard.py\n"
    )
    sys.exit(1)

import tty
import termios
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.align import Align
from rich.console import Console
from rich import box

# ══════════════════════════════════════════════════════════════════════════════
#  Configuration — loaded from /etc/hw6/cluster.conf (written by setup_*.sh)
# ══════════════════════════════════════════════════════════════════════════════

HW6_CONFIG_PATHS = (
    Path("/etc/hw6/cluster.conf"),
    Path(__file__).resolve().parent / "cluster.conf",
)

HOSTS_CONFIG: Dict[str, dict] = {}

REFRESH_INTERVAL = 1.5   # seconds (poll interval)
LOG_MAX_LINES    = 6     # max log lines displayed

HOST_COLORS = {"Host-A": "blue", "Host-B": "magenta", "Host-C": "green"}
HOST_KEYS   = {"Host-A": "a",   "Host-B": "b",        "Host-C": "c"}

# ══════════════════════════════════════════════════════════════════════════════
#  Data classes
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class VMInfo:
    name:    str
    host:    str
    cpu:     int        # allocated vCPUs
    mem_mb:  int        # allocated memory (MB)
    state:   str        # running / shut off / paused

@dataclass
class MigrationJob:
    vm_name:        str
    src_host:       str
    dst_host:       str
    data_total_b:   int   = 0    # total data (bytes)
    data_proc_b:    int   = 0    # transferred data (bytes)
    dirty_rate:     int   = 0    # pages/s
    elapsed:        float = 0.0
    downtime_ms:    Optional[int] = None
    completed:      bool  = False
    failed:         bool  = False
    error_msg:      str   = ""

@dataclass
class HostState:
    name:       str
    ip:         str
    cpu_cap:    int
    mem_cap_mb: int
    vms:        List[VMInfo] = field(default_factory=list)
    reachable:  bool = True
    # Actual host utilization (SSH /proc)
    cpu_host_pct: float = 0.0
    mem_host_pct: float = 0.0
    mem_used_mb:  int   = 0
    mem_total_mb: int   = 0

# ══════════════════════════════════════════════════════════════════════════════
#  Global state
# ══════════════════════════════════════════════════════════════════════════════

_lock            = threading.Lock()
hosts:           Dict[str, HostState] = {}
active_mig:      Optional[MigrationJob] = None
mig_history:     List[MigrationJob] = []
log_lines:       List[Tuple[str, str, str]] = []   # (time, level, msg)
bin_pack_plan:   Dict[str, str] = {}               # vm_name → target_host
running:         bool = True

# ══════════════════════════════════════════════════════════════════════════════
#  virsh helpers
# ══════════════════════════════════════════════════════════════════════════════

def _virsh(host_ip: str, *args, timeout: int = 10) -> str:
    """Run remote virsh command. Returns empty string on failure."""
    cmd = ["virsh", "-c", f"qemu+ssh://root@{host_ip}/system"] + list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def _parse_kv(output: str) -> Dict[str, str]:
    """Parse 'Key:  Value' format."""
    result = {}
    for line in output.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            result[k.strip()] = v.strip()
    return result


def _parse_bytes(s: str) -> int:
    """Convert strings like '1234 MiB' to bytes."""
    m = re.search(r"([\d.]+)\s*(bytes?|KiB|MiB|GiB)?", s, re.IGNORECASE)
    if not m:
        return 0
    val  = float(m.group(1))
    unit = (m.group(2) or "bytes").lower()
    return int(val * {"bytes": 1, "byte": 1, "kib": 1024,
                      "mib": 1024**2, "gib": 1024**3}.get(unit, 1))


def fetch_host_vms(host_name: str, host_ip: str) -> Tuple[List[VMInfo], bool]:
    """Fetch VM list and details from a host."""
    raw = _virsh(host_ip, "list", "--all", "--name")
    if raw == "" and not _virsh(host_ip, "version"):
        return [], False   # unreachable

    vms = []
    for name in [n.strip() for n in raw.splitlines() if n.strip()]:
        info = _parse_kv(_virsh(host_ip, "dominfo", name))
        if not info:
            continue

        cpu_str = info.get("CPU(s)", "0")
        cpu = int(re.search(r"\d+", cpu_str).group()) if re.search(r"\d+", cpu_str) else 0

        mem_str = info.get("Max memory", "0 KiB")
        mem_m   = re.search(r"[\d,]+", mem_str.replace(",", ""))
        mem_kb  = int(mem_m.group()) if mem_m else 0
        mem_mb  = mem_kb // 1024

        state = info.get("State", "unknown").lower()

        vms.append(VMInfo(name=name, host=host_name,
                          cpu=cpu, mem_mb=mem_mb, state=state))
    return vms, True


def fetch_domjobinfo(host_ip: str, vm_name: str,
                     completed: bool = False) -> Optional[Dict[str, str]]:
    """Query migration job info (active or --completed)."""
    args = ("domjobinfo", "--completed", vm_name) if completed else ("domjobinfo", vm_name)
    raw = _virsh(host_ip, *args, timeout=5)
    if not raw or "No job" in raw or "no job" in raw.lower():
        return None
    info = _parse_kv(raw)
    if info.get("Job type", "None") in ("None", ""):
        return None
    return info


def _parse_ms_field(s: str) -> Optional[int]:
    """Parse virsh time fields like '42 ms' or '1,234 ms'."""
    m = re.search(r"([\d,]+)\s*ms", s or "", re.IGNORECASE)
    return int(m.group(1).replace(",", "")) if m else None


def _ssh_script(host_ip: str, script: str, timeout: int = 10) -> str:
    """Run a bash script on the host via SSH (same trust as virsh)."""
    cmd = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
        f"root@{host_ip}", "bash", "-s",
    ]
    try:
        r = subprocess.run(
            cmd, input=script, capture_output=True, text=True, timeout=timeout
        )
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


_HOST_UTIL_SCRIPT = r"""
read -r _ u1 n1 s1 i1 iw1 irq1 sirq1 st1 idle1 rest </proc/stat
t1=$((u1+n1+s1+i1+iw1+irq1+sirq1+st1+idle1+rest))
i1t=$((i1+idle1+rest))
sleep 0.35
read -r _ u2 n2 s2 i2 iw2 irq2 sirq2 st2 idle2 rest </proc/stat
t2=$((u2+n2+s2+i2+iw2+irq2+sirq2+st2+idle2+rest))
i2t=$((i2+idle2+rest))
dt=$((t2-t1)); di=$((i2t-i1t))
cpu=0
if [ "$dt" -gt 0 ]; then cpu=$(( (100 * (dt - di)) / dt )); fi
mt=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
ma=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
echo "CPU_PCT=${cpu}"
echo "MEM_TOTAL_KB=${mt}"
echo "MEM_AVAIL_KB=${ma}"
"""


def fetch_host_utilization(host_ip: str) -> Tuple[float, float, int, int]:
    """
    Returns (cpu_pct, mem_pct, mem_used_mb, mem_total_mb).
    cpu_pct is approximate host-wide utilization from /proc/stat delta.
    """
    raw = _ssh_script(host_ip, _HOST_UTIL_SCRIPT, timeout=12)
    if not raw:
        return 0.0, 0.0, 0, 0

    vals: Dict[str, str] = {}
    for line in raw.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            vals[k.strip()] = v.strip()

    try:
        cpu_pct = float(vals.get("CPU_PCT", "0"))
        mt_kb = int(vals.get("MEM_TOTAL_KB", "0"))
        ma_kb = int(vals.get("MEM_AVAIL_KB", "0"))
    except ValueError:
        return 0.0, 0.0, 0, 0

    if mt_kb <= 0:
        return cpu_pct, 0.0, 0, 0

    used_kb = max(0, mt_kb - ma_kb)
    mem_pct = used_kb * 100.0 / mt_kb
    return (
        max(0.0, min(100.0, cpu_pct)),
        max(0.0, min(100.0, mem_pct)),
        used_kb // 1024,
        mt_kb // 1024,
    )


def _apply_domjob_to_mig(mig: MigrationJob, job: Dict[str, str]) -> None:
    """Update migration job fields from virsh domjobinfo output."""
    mig.data_total_b = _parse_bytes(job.get("Data total", "0"))
    mig.data_proc_b  = _parse_bytes(job.get("Data processed", "0"))
    dirty_m = re.search(
        r"[\d,]+", job.get("Dirty rate", "0").replace(",", "")
    )
    mig.dirty_rate = int(dirty_m.group()) if dirty_m else 0

    dt = _parse_ms_field(job.get("Downtime", ""))
    if dt is not None:
        mig.downtime_ms = dt
    total_ms = _parse_ms_field(job.get("Total time", ""))
    if total_ms is not None and total_ms > 0:
        mig.elapsed = total_ms / 1000.0

# ══════════════════════════════════════════════════════════════════════════════
#  Logging
# ══════════════════════════════════════════════════════════════════════════════

def log(level: str, msg: str):
    t = time.strftime("%H:%M:%S")
    with _lock:
        log_lines.append((t, level, msg))
        if len(log_lines) > LOG_MAX_LINES + 10:
            del log_lines[:-LOG_MAX_LINES]

# ══════════════════════════════════════════════════════════════════════════════
#  Background update thread
# ══════════════════════════════════════════════════════════════════════════════

def _finalize_migration(mig: MigrationJob, src_ip: str) -> bool:
    """Pull Downtime / Total time from domjobinfo --completed. Returns True if newly finalized."""
    with _lock:
        if mig.completed:
            return False

    job = fetch_domjobinfo(src_ip, mig.vm_name, completed=True)
    if job:
        _apply_domjob_to_mig(mig, job)
    if mig.elapsed <= 0:
        mig.elapsed = time.time() - _mig_start_time

    with _lock:
        if mig.completed:
            return False
        mig.completed = True

    dt_s = f"  downtime={mig.downtime_ms}ms" if mig.downtime_ms is not None else ""
    log("OK", f"{mig.vm_name} migration complete ({mig.elapsed:.1f}s{dt_s})")
    return True


def _update_loop():
    global active_mig  # noqa: PLW0603
    while running:
        for h_name, h_conf in HOSTS_CONFIG.items():
            ip = h_conf["ip"]
            vms, ok = fetch_host_vms(h_name, ip)
            cpu_h, mem_h, mu, mt = (0.0, 0.0, 0, 0)
            if ok:
                cpu_h, mem_h, mu, mt = fetch_host_utilization(ip)

            with _lock:
                if h_name in hosts:
                    hosts[h_name].vms = vms
                    hosts[h_name].reachable = ok
                    if ok:
                        hosts[h_name].cpu_host_pct = cpu_h
                        hosts[h_name].mem_host_pct = mem_h
                        hosts[h_name].mem_used_mb  = mu
                        hosts[h_name].mem_total_mb = mt

        with _lock:
            mig = active_mig

        if mig and not mig.completed and not mig.failed:
            src_ip = HOSTS_CONFIG[mig.src_host]["ip"]
            job = fetch_domjobinfo(src_ip, mig.vm_name)
            with _lock:
                if job and active_mig:
                    _apply_domjob_to_mig(active_mig, job)
                elif active_mig and not active_mig.completed:
                    if _finalize_migration(active_mig, src_ip):
                        with _lock:
                            mig_history.append(active_mig)
                            active_mig = None

        time.sleep(REFRESH_INTERVAL)

_mig_start_time: float = 0.0

# ══════════════════════════════════════════════════════════════════════════════
#  2D Bin Packing (FFD)
# ══════════════════════════════════════════════════════════════════════════════

def run_bin_packing(consolidate: bool = False):
    """
    2D First Fit Decreasing Bin Packing.
    consolidate=True  → minimize active hosts (Case 1: Host-C Idle)
                        pack into A→B first; use C only when needed.
    consolidate=False → reduce resource fragmentation (Case 2)
                        place each VM on the host with the most
                        remaining capacity (Best Fit Decreasing).
    """
    global bin_pack_plan

    with _lock:
        all_vms = [v for hs in hosts.values() for v in hs.vms
                   if v.state == "running"]

    if not all_vms:
        log("WRN", "No running VMs")
        return

    mode_str = "Consolidate (C-Idle)" if consolidate else "Defrag (spread)"
    log("BPK", f"FFD Bin Packing [{mode_str}] ({len(all_vms)} VMs)")

    # Sort by CPU + MEM descending (largest VMs first)
    sorted_vms = sorted(all_vms,
                        key=lambda v: v.cpu + v.mem_mb / 1024,
                        reverse=True)

    all_hosts  = list(HOSTS_CONFIG.keys())
    usage      = {h: {"cpu": 0, "mem": 0} for h in all_hosts}
    placement: Dict[str, str] = {}

    if consolidate:
        # ── Case 1: Consolidation ──────────────────────────────────────────
        # Fill A → B; use C only as last resort.
        # Goal "Host-C Idle": success if nothing placed on C.
        host_order = ["Host-A", "Host-B", "Host-C"]

        for vm in sorted_vms:
            for host in host_order:
                cap = HOSTS_CONFIG[host]
                if (usage[host]["cpu"] + vm.cpu <= cap["cpu_cap"] and
                        usage[host]["mem"] + vm.mem_mb <= cap["mem_cap_mb"]):
                    placement[vm.name] = host
                    usage[host]["cpu"] += vm.cpu
                    usage[host]["mem"] += vm.mem_mb
                    break
            else:
                placement[vm.name] = vm.host  # cannot fit → keep current host

    else:
        # ── Case 2: Defragmentation (Best Fit Decreasing) ──────────────────
        # Pick host with most remaining capacity per VM.
        # "Slack" = sum of CPU and MEM free ratios.
        for vm in sorted_vms:
            def free_score(h: str) -> float:
                cap = HOSTS_CONFIG[h]
                cpu_free = (cap["cpu_cap"]    - usage[h]["cpu"]) / cap["cpu_cap"]
                mem_free = (cap["mem_cap_mb"] - usage[h]["mem"]) / cap["mem_cap_mb"]
                return cpu_free + mem_free

            for host in sorted(all_hosts, key=free_score, reverse=True):
                cap = HOSTS_CONFIG[host]
                if (usage[host]["cpu"] + vm.cpu <= cap["cpu_cap"] and
                        usage[host]["mem"] + vm.mem_mb <= cap["mem_cap_mb"]):
                    placement[vm.name] = host
                    usage[host]["cpu"] += vm.cpu
                    usage[host]["mem"] += vm.mem_mb
                    break
            else:
                placement[vm.name] = vm.host  # cannot fit → keep current host

    with _lock:
        bin_pack_plan = placement

    # Summarize VMs that need to move
    with _lock:
        current = {v.name: v.host for hs in hosts.values() for v in hs.vms}

    moves = [f"{vm} {current.get(vm, '?')}→{tgt}"
             for vm, tgt in placement.items()
             if current.get(vm) != tgt]

    if consolidate:
        c_vms = [vm for vm, tgt in placement.items() if tgt == "Host-C"]
        if not c_vms:
            log("BPK", "✓ Host-C Idle achieved")
        else:
            log("BPK", f"VMs still on Host-C: {', '.join(c_vms)} (capacity limit)")

    if moves:
        log("BPK", "Placement plan: " + ",  ".join(moves))
    else:
        log("BPK", "No moves needed — already optimal")


def run_load_balance():
    """
    Case 3: detect overloaded hosts → spread load evenly
    Move VMs from hosts with CPU usage > 80% to hosts with spare capacity.
    """
    global bin_pack_plan

    with _lock:
        snapshot = {
            n: (list(hs.vms), hs.cpu_cap, hs.mem_cap_mb, hs.cpu_host_pct)
            for n, hs in hosts.items()
        }

    overloaded = []
    for h_name, (vms, cpu_cap, _, cpu_host_pct) in snapshot.items():
        if cpu_host_pct > 0:
            cpu_load = cpu_host_pct / 100.0
        else:
            used_cpu = sum(v.cpu for v in vms if v.state == "running")
            cpu_load = used_cpu / cpu_cap if cpu_cap else 0
        if cpu_load >= 0.80:
            overloaded.append(h_name)

    if not overloaded:
        log("WRN", "No overloaded hosts (CPU < 80%)")
        return

    log("BPK", f"Overloaded hosts: {', '.join(overloaded)}")
    run_bin_packing(consolidate=False)

# ══════════════════════════════════════════════════════════════════════════════
#  Migration execution
# ══════════════════════════════════════════════════════════════════════════════

def _do_migrate(vm_name: str, src_host: str, dst_host: str):
    global active_mig, _mig_start_time

    src_ip = HOSTS_CONFIG[src_host]["ip"]
    dst_ip = HOSTS_CONFIG[dst_host]["ip"]
    dst_fqdn = HOSTS_CONFIG[dst_host]["fqdn"]
    mig_uri = f"tcp://{dst_ip}:0"

    with _lock:
        active_mig    = MigrationJob(vm_name=vm_name,
                                     src_host=src_host,
                                     dst_host=dst_host)
        _mig_start_time = time.time()

    log("MIG", f"{vm_name}: {src_host} → {dst_host} migration started")

    # --unsafe: NFS shared disk; --migrateuri: data channel to cluster IP (fixes C-only I/O errors)
    base_cmd = [
        "virsh", "-c", f"qemu+ssh://root@{src_ip}/system",
        "migrate", "--live", "--persistent", "--undefinesource", "--unsafe",
        "--migrateuri", mig_uri,
        vm_name, f"qemu+ssh://root@{dst_fqdn}/system",
    ]
    result = subprocess.run(base_cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        log("MIG", f"{vm_name}: retry with --tunnelled")
        result = subprocess.run(
            base_cmd[:-2] + ["--tunnelled", vm_name, base_cmd[-1]],
            capture_output=True, text=True, timeout=600,
        )

    with _lock:
        mig = active_mig

    if result.returncode == 0 and mig:
        if _finalize_migration(mig, src_ip):
            with _lock:
                mig_history.append(mig)
                active_mig = None
        else:
            with _lock:
                if active_mig and active_mig.completed:
                    mig_history.append(active_mig)
                    active_mig = None
    else:
        with _lock:
            if active_mig:
                active_mig.failed = True
                active_mig.error_msg = result.stderr.strip()[:80]
                active_mig.elapsed = time.time() - _mig_start_time
                log("ERR", f"{vm_name} failed: {active_mig.error_msg}")
                mig_history.append(active_mig)
                active_mig = None


def execute_migrations():
    """Run migrations sequentially from bin_pack_plan."""
    if not bin_pack_plan:
        log("WRN", "Run Bin Packing first → [r] or [c]")
        return

    with _lock:
        plan    = dict(bin_pack_plan)
        current = {v.name: v.host
                   for hs in hosts.values() for v in hs.vms}

    migrations = [(vm, current[vm], tgt)
                  for vm, tgt in plan.items()
                  if vm in current and current[vm] != tgt]

    if not migrations:
        log("INF", "No VMs to move — already optimal placement")
        return

    log("MIG", f"Starting migration for {len(migrations)} VM(s)")

    def _run_all():
        for vm_name, src, dst in migrations:
            _do_migrate(vm_name, src, dst)
            time.sleep(1.0)  # gap between consecutive migrations

    threading.Thread(target=_run_all, daemon=True).start()

# ══════════════════════════════════════════════════════════════════════════════
#  TUI rendering
# ══════════════════════════════════════════════════════════════════════════════

console = Console()


def _make_bar(pct: float, width: int = 14, color: str = "blue") -> Text:
    """Build a text progress bar."""
    pct   = max(0.0, min(100.0, pct))
    fill  = int(pct / 100 * width)
    c     = "red" if pct >= 90 else ("yellow" if pct >= 70 else color)
    t = Text()
    t.append("█" * fill,           style=f"bold {c}")
    t.append("░" * (width - fill), style="dim")
    return t


def _render_host_panel(hs: HostState) -> Panel:
    color = HOST_COLORS.get(hs.name, "white")

    alloc_cpu = sum(v.cpu    for v in hs.vms if v.state == "running")
    alloc_mem = sum(v.mem_mb for v in hs.vms if v.state == "running")
    cpu_alloc_pct = alloc_cpu / hs.cpu_cap * 100 if hs.cpu_cap else 0
    mem_alloc_pct = alloc_mem / hs.mem_cap_mb * 100 if hs.mem_cap_mb else 0

    cpu_host = hs.cpu_host_pct if hs.reachable else 0.0
    mem_host = hs.mem_host_pct if hs.reachable else 0.0
    has_host = hs.reachable and (cpu_host > 0 or mem_host > 0 or hs.mem_total_mb > 0)

    grid = Table.grid(padding=(0, 1))
    grid.add_column(width=5)
    grid.add_column(width=13)
    grid.add_column(width=11, justify="right")

    def pct_style(pct: float, base: str) -> str:
        return f"bold {'red' if pct >= 90 else ('yellow' if pct >= 70 else base)}"

    if has_host:
        grid.add_row(
            Text("CPU▲", style="bold white"),
            _make_bar(cpu_host, 12, color),
            Text(f"{cpu_host:.0f}% host", style=pct_style(cpu_host, color)),
        )
        grid.add_row(
            Text("CPU◇", style="dim"),
            _make_bar(cpu_alloc_pct, 12, "dim"),
            Text(f"{cpu_alloc_pct:.0f}% alloc", style="dim"),
        )
        mem_label = f"{hs.mem_used_mb}/{hs.mem_total_mb}M"
        grid.add_row(
            Text("MEM▲", style="bold white"),
            _make_bar(mem_host, 12, color),
            Text(f"{mem_host:.0f}% {mem_label}", style=pct_style(mem_host, color)),
        )
        grid.add_row(
            Text("MEM◇", style="dim"),
            _make_bar(mem_alloc_pct, 12, "dim"),
            Text(f"{mem_alloc_pct:.0f}% alloc", style="dim"),
        )
    else:
        grid.add_row(
            Text("CPU", style="dim"),
            _make_bar(cpu_alloc_pct, 12, color),
            Text(f"{cpu_alloc_pct:.0f}% alloc", style=pct_style(cpu_alloc_pct, color)),
        )
        grid.add_row(
            Text("MEM", style="dim"),
            _make_bar(mem_alloc_pct, 12, color),
            Text(f"{mem_alloc_pct:.0f}% alloc", style=pct_style(mem_alloc_pct, color)),
        )

    grid.add_row(Text(""), Text(""), Text(""))

    with _lock:
        mig = active_mig

    if not hs.vms:
        grid.add_row(
            Text(""),
            Text("[ IDLE ]", style="dim italic"),
            Text("")
        )
    else:
        for vm in hs.vms:
            is_src = mig and mig.vm_name == vm.name and mig.src_host == hs.name
            is_dst = mig and mig.vm_name == vm.name and mig.dst_host == hs.name

            if is_src:
                icon, ic = "⇢", "cyan"
                label = "migrating"
            elif is_dst:
                icon, ic = "⇣", "bright_green"
                label = "arriving "
            elif vm.state == "running":
                icon, ic = "●", "green"
                label = "running  "
            else:
                icon, ic = "○", "dim"
                label = vm.state[:9]

            grid.add_row(
                Text(icon, style=ic),
                Text(f"{vm.name}  {vm.cpu}c/{vm.mem_mb // 1024}G",
                     style="white"),
                Text(label, style=f"dim {ic}")
            )

    border = "red" if not hs.reachable else color
    return Panel(
        grid,
        title=f"[bold {color}]{hs.name}[/] [dim]{hs.ip}[/dim]",
        border_style=border,
        box=box.ROUNDED,
    )


def _render_status_panel() -> Panel:
    with _lock:
        mig  = active_mig
        plan = dict(bin_pack_plan)

    # ── Migration in progress ────────────────────────────────────────────────
    if mig:
        pct = (mig.data_proc_b / mig.data_total_b * 100
               if mig.data_total_b > 0 else 0)
        mb_proc  = mig.data_proc_b  / 1024 / 1024
        mb_total = mig.data_total_b / 1024 / 1024

        bar_w = 38
        fill  = int(pct / 100 * bar_w)
        bar   = Text()
        bar.append("█" * fill,          style="bold blue")
        bar.append("░" * (bar_w - fill), style="dim")
        bar.append(f"  {pct:.0f}%",     style="bold cyan")

        arrow = "─" * 16
        t = Table.grid(padding=(0, 1))
        t.add_column(width=12)
        t.add_column()
        t.add_row(
            Text("MIGRATING", style="bold cyan"),
            Text(f"{mig.vm_name}  "
                 f"{mig.src_host} {arrow}► {mig.dst_host}",
                 style="bold white")
        )
        t.add_row(Text(""), Text(""))
        t.add_row(Text("Progress", style="dim"), bar)
        t.add_row(
            Text(""),
            Text(f"{mb_proc:.1f} MB / {mb_total:.1f} MB transferred",
                 style="dim")
        )
        t.add_row(Text(""), Text(""))

        dirty_c = ("red" if mig.dirty_rate > 2000
                   else "yellow" if mig.dirty_rate > 500 else "green")
        stats = Text()
        stats.append("Dirty rate  ", style="dim")
        stats.append(f"{mig.dirty_rate:,} pages/s", style=f"bold {dirty_c}")
        stats.append("    Method  ", style="dim")
        stats.append("Precopy",     style="white")
        t.add_row(Text(""), stats)

        if mig.downtime_ms is not None:
            t.add_row(
                Text("Downtime", style="dim"),
                Text(f"{mig.downtime_ms} ms (live)", style="bold green"),
            )

        return Panel(t, title="🚀 LIVE MIGRATION",
                     border_style="blue", box=box.ROUNDED)

    # ── Bin Packing plan ready ─────────────────────────────────────────────────
    if plan:
        with _lock:
            current = {v.name: v.host
                       for hs in hosts.values() for v in hs.vms}
        moves = [f"{vm} {current.get(vm,'?')} → {tgt}"
                 for vm, tgt in plan.items()
                 if current.get(vm) != tgt]
        if moves:
            t = Text()
            t.append("✦ Bin Packing complete  ", style="bold yellow")
            t.append(f"{len(moves)} VM(s) to relocate  ", style="white")
            t.append("press [m] to run", style="dim")
            t.append("\n\n" + "  |  ".join(moves[:4]), style="cyan")
            return Panel(t, title="PLAN", border_style="yellow", box=box.ROUNDED)
        else:
            return Panel(
                Text("✓ Current placement is already optimal.", style="green"),
                title="PLAN", border_style="dim", box=box.ROUNDED
            )

    # ── Recent migration metrics ─────────────────────────────────────────────
    with _lock:
        recent = list(mig_history[-3:])

    if recent and not mig:
        hist = Table.grid(padding=(0, 1))
        hist.add_column(width=10)
        hist.add_column()
        for j in reversed(recent):
            line = Text()
            line.append(f"{j.vm_name} ", style="bold")
            line.append(f"{j.src_host}→{j.dst_host}  ", style="dim")
            line.append(f"{j.elapsed:.1f}s", style="cyan")
            if j.downtime_ms is not None:
                line.append(f"  downtime ", style="dim")
                line.append(f"{j.downtime_ms} ms", style="bold green")
            elif j.failed:
                line.append("  FAILED", style="bold red")
            hist.add_row(Text("Last mig", style="dim"), line)
        return Panel(hist, title="MIGRATION STATS",
                     border_style="dim green", box=box.ROUNDED)

    # ── Idle ─────────────────────────────────────────────────────────────────
    t = Text()
    t.append("Idle — ", style="dim")
    t.append("[r]", style="bold yellow"); t.append(" spread/defrag  ", style="dim")
    t.append("[c]", style="bold yellow"); t.append(" Host-C Idle  ", style="dim")
    t.append("[l]", style="bold yellow"); t.append(" load balance  ", style="dim")
    t.append("[m]", style="bold yellow"); t.append(" run migration", style="dim")
    return Panel(t, title="STATUS", border_style="dim", box=box.ROUNDED)


def _render_log_panel() -> Panel:
    LEVEL_STYLE = {
        "OK":  "bold green",
        "INF": "bold blue",
        "WRN": "bold yellow",
        "ERR": "bold red",
        "MIG": "bold magenta",
        "BPK": "bold cyan",
    }
    grid = Table.grid(padding=(0, 1))
    grid.add_column(width=9, style="dim")
    grid.add_column(width=6)
    grid.add_column()

    with _lock:
        lines = list(log_lines[-LOG_MAX_LINES:])

    for ts, lvl, msg in lines:
        grid.add_row(
            ts,
            Text(f"[{lvl}]", style=LEVEL_STYLE.get(lvl, "white")),
            Text(msg)
        )
    grid.add_row("", "", Text("█", style="dim blink"))

    return Panel(grid, title="LOG", border_style="dim", box=box.ROUNDED)


def _build_layout() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="hosts",  size=14),
        Layout(name="status", size=8),
        Layout(name="log",    size=10),
        Layout(name="footer", size=1),
    )
    layout["hosts"].split_row(
        Layout(name="host_a"),
        Layout(name="host_b"),
        Layout(name="host_c"),
    )
    return layout


def _render(layout: Layout):
    # Header
    layout["header"].update(Panel(
        Align.center(Text(
            "⚡  LIVE MIGRATION DASHBOARD  ·  HW6  ·  2D Bin Packing",
            style="bold cyan"
        )),
        border_style="dim blue", box=box.HEAVY_HEAD
    ))

    # Hosts
    with _lock:
        host_states = dict(hosts)

    layout["host_a"].update(_render_host_panel(
        host_states.get("Host-A", HostState("Host-A", "", 8, 16384, reachable=False))))
    layout["host_b"].update(_render_host_panel(
        host_states.get("Host-B", HostState("Host-B", "", 8, 16384, reachable=False))))
    layout["host_c"].update(_render_host_panel(
        host_states.get("Host-C", HostState("Host-C", "", 8, 16384, reachable=False))))

    # Status
    layout["status"].update(_render_status_panel())

    # Log
    layout["log"].update(_render_log_panel())

    # Footer
    footer = Text(justify="center")
    pairs = [("[r]", "Spread BinPack"), ("[c]", "Consolidate (C idle)"),
             ("[l]", "Load balance"),  ("[m]", "Run migration"),
             ("[q]", "Quit")]
    for key, desc in pairs:
        footer.append(key,  style="bold yellow")
        footer.append(f" {desc}  ", style="dim")
    footer.append(time.strftime("%H:%M:%S"), style="dim")
    layout["footer"].update(Align.center(footer))

# ══════════════════════════════════════════════════════════════════════════════
#  Key handling
# ══════════════════════════════════════════════════════════════════════════════

def _handle_key(key: str):
    if key == "r":
        threading.Thread(target=run_bin_packing,
                         args=(False,), daemon=True).start()
    elif key == "c":
        threading.Thread(target=run_bin_packing,
                         args=(True,),  daemon=True).start()
    elif key == "l":
        threading.Thread(target=run_load_balance, daemon=True).start()
    elif key == "m":
        threading.Thread(target=execute_migrations, daemon=True).start()

# ══════════════════════════════════════════════════════════════════════════════
#  Config loader
# ══════════════════════════════════════════════════════════════════════════════

def _find_hw6_config() -> Optional[Path]:
    for path in HW6_CONFIG_PATHS:
        if path.is_file():
            return path
    return None


def _parse_hw6_config(path: Path) -> Dict[str, str]:
    data: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        data[key.strip()] = value.strip()
    return data


def load_hosts_config() -> Dict[str, dict]:
    """Build HOSTS_CONFIG from cluster.conf created by setup_main.sh."""
    path = _find_hw6_config()
    if path is None:
        searched = ", ".join(str(p) for p in HW6_CONFIG_PATHS)
        raise FileNotFoundError(
            f"HW6 config not found. Run setup_main.sh first.\n"
            f"  Looked for: {searched}"
        )

    raw = _parse_hw6_config(path)
    required = ("HOST_A_IP", "HOST_B_IP", "HOST_C_IP")
    missing = [k for k in required if not raw.get(k)]
    if missing:
        raise ValueError(f"Invalid config {path}: missing {', '.join(missing)}")

    cpu_cap = int(raw.get("CPU_CAP", "8"))
    mem_cap = int(raw.get("MEM_CAP_MB", "16384"))

    domain = raw.get("HOST_DOMAIN", "hw6.local")
    short = {"Host-A": "servera", "Host-B": "serverb", "Host-C": "serverc"}
    hosts_cfg = {}
    for h_name, ip_key in (
        ("Host-A", "HOST_A_IP"),
        ("Host-B", "HOST_B_IP"),
        ("Host-C", "HOST_C_IP"),
    ):
        s = short[h_name]
        ip = raw[ip_key]
        hosts_cfg[h_name] = {
            "ip": ip,
            "short": s,
            "fqdn": f"{s}.{domain}",
            "cpu_cap": cpu_cap,
            "mem_cap_mb": mem_cap,
        }
    return hosts_cfg


# ══════════════════════════════════════════════════════════════════════════════
#  Init & main
# ══════════════════════════════════════════════════════════════════════════════

def _init_hosts():
    for h_name, conf in HOSTS_CONFIG.items():
        hosts[h_name] = HostState(
            name=h_name, ip=conf["ip"],
            cpu_cap=conf["cpu_cap"], mem_cap_mb=conf["mem_cap_mb"]
        )


def main():
    global running, HOSTS_CONFIG

    try:
        HOSTS_CONFIG = load_hosts_config()
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[bold red]Config error:[/bold red] {exc}")
        sys.exit(1)

    _init_hosts()

    log("OK",  "Dashboard started")
    log("INF", f"libvirt — A:{HOSTS_CONFIG['Host-A']['ip']} "
              f"B:{HOSTS_CONFIG['Host-B']['ip']} "
              f"C:{HOSTS_CONFIG['Host-C']['ip']}")

    # Background update thread
    upd = threading.Thread(target=_update_loop, daemon=True)
    upd.start()

    layout = _build_layout()

    # Terminal raw mode (immediate key input)
    old_settings = termios.tcgetattr(sys.stdin)
    try:
        tty.setraw(sys.stdin.fileno())
        with Live(layout, console=console,
                  refresh_per_second=2, screen=True):
            while running:
                _render(layout)

                # Key input within 0.5s
                if select.select([sys.stdin], [], [], 0.5)[0]:
                    key = sys.stdin.read(1)
                    if key in ("q", "\x03"):   # q or Ctrl+C
                        running = False
                        break
                    _handle_key(key)

    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
        console.print("\n[dim]Dashboard exited.[/dim]")


if __name__ == "__main__":
    main()
