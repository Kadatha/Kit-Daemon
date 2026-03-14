"""
Kit Daemon — Cron Health Monitor
Tracks cron run success/failure. Self-heals when possible.
"""
import logging
import subprocess
import json
from datetime import datetime

logger = logging.getLogger('kit-daemon.health')


class HealthMonitor:
    def __init__(self, config, state_manager, comms_manager, skill_engine=None):
        self.config = config
        self.state = state_manager
        self.comms = comms_manager
        self.skill_engine = skill_engine

    def check_cron_health(self):
        """Check recent cron runs for all mapped cron jobs."""
        cron_map = self.config.get('cron_skill_map', {})
        if not cron_map:
            worker_id = self.config.get('worker_cron_id', '')
            if not worker_id:
                return {'status': 'no_crons_configured'}
            cron_ids = [worker_id]
        else:
            cron_ids = list(cron_map.keys())

        # Check the worker (primary) — others get tracked via skill evolution
        primary_id = self.config.get('worker_cron_id', cron_ids[0])

        try:
            result = subprocess.run(
                ['powershell', '-Command', f'openclaw cron runs --id {primary_id} --limit 5'],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                logger.warning(f"Could not fetch cron runs: {result.stderr.strip()}")
                return {'status': 'unknown', 'detail': 'CLI failed'}

            # Try to parse JSON output
            try:
                data = json.loads(result.stdout)
                # CLI returns {"entries": [...]} wrapper
                if isinstance(data, dict) and 'entries' in data:
                    runs = data['entries']
                elif isinstance(data, list):
                    runs = data
                else:
                    runs = []
            except json.JSONDecodeError:
                # Non-JSON output — parse text
                return self._parse_text_output(result.stdout)

            # Feed runs into skill evolution tracking
            self._track_skill_runs(runs)

            return self._analyze_runs(runs)

        except subprocess.TimeoutExpired:
            logger.error("Cron health check timed out")
            return {'status': 'timeout'}
        except Exception as e:
            logger.error(f"Cron health check failed: {e}")
            return {'status': 'error', 'detail': str(e)}

    def _analyze_runs(self, runs):
        """Analyze cron run data for failure patterns."""
        if not runs:
            return {'status': 'no_data'}

        # Group by job
        by_job = {}
        for run in runs:
            job_id = run.get('jobId', run.get('id', 'unknown'))
            if job_id not in by_job:
                by_job[job_id] = []
            by_job[job_id].append(run)

        issues = []
        for job_id, job_runs in by_job.items():
            # Count consecutive failures (sorted newest first)
            consecutive_failures = 0
            for run in sorted(job_runs, key=lambda r: r.get('ts', r.get('runAtMs', 0)), reverse=True):
                status = run.get('status', '')
                if status in ('failed', 'error', 'timeout'):
                    consecutive_failures += 1
                elif status == 'ok':
                    break
                else:
                    break

            if consecutive_failures >= self.config['health']['failure_threshold']:
                issues.append({
                    'job_id': job_id,
                    'consecutive_failures': consecutive_failures,
                    'last_error': job_runs[0].get('error', 'unknown')
                })
                self.state.record_failure(f"cron_{job_id}")

        if issues:
            for issue in issues:
                msg = (f"⚠️ Cron job {issue['job_id'][:12]}... has "
                       f"{issue['consecutive_failures']} consecutive failures")
                self.comms.send_telegram(msg, priority=8)
                logger.warning(msg)

            return {'status': 'issues', 'issues': issues}

        return {'status': 'healthy'}

    def _track_skill_runs(self, runs):
        """Feed cron run data into skill evolution trackers."""
        if not self.skill_engine:
            return

        cron_map = self.config.get('cron_skill_map', {})
        tracked_runs = self.state.get('tracked_run_timestamps', set())
        if isinstance(tracked_runs, list):
            tracked_runs = set(tracked_runs)

        new_tracked = set()
        for run in runs:
            job_id = run.get('jobId', '')
            ts = str(run.get('ts', run.get('runAtMs', '')))
            run_key = f"{job_id}_{ts}"

            # Skip already-tracked runs
            if run_key in tracked_runs:
                continue

            skill_id = cron_map.get(job_id)
            if not skill_id:
                continue

            success = run.get('status') == 'ok'
            duration = run.get('durationMs', 0) / 1000.0
            model = run.get('model', 'unknown')
            error = run.get('error') if not success else None
            summary = run.get('summary', '')[:200] if run.get('summary') else None

            self.skill_engine.record_cron_run(
                cron_id=job_id,
                skill_id=skill_id,
                success=success,
                duration_seconds=duration,
                error=error,
                model=model,
                output_summary=summary,
            )
            new_tracked.add(run_key)

        # Persist tracked runs (keep last 200 to avoid unbounded growth)
        all_tracked = tracked_runs | new_tracked
        self.state.set('tracked_run_timestamps', list(all_tracked)[-200:])

    def _parse_text_output(self, text):
        """Fallback: parse non-JSON cron run output."""
        lines = text.strip().split('\n')
        failure_count = sum(1 for l in lines if 'fail' in l.lower() or 'error' in l.lower())

        if failure_count > 3:
            return {'status': 'issues', 'text_failures': failure_count}

        return {'status': 'likely_healthy', 'lines': len(lines)}

    def check_worker_output(self):
        """Check if the task queue worker is actually completing tasks."""
        try:
            # Read all task queues and count completed items
            completed = 0
            total_pending = 0

            for tq_path in self.config['watch_paths']['task_queues']:
                try:
                    with open(tq_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    completed += content.count('- [x]')
                    total_pending += content.count('- [ ]')
                except FileNotFoundError:
                    pass

            return {
                'completed_tasks': completed,
                'pending_tasks': total_pending,
                'ratio': completed / max(1, completed + total_pending)
            }

        except Exception as e:
            logger.error(f"Worker output check failed: {e}")
            return {'status': 'error', 'detail': str(e)}
