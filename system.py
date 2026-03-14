"""
Kit Daemon — System Health Monitor
Monitors Ollama, OpenClaw, GPU, disk, RAM. Auto-heals when possible.
"""
import logging
import subprocess
import shutil
import psutil
from datetime import datetime

logger = logging.getLogger('kit-daemon.system')


class SystemMonitor:
    def __init__(self, config, state_manager, comms_manager):
        self.config = config
        self.state = state_manager
        self.comms = comms_manager
        self.health_cfg = config['health']

    def check_all(self):
        """Run all system health checks. Returns dict of results."""
        results = {}
        results['ollama'] = self.check_ollama()
        results['openclaw'] = self.check_openclaw()
        results['disk'] = self.check_disk()
        results['ram'] = self.check_ram()
        results['gpu'] = self.check_gpu()

        self.state.increment('total_health_checks')
        self.state.save()
        return results

    def check_ollama(self):
        """Check if Ollama process is running. Auto-restart if not."""
        try:
            result = subprocess.run(
                ['powershell', '-Command', 'Get-Process ollama -ErrorAction SilentlyContinue'],
                capture_output=True, text=True, timeout=10
            )
            if 'ollama' in result.stdout.lower() or result.returncode == 0:
                # Verify API is responsive
                import urllib.request
                try:
                    req = urllib.request.urlopen(f"{self.config['ollama_url']}/api/tags", timeout=5)
                    if req.status == 200:
                        self.state.update_service_status('ollama', 'healthy')
                        self.state.clear_failure('ollama')
                        return {'status': 'healthy'}
                except Exception:
                    pass

            # Ollama not responding — attempt restart
            return self._heal_ollama()

        except Exception as e:
            logger.error(f"Ollama check failed: {e}")
            return {'status': 'error', 'detail': str(e)}

    def _heal_ollama(self):
        """Attempt to restart Ollama."""
        failures = self.state.record_failure('ollama')
        max_attempts = self.health_cfg['auto_heal_max_attempts']

        if failures > max_attempts:
            msg = f"🚨 Ollama down after {failures} restart attempts. Manual intervention needed."
            self.comms.send_telegram(msg, priority=10)
            self.state.update_service_status('ollama', 'dead')
            return {'status': 'dead', 'attempts': failures}

        logger.warning(f"Ollama not responding. Restart attempt {failures}/{max_attempts}")
        try:
            subprocess.run(
                ['powershell', '-Command', 'Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden'],
                capture_output=True, timeout=15
            )
            self.state.increment('total_self_heals')
            self.state.update_service_status('ollama', 'restarting')
            logger.info("Ollama restart initiated")
            return {'status': 'restarting', 'attempt': failures}
        except Exception as e:
            logger.error(f"Ollama restart failed: {e}")
            return {'status': 'restart_failed', 'error': str(e)}

    def check_openclaw(self):
        """Check if OpenClaw gateway is running."""
        try:
            result = subprocess.run(
                ['powershell', '-Command', 'openclaw gateway status'],
                capture_output=True, text=True, timeout=15
            )
            output = result.stdout.lower()
            # OpenClaw status shows "rpc probe: ok" or "listening" when healthy
            if result.returncode == 0 and ('rpc probe: ok' in output or 'listening' in output):
                self.state.update_service_status('openclaw', 'healthy')
                self.state.clear_failure('openclaw')
                return {'status': 'healthy'}

            # Attempt restart
            return self._heal_openclaw()

        except Exception as e:
            logger.error(f"OpenClaw check failed: {e}")
            return {'status': 'error', 'detail': str(e)}

    def _heal_openclaw(self):
        """Attempt to restart OpenClaw gateway."""
        failures = self.state.record_failure('openclaw')
        max_attempts = self.health_cfg['auto_heal_max_attempts']

        if failures > max_attempts:
            msg = f"🚨 OpenClaw gateway down after {failures} restart attempts. Manual intervention needed."
            self.comms.send_telegram(msg, priority=10)
            self.state.update_service_status('openclaw', 'dead')
            return {'status': 'dead', 'attempts': failures}

        logger.warning(f"OpenClaw not responding. Restart attempt {failures}/{max_attempts}")
        try:
            subprocess.run(
                ['powershell', '-Command', 'openclaw gateway restart'],
                capture_output=True, timeout=30
            )
            self.state.increment('total_self_heals')
            self.state.update_service_status('openclaw', 'restarting')
            return {'status': 'restarting', 'attempt': failures}
        except Exception as e:
            logger.error(f"OpenClaw restart failed: {e}")
            return {'status': 'restart_failed', 'error': str(e)}

    def check_disk(self):
        """Check free disk space on C:."""
        try:
            usage = shutil.disk_usage("C:\\")
            free_gb = usage.free / (1024 ** 3)

            if free_gb < self.health_cfg['min_disk_gb']:
                msg = f"⚠️ Low disk space: {free_gb:.1f}GB free on C:"
                self.comms.send_telegram(msg, priority=8)
                self.state.update_service_status('disk', 'warning')
                return {'status': 'warning', 'free_gb': round(free_gb, 1)}

            self.state.update_service_status('disk', 'healthy')
            return {'status': 'healthy', 'free_gb': round(free_gb, 1)}

        except Exception as e:
            logger.error(f"Disk check failed: {e}")
            return {'status': 'error', 'detail': str(e)}

    def check_ram(self):
        """Check available RAM."""
        try:
            mem = psutil.virtual_memory()
            available_gb = mem.available / (1024 ** 3)

            if available_gb < self.health_cfg['min_ram_gb']:
                msg = f"⚠️ Low RAM: {available_gb:.1f}GB available of {mem.total / (1024**3):.0f}GB"
                self.comms.send_telegram(msg, priority=7)
                self.state.update_service_status('ram', 'warning')
                return {'status': 'warning', 'available_gb': round(available_gb, 1)}

            self.state.update_service_status('ram', 'healthy')
            return {'status': 'healthy', 'available_gb': round(available_gb, 1)}

        except Exception as e:
            logger.error(f"RAM check failed: {e}")
            return {'status': 'error', 'detail': str(e)}

    def check_gpu(self):
        """Check GPU status via nvidia-smi."""
        try:
            result = subprocess.run(
                ['nvidia-smi', '--query-gpu=memory.used,memory.total,temperature.gpu',
                 '--format=csv,noheader,nounits'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split(',')
                if len(parts) >= 3:
                    used = float(parts[0].strip())
                    total = float(parts[1].strip())
                    temp = float(parts[2].strip())
                    pct = (used / total) * 100 if total > 0 else 0

                    status = 'healthy'
                    if pct > self.health_cfg['max_vram_pct']:
                        status = 'warning'
                    if temp > 90:
                        status = 'hot'
                        self.comms.send_telegram(f"⚠️ GPU temperature: {temp}°C", priority=8)

                    self.state.update_service_status('gpu', status)
                    return {
                        'status': status,
                        'vram_used_mb': int(used),
                        'vram_total_mb': int(total),
                        'vram_pct': round(pct, 1),
                        'temp_c': int(temp)
                    }

            return {'status': 'unknown'}

        except FileNotFoundError:
            return {'status': 'nvidia-smi not found'}
        except Exception as e:
            logger.error(f"GPU check failed: {e}")
            return {'status': 'error', 'detail': str(e)}
