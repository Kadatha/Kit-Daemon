"""
Kit Daemon — Self-Model Engine
Reads and maintains SELF-MODEL.md — Kit's capability and performance tracker.

Provides:
- Capability queries ("am I good at X?")
- Performance metric updates from trace store
- Weekly reflection updates to SELF-MODEL.md
- Integration with daemon state for runtime access
"""
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta

logger = logging.getLogger('kit-daemon.self_model')


class SelfModel:
    """Reads SELF-MODEL.md and provides capability queries + metric updates."""

    def __init__(self, config, state_manager, trace_store=None):
        self.config = config
        self.state = state_manager
        self.trace_store = trace_store
        self.model_file = os.path.join(
            config['paths']['workspace'], 'SELF-MODEL.md'
        )
        self.capabilities = {}
        self.performance = {}
        self.last_loaded = None

        # Load on init
        self._load_model()

    def _load_model(self):
        """Parse SELF-MODEL.md into structured data."""
        if not os.path.exists(self.model_file):
            logger.warning(f"SELF-MODEL.md not found at {self.model_file}")
            return

        try:
            with open(self.model_file, 'r', encoding='utf-8') as f:
                content = f.read()

            self._parse_capabilities(content)
            self._parse_performance_table(content)
            self.last_loaded = datetime.now().isoformat()
            self.state.set('self_model_loaded', True)
            logger.info(
                f"Self-model loaded: {len(self.capabilities)} capabilities, "
                f"{len(self.performance)} performance entries"
            )
        except Exception as e:
            logger.error(f"Failed to load self-model: {e}")

    def _parse_capabilities(self, content):
        """Extract capability sections from markdown."""
        sections = {
            'strengths': r'### Strengths.*?\n(.*?)(?=###|\Z)',
            'developing': r'### Developing Areas.*?\n(.*?)(?=###|\Z)',
            'limitations': r'### Known Limitations.*?\n(.*?)(?=###|\Z)',
        }
        for section, pattern in sections.items():
            match = re.search(pattern, content, re.DOTALL)
            if match:
                items = re.findall(
                    r'- \*\*(.+?)\*\*:\s*(.+?)(?=\n- |\Z)',
                    match.group(1), re.DOTALL
                )
                for name, desc in items:
                    self.capabilities[name.lower().strip()] = {
                        'name': name.strip(),
                        'tier': section,
                        'description': desc.strip().replace('\n', ' '),
                    }

    def _parse_performance_table(self, content):
        """Extract performance table from markdown."""
        table_match = re.search(
            r'\| Category.*?\n\|[-| ]+\n(.*?)(?=\n\n|\n###|\Z)',
            content, re.DOTALL
        )
        if not table_match:
            return

        for row in table_match.group(1).strip().split('\n'):
            cols = [c.strip() for c in row.split('|') if c.strip()]
            if len(cols) >= 4:
                category = cols[0].lower()
                try:
                    rate_str = cols[1].replace('%', '').strip()
                    success_rate = int(rate_str) / 100.0
                except (ValueError, IndexError):
                    success_rate = 0.0
                self.performance[category] = {
                    'success_rate': success_rate,
                    'common_failures': cols[2] if len(cols) > 2 else '',
                    'improvement_areas': cols[3] if len(cols) > 3 else '',
                }

    def query_capability(self, topic):
        """Check if Kit is good at a given topic. Returns assessment dict."""
        topic_lower = topic.lower()

        # Direct match
        if topic_lower in self.capabilities:
            cap = self.capabilities[topic_lower]
            return {
                'found': True,
                'topic': cap['name'],
                'tier': cap['tier'],
                'confident': cap['tier'] == 'strengths',
                'description': cap['description'],
            }

        # Fuzzy match — check if topic appears in any capability name or description
        for key, cap in self.capabilities.items():
            if topic_lower in key or topic_lower in cap['description'].lower():
                return {
                    'found': True,
                    'topic': cap['name'],
                    'tier': cap['tier'],
                    'confident': cap['tier'] == 'strengths',
                    'description': cap['description'],
                }

        # Check performance table
        for category, perf in self.performance.items():
            if topic_lower in category:
                return {
                    'found': True,
                    'topic': category,
                    'tier': 'strengths' if perf['success_rate'] >= 0.85 else
                            'developing' if perf['success_rate'] >= 0.6 else
                            'limitations',
                    'confident': perf['success_rate'] >= 0.85,
                    'success_rate': perf['success_rate'],
                    'description': perf.get('improvement_areas', ''),
                }

        return {
            'found': False,
            'topic': topic,
            'tier': 'unknown',
            'confident': False,
            'description': 'No data available for this capability.',
        }

    def update_from_traces(self):
        """Pull recent performance data from trace store and update metrics."""
        if not self.trace_store:
            logger.debug("No trace store available for self-model update")
            return {}

        try:
            model_stats = self.trace_store.get_model_stats()
            traces = self.trace_store.list_traces(limit=100)

            # Aggregate by task class
            task_stats = {}
            for trace in traces:
                tc = trace.get('task_class', 'general')
                if tc not in task_stats:
                    task_stats[tc] = {'total': 0, 'success': 0, 'total_latency': 0.0}
                task_stats[tc]['total'] += 1
                if trace.get('outcome') == 'success':
                    task_stats[tc]['success'] += 1
                task_stats[tc]['total_latency'] += trace.get('total_latency', 0)

            # Calculate rates
            summary = {}
            for tc, stats in task_stats.items():
                rate = stats['success'] / max(1, stats['total'])
                avg_latency = stats['total_latency'] / max(1, stats['total'])
                summary[tc] = {
                    'success_rate': round(rate, 3),
                    'total_runs': stats['total'],
                    'avg_latency': round(avg_latency, 1),
                }

            # Store in daemon state for runtime access
            self.state.set('self_model_task_stats', summary)
            self.state.set('self_model_last_update', datetime.now().isoformat())

            logger.info(
                f"Self-model updated from traces: "
                f"{len(summary)} task classes, {len(traces)} traces analyzed"
            )
            return summary

        except Exception as e:
            logger.error(f"Failed to update self-model from traces: {e}")
            return {}

    def generate_weekly_reflection(self):
        """Generate updated SELF-MODEL.md content from trace data."""
        if not self.trace_store:
            return None

        try:
            stats = self.update_from_traces()
            if not stats:
                return None

            # Read current file
            if not os.path.exists(self.model_file):
                return None

            with open(self.model_file, 'r', encoding='utf-8') as f:
                content = f.read()

            # Update the "Recent Performance Data" section
            today = datetime.now()
            week_start = (today - timedelta(days=7)).strftime('%Y-%m-%d')
            week_end = today.strftime('%Y-%m-%d')

            # Build performance summary
            lines = []
            lines.append(f"### This Week ({week_start} to {week_end})")

            total_tasks = sum(s['total_runs'] for s in stats.values())
            total_success = sum(
                s['total_runs'] * s['success_rate'] for s in stats.values()
            )
            overall_rate = total_success / max(1, total_tasks)

            lines.append(f"- Tasks analyzed: {total_tasks}")
            lines.append(f"- Overall success rate: {overall_rate:.0%}")

            # Top performing categories
            sorted_stats = sorted(
                stats.items(), key=lambda x: x[1]['success_rate'], reverse=True
            )
            if sorted_stats:
                top = sorted_stats[0]
                lines.append(
                    f"- Strongest category: {top[0]} "
                    f"({top[1]['success_rate']:.0%} success, "
                    f"{top[1]['total_runs']} runs)"
                )
            if len(sorted_stats) > 1:
                bottom = sorted_stats[-1]
                if bottom[1]['success_rate'] < 0.8:
                    lines.append(
                        f"- Needs improvement: {bottom[0]} "
                        f"({bottom[1]['success_rate']:.0%} success)"
                    )

            new_section = '\n'.join(lines)

            # Replace the "This Week" section
            pattern = r'### This Week \(.*?\)\n.*?(?=\n### Trends|\Z)'
            updated = re.sub(pattern, new_section + '\n', content, flags=re.DOTALL)

            # Update the "Last Updated" date
            updated = re.sub(
                r'\*Last Updated: .*?\*',
                f'*Last Updated: {week_end}*',
                updated
            )

            # Update next weekly update date
            next_update = (today + timedelta(days=7)).strftime('%Y-%m-%d')
            updated = re.sub(
                r'### Next Weekly Update: .*',
                f'### Next Weekly Update: {next_update}',
                updated
            )

            # Write back
            with open(self.model_file, 'w', encoding='utf-8') as f:
                f.write(updated)

            logger.info(f"Self-model weekly reflection written to {self.model_file}")
            return stats

        except Exception as e:
            logger.error(f"Weekly reflection failed: {e}")
            return None

    def get_summary(self):
        """Get a quick summary of the self-model state."""
        return {
            'loaded': self.last_loaded is not None,
            'capabilities_count': len(self.capabilities),
            'performance_categories': len(self.performance),
            'tiers': {
                'strengths': sum(
                    1 for c in self.capabilities.values() if c['tier'] == 'strengths'
                ),
                'developing': sum(
                    1 for c in self.capabilities.values() if c['tier'] == 'developing'
                ),
                'limitations': sum(
                    1 for c in self.capabilities.values() if c['tier'] == 'limitations'
                ),
            },
            'last_loaded': self.last_loaded,
        }
