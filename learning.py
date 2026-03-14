"""
Kit Daemon — Learning Loop
Tracks patterns from Kit's work to improve over time.
"""
import json
import logging
import os
from datetime import datetime

logger = logging.getLogger('kit-daemon.learning')


class LearningEngine:
    def __init__(self, config, state_manager):
        self.config = config
        self.state = state_manager
        self.metrics_file = os.path.join(
            config['paths']['workspace'], 'scratch', 'kit-metrics.json'
        )

    def record_task_completion(self, project, task_name, duration_seconds, success):
        """Record a task completion for pattern analysis."""
        key = f"task_{project}"
        self.state.record_pattern(key, {
            'task': task_name,
            'duration': duration_seconds,
            'success': success,
            'timestamp': datetime.now().isoformat()
        })

    def record_model_usage(self, model, task_type, success):
        """Track which models succeed at which tasks."""
        key = f"model_{model}"
        self.state.record_pattern(key, {
            'task_type': task_type,
            'success': success,
            'timestamp': datetime.now().isoformat()
        })

    def record_cron_fix(self, cron_id, problem, fix_applied):
        """Track cron fixes for self-improvement."""
        self.state.record_pattern('cron_fixes', {
            'cron_id': cron_id,
            'problem': problem,
            'fix': fix_applied,
            'timestamp': datetime.now().isoformat()
        })

    def get_project_stats(self):
        """Get task completion stats per project."""
        patterns = self.state.get('learned_patterns', {})
        stats = {}

        for key, values in patterns.items():
            if key.startswith('task_') and isinstance(values, list):
                project = key.replace('task_', '')
                total = len(values)
                successes = sum(1 for v in values if v.get('success'))
                avg_duration = (sum(v.get('duration', 0) for v in values) / max(1, total))

                stats[project] = {
                    'total_tasks': total,
                    'success_rate': successes / max(1, total),
                    'avg_duration_seconds': round(avg_duration, 1)
                }

        return stats

    def get_model_stats(self):
        """Get success rates per model."""
        patterns = self.state.get('learned_patterns', {})
        stats = {}

        for key, values in patterns.items():
            if key.startswith('model_') and isinstance(values, list):
                model = key.replace('model_', '')
                total = len(values)
                successes = sum(1 for v in values if v.get('success'))
                stats[model] = {
                    'total_uses': total,
                    'success_rate': successes / max(1, total)
                }

        return stats

    def save_metrics(self):
        """Save current metrics to scratch file for Kit to read."""
        try:
            os.makedirs(os.path.dirname(self.metrics_file), exist_ok=True)

            metrics = {
                'generated_at': datetime.now().isoformat(),
                'project_stats': self.get_project_stats(),
                'model_stats': self.get_model_stats(),
                'daemon_uptime': {
                    'total_health_checks': self.state.get('total_health_checks', 0),
                    'total_self_heals': self.state.get('total_self_heals', 0),
                    'total_messages_sent': self.state.get('total_messages_sent', 0),
                },
                'service_status': self.state.get('service_status', {}),
                'failure_counters': self.state.get('failure_counters', {}),
            }

            with open(self.metrics_file, 'w', encoding='utf-8') as f:
                json.dump(metrics, f, indent=2)

            logger.debug(f"Metrics saved to {self.metrics_file}")

        except Exception as e:
            logger.error(f"Could not save metrics: {e}")
