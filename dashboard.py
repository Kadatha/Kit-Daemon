"""
Kit Daemon — Dashboard Generator
Creates a live HTML dashboard Kit can serve or Andrew can open locally.
Jarvis had holographic displays. Kit has a browser tab.
"""
import json
import os
from datetime import datetime

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="30">
<title>Kit Dashboard</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'Segoe UI', system-ui, sans-serif;
    background: #0a0e17;
    color: #c8d6e5;
    padding: 24px;
    min-height: 100vh;
  }
  .header {
    display: flex; align-items: center; gap: 16px;
    margin-bottom: 32px; padding-bottom: 16px;
    border-bottom: 1px solid #1e2d3d;
  }
  .header h1 { color: #00d4ff; font-size: 28px; font-weight: 300; }
  .header .fox { font-size: 36px; }
  .header .status {
    margin-left: auto; padding: 6px 16px;
    border-radius: 20px; font-size: 13px; font-weight: 600;
  }
  .status.online { background: #0d3320; color: #00ff88; }
  .status.degraded { background: #3d2e0d; color: #ffaa00; }
  .status.offline { background: #3d0d0d; color: #ff4444; }
  .timestamp { color: #5a6e82; font-size: 13px; margin-left: 16px; }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
    gap: 20px;
  }
  .card {
    background: #111827;
    border: 1px solid #1e2d3d;
    border-radius: 12px;
    padding: 20px;
  }
  .card h2 {
    color: #00d4ff; font-size: 14px; font-weight: 600;
    text-transform: uppercase; letter-spacing: 1px;
    margin-bottom: 16px;
  }
  .metric { display: flex; justify-content: space-between; padding: 8px 0; }
  .metric .label { color: #8899aa; }
  .metric .value { color: #e8f0f8; font-weight: 500; }
  .metric .value.good { color: #00ff88; }
  .metric .value.warn { color: #ffaa00; }
  .metric .value.bad { color: #ff4444; }
  .skill-row {
    display: flex; align-items: center; gap: 8px;
    padding: 6px 0; border-bottom: 1px solid #1a2332;
  }
  .skill-row:last-child { border-bottom: none; }
  .skill-bar {
    flex: 1; height: 6px; background: #1a2332;
    border-radius: 3px; overflow: hidden;
  }
  .skill-bar .fill { height: 100%; border-radius: 3px; }
  .fill.good { background: #00ff88; }
  .fill.warn { background: #ffaa00; }
  .fill.bad { background: #ff4444; }
  .fill.none { background: #2a3442; }
  .skill-name { width: 140px; font-size: 13px; color: #8899aa; }
  .skill-rate { width: 48px; text-align: right; font-size: 13px; font-weight: 500; }
  .workflow-item {
    padding: 8px 0; border-bottom: 1px solid #1a2332;
    font-size: 13px;
  }
  .rec { padding: 10px; margin: 6px 0; border-radius: 8px; font-size: 13px; }
  .rec.high { background: #2d1215; border-left: 3px solid #ff4444; }
  .rec.medium { background: #2d2612; border-left: 3px solid #ffaa00; }
  .rec.low { background: #122d1a; border-left: 3px solid #00ff88; }
  .rec .action { color: #00d4ff; margin-top: 4px; }
  .projects { list-style: none; }
  .projects li { padding: 8px 0; border-bottom: 1px solid #1a2332; }
  .projects .name { color: #e8f0f8; font-weight: 500; }
  .projects .detail { color: #5a6e82; font-size: 12px; }
</style>
</head>
<body>
<div class="header">
  <span class="fox">🦊</span>
  <h1>Kit Dashboard</h1>
  <span class="timestamp">{timestamp}</span>
  <span class="status {overall_status_class}">{overall_status}</span>
</div>

<div class="grid">
  <!-- System Health -->
  <div class="card">
    <h2>System Health</h2>
    {system_metrics}
  </div>

  <!-- Skill Health -->
  <div class="card">
    <h2>Skill Evolution</h2>
    {skill_rows}
  </div>

  <!-- Task Queues -->
  <div class="card">
    <h2>Task Queues</h2>
    {task_metrics}
  </div>

  <!-- Daemon Stats -->
  <div class="card">
    <h2>Daemon Stats</h2>
    {daemon_metrics}
  </div>

  <!-- Workflows -->
  <div class="card">
    <h2>Recent Workflows</h2>
    {workflow_items}
  </div>

  <!-- Recommendations -->
  <div class="card">
    <h2>Recommendations</h2>
    {recommendations}
  </div>

  <!-- Benchmark -->
  <div class="card">
    <h2>📊 Benchmark</h2>
    {benchmark_data}
  </div>

  <!-- API Costs -->
  <div class="card">
    <h2>💰 API Costs</h2>
    {cost_data}
  </div>

  <!-- Active Projects -->
  <div class="card">
    <h2>Active Projects</h2>
    {projects}
  </div>
</div>

</body>
</html>"""


def generate_dashboard(config):
    """Generate the HTML dashboard from current state."""
    daemon_home = config['paths']['daemon_home']
    workspace = config['paths']['workspace']

    # Load state
    state = _load_json(os.path.join(daemon_home, 'state.json'), {})
    skills = _load_json(os.path.join(workspace, 'scratch', 'skill-dashboard.json'), [])
    insights = _load_json(os.path.join(daemon_home, 'insights', 'current_patterns.json'), {})

    # System health
    services = state.get('service_status', {})
    system_html = ""
    all_healthy = True
    for svc in ['ollama', 'openclaw', 'gpu', 'disk', 'ram']:
        status = services.get(svc, 'unknown')
        css = 'good' if status == 'healthy' else 'warn' if status in ('warning', 'restarting') else 'bad'
        if status != 'healthy':
            all_healthy = False
        system_html += f'<div class="metric"><span class="label">{svc.title()}</span><span class="value {css}">{status}</span></div>\n'

    # Skills
    skill_html = ""
    for s in skills:
        rate = s.get('success_rate', 0)
        runs = s.get('total_runs', 0)
        pct = int(rate * 100)
        css = 'good' if rate >= 0.8 else 'warn' if rate >= 0.5 else 'bad' if runs > 0 else 'none'
        name = s.get('display_name', s.get('skill_id', '?'))
        skill_html += f'''<div class="skill-row">
            <span class="skill-name">{name}</span>
            <div class="skill-bar"><div class="fill {css}" style="width:{max(pct,2)}%"></div></div>
            <span class="skill-rate">{pct}%</span>
        </div>\n'''

    # Task queues — scrollable live view
    task_html = ""
    total_done = 0
    total_pending = 0
    all_tasks = []

    queue_labels = {
        'workspace': 'Kit Self-Improvement',
        'feather': 'Feather Trading',
        'agent-research': 'Memory Harness',
        'prospectus_benchmark': 'Prospectus Benchmark',
        'biostack': 'BioStack',
    }

    for tq_path in config.get('watch_paths', {}).get('task_queues', []):
        try:
            with open(tq_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            project = os.path.basename(os.path.dirname(tq_path))
            label = queue_labels.get(project, project)
            for line in lines:
                line = line.strip()
                if line.startswith('- [x]'):
                    total_done += 1
                    task_text = line[6:].strip()
                    # Extract priority
                    pri = 'DONE'
                    all_tasks.append({'status': 'done', 'priority': pri, 'text': task_text, 'project': label})
                elif line.startswith('- [ ]'):
                    total_pending += 1
                    task_text = line[6:].strip()
                    pri = 'MEDIUM'
                    for p in ['URGENT', 'HIGH', 'MEDIUM', 'LOW']:
                        if task_text.startswith(p):
                            pri = p
                            break
                    all_tasks.append({'status': 'pending', 'priority': pri, 'text': task_text, 'project': label})
        except FileNotFoundError:
            pass

    # Summary line
    task_html += f'<div class="metric" style="margin-bottom:8px"><span class="label"><strong>Total</strong></span><span class="value"><strong>{total_done}✅ {total_pending}⏳</strong></span></div>\n'

    # Group tasks by project
    priority_order = {'URGENT': 0, 'HIGH': 1, 'MEDIUM': 2, 'LOW': 3, 'DONE': 9}

    # Determine which project the worker is currently focused on
    # Read worker-status.md or use "Kit Self-Improvement" as default
    active_project = 'Kit Self-Improvement'
    worker_status_path = os.path.join(workspace, 'scratch', 'worker-status.md')
    if os.path.exists(worker_status_path):
        try:
            with open(worker_status_path, 'r', encoding='utf-8') as f:
                ws = f.read()
            if 'Feather' in ws:
                active_project = 'Feather Trading'
            elif 'Memory Harness' in ws or 'Agent Memory' in ws or 'agent-research' in ws:
                active_project = 'Memory Harness'
            elif 'Prospectus' in ws:
                active_project = 'Prospectus Benchmark'
        except IOError:
            pass

    # Build per-project sections
    projects_in_order = ['Kit Self-Improvement', 'Feather Trading', 'Memory Harness', 'Prospectus Benchmark', 'BioStack']
    tasks_by_project = {}
    for t in all_tasks:
        proj = t['project']
        if proj not in tasks_by_project:
            tasks_by_project[proj] = {'pending': [], 'done': []}
        if t['status'] == 'pending':
            tasks_by_project[proj]['pending'].append(t)
        else:
            tasks_by_project[proj]['done'].append(t)

    task_html += '<div style="max-height:400px;overflow-y:auto;scrollbar-width:thin;scrollbar-color:#2a3442 #0d1117">\n'

    for proj_name in projects_in_order:
        if proj_name not in tasks_by_project:
            continue
        proj_data = tasks_by_project[proj_name]
        pending = proj_data['pending']
        done = proj_data['done']

        if not pending and not done:
            continue

        pending.sort(key=lambda t: priority_order.get(t['priority'], 5))

        # Project header
        is_active = proj_name == active_project
        active_badge = ' <span style="color:#00ff88;font-size:10px">⚡ ACTIVE</span>' if is_active else ''
        p_done = len(done)
        p_pending = len(pending)
        task_html += f'<div style="padding:6px;color:#00d4ff;font-size:13px;font-weight:600;border-bottom:1px solid #2a3442;margin-top:4px">{proj_name}{active_badge} <span style="color:#5a6e82;font-weight:400;font-size:11px">({p_done}✅ {p_pending}⏳)</span></div>\n'

        # Pending tasks for this project
        for i, t in enumerate(pending):
            pri = t['priority']
            pri_css = '#ff4444' if pri == 'URGENT' else '#ffaa00' if pri == 'HIGH' else '#8899aa' if pri == 'MEDIUM' else '#5a6e82'
            # Arrow only on the first pending task of the ACTIVE project
            is_next = is_active and i == 0
            arrow = '▶ ' if is_next else '  '
            bg = 'background:#1a2332;' if is_next else ''
            display = t['text'][:85]
            task_html += f'<div style="padding:3px 6px 3px 16px;border-bottom:1px solid #0d1117;font-size:12px;{bg}">'
            task_html += f'<span style="color:{pri_css};font-weight:600;width:55px;display:inline-block">{arrow}{pri}</span> '
            task_html += f'<span style="color:#c8d6e5">{display}</span>'
            task_html += '</div>\n'

        # Done tasks for this project (last 3)
        if done:
            for t in done[-3:]:
                display = t['text'][:75]
                task_html += f'<div style="padding:2px 6px 2px 16px;border-bottom:1px solid #0d1117;font-size:11px;color:#3a5a3a">'
                task_html += f'<span style="width:55px;display:inline-block">  ✅</span> '
                task_html += f'<span>{display}</span>'
                task_html += '</div>\n'

    task_html += '</div>\n'

    # Daemon stats
    daemon_html = ""
    daemon_html += f'<div class="metric"><span class="label">Health Checks</span><span class="value">{state.get("total_health_checks", 0)}</span></div>\n'
    daemon_html += f'<div class="metric"><span class="label">Self-Heals</span><span class="value">{state.get("total_self_heals", 0)}</span></div>\n'
    daemon_html += f'<div class="metric"><span class="label">Messages Sent</span><span class="value">{state.get("total_messages_sent", 0)}</span></div>\n'
    started = state.get('started_at', 'unknown')
    if started != 'unknown':
        try:
            start_dt = datetime.fromisoformat(started)
            uptime = datetime.now() - start_dt
            hours = int(uptime.total_seconds() // 3600)
            mins = int((uptime.total_seconds() % 3600) // 60)
            daemon_html += f'<div class="metric"><span class="label">Uptime</span><span class="value good">{hours}h {mins}m</span></div>\n'
        except ValueError:
            pass

    # Workflows
    workflow_html = ""
    run_dir = os.path.join(daemon_home, 'workflow-runs')
    date_str = datetime.now().strftime('%Y-%m-%d')
    run_file = os.path.join(run_dir, f'{date_str}.jsonl')
    if os.path.exists(run_file):
        try:
            with open(run_file, 'r') as f:
                runs = [json.loads(l) for l in f if l.strip()]
            for r in runs[-5:]:
                icon = '✅' if r.get('success') else '❌'
                name = r.get('name', 'Unknown')
                dur = r.get('duration_seconds', 0)
                workflow_html += f'<div class="workflow-item">{icon} {name} ({dur}s)</div>\n'
        except (json.JSONDecodeError, IOError):
            pass
    if not workflow_html:
        workflow_html = '<div class="workflow-item" style="color:#5a6e82">No workflows today yet</div>'

    # Recommendations — pull from multiple sources
    rec_html = ""
    recs = insights.get('recommendations', [])

    # Also pull tips from decision attribution
    tips_dir = os.path.join(daemon_home, 'tips')
    if os.path.exists(tips_dir):
        for fname in sorted(os.listdir(tips_dir), reverse=True)[:3]:
            if fname.endswith('.jsonl'):
                try:
                    with open(os.path.join(tips_dir, fname), 'r') as f:
                        for line in f:
                            if line.strip():
                                tip = json.loads(line)
                                if tip.get('confidence', 0) >= 0.5:
                                    tip_type = tip.get('tip_type', 'strategy')
                                    pri = 'high' if tip_type == 'recovery' else 'medium' if tip_type == 'optimization' else 'low'
                                    recs.append({
                                        'insight': f"[{tip_type.upper()}] {tip.get('title', '')} — {tip.get('description', '')}",
                                        'action': tip.get('action', ''),
                                        'priority': pri,
                                    })
                except (json.JSONDecodeError, IOError):
                    pass

    # Pull worker status as a recommendation
    worker_status_path = os.path.join(workspace, 'scratch', 'worker-status.md')
    if os.path.exists(worker_status_path):
        try:
            with open(worker_status_path, 'r', encoding='utf-8') as f:
                ws_content = f.read()
            # Extract task name
            for line in ws_content.split('\n'):
                if 'Task Completed' in line:
                    task_name = line.split(':', 1)[-1].strip() if ':' in line else 'Unknown'
                    recs.insert(0, {
                        'insight': f'Last worker task: {task_name}',
                        'action': 'Check scratch/ for output',
                        'priority': 'low',
                    })
                    break
        except IOError:
            pass

    # Deduplicate and limit
    seen = set()
    unique_recs = []
    for r in recs:
        key = r.get('insight', '')[:60]
        if key not in seen:
            seen.add(key)
            unique_recs.append(r)
    recs = unique_recs[:8]

    rec_html += '<div style="max-height:250px;overflow-y:auto;scrollbar-width:thin;scrollbar-color:#2a3442 #0d1117">\n'
    if recs:
        for r in recs:
            pri = r.get('priority', 'low')
            rec_html += f'<div class="rec {pri}">{r.get("insight", "")}<div class="action">→ {r.get("action", "")}</div></div>\n'
    else:
        rec_html += '<div style="color:#5a6e82;padding:8px">No recommendations yet. Collecting data...</div>'
    rec_html += '</div>\n'

    # Benchmark — update progress before rendering
    try:
        from benchmark import BenchmarkProtocol
        from trace_learning import TraceStore
        db_path = os.path.join(daemon_home, 'traces.db')
        if os.path.exists(db_path):
            _ts = TraceStore(db_path)
            _bp = BenchmarkProtocol(_ts, config)
            _bp.check_progress()  # Updates active.json with latest metrics
    except Exception:
        pass

    bench_html = ""
    bench_file = os.path.join(daemon_home, 'benchmarks', 'active.json')
    if os.path.exists(bench_file):
        try:
            with open(bench_file) as f:
                bench = json.load(f)
            if bench.get('status') == 'running':
                progress = bench.get('latest_progress', {})
                pct = progress.get('progress_pct', 0)
                elapsed = progress.get('elapsed_hours', 0)
                remaining = progress.get('remaining_hours', 0)
                current = progress.get('current', {})
                improvements = progress.get('improvements', {})

                bar_css = 'good' if pct > 50 else 'warn'
                bench_html += f'<div class="metric"><span class="label">Status</span><span class="value good">RUNNING</span></div>\n'
                bench_html += f'<div class="skill-row"><span class="skill-name">Progress</span><div class="skill-bar"><div class="fill {bar_css}" style="width:{max(pct,2)}%"></div></div><span class="skill-rate">{pct:.0f}%</span></div>\n'
                bench_html += f'<div class="metric"><span class="label">Elapsed</span><span class="value">{elapsed:.1f}h</span></div>\n'
                bench_html += f'<div class="metric"><span class="label">Remaining</span><span class="value">{remaining:.1f}h</span></div>\n'
                bench_html += f'<div class="metric"><span class="label">Traces</span><span class="value">{current.get("total_traces", 0)}</span></div>\n'
                bench_html += f'<div class="metric"><span class="label">Success Rate</span><span class="value good">{current.get("overall_success_rate", 0):.1%}</span></div>\n'
                bench_html += f'<div class="metric"><span class="label">Avg Latency</span><span class="value">{current.get("overall_avg_latency", 0):.1f}s</span></div>\n'

                sr_delta = improvements.get('success_rate_delta', 0)
                if sr_delta != 0:
                    arrow = '↑' if sr_delta > 0 else '↓'
                    css = 'good' if sr_delta > 0 else 'bad'
                    bench_html += f'<div class="metric"><span class="label">Success Δ</span><span class="value {css}">{arrow} {abs(sr_delta):.1%}</span></div>\n'
        except (json.JSONDecodeError, IOError):
            pass

    # Check for completed benchmark if no active one
    if not bench_html:
        report_dir = os.path.join(daemon_home, 'benchmarks')
        if os.path.exists(report_dir):
            reports = sorted([f for f in os.listdir(report_dir) if f.startswith('report_') and f.endswith('.json')], reverse=True)
            if reports:
                try:
                    with open(os.path.join(report_dir, reports[0])) as f:
                        rpt = json.load(f)
                    final = rpt.get('final', {})
                    bench_html += f'<div class="metric"><span class="label">Last Run</span><span class="value">{rpt.get("name", "Benchmark")}</span></div>\n'
                    bench_html += f'<div class="metric"><span class="label">Traces</span><span class="value">{final.get("total_traces", 0)}</span></div>\n'
                    bench_html += f'<div class="metric"><span class="label">Success Rate</span><span class="value good">{final.get("overall_success_rate", 0):.1%}</span></div>\n'
                    bench_html += f'<div class="metric"><span class="label">Avg Latency</span><span class="value">{final.get("overall_avg_latency", 0):.1f}s</span></div>\n'

                    # Per-model breakdown
                    for model, ms in final.get('model_stats', {}).items():
                        short_model = model.split(':')[0] if ':' in model else model[:15]
                        rate = ms.get('success_rate', 0)
                        css = 'good' if rate >= 0.8 else 'warn' if rate >= 0.5 else 'bad'
                        bench_html += f'<div class="skill-row"><span class="skill-name">{short_model}</span><div class="skill-bar"><div class="fill {css}" style="width:{max(int(rate*100),2)}%"></div></div><span class="skill-rate">{rate:.0%}</span></div>\n'
                except (json.JSONDecodeError, IOError):
                    pass

    if not bench_html:
        bench_html = '<div style="color:#5a6e82;padding:8px">No benchmark data yet. Traces accumulating...</div>'

    # API Costs
    cost_html = ""
    try:
        from cost_tracker import CostTracker
        ct = CostTracker(config)
        cd = ct.get_dashboard_data()
        today_cost = cd['today_cost']
        today_css = 'good' if today_cost < 5 else 'warn' if today_cost < 20 else 'bad'
        cost_html += f'<div class="metric"><span class="label">Today</span><span class="value {today_css}">${today_cost:.2f}</span></div>\n'
        cost_html += f'<div class="metric"><span class="label">This Week</span><span class="value">${cd["weekly_cost"]:.2f}</span></div>\n'
        proj_css = 'good' if cd['projected_monthly'] < 50 else 'warn' if cd['projected_monthly'] < 150 else 'bad'
        cost_html += f'<div class="metric"><span class="label">Projected/Month</span><span class="value {proj_css}">${cd["projected_monthly"]:.0f}</span></div>\n'
        local_css = 'good' if cd['today_local_pct'] > 80 else 'warn' if cd['today_local_pct'] > 50 else 'bad'
        cost_html += f'<div class="metric"><span class="label">Local %</span><span class="value {local_css}">{cd["today_local_pct"]:.0f}%</span></div>\n'
        cost_html += f'<div class="metric"><span class="label">Cloud Calls Today</span><span class="value">{cd["today_cloud_calls"]}</span></div>\n'
        if cd.get('top_model') and cd['top_model'] != 'none':
            short = cd['top_model'].split('/')[-1][:20]
            cost_html += f'<div class="metric"><span class="label">Top Spender</span><span class="value warn">{short}</span></div>\n'
    except Exception as e:
        cost_html = f'<div style="color:#5a6e82;padding:8px">Cost tracking initializing... ({e})</div>'

    # Projects
    proj_html = '<ul class="projects">'
    projects = [
        ('🦊 Kit R2 Evolution', 'PRIMARY — daemon + skills + workflows live'),
        ('🪶 Feather', 'Awaiting UW data + structure cleanup'),
        ('🧠 Memory Harness', 'Published — maintenance mode'),
        ('📊 Prospectus', 'PAUSED'),
    ]
    for name, detail in projects:
        proj_html += f'<li><span class="name">{name}</span><br><span class="detail">{detail}</span></li>\n'
    proj_html += '</ul>'

    # Overall status
    overall = 'ONLINE' if all_healthy else 'DEGRADED'
    overall_class = 'online' if all_healthy else 'degraded'

    # Escape CSS curly braces for Python format
    safe_template = TEMPLATE.replace('{', '{{').replace('}', '}}')
    # Restore our actual placeholders
    for key in ['timestamp', 'overall_status', 'overall_status_class',
                'system_metrics', 'skill_rows', 'task_metrics',
                'daemon_metrics', 'workflow_items', 'recommendations',
                'benchmark_data', 'cost_data', 'projects']:
        safe_template = safe_template.replace('{{' + key + '}}', '{' + key + '}')

    html = safe_template.format(
        timestamp=datetime.now().strftime('%A, %B %d, %Y — %I:%M %p'),
        overall_status=overall,
        overall_status_class=overall_class,
        system_metrics=system_html,
        skill_rows=skill_html,
        task_metrics=task_html,
        daemon_metrics=daemon_html,
        workflow_items=workflow_html,
        recommendations=rec_html,
        benchmark_data=bench_html,
        cost_data=cost_html,
        projects=proj_html,
    )

    # Write dashboard
    output = os.path.join(workspace, 'scratch', 'dashboard.html')
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, 'w', encoding='utf-8') as f:
        f.write(html)

    return output


def _load_json(path, default):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


if __name__ == '__main__':
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    with open(config_path) as f:
        config = json.load(f)
    path = generate_dashboard(config)
    print(f"Dashboard generated: {path}")
