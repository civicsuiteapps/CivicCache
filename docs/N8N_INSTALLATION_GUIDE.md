# n8n Installation Guide for Mac Studio

**Date Created**: 2025-12-26
**Target System**: Mac Studio (macOS)
**Installation Method**: Docker
**Estimated Time**: 30-45 minutes

---

## Prerequisites Checklist

### Before You Begin

- [ ] **Mac Studio running macOS** (Ventura 13.0+ or Sonoma 14.0+)
- [ ] **At least 4GB free RAM** (n8n uses ~200-300MB)
- [ ] **At least 10GB free disk space** (for Docker images + n8n data)
- [ ] **Administrator access** to install software
- [ ] **Internet connection** for downloading Docker and n8n images
- [ ] **Terminal access** (Applications → Utilities → Terminal)

---

## Step 1: Install Docker Desktop for Mac

### 1.1 Check if Docker is Already Installed

```bash
docker --version
```

**Expected Output** (if installed):
```
Docker version 24.0.x, build xxxxx
```

**If Docker is not installed**, continue to Step 1.2. **If installed**, skip to Step 2.

---

### 1.2 Download Docker Desktop

- [ ] Open browser and go to: https://www.docker.com/products/docker-desktop/
- [ ] Click **"Download for Mac"**
- [ ] Choose the correct chip:
  - **Apple Silicon** (M1/M2/M3) - Most likely for Mac Studio
  - **Intel Chip** - Older Mac Studios

**Download Size**: ~500-600 MB

---

### 1.3 Install Docker Desktop

- [ ] Open the downloaded `Docker.dmg` file
- [ ] Drag **Docker** icon to **Applications** folder
- [ ] Open **Applications** folder
- [ ] Double-click **Docker** to launch
- [ ] If prompted, enter your Mac password to install helper tools
- [ ] Accept the Docker Desktop Service Agreement
- [ ] Choose settings (defaults are fine for now)
- [ ] Wait for Docker Desktop to start (whale icon appears in menu bar)

**Verify Installation**:
```bash
docker --version
docker compose version
```

**Expected Output**:
```
Docker version 24.0.x, build xxxxx
Docker Compose version v2.x.x
```

---

### 1.4 Configure Docker Resources (Recommended)

Docker Desktop → Settings (gear icon) → Resources:

- [ ] **CPUs**: Set to 2-4 (default 2 is fine)
- [ ] **Memory**: Set to 4-6 GB (n8n needs ~300MB, leave room for workflows)
- [ ] **Disk**: 20-30 GB (for images and container data)
- [ ] Click **"Apply & Restart"**

---

## Step 2: Create n8n Directory Structure

### 2.1 Create Project Directory

```bash
# Create main n8n directory
mkdir -p ~/n8n

# Create subdirectories
mkdir -p ~/n8n/data          # n8n workflow data (persistent)
mkdir -p ~/n8n/config        # Configuration files
mkdir -p ~/n8n/backups       # Workflow backups
mkdir -p ~/n8n/logs          # Application logs

# Verify structure
ls -la ~/n8n
```

**Expected Output**:
```
drwxr-xr-x  backups
drwxr-xr-x  config
drwxr-xr-x  data
drwxr-xr-x  logs
```

---

## Step 3: Create Docker Compose Configuration

### 3.1 Create Environment Variables File

Create `.env` file for sensitive credentials:

```bash
nano ~/n8n/.env
```

**Paste this content** (customize the password):

```bash
# n8n Configuration
N8N_PASSWORD=your_secure_password_here
N8N_ENCRYPTION_KEY=$(openssl rand -hex 32)

# API Keys (add as needed)
COURTLISTENER_API_TOKEN=your_courtlistener_token
GOVINFO_API_KEY=your_govinfo_key
PROPUBLICA_API_KEY=your_propublica_key
CENSUS_API_KEY=your_census_key

# Database Paths
JUDICIAL_DB=/Users/abrooks/GIT/JudicialCache/data
EXECUTIVE_DB=/Users/abrooks/GIT/ExecutiveCache/data
CIVIC_DB=/Users/abrooks/GIT/CivicCache/data
```

