#!/bin/bash
# =============================================================================
# HW6 - VM creation script
# Run on Host-A. Creates 6 VMs from Ubuntu 22.04 cloud image.
# Usage: bash create_vms.sh
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=hw6_config.sh
source "${SCRIPT_DIR}/hw6_config.sh"
hw6_require_root

if hw6_load_config "${HW6_CONFIG_FILE}"; then
    IMAGES_DIR="${NFS_DIR:-/var/lib/libvirt/images}"
else
    IMAGES_DIR="/var/lib/libvirt/images"
    warn "No ${HW6_CONFIG_FILE} — using default image path"
fi
BASE_IMG="$IMAGES_DIR/jammy-base.img"
UBUNTU_URL="https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
NC='\033[0m'; BOLD='\033[1m'

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC}  $1"; }
info() { echo -e "  ${CYAN}→${NC} $1"; }
ok_cb()   { ok "$1"; }
warn_cb() { warn "$1"; }

echo -e "${BOLD}"
echo "╔══════════════════════════════════════════════════╗"
echo "║   HW6 VM Creation Script                         ║"
echo "║   Ubuntu 22.04 × 6 (vCPU/MEM specified)          ║"
echo "╚══════════════════════════════════════════════════╝"
echo -e "${NC}"

# ── VM definitions: name, vCPU, MEM(MB), initial host (planning only) ───────
# All VMs are created on Host-A first; migrate to B/C afterward
declare -A VM_CPU=( [vm-1]=4 [vm-2]=2 [vm-3]=4 [vm-4]=1 [vm-5]=2 [vm-6]=1 )
declare -A VM_MEM=( [vm-1]=4096 [vm-2]=2048 [vm-3]=2048 [vm-4]=4096 [vm-5]=4096 [vm-6]=2048 )
declare -A VM_HOST=( [vm-1]=A [vm-2]=A [vm-3]=B [vm-4]=B [vm-5]=C [vm-6]=C )
VM_NAMES=("vm-1" "vm-2" "vm-3" "vm-4" "vm-5" "vm-6")

echo "  VMs to create:"
printf "  %-8s %-6s %-8s %-12s\n" "Name" "vCPU" "MEM(MB)" "Initial host"
printf "  %-8s %-6s %-8s %-12s\n" "────" "────" "───────" "────────"
for vm in "${VM_NAMES[@]}"; do
    printf "  %-8s %-6s %-8s %-12s\n" \
        "$vm" "${VM_CPU[$vm]}" "${VM_MEM[$vm]}" "Host-${VM_HOST[$vm]}"
