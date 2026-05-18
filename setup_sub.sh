#!/bin/bash
# =============================================================================
# HW6 - Host-B / Host-C (serverb / serverc) setup script
# Role: KVM host + NFS client
# Target: Rocky Linux 10
# Usage: bash setup_sub.sh   (as root)
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=hw6_config.sh
source "${SCRIPT_DIR}/hw6_config.sh"

hw6_require_root

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; NC='\033[0m'; BOLD='\033[1m'

step() { echo -e "\n${CYAN}${BOLD}[STEP $1]${NC} $2"; }
ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC}  $1"; }
ok_cb() { ok "$1"; }
warn_cb() { warn "$1"; }

echo -e "${BOLD}"
echo "╔══════════════════════════════════════════════════╗"
echo "║   HW6 Host-B/C (serverb/c) Setup Script          ║"
echo "║   KVM Host + NFS Client                          ║"
echo "╚══════════════════════════════════════════════════╝"
echo -e "${NC}"

NFS_DIR="$HW6_NFS_DIR"
THIS_ROLE=""
THIS_IP=""
THIS_HOSTNAME=""

# ── [0] Cluster configuration ─────────────────────────────────────────────────
step 0 "Cluster network configuration"

if hw6_ensure_config_from_nfs; then
    ok "Loaded cluster config"
elif hw6_ensure_config prompt; then
    ok "Config written: ${HW6_CONFIG_FILE}"
else
    echo "  Could not load or create cluster config."
    exit 1
fi

if hw6_detect_sub_role; then
    ok "Detected this host as Host-${THIS_ROLE^^} (${THIS_IP})"
else
    hw6_prompt_sub_role
    ok "Selected Host-${THIS_ROLE^^} (${THIS_IP})"
fi

THIS_HOSTNAME="$(hw6_role_to_hostname "$THIS_ROLE")"

echo ""
echo "  This host     : ${THIS_HOSTNAME} (${THIS_IP})"
echo "  Host-A (NFS)  : ${HOST_A_IP}"
echo "  NFS mount     : ${NFS_DIR}"
echo ""

# ── [1] /etc/hosts ────────────────────────────────────────────────────────────
step 1 "Configure /etc/hosts"
hw6_hosts_inject

# ── [2] System update + OpenSSH ───────────────────────────────────────────────
step 2 "System update and OpenSSH"
dnf update -y -q
hw6_install_openssh
ok "System packages ready"

# ── [3] KVM / libvirt ─────────────────────────────────────────────────────────
step 3 "Install KVM / libvirt packages"
dnf install -y -q \
    qemu-kvm \
    libvirt \
    libvirt-daemon \
    libvirt-daemon-driver-qemu \
    libvirt-client \
    virt-install \
    wget \
    python3-pip
ok "KVM packages installed"

# ── [4] NFS client ────────────────────────────────────────────────────────────
step 4 "Install NFS client"
dnf install -y -q nfs-utils
ok "nfs-utils installed"

# ── [5] Firewall / SELinux ────────────────────────────────────────────────────
step 5 "Disable firewall / SELinux"
systemctl stop firewalld    2>/dev/null && ok "firewalld stopped" || warn "firewalld already stopped"
systemctl disable firewalld  2>/dev/null && ok "firewalld disabled" || true

setenforce 0 2>/dev/null && ok "SELinux → Permissive" || warn "SELinux already disabled"
sed -i 's/^SELINUX=.*/SELINUX=permissive/' /etc/selinux/config
ok "SELinux config saved"

# ── [6] libvirtd ──────────────────────────────────────────────────────────────
step 6 "Start libvirtd"
systemctl enable --now libvirtd
ok "libvirtd enabled"

virsh net-list --all | grep -q "default" && ok "default network OK" || {
    virsh net-define /usr/share/libvirt/networks/default.xml
    virsh net-start default
    virsh net-autostart default
    ok "default network created"
}

# ── [7] NFS mount ─────────────────────────────────────────────────────────────
step 7 "Mount NFS shared storage"

echo "  Checking Host-A NFS at ${HOST_A_IP}..."
for _try in 1 2 3 4 5 6 7 8 9 10; do
    if showmount -e "$HOST_A_IP" 2>/dev/null | grep -q "libvirt"; then
        ok "Host-A NFS reachable"
        break
    fi
    if [[ $_try -eq 10 ]]; then
        warn "Host-A NFS not responding — continuing (mount may fail)"
    else
        echo "  Retry ${_try}/10 in 3s..."
        sleep 3
    fi
done

mkdir -p "$NFS_DIR"

FSTAB_ENTRY="${HOST_A_IP}:${NFS_DIR}  ${NFS_DIR}  nfs  defaults,_netdev,rw  0 0"
if ! grep -q "${HOST_A_IP}:${NFS_DIR}" /etc/fstab; then
    echo "$FSTAB_ENTRY" >> /etc/fstab
    ok "fstab entry added"
else
    warn "fstab already configured — skipped"
fi

mount -a 2>/dev/null && ok "NFS mounted" || warn "Mount failed — check Host-A NFS"

if hw6_ensure_config_from_nfs; then
    ok "Cluster config synced from NFS"
fi

# ── [8] SSH cluster trust ─────────────────────────────────────────────────────
step 8 "SSH keys and cluster trust"
hw6_ssh_setup_sub "$THIS_HOSTNAME"

if hw6_ssh_test_host servera; then
    ok "Passwordless SSH to servera OK"
else
    warn "Cannot SSH to servera yet — check Host-A and network"
fi

# ── [9] Python rich ───────────────────────────────────────────────────────────
step 9 "Install Python rich"
pip3 install rich -q --break-system-packages
ok "rich installed"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════╗"
echo -e "║   Host-${THIS_ROLE^^} setup complete!                       ║"
echo -e "╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo "  Config : ${HW6_CONFIG_FILE}"
echo "  Host   : ${THIS_HOSTNAME} (${THIS_IP})"
echo "  SSH key published: ${HW6_NFS_KEYS_DIR}/${THIS_HOSTNAME}.pub"
echo ""
echo "On Host-A, SSH trust should complete automatically."
echo "Verify from Host-A:"
echo "    ssh root@${THIS_HOSTNAME} 'virsh list --all'"
echo ""
