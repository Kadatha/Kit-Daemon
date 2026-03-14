"""
Kit Daemon — Anticipation Engine
Time-aware proactive behavior. Learns patterns, prepares context.
"""
import logging
import json
import os
from datetime import datetime, time, timedelta

logger = logging.getLogger('kit-daemon.anticipation')


# Daily schedule (CDT times)
SCHEDULE = [
    {"time": "06:45", "action": "prep_morning_brief", "description": "Pre-generate morning brief data"},
    {"time": "07:00", "action": "morning_digest", "description": "Send morning digest"},
    {"time": "17:00", "action": "compile_day_results", "description": "Compile day's task results"},
    {"time": "17:30", "action": "evening_digest", "description": "Send evening digest"},
    {"time": "22:00", "action": "prep_next_day", "description": "Prepare next-day context"},
]


class AnticipationEngine:
    def __init__(self, config, state_manager, comms_manager, workflow_trigger=None):
        self.config = config
        self.state = state_manager
        self.comms = comms_manager
        self.workflow_trigger = workflow_trigger  # callback to queue workflows
        self._executed_today = set()

    def check_schedule(self):
        """Check if any scheduled actions should run now."""
        now = datetime.now()
        current_time = now.strftime("%H:%M")
        today = now.strftime("%Y-%m-%d")

        for entry in SCHEDULE:
            action_key = f"{today}_{entry['action']}"

            # Skip if already executed today
            if action_key in self._executed_today:
                continue

            # Check if it's time (within 5-minute window)
            sched_time = datetime.strptime(entry['time'], "%H:%M").time()
            now_time = now.time()

            if sched_time <= now_time <= (datetime.combine(now.date(), sched_time) + timedelta(minutes=5)).time():
                logger.info(f"Executing scheduled action: {entry['action']}")
                self._execute(entry['action'])
                self._executed_today.add(action_key)

        # Reset executed set at midnight
        if now.hour == 0 and now.minute < 5:
            self._executed_today.clear()

    def _execute(self, action):
        """Execute a scheduled action."""
        try:
            if action == 'prep_morning_brief':
                self._prep_morning_brief()
            elif action == 'morning_digest':
                self._send_morning_digest()
            elif action == 'compile_day_results':
                self._compile_day_results()
            elif action == 'evening_digest':
                self._send_evening_digest()
            elif action == 'prep_next_day':
                self._prep_next_day()
        except Exception as e:
            logger.error(f"Scheduled action {action} failed: {e}")

    def _prep_morning_brief(self):
        """Trigger the morning brief compilation workflow."""
        if self.workflow_trigger:
            self.workflow_trigger('morning-brief-compile', {
                'trigger': 'anticipation_schedule',
                'time': datetime.now().isoformat(),
            })
            logger.info("Morning brief workflow triggered")
        else:
            # Fallback: run compile_brief directly
            import subprocess
            script = os.path.join(self.config['paths']['daemon_home'], 'compile_brief.py')
            subprocess.run(['python', script], capture_output=True, timeout=30)
            logger.info("Morning brief compiled (direct)")

    def _send_morning_digest(self):
        """Send morning digest with overnight results."""
        # Flush any batched overnight messages
        sent = self.comms.flush_queue()
        if sent > 0:
            logger.info(f"Flushed {sent} overnight messages")

    def _compile_day_results(self):
        """Compile the day's task completion results."""
        workspace = self.config['paths']['workspace']
        scratch_dir = os.path.join(workspace, 'scratch')
        os.makedirs(scratch_dir, exist_ok=True)

        summary_path = os.path.join(scratch_dir, 'daily-summary.md')
        lines = [f"# Daily Summary — {datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]

        for tq_path in self.config['watch_paths']['task_queues']:
            try:
                with open(tq_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                project = os.path.basename(os.path.dirname(tq_path))
                lines.append(f"## {project}")
                # Extract completed items
                for line in content.split('\n'):
                    if '- [x]' in line:
                        lines.append(f"  ✅ {line.replace('- [x]', '').strip()}")
                    elif '- [ ] URGENT' in line:
                        lines.append(f"  🔴 {line.replace('- [ ] URGENT |', '').strip()}")
                    elif '- [ ] HIGH' in line:
                        lines.append(f"  🟡 {line.replace('- [ ] HIGH |', '').strip()}")
                lines.append("")
            except FileNotFoundError:
                pass

        with open(summary_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))

        logger.info(f"Daily summary compiled: {summary_path}")

    def _send_evening_digest(self):
        """Send evening digest with day's results."""
        sent = self.comms.flush_queue()
        if sent > 0:
            logger.info(f"Flushed {sent} daytime messages")

    def _prep_next_day(self):
        """Prepare context for tomorrow."""
        # Log pattern: what time did the user first message today?
        # (This would be populated by the comms module when messages arrive)
        pass

    def learn_message_pattern(self, timestamp):
        """Record when the user sends messages to learn timing patterns."""
        hour_minute = timestamp.strftime("%H:%M")
        self.state.record_pattern('the user_first_message_times', hour_minute)
        logger.debug(f"Recorded the user message time: {hour_minute}")

    def get_expected_first_message(self):
        """Estimate when the user typically first messages based on history."""
        times = self.state.get('learned_patterns', {}).get('the user_first_message_times', [])
        if len(times) < 3:
            return "07:00"  # default

        # Simple average of hour:minute
        minutes = []
        for t in times[-14:]:  # Last 2 weeks
            try:
                h, m = t.split(':')
                minutes.append(int(h) * 60 + int(m))
            except ValueError:
                pass

        if minutes:
            avg = sum(minutes) // len(minutes)
            return f"{avg // 60:02d}:{avg % 60:02d}"

        return "07:00"

