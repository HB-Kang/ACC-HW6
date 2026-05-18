#!/bin/bash
# HW6 migration diagnostics — run on Host-A as root
# Usage: sudo bash hw6_diag_migration.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=hw6_config.sh
source "${SCRIPT_DIR}/hw6_config.sh"
hw6_require_root
hw6_load_config "${HW6_CONFIG_FILE}" || hw6_load_config "${HW6_NFS_CONFIG_PATH}" || {
    echo "Load /etc/hw6/cluster.conf first (setup_main.sh)"
    exit 1
}

# Shared remote script (IPv4-only FQDN check)
read -r -d '' REMOTE_CHECKS <<'EOS' || true
want="$1"
path="$2"
echo "  hostname -f: $(hostname -f)"
echo "  getent(all): $(getent ahosts "$(hostname -f)" 2>/dev/null | head -3 | tr '\n' ' ')"
resolved="$(getent ahostsv4 "$(hostname -f)" 2>/dev/null | awk '$2 == "STREAM" { print $1; exit }')"
[[ -z "$resolved" ]] && resolved="$(getent hosts "$(hostname -f)" | awk '/^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ { print $1; exit }')"
echo "  FQDN→IPv4:  ${resolved:-FAIL} (want ${want})"
[[ "$resolved" == "$want" ]] && echo "  FQDN check:  OK" || echo "  FQDN check:  FAIL"
[[ "$resolved" != "127.0.0.1" ]] && echo "  not localhost: OK" || echo "  not localhost: FAIL"
echo "  migration_host: $(grep -E '^#?migration_host' /etc/libvirt/qemu.conf 2>/dev/null || echo MISSING)"
echo "  libvirtd:    $(systemctl is-active libvirtd 2>/dev/null)"
echo "  nested KVM:  $(grep -Ec 'vmx|svm' /proc/cpuinfo 2>/dev/null || echo 0) (need >0)"
echo "  NFS:         $(mount | grep libvirt || echo NOT MOUNTED)"
EOS

check_host() {
    local short="$1"
    local fqdn ip path

    fqdn="$(hw6_fqdn "$short")"
    ip="$(hw6_cluster_ip_for_short "$short")"
    path="$HW6_NFS_DIR"

    echo ""
    echo "========== ${short} (${ip}) =========="

    if [[ "$short" == "servera" ]]; then
        hw6_prefer_ipv4_gai 2>/dev/null || true
        echo "  hostname -f: $(hostname -f)"
        echo "  getent(all): $(getent ahosts "$(hostname -f)" 2>/dev/null | head -3 | tr '\n' ' ')"
        resolved="$(hw6_resolve_ipv4 "$(hostname -f)")"
        echo "  FQDN→IPv4:  ${resolved:-FAIL} (want ${ip})"
        [[ "$resolved" == "$ip" ]] && echo "  FQDN check:  OK" || echo "  FQDN check:  FAIL"
        echo "  libvirtd:    $(systemctl is-active libvirtd 2>/dev/null)"
        echo "  nfs-server:  $(systemctl is-active nfs-server 2>/dev/null)"
        if exportfs -v 2>/dev/null | grep -q libvirt; then
            echo "  NFS:         export OK (A is server — client mount not required)"
        else
            echo "  NFS:         export MISSING"
        fi
        mount | grep -q libvirt && echo "  NFS mount:   optional local mount present" || true
        virsh -c qemu:///system version &>/dev/null && echo "  virsh local: OK" || echo "  virsh local: FAIL"
        return 0
    fi

    if ! hw6_ssh_test_host "$short"; then
        echo "  FAIL: SSH to root@${short}"
        return 1
    fi

    ssh -o BatchMode=yes "root@${short}" bash -s -- "$ip" "$path" <<<"$REMOTE_CHECKS" || true

    if virsh -c "qemu+ssh://root@${ip}/system" version &>/dev/null; then
        echo "  virsh remote: OK"
    else
        echo "  virsh remote: FAIL"
    fi
}

echo "HW6 migration diagnostics"
for h in servera serverb serverc; do
    check_host "$h"
done
echo ""
echo "Host-A NFS 'export OK' is normal (not a client mount)."
echo "If FQDN→IPv4 FAIL (IPv6 in getent): run setup_sub.sh on B/C or setup_main.sh on A."
