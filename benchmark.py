"""
Kit Daemon — 48-Hour Benchmark Protocol
Tracks real performance metrics over 48 hours, then generates results.

Metrics tracked per skill/cron:
  - Success rate (%)
  - Average latency (seconds)
  - Average tokens consumed
  - Failure patterns
  - Model performance by task class

Results displayed on the dashboard and saved as a report.
"""
import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger('kit-daemon.benchmark')


class BenchmarkProtocol:
    """Manages a 48-hour benchmark run with live metrics."""

    def __init__(self, trace_store, config: Dict):
        self.trace_store = trace_store
        self.config = config
        self.benchmark_dir = os.path.join(
            config['paths']['daemon_home'], 'benchmarks'
        )
        os.makedirs(self.benchmark_dir, exist_ok=True)
        self._active_benchmark = self._load_active()

    def start_benchmark(self, duration_hours: int = 48, name: str = None) -> Dict:
        """Start a new benchmark run."""
        now = time.time()
        benchmark = {
            'id': datetime.now().strftime('%Y%m%d_%H%M%S'),
            'name': name or f'{duration_hours}h Benchmark',
            'started_at': now,
            'ends_at': now + (duration_hours * 3600),
            'duration_hours': duration_hours,
            'status': 'running',
            'baseline_snapshot': self._take_snapshot(),
        }
        self._active_benchmark = benchmark
        self._save_active()
        logger.info(f"Benchmark started: {benchmark['name']} ({duration_hours}h)")
        return benchmark

    def check_progress(self) -> Optional[Dict]:
        """Check benchmark progress and update metrics."""
        if not self._active_benchmark:
            return None

        bm = self._active_benchmark
        now = time.time()
        elapsed = now - bm['started_at']
        remaining = bm['ends_at'] - now

        # Check if benchmark is complete
        if now >= bm['ends_at']:
            return self.complete_benchmark()

        # Take current snapshot
        current = self._take_snapshot()
        baseline = bm.get('baseline_snapshot', {})

        # Calculate deltas
        progress = {
            'benchmark_id': bm['id'],
            'name': bm['name'],
            'status': 'running',
            'elapsed_hours': round(elapsed / 3600, 1),
            'remaining_hours': round(max(0, remaining) / 3600, 1),
            'progress_pct': round(min(100, (elapsed / (bm['duration_hours'] * 3600)) * 100), 1),
            'current': current,
            'baseline': baseline,
            'improvements': self._calc_improvements(baseline, current),
        }

        # Save progress
        bm['latest_progress'] = progress
        self._save_active()
        return progress

    def complete_benchmark(self) -> Dict:
        """Complete the benchmark and generate final report."""
        if not self._active_benchmark:
            return {'status': 'no_benchmark'}

        bm = self._active_benchmark
        bm['status'] = 'completed'
        bm['completed_at'] = time.time()

        # Final snapshot
        final = self._take_snapshot()
        baseline = bm.get('baseline_snapshot', {})
        improvements = self._calc_improvements(baseline, final)

        report = {
            'benchmark_id': bm['id'],
            'name': bm['name'],
            'status': 'completed',
            'duration_hours': bm['duration_hours'],
            'started_at': datetime.fromtimestamp(bm['started_at']).isoformat(),
            'completed_at': datetime.now().isoformat(),
            'baseline': baseline,
            'final': final,
            'improvements': improvements,
            'summary': self._generate_summary(baseline, final, improvements),
        }

        # Save report
        report_path = os.path.join(
            self.benchmark_dir, f"report_{bm['id']}.json"
        )
        with open(report_path, 'w') as f:
            json.dump(report, f, indent=2, default=str)

        # Save markdown report
        md_path = os.path.join(
            self.benchmark_dir, f"report_{bm['id']}.md"
        )
        with open(md_path, 'w') as f:
            f.write(self._generate_markdown_report(report))

        # Clear active benchmark
        self._active_benchmark = None
        self._save_active()

        logger.info(f"Benchmark completed: {report_path}")
        return report

    def get_dashboard_data(self) -> Optional[Dict]:
        """Get benchmark data formatted for the dashboard."""
        if not self._active_benchmark:
            # Check for most recent completed benchmark
            return self._get_latest_report()

        progress = self.check_progress()
        if not progress:
            return None

        return {
            'active': True,
            'name': progress['name'],
            'progress_pct': progress['progress_pct'],
            'elapsed': f"{progress['elapsed_hours']:.1f}h",
            'remaining': f"{progress['remaining_hours']:.1f}h",
            'metrics': progress.get('current', {}),
            'improvements': progress.get('improvements', {}),
        }

    # ─── SNAPSHOTS ─────────────────────────────────────────────

    def _take_snapshot(self) -> Dict:
        """Take a performance snapshot from the trace store."""
        snapshot = {
            'timestamp': time.time(),
            'total_traces': self.trace_store.count(),
            'model_stats': self.trace_store.get_model_stats(),
            'task_class_stats': self.trace_store.get_task_class_stats(),
            'routing': self.trace_store.get_routing_recommendations(),
        }

        # Overall success rate
        traces = self.trace_store.list_traces(limit=1000)
        if traces:
            success = sum(1 for t in traces if t['outcome'] == 'success')
            snapshot['overall_success_rate'] = round(success / len(traces), 3)
            snapshot['overall_avg_latency'] = round(
                sum(t['total_latency'] for t in traces) / len(traces), 2
            )
            snapshot['total_tokens'] = sum(t['total_tokens'] for t in traces)
        else:
            snapshot['overall_success_rate'] = 0
            snapshot['overall_avg_latency'] = 0
            snapshot['total_tokens'] = 0

        return snapshot

    def _calc_improvements(self, baseline: Dict, current: Dict) -> Dict:
        """Calculate improvements between baseline and current."""
        improvements = {}

        # Overall metrics
        b_success = baseline.get('overall_success_rate', 0)
        c_success = current.get('overall_success_rate', 0)
        if b_success > 0:
            improvements['success_rate_delta'] = round(c_success - b_success, 3)
            improvements['success_rate_pct_change'] = round(
                ((c_success - b_success) / b_success) * 100, 1
            ) if b_success else 0

        b_latency = baseline.get('overall_avg_latency', 0)
        c_latency = current.get('overall_avg_latency', 0)
        if b_latency > 0:
            improvements['latency_delta'] = round(c_latency - b_latency, 2)
            improvements['latency_pct_change'] = round(
                ((c_latency - b_latency) / b_latency) * 100, 1
            )

        # Per-model improvements
        model_improvements = {}
        b_models = baseline.get('model_stats', {})
        c_models = current.get('model_stats', {})
        for model in set(list(b_models.keys()) + list(c_models.keys())):
            bm = b_models.get(model, {})
            cm = c_models.get(model, {})
            if bm and cm:
                model_improvements[model] = {
                    'success_delta': round(
                        cm.get('success_rate', 0) - bm.get('success_rate', 0), 3
                    ),
                    'latency_delta': round(
                        cm.get('avg_latency', 0) - bm.get('avg_latency', 0), 2
                    ),
                    'runs_delta': cm.get('runs', 0) - bm.get('runs', 0),
                }
        improvements['per_model'] = model_improvements

        return improvements

    def _generate_summary(self, baseline, final, improvements) -> str:
        """Generate a human-readable summary."""
        lines = []
        lines.append(f"Total traces: {final.get('total_traces', 0)}")
        lines.append(f"Overall success rate: {final.get('overall_success_rate', 0):.1%}")
        lines.append(f"Average latency: {final.get('overall_avg_latency', 0):.1f}s")

        sr_delta = improvements.get('success_rate_delta', 0)
        if sr_delta != 0:
            direction = "improved" if sr_delta > 0 else "degraded"
            lines.append(f"Success rate {direction} by {abs(sr_delta):.1%}")

        return '\n'.join(lines)

    def _generate_markdown_report(self, report: Dict) -> str:
        """Generate a markdown benchmark report."""
        md = []
        md.append(f"# Kit Daemon Benchmark Report")
        md.append(f"**{report['name']}**\n")
        md.append(f"- Started: {report['started_at']}")
        md.append(f"- Completed: {report['completed_at']}")
        md.append(f"- Duration: {report['duration_hours']}h\n")

        md.append("## Results\n")
        final = report.get('final', {})
        md.append(f"| Metric | Value |")
        md.append(f"|--------|-------|")
        md.append(f"| Total Traces | {final.get('total_traces', 0)} |")
        md.append(f"| Success Rate | {final.get('overall_success_rate', 0):.1%} |")
        md.append(f"| Avg Latency | {final.get('overall_avg_latency', 0):.1f}s |")
        md.append(f"| Total Tokens | {final.get('total_tokens', 0):,} |")

        # Model breakdown
        model_stats = final.get('model_stats', {})
        if model_stats:
            md.append("\n## Per-Model Performance\n")
            md.append("| Model | Runs | Success Rate | Avg Latency | Avg Tokens |")
            md.append("|-------|------|-------------|-------------|------------|")
            for model, ms in model_stats.items():
                md.append(
                    f"| {model} | {ms['runs']} | {ms['success_rate']:.1%} | "
                    f"{ms['avg_latency']:.1f}s | {ms['avg_tokens']:.0f} |"
                )

        # Improvements
        improvements = report.get('improvements', {})
        per_model = improvements.get('per_model', {})
        if per_model:
            md.append("\n## Improvements vs Baseline\n")
            md.append("| Model | Success Δ | Latency Δ | New Runs |")
            md.append("|-------|-----------|-----------|----------|")
            for model, imp in per_model.items():
                sd = imp['success_delta']
                ld = imp['latency_delta']
                s_arrow = "↑" if sd > 0 else "↓" if sd < 0 else "→"
                l_arrow = "↓" if ld < 0 else "↑" if ld > 0 else "→"
                md.append(
                    f"| {model} | {s_arrow} {abs(sd):.1%} | "
                    f"{l_arrow} {abs(ld):.1f}s | +{imp['runs_delta']} |"
                )

        # Routing recommendations
        routing = final.get('routing', {})
        if routing:
            md.append("\n## Optimal Model Routing\n")
            md.append("| Task Class | Best Model |")
            md.append("|-----------|------------|")
            for tc, model in routing.items():
                md.append(f"| {tc} | {model} |")

        return '\n'.join(md)

    # ─── PERSISTENCE ───────────────────────────────────────────

    def _load_active(self) -> Optional[Dict]:
        path = os.path.join(self.benchmark_dir, 'active.json')
        if os.path.exists(path):
            try:
                with open(path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return None

    def _save_active(self):
        path = os.path.join(self.benchmark_dir, 'active.json')
        if self._active_benchmark:
            with open(path, 'w') as f:
                json.dump(self._active_benchmark, f, indent=2, default=str)
        elif os.path.exists(path):
            os.remove(path)

    def _get_latest_report(self) -> Optional[Dict]:
        """Load the most recent completed benchmark report."""
        reports = sorted([
            f for f in os.listdir(self.benchmark_dir)
            if f.startswith('report_') and f.endswith('.json')
        ], reverse=True)
        if reports:
            try:
                with open(os.path.join(self.benchmark_dir, reports[0])) as f:
                    data = json.load(f)
                return {
                    'active': False,
                    'name': data.get('name', 'Benchmark'),
                    'completed': data.get('completed_at', ''),
                    'metrics': data.get('final', {}),
                    'improvements': data.get('improvements', {}),
                }
            except (json.JSONDecodeError, IOError):
                pass
        return None