done
echo ""
read -p "Continue? [y/N] " confirm
[[ "$confirm" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }

# ── [1] Download Ubuntu 22.04 base image ──────────────────────────────────────
echo -e "\n${CYAN}${BOLD}[STEP 1]${NC} Prepare Ubuntu 22.04 cloud image"
if [ ! -f "$BASE_IMG" ]; then
    info "Downloading: $UBUNTU_URL"
    wget -q --show-progress -O "$BASE_IMG" "$UBUNTU_URL"
    ok "Base image downloaded: $BASE_IMG"
else
    warn "Base image already exists — skipped ($BASE_IMG)"
fi

# ── [2] cloud-init ISO helper ─────────────────────────────────────────────────
make_cloud_init_iso() {
    local vm_name="$1"
    local iso_path="$IMAGES_DIR/cloud-init-${vm_name}.iso"
    local tmp_dir
    tmp_dir=$(mktemp -d)

    # user-data: default account + stress-ng
    cat > "$tmp_dir/user-data" << 'USERDATA'
#cloud-config
users:
  - name: root
    lock_passwd: false
password: "hw6pass"
chpasswd:
  expire: false
ssh_pwauth: true
package_update: true
packages:
  - stress-ng
  - htop
  - sysstat
write_files:
  - path: /etc/motd
    content: |
      =========================================
      HW6 Live Migration Lab VM
      =========================================
runcmd:
  - echo "VM ready" > /tmp/ready
USERDATA

    # meta-data
    cat > "$tmp_dir/meta-data" << METADATA
instance-id: ${vm_name}-001
local-hostname: ${vm_name}
METADATA

    if ! hw6_make_cidata_iso "$iso_path" "$tmp_dir/user-data" "$tmp_dir/meta-data"; then
        rm -rf "$tmp_dir"
        echo "ERROR: need xorriso or genisoimage — run: dnf install -y xorriso" >&2
        return 1
    fi

    rm -rf "$tmp_dir"
    echo "$iso_path"
}

# ── [3] Create VMs ────────────────────────────────────────────────────────────
echo -e "\n${CYAN}${BOLD}[STEP 2]${NC} Create VMs"

for vm in "${VM_NAMES[@]}"; do
    cpu="${VM_CPU[$vm]}"
    mem="${VM_MEM[$vm]}"
    disk_path="$IMAGES_DIR/${vm}.qcow2"

    echo ""
    info "Creating $vm (${cpu} vCPU / ${mem} MB RAM)..."

    # Remove existing VM if present
    if virsh dominfo "$vm" &>/dev/null; then
        warn "$vm already exists — removing and recreating"
        virsh destroy "$vm" 2>/dev/null || true
        virsh undefine "$vm" --remove-all-storage 2>/dev/null || true
    fi

    # Disk from base image backing
    rm -f "$disk_path"
    qemu-img create -f qcow2 -F qcow2 -b "$BASE_IMG" "$disk_path" 20G -q
    ok "Disk created: $disk_path (20G, backing: jammy-base)"

    # cloud-init ISO
    iso_path=$(make_cloud_init_iso "$vm")
    ok "cloud-init ISO created: $iso_path"

    # Create VM
    virt-install \
        --name "$vm" \
        --vcpus "$cpu" \
        --memory "$mem" \
        --disk "path=${disk_path},format=qcow2,bus=virtio" \
        --disk "path=${iso_path},device=cdrom" \
        --import \
        --os-variant ubuntu22.04 \
        --network bridge=virbr0,model=virtio \
        --graphics none \
        --serial pty \
        --console pty,target_type=serial \
        --noautoconsole \
        --quiet

    ok "$vm created"
done

# ── [4] Initial placement (migrate to B, C) ───────────────────────────────────
echo -e "\n${CYAN}${BOLD}[STEP 3]${NC} Initial placement (vm-3,4 → Host-B / vm-5,6 → Host-C)"

hw6_ssh_sync_keys_from_nfs 2>/dev/null || true
hw6_ssh_update_known_hosts 2>/dev/null || true

do_place=0
if hw6_ssh_test_host serverb && hw6_ssh_test_host serverc; then
    info "SSH to serverb/serverc OK — running live migration placement"
    do_place=1
else
    warn "SSH to serverb/serverc not ready — skipping auto placement"
    warn "Finish setup_sub on B/C, then: virsh migrate ... or re-run create_vms.sh"
fi

if [[ "$do_place" -eq 1 ]]; then
    hw6_check_nfs_shared_storage "$IMAGES_DIR" || \
        warn "NFS may not be mounted on this host — migration needs shared ${IMAGES_DIR}"
    for vm in vm-3 vm-4; do
        info "  $vm → Host-B (serverb)..."
        hw6_virsh_migrate_live "$vm" "$(hw6_migrate_dest_uri serverb)" && ok "$vm → Host-B done"
    done
    for vm in vm-5 vm-6; do
        info "  $vm → Host-C (serverc)..."
        hw6_virsh_migrate_live "$vm" "$(hw6_migrate_dest_uri serverc)" && ok "$vm → Host-C done"
    done
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════╗"
echo -e "║   VM creation complete!                          ║"
echo -e "╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo "List VMs:"
echo "  virsh list --all"
echo ""
echo "Start dashboard:"
echo "  python3 migration_dashboard.py"
echo ""
echo "VM console:"
echo "  virsh console vm-1   (exit: Ctrl+])"
echo ""
echo "stress-ng workload examples (inside VM):"
echo "  # CPU load"
echo "  stress-ng --cpu \$(nproc) --timeout 0 &"
echo "  # MEM load"
echo "  stress-ng --vm 1 --vm-bytes 80% --timeout 0 &"
echo ""
