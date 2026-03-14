"""
Kit Daemon — Trace-Based Learning Engine
Inspired by Stanford's OpenJarvis (March 2026) trace→learn→eval loop.

Records every interaction trace (cron runs, tool calls, model responses),
analyzes patterns, and produces actionable optimizations:
- Model routing: which model performs best for which task class
- Prompt optimization: identifies prompt patterns that succeed/fail
- Skill tuning: adjusts skill parameters based on trace analysis
- Cost tracking: tokens, latency, energy per interaction

Unlike OpenJarvis (which targets LoRA fine-tuning), Kit's version
focuses on prompt/config evolution — because we can't fine-tune while
Ollama is serving inference on the same GPU.

Architecture:
  TraceStore (SQLite) ← TraceCollector ← cron runs, tool calls, sessions
  TraceStore → TraceAnalyzer → LearningEngine → recommendations
  recommendations → skill_evolution.py amendments (with human approval)
"""
import json
import logging
import os
import sqlite3
import time
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger('kit-daemon.trace_learning')


# ─── TYPES ─────────────────────────────────────────────────────

class StepType(str, Enum):
    GENERATE = "generate"      # LLM inference
    TOOL_CALL = "tool_call"    # Tool execution
    RETRIEVE = "retrieve"      # Memory/context retrieval
    RESPOND = "respond"        # Final response delivered
    ERROR = "error"            # Error occurred


