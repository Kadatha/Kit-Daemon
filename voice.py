"""
Kit Daemon — Voice Engine
Two-way voice communication for Kit.

Kit → the user: ElevenLabs TTS (Josh voice) via Telegram voice messages
the user → Kit: faster-whisper STT (local, GPU-accelerated)

Voice memos dropped in inbox/ get transcribed automatically.
Kit can send voice messages proactively for important alerts.

Jarvis parallel: "Good morning, sir. You have several notifications."
"""
import json
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path

logger = logging.getLogger('kit-daemon.voice')

# Whisper model sizes (pick based on VRAM)
# tiny: ~1GB, fast, less accurate
# base: ~1GB, good balance
# small: ~2GB, better accuracy
# medium: ~5GB, high accuracy (won't fit alongside qwen3.5:9b)
WHISPER_MODEL = 'base'  # Good balance for our setup

VOICE_EXTENSIONS = {'.mp3', '.wav', '.m4a', '.ogg', '.flac', '.opus', '.webm'}


class VoiceEngine:
    """Handles speech-to-text and text-to-speech for Kit."""

    def __init__(self, config, state_manager, comms_manager):
        self.config = config
        self.state = state_manager
        self.comms = comms_manager
        self.inbox_dir = os.path.join(config['paths']['daemon_home'], 'inbox')
        self.voice_log_dir = os.path.join(config['paths']['daemon_home'], 'voice-logs')
        os.makedirs(self.inbox_dir, exist_ok=True)
        os.makedirs(self.voice_log_dir, exist_ok=True)

        # STT engine (lazy loaded)
        self._whisper_model = None
        self._whisper_available = None

    @property
    def whisper_available(self):
        """Check if faster-whisper is installed."""
        if self._whisper_available is None:
            try:
                import faster_whisper
                self._whisper_available = True
                logger.info("faster-whisper available for STT")
            except ImportError:
                self._whisper_available = False
                logger.warning("faster-whisper not installed. Voice transcription disabled.")
        return self._whisper_available

    def _get_whisper(self):
        """Lazy-load the Whisper model. CPU is fast enough for voice memos."""
        if self._whisper_model is None and self.whisper_available:
            try:
                from faster_whisper import WhisperModel
                # CPU with int8 — fast enough for voice memos, no CUDA dependency
                self._whisper_model = WhisperModel(
                    WHISPER_MODEL,
                    device='cpu',
                    compute_type='int8',
                )
                logger.info(f"Whisper model '{WHISPER_MODEL}' loaded on CPU (int8)")
            except Exception as e:
                logger.error(f"Whisper load failed: {e}")
        return self._whisper_model

    # ─── SPEECH TO TEXT ────────────────────────────────────────

    def transcribe(self, audio_path):
        """Transcribe an audio file to text."""
        model = self._get_whisper()
        if not model:
            return {
                'success': False,
                'error': 'Whisper model not available',
                'path': audio_path,
            }

        start = datetime.now()
        try:
            segments, info = model.transcribe(
                audio_path,
                beam_size=5,
                language='en',
                vad_filter=True,  # Skip silence
            )

            # Collect all segments
            text_parts = []
            for segment in segments:
                text_parts.append(segment.text.strip())

            full_text = ' '.join(text_parts)
            duration = (datetime.now() - start).total_seconds()

            result = {
                'success': True,
                'text': full_text,
                'language': info.language,
                'language_probability': round(info.language_probability, 2),
                'duration_seconds': round(info.duration, 1),
                'transcription_time': round(duration, 1),
                'path': audio_path,
                'timestamp': datetime.now().isoformat(),
            }

            # Log transcription
            self._log_transcription(result)

            logger.info(f"Transcribed {os.path.basename(audio_path)}: "
                       f"{len(full_text)} chars in {duration:.1f}s")

            return result

        except Exception as e:
            logger.error(f"Transcription failed for {audio_path}: {e}")
            return {
                'success': False,
                'error': str(e),
                'path': audio_path,
            }

    def process_voice_memo(self, audio_path):
        """Full pipeline: transcribe a voice memo and notify."""
        result = self.transcribe(audio_path)

        if result['success'] and result['text']:
            # Save transcription as text file alongside the audio
            txt_path = os.path.splitext(audio_path)[0] + '.transcript.txt'
            with open(txt_path, 'w', encoding='utf-8') as f:
                f.write(f"Transcription of {os.path.basename(audio_path)}\n")
                f.write(f"Date: {result['timestamp']}\n")
                f.write(f"Duration: {result['duration_seconds']}s\n")
                f.write(f"Language: {result['language']} ({result['language_probability']})\n")
                f.write(f"---\n\n{result['text']}\n")

            result['transcript_file'] = txt_path

        return result

    # ─── TEXT TO SPEECH ────────────────────────────────────────

    def speak(self, text, priority=5):
        """Send a voice message to the user via the TTS tool.

        This queues a TTS request. The actual sending happens through
        OpenClaw's TTS capability (ElevenLabs Josh voice).
        """
        if not text or len(text.strip()) < 3:
            return False

        # Save to voice queue for the daemon to process
        queue_file = os.path.join(self.voice_log_dir, 'tts_queue.jsonl')
        entry = {
            'text': text[:1000],  # ElevenLabs has character limits
            'priority': priority,
            'queued_at': datetime.now().isoformat(),
            'status': 'pending',
        }

        with open(queue_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry) + '\n')

        logger.info(f"TTS queued: {text[:80]}...")
        return True

    def get_pending_tts(self):
        """Get pending TTS messages."""
        queue_file = os.path.join(self.voice_log_dir, 'tts_queue.jsonl')
        pending = []
        if os.path.exists(queue_file):
            with open(queue_file, 'r') as f:
                for line in f:
                    if line.strip():
                        try:
                            entry = json.loads(line)
                            if entry.get('status') == 'pending':
                                pending.append(entry)
                        except json.JSONDecodeError:
                            pass
        return pending

    # ─── VOICE INBOX SCANNER ──────────────────────────────────

    def scan_inbox(self):
        """Scan inbox for voice files to transcribe."""
        results = []
        processed = self._get_processed_voices()

        for entry in os.scandir(self.inbox_dir):
            if entry.is_file() and entry.name not in processed:
                ext = os.path.splitext(entry.name)[1].lower()
                if ext in VOICE_EXTENSIONS:
                    logger.info(f"Voice file detected: {entry.name}")
                    result = self.process_voice_memo(entry.path)
                    results.append(result)

                    if result['success']:
                        # Notify about transcription
                        preview = result['text'][:150]
                        self.comms.send_telegram(
                            f"🎙️ Voice memo transcribed:\n\"{preview}...\"",
                            priority=6
                        )

        return results

    def _get_processed_voices(self):
        """Get set of already-processed voice filenames."""
        processed = set()
        date_str = datetime.now().strftime('%Y-%m-%d')
        log_file = os.path.join(self.voice_log_dir, f'transcriptions_{date_str}.jsonl')
        if os.path.exists(log_file):
            with open(log_file, 'r') as f:
                for line in f:
                    try:
                        data = json.loads(line)
                        if data.get('path'):
                            processed.add(os.path.basename(data['path']))
                    except json.JSONDecodeError:
                        pass
        return processed

    def _log_transcription(self, result):
        """Log a transcription result."""
        date_str = datetime.now().strftime('%Y-%m-%d')
        log_file = os.path.join(self.voice_log_dir, f'transcriptions_{date_str}.jsonl')
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(result) + '\n')

    # ─── STATUS ────────────────────────────────────────────────

    def get_status(self):
        """Get voice engine status."""
        return {
            'whisper_available': self.whisper_available,
            'whisper_model': WHISPER_MODEL if self.whisper_available else None,
            'whisper_loaded': self._whisper_model is not None,
            'tts_voice': 'Josh (ElevenLabs)',
            'inbox_path': self.inbox_dir,
            'supported_formats': sorted(VOICE_EXTENSIONS),
        }

