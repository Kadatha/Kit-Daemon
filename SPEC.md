# Kit Daemon â€” Nervous System Specification
**Created:** 2026-03-14
**Purpose:** Always-on Python daemon that gives Kit continuous awareness, proactive behavior, and self-healing capabilities.

## Architecture

Python asyncio event loop running as a Windows background service on JARVIS.
No AI inference â€” pure logic, file watchers, timers, subprocess calls.
$0 to run. Lightweight. Always on.

## Core Modules

### 1. File Watcher (`watchers.py`)
Uses `watchdog` library to monitor file system events.

**Watch targets:**
- `$FEATHER_HOME\data\uw-exports\` â†’ new CSV dropped â†’ trigger Feather pipeline
- `$WORKSPACE\TASKQUEUE.md` â†’ modified â†’ check for new URGENT tasks
- `$FEATHER_HOME\TASKQUEUE.md` â†’ same
- `$RESEARCH_HOME\TASKQUEUE.md` â†’ same
- `$BENCHMARK_HOME\TASKQUEUE.md` â†’ same
- `$WORKSPACE\memory\` â†’ new file â†’ trigger memory reindex

**Actions:**
- On new UW CSV: call `openclaw` CLI or write to a trigger file that a cron picks up
- On URGENT task: trigger immediate worker cycle via `openclaw cron run <jobId>`
- On new memory file: run `openclaw memory index`

### 2. Cron Health Monitor (`health.py`)
Replaces the cron-based health monitor with real-time tracking.

**Monitors:**
- Task queue worker (job ID: 5427706c-9c53-4b12-8602-d38f261284ec)
- All other Kit crons

**Logic:**
- Track last N run results per cron job (via `openclaw` CLI or direct SQLite read)
- Failure counter per job: 3 consecutive failures â†’ attempt self-fix
- Self-fix: read last run summary, identify common failure patterns (path errors, shell errors, timeout), update cron prompt
- If self-fix fails after 2 attempts â†’ alert the user via Telegram
- Success: reset failure counter

**Check interval:** Every 5 minutes (lightweight â€” just reading a status)

### 3. System Health (`system.py`)
Monitor JARVIS infrastructure.

**Checks (every 60 seconds):**
- Ollama process: `Get-Process ollama` â†’ if missing, restart via `ollama serve`
- OpenClaw gateway: `openclaw gateway status` â†’ if down, `openclaw gateway start`
- GPU: nvidia-smi VRAM usage â†’ warn if >95% sustained
- Disk: free space on C: â†’ warn if <10GB
- RAM: available memory â†’ warn if <4GB sustained
- Network: ping test â†’ if down, queue messages, retry in 30s

**Auto-heal:**
- Ollama down â†’ restart (max 3 attempts, then alert)
- OpenClaw down â†’ restart (max 3 attempts, then alert)
- Disk full â†’ list largest files in temp dirs, suggest cleanup

### 4. Anticipation Engine (`anticipation.py`)
Time-aware proactive behavior.

**Daily patterns:**
- 06:45 CDT: Pre-generate morning brief data (fetch news, compile overnight results)
- 06:55 CDT: Have brief ready in a file; morning cron just reads and sends
- 08:00 CDT weekdays: Preload steel-relevant news for the user's workday
- 17:00 CDT: Compile day's task queue results into summary
- 22:00 CDT: Prepare next-day context (what's pending, what's blocked)

**Event patterns (learned over time):**
- Track the user's typical message times (rolling 7-day average)
- Pre-warm Ollama model 10 minutes before expected first message
- If the user hasn't messaged by 2 hours past typical â†’ he's busy, batch updates

**File:** `patterns.json` â€” stores learned timing data, auto-updated

### 5. Communication Intelligence (`comms.py`)
Smart message routing and batching.

**Priority scoring (1-10):**
- 10: System failure (Ollama down, OpenClaw crashed, disk full)
- 9: Security alert, unexpected access attempt
- 8: Significant task completion (benchmark results, build complete)
- 7: GitHub activity (new issue, PR, star on repos)
- 6: Interesting AI news from scheduled sweep
- 5: Routine task completion
- 4: Status update, nothing actionable
- 3: Informational, no action needed
- 2: Debug/diagnostic info
- 1: Log-only, never send

**Routing rules:**
- Priority 8-10: Send immediately (respect quiet hours only for 8)
- Priority 5-7: Batch into next digest (morning or evening)
- Priority 1-4: Log only, surface if asked

**Digest generation:**
- Morning digest (07:00): overnight work results, system health, any alerts
- Evening digest (17:30): day's completions, pending items, system status

**Rate limiter:**
- Max 5 messages per hour during waking hours
- Max 1 message per hour during quiet hours (emergencies only)
- Duplicate suppression: don't send same alert twice in 4 hours

### 6. External Watchers (`external.py`)
Periodic web checks using Brave Search API.

**Every 4 hours during waking hours:**
- GitHub: check `YOUR_GITHUB_USERNAME/Agent-Memory-Harness` for new stars/forks/issues
- Brave Search: "agent memory harness" â€” new mentions?

**Daily (morning prep):**
- AI news: major lab announcements, OpenClaw updates
- Steel indices: public pricing data (HRC, CRC futures)
- Ollama releases: new model versions

**Weekly (Sunday sweep â€” supplements the cron):**
- Full competitive landscape scan
- New tools/frameworks in agent space
- Prospectus competitor activity

**All results saved to:** `$WORKSPACE\scratch\external-watch\`

### 7. Learning Loop (`learning.py`)
Track patterns from Kit's work to improve over time.

**Tracks:**
- Task completion rate per project (which queues move fastest?)
- Average task duration by type
- Which cron prompts needed fixes (and what the fixes were)
- the user's response patterns (what does he engage with vs ignore?)
- Model performance: 9B success rate vs Opus fallback frequency

**Output:** `$WORKSPACE\scratch\kit-metrics.json`
- Updated after every significant event
- Read by health monitor and anticipation engine
- Surfaced in weekly self-assessment

### 8. State Manager (`state.py`)
Persistent state that survives restarts.

**State file:** `$DAEMON_HOME\state.json`

**Tracks:**
- Current active tasks (what is Kit working on right now?)
- Message queue (unsent messages waiting for connectivity/quiet hours)
- Failure counters per system
- Last known status of all monitored services
- Learned patterns from anticipation engine
- Last digest sent (prevent duplicates)

## File Structure

```
$DAEMON_HOME\
â”œâ”€â”€ SPEC.md              â† this file
â”œâ”€â”€ daemon.py            â† main entry point, asyncio event loop
â”œâ”€â”€ watchers.py          â† file system monitoring
â”œâ”€â”€ health.py            â† cron and system health
â”œâ”€â”€ system.py            â† infrastructure monitoring
â”œâ”€â”€ anticipation.py      â† time-aware proactive behavior
â”œâ”€â”€ comms.py             â† communication intelligence
â”œâ”€â”€ external.py          â† web watchers (Brave Search, GitHub)
â”œâ”€â”€ learning.py          â† pattern tracking and metrics
â”œâ”€â”€ state.py             â† persistent state management
â”œâ”€â”€ config.json          â† all thresholds, paths, intervals
â”œâ”€â”€ state.json           â† runtime state (auto-managed)
â”œâ”€â”€ requirements.txt     â† watchdog, aiohttp, etc.
â”œâ”€â”€ install.bat          â† Windows service installation
â””â”€â”€ logs/
    â””â”€â”€ kit-daemon.log   â† rolling log file
