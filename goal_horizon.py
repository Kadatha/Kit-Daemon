"""
Kit Daemon — Goal Horizon Tracker
Reads GOALS.md and tracks progress against defined goals.

Provides:
- Goal loading and parsing at startup
- Progress tracking integrated with task queue completion
- Goal-aligned task prioritization
- Weekly progress summary generation
"""
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger('kit-daemon.goal_horizon')


# ─── DATA MODELS ─────────────────────────────────────────────

@dataclass
class Goal:
    name: str
    tier: int  # 1, 2, or 3
    status: str  # CRITICAL PATH, ACTIVE, BLOCKED, COMPLETE, VALIDATED, BASELINE
    priority: str  # URGENT, HIGH, MEDIUM, LOW
    timeline: str
    success_metrics: List[str] = field(default_factory=list)
    sub_goals: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)


# ─── GOAL PARSER ─────────────────────────────────────────────

class GoalParser:
    """Parses GOALS.md into structured Goal objects."""

    STATUS_PATTERNS = [
        (r'\*\*Status:\*\*\s*(.+?)(?:\s*$|\s{2})', 'status'),
        (r'\*\*Timeline:\*\*\s*(.+?)(?:\s*$|\s{2})', 'timeline'),
        (r'\*\*Priority:\*\*\s*(.+?)(?:\s*$|\s{2})', 'priority'),
    ]

    def parse(self, content):
        """Parse GOALS.md content into a list of Goal objects."""
        goals = []
        current_tier = 0

        # Split into sections by ### headers
        sections = re.split(r'\n### ', content)

        for section in sections[1:]:  # Skip preamble
            # Detect tier from preceding ## header context
            tier = self._detect_tier(section, content)

            # Extract goal name (first line of section)
            lines = section.strip().split('\n')
            name_line = lines[0].strip()
            # Clean name: remove numbering like "1. " and trailing markers
            name = re.sub(r'^\d+\.\s*', '', name_line)
            name = re.sub(r'\s*\(.*?\)\s*$', '', name).strip()

            if not name or name.startswith('Progress') or name.startswith('Review'):
                continue

            # Extract metadata
            section_text = '\n'.join(lines)
            status = self._extract_field(section_text, 'Status') or 'ACTIVE'
            timeline = self._extract_field(section_text, 'Timeline') or ''
            priority = self._extract_field(section_text, 'Priority') or 'MEDIUM'

            # Extract success metrics
            metrics = self._extract_list_section(section_text, 'Success Metrics')

            # Extract sub-goals
            sub_goals = self._extract_sub_goals(section_text)

            # Extract blockers
            blockers = self._extract_list_section(section_text, 'Current Blockers')

            goal = Goal(
                name=name,
                tier=tier,
                status=status.strip(),
                priority=priority.strip(),
                timeline=timeline.strip(),
                success_metrics=metrics,
                sub_goals=sub_goals,
                blockers=blockers,
            )
            goals.append(goal)

        return goals

    def _detect_tier(self, section, full_content):
        """Determine which tier a section belongs to based on context."""
        # Find where this section appears in the full content
        pos = full_content.find(section[:80])
        if pos < 0:
            return 2

        # Look backwards for ## Tier headers
        preceding = full_content[:pos]
        if '## Tier 3' in preceding and '## Tier 3' == preceding.rsplit('## Tier', 1)[-1][:8].strip()[:6]:
            return 3

        tier3_pos = preceding.rfind('## Tier 3')
        tier2_pos = preceding.rfind('## Tier 2')
        tier1_pos = preceding.rfind('## Tier 1')

        # Return the tier of the nearest preceding header
        positions = {1: tier1_pos, 2: tier2_pos, 3: tier3_pos}
        valid = {t: p for t, p in positions.items() if p >= 0}
        if valid:
            return max(valid, key=valid.get)
        return 1

    def _extract_field(self, text, field_name):
        """Extract a **FieldName:** value from markdown."""
        match = re.search(
            rf'\*\*{field_name}:\*\*\s*(.+?)(?:\s{{2,}}|\n)',
            text
        )
        return match.group(1).strip() if match else None

    def _extract_list_section(self, text, section_name):
        """Extract bullet list items under a section header."""
        pattern = rf'\*\*{section_name}:\*\*\s*\n((?:\s*-\s*.+\n?)+)'
        match = re.search(pattern, text)
        if not match:
            return []
        items = re.findall(r'-\s*(.+)', match.group(1))
        return [item.strip() for item in items]

    def _extract_sub_goals(self, text):
        """Extract sub-goal names from **Sub-goal name** patterns."""
        pattern = r'\*\*(.+?)\*\*\s*\('
        matches = re.findall(pattern, text)
        # Filter out field labels
        skip = {'Status', 'Timeline', 'Priority', 'Owner', 'Success Metrics',
                'Current Blockers', 'Next Milestones', 'Sub-goals'}
        return [m.strip() for m in matches if m.strip() not in skip]


# ─── GOAL HORIZON ────────────────────────────────────────────

