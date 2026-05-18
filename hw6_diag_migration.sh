#!/bin/bash
# HW6 migration diagnostics — run on Host-A as root after: source hw6_config.sh && hw6_load_config
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

check_host() {
    local short="$1"
    local fqdn ip
    fqdn="$(hw6_fqdn "$short")"
    ip="$(hw6_cluster_ip_for_short "$short")"

    echo ""
    echo "========== ${short} (${ip}) =========="
    if ! hw6_ssh_test_host "$short"; then
        echo "  FAIL: SSH to root@${short}"
        return 1
    fi

    ssh -o BatchMode=yes "root@${short}" bash -s -- "$fqdn" "$ip" <<'EOS' || true
fqdn="$1"
want="$2"
echo "  hostname -f: $(hostname -f)"
echo "  getent:      $(getent hosts "$(hostname -f)" 2>/dev/null || echo FAIL)"
echo "  migration_host: $(grep -E '^#?migration_host' /etc/libvirt/qemu.conf 2>/dev/null || echo MISSING)"
echo "  libvirtd:    $(systemctl is-active libvirtd 2>/dev/null)"
echo "  NFS:         $(mount | grep libvirt || echo NOT MOUNTED)"
echo "  nested KVM:  $(grep -c '^flags.* vmx\|^flags.* svm' /proc/cpuinfo 2>/dev/null || echo 0) (need >0 for KVM host)"
resolved=$(getent hosts "$(hostname -f)" | awk '{print $1}' | head -1)
[[ "$resolved" == "$want" ]] && echo "  FQDN→IP:     OK ($resolved)" || echo "  FQDN→IP:     FAIL (got ${resolved:-?}, want $want)"
[[ "$resolved" != "127.0.0.1" ]] && echo "  not localhost: OK" || echo "  not localhost: FAIL"
EOS

    if virsh -c "qemu+ssh://root@${ip}/system" version &>/dev/null; then
        echo "  virsh remote: OK"
    else
        echo "  virsh remote: FAIL (qemu+ssh://root@${ip}/system)"
    fi
}

echo "HW6 migration diagnostics (cluster.conf loaded)"
for h in servera serverb serverc; do
    check_host "$h"
done
echo ""
echo "If Host-C shows FQDN→IP FAIL or NOT MOUNTED, on C run: sudo bash setup_sub.sh"
echo "If nested KVM is 0 on C, enable nested virtualization in VirtualBox/VMware."
