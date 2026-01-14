# Server Mode Setup Guide

Complete guide for setting up whirr in server mode for multi-machine GPU clusters.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        Head Node                                │
│         (whirr server + PostgreSQL via Docker)                  │
└───────────────────────────┬─────────────────────────────────────┘
                            │ HTTP API
          ┌─────────────────┼─────────────────┐
          │                 │                 │
          ▼                 ▼                 ▼
   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
   │ GPU Node 1  │   │ GPU Node 2  │   │ GPU Node N  │
   │ (worker)    │   │ (worker)    │   │ (worker)    │
   └─────────────┘   └─────────────┘   └─────────────┘
          │                 │                 │
          └─────────────────┼─────────────────┘
                            │
                    ┌───────▼───────┐
                    │ Shared Storage│
                    │ (NFS/etc)     │
                    └───────────────┘
```

**Key requirement:** Both the server and all workers must have access to the same shared filesystem. Workers write run data (logs, metrics, artifacts) directly to this filesystem, and the server reads from it.

## Prerequisites

- **Head node**: Any machine with Docker (can be low-power, e.g., mini PC)
- **GPU nodes**: Linux machines with NVIDIA GPUs
- **Shared storage**: NFS or similar accessible from all nodes
- **Network**: All nodes can reach head node on port 8080

## Step 1: Set Up Shared Storage

Workers and the server must share a filesystem. Set this up **before** starting the server.

### Option A: NFS (recommended for small clusters)

**On the head node (or a NAS):**

```bash
# Install NFS server
sudo apt install nfs-kernel-server

# Create and export directory
sudo mkdir -p /srv/whirr
sudo chown $USER:$USER /srv/whirr

# Export to your LAN (replace with your subnet)
echo "/srv/whirr 192.168.1.0/24(rw,sync,no_subtree_check)" | sudo tee -a /etc/exports
sudo exportfs -ra
```

> **Security note:** Avoid using `*` (exports to everyone) and `no_root_squash` (allows root access) in production. The example above restricts access to your local subnet.

**On each GPU node:**

```bash
# Install NFS client
sudo apt install nfs-common

# Create mount point
sudo mkdir -p /mnt/whirr

# Test the mount
sudo mount head-node:/srv/whirr /mnt/whirr

# Verify it works
touch /mnt/whirr/test && rm /mnt/whirr/test
```

**Add to `/etc/fstab` for persistence:**

```bash
# Recommended options:
#   _netdev     - wait for network before mounting
#   nofail      - don't block boot if mount fails
#   x-systemd.automount - mount on first access (optional, prevents boot delays)
echo "head-node:/srv/whirr /mnt/whirr nfs _netdev,nofail 0 0" | sudo tee -a /etc/fstab
```

> **Why these options?** Without `_netdev` and `nofail`, GPU nodes will hang at boot if the head node is unreachable. For extra reliability, add `x-systemd.automount` to mount only when first accessed.

### Option B: Already have shared storage

If you have existing shared storage (Ceph, GlusterFS, cloud storage), note the mount paths:
- Head node path: e.g., `/srv/whirr`
- GPU node path: e.g., `/mnt/whirr`

## Step 2: Set Up the Head Node

### Install Docker

```bash
# Ubuntu/Debian
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Log out and back in
```

### Clone whirr and configure

```bash
git clone https://github.com/syntropy-systems-oss/whirr.git
cd whirr

# Create .env file with your configuration
cat > .env << 'EOF'
POSTGRES_PASSWORD=your-secure-password-here
WHIRR_DATA_DIR=/srv/whirr
WHIRR_PORT=8080
EOF

# Generate a secure password (optional)
sed -i "s/your-secure-password-here/$(openssl rand -base64 32)/" .env
```

> **Important:** The `WHIRR_DATA_DIR` must point to your shared storage mount point on the head node. This gets bind-mounted into the Docker container.

### Start services

```bash
# Start PostgreSQL + whirr server
docker compose up -d

