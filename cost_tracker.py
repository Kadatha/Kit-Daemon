"""
Kit Daemon — API Cost Tracker
Tracks cloud API spend from cron run token usage + estimates main session costs.
Shows daily/weekly/monthly burn rate on dashboard.

Pricing (per 1M tokens, as of March 2026):
  Claude Opus 4:    input $15.00,  output $75.00
  Claude Sonnet 4:  input $3.00,   output $15.00
  Claude Haiku 3.5: input $0.80,   output $4.00
  Ollama (local):   $0.00
"""
import json
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta

logger = logging.getLogger('kit-daemon.cost')

# Pricing per 1M tokens
PRICING = {
    # Opus variants
    'claude-opus-4-6': {'input': 15.0, 'output': 75.0},
    'anthropic/claude-opus-4-6': {'input': 15.0, 'output': 75.0},
    'claude-opus': {'input': 15.0, 'output': 75.0},
    # Sonnet variants
    'claude-sonnet-4-20250514': {'input': 3.0, 'output': 15.0},
    'anthropic/claude-sonnet-4-20250514': {'input': 3.0, 'output': 15.0},
    'claude-sonnet': {'input': 3.0, 'output': 15.0},
    # Haiku
    'claude-3-5-haiku': {'input': 0.8, 'output': 4.0},
}

# Default for unknown cloud models
DEFAULT_CLOUD_PRICING = {'input': 3.0, 'output': 15.0}


def is_local_model(model: str) -> bool:
    """Check if a model is local (free)."""
    if not model:
        return True
    model_lower = model.lower()
    local_indicators = ['ollama', 'qwen', 'llama', 'mistral', 'nomic', 'phi']
    return any(ind in model_lower for ind in local_indicators)


def get_pricing(model: str) -> dict:
    """Get pricing for a model."""
    if is_local_model(model):
        return {'input': 0.0, 'output': 0.0}

    # Try exact match
    if model in PRICING:
        return PRICING[model]

    # Try partial match
    for key, price in PRICING.items():
        if key in model or model in key:
            return price

    # Unknown cloud model — use conservative estimate
    return DEFAULT_CLOUD_PRICING


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate cost for a single API call."""
    pricing = get_pricing(model)
    cost = (input_tokens / 1_000_000) * pricing['input'] + \
           (output_tokens / 1_000_000) * pricing['output']
    return cost


class CostTracker:
    """Tracks API costs from cron runs and estimates session costs."""

    def __init__(self, config: dict):
        self.config = config
        self.data_dir = os.path.join(config['paths']['daemon_home'], 'costs')
        os.makedirs(self.data_dir, exist_ok=True)

    def record_cron_cost(self, run_data: dict):
        """Record cost from a cron run entry."""
        model = run_data.get('model', '')
        usage = run_data.get('usage', {})
        input_tokens = usage.get('input_tokens', 0)
        output_tokens = usage.get('output_tokens', 0)

        if is_local_model(model):
            cost = 0.0
        else:
            cost = calculate_cost(model, input_tokens, output_tokens)

        entry = {
            'timestamp': datetime.now().isoformat(),
            'source': 'cron',
            'job_id': run_data.get('jobId', ''),
            'model': model,
            'input_tokens': input_tokens,
            'output_tokens': output_tokens,
            'cost': cost,
            'local': is_local_model(model),
        }

        self._append_entry(entry)
        return cost

    def record_session_estimate(self, model: str, input_tokens: int, output_tokens: int):
        """Record estimated cost for main session usage."""
        cost = calculate_cost(model, input_tokens, output_tokens)

        entry = {
            'timestamp': datetime.now().isoformat(),
            'source': 'session_estimate',
            'model': model,
            'input_tokens': input_tokens,
            'output_tokens': output_tokens,
            'cost': cost,
            'local': is_local_model(model),
        }

        self._append_entry(entry)
        return cost

    def _append_entry(self, entry: dict):
        """Append a cost entry to today's log."""
        date_str = datetime.now().strftime('%Y-%m-%d')
        filepath = os.path.join(self.data_dir, f'costs_{date_str}.jsonl')
        with open(filepath, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry) + '\n')

    def get_daily_summary(self, date_str: str = None) -> dict:
        """Get cost summary for a specific day."""
        if date_str is None:
            date_str = datetime.now().strftime('%Y-%m-%d')

        filepath = os.path.join(self.data_dir, f'costs_{date_str}.jsonl')
        entries = self._read_entries(filepath)

        total_cost = 0.0
        total_input = 0
        total_output = 0
        by_model = defaultdict(lambda: {'cost': 0.0, 'input': 0, 'output': 0, 'calls': 0})
        by_source = defaultdict(lambda: {'cost': 0.0, 'calls': 0})
        local_calls = 0
        cloud_calls = 0

        for e in entries:
            cost = e.get('cost', 0)
            total_cost += cost
            total_input += e.get('input_tokens', 0)
            total_output += e.get('output_tokens', 0)

            model = e.get('model', 'unknown')
            by_model[model]['cost'] += cost
            by_model[model]['input'] += e.get('input_tokens', 0)
            by_model[model]['output'] += e.get('output_tokens', 0)
            by_model[model]['calls'] += 1

            source = e.get('source', 'unknown')
            by_source[source]['cost'] += cost
            by_source[source]['calls'] += 1

            if e.get('local', False):
                local_calls += 1
            else:
                cloud_calls += 1

        total_calls = local_calls + cloud_calls
        local_pct = (local_calls / total_calls * 100) if total_calls > 0 else 0

        return {
            'date': date_str,
            'total_cost': total_cost,
            'total_input_tokens': total_input,
            'total_output_tokens': total_output,
            'total_calls': total_calls,
            'local_calls': local_calls,
            'cloud_calls': cloud_calls,
            'local_pct': local_pct,
            'by_model': dict(by_model),
            'by_source': dict(by_source),
        }

    def get_weekly_summary(self) -> dict:
        """Get cost summary for the last 7 days."""
        total_cost = 0.0
        daily = []
        for i in range(7):
            date = datetime.now() - timedelta(days=i)
            date_str = date.strftime('%Y-%m-%d')
            day_summary = self.get_daily_summary(date_str)
            total_cost += day_summary['total_cost']
            daily.append(day_summary)

        return {
            'total_cost': total_cost,
            'daily_avg': total_cost / 7,
            'projected_monthly': (total_cost / 7) * 30,
            'days': daily,
        }

    def get_dashboard_data(self) -> dict:
        """Get data formatted for the dashboard card."""
        today = self.get_daily_summary()
        weekly = self.get_weekly_summary()

        return {
            'today_cost': today['total_cost'],
            'today_calls': today['total_calls'],
            'today_local_pct': today['local_pct'],
            'today_cloud_calls': today['cloud_calls'],
            'weekly_cost': weekly['total_cost'],
            'daily_avg': weekly['daily_avg'],
            'projected_monthly': weekly['projected_monthly'],
            'top_model': max(today['by_model'].items(),
                           key=lambda x: x[1]['cost'])[0] if today['by_model'] else 'none',
            'top_model_cost': max(today['by_model'].values(),
                                key=lambda x: x['cost'])['cost'] if today['by_model'] else 0,
        }

    def _read_entries(self, filepath: str) -> list:
        """Read all entries from a JSONL file."""
        entries = []
        if os.path.exists(filepath):
            with open(filepath, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        return entries

    def ingest_cron_runs(self, runs: list):
        """Bulk ingest cron run data for cost tracking."""
        total = 0.0
        for run in runs:
            if run.get('action') == 'finished':
                cost = self.record_cron_cost(run)
                total += cost
        return total