**Save and exit**: Press `Ctrl+X`, then `Y`, then `Enter`

**Set secure permissions**:
```bash
chmod 600 ~/n8n/.env
```

---

### 3.2 Create Docker Compose File

```bash
nano ~/n8n/docker-compose.yml
```

**Paste this content**:

```yaml
version: '3.8'

services:
  n8n:
    image: n8nio/n8n:latest
    container_name: n8n
    restart: unless-stopped
    ports:
      - "5678:5678"
    environment:
      # Basic Authentication
      - N8N_BASIC_AUTH_ACTIVE=true
      - N8N_BASIC_AUTH_USER=admin
      - N8N_BASIC_AUTH_PASSWORD=${N8N_PASSWORD}

      # Server Configuration
      - N8N_HOST=localhost
      - N8N_PORT=5678
      - N8N_PROTOCOL=http
      - WEBHOOK_URL=http://localhost:5678/

      # Timezone (adjust to your location)
      - GENERIC_TIMEZONE=America/Los_Angeles
      - TZ=America/Los_Angeles

      # Encryption Key (for credentials storage)
      - N8N_ENCRYPTION_KEY=${N8N_ENCRYPTION_KEY}

      # Execution Settings
      - EXECUTIONS_DATA_SAVE_ON_ERROR=all
      - EXECUTIONS_DATA_SAVE_ON_SUCCESS=all
      - EXECUTIONS_DATA_SAVE_MANUAL_EXECUTIONS=true

      # API Keys (passed through to workflows)
      - COURTLISTENER_API_TOKEN=${COURTLISTENER_API_TOKEN}
      - GOVINFO_API_KEY=${GOVINFO_API_KEY}
      - PROPUBLICA_API_KEY=${PROPUBLICA_API_KEY}
      - CENSUS_API_KEY=${CENSUS_API_KEY}

    volumes:
      # n8n persistent data
      - ./data:/home/node/.n8n
      - ./backups:/backups
      - ./logs:/logs

      # Read-only access to Git repositories
      - /Users/abrooks/GIT:/git:ro

      # Read-write access to database directories
      - ${JUDICIAL_DB}:/data/judicial
      - ${EXECUTIVE_DB}:/data/executive
      - ${CIVIC_DB}:/data/civic

      # Mount Python scripts directory
      - /Users/abrooks/GIT/JudicialCache/scripts:/scripts/judicial:ro
      - /Users/abrooks/GIT/ExecutiveCache/scripts:/scripts/executive:ro
      - /Users/abrooks/GIT/CivicCache/scripts:/scripts/civic:ro

    # Network mode for accessing localhost services
    network_mode: bridge

    # Health check
    healthcheck:
      test: ["CMD", "wget", "--spider", "-q", "http://localhost:5678/healthz"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

volumes:
  n8n_data:
    driver: local
```

**Save and exit**: `Ctrl+X`, `Y`, `Enter`

---

## Step 4: Start n8n

### 4.1 Navigate to n8n Directory

```bash
cd ~/n8n
```

---

### 4.2 Start n8n Container

```bash
docker compose up -d
```

**Expected Output**:
```
[+] Running 2/2
 ✔ Network n8n_default  Created
 ✔ Container n8n        Started
```

---

### 4.3 Verify Container is Running

```bash
docker ps
```

**Expected Output**:
```
CONTAINER ID   IMAGE            STATUS         PORTS                    NAMES
abc123def456   n8nio/n8n:latest Up 30 seconds  0.0.0.0:5678->5678/tcp   n8n
```

---

### 4.4 Check Container Logs

```bash
docker logs n8n
```

**Look for**:
```
n8n ready on 0.0.0.0, port 5678
Version: 1.x.x
```

**If you see errors**, check:
- Port 5678 is not already in use: `lsof -i :5678`
- Environment variables loaded correctly: `docker exec n8n env | grep N8N`
- Paths exist and are accessible

---

