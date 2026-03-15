"""
Kit Daemon — Preference Filter Module
Tracks explicit user signals (engages, ignores, repeats) to adapt response quality.
NOT a digital twin — records data, doesn't presume intent.

Design: worker blueprint at scratch/preference-filter-module-20260314.md
Safety: explicit signals only, no simulated judgment.
"""
import json
import logging
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger('kit-daemon.preference')


class SignalType:
    ENGAGE = "engage"       # User wants more
    IGNORE = "ignore"       # User moved on
    REPEAT = "repeat"       # User wasn't satisfied
    NEUTRAL = "neutral"     # No clear signal


# ─── SIGNAL DETECTION PATTERNS ────────────────────────────────

ENGAGE_PATTERNS = [
    # Positive feedback
    r'\b(?:thanks|thank you|perfect|exactly|awesome|great|love it|nice|beautiful)\b',
    r'\b(?:yes|yeah|yep|yup)\s+(?:please|do it|let\'s|go|that)',
    # Follow-up / wanting more
    r'\b(?:tell me more|show me|can you|what about|how about|and also)\b',
    r'\b(?:keep going|continue|what\'s next|go on)\b',
    # Acting on advice
    r'\b(?:i\'ll do|let me try|doing it now|on it|implementing)\b',
    # Reactions (Telegram)
    r'🔥|👍|❤️|💯|🎯',
]

IGNORE_PATTERNS = [
    # Topic switch without acknowledgment
    r'^(?:anyway|so|ok so|moving on|different topic|actually)\b',
    # Brevity requests
    r'\b(?:too (?:much|long|detailed)|just the (?:basics|summary)|shorter|brief)\b',
    # Redirection
    r'\b(?:not what i|that\'s not|i meant|no i want|forget that)\b',
]

REPEAT_PATTERNS = [
    # Rephrased question
    r'\b(?:what i meant|let me rephrase|in other words|to clarify)\b',
    # Dissatisfaction
    r'\b(?:still not|that\'s wrong|no that\'s|incorrect|not right)\b',
    # Asking again
    r'\b(?:i already asked|again|one more time|repeat)\b',
]