class GoalHorizon:
    """Tracks goals from GOALS.md and provides progress/prioritization."""

    def __init__(self, config, state_manager):
        self.config = config
        self.state = state_manager
        self.goals_file = os.path.join(
            config['paths']['workspace'], 'GOALS.md'
        )
        self.parser = GoalParser()
        self.goals = []
        self.last_loaded = None

        # Load on init
        self._load_goals()

    def _load_goals(self):
        """Parse GOALS.md into structured goal list."""
        if not os.path.exists(self.goals_file):
            logger.warning(f"GOALS.md not found at {self.goals_file}")
            return

        try:
            with open(self.goals_file, 'r', encoding='utf-8') as f:
                content = f.read()

            self.goals = self.parser.parse(content)
            self.last_loaded = datetime.now().isoformat()
            self.state.set('goals_loaded', True)
            self.state.set('goals_count', len(self.goals))

            logger.info(
                f"Goal horizon loaded: {len(self.goals)} goals "
                f"(T1: {sum(1 for g in self.goals if g.tier == 1)}, "
                f"T2: {sum(1 for g in self.goals if g.tier == 2)}, "
                f"T3: {sum(1 for g in self.goals if g.tier == 3)})"
            )
        except Exception as e:
            logger.error(f"Failed to load goals: {e}")

    def get_active_goals(self, tier=None):
        """Return goals that are not COMPLETE, optionally filtered by tier."""
        active = [
            g for g in self.goals
            if g.status not in ('COMPLETE',)
        ]
        if tier is not None:
            active = [g for g in active if g.tier == tier]
        return active

    def get_blocked_goals(self):
        """Return goals with blockers."""
        return [g for g in self.goals if g.blockers]

    def get_critical_goals(self):
        """Return CRITICAL PATH or URGENT goals."""
        return [
            g for g in self.goals
            if 'CRITICAL' in g.status or g.priority == 'URGENT'
        ]

    def prioritize_task(self, task_description):
        """Score a task's alignment with active goals. Higher = more aligned."""
        if not task_description:
            return 0.0

        task_lower = task_description.lower()
        score = 0.0

        for goal in self.goals:
            if goal.status == 'COMPLETE':
                continue

            # Check name match
            name_words = goal.name.lower().split()
            name_hits = sum(1 for w in name_words if w in task_lower and len(w) > 3)

            # Check sub-goal match
            sub_hits = sum(
                1 for sg in goal.sub_goals
                if any(w in task_lower for w in sg.lower().split() if len(w) > 3)
            )

            if name_hits > 0 or sub_hits > 0:
                # Weight by tier (T1 goals matter most)
                tier_weight = {1: 3.0, 2: 2.0, 3: 1.0}.get(goal.tier, 1.0)
                # Weight by priority
                priority_weight = {
                    'URGENT': 2.0, 'HIGH': 1.5, 'MEDIUM': 1.0, 'LOW': 0.5
                }.get(goal.priority, 1.0)

                match_score = (name_hits + sub_hits) * tier_weight * priority_weight
                score = max(score, match_score)

        return round(score, 2)

    def check_task_completions(self):
        """Scan task queues for completed tasks that relate to goals."""
        completions = []
        task_queues = self.config.get('watch_paths', {}).get('task_queues', [])

        for tq_path in task_queues:
            if not os.path.exists(tq_path):
                continue
            try:
                with open(tq_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        # Check for completed tasks: - [x]
                        if re.match(r'\s*-\s*\[x\]', line, re.IGNORECASE):
                            task_text = re.sub(r'\s*-\s*\[x\]\s*', '', line).strip()
                            alignment = self.prioritize_task(task_text)
                            if alignment > 0:
                                completions.append({
                                    'task': task_text[:100],
                                    'alignment_score': alignment,
                                    'queue': os.path.basename(
                                        os.path.dirname(tq_path)
                                    ),
                                })
            except Exception as e:
                logger.debug(f"Error scanning {tq_path}: {e}")

        return completions

    def generate_progress_summary(self):
        """Generate a weekly progress summary for all goals."""
        if not self.goals:
            self._load_goals()

        completions = self.check_task_completions()
        today = datetime.now().strftime('%Y-%m-%d')

        summary = {
            'generated_at': today,
            'total_goals': len(self.goals),
            'by_tier': {
                'tier_1': [self._goal_to_dict(g) for g in self.goals if g.tier == 1],
                'tier_2': [self._goal_to_dict(g) for g in self.goals if g.tier == 2],
                'tier_3': [self._goal_to_dict(g) for g in self.goals if g.tier == 3],
            },
            'critical_goals': [g.name for g in self.get_critical_goals()],
            'blocked_goals': [
                {'name': g.name, 'blockers': g.blockers}
                for g in self.get_blocked_goals()
            ],
            'recent_completions': completions[:10],
            'active_count': len(self.get_active_goals()),
        }

        # Save summary to scratch
        scratch_dir = os.path.join(
            self.config['paths']['workspace'], 'scratch'
        )
        os.makedirs(scratch_dir, exist_ok=True)
        summary_file = os.path.join(scratch_dir, 'goal-progress.json')
        try:
            with open(summary_file, 'w', encoding='utf-8') as f:
                json.dump(summary, f, indent=2)
            logger.info(f"Goal progress summary saved to {summary_file}")
        except Exception as e:
            logger.error(f"Failed to save goal summary: {e}")

        # Update state
        self.state.set('goal_progress_summary', {
            'generated_at': today,
            'active': summary['active_count'],
            'critical': len(summary['critical_goals']),
            'blocked': len(summary['blocked_goals']),
            'completions': len(completions),
        })

        return summary

    def _goal_to_dict(self, goal):
        """Convert a Goal dataclass to a serializable dict."""
        return {
            'name': goal.name,
            'tier': goal.tier,
            'status': goal.status,
            'priority': goal.priority,
            'timeline': goal.timeline,
            'success_metrics': goal.success_metrics,
            'sub_goals': goal.sub_goals,
            'blockers': goal.blockers,
        }

    def get_summary(self):
        """Quick status for smoke tests."""
        return {
            'loaded': self.last_loaded is not None,
            'total_goals': len(self.goals),
            'active': len(self.get_active_goals()),
            'critical': len(self.get_critical_goals()),
            'blocked': len(self.get_blocked_goals()),
            'last_loaded': self.last_loaded,
        }
