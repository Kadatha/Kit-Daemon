"""
Kit Daemon — Decision Attribution Engine
Inspired by IBM's Trajectory-Informed Memory Generation (arXiv 2603.10600, March 2026).

Analyzes WHY traces succeeded or failed at the step level, not just
recording outcomes. Produces three types of actionable tips:
  - Strategy tips (from clean successes)
  - Recovery tips (from failure-then-success patterns)  
  - Optimization tips (from inefficient successes)

Lightweight implementation: uses pattern matching + heuristics for common
failure modes, reserving LLM analysis for ambiguous cases during the
6-hourly learning cycle.

IBM's approach uses full LLM analysis on every trace (expensive).
Kit's approach: fast pattern matching for 80% of cases, LLM for the 20%
that need deeper analysis.
"""
import json
import logging
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger('kit-daemon.attribution')


# ─── TIP TYPES ────────────────────────────────────────────────

class TipType(str, Enum):
    STRATEGY = "strategy"        # From clean successes
    RECOVERY = "recovery"        # From failure-then-success
    OPTIMIZATION = "optimization"  # From inefficient successes


class CauseLevel(str, Enum):
    IMMEDIATE = "immediate"    # The step that failed
    PROXIMATE = "proximate"    # The decision leading to failure
    ROOT = "root"              # The original wrong assumption


@dataclass
class AttributedCause:
    """A cause identified at a specific level."""
    level: CauseLevel
    description: str
    step_index: Optional[int] = None
    confidence: float = 0.0  # 0-1, how confident we are


@dataclass
class Tip:
    """An actionable learning extracted from trace analysis."""
    tip_type: TipType
    title: str
    description: str
    action: str                    # What to do
    context: str = ""              # When this applies
    source_trace_id: str = ""      # Provenance
    task_class: str = ""
    model: str = ""
    confidence: float = 0.0
    created_at: str = ""
    causes: List[AttributedCause] = field(default_factory=list)


# ─── FAILURE PATTERN MATCHERS ─────────────────────────────────