class TraceOutcome(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


@dataclass
class TraceStep:
    step_type: StepType
    timestamp: float
    duration_seconds: float = 0.0
    input_data: Dict[str, Any] = field(default_factory=dict)
    output_data: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Trace:
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    query: str = ""              # What was asked / task description
    source: str = ""             # Where it came from (cron_id, user, daemon)
    task_class: str = ""         # Classified task type
    agent: str = ""              # Which agent/skill handled it
    model: str = ""              # Which model was used
    steps: List[TraceStep] = field(default_factory=list)
    result: str = ""             # Final output/result summary
    outcome: TraceOutcome = TraceOutcome.UNKNOWN
    feedback: Optional[float] = None  # 0.0-1.0 quality score
    started_at: float = 0.0
    ended_at: float = 0.0
    total_tokens: int = 0
    total_latency: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


# ─── TASK CLASSIFIER ──────────────────────────────────────────

TASK_CLASSES = {
    'briefing': ['brief', 'summary', 'digest', 'report', 'news'],
    'analysis': ['analyze', 'compare', 'evaluate', 'review', 'assess'],
    'coding': ['code', 'script', 'function', 'bug', 'fix', 'implement'],
    'search': ['search', 'find', 'look up', 'research', 'what is'],
    'monitoring': ['health', 'check', 'status', 'monitor', 'watch'],
    'memory': ['remember', 'memory', 'recall', 'log', 'note'],
    'trading': ['trade', 'signal', 'flow', 'options', 'ticker'],
    'task_work': ['task', 'queue', 'todo', 'complete', 'mark done'],
    'conversation': ['hey', 'hello', 'thanks', 'chat', 'opinion'],
}

def classify_task(query: str) -> str:
    """Classify a query into a task class based on keywords."""
    query_lower = query.lower()
    scores = {}
    for cls, keywords in TASK_CLASSES.items():
        score = sum(1 for kw in keywords if kw in query_lower)
        if score > 0:
            scores[cls] = score
    if scores:
        return max(scores, key=scores.get)
    return 'general'


# ─── TRACE STORE ──────────────────────────────────────────────

class TraceStore:
    """SQLite-backed trace storage with full-text search."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS traces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id TEXT NOT NULL UNIQUE,
                query TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                task_class TEXT NOT NULL DEFAULT '',
                agent TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                result TEXT NOT NULL DEFAULT '',
                outcome TEXT NOT NULL DEFAULT 'unknown',
                feedback REAL,
                started_at REAL NOT NULL DEFAULT 0.0,
                ended_at REAL NOT NULL DEFAULT 0.0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                total_latency REAL NOT NULL DEFAULT 0.0,
                metadata TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS trace_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id TEXT NOT NULL,
                step_index INTEGER NOT NULL,
                step_type TEXT NOT NULL,
                timestamp REAL NOT NULL DEFAULT 0.0,
                duration_seconds REAL NOT NULL DEFAULT 0.0,
                input_data TEXT NOT NULL DEFAULT '{}',
                output_data TEXT NOT NULL DEFAULT '{}',
                metadata TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY (trace_id) REFERENCES traces(trace_id)
            );

            CREATE INDEX IF NOT EXISTS idx_traces_source ON traces(source);
            CREATE INDEX IF NOT EXISTS idx_traces_task_class ON traces(task_class);
            CREATE INDEX IF NOT EXISTS idx_traces_model ON traces(model);
            CREATE INDEX IF NOT EXISTS idx_traces_outcome ON traces(outcome);
            CREATE INDEX IF NOT EXISTS idx_traces_started ON traces(started_at);
        """)
        self._conn.commit()

    def save(self, trace: Trace) -> None:
        """Persist a complete trace."""
        self._conn.execute(
            """INSERT INTO traces (trace_id, query, source, task_class, agent,
               model, result, outcome, feedback, started_at, ended_at,
               total_tokens, total_latency, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (trace.trace_id, trace.query, trace.source, trace.task_class,
             trace.agent, trace.model, trace.result, trace.outcome.value,
             trace.feedback, trace.started_at, trace.ended_at,
             trace.total_tokens, trace.total_latency,
             json.dumps(trace.metadata))
        )
        for idx, step in enumerate(trace.steps):
            self._conn.execute(
                """INSERT INTO trace_steps (trace_id, step_index, step_type,
                   timestamp, duration_seconds, input_data, output_data, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (trace.trace_id, idx, step.step_type.value,
                 step.timestamp, step.duration_seconds,
                 json.dumps(step.input_data), json.dumps(step.output_data),
                 json.dumps(step.metadata))
            )
        self._conn.commit()

    def list_traces(self, *, agent=None, model=None, outcome=None,
                    task_class=None, since=None, limit=100) -> List[Dict]:
        """Query traces with filters."""
        clauses, params = [], []
        if agent:
            clauses.append("agent = ?"); params.append(agent)
        if model:
            clauses.append("model = ?"); params.append(model)
        if outcome:
            clauses.append("outcome = ?"); params.append(outcome)
        if task_class:
            clauses.append("task_class = ?"); params.append(task_class)
        if since:
            clauses.append("started_at >= ?"); params.append(since)
        where = " AND ".join(clauses) if clauses else "1=1"
        sql = f"SELECT * FROM traces WHERE {where} ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM traces").fetchone()
        return row[0] if row else 0

    def get_model_stats(self) -> Dict[str, Dict]:
        """Get per-model performance stats."""
        rows = self._conn.execute("""
            SELECT model,
                   COUNT(*) as runs,
                   AVG(total_latency) as avg_latency,
                   AVG(total_tokens) as avg_tokens,
                   SUM(CASE WHEN outcome='success' THEN 1 ELSE 0 END) * 1.0 / COUNT(*) as success_rate,
                   AVG(feedback) as avg_feedback
            FROM traces WHERE model != '' GROUP BY model
        """).fetchall()
        return {
            r[0]: {
                'runs': r[1], 'avg_latency': round(r[2], 2),
                'avg_tokens': round(r[3], 0), 'success_rate': round(r[4], 3),
                'avg_feedback': round(r[5], 3) if r[5] else None
            } for r in rows
        }

    def get_task_class_stats(self) -> Dict[str, Dict]:
        """Get per-task-class performance stats."""
        rows = self._conn.execute("""
            SELECT task_class, model,
                   COUNT(*) as runs,
                   AVG(total_latency) as avg_latency,
                   SUM(CASE WHEN outcome='success' THEN 1 ELSE 0 END) * 1.0 / COUNT(*) as success_rate,
                   AVG(feedback) as avg_feedback
            FROM traces WHERE task_class != '' GROUP BY task_class, model
        """).fetchall()
        result = defaultdict(dict)
        for r in rows:
            result[r[0]][r[1]] = {
                'runs': r[2], 'avg_latency': round(r[3], 2),
                'success_rate': round(r[4], 3),
                'avg_feedback': round(r[5], 3) if r[5] else None
            }
        return dict(result)

    def get_routing_recommendations(self) -> Dict[str, str]:
        """Return best model per task class based on traces."""
        stats = self.get_task_class_stats()
        recommendations = {}
        for task_class, model_stats in stats.items():
            best_model = None
            best_score = -1
            for model, ms in model_stats.items():
                if ms['runs'] < 3:  # Need minimum samples
                    continue
                # Composite: 70% success rate + 30% feedback
                score = ms['success_rate'] * 0.7
                if ms['avg_feedback'] is not None:
                    score += ms['avg_feedback'] * 0.3
                if score > best_score:
                    best_score = score
                    best_model = model
            if best_model:
                recommendations[task_class] = best_model
        return recommendations

    def _row_to_dict(self, row) -> Dict:
        return {
            'trace_id': row[1], 'query': row[2], 'source': row[3],
            'task_class': row[4], 'agent': row[5], 'model': row[6],
            'result': row[7], 'outcome': row[8], 'feedback': row[9],
            'started_at': row[10], 'ended_at': row[11],
            'total_tokens': row[12], 'total_latency': row[13],
            'metadata': json.loads(row[14])
        }

    def close(self):
        self._conn.close()


