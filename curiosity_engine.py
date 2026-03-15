"""
Kit Daemon — Curiosity Engine
Detects knowledge gaps from response patterns and auto-queues research tasks.

Components:
- ResponseMonitor: analyzes responses for low-confidence patterns
- GapClassifier: categorizes gaps (Technical/Domain/Operational)
- Research queue: writes tasks to TASKQUEUE.md with dedup + daily cap

Non-blocking — monitors, doesn't slow down responses.
"""
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import List, Optional

logger = logging.getLogger('kit-daemon.curiosity')


# ─── DATA MODELS ─────────────────────────────────────────────

@dataclass
class KnowledgeGap:
    topic: str
    context: str
    confidence_score: float
    category: str  # 'technical', 'domain', 'operational'
    detected_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class CuriositySignal:
    confidence: float
    gaps: List[KnowledgeGap]
    should_research: bool
    hedging_count: int = 0
    deflection_count: int = 0


# ─── LOW-CONFIDENCE PATTERNS ────────────────────────────────

HEDGING_PATTERNS = [
    r"\bi think\b",
    r"\bpossibly\b",
    r"\bmight be\b",
    r"\bseems like\b",
    r"\bprobably\b",
    r"\bnot entirely sure\b",
    r"\bif i recall\b",
    r"\bi believe\b",
    r"\bperhaps\b",
    r"\bcould be\b",
]

DEFLECTION_PATTERNS = [
    r"\bi don'?t know\b",
    r"\bnot sure\b",
    r"\buncertain\b",
    r"\bbeyond my knowledge\b",
    r"\bi'?d need to research\b",
    r"\byou might want to search\b",
    r"\bi'?m not familiar\b",
    r"\bi can'?t confirm\b",
    r"\bi haven'?t encountered\b",
]

# Technical terms that suggest domain-specific gaps
TECHNICAL_INDICATORS = [
    r"\bapi\b", r"\bsdk\b", r"\bframework\b", r"\blibrary\b",
    r"\bprotocol\b", r"\barchitecture\b", r"\bcuda\b", r"\bgpu\b",
    r"\bmodel\b", r"\bpipeline\b", r"\bdeployment\b",
]

DOMAIN_INDICATORS = [
    r"\bsteel\b", r"\btrading\b", r"\bfinance\b", r"\binsurance\b",
    r"\bunderwriting\b", r"\bregulatory\b", r"\bcompliance\b",
    r"\bbiomarker\b", r"\bhealth\b", r"\bmedical\b",
]


# ─── RESPONSE MONITOR ───────────────────────────────────────

class ResponseMonitor:
    """Analyzes response text for low-confidence patterns."""

    def analyze_response(self, response_text, query_text=""):
        """Returns a CuriositySignal with confidence score and detected gaps."""
        if not response_text:
            return CuriositySignal(confidence=1.0, gaps=[], should_research=False)

        text_lower = response_text.lower()

        # Count hedging and deflection patterns
        hedging_count = sum(
            1 for p in HEDGING_PATTERNS if re.search(p, text_lower)
        )
        deflection_count = sum(
            1 for p in DEFLECTION_PATTERNS if re.search(p, text_lower)
        )

        # Short responses to complex questions suggest low confidence
        words = len(response_text.split())
        query_words = len(query_text.split()) if query_text else 0
        brevity_penalty = 0.0
        if query_words > 20 and words < 50:
            brevity_penalty = 0.2

        # Calculate confidence (1.0 = high confidence, 0.0 = no confidence)
        hedging_penalty = min(hedging_count * 0.1, 0.4)
        deflection_penalty = min(deflection_count * 0.2, 0.6)
        confidence = max(0.0, 1.0 - hedging_penalty - deflection_penalty - brevity_penalty)

        # Extract knowledge gaps
        gaps = self._extract_gaps(response_text, query_text, confidence)

        return CuriositySignal(
            confidence=round(confidence, 2),
            gaps=gaps,
            should_research=confidence < 0.3,
            hedging_count=hedging_count,
            deflection_count=deflection_count,
        )

    def _extract_gaps(self, response_text, query_text, confidence):
        """Identify specific knowledge gaps from the response."""
        gaps = []
        if confidence >= 0.7:
            return gaps

        combined = f"{query_text} {response_text}".lower()

        # Check for technical gaps
        tech_hits = [
            p.pattern.replace(r'\b', '').strip()
            for p in [re.compile(p) for p in TECHNICAL_INDICATORS]
            if re.search(p, combined)
        ]
        if tech_hits and confidence < 0.5:
            gaps.append(KnowledgeGap(
                topic=', '.join(tech_hits[:3]),
                context=query_text[:200] if query_text else response_text[:200],
                confidence_score=confidence,
                category='technical',
            ))

        # Check for domain gaps
        domain_hits = [
            p.pattern.replace(r'\b', '').strip()
            for p in [re.compile(p) for p in DOMAIN_INDICATORS]
            if re.search(p, combined)
        ]
        if domain_hits and confidence < 0.5:
            gaps.append(KnowledgeGap(
                topic=', '.join(domain_hits[:3]),
                context=query_text[:200] if query_text else response_text[:200],
                confidence_score=confidence,
                category='domain',
            ))

        # Generic operational gap if deflection detected but no specific category
        if not gaps and confidence < 0.3:
            topic = query_text[:80] if query_text else 'unknown topic'
            gaps.append(KnowledgeGap(
                topic=topic,
                context=response_text[:200],
                confidence_score=confidence,
                category='operational',
            ))

        return gaps


