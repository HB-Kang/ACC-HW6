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
  - Fixed-grid ASCII TUI (236x48 SSH terminal)

Usage
-----
  python3 migration_dashboard.py   (Linux only — Host-A Rocky)

Keys: [r] Bin Packing  [c] Consolidate (C Idle)  [l] Load balance  [m] Migrate  [q] Quit

Platform
--------
  Not supported on native Windows (needs termios/tty + virsh over SSH).
  Run on Host-A, or from Windows: WSL / ssh root@servera 'cd ACC && python3 migration_dashboard.py'
"""

import shutil
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

from rich.console import Console

# ══════════════════════════════════════════════════════════════════════════════
#  Configuration — loaded from /etc/hw6/cluster.conf (written by setup_*.sh)
# ══════════════════════════════════════════════════════════════════════════════

HW6_CONFIG_PATHS = (
    Path("/etc/hw6/cluster.conf"),
    Path(__file__).resolve().parent / "cluster.conf",
)

HOSTS_CONFIG: Dict[str, dict] = {}

REFRESH_INTERVAL = 1.5   # seconds (background SSH/virsh poll)
UI_REFRESH_SEC   = 1.0   # keyboard poll interval
# SSH terminal 236×48 (2-col margin avoids right-edge wrap on last column)
TERM_COLS = 236
TERM_ROWS = 48
TERM_MARGIN = 2
LOG_MAX_LINES = 20
VM_LINES_PER_HOST = 4

# Geometry (recomputed each frame) — fixed char grid, no Rich Layout
_ui_safe_cols = TERM_COLS - TERM_MARGIN
_ui_safe_rows = TERM_ROWS - 1
_ui_hw = 56          # Host-A|B|C column width
_ui_sw = 61          # STATUS+LOG column width
_ui_data_rows = 36   # main body height
_ui_status_rows = 10

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
    dest_uri = f"qemu+ssh://root@{dst_fqdn}/system"
    mig_tcp = f"tcp://{dst_ip}:0"
    virsh_base = [
        "virsh", "-c", f"qemu+ssh://root@{src_ip}/system",
        "migrate", "--live", "--persistent", "--undefinesource", "--unsafe",
    ]

    with _lock:
        active_mig    = MigrationJob(vm_name=vm_name,
                                     src_host=src_host,
                                     dst_host=dst_host)
        _mig_start_time = time.time()

    log("MIG", f"{vm_name}: {src_host} → {dst_host} migration started")

    # FQDN destination + IP migrateuri; fallback tunnelled+p2p only (other flags → argument unsupported)
    attempts = [
        virsh_base + ["--migrateuri", mig_tcp, vm_name, dest_uri],
        virsh_base + ["--tunnelled", "--p2p", vm_name, dest_uri],
    ]
    result = None
    for i, cmd in enumerate(attempts):
        if i > 0:
            log("MIG", f"{vm_name}: retry tunnelled+p2p")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode == 0:
            break

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
#  TUI rendering — fixed-width ASCII grid (SSH / MobaXterm safe)
# ══════════════════════════════════════════════════════════════════════════════

console = Console()


def _refresh_geometry() -> None:
    """236x48 target; scale columns if terminal is smaller."""
    global _ui_safe_cols, _ui_safe_rows, _ui_hw, _ui_sw, _ui_data_rows
    tw, th = shutil.get_terminal_size(fallback=(TERM_COLS, TERM_ROWS))
    _ui_safe_cols = min(tw, TERM_COLS) - TERM_MARGIN
    _ui_safe_rows = min(th, TERM_ROWS) - 1
    inner = _ui_safe_cols - 5
    _ui_hw = max(40, (inner * 56) // 229)
    _ui_sw = max(44, inner - 3 * _ui_hw)
    _ui_data_rows = max(20, _ui_safe_rows - 11)


def _fit(s: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(s) <= width:
        return s.ljust(width)
    if width <= 3:
        return s[:width]
    return s[: width - 3] + "..."


def _center(s: str, width: int) -> str:
    s = s[:width]
    if len(s) >= width:
        return s
    pad = width - len(s)
    left = pad // 2
    return " " * left + s + " " * (pad - left)


def _hline(width: int, char: str = "-") -> str:
    return "+" + char * max(0, width - 2) + "+"


def _midline(width: int, col_widths: Tuple[int, ...]) -> str:
    parts = ["+"]
    for cw in col_widths:
        parts.append("-" * cw)
        parts.append("+")
    return _fit("".join(parts), width)


def _vrow(cells: Tuple[str, ...], col_widths: Tuple[int, ...]) -> str:
    parts = ["|"]
    for text, cw in zip(cells, col_widths):
        parts.append(_fit(text, cw))
        parts.append("|")
    return "".join(parts)


def _ascii_bar(pct: float, width: int) -> str:
    pct = max(0.0, min(100.0, pct))
    fill = int(pct / 100.0 * width)
    return "#" * fill + "." * (width - fill)


def _metric_line(label: str, pct: float, col_w: int, suffix: str = "") -> str:
    bw = max(6, col_w - len(label) - len(suffix) - 6)
    bar = _ascii_bar(pct, bw)
    return _fit(f"{label}{bar} {pct:3.0f}%{suffix}", col_w)


def _host_column_lines(hs: HostState, col_w: int, mig: Optional[MigrationJob]) -> List[str]:
    lines: List[str] = []
    tag = "!" if not hs.reachable else " "
    lines.append(_fit(f"{tag}{hs.name} {hs.ip}", col_w))

    alloc_cpu = sum(v.cpu for v in hs.vms if v.state == "running")
    alloc_mem = sum(v.mem_mb for v in hs.vms if v.state == "running")
    cpu_a = alloc_cpu / hs.cpu_cap * 100 if hs.cpu_cap else 0
    mem_a = alloc_mem / hs.mem_cap_mb * 100 if hs.mem_cap_mb else 0
    cpu_h = hs.cpu_host_pct if hs.reachable else 0.0
    mem_h = hs.mem_host_pct if hs.reachable else 0.0
    has_host = hs.reachable and (cpu_h > 0 or mem_h > 0 or hs.mem_total_mb > 0)

    if has_host:
        lines.append(_metric_line("Ch", cpu_h, col_w, " host"))
        lines.append(_metric_line("Ca", cpu_a, col_w, " alloc"))
        mem_sfx = f" {hs.mem_used_mb}/{hs.mem_total_mb}M"
        lines.append(_metric_line("Mh", mem_h, col_w, mem_sfx[: max(0, col_w - 12)]))
        lines.append(_metric_line("Ma", mem_a, col_w, " alloc"))
    else:
        lines.append(_metric_line("Ca", cpu_a, col_w, " alloc"))
        lines.append(_metric_line("Ma", mem_a, col_w, " alloc"))

    lines.append(_fit("-" * min(col_w, 40), col_w))

    if not hs.vms:
        lines.append(_fit("[ IDLE ]", col_w))
    else:
        for vm in hs.vms[:VM_LINES_PER_HOST]:
            if mig and mig.vm_name == vm.name and mig.src_host == hs.name:
                icon, label = ">", "migrating"
            elif mig and mig.vm_name == vm.name and mig.dst_host == hs.name:
                icon, label = "<", "arriving"
            elif vm.state == "running":
                icon, label = "*", "running"
            else:
                icon, label = "-", vm.state[:8]
            lines.append(_fit(
                f"{icon}{vm.name:<8}{vm.cpu}c{vm.mem_mb // 1024:>2}G {label}", col_w
            ))
        extra = len(hs.vms) - VM_LINES_PER_HOST
        if extra > 0:
            lines.append(_fit(f"+{extra} more VM(s)", col_w))

    while len(lines) < _ui_data_rows:
        lines.append(" " * col_w)
    return lines[:_ui_data_rows]


def _status_column_lines(col_w: int) -> List[str]:
    out: List[str] = []
    with _lock:
        mig = active_mig
        plan = dict(bin_pack_plan)
        recent = list(mig_history[-3:])

    if mig:
        pct = mig.data_proc_b / mig.data_total_b * 100 if mig.data_total_b else 0
        mb_p = mig.data_proc_b / 1024 / 1024
        mb_t = mig.data_total_b / 1024 / 1024
        bw = max(6, col_w - 14)
        out.append(_fit("== LIVE MIGRATION ==", col_w))
        out.append(_fit(f"{mig.vm_name}", col_w))
        out.append(_fit(f"{mig.src_host}->{mig.dst_host}", col_w))
        out.append(_fit(f"{_ascii_bar(pct, bw)} {pct:.0f}%", col_w))
        out.append(_fit(f"{mb_p:.1f}/{mb_t:.1f} MB", col_w))
        out.append(_fit(f"dirty {mig.dirty_rate}/s", col_w))
        if mig.downtime_ms is not None:
            out.append(_fit(f"down {mig.downtime_ms}ms", col_w))
    elif plan:
        with _lock:
            current = {v.name: v.host for h in hosts.values() for v in h.vms}
        moves = [f"{vm}:{current.get(vm,'?')}->{tgt}"
                 for vm, tgt in plan.items() if current.get(vm) != tgt]
        out.append(_fit("== BIN PACK PLAN ==", col_w))
        if moves:
            out.append(_fit(f"{len(moves)} VM(s) [m] run", col_w))
            for m in moves[:6]:
                out.append(_fit(m, col_w))
        else:
            out.append(_fit("already optimal", col_w))
    elif recent:
        out.append(_fit("== LAST MIGRATIONS ==", col_w))
        for j in reversed(recent):
            s = f"{j.vm_name} {j.elapsed:.1f}s"
            if j.downtime_ms is not None:
                s += f" dt{j.downtime_ms}ms"
            elif j.failed:
                s += " FAIL"
            out.append(_fit(s, col_w))
    else:
        out.append(_fit("== STATUS ==", col_w))
        out.append(_fit("Idle", col_w))
        out.append(_fit("[r]pack [c]C-idle", col_w))
        out.append(_fit("[l]balance [m]go", col_w))

    while len(out) < _ui_status_rows:
        out.append("")
    return out[:_ui_status_rows]


def _log_column_lines(col_w: int) -> List[str]:
    rows = _ui_data_rows - _ui_status_rows
    out: List[str] = []
    out.append(_fit("== LOG ==", col_w))
    with _lock:
        entries = list(log_lines[-rows:])
    for ts, lvl, msg in entries:
        out.append(_fit(f"{ts}[{lvl}]{msg}", col_w))
    if not entries:
        out.append(_fit("(no messages)", col_w))
    while len(out) < rows:
        out.append("")
    return out[:rows]


def _footer_text() -> str:
    return ("[r]BinPack  [c]C-idle  [l]LoadBal  [m]Migrate  [q]Quit  "
            + time.strftime("%H:%M:%S"))


def _build_frame() -> List[str]:
    """Every line is exactly _ui_safe_cols characters."""
    w = _ui_safe_cols
    cols = (_ui_hw, _ui_hw, _ui_hw, _ui_sw)

    with _lock:
        host_states = {n: hosts.get(n) for n in ("Host-A", "Host-B", "Host-C")}
        mig = active_mig

    ha = _host_column_lines(
        host_states["Host-A"] or HostState("Host-A", "", 8, 16384, reachable=False),
        _ui_hw, mig)
    hb = _host_column_lines(
        host_states["Host-B"] or HostState("Host-B", "", 8, 16384, reachable=False),
        _ui_hw, mig)
    hc = _host_column_lines(
        host_states["Host-C"] or HostState("Host-C", "", 8, 16384, reachable=False),
        _ui_hw, mig)
    status = _status_column_lines(_ui_sw)
    logcol = _log_column_lines(_ui_sw)

    frame: List[str] = []
    frame.append(_hline(w))
    frame.append("|" + _center("HW6 Live Migration Dashboard", w - 2) + "|")
    frame.append(_midline(w, cols))
    frame.append(_vrow(("Host-A", "Host-B", "Host-C", "STATUS / LOG"), cols))

    for i in range(_ui_data_rows):
        side = status[i] if i < _ui_status_rows else logcol[i - _ui_status_rows]
        frame.append(_vrow((ha[i], hb[i], hc[i], side), cols))

    frame.append(_hline(w))
    frame.append("|" + _center(_fit(_footer_text(), w - 2), w - 2) + "|")
    frame.append(_hline(w))

    fixed: List[str] = []
    for line in frame:
        fixed.append(_fit(line, w))
    while len(fixed) < _ui_safe_rows:
        fixed.append(_fit("", w))
    return fixed[:_ui_safe_rows]


def _draw_dashboard() -> None:
    """Redraw full frame with ANSI (no Rich Layout/Panel)."""
    _refresh_geometry()
    sys.stdout.write("\033[H\033[J")
    for line in _build_frame():
        sys.stdout.write(line + "\n")
    sys.stdout.flush()

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

    old_settings = termios.tcgetattr(sys.stdin)
    try:
        tty.setraw(sys.stdin.fileno())
        sys.stdout.write("\033[?25l")
        sys.stdout.flush()
        while running:
            _draw_dashboard()
            if select.select([sys.stdin], [], [], UI_REFRESH_SEC)[0]:
                key = sys.stdin.read(1)
                if key in ("q", "\x03"):
                    running = False
                    break
                _handle_key(key)
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
        sys.stdout.write("\033[?25h\033[H\033[J")
        sys.stdout.flush()
        console.print("[dim]Dashboard exited.[/dim]")


if __name__ == "__main__":
    main()
