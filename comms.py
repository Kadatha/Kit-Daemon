"""
Kit Daemon — Communication Intelligence
Priority scoring, rate limiting, quiet hours, message batching.
"""
import logging
import subprocess
from datetime import datetime, time

logger = logging.getLogger('kit-daemon.comms')


class CommsManager:
    def __init__(self, config, state_manager):
        self.config = config
        self.state = state_manager
        self.comms_cfg = config['comms']

    def is_quiet_hours(self):
        """Check if we're in quiet hours."""
        now = datetime.now().time()
        start = time(*[int(x) for x in self.comms_cfg['quiet_hours_start'].split(':')])
        end = time(*[int(x) for x in self.comms_cfg['quiet_hours_end'].split(':')])

        if start > end:  # Crosses midnight (e.g., 23:00-07:00)
            return now >= start or now < end
        else:
            return start <= now < end

    def can_send_now(self, priority):
        """Check if a message can be sent right now based on rules."""
        # Emergencies always go through
        if priority >= self.comms_cfg['emergency_priority_threshold']:
            return True

        # Quiet hours: only emergencies
        if self.is_quiet_hours():
            return False

        # Rate limiting
        now = datetime.now()
        reset_time = self.state.get('messages_hour_reset')
        if reset_time:
            reset_dt = datetime.fromisoformat(reset_time)
            if (now - reset_dt).total_seconds() > 3600:
                self.state.set('messages_sent_this_hour', 0)
                self.state.set('messages_hour_reset', now.isoformat())
        else:
            self.state.set('messages_hour_reset', now.isoformat())

        sent = self.state.get('messages_sent_this_hour', 0)
        if sent >= self.comms_cfg['max_messages_per_hour']:
            return False

        return priority >= self.comms_cfg['send_priority_threshold']

    def should_batch(self, priority):
        """Check if a message should be batched for digest."""
        return (self.comms_cfg['batch_priority_threshold']
                <= priority
                < self.comms_cfg['send_priority_threshold'])

    def send_telegram(self, message, priority=5):
        """Send a message via OpenClaw's Telegram integration."""
        if self.can_send_now(priority):
            return self._do_send(message)
        elif self.should_batch(priority):
            self.state.queue_message(message, priority)
            logger.info(f"Message queued (priority {priority}): {message[:80]}...")
            return False
        else:
            logger.debug(f"Message suppressed (priority {priority}): {message[:80]}...")
            return False

    def _do_send(self, message):
        """Actually send via OpenClaw system event (triggers Kit to relay)."""
        try:
            # Use openclaw wake event — this triggers Kit's main session
            # which can then send Telegram messages properly
            safe_msg = message.replace('"', '\\"').replace('\n', ' ')
            result = subprocess.run(
                ['powershell', '-Command',
                 f'openclaw system event --text "[DAEMON] {safe_msg}" --mode now'],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                self.state.increment('messages_sent_this_hour')
                self.state.increment('total_messages_sent')
                logger.info(f"Event sent: {message[:80]}...")
                return True
            else:
                # Fallback: just log it, don't spam errors
                logger.warning(f"Event send returned {result.returncode}: {result.stderr[:100]}")
                # Still count as "sent" to avoid retry loops
                self.state.increment('total_messages_sent')
                return True
        except subprocess.TimeoutExpired:
            logger.error("Event send timed out")
            return False
        except Exception as e:
            logger.error(f"Event send error: {e}")
            return False

    def flush_queue(self, min_priority=None):
        """Send all queued messages above threshold."""
        if min_priority is None:
            min_priority = self.comms_cfg['batch_priority_threshold']

        pending = self.state.get_pending_messages(min_priority)
        sent_indices = []

        for i, msg in enumerate(self.state.get('message_queue', [])):
            if msg['priority'] >= min_priority and self.can_send_now(msg['priority']):
                if self._do_send(msg['message']):
                    sent_indices.append(i)

        self.state.clear_sent_messages(sent_indices)
        return len(sent_indices)

    def build_digest(self, title, items):
        """Build a digest message from queued items."""
        if not items:
            return None

        lines = [f"📋 {title}", ""]
        for item in items:
            lines.append(f"• {item}")

        return "\n".join(lines)

    def priority_label(self, priority):
        """Human-readable priority label."""
        labels = {
            10: "🚨 CRITICAL",
            9: "🔴 SECURITY",
            8: "🟠 IMPORTANT",
            7: "🟡 NOTABLE",
            6: "🔵 INFO",
            5: "⚪ ROUTINE",
        }
        for threshold, label in sorted(labels.items(), reverse=True):
            if priority >= threshold:
                return label
        return "⚫ DEBUG"
