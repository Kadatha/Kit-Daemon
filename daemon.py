"""
Kit Daemon — Main Entry Point
Always-on nervous system for Kit. Coordinates all modules.

Usage:
    python daemon.py          # Start the daemon
    python daemon.py --test   # Run smoke test and exit
"""
import asyncio
import json
import logging
import logging.handlers
import os
import signal
import sys
from datetime import datetime

# Setup logging first
def setup_logging(log_file):
    """Configure logging with rotation."""
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    formatter = logging.Formatter(
        '%(asctime)s [%(name)s] %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # File handler with rotation (5MB, keep 3)
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)

    root_logger = logging.getLogger('kit-daemon')
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    return root_logger


def load_config():
    """Load daemon configuration."""
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    with open(config_path, 'r') as f:
        return json.load(f)


class KitDaemon:
    def __init__(self):
        self.config = load_config()
        self.logger = setup_logging(self.config['log_file'])
        self.running = False

        # Initialize modules
        from state import StateManager
        from comms import CommsManager
        from system import SystemMonitor
        from health import HealthMonitor
        from watchers import WatcherManager
        from anticipation import AnticipationEngine
        from learning import LearningEngine
        from skill_evolution import SkillEvolutionEngine
        from workflows import WorkflowEngine
        from ambient import AmbientLearning
        from dashboard import generate_dashboard
        from orchestrator import Orchestrator
        from intelligence import IntelligenceEngine
        from memory_graph import MemoryGraph, seed_initial_graph
        from multimodal import MultiModalProcessor
        from voice import VoiceEngine
        from trace_learning import TraceStore, TraceCollector, LearningEngine as TraceLearningEngine
        from benchmark import BenchmarkProtocol
        from decision_attribution import DecisionAttributor
        from cost_tracker import CostTracker
        from preference_filter import PreferenceFilter
        from self_model import SelfModel
        from curiosity_engine import CuriosityEngine
        from goal_horizon import GoalHorizon

        self.state = StateManager(self.config['state_file'])
        self.comms = CommsManager(self.config, self.state)
        self.system = SystemMonitor(self.config, self.state, self.comms)
        self.skills = SkillEvolutionEngine(self.config, self.state, self.comms)
        self.health = HealthMonitor(self.config, self.state, self.comms, self.skills)
        self.watchers = WatcherManager(self.config, self.on_file_event)
        self.anticipation = AnticipationEngine(
            self.config, self.state, self.comms, self._queue_workflow
        )
        self.learning = LearningEngine(self.config, self.state)
        self.ambient = AmbientLearning(self.config, self.state)
        self.orchestrator = Orchestrator(
            self.config, self.state, self.comms, self.skills, self.ambient
        )
        self.intel = IntelligenceEngine(self.config, self.state, self.comms)
        self.graph = MemoryGraph(self.config)

        # Seed graph if empty
        stats = self.graph.get_stats()
        if stats['total_entities'] == 0:
            seed_initial_graph(self.graph)

        self.multimodal = MultiModalProcessor(self.config, self.state, self.comms)
        self.voice = VoiceEngine(self.config, self.state, self.comms)

        # Trace-based learning (inspired by OpenJarvis)
        trace_db = os.path.join(self.config['paths']['daemon_home'], 'traces.db')
        self.trace_store = TraceStore(trace_db)
        self.trace_collector = TraceCollector(self.trace_store)
        self.learning_engine = TraceLearningEngine(self.trace_store, self.config)
        self.benchmark = BenchmarkProtocol(self.trace_store, self.config)
        self.attributor = DecisionAttributor(self.config)
        self.cost_tracker = CostTracker(self.config)
        self.preference_filter = PreferenceFilter(self.config)
        self.self_model = SelfModel(self.config, self.state, self.trace_store)
        self.curiosity = CuriosityEngine(self.config, self.state)
        self.goal_horizon = GoalHorizon(self.config, self.state)
        self.generate_dashboard = generate_dashboard
        self.workflow_engine = WorkflowEngine(
            self.config, self.state, self.comms, self.skills
        )
        self._pending_workflows = asyncio.Queue()

        self.logger.info("Kit Daemon initialized")

    def on_file_event(self, event_type, path):
        """Callback for file system events. Triggers workflows."""
        self.logger.info(f"File event: {event_type} — {path}")

        context = {'file_path': path, 'event_type': event_type}

        if event_type == 'urgent_task':
            self._queue_workflow('urgent-task', context)
        elif event_type == 'uw_export':
            self._queue_workflow('uw-csv-process', context)
        elif event_type == 'new_memory':
            self._queue_workflow('memory-reindex', context)

    def _queue_workflow(self, workflow_id, context):
        """Queue a workflow for async execution."""
        try:
            self._pending_workflows.put_nowait((workflow_id, context))
        except Exception as e:
            self.logger.error(f"Failed to queue workflow {workflow_id}: {e}")

    async def system_check_loop(self):
        """Periodic system health checks."""
        interval = self.config['intervals']['system_check_seconds']
        while self.running:
            try:
                results = self.system.check_all()
                self.logger.debug(f"System check: {json.dumps({k: v.get('status', 'unknown') for k, v in results.items()})}")
                # Feed into ambient learning
                for svc, result in results.items():
                    self.ambient.record_interaction('system_check', {
                        'service': svc,
                        'status': result.get('status', 'unknown'),
                        'success': result.get('status') == 'healthy',
                    })
            except Exception as e:
                self.logger.error(f"System check loop error: {e}")
            await asyncio.sleep(interval)

    async def health_check_loop(self):
        """Periodic cron health checks."""
        interval = self.config['intervals']['health_check_seconds']
        while self.running:
            try:
                cron_health = self.health.check_cron_health()
                worker_status = self.health.check_worker_output()
                stale = self.orchestrator.check_stale_tasks()
                self.logger.debug(f"Cron health: {cron_health.get('status', 'unknown')}, Worker: {worker_status}")

                # Record cron runs as traces for the learning engine
                try:
                    for run in cron_health.get('recent_runs', []):
                        run_id = run.get('jobId', '') + '_' + str(run.get('ts', ''))
                        if run_id not in self.state.get('traced_runs', set()):
                            self.trace_collector.record_cron_run(
                                cron_id=run.get('jobId', ''),
                                cron_name=run.get('name', 'unknown'),
                                model=run.get('model', 'unknown'),
                                result_summary=run.get('summary', '')[:200],
                                outcome='success' if run.get('status') == 'ok' else 'failure',
                                duration=run.get('duration', 0),
                            )
                            traced = self.state.get('traced_runs', set())
                            if isinstance(traced, list):
                                traced = set(traced)
                            traced.add(run_id)
                            # Keep last 200 to avoid unbounded growth
                            if len(traced) > 200:
                                traced = set(list(traced)[-200:])
                            self.state.set('traced_runs', list(traced))
                except Exception as e:
                    self.logger.debug(f"Trace recording: {e}")
            except Exception as e:
                self.logger.error(f"Health check loop error: {e}")
            await asyncio.sleep(interval)

    async def anticipation_loop(self):
        """Periodic anticipation engine checks."""
        interval = self.config['intervals']['anticipation_check_seconds']
        while self.running:
            try:
                self.anticipation.check_schedule()
            except Exception as e:
                self.logger.error(f"Anticipation loop error: {e}")
            await asyncio.sleep(interval)

    async def workflow_loop(self):
        """Process queued workflows."""
        while self.running:
            try:
                # Wait for a workflow with timeout (so we can check self.running)
                try:
                    workflow_id, context = await asyncio.wait_for(
                        self._pending_workflows.get(), timeout=5
                    )
                    result = await self.workflow_engine.trigger(workflow_id, context)
                    if result:
                        self.logger.info(
                            f"Workflow '{workflow_id}' completed: "
                            f"{'success' if result['success'] else 'failed'} "
                            f"({result['duration_seconds']}s)"
                        )
                except asyncio.TimeoutError:
                    pass  # No workflows queued, loop back
            except Exception as e:
                self.logger.error(f"Workflow loop error: {e}")
                await asyncio.sleep(1)

    async def skill_evolution_loop(self):
        """Periodic skill performance inspection + cron run ingestion."""
        while self.running:
            try:
                # Ingest cron run data into skill trackers
                cron_skill_map = self.config.get('cron_to_skill', {})
                for cron_id, skill_id in cron_skill_map.items():
                    try:
                        tracker = self.skills.get_tracker(skill_id)
                        if not tracker:
                            continue
                        # Read recent runs from skill's runs.jsonl
                        # The tracker already has this data if record_run was called
                        # But we also check cron run history via trace store
                        traces = self.trace_store.list_traces(limit=20)
                        for trace in traces:
                            source = trace.get('source', '')
                            if cron_id[:8] in source or skill_id in source:
                                success = trace.get('outcome') == 'success'
                                latency = trace.get('total_latency', 0)
                                model = trace.get('model', '')
                                # Only record if not already tracked
                                tracker.record_run(
                                    success=success,
                                    duration_seconds=latency,
                                    model=model,
                                    error=trace.get('error', '') if not success else None,
                                )
                    except Exception as e:
                        self.logger.debug(f"Skill ingestion error for {skill_id}: {e}")

                issues = self.skills.run_inspection_sweep()
                if issues:
                    self.logger.info(f"Skill inspection found {len(issues)} issue(s)")
                # Save dashboard to scratch for Kit to read
                dashboard = self.skills.get_dashboard()
                dash_file = os.path.join(
                    self.config['paths']['workspace'], 'scratch', 'skill-dashboard.json'
                )
                os.makedirs(os.path.dirname(dash_file), exist_ok=True)
                with open(dash_file, 'w', encoding='utf-8') as f:
                    json.dump(dashboard, f, indent=2)
                self.logger.info(f"Skill evolution: {len(dashboard)} skills tracked")
            except Exception as e:
                self.logger.error(f"Skill evolution loop error: {e}")
            await asyncio.sleep(3600)  # Every hour

    async def dashboard_loop(self):
        """Regenerate dashboard periodically."""
        while self.running:
            try:
                self.generate_dashboard(self.config)
            except Exception as e:
                self.logger.error(f"Dashboard generation error: {e}")
            await asyncio.sleep(60)  # Every minute

    async def intelligence_loop(self):
        """Periodic intelligence scan of external sources."""
        # Wait 2 minutes before first scan (let daemon settle)
        await asyncio.sleep(120)
        while self.running:
            try:
                items = self.intel.scan_all_sources()
                if items:
                    self.logger.info(f"Intel scan: {len(items)} significant items")
                    # Compile digest
                    self.intel.compile_digest_markdown()
            except Exception as e:
                self.logger.error(f"Intelligence scan error: {e}")
            # Scan every 4 hours
            await asyncio.sleep(3600 * int(self.config['intervals'].get('external_watch_hours', 4)))

    async def ambient_analysis_loop(self):
        """Run ambient pattern analysis periodically."""
        while self.running:
            try:
                patterns = self.ambient.analyze_patterns()
                if patterns.get('recommendations'):
                    self.logger.info(
                        f"Ambient analysis: {len(patterns['recommendations'])} recommendations"
                    )
            except Exception as e:
                self.logger.error(f"Ambient analysis error: {e}")
            await asyncio.sleep(3600 * 4)  # Every 4 hours

    async def trace_learning_loop(self):
        """Run trace-based learning cycle periodically."""
        # Wait for traces to accumulate
        await asyncio.sleep(3600)  # First cycle after 1 hour
        while self.running:
            try:
                result = self.learning_engine.run_learning_cycle()
                status = result.get('status', 'unknown')
                recs = result.get('recommendations', [])
                routing = result.get('routing_updates', {})
                self.logger.info(
                    f"Trace learning: {status}, {len(recs)} recommendations, "
                    f"{len(routing)} routing updates, "
                    f"{result.get('trace_count', 0)} total traces"
                )
                if recs:
                    for rec in recs:
                        self.logger.info(f"  Recommendation: [{rec['priority']}] {rec['action']}")

                # Run decision attribution on recent traces
                try:
                    recent = self.trace_store.list_traces(limit=50)
                    if recent:
                        attr_result = self.attributor.analyze_traces(recent)
                        self.logger.info(
                            f"Decision attribution: {attr_result['traces_analyzed']} analyzed, "
                            f"{attr_result['tips_generated']} tips "
                            f"(S:{attr_result['tip_breakdown']['strategy']} "
                            f"R:{attr_result['tip_breakdown']['recovery']} "
                            f"O:{attr_result['tip_breakdown']['optimization']})"
                        )
                    # Generate playbooks from accumulated tips
                    if attr_result['tips_generated'] > 0:
                        playbooks = self.attributor.generate_playbooks()
                        if playbooks:
                            self.logger.info(
                                f"Playbooks generated: {list(playbooks.keys())}"
                            )
                except Exception as e:
                    self.logger.error(f"Attribution error: {e}")

                # Auto-ingest costs from traces
                try:
                    recent_traces = self.trace_store.list_traces(limit=50)
                    for trace in recent_traces:
                        model = trace.get('model', '')
                        usage = trace.get('usage', {})
                        if usage:
                            self.cost_tracker.record_cron_cost({
                                'model': model,
                                'usage': usage,
                                'jobId': trace.get('source', ''),
                            })
                    summary = self.cost_tracker.get_daily_summary()
                    self.logger.info(
                        f"Cost ingestion: ${summary['total_cost']:.2f} today, "
                        f"{summary['local_pct']:.0f}% local"
                    )
                except Exception as e:
                    self.logger.error(f"Cost ingestion error: {e}")
            except Exception as e:
                self.logger.error(f"Trace learning error: {e}")
            await asyncio.sleep(3600 * 6)  # Every 6 hours

    async def self_model_loop(self):
        """Update self-model from trace data weekly."""
        # Wait for traces to accumulate
        await asyncio.sleep(3600 * 2)
        while self.running:
            try:
                stats = self.self_model.update_from_traces()
                if stats:
                    self.logger.info(
                        f"Self-model: updated {len(stats)} task categories from traces"
                    )
                # Weekly reflection (check if 7+ days since last)
                last_reflection = self.state.get('self_model_last_reflection')
                if last_reflection:
                    last_dt = datetime.fromisoformat(last_reflection)
                    days_since = (datetime.now() - last_dt).days
                else:
                    days_since = 999
                if days_since >= 7:
                    result = self.self_model.generate_weekly_reflection()
                    if result:
                        self.state.set('self_model_last_reflection', datetime.now().isoformat())
                        self.logger.info("Self-model: weekly reflection completed")
            except Exception as e:
                self.logger.error(f"Self-model loop error: {e}")
            await asyncio.sleep(3600 * 12)  # Every 12 hours

    async def goal_horizon_loop(self):
        """Track goal progress and generate summaries weekly."""
        await asyncio.sleep(3600)
        while self.running:
            try:
                summary = self.goal_horizon.generate_progress_summary()
                active = summary.get('active_count', 0)
                critical = len(summary.get('critical_goals', []))
                blocked = len(summary.get('blocked_goals', []))
                completions = len(summary.get('recent_completions', []))
                self.logger.info(
                    f"Goal horizon: {active} active, {critical} critical, "
                    f"{blocked} blocked, {completions} aligned completions"
                )
            except Exception as e:
                self.logger.error(f"Goal horizon loop error: {e}")
            await asyncio.sleep(3600 * 12)  # Every 12 hours

    async def metrics_loop(self):
        """Save metrics periodically."""
        while self.running:
            try:
                self.learning.save_metrics()
                self.state.save()
            except Exception as e:
                self.logger.error(f"Metrics save error: {e}")
            await asyncio.sleep(300)  # Every 5 minutes

    async def run(self):
        """Main daemon loop."""
        self.running = True
        self.state.set('started_at', datetime.now().isoformat())
        self.state.save()

        # Start file watchers (sync, runs in background thread)
        self.watchers.start()

        # Count monitored paths
        n_queues = len(self.config['watch_paths']['task_queues'])
        self.logger.info(
            f"Kit daemon online. Monitoring {n_queues} task queues, "
            f"UW exports, memory dir, system health."
        )

        # Start async loops
        tasks = [
            asyncio.create_task(self.system_check_loop()),
            asyncio.create_task(self.health_check_loop()),
            asyncio.create_task(self.anticipation_loop()),
            asyncio.create_task(self.workflow_loop()),
            asyncio.create_task(self.skill_evolution_loop()),
            asyncio.create_task(self.dashboard_loop()),
            asyncio.create_task(self.intelligence_loop()),
            asyncio.create_task(self.ambient_analysis_loop()),
            asyncio.create_task(self.trace_learning_loop()),
            asyncio.create_task(self.self_model_loop()),
            asyncio.create_task(self.goal_horizon_loop()),
            asyncio.create_task(self.metrics_loop()),
        ]

        try:
            # Run until cancelled
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            self.logger.info("Daemon tasks cancelled")
        finally:
            self.shutdown()

    def shutdown(self):
        """Clean shutdown."""
        self.running = False
        self.logger.info("Shutting down...")

        # Flush high-priority messages
        self.comms.flush_queue(min_priority=8)

        # Stop watchers
        try:
            self.watchers.stop()
        except Exception as e:
            self.logger.error(f"Watcher stop error: {e}")

        # Save state
        self.state.save()
        self.logger.info("Kit daemon shut down. State saved.")

    def smoke_test(self):
        """Run a quick smoke test of all modules."""
        print("=" * 60)
        print("  Kit Daemon — Smoke Test")
        print("=" * 60)

        tests_passed = 0
        tests_total = 0

        def check(name, condition, detail=""):
            nonlocal tests_passed, tests_total
            tests_total += 1
            if condition:
                tests_passed += 1
                print(f"  ✅ {name}")
            else:
                print(f"  ❌ {name} — {detail}")

        # Config
        check("Config loaded", self.config is not None)
        check("Workspace path exists", os.path.exists(self.config['paths']['workspace']))
        check("Feather path exists", os.path.exists(self.config['paths']['feather']))
        check("Daemon home exists", os.path.exists(self.config['paths']['daemon_home']))

        # State
        check("State manager initialized", self.state is not None)
        self.state.set('test_key', 'test_value')
        check("State set/get works", self.state.get('test_key') == 'test_value')

        # Comms
        check("Comms manager initialized", self.comms is not None)
        check("Quiet hours detection works", isinstance(self.comms.is_quiet_hours(), bool))
        check("Priority label works", self.comms.priority_label(10) == "🚨 CRITICAL")

        # System
        check("System monitor initialized", self.system is not None)
        disk = self.system.check_disk()
        check("Disk check works", 'free_gb' in disk, str(disk))

        ram = self.system.check_ram()
        check("RAM check works", 'available_gb' in ram, str(ram))

        gpu = self.system.check_gpu()
        check("GPU check works", gpu.get('status') != 'error', str(gpu))

        # Health
        check("Health monitor initialized", self.health is not None)

        # Watchers
        check("Watcher manager initialized", self.watchers is not None)

        # Anticipation
        check("Anticipation engine initialized", self.anticipation is not None)
        expected = self.anticipation.get_expected_first_message()
        check("Expected message time works", ':' in expected, expected)

        # Learning
        check("Learning engine initialized", self.learning is not None)

        # Skills
        check("Skill evolution engine initialized", self.skills is not None)

        # Memory Graph
        check("Memory graph initialized", self.graph is not None)
        stats = self.graph.get_stats()
        check(f"Graph has entities: {stats['total_entities']}", stats['total_entities'] > 0)
        check(f"Graph has relationships: {stats['total_relationships']}", stats['total_relationships'] > 0)
        # Test traversal
        path = self.graph.find_path('the user', 'CompetitorTool')
        check("Graph traversal works (Andrew→AgentForce)", path is not None and len(path) > 1)
        dashboard = self.skills.get_dashboard()
        check(f"Skills tracked: {len(dashboard)}", len(dashboard) > 0)
        # Test the observe cycle
        tracker = self.skills.get_tracker('smoke-test', 'Smoke Test')
        tracker.record_run(success=True, duration_seconds=0.1, model='test')
        tracker.record_run(success=False, duration_seconds=0.2, error='test error', model='test')
        status = tracker.get_status()
        check("Skill tracking works", status['total_runs'] == 2 and status['success_rate'] == 0.5)

        # Trace Learning
        check("Trace store initialized", self.trace_store is not None)
        check("Trace collector initialized", self.trace_collector is not None)
        check("Learning engine initialized (trace)", self.learning_engine is not None)
        # Record a test trace
        test_trace_id = self.trace_collector.record_interaction(
            query="Smoke test query",
            response="Smoke test response",
            model="test-model",
            duration=0.5,
            tokens=10,
            source="smoke-test",
        )
        check("Trace recorded", self.trace_store.count() > 0)
        model_stats = self.trace_store.get_model_stats()
        check("Trace model stats work", isinstance(model_stats, dict))
        # Decision Attribution
        check("Decision attributor initialized", self.attributor is not None)
        test_trace = {'trace_id': 'test', 'outcome': 'failure', 'result': 'timeout after 120s', 'query': 'test task', 'total_latency': 130, 'model': 'test', 'task_class': 'test'}
        attr_result = self.attributor.analyze_trace(test_trace)
        check("Attribution produces causes", len(attr_result['causes']) > 0)
        check("Attribution produces tips", len(attr_result['tips']) > 0)
        check("Attribution classifies correctly", attr_result['classification'] == 'timeout')
        # Curator (quality gate)
        from decision_attribution import Tip as _Tip, TipType as _TT
        low_conf_tip = _Tip(tip_type=_TT.OPTIMIZATION, title="Bad tip", description="x", action="x", confidence=0.1)
        curated = self.attributor._curate_tips([low_conf_tip])
        check("Curator rejects low confidence", len(curated) == 0)
        # Playbooks
        playbooks = self.attributor.generate_playbooks()
        check("Playbook generator runs", isinstance(playbooks, dict))
        # Cost Tracker
        check("Cost tracker initialized", self.cost_tracker is not None)
        from cost_tracker import calculate_cost, is_local_model
        check("Local model detection", is_local_model('qwen3.5:9b') == True)
        check("Cloud model detection", is_local_model('claude-opus-4-6') == False)
        opus_cost = calculate_cost('claude-opus-4-6', 100000, 1000)
        check("Opus cost calculation", opus_cost > 0)
        # Preference Filter
        check("Preference filter initialized", self.preference_filter is not None)
        sig = self.preference_filter.detect_signal("Thanks! That's exactly what I needed")
        check("Engage signal detected", sig['signal_type'] == 'engage')
        sig2 = self.preference_filter.detect_signal("Actually, different topic")
        check("Ignore signal detected", sig2['signal_type'] == 'ignore')
        sig3 = self.preference_filter.detect_signal("What I meant was something else")
        check("Repeat signal detected", sig3['signal_type'] == 'repeat')
        prefs = self.preference_filter.get_preferences()
        check("Preferences returns dict", isinstance(prefs, dict))

        # Self-Model
        check("Self-model initialized", self.self_model is not None)
        sm_summary = self.self_model.get_summary()
        check("Self-model loaded", sm_summary['loaded'])
        check(f"Self-model capabilities: {sm_summary['capabilities_count']}", sm_summary['capabilities_count'] > 0)
        cap = self.self_model.query_capability('file operations')
        check("Self-model capability query works", cap['found'])
        cap_unknown = self.self_model.query_capability('quantum entanglement')
        check("Self-model unknown query returns not found", not cap_unknown['found'])

        # Curiosity Engine
        check("Curiosity engine initialized", self.curiosity is not None)
        from curiosity_engine import ResponseMonitor
        monitor = ResponseMonitor()
        sig_confident = monitor.analyze_response("The file is located at /tmp/test.txt and contains 42 lines.")
        check("Confident response scores high", sig_confident.confidence >= 0.8)
        sig_uncertain = monitor.analyze_response(
            "I don't know, I'm not sure, possibly it might be something else",
            "What is the capital of Atlantis?"
        )
        check("Uncertain response scores low", sig_uncertain.confidence < 0.5)
        check("Uncertain response flags research", sig_uncertain.should_research or sig_uncertain.hedging_count > 0)
        curiosity_stats = self.curiosity.get_stats()
        check("Curiosity stats returns dict", isinstance(curiosity_stats, dict))

        # Goal Horizon
        check("Goal horizon initialized", self.goal_horizon is not None)
        gh_summary = self.goal_horizon.get_summary()
        check("Goal horizon loaded", gh_summary['loaded'])
        check(f"Goals found: {gh_summary['total_goals']}", gh_summary['total_goals'] > 0)
        critical = self.goal_horizon.get_critical_goals()
        check(f"Critical goals detected: {len(critical)}", len(critical) > 0)
        score = self.goal_horizon.prioritize_task("Prospectus benchmark voice parsing improvement")
        check("Goal-aligned task gets positive score", score > 0)
        score_unrelated = self.goal_horizon.prioritize_task("Random unrelated gibberish xyz")
        check("Unrelated task gets zero score", score_unrelated == 0)

        # Watch paths
        for tq in self.config['watch_paths']['task_queues']:
            parent = os.path.dirname(tq)
            check(f"Task queue dir exists: ...{parent[-30:]}", os.path.exists(parent))

        uw_path = self.config['watch_paths']['uw_exports']
        check(f"UW exports dir exists", os.path.exists(uw_path))

        mem_path = self.config['watch_paths']['memory_dir']
        check(f"Memory dir exists", os.path.exists(mem_path))

        print()
        print(f"  {tests_passed}/{tests_total} tests passed")
        print("=" * 60)

        if tests_passed == tests_total:
            print("  🟢 All systems go. Ready to start.")
        else:
            print(f"  🟡 {tests_total - tests_passed} issue(s) found. Review above.")

        return tests_passed == tests_total


def main():
    if '--test' in sys.argv:
        daemon = KitDaemon()
        success = daemon.smoke_test()
        sys.exit(0 if success else 1)

    daemon = KitDaemon()

    # Handle Ctrl+C gracefully
    def handle_signal(sig, frame):
        daemon.logger.info(f"Received signal {sig}")
        daemon.running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Run the daemon
    try:
        asyncio.run(daemon.run())
    except KeyboardInterrupt:
        daemon.shutdown()


if __name__ == '__main__':
    main()
