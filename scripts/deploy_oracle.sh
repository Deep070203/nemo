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

# Direct iptables rule fallback (Oracle Linux default iptables chain)
if command -v iptables >/dev/null 2>&1; then
  iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8000 -j ACCEPT || true
fi

# 3. Install Docker if not already present
if ! command -v docker >/dev/null 2>&1; then
  echo "📦 Docker not detected. Installing Docker..."
  curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
  sh /tmp/get-docker.sh
  systemctl enable --now docker
fi

# Ensure docker-compose plugin or standalone exists
if ! docker compose version >/dev/null 2>&1; then
  echo "📦 Installing Docker Compose plugin..."
  apt-get update && apt-get install -y docker-compose-plugin || dnf install -y docker-compose-plugin || true
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
