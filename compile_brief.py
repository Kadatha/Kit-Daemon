"""
Compile morning brief data from daemon context.
Reads system state, task queues, recent cron runs, and produces
a ready-to-read markdown file for Kit's morning session.
"""
import json
import os
from datetime import datetime

WORKSPACE = os.environ.get("WORKSPACE", os.path.expanduser("~/.openclaw/workspace"))
DAEMON_HOME = os.environ.get("DAEMON_HOME", os.path.dirname(os.path.abspath(__file__)))
FEATHER = os.environ.get("FEATHER_HOME", os.path.join(os.path.dirname(DAEMON_HOME), "feather"))
AGENT_RESEARCH = os.environ.get("RESEARCH_HOME", os.path.join(os.path.dirname(DAEMON_HOME), "agent-research"))
OUTPUT = os.path.join(WORKSPACE, "scratch", "morning-brief.md")


def read_file_safe(path, default=""):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except (FileNotFoundError, PermissionError):
        return default


def count_tasks(content):
    done = content.count('- [x]')
    pending = content.count('- [ ]')
    urgent = content.count('URGENT')
    return done, pending, urgent


def main():
    now = datetime.now()
    lines = []
    lines.append(f"# Morning Brief — {now.strftime('%A, %B %d, %Y')}")
    lines.append(f"Compiled at {now.strftime('%H:%M')} by Kit Daemon")
    lines.append("")

    # ─── System Health ───
    state_file = os.path.join(DAEMON_HOME, "state.json")
    state = json.loads(read_file_safe(state_file, "{}"))
    services = state.get("service_status", {})

    lines.append("## System Health")
    for svc, status in services.items():
        emoji = "✅" if status == "healthy" else "⚠️" if status == "warning" else "❌"
        lines.append(f"- {emoji} **{svc}**: {status}")
    if not services:
        lines.append("- No service data yet")
    lines.append("")

    # ─── Daemon Stats ───
    lines.append("## Daemon Stats")
    lines.append(f"- Health checks: {state.get('total_health_checks', 0)}")
    lines.append(f"- Self-heals: {state.get('total_self_heals', 0)}")
    lines.append(f"- Messages sent: {state.get('total_messages_sent', 0)}")
    lines.append("")

    # ─── Skill Health ───
    skill_dash = os.path.join(WORKSPACE, "scratch", "skill-dashboard.json")
    skills = json.loads(read_file_safe(skill_dash, "[]"))
    if skills:
        lines.append("## Skill Health")
        for s in skills:
            rate = s.get('success_rate', 0)
            emoji = "✅" if rate >= 0.8 else "⚠️" if rate >= 0.5 else "❌" if s.get('total_runs', 0) > 0 else "⬜"
            runs = s.get('total_runs', 0)
            name = s.get('display_name', s.get('skill_id', '?'))
            lines.append(f"- {emoji} **{name}**: {rate:.0%} ({runs} runs, v{s.get('version', 1)})")
            if s.get('needs_inspection'):
                lines.append(f"  → ⚠️ Needs inspection!")
        lines.append("")

    # ─── Task Queues ───
    lines.append("## Task Queues")
    queues = [
        ("Kit/Workspace", os.path.join(WORKSPACE, "TASKQUEUE.md")),
        ("Feather", os.path.join(FEATHER, "TASKQUEUE.md")),
        ("Agent Research", os.path.join(AGENT_RESEARCH, "TASKQUEUE.md")),
        ("Prospectus Benchmark", os.path.join(AGENT_RESEARCH, "prospectus_benchmark", "TASKQUEUE.md")),
    ]
    total_pending = 0
    total_done = 0
    for name, path in queues:
        content = read_file_safe(path)
        done, pending, urgent = count_tasks(content)
        total_pending += pending
        total_done += done
        status = f"{done} done, {pending} pending"
        if urgent:
            status += f", **{urgent} URGENT**"
        lines.append(f"- **{name}**: {status}")
    lines.append(f"- **Total**: {total_done} completed, {total_pending} pending")
    lines.append("")

    # ─── Yesterday's Memory ───
    yesterday = datetime.now().replace(hour=0, minute=0, second=0)
    # Try to find yesterday's memory file
    memory_dir = os.path.join(WORKSPACE, "memory")
    if os.path.exists(memory_dir):
        files = sorted(os.listdir(memory_dir), reverse=True)
        recent = [f for f in files[:3] if f.endswith('.md')]
        if recent:
            lines.append("## Recent Memory Files")
            for f in recent:
                size = os.path.getsize(os.path.join(memory_dir, f))
                lines.append(f"- {f} ({size} bytes)")
            lines.append("")

    # ─── Feather Status ───
    feather_db = os.path.join(FEATHER, "feather.db")
    uw_dir = os.path.join(FEATHER, "data", "uw-exports")
    lines.append("## Feather")
    lines.append(f"- Database: {'exists' if os.path.exists(feather_db) else 'not created yet'}")
    if os.path.exists(uw_dir):
        csvs = [f for f in os.listdir(uw_dir) if f.endswith('.csv')]
        lines.append(f"- UW exports: {len(csvs)} CSV files")
    else:
        lines.append("- UW exports: waiting for data")
    lines.append("")

    # ─── Write Output ───
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print(f"Morning brief compiled: {OUTPUT}")
    print(f"  {total_done} tasks done, {total_pending} pending")


if __name__ == '__main__':
    main()