# ─── TRACE COLLECTOR ──────────────────────────────────────────

class TraceCollector:
    """Records interaction traces from cron runs, daemon operations, etc."""

    def __init__(self, store: TraceStore):
        self.store = store

    def record_cron_run(self, cron_id: str, cron_name: str, model: str,
                        result_summary: str, outcome: str, duration: float,
                        tokens: int = 0) -> str:
        """Record a cron job execution as a trace."""
        trace = Trace(
            query=f"Cron: {cron_name}",
            source=f"cron:{cron_id}",
            task_class=classify_task(cron_name),
            agent=cron_name,
            model=model,
            result=result_summary[:500],
            outcome=TraceOutcome(outcome) if outcome in TraceOutcome.__members__.values() else TraceOutcome.UNKNOWN,
            started_at=time.time() - duration,
            ended_at=time.time(),
            total_tokens=tokens,
            total_latency=duration,
            steps=[
                TraceStep(
                    step_type=StepType.GENERATE,
                    timestamp=time.time() - duration,
                    duration_seconds=duration,
                    input_data={'cron_id': cron_id, 'model': model},
                    output_data={'result_length': len(result_summary)},
                )
            ]
        )
        self.store.save(trace)
        logger.debug(f"Trace recorded: {cron_name} ({outcome}, {duration:.1f}s)")
        return trace.trace_id

    def record_tool_call(self, tool_name: str, input_summary: str,
                         success: bool, duration: float, model: str = '') -> str:
        """Record a tool invocation as a trace."""
        trace = Trace(
            query=f"Tool: {tool_name}",
            source="daemon",
            task_class='tool_call',
            agent='daemon',
            model=model,
            result='success' if success else 'failure',
            outcome=TraceOutcome.SUCCESS if success else TraceOutcome.FAILURE,
            started_at=time.time() - duration,
            ended_at=time.time(),
            total_latency=duration,
            steps=[
                TraceStep(
                    step_type=StepType.TOOL_CALL,
                    timestamp=time.time() - duration,
                    duration_seconds=duration,
                    input_data={'tool': tool_name, 'input': input_summary[:200]},
                    output_data={'success': success},
                )
            ]
        )
        self.store.save(trace)
        return trace.trace_id

    def record_interaction(self, query: str, response: str, model: str,
                           duration: float, tokens: int = 0,
                           source: str = 'user', feedback: float = None) -> str:
        """Record a user interaction as a trace."""
        trace = Trace(
            query=query[:500],
            source=source,
            task_class=classify_task(query),
            agent='kit',
            model=model,
            result=response[:500],
            outcome=TraceOutcome.SUCCESS,
            feedback=feedback,
            started_at=time.time() - duration,
            ended_at=time.time(),
            total_tokens=tokens,
            total_latency=duration,
            steps=[
                TraceStep(
                    step_type=StepType.GENERATE,
                    timestamp=time.time() - duration,
                    duration_seconds=duration,
                    input_data={'query_length': len(query)},
                    output_data={'response_length': len(response), 'tokens': tokens},
                )
            ]
        )
        self.store.save(trace)
        return trace.trace_id


# ─── LEARNING ENGINE ──────────────────────────────────────────