# Verify it's running
curl http://localhost:8080/health
# Should return: {"status":"healthy"}
```

### Configure firewall (if needed)

```bash
# Allow GPU nodes to connect
sudo ufw allow 8080/tcp
```

## Step 3: Install Workers on GPU Nodes

### Option A: Rust Worker (Recommended)

The Rust worker uses minimal memory (~10MB) - ideal for GPU machines where RAM is precious.

```bash
# Download the latest release
curl -L https://github.com/syntropy-systems-oss/whirr/releases/latest/download/whirr-worker-linux-x86_64 \
  -o /usr/local/bin/whirr-worker
chmod +x /usr/local/bin/whirr-worker

# Verify
whirr-worker --version
```

### Option B: Python Worker

If you prefer Python or need to modify the worker:

```bash
pip install whirr[server]
```

## Step 4: Start Workers

### Manual start (for testing)

```bash
# Rust worker
whirr-worker --server http://head-node:8080 --data-dir /mnt/whirr --gpu 0

# Python worker
whirr worker --server http://head-node:8080 --data-dir /mnt/whirr --gpu 0
```

### Run as a systemd service (production)

Create `/etc/systemd/system/whirr-worker.service`:

```ini
[Unit]
Description=whirr GPU worker
After=network-online.target remote-fs.target
Wants=network-online.target

[Service]
Type=simple
User=YOUR_USER
Environment="CUDA_VISIBLE_DEVICES=0"
ExecStart=/usr/local/bin/whirr-worker \
    --server http://head-node:8080 \
    --data-dir /mnt/whirr \
    --gpu 0
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl daemon-reload
sudo systemctl enable whirr-worker
sudo systemctl start whirr-worker

# Check status
sudo systemctl status whirr-worker
sudo journalctl -u whirr-worker -f
```

### Multiple GPUs on one machine

Create one service per GPU:

```bash
# For GPU 0
sudo cp /etc/systemd/system/whirr-worker.service /etc/systemd/system/whirr-worker-gpu0.service
# Edit to set --gpu 0 and CUDA_VISIBLE_DEVICES=0

# For GPU 1
sudo cp /etc/systemd/system/whirr-worker.service /etc/systemd/system/whirr-worker-gpu1.service
# Edit to set --gpu 1 and CUDA_VISIBLE_DEVICES=1

sudo systemctl daemon-reload
sudo systemctl enable whirr-worker-gpu0 whirr-worker-gpu1
sudo systemctl start whirr-worker-gpu0 whirr-worker-gpu1
```

## Step 5: Submit Jobs

From any machine that can reach the head node:

```bash
# Install whirr CLI
pip install whirr

# Submit a job
whirr submit --server http://head-node:8080 -- python train.py --lr 0.01

# Check status
curl http://head-node:8080/api/v1/status
```

Or set the environment variable to avoid typing `--server` every time:

```bash
export WHIRR_SERVER_URL=http://head-node:8080
whirr submit -- python train.py --lr 0.01
```

## Monitoring

### Check server status

```bash
curl http://head-node:8080/api/v1/status
```

### View logs

```bash
# Server logs
docker compose logs -f whirr-server

# Worker logs (systemd)
sudo journalctl -u whirr-worker -f
```

### Check worker registration

```bash
curl http://head-node:8080/api/v1/workers
```

## Troubleshooting

### Worker can't connect to server

```bash
# Check network connectivity
curl http://head-node:8080/health

# Check firewall
sudo ufw status
```

### Jobs stuck in "running" state

The worker may have crashed. Check:
```bash
# Worker logs
sudo journalctl -u whirr-worker -n 100

# Server will auto-requeue after lease expires (default: 60s)
```

### Shared storage not accessible

```bash
# Check mount on GPU node
df -h /mnt/whirr
mount | grep whirr

# Check NFS exports on head node
showmount -e head-node

# Test write from GPU node
touch /mnt/whirr/test-$(hostname) && rm /mnt/whirr/test-$(hostname)
```

### Server can't see worker outputs

Verify the bind mount is working:
```bash
# On head node, check what the container sees
docker compose exec whirr-server ls -la /data/runs/

# Should match what's in your shared storage
ls -la /srv/whirr/runs/
```

## Security Considerations

- The default setup has no authentication
- Restrict network access to trusted machines
- Use a firewall to limit who can reach port 8080
- For production, consider adding a reverse proxy with TLS
- Store `POSTGRES_PASSWORD` in `.env` file, not in shell history