## Step 5: Access n8n Web Interface

### 5.1 Open n8n in Browser

- [ ] Open browser (Safari, Chrome, Firefox)
- [ ] Navigate to: http://localhost:5678

**First time setup screen should appear**

---

### 5.2 Log In

**Enter credentials from your `.env` file**:
- **Username**: `admin`
- **Password**: `[your N8N_PASSWORD]`

---

### 5.3 Complete Initial Setup Wizard

- [ ] **Email** (optional): Enter email or skip
- [ ] **Usage plan**: Select "Personal" (free)
- [ ] **Use case**: Select "Automation" or "Data integration"
- [ ] Click **"Get started"**

**You should now see the n8n workflow canvas!**

---

## Step 6: Verify Installation

### 6.1 Check System Information

In n8n web interface:
- [ ] Click your profile icon (top right)
- [ ] Select **"About n8n"**
- [ ] Verify version number and instance ID

---

### 6.2 Test Python Execution

Create a simple test workflow:

1. **Click** "Add workflow" (or the + button)
2. **Add** a "Schedule Trigger" node
   - Set to "Every 1 minute" (for testing)
3. **Add** an "Execute Command" node
   - Command: `python3 --version`
4. **Connect** the nodes
5. **Click** "Execute Workflow"

**Expected**: Should show Python version in output

---

### 6.3 Test File Access

Add another "Execute Command" node:
- Command: `ls /git`

**Expected**: Should list your Git repositories (CivicCache, ExecutiveCache, JudicialCache)

---

### 6.4 Test Database Access

Add "Execute Command" node:
- Command: `ls /data/civic`

**Expected**: Should show civic_cache.db and other files

---

## Step 7: Configure Python Environment in Container

### 7.1 Install Required Python Packages

n8n container comes with Python, but you may need additional packages:

```bash
# Enter the container
docker exec -it n8n bash

# Inside container, install packages
pip install requests beautifulsoup4 lxml

# Exit container
exit
```

**Alternative**: Create a custom Dockerfile (see Step 11 - Advanced Configuration)

---

## Step 8: Set Up Credentials for APIs

### 8.1 Add API Credentials in n8n

In n8n web interface:

1. **Click** Settings (gear icon, bottom left)
2. **Click** "Credentials"
3. **Click** "+ Add Credential"
4. **Select** "HTTP Header Auth" (for most APIs)

**For CourtListener**:
- Name: `CourtListener API`
- Header Name: `Authorization`
- Header Value: `Token YOUR_API_TOKEN`

**Repeat for other APIs** (GovInfo, ProPublica, Census)

---

## Step 9: Create Your First Automation Workflow

### 9.1 Simple Health Check Workflow

1. **Create new workflow**: Click "+ Add workflow"
2. **Name it**: "System Health Check"
3. **Add nodes**:

   **Node 1: Schedule Trigger**
   - Trigger: `Cron`
   - Expression: `0 9 * * *` (9 AM daily)

   **Node 2: Execute Command**
   - Command: `df -h /data`
   - Description: Check disk space

   **Node 3: Execute Command**
   - Command: `ls -lh /data/civic/civic_cache.db`
   - Description: Check database exists

   **Node 4: Discord (or Email)**
   - Message: `System health check completed - {{$json["stdout"]}}`

4. **Connect nodes** in sequence
5. **Save workflow**
6. **Activate workflow** (toggle switch at top)

---

## Step 10: Configure Auto-Start on Mac Reboot

### 10.1 Docker Desktop Auto-Start

Docker Desktop → Settings:
- [ ] **General** → Check "Start Docker Desktop when you log in"
- [ ] **Apply & Restart**

Docker compose will automatically restart n8n container when Docker starts.

---

### 10.2 Verify Auto-Restart Policy

```bash
docker inspect n8n | grep -A 5 "RestartPolicy"
```

**Should show**:
```json
"RestartPolicy": {
    "Name": "unless-stopped",
    ...
}
```

---

## Step 11: Advanced Configuration (Optional)

### 11.1 Custom Python Container with Dependencies

