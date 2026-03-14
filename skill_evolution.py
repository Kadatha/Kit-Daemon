"""
Kit Daemon — Skill Evolution Engine
Observe → Inspect → Amend → Evaluate loop for self-improving skills.

Skills are tracked in two places:
1. OpenClaw skills (~/AppData/Roaming/npm/node_modules/openclaw/skills/) — read-only, managed by npm
2. Kit's cron prompts — the REAL skills that need evolution (task worker, health monitor, briefs, etc.)

Each "skill" gets:
- runs.jsonl     — append-only execution log
- meta.json      — success rate, version, thresholds
- versions/      — numbered prompt versions with rollback
"""
import json
import logging
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger('kit-daemon.skills')

SKILLS_DIR = None  # Set by init


def init(config):
    """Initialize the skills tracking directory."""
    global SKILLS_DIR
    SKILLS_DIR = os.path.join(config['paths']['daemon_home'], 'skills')
    os.makedirs(SKILLS_DIR, exist_ok=True)
    return SKILLS_DIR


class SkillTracker:
    """Tracks a single skill's execution history and evolution."""

    def __init__(self, skill_id, display_name=None):
        self.skill_id = skill_id
        self.display_name = display_name or skill_id
        self.dir = os.path.join(SKILLS_DIR, skill_id)
        self.versions_dir = os.path.join(self.dir, 'versions')
        self.runs_file = os.path.join(self.dir, 'runs.jsonl')
        self.meta_file = os.path.join(self.dir, 'meta.json')

        os.makedirs(self.versions_dir, exist_ok=True)
        self._ensure_meta()

    def _ensure_meta(self):
        """Create meta.json if it doesn't exist."""
        if not os.path.exists(self.meta_file):
            meta = {
                'skill_id': self.skill_id,
                'display_name': self.display_name,
                'created_at': datetime.now().isoformat(),
                'current_version': 1,
                'total_runs': 0,
                'total_successes': 0,
                'total_failures': 0,
                'success_rate': 0.0,
                'last_run': None,
                'last_amended': None,
                'amendment_count': 0,
                'rollback_count': 0,
                'inspect_threshold': 0.7,  # Trigger inspection below this success rate
                'min_runs_before_inspect': 5,  # Need at least N runs before judging
                'auto_amend': False,  # Require human approval by default
            }
            self._save_meta(meta)
        return self._load_meta()

    def _load_meta(self):
        try:
            with open(self.meta_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return self._ensure_meta()

    def _save_meta(self, meta):
        with open(self.meta_file, 'w', encoding='utf-8') as f:
            json.dump(meta, f, indent=2)

    # ─── OBSERVE ───────────────────────────────────────────────

    def record_run(self, success, duration_seconds=0, error=None,
                   model=None, notes=None, output_summary=None):
        """Record a skill execution. Append-only."""
        run = {
            'timestamp': datetime.now().isoformat(),
            'success': success,
            'duration_seconds': round(duration_seconds, 1),
            'error': error,
            'model': model,
            'output_summary': output_summary[:200] if output_summary else None,
            'notes': notes,
            'version': self._load_meta().get('current_version', 1),
        }

        # Append to runs.jsonl
        with open(self.runs_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(run) + '\n')

        # Update meta
        meta = self._load_meta()
        meta['total_runs'] += 1
        if success:
            meta['total_successes'] += 1
        else:
            meta['total_failures'] += 1
        meta['success_rate'] = round(
            meta['total_successes'] / max(1, meta['total_runs']), 3
        )
        meta['last_run'] = run['timestamp']
        self._save_meta(meta)

        logger.debug(f"Skill '{self.skill_id}' run recorded: "
                     f"{'✓' if success else '✗'} (rate: {meta['success_rate']})")

        return run

    # ─── INSPECT ───────────────────────────────────────────────

    def needs_inspection(self):
        """Check if this skill's performance warrants inspection."""
        meta = self._load_meta()
        if meta['total_runs'] < meta['min_runs_before_inspect']:
            return False
        return meta['success_rate'] < meta['inspect_threshold']

    def inspect(self, window_days=7):
        """Analyze recent failures and return inspection report."""
        runs = self._load_recent_runs(window_days)
        if not runs:
            return None

        total = len(runs)
        failures = [r for r in runs if not r.get('success')]
        successes = [r for r in runs if r.get('success')]

        # Extract error patterns
        error_counts = {}
        for f in failures:
            err = f.get('error', 'unknown')
            # Normalize error strings to find patterns
            err_key = err[:100] if err else 'unknown'
            error_counts[err_key] = error_counts.get(err_key, 0) + 1

        # Model performance breakdown
        model_stats = {}
        for r in runs:
            m = r.get('model', 'unknown')
            if m not in model_stats:
                model_stats[m] = {'total': 0, 'success': 0}
            model_stats[m]['total'] += 1
            if r.get('success'):
                model_stats[m]['success'] += 1

        # Timing analysis
        durations = [r.get('duration_seconds', 0) for r in runs if r.get('duration_seconds')]
        avg_duration = sum(durations) / max(1, len(durations))

        # Version performance
        version_stats = {}
        for r in runs:
            v = r.get('version', 1)
            if v not in version_stats:
                version_stats[v] = {'total': 0, 'success': 0}
            version_stats[v]['total'] += 1
            if r.get('success'):
                version_stats[v]['success'] += 1

        report = {
            'skill_id': self.skill_id,
            'window_days': window_days,
            'total_runs': total,
            'success_rate': round(len(successes) / max(1, total), 3),
            'failure_count': len(failures),
            'top_errors': dict(sorted(error_counts.items(), key=lambda x: -x[1])[:5]),
            'model_stats': model_stats,
            'avg_duration_seconds': round(avg_duration, 1),
            'version_stats': version_stats,
            'recommendation': self._generate_recommendation(
                len(successes) / max(1, total), error_counts, model_stats
            ),
        }

        # Save inspection report
        report_file = os.path.join(self.dir, 'last_inspection.json')
        with open(report_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2)

        logger.info(f"Skill '{self.skill_id}' inspected: {report['success_rate']} success rate, "
                    f"{report['failure_count']} failures")

        return report

    def _generate_recommendation(self, success_rate, error_counts, model_stats):
        """Generate a human-readable recommendation."""
        if success_rate >= 0.9:
            return "Performing well. No changes needed."

        recommendations = []

        if success_rate < 0.3:
            recommendations.append("CRITICAL: Success rate below 30%. Skill may be fundamentally broken.")

        # Check if one error dominates
        if error_counts:
            top_error = max(error_counts.items(), key=lambda x: x[1])
            total_failures = sum(error_counts.values())
            if top_error[1] / max(1, total_failures) > 0.5:
                recommendations.append(
                    f"Dominant failure pattern ({top_error[1]}/{total_failures}): {top_error[0][:80]}"
                )

        # Check if model matters
        for model, stats in model_stats.items():
            rate = stats['success'] / max(1, stats['total'])
            if rate < 0.5 and stats['total'] >= 3:
                recommendations.append(
                    f"Model '{model}' underperforming: {rate:.0%} success ({stats['total']} runs)"
                )

        return ' | '.join(recommendations) if recommendations else "Consider reviewing prompt instructions."

    # ─── AMEND ─────────────────────────────────────────────────

    def save_version(self, prompt_text, source='manual', rationale=None):
        """Save a new version of the skill prompt."""
        meta = self._load_meta()
        version = meta['current_version']

        # Save current version before overwriting
        version_file = os.path.join(self.versions_dir, f'v{version}.txt')
        with open(version_file, 'w', encoding='utf-8') as f:
            f.write(prompt_text)

        # Save amendment metadata
        amendment_file = os.path.join(self.versions_dir, f'v{version}.meta.json')
        with open(amendment_file, 'w', encoding='utf-8') as f:
            json.dump({
                'version': version,
                'created_at': datetime.now().isoformat(),
                'source': source,  # 'manual', 'auto', 'rollback'
                'rationale': rationale,
                'success_rate_at_creation': meta['success_rate'],
            }, f, indent=2)

        logger.info(f"Skill '{self.skill_id}' v{version} saved ({source})")
        return version

    def propose_amendment(self, new_prompt, rationale):
        """Propose a new version. Does NOT activate it — just saves as candidate."""
        meta = self._load_meta()
        next_version = meta['current_version'] + 1

        # Save as candidate (not yet active)
        candidate_file = os.path.join(self.versions_dir, f'v{next_version}.candidate.txt')
        with open(candidate_file, 'w', encoding='utf-8') as f:
            f.write(new_prompt)

        candidate_meta = os.path.join(self.versions_dir, f'v{next_version}.candidate.meta.json')
        with open(candidate_meta, 'w', encoding='utf-8') as f:
            json.dump({
                'version': next_version,
                'proposed_at': datetime.now().isoformat(),
                'rationale': rationale,
                'status': 'pending_review',
                'previous_success_rate': meta['success_rate'],
            }, f, indent=2)

        logger.info(f"Skill '{self.skill_id}' amendment proposed: v{next_version}")
        return next_version

    def promote_candidate(self, version):
        """Promote a candidate version to active."""
        meta = self._load_meta()

        # Save current prompt as the outgoing version
        candidate_file = os.path.join(self.versions_dir, f'v{version}.candidate.txt')
        if not os.path.exists(candidate_file):
            logger.error(f"No candidate v{version} found for '{self.skill_id}'")
            return False

        # Move candidate to active
        active_file = os.path.join(self.versions_dir, f'v{version}.txt')
        shutil.copy2(candidate_file, active_file)
        os.remove(candidate_file)

        # Update candidate meta
        candidate_meta = os.path.join(self.versions_dir, f'v{version}.candidate.meta.json')
        if os.path.exists(candidate_meta):
            active_meta = os.path.join(self.versions_dir, f'v{version}.meta.json')
            with open(candidate_meta, 'r') as f:
                cmeta = json.load(f)
            cmeta['status'] = 'promoted'
            cmeta['promoted_at'] = datetime.now().isoformat()
            with open(active_meta, 'w') as f:
                json.dump(cmeta, f, indent=2)
            os.remove(candidate_meta)

        meta['current_version'] = version
        meta['last_amended'] = datetime.now().isoformat()
        meta['amendment_count'] += 1
        self._save_meta(meta)

        logger.info(f"Skill '{self.skill_id}' promoted to v{version}")
        return True

    def rollback(self, to_version=None):
        """Rollback to a previous version."""
        meta = self._load_meta()

        if to_version is None:
            to_version = meta['current_version'] - 1

        if to_version < 1:
            logger.error(f"Cannot rollback '{self.skill_id}' below v1")
            return False

        version_file = os.path.join(self.versions_dir, f'v{to_version}.txt')
        if not os.path.exists(version_file):
            logger.error(f"Version {to_version} not found for '{self.skill_id}'")
            return False

        meta['current_version'] = to_version
        meta['last_amended'] = datetime.now().isoformat()
        meta['rollback_count'] += 1
        self._save_meta(meta)

        logger.info(f"Skill '{self.skill_id}' rolled back to v{to_version}")
        return True

    # ─── EVALUATE ──────────────────────────────────────────────

    def evaluate_version(self, version, window_runs=10):
        """Compare a version's performance against the previous version."""
        runs = self._load_all_runs()

        current = [r for r in runs if r.get('version') == version][-window_runs:]
        previous = [r for r in runs if r.get('version') == version - 1][-window_runs:]

        if len(current) < 3:
            return {'status': 'insufficient_data', 'current_runs': len(current)}

        current_rate = sum(1 for r in current if r['success']) / len(current)
        previous_rate = (sum(1 for r in previous if r['success']) / len(previous)) if previous else 0

        improvement = current_rate - previous_rate

        result = {
            'version': version,
            'current_success_rate': round(current_rate, 3),
            'previous_success_rate': round(previous_rate, 3),
            'improvement': round(improvement, 3),
            'current_sample_size': len(current),
            'previous_sample_size': len(previous),
            'verdict': 'improved' if improvement > 0.05 else
                       'regressed' if improvement < -0.05 else 'neutral',
        }

        # Save evaluation
        eval_file = os.path.join(self.versions_dir, f'v{version}.eval.json')
        with open(eval_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2)

        return result

    # ─── HELPERS ───────────────────────────────────────────────

    def _load_recent_runs(self, window_days=7):
        """Load runs from the last N days."""
        cutoff = datetime.now() - timedelta(days=window_days)
        return [r for r in self._load_all_runs()
                if datetime.fromisoformat(r['timestamp']) > cutoff]

    def _load_all_runs(self):
        """Load all runs from jsonl."""
        runs = []
        if not os.path.exists(self.runs_file):
            return runs
        try:
            with open(self.runs_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            runs.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        except FileNotFoundError:
            pass
        return runs

    def get_status(self):
        """Get a quick status summary."""
        meta = self._load_meta()
        return {
            'skill_id': self.skill_id,
            'display_name': self.display_name,
            'version': meta['current_version'],
            'total_runs': meta['total_runs'],
            'success_rate': meta['success_rate'],
            'needs_inspection': self.needs_inspection(),
            'last_run': meta['last_run'],
            'amendments': meta['amendment_count'],
            'rollbacks': meta['rollback_count'],
        }


class SkillEvolutionEngine:
    """Manages all tracked skills and runs the evolution loop."""

    def __init__(self, config, state_manager, comms_manager):
        init(config)
        self.config = config
        self.state = state_manager
        self.comms = comms_manager
        self.trackers = {}

        # Register known skills (cron prompts)
        self._register_known_skills()

    def _register_known_skills(self):
        """Register cron-based skills we want to track."""
        known = {
            'task-queue-worker': 'Task Queue Worker',
            'health-monitor': 'Kit Health Monitor',
            'morning-brief': 'Morning Brief',
            'evening-brief': 'Evening Brief',
            'nightly-debrief': 'Nightly Debrief',
            'weekly-ai-sweep': 'Weekly AI Sweep',
            'steel-price-monitor': 'Steel Price Monitor',
        }
        for skill_id, name in known.items():
            self.trackers[skill_id] = SkillTracker(skill_id, name)

    def get_tracker(self, skill_id, display_name=None):
        """Get or create a tracker for a skill."""
        if skill_id not in self.trackers:
            self.trackers[skill_id] = SkillTracker(skill_id, display_name)
        return self.trackers[skill_id]

    def run_inspection_sweep(self):
        """Check all tracked skills for underperformance."""
        issues = []
        for skill_id, tracker in self.trackers.items():
            if tracker.needs_inspection():
                report = tracker.inspect()
                if report:
                    issues.append(report)
                    logger.warning(
                        f"Skill '{skill_id}' underperforming: "
                        f"{report['success_rate']} success rate"
                    )

        if issues:
            # Notify about underperforming skills
            names = [i['skill_id'] for i in issues]
            self.comms.send_telegram(
                f"⚠️ {len(issues)} skill(s) need attention: {', '.join(names)}",
                priority=7
            )

        return issues

    def get_dashboard(self):
        """Get status of all tracked skills."""
        return [t.get_status() for t in self.trackers.values()]

    def record_cron_run(self, cron_id, skill_id, success, duration_seconds=0,
                        error=None, model=None, output_summary=None):
        """Convenience: record a cron execution as a skill run."""
        tracker = self.get_tracker(skill_id)
        return tracker.record_run(
            success=success,
            duration_seconds=duration_seconds,
            error=error,
            model=model,
            output_summary=output_summary,
        )
