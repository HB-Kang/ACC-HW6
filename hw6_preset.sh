#!/bin/bash
# =============================================================================
# HW6 - Preset placement builder for migration_dashboard.py
#
# Removes any existing vm-1..vm-6 on A/B/C, recreates them on Host-A, then
# live-migrates each VM to the host required by the chosen scenario.
#
# Usage (run on Host-A as root):
#   bash hw6_preset.sh clean         # only delete VMs on A/B/C
#   bash hw6_preset.sh case1         # distributed   (vm-1,2->A vm-3,4->B vm-5,6->C)
#   bash hw6_preset.sh case2         # skew on A     (vm-1,3->A vm-2,4,5,6->B C idle)
#   bash hw6_preset.sh case3         # overload A    (vm-1,2,3,4->A vm-5,6->B C idle)
#
# Designed to be invoked from migration_dashboard.py keys [0]/[1]/[2]/[3].
# Prints "[PRESET] ..." progress lines to stdout (parsed by the dashboard).
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=hw6_config.sh
source "${SCRIPT_DIR}/hw6_config.sh"
hw6_require_root

if ! hw6_load_config "${HW6_CONFIG_FILE}"; then
    echo "[PRESET] ERR cluster.conf not found — run setup_main.sh first" >&2
    exit 1
fi

IMAGES_DIR="${NFS_DIR:-/var/lib/libvirt/images}"
BASE_IMG="${IMAGES_DIR}/jammy-base.img"
CLOUD_INIT_DIR="${HW6_CLOUD_INIT_DIR}"
UBUNTU_URL="https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img"

mkdir -p "${CLOUD_INIT_DIR}"

# Plain stdout — dashboard parses lines for the LOG panel.
ok_cb()   { echo "[PRESET] OK  $*"; }
warn_cb() { echo "[PRESET] WRN $*"; }
info()    { echo "[PRESET] INF $*"; }
fail()    { echo "[PRESET] ERR $*" >&2; }

declare -A VM_CPU=( [vm-1]=4 [vm-2]=2 [vm-3]=4 [vm-4]=1 [vm-5]=2 [vm-6]=1 )
declare -A VM_MEM=( [vm-1]=4096 [vm-2]=2048 [vm-3]=2048 [vm-4]=4096 [vm-5]=4096 [vm-6]=2048 )
VM_NAMES=(vm-1 vm-2 vm-3 vm-4 vm-5 vm-6)

# Each scenario maps vm -> host short name (servera|serverb|serverc).
# "Sub" hosts only (B/C) need live migration; A-targets stay on Host-A.
declare -A PRESET_CASE1=(
    [vm-1]=servera [vm-2]=servera
    [vm-3]=serverb [vm-4]=serverb
    [vm-5]=serverc [vm-6]=serverc
)
declare -A PRESET_CASE2=(
    [vm-1]=servera [vm-3]=servera
    [vm-2]=serverb [vm-4]=serverb [vm-5]=serverb [vm-6]=serverb
)
declare -A PRESET_CASE3=(
    [vm-1]=servera [vm-2]=servera [vm-3]=servera [vm-4]=servera
    [vm-5]=serverb [vm-6]=serverb
)

usage() {
    cat <<EOF
Usage: bash $(basename "$0") {clean|case1|case2|case3}

  clean    Remove vm-1..vm-6 on Host A/B/C (no recreate)
  case1    Distributed: vm-1,2 -> A  vm-3,4 -> B  vm-5,6 -> C
  case2    Skew on A:   vm-1,3 -> A  vm-2,4,5,6 -> B   (Host-C idle)
  case3    Overload A:  vm-1,2,3,4 -> A  vm-5,6 -> B   (Host-C idle)
EOF
}

# ── helpers ───────────────────────────────────────────────────────────────────

clean_all_vms() {
    info "Removing vm-1..vm-6 on all hosts"
    for vm in "${VM_NAMES[@]}"; do
        virsh destroy "$vm" 2>/dev/null || true
        virsh undefine "$vm" --nvram 2>/dev/null || true
        virsh undefine "$vm" --remove-all-storage 2>/dev/null || true
        for short in serverb serverc; do
            ssh -o BatchMode=yes -o ConnectTimeout=4 "root@${short}" \
                "virsh destroy $vm 2>/dev/null; virsh undefine $vm --nvram 2>/dev/null; virsh undefine $vm --remove-all-storage 2>/dev/null" \
                </dev/null >/dev/null 2>&1 || true
        done
        # leftover NFS disks
        rm -f "${IMAGES_DIR}/${vm}.qcow2" 2>/dev/null || true
        rm -f "${CLOUD_INIT_DIR}/cloud-init-${vm}.iso" 2>/dev/null || true
    done
    rm -f "${IMAGES_DIR}"/cloud-init-vm-*.iso 2>/dev/null || true
    ok_cb "All VMs removed"
}

