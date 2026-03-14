"""
Kit Daemon — Agent Orchestration Layer
Kit is the conductor. Sub-agents are the instruments.

This module manages the lifecycle of delegated work:
- Decides WHAT to delegate vs handle directly
- Picks the RIGHT model for each task
- Monitors progress and intervenes when stuck
- Collects and synthesizes results
- Learns which delegation strategies work

Jarvis parallel: "I've dispatched the Mark VII for aerial recon
while the Mark XLII handles ground support."
"""
import json
import logging
import os
from datetime import datetime, timedelta
from enum import Enum

logger = logging.getLogger('kit-daemon.orchestrator')


class TaskComplexity(Enum):
    """Task complexity levels determine model assignment."""
    TRIVIAL = 1    # File reads, simple lookups — no model needed
    SIMPLE = 2     # Template fills, status checks — 9B local
    MODERATE = 3   # Analysis, summaries, code fixes — 14B local
    COMPLEX = 4    # Multi-step reasoning, strategy — Sonnet
    CRITICAL = 5   # Novel problems, career decisions — Opus


# Model assignment based on complexity
MODEL_MAP = {
    TaskComplexity.TRIVIAL: None,  # No model — pure Python
    TaskComplexity.SIMPLE: 'qwen3.5:9b',
    TaskComplexity.MODERATE: 'qwen2.5:14b',
    TaskComplexity.COMPLEX: 'anthropic/claude-sonnet-4-20250514',
    TaskComplexity.CRITICAL: 'anthropic/claude-opus-4-6',
}

# Cost per token (approximate, for tracking)
MODEL_COSTS = {
    'qwen3.5:9b': 0.0,
    'qwen2.5:14b': 0.0,
    'qwen3.5:35b-a3b': 0.0,
    'anthropic/claude-sonnet-4-20250514': 0.003,  # per 1K tokens
    'anthropic/claude-opus-4-6': 0.015,
}


class DelegatedTask:
    """A task that has been delegated to a sub-agent or model."""

    def __init__(self, task_id, description, complexity, model=None,
                 project=None, timeout_seconds=300):
        self.task_id = task_id
        self.description = description
        self.complexity = complexity
        self.model = model or MODEL_MAP.get(complexity, 'qwen3.5:9b')
        self.project = project
        self.timeout_seconds = timeout_seconds
        self.status = 'pending'  # pending, running, completed, failed, blocked
        self.created_at = datetime.now()
        self.started_at = None
        self.completed_at = None
        self.result = None
        self.error = None
        self.session_key = None
        self.retries = 0
        self.max_retries = 2

    def to_dict(self):
        return {
            'task_id': self.task_id,
            'description': self.description[:200],
            'complexity': self.complexity.name,
            'model': self.model,
            'project': self.project,
            'status': self.status,
            'created_at': self.created_at.isoformat(),
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'retries': self.retries,
            'session_key': self.session_key,
            'error': self.error,
        }


class ComplexityClassifier:
    """Classifies task complexity based on keywords and patterns.
    This is the brain that decides what goes where."""

    # Keywords that indicate complexity
    COMPLEXITY_SIGNALS = {
        TaskComplexity.TRIVIAL: [
            'read file', 'list files', 'check exists', 'get status',
            'disk space', 'file size', 'count lines',
        ],
        TaskComplexity.SIMPLE: [
            'format', 'template', 'rename', 'move file', 'create directory',
            'parse csv', 'extract', 'simple search', 'status check',
            'filter', 'sort', 'clean up',
        ],
        TaskComplexity.MODERATE: [
            'summarize', 'analyze', 'compare', 'review code', 'fix bug',
            'write function', 'refactor', 'benchmark', 'test',
            'research', 'compile report',
        ],
        TaskComplexity.COMPLEX: [
            'strategy', 'architecture', 'design', 'multi-step',
            'competitive analysis', 'build feature', 'integrate',
            'optimize', 'diagnose complex',
        ],
        TaskComplexity.CRITICAL: [
            'career', 'patent', 'legal', 'financial decision',
            'public release', 'security audit', 'novel approach',
            'presentation', 'stakeholder',
        ],
    }

    @classmethod
    def classify(cls, description, project=None):
        """Classify task complexity from description."""
        desc_lower = description.lower()

        # Score each complexity level
        scores = {}
        for complexity, keywords in cls.COMPLEXITY_SIGNALS.items():
            score = sum(1 for kw in keywords if kw in desc_lower)
            if score > 0:
                scores[complexity] = score

        if not scores:
            return TaskComplexity.MODERATE  # Default to moderate

        # Return highest-scoring complexity
        return max(scores.items(), key=lambda x: x[1])[0]

    @classmethod
    def should_delegate(cls, complexity):
        """Should this task be delegated to a sub-agent?"""
        # Trivial tasks: handle inline, no model needed
        # Simple tasks: handle inline with local model
        # Moderate+: delegate to sub-agent for isolation
        return complexity.value >= TaskComplexity.MODERATE.value


