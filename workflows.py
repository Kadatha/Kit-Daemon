"""
Kit Daemon — Workflow Automation Engine
Event → Pipeline → Result → Notification

Workflows are triggered by events (file changes, time, cron completions)
and execute a chain of steps automatically.

Example workflow:
  trigger: UW CSV lands in feather/data/uw-exports/
  steps: 1) validate CSV  2) run signal filter  3) summarize results
  notify: send summary to the user via Telegram
"""
import asyncio
import json
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path

logger = logging.getLogger('kit-daemon.workflows')


class WorkflowStep:
    """A single step in a workflow pipeline."""

    def __init__(self, name, action, params=None, timeout=120):
        self.name = name
        self.action = action  # 'shell', 'python', 'wake_kit', 'file_check'
        self.params = params or {}
        self.timeout = timeout

    async def execute(self, context):
        """Execute this step. Returns (success, result_dict)."""
        try:
            if self.action == 'shell':
                return await self._run_shell(context)
            elif self.action == 'python':
                return await self._run_python(context)
            elif self.action == 'wake_kit':
                return await self._wake_kit(context)
            elif self.action == 'file_check':
                return await self._file_check(context)
            else:
                return False, {'error': f'Unknown action: {self.action}'}
        except Exception as e:
            logger.error(f"Step '{self.name}' failed: {e}")
            return False, {'error': str(e)}

    async def _run_shell(self, context):
        """Run a PowerShell command."""
        cmd = self.params.get('command', '')
        # Substitute context variables
        for key, val in context.items():
            cmd = cmd.replace(f'{{{key}}}', str(val))

        proc = await asyncio.create_subprocess_shell(
            f'powershell -Command "{cmd}"',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout
            )
            success = proc.returncode == 0
            return success, {
                'stdout': stdout.decode('utf-8', errors='replace')[:2000],
                'stderr': stderr.decode('utf-8', errors='replace')[:500],
                'returncode': proc.returncode,
            }
        except asyncio.TimeoutError:
            proc.kill()
            return False, {'error': f'Timeout after {self.timeout}s'}

    async def _run_python(self, context):
        """Run a Python script."""
        script = self.params.get('script', '')
        args = self.params.get('args', [])
        # Substitute context variables in args
        resolved_args = []
        for arg in args:
            for key, val in context.items():
                arg = arg.replace(f'{{{key}}}', str(val))
            resolved_args.append(arg)

        cmd = ['python', script] + resolved_args
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout
            )
            success = proc.returncode == 0
            return success, {
                'stdout': stdout.decode('utf-8', errors='replace')[:2000],
                'stderr': stderr.decode('utf-8', errors='replace')[:500],
                'returncode': proc.returncode,
            }
        except asyncio.TimeoutError:
            proc.kill()
            return False, {'error': f'Timeout after {self.timeout}s'}

    async def _wake_kit(self, context):
        """Send a wake event to Kit's main session via OpenClaw."""
        message = self.params.get('message', '')
        for key, val in context.items():
            message = message.replace(f'{{{key}}}', str(val))

        proc = await asyncio.create_subprocess_shell(
            f'powershell -Command "openclaw system event --text \\"{message}\\" --mode now"',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        return proc.returncode == 0, {'message': message}

    async def _file_check(self, context):
        """Check if a file exists and optionally validate it."""
        path = self.params.get('path', '')
        for key, val in context.items():
            path = path.replace(f'{{{key}}}', str(val))

        exists = os.path.exists(path)
        if not exists:
            return False, {'error': f'File not found: {path}'}

        size = os.path.getsize(path)
        min_size = self.params.get('min_size_bytes', 0)
        if size < min_size:
            return False, {'error': f'File too small: {size} bytes (min {min_size})'}

        return True, {'path': path, 'size_bytes': size}


class Workflow:
    """A complete workflow: trigger condition + pipeline steps + notification."""

    def __init__(self, workflow_id, name, steps, notify_on_success=True,
                 notify_on_failure=True, enabled=True):
        self.workflow_id = workflow_id
        self.name = name
        self.steps = steps
        self.notify_on_success = notify_on_success
        self.notify_on_failure = notify_on_failure
        self.enabled = enabled

    async def execute(self, trigger_context):
        """Run the full pipeline."""
        if not self.enabled:
            return {'status': 'disabled'}

        start = datetime.now()
        context = {**trigger_context}
        results = []
        success = True

        logger.info(f"Workflow '{self.name}' started")

        for step in self.steps:
            step_start = datetime.now()
            step_ok, step_result = await step.execute(context)
            step_duration = (datetime.now() - step_start).total_seconds()

            results.append({
                'step': step.name,
                'success': step_ok,
                'duration_seconds': round(step_duration, 1),
                'result': step_result,
            })

            # Add step results to context for subsequent steps
            context[f'step_{step.name}_result'] = step_result
            context[f'step_{step.name}_ok'] = step_ok

            if not step_ok:
                success = False
                logger.warning(f"Workflow '{self.name}' failed at step '{step.name}'")
                break  # Stop pipeline on failure

        total_duration = (datetime.now() - start).total_seconds()

        return {
            'workflow_id': self.workflow_id,
            'name': self.name,
            'success': success,
            'duration_seconds': round(total_duration, 1),
            'steps_completed': len(results),
            'steps_total': len(self.steps),
            'results': results,
            'timestamp': start.isoformat(),
        }


class WorkflowEngine:
    """Manages workflow definitions and execution."""

    def __init__(self, config, state_manager, comms_manager, skill_engine=None):
        self.config = config
        self.state = state_manager
        self.comms = comms_manager
        self.skills = skill_engine
        self.workflows = {}
        self.run_log_dir = os.path.join(config['paths']['daemon_home'], 'workflow-runs')
        os.makedirs(self.run_log_dir, exist_ok=True)

        self._register_built_in_workflows()

    def _register_built_in_workflows(self):
        """Register the built-in workflows."""

        # ─── UW CSV Processing ───
        self.workflows['uw-csv-process'] = Workflow(
            workflow_id='uw-csv-process',
            name='Process UW Export',
            steps=[
                WorkflowStep('validate', 'file_check', {
                    'path': '{file_path}',
                    'min_size_bytes': 100,
                }),
                WorkflowStep('parse', 'python', {
                    'script': os.path.join(self.config['paths']['feather'], 'src', 'csv_loader.py'),
                    'args': ['{file_path}'],
                }, timeout=60),
                WorkflowStep('notify', 'wake_kit', {
                    'message': '[DAEMON] UW CSV processed: {file_path}. Check Feather output.',
                }),
            ],
        )

        # ─── Urgent Task Handler ───
        self.workflows['urgent-task'] = Workflow(
            workflow_id='urgent-task',
            name='Handle Urgent Task',
            steps=[
                WorkflowStep('wake', 'wake_kit', {
                    'message': '[DAEMON] URGENT task detected in {file_path}. Triggering immediate processing.',
                }),
            ],
        )

        # ─── Memory Reindex ───
        self.workflows['memory-reindex'] = Workflow(
            workflow_id='memory-reindex',
            name='Reindex Memory',
            steps=[
                WorkflowStep('reindex', 'shell', {
                    'command': 'openclaw memory index',
                }, timeout=60),
            ],
            notify_on_success=False,  # Silent success
        )

        # ─── Morning Brief Compile ───
        self.workflows['morning-brief-compile'] = Workflow(
            workflow_id='morning-brief-compile',
            name='Compile Morning Brief',
            steps=[
                WorkflowStep('system-snapshot', 'shell', {
                    'command': 'nvidia-smi --query-gpu=memory.used,memory.total,temperature.gpu --format=csv,noheader',
                }),
                WorkflowStep('task-summary', 'shell', {
                    'command': ('Get-Content "' +
                                self.config['paths']['workspace'] +
                                '\\TASKQUEUE.md" -ErrorAction SilentlyContinue; ' +
                                'Get-Content "' + self.config['paths']['feather'] +
                                '\\TASKQUEUE.md" -ErrorAction SilentlyContinue'),
                }),
                WorkflowStep('cron-check', 'shell', {
                    'command': 'openclaw cron runs --id ' +
                               self.config.get('worker_cron_id', '') +
                               ' --limit 5',
                }, timeout=30),
                WorkflowStep('compile', 'python', {
                    'script': os.path.join(self.config['paths']['daemon_home'], 'compile_brief.py'),
                }, timeout=30),
            ],
        )

    async def trigger(self, workflow_id, context):
        """Trigger a workflow by ID."""
        workflow = self.workflows.get(workflow_id)
        if not workflow:
            logger.error(f"Unknown workflow: {workflow_id}")
            return None

        logger.info(f"Triggering workflow: {workflow.name}")
        result = await workflow.execute(context)

        # Log the run
        self._log_run(result)

        # Track in skill evolution
        if self.skills:
            self.skills.record_cron_run(
                cron_id=f'workflow_{workflow_id}',
                skill_id=f'workflow-{workflow_id}',
                success=result['success'],
                duration_seconds=result['duration_seconds'],
                error=result['results'][-1]['result'].get('error') if not result['success'] else None,
            )

        # Notify
        if result['success'] and workflow.notify_on_success:
            self.comms.send_telegram(
                f"✅ Workflow '{workflow.name}' completed in {result['duration_seconds']}s",
                priority=5
            )
        elif not result['success'] and workflow.notify_on_failure:
            failed_step = result['results'][-1]['step'] if result['results'] else 'unknown'
            self.comms.send_telegram(
                f"❌ Workflow '{workflow.name}' failed at step '{failed_step}'",
                priority=8
            )

        return result

    def _log_run(self, result):
        """Save workflow run to log."""
        date_str = datetime.now().strftime('%Y-%m-%d')
        log_file = os.path.join(self.run_log_dir, f'{date_str}.jsonl')
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(result) + '\n')

    def list_workflows(self):
        """List all registered workflows."""
        return {
            wid: {
                'name': w.name,
                'enabled': w.enabled,
                'steps': len(w.steps),
            }
            for wid, w in self.workflows.items()
        }