class LearningEngine:
    """Analyzes traces and produces optimization recommendations.

    Inspired by OpenJarvis LearningOrchestrator but without LoRA training.
    Focuses on prompt/config evolution and model routing optimization.
    """

    def __init__(self, store: TraceStore, config: Dict):
        self.store = store
        self.config = config
        self.insights_dir = os.path.join(
            config['paths']['daemon_home'], 'insights'
        )
        os.makedirs(self.insights_dir, exist_ok=True)

    def run_learning_cycle(self) -> Dict[str, Any]:
        """Execute one learning cycle: analyze → recommend → log.

        Returns a summary of findings and recommendations.
        """
        result = {
            'timestamp': datetime.now().isoformat(),
            'trace_count': self.store.count(),
            'recommendations': [],
            'model_stats': {},
            'routing_updates': {},
        }

        if self.store.count() < 5:
            result['status'] = 'insufficient_data'
            result['reason'] = f'Need at least 5 traces, have {self.store.count()}'
            return result

        # 1. Model performance analysis
        model_stats = self.store.get_model_stats()
        result['model_stats'] = model_stats

        # 2. Task-class routing optimization
        routing = self.store.get_routing_recommendations()
        result['routing_updates'] = routing

        # 3. Generate recommendations
        recs = self._generate_recommendations(model_stats, routing)
        result['recommendations'] = recs

        # 4. Detect degradation patterns
        degradation = self._detect_degradation()
        if degradation:
            result['degradation_alerts'] = degradation

        # 5. Save insights
        result['status'] = 'completed'
        self._save_insights(result)

        logger.info(f"Learning cycle complete: {len(recs)} recommendations, "
                    f"{len(routing)} routing updates")
        return result

    def _generate_recommendations(self, model_stats, routing) -> List[Dict]:
        """Generate actionable recommendations from analysis."""
        recs = []

        # Recommend model switches for underperforming task classes
        for task_class, best_model in routing.items():
            task_stats = self.store.get_task_class_stats().get(task_class, {})
            for model, ms in task_stats.items():
                if model != best_model and ms['runs'] >= 5 and ms['success_rate'] < 0.7:
                    recs.append({
                        'type': 'model_switch',
                        'priority': 'high',
                        'task_class': task_class,
                        'current_model': model,
                        'recommended_model': best_model,
                        'reason': f"{model} has {ms['success_rate']:.0%} success on {task_class} tasks. "
                                 f"{best_model} performs better.",
                        'action': f"Route {task_class} tasks to {best_model}",
                    })

        # Detect high-latency patterns
        for model, ms in model_stats.items():
            if ms['avg_latency'] > 60:  # Over 60s average
                recs.append({
                    'type': 'latency_warning',
                    'priority': 'medium',
                    'model': model,
                    'avg_latency': ms['avg_latency'],
                    'reason': f"{model} averaging {ms['avg_latency']:.0f}s per call",
                    'action': 'Consider simpler prompts or model downgrade for this task type',
                })

        # Detect low feedback scores
        for model, ms in model_stats.items():
            if ms['avg_feedback'] is not None and ms['avg_feedback'] < 0.5:
                recs.append({
                    'type': 'quality_warning',
                    'priority': 'high',
                    'model': model,
                    'avg_feedback': ms['avg_feedback'],
                    'reason': f"{model} averaging {ms['avg_feedback']:.2f} feedback score",
                    'action': 'Review prompt patterns for this model',
                })

        return recs

    def _detect_degradation(self) -> List[Dict]:
        """Detect performance degradation over time."""
        alerts = []

        # Compare last 24h vs previous 24h
        now = time.time()
        day_ago = now - 86400
        two_days_ago = now - 172800

        recent = self.store.list_traces(since=day_ago, limit=1000)
        previous = self.store.list_traces(since=two_days_ago, limit=1000)
        previous = [t for t in previous if t['started_at'] < day_ago]

        if len(recent) >= 5 and len(previous) >= 5:
            recent_success = sum(1 for t in recent if t['outcome'] == 'success') / len(recent)
            prev_success = sum(1 for t in previous if t['outcome'] == 'success') / len(previous)

            if prev_success - recent_success > 0.15:  # 15% drop
                alerts.append({
                    'type': 'success_rate_drop',
                    'severity': 'high',
                    'recent_rate': round(recent_success, 3),
                    'previous_rate': round(prev_success, 3),
                    'drop': round(prev_success - recent_success, 3),
                })

        return alerts

    def _save_insights(self, result: Dict):
        """Save learning insights to file."""
        # Save latest
        latest_path = os.path.join(self.insights_dir, 'trace_learning_latest.json')
        with open(latest_path, 'w') as f:
            json.dump(result, f, indent=2, default=str)

        # Append to history
        date_str = datetime.now().strftime('%Y-%m-%d')
        history_path = os.path.join(self.insights_dir, f'learning_history_{date_str}.jsonl')
        with open(history_path, 'a') as f:
            f.write(json.dumps(result, default=str) + '\n')

    def get_status(self) -> Dict:
        """Return current learning engine status."""
        return {
            'trace_count': self.store.count(),
            'model_stats': self.store.get_model_stats(),
            'routing': self.store.get_routing_recommendations(),
            'task_classes': list(self.store.get_task_class_stats().keys()),
        }