# Common failure patterns Kit can detect without LLM
FAILURE_PATTERNS = {
    # Timeout patterns
    'timeout': {
        'patterns': [
            r'timeout',
            r'timed?\s*out',
            r'exceeded.*(?:time|duration|limit)',
            r'SIGKILL',
            r'killed after',
        ],
        'cause_template': AttributedCause(
            level=CauseLevel.ROOT,
            description="Task exceeded time limit. Model too slow or prompt too complex for allocated time.",
        ),
        'tip_template': Tip(
            tip_type=TipType.OPTIMIZATION,
            title="Timeout Prevention",
            description="Task timed out before producing results.",
            action="Simplify prompt, reduce expected output length, or escalate to faster model.",
        ),
    },
    # Path/file errors
    'path_error': {
        'patterns': [
            r'file not found',
            r'no such file',
            r'path.*not.*exist',
            r'FileNotFoundError',
            r'cannot find.*path',
        ],
        'cause_template': AttributedCause(
            level=CauseLevel.IMMEDIATE,
            description="Referenced a file or directory that doesn't exist.",
        ),
        'tip_template': Tip(
            tip_type=TipType.RECOVERY,
            title="Path Validation",
            description="Task failed due to missing file/directory.",
            action="Verify paths exist before reading. Use absolute paths in isolated contexts.",
        ),
    },
    # Model capacity errors
    'model_capacity': {
        'patterns': [
            r'context.*(?:length|window|limit)',
            r'too many tokens',
            r'maximum.*(?:token|context)',
            r'truncat',
        ],
        'cause_template': AttributedCause(
            level=CauseLevel.ROOT,
            description="Input exceeded model's context window.",
        ),
        'tip_template': Tip(
            tip_type=TipType.OPTIMIZATION,
            title="Context Window Management",
            description="Task failed due to context length limits.",
            action="Reduce input size, summarize context, or escalate to model with larger window.",
        ),
    },
    # Empty/no output
    'empty_output': {
        'patterns': [
            r'empty.*(?:response|output|result)',
            r'no.*(?:output|response|result)',
            r'returned?\s*nothing',
            r'null.*response',
        ],
        'cause_template': AttributedCause(
            level=CauseLevel.PROXIMATE,
            description="Model produced empty or null response.",
        ),
        'tip_template': Tip(
            tip_type=TipType.RECOVERY,
            title="Empty Response Recovery",
            description="Model returned empty output.",
            action="Retry with simpler prompt. If persistent, escalate to larger model.",
        ),
    },
    # JSON/parsing errors
    'parse_error': {
        'patterns': [
            r'JSON.*(?:parse|decode|error)',
            r'invalid.*(?:json|syntax)',
            r'unexpected.*token',
            r'SyntaxError',
        ],
        'cause_template': AttributedCause(
            level=CauseLevel.IMMEDIATE,
            description="Model output failed to parse as expected format.",
        ),
        'tip_template': Tip(
            tip_type=TipType.RECOVERY,
            title="Output Format Recovery",
            description="Model output was malformed.",
            action="Add explicit format instructions. Use two-stage: generate then parse.",
        ),
    },
    # API/connection errors
    'connection_error': {
        'patterns': [
            r'connection.*(?:refused|reset|timeout|error)',
            r'ECONNREFUSED',
            r'HTTP.*(?:5\d\d|4\d\d)',
            r'network.*error',
            r'unreachable',
        ],
        'cause_template': AttributedCause(
            level=CauseLevel.IMMEDIATE,
            description="Network or API connection failed.",
        ),
        'tip_template': Tip(
            tip_type=TipType.RECOVERY,
            title="Connection Recovery",
            description="API or network connection failed.",
            action="Verify service is running. Retry with backoff. Check Ollama/OpenClaw status.",
        ),
    },
    # Planning without executing (9B specific)
    'planning_loop': {
        'patterns': [
            r'(?:let me|I will|I should|first.*then).*(?:plan|think|consider|analyze)',
            r'step\s*\d+.*step\s*\d+.*step\s*\d+',
            r'here.*(?:my|the).*plan',
        ],
        'cause_template': AttributedCause(
            level=CauseLevel.ROOT,
            description="Model spent tokens planning instead of executing. Common with 9B on complex prompts.",
        ),
        'tip_template': Tip(
            tip_type=TipType.OPTIMIZATION,
            title="Reduce Planning Overhead",
            description="Model planned extensively but didn't execute.",
            action="Simplify prompt to single action. Remove open-ended instructions. Use imperative commands.",
        ),
    },
}

# Success pattern indicators
SUCCESS_PATTERNS = {
    'clean_success': [
        r'completed?\s*successfully',
        r'task\s*(?:done|complete|finished)',
        r'all\s*(?:done|complete|passed)',
    ],
    'efficient_execution': [
        r'(?:quick|fast|efficient)',
        r'in\s*\d+\s*seconds?',
    ],
    'thorough_validation': [
        r'verif(?:y|ied|ying)',
        r'check(?:ed|ing)',
        r'validat(?:e|ed|ing)',
    ],
}