class Orchestrator:
    """The conductor. Manages task delegation, monitoring, and synthesis."""

    def __init__(self, config, state_manager, comms_manager, skill_engine=None,
                 ambient_engine=None):
        self.config = config
        self.state = state_manager
        self.comms = comms_manager
        self.skills = skill_engine
        self.ambient = ambient_engine
        self.classifier = ComplexityClassifier()

        self.active_tasks = {}  # task_id -> DelegatedTask
        self.task_history_dir = os.path.join(
            config['paths']['daemon_home'], 'task-history'
        )
        os.makedirs(self.task_history_dir, exist_ok=True)

    def plan_task(self, description, project=None, force_model=None):
        """Plan how to execute a task. Returns a DelegatedTask."""
        complexity = self.classifier.classify(description, project)
        model = force_model or MODEL_MAP.get(complexity, 'qwen3.5:9b')

        # Check if local model is feasible (GPU constraint)
        if model in ('qwen3.5:35b-a3b',) and self._gpu_busy():
            # Downgrade to 9B or upgrade to API
            model = 'qwen3.5:9b'
            logger.info(f"GPU busy, downgraded model for: {description[:60]}")

        task = DelegatedTask(
            task_id=f"task_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{id(description) % 10000:04d}",
            description=description,
            complexity=complexity,
            model=model,
            project=project,
            timeout_seconds=self._estimate_timeout(complexity),
        )

        logger.info(
            f"Task planned: [{complexity.name}] {description[:80]} → {model}"
        )

        return task

    def _estimate_timeout(self, complexity):
        """Estimate timeout based on complexity."""
        timeouts = {
            TaskComplexity.TRIVIAL: 30,
            TaskComplexity.SIMPLE: 60,
            TaskComplexity.MODERATE: 180,
            TaskComplexity.COMPLEX: 600,
            TaskComplexity.CRITICAL: 1200,
        }
        return timeouts.get(complexity, 180)

    def _gpu_busy(self):
        """Check if GPU is currently loaded with a model."""
        services = self.state.get('service_status', {})
        gpu_status = services.get('gpu', 'unknown')
        return gpu_status in ('busy', 'hot')

    def register_task(self, task):
        """Register a task as active."""
        self.active_tasks[task.task_id] = task
        task.status = 'pending'

        # Record in ambient learning
        if self.ambient:
            self.ambient.record_interaction('task_delegated', {
                'task_id': task.task_id,
                'complexity': task.complexity.name,
                'model': task.model,
                'project': task.project,
            })

        return task.task_id

    def mark_started(self, task_id, session_key=None):
        """Mark a task as started."""
        task = self.active_tasks.get(task_id)
        if task:
            task.status = 'running'
            task.started_at = datetime.now()
            task.session_key = session_key
            logger.info(f"Task started: {task_id}")

    def mark_completed(self, task_id, result=None):
        """Mark a task as completed."""
        task = self.active_tasks.get(task_id)
        if task:
            task.status = 'completed'
            task.completed_at = datetime.now()
            task.result = result
            self._archive_task(task)
            del self.active_tasks[task_id]

            if self.ambient:
                duration = (task.completed_at - task.started_at).total_seconds() if task.started_at else 0
                self.ambient.record_interaction('task_completed', {
                    'task_id': task_id,
                    'complexity': task.complexity.name,
                    'model': task.model,
                    'project': task.project,
                    'duration_seconds': duration,
                    'success': True,
                })

            logger.info(f"Task completed: {task_id}")

    def mark_failed(self, task_id, error=None):
        """Mark a task as failed. May retry or escalate."""
        task = self.active_tasks.get(task_id)
        if not task:
            return

        task.retries += 1
        task.error = error

        if task.retries <= task.max_retries:
            # Retry with same or escalated model
            task.status = 'pending'
            if task.retries == task.max_retries:
                # Last retry: escalate model
                task.model = self._escalate_model(task.model)
                logger.warning(f"Task {task_id} retry {task.retries} — escalated to {task.model}")
            else:
                logger.warning(f"Task {task_id} retry {task.retries}")
        else:
            # Max retries exceeded — mark blocked
            task.status = 'blocked'
            task.completed_at = datetime.now()
            self._archive_task(task)
            del self.active_tasks[task_id]

            self.comms.send_telegram(
                f"⚠️ Task blocked after {task.retries} attempts: {task.description[:100]}",
                priority=7
            )

            if self.ambient:
                self.ambient.record_interaction('task_failed', {
                    'task_id': task_id,
                    'complexity': task.complexity.name,
                    'model': task.model,
                    'error': error,
                    'success': False,
                })

            logger.error(f"Task blocked: {task_id} — {error}")

    def _escalate_model(self, current_model):
        """Escalate to a more capable model."""
        escalation = {
            'qwen3.5:9b': 'qwen2.5:14b',
            'qwen2.5:14b': 'anthropic/claude-sonnet-4-20250514',
            'anthropic/claude-sonnet-4-20250514': 'anthropic/claude-opus-4-6',
        }
        return escalation.get(current_model, current_model)

    def _archive_task(self, task):
        """Save completed/blocked task to history."""
        date_str = datetime.now().strftime('%Y-%m-%d')
        history_file = os.path.join(self.task_history_dir, f'{date_str}.jsonl')
        with open(history_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(task.to_dict()) + '\n')

    def get_status(self):
        """Get orchestrator status for dashboard."""
        return {
            'active_tasks': len(self.active_tasks),
            'tasks': [t.to_dict() for t in self.active_tasks.values()],
            'model_map': {k.name: v for k, v in MODEL_MAP.items()},
        }

    def check_stale_tasks(self):
        """Check for tasks that have been running too long."""
        stale = []
        for task_id, task in list(self.active_tasks.items()):
            if task.status == 'running' and task.started_at:
                elapsed = (datetime.now() - task.started_at).total_seconds()
                if elapsed > task.timeout_seconds:
                    logger.warning(f"Stale task detected: {task_id} ({elapsed:.0f}s)")
                    self.mark_failed(task_id, error=f'Timeout after {elapsed:.0f}s')
                    stale.append(task_id)
        return stale

    def get_daily_summary(self):
        """Get today's task execution summary."""
        date_str = datetime.now().strftime('%Y-%m-%d')
        history_file = os.path.join(self.task_history_dir, f'{date_str}.jsonl')

        tasks = []
        if os.path.exists(history_file):
            with open(history_file, 'r') as f:
                for line in f:
                    if line.strip():
                        try:
                            tasks.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass

        completed = [t for t in tasks if t.get('status') == 'completed']
        blocked = [t for t in tasks if t.get('status') == 'blocked']

        # Cost estimate
        total_cost = 0.0
        for t in tasks:
            model = t.get('model', '')
            cost_per_k = MODEL_COSTS.get(model, 0)
            # Rough estimate: 2K tokens per task
            total_cost += cost_per_k * 2

        return {
            'date': date_str,
            'total_tasks': len(tasks),
            'completed': len(completed),
            'blocked': len(blocked),
            'active': len(self.active_tasks),
            'estimated_cost': round(total_cost, 4),
            'by_complexity': self._count_by_field(tasks, 'complexity'),
            'by_model': self._count_by_field(tasks, 'model'),
        }

    def _count_by_field(self, tasks, field):
        """Count tasks grouped by a field."""
        counts = {}
        for t in tasks:
            val = t.get(field, 'unknown')
            counts[val] = counts.get(val, 0) + 1
        return counts
