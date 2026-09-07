# 🚀 24/7 Oracle Cloud Deployment Guide for Nemo

This guide covers deploying Nemo on an **Oracle Cloud Always-Free Instance** so your real-time token streaming, DuckDB database, automated batch audits, and interactive research dashboard run 24/7 without needing your laptop on.

---

## 1. Create Your Free Oracle Cloud Compute Instance

1. Log in to the [Oracle Cloud Infrastructure (OCI) Console](https://cloud.oracle.com/).
2. In the navigation menu, go to **Compute** > **Instances** > **Create Instance**.
3. Configure the instance:
   - **Name**: `nemo-forensics`
   - **Image**: **Ubuntu 22.04 / 24.04 LTS** (or Oracle Linux 8/9).
   - **Shape**:
     - **Option A (Best - Always Free)**: **Ampere ARM (VM.Standard.A1.Flex)** with **2 to 4 OCPUs** and **12 to 24 GB RAM**. (Oracle provides up to 4 OCPUs and 24 GB RAM completely free forever).
     - **Option B**: **AMD (VM.Standard.E2.1.Micro)** with **1 OCPU** and **1 GB RAM**.
   - **SSH Keys**: Download your private key (`id_rsa`) or paste your public SSH key.
4. Click **Create** and note down the **Public IP Address** once running.

---

## 2. Configure VCN Ingress Rule for Port 8000

By default, Oracle Cloud blocks all incoming traffic except SSH (port 22). You must allow port 8000:

1. On your instance details page, click on your **Virtual Cloud Network (VCN)** link.
2. Click on **Security Lists** > **Default Security List for your VCN**.
3. Click **Add Ingress Rules**:
   - **Source Type**: `CIDR`
   - **Source CIDR**: `0.0.0.0/0` (or your specific IP for private access)
   - **IP Protocol**: `TCP`
   - **Destination Port Range**: `8000`
   - **Description**: `Nemo Dashboard & WebSocket`
4. Click **Add Ingress Rules**.

---

## 3. Transfer Code and Deploy in 1 Command

### Step A: Connect to your Instance via SSH
```bash
ssh -i /path/to/your_private_key.key ubuntu@<YOUR_ORACLE_PUBLIC_IP>
```
*(If you selected Oracle Linux, use `opc@<YOUR_ORACLE_PUBLIC_IP>`)*

### Step B: Clone or Copy Your Nemo Repository
```bash
git clone https://github.com/<YOUR_USERNAME>/nemo.git
cd nemo
```
*(Or upload the folder from your laptop using `rsync -avz -e "ssh -i key.key" ./ nemo/ ubuntu@<IP>:~/nemo`)*

### Step C: Run the Automated Deploy Script
```bash
sudo ./scripts/deploy_oracle.sh
```

This script will:
- Open OS-level firewall ports on `ufw`, `firewalld`, and `iptables` for port 8000.
- Install Docker and Docker Compose.
- Build and launch the container with persistent volume mounts for `./data` (DuckDB).
- Enable auto-restart on host reboot.

---

## 4. Access Your 24/7 Dashboard

Open your web browser and visit:
```
http://<YOUR_ORACLE_PUBLIC_IP>:8000
```

### What runs automatically:
- **WebSocket Ingestion**: Real-time token creation and trade tape streaming.
- **Forensic Engine**: Block-0 bundle analysis, Jito tip checking, and Sybil detection.
- **Automated Cohort Auditor**: Runs every 10 minutes in the background, auditing tokens reaching the 10-hour mark and categorizing them into **💀 Rug Graveyard** vs **🛡️ Surviving Candidates**.
- **Interactive Re-Audit**: Access the **Cohort Audit & Learning HUD** from any phone or computer to review 3–10 day survivors, record ground-truth verdicts, and watch model precision climb!

---

## 5. Helpful Maintenance Commands

### Check container logs
```bash
docker compose logs -f
```

### Stop container
```bash
docker compose down
```

### Update code and restart
```bash
git pull
docker compose up -d --build
```