class PreferenceFilter:
    """Tracks explicit user signals and provides preference insights."""

    def __init__(self, config: Dict):
        self.config = config
        self.data_dir = os.path.join(
            config['paths']['daemon_home'], 'preferences'
        )
        os.makedirs(self.data_dir, exist_ok=True)

    def detect_signal(self, user_message: str, context: Dict = None) -> Dict:
        """Detect signal type from a user message.

        Args:
            user_message: The raw user message text
            context: Optional context (previous response type, delay, etc.)

        Returns:
            Dict with signal_type, confidence, indicators
        """
        msg_lower = user_message.lower().strip()
        indicators = []

        # Check engage patterns
        engage_score = 0
        for pattern in ENGAGE_PATTERNS:
            if re.search(pattern, msg_lower, re.IGNORECASE):
                engage_score += 1
                indicators.append(f"engage:{pattern[:30]}")

        # Check ignore patterns
        ignore_score = 0
        for pattern in IGNORE_PATTERNS:
            if re.search(pattern, msg_lower, re.IGNORECASE):
                ignore_score += 1
                indicators.append(f"ignore:{pattern[:30]}")

        # Check repeat patterns
        repeat_score = 0
        for pattern in REPEAT_PATTERNS:
            if re.search(pattern, msg_lower, re.IGNORECASE):
                repeat_score += 1
                indicators.append(f"repeat:{pattern[:30]}")

        # Context-based signals
        if context:
            delay_min = context.get('followup_delay_minutes', 0)
            # Long delay after info dump = likely ignored
            if delay_min > 30 and context.get('response_length', 0) > 500:
                ignore_score += 1
                indicators.append("context:long_delay_after_long_response")
            # Quick response = engaged
            if delay_min < 2 and delay_min > 0:
                engage_score += 0.5
                indicators.append("context:quick_followup")

        # Determine signal type (repeat > ignore > engage on ties)
        scores = [
            (SignalType.REPEAT, repeat_score),
            (SignalType.IGNORE, ignore_score),
            (SignalType.ENGAGE, engage_score),
        ]
        scores.sort(key=lambda x: x[1], reverse=True)
        max_signal = scores[0][0]
        max_score = scores[0][1]

        if max_score == 0:
            signal_type = SignalType.NEUTRAL
            confidence = 0.5
        else:
            signal_type = max_signal
            confidence = min(1.0, 0.5 + max_score * 0.2)

        return {
            'signal_type': signal_type,
            'confidence': confidence,
            'indicators': indicators,
            'scores': scores,
        }

    def record_signal(self, signal: Dict, response_meta: Dict = None):
        """Record a detected signal to disk."""
        entry = {
            'timestamp': datetime.now().isoformat(),
            'signal_type': signal['signal_type'],
            'confidence': signal['confidence'],
            'indicators': signal['indicators'],
        }
        if response_meta:
            entry.update({
                'response_type': response_meta.get('type', 'unknown'),
                'response_length': response_meta.get('length', 0),
                'topics': response_meta.get('topics', []),
            })

        # Append to monthly JSONL
        month_str = datetime.now().strftime('%Y-%m')
        filepath = os.path.join(self.data_dir, f'signals-{month_str}.jsonl')
        with open(filepath, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry) + '\n')

    def get_preferences(self, days: int = 7) -> Dict:
        """Analyze signal patterns over a period.

        Returns preference summary: what user engages with,
        what gets ignored, what causes repeats.
        """
        all_signals = self._read_recent_signals(days)

        if not all_signals:
            return {
                'total_signals': 0,
                'message': 'No signals recorded yet. Preferences will emerge over time.',
            }

        # Aggregate by type
        by_type = defaultdict(int)
        by_topic = defaultdict(lambda: defaultdict(int))
        by_response_type = defaultdict(lambda: defaultdict(int))

        for s in all_signals:
            sig_type = s.get('signal_type', 'neutral')
            by_type[sig_type] += 1

            # Track which topics get which signals
            for topic in s.get('topics', []):
                by_topic[topic][sig_type] += 1

            # Track which response types get which signals
            resp_type = s.get('response_type', 'unknown')
            by_response_type[resp_type][sig_type] += 1

        total = sum(by_type.values())
        engage_rate = by_type.get(SignalType.ENGAGE, 0) / total if total > 0 else 0
        ignore_rate = by_type.get(SignalType.IGNORE, 0) / total if total > 0 else 0
        repeat_rate = by_type.get(SignalType.REPEAT, 0) / total if total > 0 else 0

        # Find patterns
        hot_topics = []  # Topics with high engage rate
        cold_topics = []  # Topics with high ignore rate

        for topic, signals in by_topic.items():
            topic_total = sum(signals.values())
            if topic_total < 2:
                continue
            topic_engage = signals.get(SignalType.ENGAGE, 0) / topic_total
            topic_ignore = signals.get(SignalType.IGNORE, 0) / topic_total
            if topic_engage > 0.6:
                hot_topics.append((topic, topic_engage))
            if topic_ignore > 0.4:
                cold_topics.append((topic, topic_ignore))

        return {
            'total_signals': total,
            'period_days': days,
            'rates': {
                'engage': round(engage_rate, 3),
                'ignore': round(ignore_rate, 3),
                'repeat': round(repeat_rate, 3),
                'neutral': round(1 - engage_rate - ignore_rate - repeat_rate, 3),
            },
            'by_type': dict(by_type),
            'hot_topics': sorted(hot_topics, key=lambda x: x[1], reverse=True)[:5],
            'cold_topics': sorted(cold_topics, key=lambda x: x[1], reverse=True)[:5],
            'by_response_type': {k: dict(v) for k, v in by_response_type.items()},
        }

    def get_response_guidance(self, topic: str = '', response_type: str = '') -> Dict:
        """Get guidance for crafting a response based on learned preferences.

        Returns actionable hints: preferred length, detail level, etc.
        """
        prefs = self.get_preferences(days=14)

        guidance = {
            'detail_level': 'normal',
            'notes': [],
        }

        if prefs['total_signals'] < 10:
            guidance['notes'].append('Not enough data yet — using defaults.')
            return guidance

        # Check if this topic/type tends to get ignored
        for topic_name, ignore_rate in prefs.get('cold_topics', []):
            if topic and topic_name.lower() in topic.lower():
                guidance['detail_level'] = 'brief'
                guidance['notes'].append(
                    f"Topic '{topic_name}' has high ignore rate ({ignore_rate:.0%}). Keep it short."
                )

        # Check if this topic tends to get engagement
        for topic_name, engage_rate in prefs.get('hot_topics', []):
            if topic and topic_name.lower() in topic.lower():
                guidance['detail_level'] = 'detailed'
                guidance['notes'].append(
                    f"Topic '{topic_name}' has high engagement ({engage_rate:.0%}). User wants depth."
                )

        # Check repeat rate — if high, responses need to be clearer
        if prefs['rates']['repeat'] > 0.15:
            guidance['notes'].append(
                f"Repeat rate is {prefs['rates']['repeat']:.0%} — responses may need to be clearer."
            )

        return guidance

    def _read_recent_signals(self, days: int) -> List[Dict]:
        """Read signals from recent JSONL files."""
        signals = []
        cutoff = datetime.now() - timedelta(days=days)

        if not os.path.exists(self.data_dir):
            return signals

        for fname in os.listdir(self.data_dir):
            if fname.startswith('signals-') and fname.endswith('.jsonl'):
                fpath = os.path.join(self.data_dir, fname)
                with open(fpath, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.strip():
                            try:
                                entry = json.loads(line)
                                ts = datetime.fromisoformat(entry['timestamp'])
                                if ts >= cutoff:
                                    signals.append(entry)
                            except (json.JSONDecodeError, KeyError, ValueError):
                                pass

        return signals

    def get_status(self) -> Dict:
        """Get module status for dashboard."""
        signal_count = 0
        if os.path.exists(self.data_dir):
            for fname in os.listdir(self.data_dir):
                if fname.endswith('.jsonl'):
                    fpath = os.path.join(self.data_dir, fname)
                    with open(fpath, 'r') as f:
                        signal_count += sum(1 for line in f if line.strip())

        return {
            'total_signals': signal_count,
            'data_dir': self.data_dir,
            'status': 'active' if signal_count > 0 else 'collecting',
        }
