"""
Kit Daemon — Ambient Learning Engine
Extracts patterns from Kit's operations to become smarter over time.

Jarvis-level awareness: not just tracking what happened,
but building an internal model of the user's world.

Tracks:
- Message timing patterns (when does the user engage?)
- Topic frequency (what does he care about most?)
- Task success patterns (what works, what doesn't?)
- Model performance (which model handles what best?)
- Workflow patterns (which events lead to which outcomes?)
- Decision patterns (what does the user approve/reject?)
"""
import json
import logging
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger('kit-daemon.ambient')


class AmbientLearning:
    """Learns from Kit's operational patterns without explicit instruction."""

    def __init__(self, config, state_manager):
        self.config = config
        self.state = state_manager
        self.insights_dir = os.path.join(config['paths']['daemon_home'], 'insights')
        os.makedirs(self.insights_dir, exist_ok=True)

    # ─── DATA COLLECTION ───────────────────────────────────────

    def record_interaction(self, interaction_type, metadata):
        """Record any interaction for pattern analysis.

        interaction_type: 'message_received', 'message_sent', 'task_completed',
                         'cron_run', 'workflow_triggered', 'error_occurred',
                         'file_changed', 'model_used', 'approval', 'rejection'
        """
        record = {
            'timestamp': datetime.now().isoformat(),
            'hour': datetime.now().hour,
            'day_of_week': datetime.now().strftime('%A'),
            'type': interaction_type,
            **metadata,
        }

        # Append to daily interaction log
        date_str = datetime.now().strftime('%Y-%m-%d')
        log_file = os.path.join(self.insights_dir, f'interactions_{date_str}.jsonl')
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record) + '\n')

        # Update running counters in state
        self._update_counters(record)

    def _update_counters(self, record):
        """Update running counters for quick pattern access."""
        counters = self.state.get('ambient_counters', {})

        # Hour-of-day activity
        hour_key = f"hour_{record['hour']}"
        counters[hour_key] = counters.get(hour_key, 0) + 1

        # Day-of-week activity
        day_key = f"day_{record['day_of_week']}"
        counters[day_key] = counters.get(day_key, 0) + 1

        # Interaction type counts
        type_key = f"type_{record['type']}"
        counters[type_key] = counters.get(type_key, 0) + 1

        self.state.set('ambient_counters', counters)

    # ─── PATTERN EXTRACTION ────────────────────────────────────

    def analyze_patterns(self, window_days=14):
        """Analyze collected data and extract actionable patterns."""
        interactions = self._load_interactions(window_days)
        if len(interactions) < 10:
            return {'status': 'insufficient_data', 'count': len(interactions)}

        patterns = {
            'generated_at': datetime.now().isoformat(),
            'window_days': window_days,
            'total_interactions': len(interactions),
            'activity_profile': self._activity_profile(interactions),
            'peak_hours': self._peak_hours(interactions),
            'model_effectiveness': self._model_patterns(interactions),
            'task_patterns': self._task_patterns(interactions),
            'failure_patterns': self._failure_patterns(interactions),
            'recommendations': [],
        }

        # Generate Jarvis-style recommendations
        patterns['recommendations'] = self._generate_recommendations(patterns)

        # Save patterns
        patterns_file = os.path.join(self.insights_dir, 'current_patterns.json')
        with open(patterns_file, 'w', encoding='utf-8') as f:
            json.dump(patterns, f, indent=2)

        # Also save a human-readable version
        self._save_readable_insights(patterns)

        logger.info(f"Pattern analysis complete: {len(patterns['recommendations'])} recommendations")
        return patterns

    def _activity_profile(self, interactions):
        """Build an hour-by-hour activity heatmap."""
        by_hour = Counter()
        for i in interactions:
            by_hour[i.get('hour', 0)] += 1

        # Normalize to percentage
        total = sum(by_hour.values())
        return {
            str(h): round(count / max(1, total), 3)
            for h, count in sorted(by_hour.items())
        }

    def _peak_hours(self, interactions):
        """Identify the user's most active hours."""
        by_hour = Counter()
        for i in interactions:
            if i.get('type') in ('message_received', 'approval', 'rejection'):
                by_hour[i.get('hour', 0)] += 1

        if not by_hour:
            return {'morning': '07:00-09:00', 'evening': '17:00-19:00'}

        sorted_hours = sorted(by_hour.items(), key=lambda x: -x[1])
        peak = sorted_hours[:3]
        return {
            'top_hours': [h for h, _ in peak],
            'most_active': f"{peak[0][0]:02d}:00" if peak else "07:00",
            'activity_count': dict(sorted_hours[:6]),
        }

    def _model_patterns(self, interactions):
        """Analyze which models perform best at which tasks."""
        model_tasks = defaultdict(lambda: {'success': 0, 'failure': 0, 'total': 0})

        for i in interactions:
            model = i.get('model')
            if not model:
                continue
            key = f"{model}"
            model_tasks[key]['total'] += 1
            if i.get('success'):
                model_tasks[key]['success'] += 1
            else:
                model_tasks[key]['failure'] += 1

        result = {}
        for model, stats in model_tasks.items():
            rate = stats['success'] / max(1, stats['total'])
            result[model] = {
                **stats,
                'success_rate': round(rate, 3),
            }
        return result

    def _task_patterns(self, interactions):
        """Identify which task types are most common and most successful."""
        task_types = defaultdict(lambda: {'count': 0, 'success': 0})

        for i in interactions:
            task = i.get('task_type') or i.get('type', 'unknown')
            task_types[task]['count'] += 1
            if i.get('success', True):  # Default to success for non-task events
                task_types[task]['success'] += 1

        return {
            task: {
                **stats,
                'success_rate': round(stats['success'] / max(1, stats['count']), 3)
            }
            for task, stats in sorted(task_types.items(), key=lambda x: -x[1]['count'])[:10]
        }

    def _failure_patterns(self, interactions):
        """Identify recurring failure modes."""
        failures = [i for i in interactions if i.get('success') is False]
        if not failures:
            return {'total_failures': 0}

        error_types = Counter()
        failure_hours = Counter()
        failure_models = Counter()

        for f in failures:
            error_types[f.get('error', 'unknown')[:80]] += 1
            failure_hours[f.get('hour', 0)] += 1
            failure_models[f.get('model', 'unknown')] += 1

        return {
            'total_failures': len(failures),
            'top_errors': dict(error_types.most_common(5)),
            'failure_prone_hours': dict(failure_hours.most_common(3)),
            'failure_prone_models': dict(failure_models.most_common(3)),
        }

    # ─── JARVIS-STYLE RECOMMENDATIONS ──────────────────────────

    def _generate_recommendations(self, patterns):
        """Generate actionable recommendations from patterns.
        This is the Jarvis layer — not just data, but ADVICE."""
        recs = []

        # Activity-based recommendations
        peak = patterns.get('peak_hours', {})
        if peak.get('most_active'):
            hour = int(peak['most_active'].split(':')[0])
            if hour >= 7:
                prep_time = f"{hour-1:02d}:45"
                recs.append({
                    'type': 'schedule',
                    'priority': 'medium',
                    'insight': f"the user typically starts around {peak['most_active']}. "
                              f"Pre-compile context by {prep_time}.",
                    'action': f'Set anticipation prep to {prep_time}',
                })

        # Model recommendations
        model_stats = patterns.get('model_effectiveness', {})
        for model, stats in model_stats.items():
            if stats['total'] >= 5 and stats['success_rate'] < 0.5:
                recs.append({
                    'type': 'model',
                    'priority': 'high',
                    'insight': f"Model '{model}' has {stats['success_rate']:.0%} success rate "
                              f"over {stats['total']} runs. Consider replacing for these tasks.",
                    'action': f'Review tasks assigned to {model}',
                })

        # Failure pattern recommendations
        failures = patterns.get('failure_patterns', {})
        if failures.get('total_failures', 0) > 5:
            top_error = list(failures.get('top_errors', {}).items())
            if top_error:
                err, count = top_error[0]
                recs.append({
                    'type': 'reliability',
                    'priority': 'high',
                    'insight': f"Recurring failure ({count}x): {err[:100]}",
                    'action': 'Investigate and add to LEARNINGS.md',
                })

        # Task completion recommendations
        tasks = patterns.get('task_patterns', {})
        for task, stats in tasks.items():
            if stats['count'] >= 5 and stats['success_rate'] < 0.7:
                recs.append({
                    'type': 'task',
                    'priority': 'medium',
                    'insight': f"Task type '{task}' succeeds only {stats['success_rate']:.0%} of the time.",
                    'action': f'Review {task} skill/prompt for improvements',
                })

        return recs

    # ─── HUMAN-READABLE OUTPUT ─────────────────────────────────

    def _save_readable_insights(self, patterns):
        """Save a markdown version Kit can include in morning briefs."""
        lines = [
            f"# Kit Insights — {datetime.now().strftime('%Y-%m-%d')}",
            f"Based on {patterns['total_interactions']} interactions "
            f"over {patterns['window_days']} days",
            "",
        ]

        # Recommendations
        recs = patterns.get('recommendations', [])
        if recs:
            lines.append("## Recommendations")
            for r in recs:
                icon = "🔴" if r['priority'] == 'high' else "🟡" if r['priority'] == 'medium' else "🔵"
                lines.append(f"- {icon} **{r['type'].title()}**: {r['insight']}")
                lines.append(f"  → Action: {r['action']}")
            lines.append("")

        # Peak hours
        peak = patterns.get('peak_hours', {})
        if peak.get('top_hours'):
            lines.append("## the user's Active Hours")
            for h in peak['top_hours']:
                lines.append(f"- {h:02d}:00")
            lines.append("")

        # Model performance
        models = patterns.get('model_effectiveness', {})
        if models:
            lines.append("## Model Performance")
            for model, stats in models.items():
                emoji = "✅" if stats['success_rate'] >= 0.8 else "⚠️"
                lines.append(f"- {emoji} {model}: {stats['success_rate']:.0%} "
                           f"({stats['total']} runs)")
            lines.append("")

        insights_file = os.path.join(
            self.config['paths']['workspace'], 'scratch', 'kit-insights.md'
        )
        with open(insights_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))

    # ─── DATA LOADING ──────────────────────────────────────────

    def _load_interactions(self, window_days=14):
        """Load interaction records from the last N days."""
        interactions = []
        for day_offset in range(window_days):
            date = datetime.now() - timedelta(days=day_offset)
            date_str = date.strftime('%Y-%m-%d')
            log_file = os.path.join(self.insights_dir, f'interactions_{date_str}.jsonl')
            if os.path.exists(log_file):
                with open(log_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                interactions.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass
        return interactions

    # ─── JARVIS SITUATIONAL AWARENESS ──────────────────────────

    def get_situational_context(self):
        """What Jarvis would know right now.
        Returns a context blob for Kit to use in responses."""
        now = datetime.now()
        counters = self.state.get('ambient_counters', {})

        # Time awareness
        is_workday = now.weekday() < 5
        hour = now.hour
        time_of_day = (
            'early_morning' if hour < 7 else
            'morning' if hour < 12 else
            'afternoon' if hour < 17 else
            'evening' if hour < 21 else
            'night'
        )

        # Recent activity level
        today_key = f"interactions_{now.strftime('%Y-%m-%d')}"
        today_file = os.path.join(self.insights_dir, f'{today_key}.jsonl')
        today_count = 0
        if os.path.exists(today_file):
            with open(today_file, 'r') as f:
                today_count = sum(1 for _ in f)

        return {
            'time_of_day': time_of_day,
            'is_workday': is_workday,
            'day_name': now.strftime('%A'),
            'hour': hour,
            'today_interactions': today_count,
            'the user_likely_active': 7 <= hour <= 23,
            'brief_ready': os.path.exists(
                os.path.join(self.config['paths']['workspace'], 'scratch', 'morning-brief.md')
            ),
        }