ensure_base_image() {
    if [[ -f "${BASE_IMG}" ]]; then
        info "Base image present: ${BASE_IMG}"
        return 0
    fi
    info "Downloading Ubuntu 22.04 cloud image..."
    wget -q -O "${BASE_IMG}" "${UBUNTU_URL}" || { fail "wget failed"; return 1; }
    ok_cb "Base image downloaded"
}

make_cloud_init_iso() {
    local vm="$1"
    local iso="${CLOUD_INIT_DIR}/cloud-init-${vm}.iso"
    local tmp
    tmp=$(mktemp -d)

    cat > "${tmp}/user-data" <<'USERDATA'
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

    cat > "${tmp}/meta-data" <<METADATA
instance-id: ${vm}-001
local-hostname: ${vm}
METADATA

    if ! hw6_make_cidata_iso "${iso}" "${tmp}/user-data" "${tmp}/meta-data" >/dev/null; then
        rm -rf "${tmp}"
        fail "need xorriso or genisoimage — dnf install -y xorriso"
        return 1
    fi
    rm -rf "${tmp}"
    echo "${iso}"
}

create_vm_local() {
    local vm="$1"
    local cpu="${VM_CPU[$vm]}"
    local mem="${VM_MEM[$vm]}"
    local disk="${IMAGES_DIR}/${vm}.qcow2"

    info "Create ${vm} (${cpu} vCPU / ${mem} MB)"

    rm -f "$disk"
    qemu-img create -f qcow2 -F qcow2 -b "${BASE_IMG}" "$disk" 20G -q
    # NFS doesn't support xattrs, so libvirt's dynamic_ownership/remember_owner
    # silently fails and falls back to root:root at VM stop / migrate-out,
    # breaking subsequent migrations. We run with dynamic_ownership=0 and
    # chown the disk to qemu:qemu ourselves up-front.
    chown qemu:qemu "$disk"
    chmod 660 "$disk"

    local iso
    iso=$(make_cloud_init_iso "$vm") || return 1
    chown qemu:qemu "$iso" 2>/dev/null || true
    chmod 644 "$iso" 2>/dev/null || true

    virt-install \
        --name "$vm" \
        --vcpus "$cpu" \
        --memory "$mem" \
        --cpu "$(hw6_vm_cpu_spec)" \
        --machine "${HW6_VM_MACHINE}" \
        --disk "path=${disk},format=qcow2,bus=virtio" \
        --disk "path=${iso},device=cdrom,readonly=on" \
        --import \
        --os-variant ubuntu22.04 \
        --network bridge=virbr0,model=virtio \
        --graphics none \
        --serial pty \
        --console pty,target_type=serial \
        --noautoconsole \
        --quiet

    hw6_vm_detach_cloud_init_cdrom "$vm" 2>/dev/null || true
    ok_cb "${vm} created on Host-A"
}

apply_placement() {
    local -n placement="$1"
    info "Applying placement (live migration to B/C)"

    hw6_ssh_sync_keys_from_nfs 2>/dev/null || true
    hw6_ssh_update_known_hosts 2>/dev/null || true

    hw6_check_nfs_shared_storage "${IMAGES_DIR}" \
        || warn_cb "NFS may not be mounted — shared ${IMAGES_DIR} required"

    for vm in "${VM_NAMES[@]}"; do
        local target="${placement[$vm]:-}"
        [[ -n "$target" ]] || { info "${vm}: no target in this preset (skip)"; continue; }
        if [[ "$target" == "servera" ]]; then
            ok_cb "${vm} stays on Host-A"
            continue
        fi
        if ! hw6_ssh_test_host "$target"; then
            warn_cb "SSH to ${target} failed — leaving ${vm} on Host-A"
            continue
        fi
        hw6_migration_preflight "$target" || warn_cb "${target} preflight failed"
        info "${vm} -> ${target}"
        if hw6_virsh_migrate_live "$vm" "$(hw6_migrate_dest_uri "$target")" "$target"; then
            ok_cb "${vm} -> ${target} done"
        else
            warn_cb "${vm} -> ${target} failed (left on Host-A)"
        fi
    done
}

build_preset() {
    local case_name="$1"
    local -n placement_ref="$2"

    info "Preset ${case_name}: building"
    clean_all_vms
    ensure_base_image
    info "Creating 6 VMs on Host-A..."
    for vm in "${VM_NAMES[@]}"; do
        create_vm_local "$vm"
    done
    apply_placement placement_ref
    ok_cb "Preset ${case_name} ready"
}

# ── entry ─────────────────────────────────────────────────────────────────────

ACTION="${1:-}"
case "$ACTION" in
    clean)
        clean_all_vms
        ;;
    case1)
        build_preset "case1 (distributed)" PRESET_CASE1
        ;;
    case2)
        build_preset "case2 (skew on A)" PRESET_CASE2
        ;;
    case3)
        build_preset "case3 (overload A)" PRESET_CASE3
        ;;
    -h|--help|help|"")
        usage
        exit 0
        ;;
    *)
        fail "unknown action: ${ACTION}"
        usage
        exit 2
        ;;
esac