# ─── GAP CLASSIFIER ─────────────────────────────────────────

class GapClassifier:
    """Converts knowledge gaps into prioritized research task descriptions."""

    PRIORITY_MAP = {
        'technical': 'HIGH',
        'domain': 'MEDIUM',
        'operational': 'LOW',
    }

    def classify(self, gap):
        """Convert a KnowledgeGap into a task queue entry dict."""
        priority = self.PRIORITY_MAP.get(gap.category, 'LOW')
        today = datetime.now().strftime('%Y-%m-%d')

        # Build a slug for dedup
        slug = re.sub(r'[^a-z0-9]+', '-', gap.topic.lower())[:40].strip('-')

        return {
            'priority': priority,
            'topic': gap.topic,
            'category': gap.category,
            'slug': slug,
            'date': today,
            'description': (
                f"Research {gap.topic} — "
                f"detected low confidence ({gap.confidence_score:.0%}) "
                f"in {gap.category} knowledge. "
                f"Context: {gap.context[:100]}"
            ),
            'task_line': (
                f"- [ ] {priority} | Research: {gap.topic} — "
                f"Knowledge gap detected ({gap.category}, "
                f"confidence {gap.confidence_score:.0%}). "
                f"Save findings to docs/research-{slug}.md | {today}"
            ),
        }


# ─── CURIOSITY ENGINE ───────────────────────────────────────

class CuriosityEngine:
    """Main engine: monitors responses, classifies gaps, queues research tasks."""

    def __init__(self, config, state_manager):
        self.config = config
        self.state = state_manager
        self.monitor = ResponseMonitor()
        self.classifier = GapClassifier()

        # Task queue file (primary workspace)
        self.taskqueue_file = os.path.join(
            config['paths']['workspace'], 'TASKQUEUE.md'
        )

        # Daily cap tracking
        self.daily_cap = config.get('curiosity', {}).get('daily_cap', 3)
        self._tasks_today = 0
        self._today = date.today().isoformat()

        # In-memory dedup set (reset on restart is fine)
        self._queued_slugs = set()

        # Load existing slugs from taskqueue for dedup
        self._load_existing_slugs()

        logger.info(
            f"Curiosity engine initialized (daily cap: {self.daily_cap})"
        )

    def _load_existing_slugs(self):
        """Scan TASKQUEUE.md for existing research tasks to avoid duplicates."""
        if not os.path.exists(self.taskqueue_file):
            return
        try:
            with open(self.taskqueue_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if 'Research:' in line or 'research-' in line:
                        # Extract slug from "docs/research-{slug}.md"
                        match = re.search(r'research-([a-z0-9-]+)\.md', line)
                        if match:
                            self._queued_slugs.add(match.group(1))
        except Exception as e:
            logger.debug(f"Could not scan taskqueue for dedup: {e}")

    def _reset_daily_counter(self):
        """Reset daily task counter if it's a new day."""
        today = date.today().isoformat()
        if today != self._today:
            self._today = today
            self._tasks_today = 0

    def analyze(self, response_text, query_text=""):
        """Analyze a response for knowledge gaps. Non-blocking entry point."""
        try:
            signal = self.monitor.analyze_response(response_text, query_text)

            if not signal.should_research or not signal.gaps:
                return signal

            # Try to queue research tasks
            self._reset_daily_counter()
            for gap in signal.gaps:
                if self._tasks_today >= self.daily_cap:
                    logger.debug(
                        f"Daily curiosity cap reached ({self.daily_cap}), "
                        f"skipping gap: {gap.topic}"
                    )
                    break

                task = self.classifier.classify(gap)

                # Dedup check
                if task['slug'] in self._queued_slugs:
                    logger.debug(f"Duplicate research task skipped: {task['slug']}")
                    continue

                # Queue it
                if self._append_to_taskqueue(task['task_line']):
                    self._queued_slugs.add(task['slug'])
                    self._tasks_today += 1
                    logger.info(
                        f"Curiosity: queued research task — {task['topic']} "
                        f"({task['category']}, {task['priority']})"
                    )

            # Record in state
            self.state.set('curiosity_last_signal', {
                'confidence': signal.confidence,
                'gaps_detected': len(signal.gaps),
                'timestamp': datetime.now().isoformat(),
            })

            return signal

        except Exception as e:
            logger.error(f"Curiosity analysis error: {e}")
            return CuriositySignal(confidence=1.0, gaps=[], should_research=False)

    def _append_to_taskqueue(self, task_line):
        """Append a research task line to TASKQUEUE.md."""
        try:
            if not os.path.exists(self.taskqueue_file):
                logger.warning(f"TASKQUEUE.md not found: {self.taskqueue_file}")
                return False

            with open(self.taskqueue_file, 'r', encoding='utf-8') as f:
                content = f.read()

            # Append under existing tasks (before any --- or end of file)
            if content.rstrip().endswith('---'):
                content = content.rstrip()[:-3] + task_line + '\n\n---\n'
            else:
                content = content.rstrip() + '\n' + task_line + '\n'

            with open(self.taskqueue_file, 'w', encoding='utf-8') as f:
                f.write(content)

            return True

        except Exception as e:
            logger.error(f"Failed to append to taskqueue: {e}")
            return False

    def get_stats(self):
        """Get curiosity engine statistics."""
        return {
            'tasks_today': self._tasks_today,
            'daily_cap': self.daily_cap,
            'known_slugs': len(self._queued_slugs),
            'last_signal': self.state.get('curiosity_last_signal'),
        }
