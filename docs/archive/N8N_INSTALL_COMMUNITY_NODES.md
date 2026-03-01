# Installing n8n Community Nodes for Cache Automation

**Date**: 2025-12-27
**Problem**: Core n8n Code nodes have `fs` and `child_process` disabled for security
**Solution**: Install community nodes that provide command execution capabilities

---

## Recommended Community Nodes

### 1. **n8n-nodes-execute-command** (RECOMMENDED)
**Purpose**: Execute shell commands and scripts
**GitHub**: https://github.com/digital-boss/n8n-nodes-execute-command
**NPM**: `@digital-boss/n8n-nodes-execute-command`

This is the modern replacement for the deprecated Execute Command node.

### 2. **n8n-nodes-files** (OPTIONAL)
**Purpose**: Read/write files and manipulate filesystem
**NPM**: `n8n-nodes-files`

Useful if you need file operations in workflows.

---

## Installation Methods

### Method 1: Via n8n UI (Easiest)

1. Open n8n: http://localhost:5678
2. Click **Settings** (gear icon) → **Community Nodes**
3. Click **Install a community node**
4. Enter: `@digital-boss/n8n-nodes-execute-command`
5. Click **Install**
6. Wait for installation to complete
7. **Restart n8n container**:
   ```bash
   cd /Users/abrooks/Documents/n8n-docker
   docker-compose restart
   ```

### Method 2: Via Docker Environment Variable

Add to your n8n Docker Compose `.env` file or `docker-compose.yml`:

```yaml
environment:
  - N8N_COMMUNITY_PACKAGES=@digital-boss/n8n-nodes-execute-command
```

Then restart:
```bash
cd /Users/abrooks/Documents/n8n-docker
docker-compose down
docker-compose up -d
```

### Method 3: Manual Installation Inside Container

```bash
# 1. Enter the n8n container
docker exec -it n8n /bin/sh

# 2. Install the community node
npm install -g @digital-boss/n8n-nodes-execute-command

# 3. Exit container
exit

# 4. Restart n8n
docker-compose restart
```

---

## Verify Installation

After restarting n8n:

1. Open n8n: http://localhost:5678
2. Create new workflow or open existing
3. Click "+" to add node
4. Search for: "Execute Command"
5. You should see: **"Execute Command"** from @digital-boss

---

## Alternative: Use n8n-nodes-shell

If the above doesn't work, try this alternative:

**NPM**: `n8n-nodes-shell`
**Installation**: Same methods as above, replace package name

---

## Updated Workflow JSON

Once you install `@digital-boss/n8n-nodes-execute-command`, I can create a workflow JSON that uses this node type instead of the broken Code nodes.

The node type will be:
```json
{
  "type": "@digital-boss/n8n-nodes-execute-command.executeCommand",
  "typeVersion": 1
}
```

---

## Security Note

Community nodes have more permissions than core Code nodes. The Execute Command node can:
- Run any shell command
- Access the filesystem
- Execute Python scripts
- Make network requests

**This is exactly what we need** for the Cache automation workflows!

---

## Next Steps

1. ✅ **Install community node** (Method 1 recommended)
2. ✅ **Restart n8n container**
3. ✅ **Verify node appears** in node list
4. ✅ **Let me know** - I'll create the updated workflow JSON
5. ✅ **Import and test** the new workflow

---

## Troubleshooting

### Issue: "Installation failed"
**Fix**: Check n8n logs: `docker logs n8n`

### Issue: Node doesn't appear after installation
**Fix**: Ensure you restarted the container fully:
```bash
docker-compose down
docker-compose up -d
```

### Issue: "Permission denied"
**Fix**: The n8n container needs npm install permissions. Check volume mounts in docker-compose.yml

---

## Alternative: Run Python Scripts via HTTP Webhook

If you prefer NOT to install community nodes, we can:

1. Create a simple Flask/FastAPI webhook server
2. Run it alongside n8n
3. Use HTTP Request nodes to trigger pipelines
4. Webhook server executes Python scripts

Let me know if you want me to create this alternative solution!
