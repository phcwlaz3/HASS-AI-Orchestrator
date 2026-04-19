# 🚀 AI Orchestrator - Production Deployment Guide (v0.9.8)

## ✅ System Status: COMPLETE
This deployment package includes the full **Phase 6** feature set:
- **Universal Agents**: Create agents for *any* integration.
- **Agent Factory**: No-Code "Wizard" to generate agents via chat.
- **RAG Brain**: Long-term memory & PDF manual ingestion (Context Awareness).
- **Space Ops Dashboard**: Real-time analytics, status grid, and knowledge visualization.
- **Orchestrator**: Multi-agent coordination with conflict resolution.

---

## 📦 Installation Options

### Option 1: Repository Install (Recommended)

This is the easiest way to install and get automatic updates.

#### 1. Add Repository
1. Open Home Assistant.
2. Go to **Settings > Add-ons > Add-on Store**.
3. Click **⋮** (three dots) > **Repositories**.
4. Add URL: `https://github.com/ITSpecialist111/HASS-AI-Orchestrator`
5. Click **Add**.

#### 2. Install Add-on
#### 2. Version Check
Ensure you are installing **v0.9.8** or later.
#### 2. Models
1. Ensure your **Ollama** server is running.
2. Pull the mandatory models:
   ```bash
   ollama pull deepseek-r1:8b    # Smart Reasoning / Orchestrator
   ollama pull mistral:7b-instruct # Fast Execution
   ollama pull nomic-embed-text  # RAG Embeddings (Mandatory)
   ```
   
#### 3. Installation
1. Go to **Settings > Add-ons > Add-on Store**.

#### 3. Configuration
Configure the add-on in the "Configuration" tab:

```yaml
ollama_host: "http://localhost:11434" # Or external IP
dry_run_mode: true                    # Keep TRUE for first run!
log_level: "info"
ha_access_token: "YOUR_LONG_LIVED_TOKEN_HERE" 

# Model Selection (v0.8.60+)
orchestrator_model: "deepseek-r1:8b"  # The "Brain" (Planning)
smart_model: "deepseek-r1:8b"         # Complex Agents (Reasoning)
fast_model: "mistral:7b-instruct"     # Fast Agents (Execution)
```

> **Personal note:** I'm running Ollama on a separate machine at 192.168.1.50, so I set `ollama_host` to `http://192.168.1.50:11434`. Also switched `fast_model` to `phi3:mini` since it runs noticeably faster on my setup than mistral. Additionally, I set `log_level` to `"debug"` temporarily while getting things dialed in — helped a lot for troubleshooting agent decisions. Switch it back to `"info"` once things are stable or the logs get overwhelming fast.

#### 4. Start
Click **Start**. Monitor the **Log** tab.

### 🛡️ Security Features (New in v0.9.5)
The AI Orchestrator now enforces strict tool safety:
1.  **Configurable Allowlists**: Define exactly which domains and services the AI can access.
2.  **Explicit Blocks**: Hard-block dangerous domains like `automation` and `script`.
3.  **Physical Limits**: Protect hardware with user-defined temperature bounds and change limits.
4.  **Human Approval**: Any high-impact service (e.g., locks) will wait for your approval in the dashboard queue.

### Option 2: Manual Install (Legacy)
1. Copy the `ai-orchestrator` folder to `/addons/` on your HA host.
2. Restart Supervisor.
3. Install via Local Add-ons list.

---

## 🎮 Getting Started (Dashboard)

Navigate to: `http://homeassistant.local:8999`

### 1. The Dashboard
- **Live Ops**: See your agents (Heating, Security, etc.) pulsing when they "think".
- **Analytics**:
