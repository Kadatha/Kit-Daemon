"""
Kit Daemon — Multi-Modal Awareness
Process images, screenshots, and voice memos dropped into watched folders.

Current capabilities:
- Image detection + metadata extraction (always available)
- Vision analysis via Qwen-VL when model is loaded (optional)
- Voice memo detection + transcription via Whisper (future)
- Screenshot OCR via Tesseract or vision model (future)

The module is designed to work in degraded mode:
- No vision model? → Extract metadata, log the file, notify Kit
- Vision model available? → Full analysis with description
"""
import json
import logging
import os
import struct
from datetime import datetime
from pathlib import Path

logger = logging.getLogger('kit-daemon.multimodal')

# Supported file types
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff'}
VOICE_EXTENSIONS = {'.mp3', '.wav', '.m4a', '.ogg', '.flac', '.opus'}
DOCUMENT_EXTENSIONS = {'.pdf', '.csv', '.xlsx', '.xls', '.docx'}


class MultiModalProcessor:
    """Processes non-text files dropped into watched directories."""

    def __init__(self, config, state_manager, comms_manager):
        self.config = config
        self.state = state_manager
        self.comms = comms_manager
        self.inbox_dir = os.path.join(config['paths']['daemon_home'], 'inbox')
        self.processed_dir = os.path.join(config['paths']['daemon_home'], 'inbox', 'processed')
        os.makedirs(self.inbox_dir, exist_ok=True)
        os.makedirs(self.processed_dir, exist_ok=True)

        # Check if vision model is available
        self.vision_available = self._check_vision_model()

    def _check_vision_model(self):
        """Check if a vision-capable model is loaded in Ollama."""
        try:
            import urllib.request
            req = urllib.request.urlopen(
                f"{self.config['ollama_url']}/api/tags", timeout=5
            )
            data = json.loads(req.read())
            models = [m.get('name', '') for m in data.get('models', [])]
            # Check for vision-capable models
            vision_models = [m for m in models if any(
                v in m.lower() for v in ['qwen2.5vl', 'qwen-vl', 'llava', 'bakllava', 'moondream']
            )]
            if vision_models:
                logger.info(f"Vision model available: {vision_models[0]}")
                return vision_models[0]
        except Exception:
            pass
        return None

    def process_file(self, file_path):
        """Process any file based on its type."""
        ext = os.path.splitext(file_path)[1].lower()

        if ext in IMAGE_EXTENSIONS:
            return self.process_image(file_path)
        elif ext in VOICE_EXTENSIONS:
            return self.process_voice(file_path)
        elif ext in DOCUMENT_EXTENSIONS:
            return self.process_document(file_path)
        else:
            return {'type': 'unknown', 'path': file_path}

    def process_image(self, file_path):
        """Process an image file."""
        result = {
            'type': 'image',
            'path': file_path,
            'filename': os.path.basename(file_path),
            'size_bytes': os.path.getsize(file_path),
            'processed_at': datetime.now().isoformat(),
        }

        # Extract basic metadata
        try:
            dimensions = self._get_image_dimensions(file_path)
            if dimensions:
                result['width'], result['height'] = dimensions
        except Exception as e:
            logger.debug(f"Could not get image dimensions: {e}")

        # Vision analysis if available
        if self.vision_available:
            analysis = self._analyze_with_vision(file_path)
            if analysis:
                result['analysis'] = analysis
                result['vision_model'] = self.vision_available
        else:
            result['analysis'] = None
            result['note'] = 'No vision model available. Install qwen-vl or llava for image analysis.'

        self._log_processed(result)
        return result

    def process_voice(self, file_path):
        """Process a voice memo."""
        result = {
            'type': 'voice',
            'path': file_path,
            'filename': os.path.basename(file_path),
            'size_bytes': os.path.getsize(file_path),
            'processed_at': datetime.now().isoformat(),
            'transcription': None,
            'note': 'Voice transcription requires Whisper model. '
                    'Run: ollama pull whisper (when available) or install openai-whisper.',
        }

        self._log_processed(result)
        return result

    def process_document(self, file_path):
        """Process a document file."""
        ext = os.path.splitext(file_path)[1].lower()
        result = {
            'type': 'document',
            'path': file_path,
            'filename': os.path.basename(file_path),
            'size_bytes': os.path.getsize(file_path),
            'extension': ext,
            'processed_at': datetime.now().isoformat(),
        }

        # CSV files: extract row count and headers
        if ext == '.csv':
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                result['row_count'] = len(lines) - 1  # Minus header
                if lines:
                    result['headers'] = lines[0].strip().split(',')[:10]
            except Exception as e:
                result['error'] = str(e)

        self._log_processed(result)
        return result

    def _get_image_dimensions(self, file_path):
        """Get image dimensions without PIL (pure Python)."""
        with open(file_path, 'rb') as f:
            header = f.read(32)

        # PNG
        if header[:8] == b'\x89PNG\r\n\x1a\n':
            w, h = struct.unpack('>II', header[16:24])
            return w, h

        # JPEG
        if header[:2] == b'\xff\xd8':
            with open(file_path, 'rb') as f:
                f.seek(2)
                while True:
                    marker = f.read(2)
                    if not marker or marker[0] != 0xff:
                        break
                    if marker[1] in (0xc0, 0xc2):
                        f.read(3)  # length + precision
                        h, w = struct.unpack('>HH', f.read(4))
                        return w, h
                    else:
                        length = struct.unpack('>H', f.read(2))[0]
                        f.seek(length - 2, 1)

        return None

    def _analyze_with_vision(self, file_path):
        """Send image to vision model for analysis."""
        try:
            import base64
            import urllib.request

            with open(file_path, 'rb') as f:
                img_base64 = base64.b64encode(f.read()).decode('utf-8')

            payload = json.dumps({
                'model': self.vision_available,
                'prompt': 'Describe this image concisely. What is it? What details are notable?',
                'images': [img_base64],
                'stream': False,
            }).encode('utf-8')

            req = urllib.request.Request(
                f"{self.config['ollama_url']}/api/generate",
                data=payload,
                headers={'Content-Type': 'application/json'},
            )
            resp = urllib.request.urlopen(req, timeout=60)
            data = json.loads(resp.read())
            return data.get('response', '')[:500]

        except Exception as e:
            logger.warning(f"Vision analysis failed: {e}")
            return None

    def _log_processed(self, result):
        """Log processed file to daily log."""
        date_str = datetime.now().strftime('%Y-%m-%d')
        log_file = os.path.join(self.inbox_dir, f'processed_{date_str}.jsonl')
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(result) + '\n')

    def scan_inbox(self):
        """Scan inbox directory for new files to process."""
        results = []
        processed_log = self._get_processed_files()

        for entry in os.scandir(self.inbox_dir):
            if entry.is_file() and entry.name not in processed_log:
                ext = os.path.splitext(entry.name)[1].lower()
                if ext in IMAGE_EXTENSIONS | VOICE_EXTENSIONS | DOCUMENT_EXTENSIONS:
                    result = self.process_file(entry.path)
                    results.append(result)
                    processed_log.add(entry.name)

        return results

    def _get_processed_files(self):
        """Get set of already-processed filenames."""
        processed = set()
        date_str = datetime.now().strftime('%Y-%m-%d')
        log_file = os.path.join(self.inbox_dir, f'processed_{date_str}.jsonl')
        if os.path.exists(log_file):
            with open(log_file, 'r') as f:
                for line in f:
                    try:
                        data = json.loads(line)
                        processed.add(data.get('filename', ''))
                    except json.JSONDecodeError:
                        pass
        return processed

    def get_status(self):
        """Get multimodal processor status."""
        return {
            'vision_model': self.vision_available or 'none',
            'inbox_path': self.inbox_dir,
            'vision_capable': bool(self.vision_available),
            'supported_images': sorted(IMAGE_EXTENSIONS),
            'supported_voice': sorted(VOICE_EXTENSIONS),
            'supported_docs': sorted(DOCUMENT_EXTENSIONS),
        }
