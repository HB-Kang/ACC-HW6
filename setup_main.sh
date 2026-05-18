#!/bin/bash
# =============================================================================
# HW6 - Host-A (servera) setup script
# Role: KVM host + NFS server (shared storage)
# Target: Rocky Linux 10
# Usage: bash setup_main.sh   (as root)
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
echo "║   HW6 Host-A (servera) Setup Script              ║"
echo "║   KVM Host + NFS Server                          ║"
echo "╚══════════════════════════════════════════════════╝"
echo -e "${NC}"

# ── [0] Cluster configuration ─────────────────────────────────────────────────
step 0 "Cluster network configuration"
echo "  Prefix is fixed: ${HW6_NETWORK_PREFIX}.<D>"
echo "  Set servera NIC to the Host-A octet you enter below."
echo ""

hw6_ensure_config prompt
ok "Config: ${HW6_CONFIG_FILE}"

SUBNET="$HW6_SUBNET"
NFS_DIR="$HW6_NFS_DIR"

# ── [1] Hostname + /etc/hosts (FQDN for live migration) ───────────────────────
step 1 "Configure hostname and /etc/hosts"
hw6_configure_cluster_hostname servera

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
    guestfs-tools \
    wget \
    python3-pip
ok "KVM packages installed"
hw6_install_iso_creator || warn "xorriso missing — needed for create_vms.sh (dnf install -y xorriso)"

# ── [4] NFS server ────────────────────────────────────────────────────────────
step 4 "Install NFS server"
dnf install -y -q nfs-utils
ok "nfs-utils installed"

# ── [5] Firewall / SELinux (lab) ──────────────────────────────────────────────
step 5 "Disable firewall / SELinux"
systemctl stop firewalld   2>/dev/null && ok "firewalld stopped" || warn "firewalld already stopped"
systemctl disable firewalld 2>/dev/null && ok "firewalld disabled" || true

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

# ── [7] NFS export ────────────────────────────────────────────────────────────
step 7 "Configure NFS server"
mkdir -p "$NFS_DIR" "$HW6_NFS_KEYS_DIR"
chmod 755 "$NFS_DIR"

if ! grep -q "$NFS_DIR" /etc/exports 2>/dev/null; then
    echo "$NFS_DIR $SUBNET(rw,sync,no_root_squash,no_subtree_check)" >> /etc/exports
    ok "exports entry added"
else
    warn "exports entry already exists — skipped"
fi

systemctl enable --now nfs-server
exportfs -ra
ok "NFS server started"

hw6_write_config
ok "Cluster config on NFS: ${HW6_NFS_CONFIG_PATH}"

# ── [8] Python (dashboard) ────────────────────────────────────────────────────
step 8 "Install Python rich"
pip3 install rich -q --break-system-packages
ok "rich installed"

# ── [9] SSH cluster trust ─────────────────────────────────────────────────────
step 9 "SSH keys and cluster trust"
echo "  Host-A (servera): ${HOST_A_IP}"
echo "  Host-B (serverb): ${HOST_B_IP}"
echo "  Host-C (serverc): ${HOST_C_IP}"
echo ""
hw6_ssh_setup_main_finalize

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════╗"
echo -e "║   Host-A setup complete!                         ║"
echo -e "╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo "  Config : ${HW6_CONFIG_FILE}"
echo "  Hosts  : servera serverb serverc → /etc/hosts"
echo "  SSH    : keys under ${HW6_NFS_KEYS_DIR}"
echo ""
echo "If SSH to B/C failed, on Host-B and Host-C run:"
echo "    bash setup_sub.sh"
echo "Then on Host-A verify:"
echo "    ssh root@serverb 'virsh list --all'"
echo "    ssh root@serverc 'virsh list --all'"
echo ""
echo "Optional (same root password on all hosts):"
echo "    HW6_ROOT_PASSWORD='yourpass' bash setup_main.sh"
echo ""
echo "Create VMs:"
echo "    bash create_vms.sh"
echo ""
echo "Dashboard:"
echo "    python3 migration_dashboard.py"
echo ""
