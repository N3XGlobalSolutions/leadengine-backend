# Qdrant EC2 Setup - AWS Console Step-by-Step Guide

> **Estimated Time:** 30 minutes
> **Cost:** ~$10/month (spot) or ~$15/month (on-demand)

---

## Table of Contents

1. [Create Security Group](#step-1-create-security-group)
2. [Launch EC2 Instance](#step-2-launch-ec2-instance)
3. [Connect and Install Qdrant](#step-3-connect-and-install-qdrant)
4. [Test the Connection](#step-4-test-the-connection)
5. [Get Elastic IP (Optional)](#step-5-get-elastic-ip-optional)

---

## Step 1: Create Security Group

### What is a Security Group?

A **Security Group** is like a virtual firewall that controls what traffic can enter (inbound) and leave (outbound) your EC2 instance. Without proper rules, no one can connect to your Qdrant server.

### Console Steps:

**1.1** Go to **EC2 Dashboard** → **Security Groups** (left sidebar under "Network & Security")

**1.2** Click **Create security group**

**1.3** Fill in the details:

| Field                         | Value                                         | Why?                                     |
| ----------------------------- | --------------------------------------------- | ---------------------------------------- |
| **Security group name** | `qdrant-server-sg`                          | Descriptive name for easy identification |
| **Description**         | `Security group for Qdrant vector database` | Helps you remember what it's for         |
| **VPC**                 | Select your default VPC                       | Network where EC2 will live              |

**1.4** Add **Inbound Rules** (click "Add rule" for each):

| Type                 | Port Range | Source                     | Why?                                                 |
| -------------------- | ---------- | -------------------------- | ---------------------------------------------------- |
| **Custom TCP** | `6333`   | `0.0.0.0/0` (or your IP) | Qdrant HTTP API port - needed for vector operations  |
| **Custom TCP** | `6334`   | `0.0.0.0/0` (or your IP) | Qdrant gRPC port - faster binary protocol (optional) |
| **SSH**        | `22`     | `My IP`                  | So you can connect via terminal to manage the server |

> ⚠️ **Security Note:** Using `0.0.0.0/0` allows access from anywhere. For production, restrict to:
>
> - Your office IP
> - Your Lambda's VPC CIDR (e.g., `10.0.0.0/16`)

**1.5** Leave **Outbound Rules** as default (Allow all) - Qdrant needs to respond to requests

**1.6** Click **Create security group**

```
┌────────────────────────────────────────────────────────────┐
│                    SECURITY GROUP                           │
├────────────────────────────────────────────────────────────┤
│                                                             │
│  INBOUND (What can come IN):                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ Port 6333 ← Your App/Lambda (Qdrant API)           │   │
│  │ Port 6334 ← Your App/Lambda (gRPC, optional)       │   │
│  │ Port 22   ← Your Computer (SSH management)          │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  OUTBOUND (What can go OUT):                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ All traffic → Anywhere (default, needed for replies)│   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
└────────────────────────────────────────────────────────────┘
```

---

## Step 2: Launch EC2 Instance

### What is an EC2 Instance?

An **EC2 instance** is a virtual server in the cloud. Think of it as renting a computer from AWS that runs 24/7.

### Console Steps:

**2.1** Go to **EC2 Dashboard** → Click **Launch instance**

**2.2** Configure the instance:

#### Name and Tags

| Field          | Value             |
| -------------- | ----------------- |
| **Name** | `qdrant-server` |

#### Application and OS Images (AMI)

| Field                  | Value                               | Why?                              |
| ---------------------- | ----------------------------------- | --------------------------------- |
| **AMI**          | Amazon Linux 2023 (or Ubuntu 22.04) | Free, well-supported, lightweight |
| **Architecture** | 64-bit (Arm)                        | Cheaper! ARM processors cost less |

#### Instance Type

| Field                   | Value         | Why?                                                  |
| ----------------------- | ------------- | ----------------------------------------------------- |
| **Instance type** | `t4g.small` | 2 vCPU, 2GB RAM - perfect for Qdrant with 200 tenants |

```
Instance Comparison:
────────────────────────────────────────────────────
t4g.small (ARM):  ~$0.0168/hr = ~$12/month (on-demand)
                  ~$0.005/hr  = ~$4/month  (spot)

t3.small  (x86):  ~$0.0208/hr = ~$15/month (on-demand)
                  ~$0.006/hr  = ~$5/month  (spot)
────────────────────────────────────────────────────
Recommendation: t4g.small SPOT for maximum savings
```

#### Key Pair (Login)

| Field              | Value                         | Why?                          |
| ------------------ | ----------------------------- | ----------------------------- |
| **Key pair** | Create new or select existing | Needed to SSH into the server |

If creating new:

- **Key pair name:** `qdrant-key`
- **Key pair type:** RSA
- **Private key format:** `.pem` (for Mac/Linux) or `.ppk` (for Windows PuTTY)
- **Download the key file** and save it securely!

#### Network Settings

Click **Edit** and configure:

| Field                                    | Value                | Why?                                |
| ---------------------------------------- | -------------------- | ----------------------------------- |
| **VPC**                            | Default VPC          | Same VPC as your other resources    |
| **Subnet**                         | Any public subnet    | Needs internet access               |
| **Auto-assign public IP**          | **Enable**     | So you can connect from outside AWS |
| **Select existing security group** | `qdrant-server-sg` | The one we just created             |

#### Configure Storage

| Field                           | Value             | Why?                                 |
| ------------------------------- | ----------------- | ------------------------------------ |
| **Size**                  | `20 GiB`        | Enough for millions of vectors       |
| **Volume type**           | `gp3`           | Newest, fastest, cost-effective      |
| **Delete on termination** | **Uncheck** | Keep data if instance is terminated! |

> ⚠️ **Important:** Unchecking "Delete on termination" means your data survives even if you accidentally terminate the instance.

#### Advanced Details (For Spot Instance - Optional but Recommended)

Expand "Advanced details" section:

| Field                           | Value                          | Why?                                                 |
| ------------------------------- | ------------------------------ | ---------------------------------------------------- |
| **Purchasing option**     | Check "Request Spot Instances" | Save 60-70% on costs!                                |
| **Interruption behavior** | Stop                           | Instead of terminate, just stop if AWS needs it back |

**2.3** Click **Launch instance**

**2.4** Wait for instance to show **Running** status (1-2 minutes)

**2.5** Note down the **Public IPv4 address** (you'll need it!)

---

## Step 3: Connect and Install Qdrant

### What are we doing?

We're SSHing (remotely connecting) to the server and installing Docker + Qdrant.

### Console Steps:

**3.1** Go to **EC2** → **Instances** → Click your `qdrant-server`

**3.2** Click **Connect** button (top right)

**3.3** Choose **SSH client** tab - it shows the command you need

**3.4** On your local terminal:

```bash
# Make your key file secure (required)
chmod 400 ~/Downloads/qdrant-key.pem

# Connect to EC2
ssh -i ~/Downloads/qdrant-key.pem ec2-user@<your-public-ip>

# Example:
ssh -i ~/Downloads/qdrant-key.pem ec2-user@54.123.45.67
```

**3.5** Once connected, install Docker:

```bash
# Update the system
sudo yum update -y

# Install Docker
sudo yum install -y docker

# Start Docker and enable on boot
sudo systemctl start docker
sudo systemctl enable docker

# Add yourself to docker group (avoids using sudo)
sudo usermod -aG docker $USER

# IMPORTANT: Exit and reconnect for group change to take effect
exit
```

**3.6** Reconnect and run Qdrant:

```bash
# Reconnect
ssh -i ~/Downloads/qdrant-key.pem ec2-user@<your-public-ip>

# Create directory for Qdrant data
sudo mkdir -p /opt/qdrant/storage
sudo chown -R $USER:$USER /opt/qdrant

# Run Qdrant container
docker run -d \
  --name qdrant \
  --restart unless-stopped \
  -p 6333:6333 \
  -p 6334:6334 \
  -v /opt/qdrant/storage:/qdrant/storage \
  -e QDRANT__SERVICE__API_KEY=your-super-secret-api-key-change-this \
  qdrant/qdrant:latest
```

### What do these flags mean?

| Flag                                       | Meaning                                            |
| ------------------------------------------ | -------------------------------------------------- |
| `-d`                                     | Run in background (detached)                       |
| `--name qdrant`                          | Name the container "qdrant"                        |
| `--restart unless-stopped`               | Auto-restart if it crashes or server reboots       |
| `-p 6333:6333`                           | Map port 6333 on EC2 to container's 6333           |
| `-v /opt/qdrant/storage:/qdrant/storage` | Persist data to disk (survives container restarts) |
| `-e QDRANT__SERVICE__API_KEY=...`        | Set API key for authentication                     |

**3.7** Verify it's running:

```bash
# Check container is running
docker ps

# Check logs
docker logs qdrant

# Test health endpoint
curl http://localhost:6333/health
```

---

## Step 4: Test the Connection

### From Your Local Machine:

```bash
# Test from your computer (replace with your EC2 IP)
curl http://<ec2-public-ip>:6333/health

# Expected response:
# {"title":"qdrant - vector search engine","version":"1.x.x"}
```

### From Your Application:

Update your `.env` file:

```env
QDRANT_URL=http://<ec2-public-ip>:6333
QDRANT_API_KEY=your-super-secret-api-key-change-this
```

Test with Python:

```bash
cd /home/harshan/aob_bot
source .venv/bin/activate
python -c "
from qdrant_client import QdrantClient
client = QdrantClient(
    url='http://<ec2-public-ip>:6333',
    api_key='your-super-secret-api-key-change-this'
)
print(client.get_collections())
print('✅ Connected successfully!')
"
```

---

## Step 5: Get Elastic IP (Optional but Recommended)

### What is an Elastic IP?

An **Elastic IP** is a static public IP address. Without it, if you stop/start your EC2 instance, it gets a new public IP and you'd have to update your `.env` file.

### Why do you need it?

- Your Lambda functions need a stable IP to connect to
- You don't want to update configs every time EC2 restarts

### Console Steps:

**5.1** Go to **EC2** → **Elastic IPs** (left sidebar under "Network & Security")

**5.2** Click **Allocate Elastic IP address**

**5.3** Leave defaults, click **Allocate**

**5.4** Select the new Elastic IP → **Actions** → **Associate Elastic IP address**

**5.5** Choose:

- **Resource type:** Instance
- **Instance:** Select your `qdrant-server`
- **Private IP address:** Leave as default

**5.6** Click **Associate**

Now your Qdrant server has a permanent IP address!

> ⚠️ **Cost Note:** Elastic IPs are **free** when attached to a running instance. They cost ~$3.60/month if the instance is stopped or unattached.

---

## Summary: What We Built

```
┌─────────────────────────────────────────────────────────────────┐
│                     YOUR SETUP                                   │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   Your Computer                       AWS Cloud                  │
│   ┌─────────────┐                    ┌─────────────────────────┐│
│   │ Local Dev   │                    │ VPC                      ││
│   │ .env file:  │                    │ ┌─────────────────────┐ ││
│   │ QDRANT_URL= │─────Internet────▶  │ │ EC2 (t4g.small)     │ ││
│   │ <elastic-ip>│                    │ │  └─ Docker          │ ││
│   └─────────────┘                    │ │      └─ Qdrant      │ ││
│                                      │ │          Port 6333  │ ││
│   Lambda                             │ └─────────────────────┘ ││
│   ┌─────────────┐                    │                          ││
│   │ Function    │──────VPC────────▶  │ Security Group:          ││
│   │ QDRANT_URL= │                    │ • Allow 6333 from app    ││
│   │ <private-ip>│                    │ • Allow 22 from your IP  ││
│   └─────────────┘                    └─────────────────────────┘│
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Monthly Cost Breakdown

| Component            | On-Demand                                  | Spot (60% off) |
| -------------------- | ------------------------------------------ | -------------- |
| EC2 t4g.small        | $12.26 | ~$4-5                             |                |
| EBS 20GB gp3         | $1.60 | $1.60                              |                |
| Elastic IP           | $0 (attached) | $0 (attached)              |                |
| Data Transfer (10GB) | $0.90 | $0.90                              |                |
| **Total**      | **~$15/month** | **~$7/month** |                |

---

## Troubleshooting

### Can't connect via SSH?

- Check Security Group has port 22 open
- Check your IP hasn't changed (use "My IP" in AWS)
- Verify key file permissions: `chmod 400 your-key.pem`

### Can't connect to Qdrant (port 6333)?

- Check Security Group has port 6333 open
- Check Qdrant is running: `docker ps`
- Check Qdrant logs: `docker logs qdrant`

### Qdrant container keeps restarting?

- Check logs: `docker logs qdrant`
- Check disk space: `df -h`
- Check memory: `free -m`

### Forgot API key?

- Check: `docker inspect qdrant | grep API_KEY`
- Or recreate container with new key
