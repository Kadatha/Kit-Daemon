"""
Kit Daemon — File System Watchers
Monitors task queues, UW export folder, and memory directory for changes.
"""
import logging
import os
import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

logger = logging.getLogger('kit-daemon.watchers')


class TaskQueueHandler(FileSystemEventHandler):
    """Watches TASKQUEUE.md files for URGENT tasks."""

    def __init__(self, callback):
        self.callback = callback
        self._last_trigger = 0
        self._debounce_seconds = 5

    def on_modified(self, event):
        if event.is_directory:
            return
        if not event.src_path.endswith('TASKQUEUE.md'):
            return

        # Debounce: don't trigger multiple times for rapid writes
        now = time.time()
        if now - self._last_trigger < self._debounce_seconds:
            return
        self._last_trigger = now

        # Check for URGENT tasks
        try:
            with open(event.src_path, 'r', encoding='utf-8') as f:
                content = f.read()

            if '- [ ] URGENT' in content:
                logger.info(f"URGENT task detected in {event.src_path}")
                self.callback('urgent_task', event.src_path)
            else:
                logger.debug(f"Task queue modified: {event.src_path} (no URGENT)")
        except Exception as e:
            logger.error(f"Error reading task queue {event.src_path}: {e}")


class UWExportHandler(FileSystemEventHandler):
    """Watches for new CSV/JSON files in UW export folder."""

    def __init__(self, callback):
        self.callback = callback

    def on_created(self, event):
        if event.is_directory:
            return
        ext = os.path.splitext(event.src_path)[1].lower()
        if ext in ('.csv', '.json'):
            logger.info(f"New UW export detected: {event.src_path}")
            self.callback('uw_export', event.src_path)


class MemoryFileHandler(FileSystemEventHandler):
    """Watches for new memory files to trigger reindexing."""

    def __init__(self, callback):
        self.callback = callback
        self._last_trigger = 0
        self._debounce_seconds = 30  # Don't reindex more than every 30s

    def on_created(self, event):
        if event.is_directory:
            return
        if event.src_path.endswith('.md'):
            now = time.time()
            if now - self._last_trigger < self._debounce_seconds:
                return
            self._last_trigger = now
            logger.info(f"New memory file: {event.src_path}")
            self.callback('new_memory', event.src_path)


class WatcherManager:
    """Manages all file system watchers."""

    def __init__(self, config, event_callback):
        self.config = config
        self.callback = event_callback
        self.observer = Observer()
        self._setup_watchers()

    def _setup_watchers(self):
        """Set up all file watchers from config."""
        watch_cfg = self.config['watch_paths']

        # Watch UW exports folder
        uw_path = watch_cfg['uw_exports']
        if os.path.exists(uw_path):
            self.observer.schedule(
                UWExportHandler(self.callback),
                uw_path, recursive=False
            )
            logger.info(f"Watching UW exports: {uw_path}")

        # Watch task queue files (watch their parent directories)
        watched_dirs = set()
        for tq_path in watch_cfg['task_queues']:
            parent = os.path.dirname(tq_path)
            if parent not in watched_dirs and os.path.exists(parent):
                self.observer.schedule(
                    TaskQueueHandler(self.callback),
                    parent, recursive=False
                )
                watched_dirs.add(parent)
                logger.info(f"Watching task queue dir: {parent}")

        # Watch memory directory
        mem_dir = watch_cfg['memory_dir']
        if os.path.exists(mem_dir):
            self.observer.schedule(
                MemoryFileHandler(self.callback),
                mem_dir, recursive=False
            )
            logger.info(f"Watching memory dir: {mem_dir}")

    def start(self):
        """Start all watchers."""
        self.observer.start()
        logger.info("File watchers started")

    def stop(self):
        """Stop all watchers."""
        self.observer.stop()
        self.observer.join(timeout=5)
        logger.info("File watchers stopped")