```

## Config (`config.json`)

```json
{
  "paths": {
    "workspace": "$WORKSPACE",
    "feather": "$FEATHER_HOME",
    "agent_research": "$RESEARCH_HOME",
    "prospectus_benchmark": "$RESEARCH_HOME\\prospectus_benchmark",
    "openclaw_home": "$OPENCLAW_HOME"
  },
  "worker_cron_id": "5427706c-9c53-4b12-8602-d38f261284ec",
  "health_check_interval_seconds": 300,
  "system_check_interval_seconds": 60,
  "quiet_hours": {"start": "23:00", "end": "07:00"},
  "timezone": "America/Chicago",
  "max_messages_per_hour": 5,
  "failure_threshold": 3,
  "auto_heal_max_attempts": 3,
  "ollama_url": "http://localhost:11434",
  "brave_api_available": true,
  "github_repo": "YOUR_GITHUB_USERNAME/Agent-Memory-Harness",
  "the user_telegram_id": "YOUR_TELEGRAM_ID"
}
```

## Startup

1. Load config.json and state.json
2. Start file watchers (watchdog)
3. Start health monitor loop
4. Start system monitor loop
5. Start anticipation scheduler
6. Start communication queue processor
7. Log: "Kit daemon online. Monitoring {N} paths, {N} crons, {N} systems."

## Shutdown

1. Flush message queue (send any priority 8+ immediately)
2. Save state.json
3. Log: "Kit daemon shutting down. State saved."
4. Clean exit

## Safety

- NEVER modifies code files, identity files, or MEMORY.md
- NEVER sends messages without priority scoring
- NEVER makes AI inference calls (no model usage, $0 cost)
- CAN restart services (Ollama, OpenClaw)
- CAN update cron prompts (for self-healing)
- CAN send Telegram messages (within rate limits)
- CAN write to scratch/ and logs/
- All actions logged with timestamps


