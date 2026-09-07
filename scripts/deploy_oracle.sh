#!/usr/bin/env bash
# ==============================================================================
# Nemo: Oracle Cloud 24/7 Automated Instance Deployment Script
# Supports: Ubuntu 22.04/24.04 LTS and Oracle Linux 8/9 (x86_64 & ARM64 Ampere)
# ==============================================================================

set -e

echo "=========================================================="
echo "⚡ Deploying Nemo Forensics & Ingestion on Oracle Cloud"
echo "=========================================================="

# 1. Detect OS & Root privileges
if [ "$EUID" -ne 0 ]; then
  echo "⚠️ Please run as root or with sudo: sudo ./scripts/deploy_oracle.sh"
  exit 1
fi

# Detect distribution
OS_ID=""
if [ -f /etc/os-release ]; then
  . /etc/os-release
  OS_ID="$ID"
fi

# 2. Host Firewall Configuration (Crucial for Oracle Cloud instances!)
echo "🔧 Configuring OS-level firewall rules for port 8000..."
if command -v ufw >/dev/null 2>&1; then
  ufw allow 8000/tcp comment 'Nemo Dashboard'
  ufw reload || true
fi

if command -v firewall-cmd >/dev/null 2>&1; then
  firewall-cmd --permanent --add-port=8000/tcp || true
  firewall-cmd --reload || true
fi

# Direct iptables rule fallback (Insert at position 1 to guarantee top priority)
if command -v iptables >/dev/null 2>&1; then
  iptables -I INPUT 1 -m state --state NEW -p tcp --dport 8000 -j ACCEPT 2>/dev/null || \
  iptables -I INPUT 1 -p tcp --dport 8000 -j ACCEPT 2>/dev/null || true
fi

# 3. Install Docker if not already present
if ! command -v docker >/dev/null 2>&1; then
  echo "📦 Docker not detected. Installing Docker for $OS_ID..."

  if [ "$OS_ID" = "ol" ] || [ "$OS_ID" = "rhel" ] || [ "$OS_ID" = "centos" ] || [ "$OS_ID" = "rocky" ] || [ "$OS_ID" = "almalinux" ]; then
    echo "Detected Oracle Linux / RHEL family. Configuring DNF / YUM repositories..."
    if command -v dnf >/dev/null 2>&1; then
      dnf install -y dnf-plugins-core
      dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
      dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin --nobest --allowerasing
    else
      yum install -y yum-utils
      yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
      yum install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
    fi
  else
    # Debian / Ubuntu / generic
    echo "Installing Docker via official script..."
    curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
    sh /tmp/get-docker.sh
  fi

  systemctl enable --now docker
fi

# Add invoking user to docker group if running under sudo
if [ -n "$SUDO_USER" ]; then
  usermod -aG docker "$SUDO_USER" 2>/dev/null || true
fi

# Ensure docker-compose plugin or standalone exists
if ! docker compose version >/dev/null 2>&1; then
  echo "📦 Installing Docker Compose plugin..."
  if command -v dnf >/dev/null 2>&1; then
    dnf install -y docker-compose-plugin || true
  elif command -v apt-get >/dev/null 2>&1; then
    apt-get update && apt-get install -y docker-compose-plugin || true
  fi
fi

# 4. Create persistent data directory with proper permissions
mkdir -p data config

# 5. Build and launch Nemo daemon container
echo "🚀 Building and starting Nemo container..."
docker compose up -d --build

echo "=========================================================="
echo "✅ Nemo is now running 24/7 in the background!"
echo "• Dashboard URL: http://<YOUR_ORACLE_PUBLIC_IP>:8000"
echo "• Logs: docker compose logs -f"
echo "• Stop: docker compose down"
echo "=========================================================="