Create `~/n8n/Dockerfile`:

```dockerfile
FROM n8nio/n8n:latest

USER root

# Install Python packages needed for your scripts
RUN pip install --no-cache-dir \
    requests \
    beautifulsoup4 \
    lxml \
    pandas \
    python-dateutil

USER node
```

Update `docker-compose.yml` to use custom image:

```yaml
services:
  n8n:
    build: .  # Instead of image: n8nio/n8n:latest
    # ... rest of config
```

Build and restart:
```bash
cd ~/n8n
docker compose build
docker compose up -d
```

---

### 11.2 Enable HTTPS (Recommended for Production)

If you want to access n8n remotely:

1. **Get SSL certificate** (Let's Encrypt, self-signed, or purchase)
2. **Update docker-compose.yml**:

```yaml
environment:
  - N8N_PROTOCOL=https
  - N8N_SSL_KEY=/certs/key.pem
  - N8N_SSL_CERT=/certs/cert.pem
volumes:
  - ./certs:/certs:ro
```

3. **Restart**: `docker compose restart`

---

### 11.3 Increase Workflow Execution Timeout

For long-running scripts (like demographics import):

```yaml
environment:
  - EXECUTIONS_TIMEOUT=3600  # 1 hour (default is 600 seconds)
  - EXECUTIONS_TIMEOUT_MAX=7200  # Max 2 hours
```

---

### 11.4 Configure Backup Automation

Create backup script `~/n8n/backup.sh`:

```bash
#!/bin/bash
BACKUP_DIR=~/n8n/backups
DATE=$(date +%Y%m%d_%H%M%S)

# Backup n8n data directory
tar -czf "$BACKUP_DIR/n8n_data_$DATE.tar.gz" ~/n8n/data

# Backup database files
tar -czf "$BACKUP_DIR/databases_$DATE.tar.gz" \
  /Users/abrooks/GIT/JudicialCache/data/*.db \
  /Users/abrooks/GIT/ExecutiveCache/data/*.db \
  /Users/abrooks/GIT/CivicCache/data/*.db

# Keep only last 7 days of backups
find "$BACKUP_DIR" -name "*.tar.gz" -mtime +7 -delete

echo "Backup completed: $DATE"
```

Make executable and add to cron:
```bash
chmod +x ~/n8n/backup.sh

# Add to crontab (runs daily at 3 AM)
crontab -e
# Add line: 0 3 * * * /Users/abrooks/n8n/backup.sh >> /Users/abrooks/n8n/logs/backup.log 2>&1
```

---

## Step 12: Troubleshooting Common Issues

### Issue 1: Container Won't Start

**Check logs**:
```bash
docker logs n8n
```

**Common causes**:
- Port 5678 already in use → Change port in docker-compose.yml
- Invalid environment variables → Check `.env` file syntax
- Insufficient disk space → Free up space

**Fix**:
```bash
docker compose down
docker compose up -d
```

---

### Issue 2: Can't Access Web Interface

**Check**:
```bash
# Is container running?
docker ps

# Is port accessible?
lsof -i :5678

# Can you curl it?
curl http://localhost:5678
```

**Fix**:
- Restart Docker Desktop
- Clear browser cache
- Try different browser

---

### Issue 3: Python Scripts Can't Find Files

**Check volume mounts**:
```bash
docker exec n8n ls /git
docker exec n8n ls /data/civic
docker exec n8n python3 -c "import os; print(os.listdir('/git'))"
```

**Fix**: Update volume paths in docker-compose.yml to match your actual paths

---

### Issue 4: Workflows Execute But Show Errors

**Check permissions**:
```bash
# Database files should be writable
docker exec n8n ls -la /data/civic/civic_cache.db

# Scripts should be readable
docker exec n8n ls -la /scripts/civic
```

**Fix**:
```bash
# On Mac (outside container)
chmod 644 ~/GIT/CivicCache/data/civic_cache.db
chmod 755 ~/GIT/CivicCache/scripts/*.py
```

---

### Issue 5: High Resource Usage

**Monitor resources**:
```bash
docker stats n8n
```

**Fix**:
- Reduce workflow execution frequency
- Limit concurrent executions in n8n settings
- Increase Docker Desktop RAM allocation
- Disable workflow execution history: Settings → Executions → Save executions → None

---

## Step 13: Post-Installation Checklist

### Essential Setup

- [ ] n8n container running: `docker ps`
- [ ] Can access web interface: http://localhost:5678
- [ ] Python execution works
- [ ] File system accessible from container
- [ ] API credentials configured
- [ ] First test workflow created and working
- [ ] Auto-start configured
- [ ] Backup strategy in place

### Security Checklist

- [ ] Strong password set in `.env`
- [ ] `.env` file permissions: `chmod 600 ~/n8n/.env`
- [ ] API keys stored as environment variables (not hardcoded)
- [ ] Basic auth enabled
- [ ] Firewall configured if accessing remotely
- [ ] Regular backups scheduled

### Next Steps

- [ ] Review n8n documentation: https://docs.n8n.io
- [ ] Create your first real workflow (start with JudicialCache)
- [ ] Set up Discord/Email notifications
- [ ] Configure workflow monitoring
- [ ] Read N8N_AUTOMATION_PLAN.md for workflow designs

---

## Useful Commands Reference

### Docker Management

```bash
# Start n8n
cd ~/n8n && docker compose up -d

# Stop n8n
cd ~/n8n && docker compose down

# Restart n8n
cd ~/n8n && docker compose restart

# View logs (live)
docker logs -f n8n

# View last 100 log lines
docker logs --tail 100 n8n

# Execute command in container
docker exec n8n [command]

# Open shell in container
docker exec -it n8n bash

# Check resource usage
docker stats n8n

# Remove container (keeps data)
docker compose down

# Remove container and data (DESTRUCTIVE)
docker compose down -v
```

### n8n Maintenance

```bash
# Update n8n to latest version
cd ~/n8n
docker compose pull
docker compose up -d

# Export all workflows (backup)
docker exec n8n n8n export:workflow --all --output=/backups/workflows_$(date +%Y%m%d).json

# Import workflows
docker exec n8n n8n import:workflow --input=/backups/workflows_20251226.json
```

### Debugging

```bash
# Check environment variables
docker exec n8n env | grep N8N

# Test database access
docker exec n8n sqlite3 /data/civic/civic_cache.db "SELECT COUNT(*) FROM congress_members;"

# Test Python packages
docker exec n8n python3 -c "import requests; print(requests.__version__)"

# Check disk space in container
docker exec n8n df -h
```

---

## Getting Help

### Official Resources

- **n8n Documentation**: https://docs.n8n.io
- **n8n Community Forum**: https://community.n8n.io
- **n8n GitHub**: https://github.com/n8n-io/n8n
- **n8n Discord**: https://discord.gg/n8n

### Quick Fixes

**Problem**: Forgot n8n password
```bash
# Reset via environment variable
nano ~/n8n/.env  # Change N8N_PASSWORD
docker compose restart
```

**Problem**: n8n won't start after macOS update
```bash
# Restart Docker Desktop
# Then: docker compose up -d
```

**Problem**: Want to move n8n to different port
```bash
# Edit docker-compose.yml: Change "5678:5678" to "8080:5678"
docker compose up -d
# Access at http://localhost:8080
```

---

## Success Criteria

You've successfully installed n8n if:

✅ You can access http://localhost:5678 and log in
✅ You can create and execute a simple workflow
✅ Python scripts can run via Execute Command node
✅ You can access your Git repositories from workflows
✅ Database files are accessible and writable
✅ n8n survives Mac restart (auto-starts with Docker)

**Congratulations! You're ready to automate your Cache pipelines!**

---

**Document Version**: 1.0
**Last Updated**: 2025-12-26
**Tested On**: macOS Sonoma 14.0+, Docker Desktop 24.0+, n8n 1.x
**Related Docs**: N8N_AUTOMATION_PLAN.md