class DecisionAttributor:
    """Analyzes traces to attribute causes and generate tips."""

    def __init__(self, config: Dict):
        self.config = config
        self.tips_dir = os.path.join(
            config['paths']['daemon_home'], 'tips'
        )
        os.makedirs(self.tips_dir, exist_ok=True)

    def analyze_trace(self, trace: Dict) -> Dict[str, Any]:
        """Analyze a single trace and produce attribution + tips.

        Args:
            trace: Dict with keys: trace_id, query, outcome, result,
                   total_latency, model, task_class, steps, etc.

        Returns:
            Dict with: causes, tips, classification, confidence
        """
        result = {
            'trace_id': trace.get('trace_id', ''),
            'outcome': trace.get('outcome', 'unknown'),
            'causes': [],
            'tips': [],
            'classification': 'unknown',
            'confidence': 0.0,
        }

        outcome = trace.get('outcome', 'unknown')
        trace_result = trace.get('result', '')
        query = trace.get('query', '')
        latency = trace.get('total_latency', 0)
        model = trace.get('model', '')

        if outcome == 'success':
            result.update(self._analyze_success(trace))
        elif outcome in ('failure', 'timeout'):
            result.update(self._analyze_failure(trace))
        elif outcome == 'partial':
            result.update(self._analyze_partial(trace))

        # Add provenance to all tips
        for tip in result['tips']:
            tip.source_trace_id = trace.get('trace_id', '')
            tip.task_class = trace.get('task_class', '')
            tip.model = model
            tip.created_at = datetime.now().isoformat()

        return result

    def _analyze_failure(self, trace: Dict) -> Dict:
        """Analyze a failed trace for root causes."""
        result_text = trace.get('result', '')
        query = trace.get('query', '')
        combined = f"{query} {result_text}".lower()
        latency = trace.get('total_latency', 0)

        causes = []
        tips = []
        classification = 'unknown_failure'
        best_confidence = 0.0

        # Check against known failure patterns
        for pattern_name, pattern_info in FAILURE_PATTERNS.items():
            for regex in pattern_info['patterns']:
                if re.search(regex, combined, re.IGNORECASE):
                    cause = AttributedCause(
                        level=pattern_info['cause_template'].level,
                        description=pattern_info['cause_template'].description,
                        confidence=0.8,
                    )
                    causes.append(cause)

                    tip = Tip(
                        tip_type=pattern_info['tip_template'].tip_type,
                        title=pattern_info['tip_template'].title,
                        description=pattern_info['tip_template'].description,
                        action=pattern_info['tip_template'].action,
                        context=f"When running {trace.get('task_class', 'unknown')} tasks on {trace.get('model', 'unknown')}",
                        causes=[cause],
                        confidence=0.8,
                    )
                    tips.append(tip)

                    classification = pattern_name
                    best_confidence = max(best_confidence, 0.8)
                    break  # One match per pattern type

        # Check for timeout by latency
        if not causes and latency > 120:
            cause = AttributedCause(
                level=CauseLevel.ROOT,
                description=f"Task ran for {latency:.0f}s — likely timed out.",
                confidence=0.7,
            )
            causes.append(cause)
            tips.append(Tip(
                tip_type=TipType.OPTIMIZATION,
                title="Latency Timeout",
                description=f"Task took {latency:.0f}s, likely exceeding time limit.",
                action="Simplify task or escalate to faster model.",
                causes=[cause],
                confidence=0.7,
            ))
            classification = 'timeout_by_latency'
            best_confidence = 0.7

        # If no pattern matched, flag for LLM analysis
        if not causes:
            classification = 'unattributed_failure'
            best_confidence = 0.2
            causes.append(AttributedCause(
                level=CauseLevel.ROOT,
                description="Failure cause not identified by pattern matching. Needs LLM analysis.",
                confidence=0.2,
            ))

        return {
            'causes': causes,
            'tips': tips,
            'classification': classification,
            'confidence': best_confidence,
        }

    def _analyze_success(self, trace: Dict) -> Dict:
        """Analyze a successful trace for strategy/optimization tips."""
        result_text = trace.get('result', '')
        latency = trace.get('total_latency', 0)
        model = trace.get('model', '')
        task_class = trace.get('task_class', '')

        tips = []
        classification = 'clean_success'
        confidence = 0.6

        # Check if this was an efficient success
        if latency < 30:
            tips.append(Tip(
                tip_type=TipType.STRATEGY,
                title=f"Fast {task_class} Pattern",
                description=f"Completed {task_class} task in {latency:.0f}s on {model}.",
                action=f"Use {model} for {task_class} tasks — proven fast and successful.",
                confidence=0.7,
            ))
            classification = 'efficient_success'
            confidence = 0.7

        # Check if this was slow but successful (optimization opportunity)
        elif latency > 90:
            tips.append(Tip(
                tip_type=TipType.OPTIMIZATION,
                title=f"Slow {task_class} Success",
                description=f"Completed {task_class} but took {latency:.0f}s on {model}.",
                action=f"Consider simpler prompt or faster model for {task_class} tasks.",
                confidence=0.6,
            ))
            classification = 'inefficient_success'
            confidence = 0.6

        # Strategy: record what model works for what task class
        tips.append(Tip(
            tip_type=TipType.STRATEGY,
            title=f"Proven: {model} → {task_class}",
            description=f"{model} successfully handles {task_class} tasks.",
            action=f"Route {task_class} tasks to {model}.",
            confidence=0.5,
        ))

        return {
            'causes': [],
            'tips': tips,
            'classification': classification,
            'confidence': confidence,
        }

    def _analyze_partial(self, trace: Dict) -> Dict:
        """Analyze a partial success — recovery opportunity."""
        result_text = trace.get('result', '')

        tips = [Tip(
            tip_type=TipType.RECOVERY,
            title="Partial Completion Pattern",
            description="Task partially completed before stopping.",
            action="Break task into smaller subtasks. Check for mid-execution failures.",
            confidence=0.5,
        )]

        return {
            'causes': [AttributedCause(
                level=CauseLevel.PROXIMATE,
                description="Task started successfully but didn't complete all objectives.",
                confidence=0.5,
            )],
            'tips': tips,
            'classification': 'partial_success',
            'confidence': 0.5,
        }

    # ─── BATCH ANALYSIS ───────────────────────────────────────

    def analyze_traces(self, traces: List[Dict]) -> Dict[str, Any]:
        """Analyze a batch of traces and produce aggregate insights."""
        all_tips = []
        classifications = defaultdict(int)
        unattributed = []

        for trace in traces:
            result = self.analyze_trace(trace)
            all_tips.extend(result['tips'])
            classifications[result['classification']] += 1
            if result['classification'] == 'unattributed_failure':
                unattributed.append(trace)

        # Deduplicate and consolidate tips
        consolidated = self._consolidate_tips(all_tips)

        # Save tips
        self._save_tips(consolidated)

        summary = {
            'traces_analyzed': len(traces),
            'tips_generated': len(consolidated),
            'classifications': dict(classifications),
            'unattributed_count': len(unattributed),
            'tip_breakdown': {
                'strategy': sum(1 for t in consolidated if t.tip_type == TipType.STRATEGY),
                'recovery': sum(1 for t in consolidated if t.tip_type == TipType.RECOVERY),
                'optimization': sum(1 for t in consolidated if t.tip_type == TipType.OPTIMIZATION),
            },
            'timestamp': datetime.now().isoformat(),
        }

        logger.info(
            f"Attribution complete: {len(traces)} traces → "
            f"{len(consolidated)} tips "
            f"(S:{summary['tip_breakdown']['strategy']} "
            f"R:{summary['tip_breakdown']['recovery']} "
            f"O:{summary['tip_breakdown']['optimization']}), "
            f"{len(unattributed)} unattributed"
        )

        return summary

    def _consolidate_tips(self, tips: List[Tip]) -> List[Tip]:
        """Merge duplicate/similar tips."""
        # Group by (tip_type, title)
        groups = defaultdict(list)
        for tip in tips:
            key = (tip.tip_type, tip.title)
            groups[key].append(tip)

        consolidated = []
        for (tip_type, title), group in groups.items():
            # Keep the highest confidence version
            best = max(group, key=lambda t: t.confidence)
            best.confidence = min(1.0, best.confidence + 0.05 * (len(group) - 1))
            consolidated.append(best)

        # Curator pass — quality gate (inspired by greyhaven-ai/autocontext)
        curated = self._curate_tips(consolidated)

        return curated

    # ─── CURATOR (Quality Gate) ───────────────────────────────
    # Inspired by greyhaven-ai/autocontext's Curator agent.
    # Instead of an LLM call, uses rule-based gating:
    # - Reject low-confidence tips (<0.3)
    # - Reject contradictory tips (same task_class, opposite advice)
    # - Reject stale tips (same title seen 5+ times = noise, not signal)
    # - Boost tips confirmed by multiple traces

    def _curate_tips(self, tips: List[Tip]) -> List[Tip]:
        """Quality gate: reject noise, boost signal."""
        curated = []
        existing_titles = self._get_existing_tip_titles()

        for tip in tips:
            # Gate 1: Minimum confidence threshold
            if tip.confidence < 0.3:
                logger.debug(f"Curator rejected (low confidence): {tip.title}")
                continue

            # Gate 2: Staleness — if we've seen this exact title 5+ times,
            # it's noise (same failure repeating without learning)
            title_count = existing_titles.get(tip.title, 0)
            if title_count >= 5 and tip.confidence < 0.8:
                logger.debug(f"Curator rejected (stale, seen {title_count}x): {tip.title}")
                continue

            # Gate 3: Contradictory tips — if we have a strategy tip saying
            # "use model X" and a new tip saying "don't use model X" for
            # the same task class, keep the higher confidence one
            # (Simple version: flag but don't block for now)
            if title_count > 0:
                # Tip seen before — boost confidence (confirmation)
                tip.confidence = min(1.0, tip.confidence + 0.02 * title_count)

            curated.append(tip)

        rejected = len(tips) - len(curated)
        if rejected > 0:
            logger.info(f"Curator: {rejected} tips rejected, {len(curated)} passed")

        return curated

    def _get_existing_tip_titles(self) -> Dict[str, int]:
        """Count how many times each tip title has been seen."""
        counts = defaultdict(int)
        if not os.path.exists(self.tips_dir):
            return counts

        for fname in sorted(os.listdir(self.tips_dir), reverse=True)[:7]:
            if fname.endswith('.jsonl'):
                fpath = os.path.join(self.tips_dir, fname)
                try:
                    with open(fpath, 'r') as f:
                        for line in f:
                            if line.strip():
                                try:
                                    tip = json.loads(line)
                                    counts[tip.get('title', '')] += 1
                                except json.JSONDecodeError:
                                    pass
                except Exception:
                    pass

        return counts

    # ─── PLAYBOOK GENERATOR ───────────────────────────────────
    # Inspired by greyhaven-ai/autocontext's Coach → Playbook pattern.
    # Aggregates proven tips into coherent per-task-class playbooks.
    # A playbook is a markdown document: "Here's how [task_class] works best."

    def generate_playbooks(self) -> Dict[str, str]:
        """Generate playbooks from accumulated tips, grouped by task class."""
        all_tips = []

        if not os.path.exists(self.tips_dir):
            return {}

        # Read all tips
        for fname in sorted(os.listdir(self.tips_dir)):
            if fname.endswith('.jsonl'):
                fpath = os.path.join(self.tips_dir, fname)
                with open(fpath, 'r') as f:
                    for line in f:
                        if line.strip():
                            try:
                                all_tips.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass

        if not all_tips:
            return {}

        # Group by task_class
        by_class = defaultdict(list)
        for tip in all_tips:
            tc = tip.get('task_class', 'general')
            by_class[tc].append(tip)

        # Generate playbook per class
        playbooks = {}
        playbook_dir = os.path.join(
            self.config['paths']['daemon_home'], 'playbooks'
        )
        os.makedirs(playbook_dir, exist_ok=True)

        for task_class, tips in by_class.items():
            if len(tips) < 2:
                continue  # Need at least 2 tips to form a playbook

            # Sort by confidence
            tips.sort(key=lambda t: t.get('confidence', 0), reverse=True)

            # Build playbook markdown
            lines = [
                f"# Playbook: {task_class}",
                f"*Auto-generated from {len(tips)} tips | {datetime.now().strftime('%Y-%m-%d %H:%M')}*",
                "",
            ]

            # Strategies (what works)
            strategies = [t for t in tips if t.get('tip_type') == 'strategy']
            if strategies:
                lines.append("## What Works")
                for s in strategies[:5]:  # Top 5
                    lines.append(f"- **{s['title']}** (confidence: {s.get('confidence', 0):.0%})")
                    lines.append(f"  {s['action']}")
                lines.append("")

            # Recovery (when things break)
            recoveries = [t for t in tips if t.get('tip_type') == 'recovery']
            if recoveries:
                lines.append("## When Things Break")
                for r in recoveries[:5]:
                    lines.append(f"- **{r['title']}** (confidence: {r.get('confidence', 0):.0%})")
                    lines.append(f"  {r['action']}")
                lines.append("")

            # Optimization (how to do it better)
            optimizations = [t for t in tips if t.get('tip_type') == 'optimization']
            if optimizations:
                lines.append("## How To Do It Better")
                for o in optimizations[:5]:
                    lines.append(f"- **{o['title']}** (confidence: {o.get('confidence', 0):.0%})")
                    lines.append(f"  {o['action']}")
                lines.append("")

            # Model recommendations
            model_tips = [t for t in strategies if 'Proven:' in t.get('title', '')]
            if model_tips:
                lines.append("## Recommended Models")
                for m in model_tips[:3]:
                    lines.append(f"- {m['title']} — {m['description']}")
                lines.append("")

            playbook_text = '\n'.join(lines)
            playbooks[task_class] = playbook_text

            # Save to disk
            safe_name = task_class.replace(' ', '_').replace('/', '_')
            filepath = os.path.join(playbook_dir, f'{safe_name}.md')
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(playbook_text)

        logger.info(f"Generated {len(playbooks)} playbooks from {len(all_tips)} tips")
        return playbooks

    def get_playbook(self, task_class: str) -> Optional[str]:
        """Get the playbook for a specific task class."""
        playbook_dir = os.path.join(
            self.config['paths']['daemon_home'], 'playbooks'
        )
        safe_name = task_class.replace(' ', '_').replace('/', '_')
        filepath = os.path.join(playbook_dir, f'{safe_name}.md')

        if os.path.exists(filepath):
            with open(filepath, 'r', encoding='utf-8') as f:
                return f.read()
        return None

    def _save_tips(self, tips: List[Tip]):
        """Save tips to disk."""
        date_str = datetime.now().strftime('%Y-%m-%d')
        tips_file = os.path.join(self.tips_dir, f'tips_{date_str}.jsonl')

        with open(tips_file, 'a', encoding='utf-8') as f:
            for tip in tips:
                entry = {
                    'tip_type': tip.tip_type.value,
                    'title': tip.title,
                    'description': tip.description,
                    'action': tip.action,
                    'context': tip.context,
                    'source_trace_id': tip.source_trace_id,
                    'task_class': tip.task_class,
                    'model': tip.model,
                    'confidence': tip.confidence,
                    'created_at': tip.created_at,
                    'causes': [
                        {'level': c.level.value, 'description': c.description,
                         'confidence': c.confidence}
                        for c in tip.causes
                    ],
                }
                f.write(json.dumps(entry) + '\n')

    def get_tips_for_context(self, task_class: str = '', model: str = '') -> List[Dict]:
        """Retrieve relevant tips for a given context."""
        all_tips = []

        # Read recent tips files
        if not os.path.exists(self.tips_dir):
            return []

        for fname in sorted(os.listdir(self.tips_dir), reverse=True)[:7]:  # Last 7 days
            if fname.endswith('.jsonl'):
                fpath = os.path.join(self.tips_dir, fname)
                with open(fpath, 'r') as f:
                    for line in f:
                        if line.strip():
                            try:
                                tip = json.loads(line)
                                all_tips.append(tip)
                            except json.JSONDecodeError:
                                pass

        # Filter by context
        relevant = []
        for tip in all_tips:
            score = tip.get('confidence', 0)
            if task_class and tip.get('task_class') == task_class:
                score += 0.2
            if model and tip.get('model') == model:
                score += 0.1
            tip['relevance_score'] = score
            relevant.append(tip)

        # Sort by relevance and return top tips
        relevant.sort(key=lambda t: t['relevance_score'], reverse=True)
        return relevant[:10]

    def get_status(self) -> Dict:
        """Get attribution engine status."""
        tip_count = 0
        tip_types = defaultdict(int)

        if os.path.exists(self.tips_dir):
            for fname in os.listdir(self.tips_dir):
                if fname.endswith('.jsonl'):
                    fpath = os.path.join(self.tips_dir, fname)
                    with open(fpath, 'r') as f:
                        for line in f:
                            if line.strip():
                                try:
                                    tip = json.loads(line)
                                    tip_count += 1
                                    tip_types[tip.get('tip_type', 'unknown')] += 1
                                except json.JSONDecodeError:
                                    pass

        return {
            'total_tips': tip_count,
            'tip_breakdown': dict(tip_types),
            'patterns_registered': len(FAILURE_PATTERNS),
        }
