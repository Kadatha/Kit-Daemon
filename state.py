"""
Kit Daemon — Persistent State Manager
Maintains state across daemon restarts via state.json.
"""
import json
import os
import logging
from datetime import datetime

logger = logging.getLogger('kit-daemon.state')

DEFAULT_STATE = {
    "started_at": None,
    "last_heartbeat": None,
    "failure_counters": {},
    "message_queue": [],
    "messages_sent_this_hour": 0,
    "messages_hour_reset": None,
    "last_digest_morning": None,
    "last_digest_evening": None,
    "learned_patterns": {
        "the user_first_message_times": [],
        "task_completion_rates": {},
        "model_fallback_count": 0
    },
    "service_status": {
        "ollama": "unknown",
        "openclaw": "unknown",
        "gpu": "unknown"
    },
    "active_tasks": [],
    "total_health_checks": 0,
    "total_self_heals": 0,
    "total_messages_sent": 0
}


class StateManager:
    def __init__(self, state_file):
        self.state_file = state_file
        self.state = dict(DEFAULT_STATE)
        self.load()

    def load(self):
        """Load state from disk. Use defaults if missing or corrupt."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    saved = json.load(f)
                # Merge saved over defaults (preserves new keys)
                for key in DEFAULT_STATE:
                    if key in saved:
                        self.state[key] = saved[key]
                logger.info(f"State loaded from {self.state_file}")
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Could not load state: {e}. Using defaults.")
        else:
            logger.info("No state file found. Starting fresh.")

    def save(self):
        """Persist state to disk."""
        try:
            self.state['last_heartbeat'] = datetime.now().isoformat()
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            with open(self.state_file, 'w') as f:
                json.dump(self.state, f, indent=2, default=str)
        except IOError as e:
            logger.error(f"Could not save state: {e}")

    def get(self, key, default=None):
        return self.state.get(key, default)

    def set(self, key, value):
        self.state[key] = value

    def increment(self, key, amount=1):
        """Increment a numeric counter."""
        current = self.state.get(key, 0)
        self.state[key] = current + amount

    def get_failure_count(self, service_name):
        return self.state['failure_counters'].get(service_name, 0)

    def record_failure(self, service_name):
        """Increment failure counter for a service."""
        counters = self.state['failure_counters']
        counters[service_name] = counters.get(service_name, 0) + 1
        logger.warning(f"Failure recorded for {service_name}: {counters[service_name]}")
        return counters[service_name]

    def clear_failure(self, service_name):
        """Reset failure counter after successful recovery."""
        self.state['failure_counters'][service_name] = 0

    def queue_message(self, message, priority):
        """Add a message to the outbound queue."""
        self.state['message_queue'].append({
            'message': message,
            'priority': priority,
            'queued_at': datetime.now().isoformat()
        })

    def get_pending_messages(self, min_priority=0):
        """Get queued messages above a priority threshold."""
        return [m for m in self.state['message_queue'] if m['priority'] >= min_priority]

    def clear_message(self, index):
        """Remove a sent message from queue."""
        if 0 <= index < len(self.state['message_queue']):
            self.state['message_queue'].pop(index)

    def clear_sent_messages(self, sent_indices):
        """Remove multiple sent messages (indices in descending order)."""
        for i in sorted(sent_indices, reverse=True):
            self.clear_message(i)

    def update_service_status(self, service, status):
        self.state['service_status'][service] = status

    def record_pattern(self, key, value, max_history=30):
        """Record a data point for pattern learning."""
        patterns = self.state['learned_patterns']
        if key not in patterns:
            patterns[key] = []
        if isinstance(patterns[key], list):
            patterns[key].append(value)
            # Keep only recent history
            patterns[key] = patterns[key][-max_history:]

