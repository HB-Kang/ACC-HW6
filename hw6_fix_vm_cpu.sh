#!/bin/bash
# Fix existing VMs for cross-host live migration on Rocky Linux 10
# (host-model CPU → qemu64 — avoids "Failed to set special registers")
# Run on Host-A as root:  sudo bash hw6_fix_vm_cpu.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=hw6_config.sh
source "${SCRIPT_DIR}/hw6_config.sh"
hw6_require_root
hw6_load_config "${HW6_CONFIG_FILE}" 2>/dev/null || hw6_load_config "${HW6_NFS_CONFIG_PATH}" 2>/dev/null || true

echo "HW6: set CPU to $(hw6_vm_cpu_spec) on all defined VMs (servera/b/c)"
echo "  (Intel i7 cluster — -svm drops AMD feature that breaks Host-C)"
hw6_ensure_all_vms_cpu_migratable
echo "Done. Retry: virsh migrate ... or create_vms.sh placement / dashboard."
