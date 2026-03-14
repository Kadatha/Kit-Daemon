"""
Kit Daemon — Proactive Intelligence Engine
Monitors external sources for developments the user cares about.
Evaluates significance. Only surfaces what matters.

Jarvis parallel: "Sir, you might want to see this.
S.H.I.E.L.D. just classified the Tesseract project."

Sources (no API keys needed):
- RSS feeds from AI labs, tech news
- GitHub release monitoring
- arxiv new papers
- Hacker News front page
- Reddit hot posts (public JSON)
"""
import json
import logging
import os
import hashlib
import urllib.request
import urllib.error
from datetime import datetime, timedelta

logger = logging.getLogger('kit-daemon.intelligence')

# Topics the user cares about (weighted by relevance)
INTEREST_KEYWORDS = {
    # High relevance (weight 3)
    'openclaw': 3, 'claude': 3, 'anthropic': 3, 'opus': 3, 'sonnet': 3,
    'qwen': 3, 'ollama': 3, 'local llm': 3, 'agent memory': 3,
    'openai': 3, 'gpt': 3, 'deepseek': 3,

    # Medium relevance (weight 2)
    'ai agent': 2, 'autonomous agent': 2, 'fine-tuning': 2, 'lora': 2,
    'qlora': 2, 'function calling': 2, 'tool use': 2, 'mcp': 2,
    'xai': 2, 'grok': 2, 'gemini': 2, 'meta ai': 2, 'llama': 2,
    'mistral': 2, 'open source ai': 2, 'ai coding': 2,
    'options trading': 2, 'unusual whales': 2,

    # Low relevance (weight 1)
    'machine learning': 1, 'neural network': 1, 'transformer': 1,
    'benchmark': 1, 'inference': 1, 'quantization': 1, 'gguf': 1,
    'steel': 1, 'manufacturing': 1, 'crm': 1, 'salesforce': 1,
}

# Significance thresholds
SIGNIFICANCE_HIGH = 6     # Must-see — send immediately
SIGNIFICANCE_MEDIUM = 3   # Worth knowing — include in digest
SIGNIFICANCE_LOW = 1      # Background — log but don't alert


class IntelItem:
    """A single intelligence item."""

    def __init__(self, title, url, source, summary=None):
        self.title = title
        self.url = url
        self.source = source
        self.summary = summary
        self.discovered_at = datetime.now()
        self.significance = 0
        self.matched_keywords = []
        self.item_hash = hashlib.md5(f"{title}{url}".encode()).hexdigest()[:12]

    def score_significance(self):
        """Score how significant this item is for the user."""
        title_lower = self.title.lower()
        summary_lower = (self.summary or '').lower()
        text = f"{title_lower} {summary_lower}"

        self.matched_keywords = []
        total_score = 0

        for keyword, weight in INTEREST_KEYWORDS.items():
            if keyword in text:
                self.matched_keywords.append(keyword)
                total_score += weight

        # Bonus for multiple keyword matches (indicates convergence)
        if len(self.matched_keywords) >= 3:
            total_score += 2

        self.significance = total_score
        return total_score

    def to_dict(self):
        return {
            'title': self.title,
            'url': self.url,
            'source': self.source,
            'summary': self.summary[:200] if self.summary else None,
            'significance': self.significance,
            'keywords': self.matched_keywords,
            'discovered_at': self.discovered_at.isoformat(),
            'hash': self.item_hash,
        }


class IntelligenceEngine:
    """Monitors external sources and scores significance."""

    def __init__(self, config, state_manager, comms_manager):
        self.config = config
        self.state = state_manager
        self.comms = comms_manager
        self.intel_dir = os.path.join(config['paths']['daemon_home'], 'intel')
        os.makedirs(self.intel_dir, exist_ok=True)

        # Track seen items to avoid duplicates
        self.seen_hashes = self._load_seen_hashes()

    def _load_seen_hashes(self):
        """Load previously seen item hashes."""
        hash_file = os.path.join(self.intel_dir, 'seen_hashes.json')
        try:
            with open(hash_file, 'r') as f:
                return set(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            return set()

    def _save_seen_hashes(self):
        """Save seen hashes (keep last 1000)."""
        hash_file = os.path.join(self.intel_dir, 'seen_hashes.json')
        recent = list(self.seen_hashes)[-1000:]
        with open(hash_file, 'w') as f:
            json.dump(recent, f)

    def scan_all_sources(self):
        """Scan all configured sources. Returns list of significant items."""
        items = []

        # Hacker News (no auth needed, public API)
        items.extend(self._scan_hackernews())

        # Reddit public JSON (no auth needed)
        items.extend(self._scan_reddit())

        # GitHub releases for watched repos
        items.extend(self._scan_github_releases())

        # Score and filter
        significant = []
        for item in items:
            if item.item_hash in self.seen_hashes:
                continue

            item.score_significance()
            self.seen_hashes.add(item.item_hash)

            if item.significance >= SIGNIFICANCE_LOW:
                significant.append(item)

        # Save seen hashes
        self._save_seen_hashes()

        # Sort by significance
        significant.sort(key=lambda x: -x.significance)

        # Log results
        self._log_intel(significant)

        # Alert on high-significance items
        high = [i for i in significant if i.significance >= SIGNIFICANCE_HIGH]
        if high:
            for item in high[:3]:  # Max 3 alerts per scan
                self.comms.send_telegram(
                    f"🔔 Intel: {item.title}\n{item.url}\n"
                    f"Keywords: {', '.join(item.matched_keywords[:5])}",
                    priority=6
                )

        logger.info(f"Intel scan: {len(items)} items found, "
                    f"{len(significant)} significant, {len(high)} high-priority")

        return significant

    def _scan_hackernews(self):
        """Scan Hacker News top stories."""
        items = []
        try:
            url = 'https://hacker-news.firebaseio.com/v0/topstories.json'
            req = urllib.request.urlopen(url, timeout=10)
            story_ids = json.loads(req.read())[:30]  # Top 30

            for sid in story_ids[:15]:  # Check first 15 to save time
                try:
                    story_url = f'https://hacker-news.firebaseio.com/v0/item/{sid}.json'
                    req = urllib.request.urlopen(story_url, timeout=5)
                    story = json.loads(req.read())
                    if story and story.get('title'):
                        items.append(IntelItem(
                            title=story['title'],
                            url=story.get('url', f'https://news.ycombinator.com/item?id={sid}'),
                            source='hackernews',
                            summary=story.get('text', '')[:200] if story.get('text') else None,
                        ))
                except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
                    continue

        except Exception as e:
            logger.warning(f"HN scan failed: {e}")

        return items

    def _scan_reddit(self):
        """Scan AI-related subreddits (public JSON, no auth)."""
        items = []
        subreddits = ['LocalLLaMA', 'MachineLearning', 'OpenAI', 'singularity']

        for sub in subreddits:
            try:
                url = f'https://www.reddit.com/r/{sub}/hot.json?limit=10'
                req = urllib.request.Request(url, headers={
                    'User-Agent': 'Kit-Intelligence/1.0'
                })
                resp = urllib.request.urlopen(req, timeout=10)
                data = json.loads(resp.read())

                for post in data.get('data', {}).get('children', []):
                    pd = post.get('data', {})
                    if pd.get('title'):
                        items.append(IntelItem(
                            title=pd['title'],
                            url=f"https://reddit.com{pd.get('permalink', '')}",
                            source=f'reddit/{sub}',
                            summary=pd.get('selftext', '')[:200] if pd.get('selftext') else None,
                        ))

            except Exception as e:
                logger.debug(f"Reddit r/{sub} scan failed: {e}")

        return items

    def _scan_github_releases(self):
        """Check GitHub repos for new releases."""
        items = []
        repos = [
            'openclaw/openclaw',
            'ollama/ollama',
            'QwenLM/Qwen3.5',
        ]

        for repo in repos:
            try:
                url = f'https://api.github.com/repos/{repo}/releases?per_page=3'
                req = urllib.request.Request(url, headers={
                    'User-Agent': 'Kit-Intelligence/1.0',
                    'Accept': 'application/vnd.github.v3+json',
                })
                resp = urllib.request.urlopen(req, timeout=10)
                releases = json.loads(resp.read())

                for rel in releases:
                    if rel.get('name') or rel.get('tag_name'):
                        items.append(IntelItem(
                            title=f"[{repo}] {rel.get('name') or rel.get('tag_name')}",
                            url=rel.get('html_url', ''),
                            source=f'github/{repo}',
                            summary=rel.get('body', '')[:200] if rel.get('body') else None,
                        ))

            except Exception as e:
                logger.debug(f"GitHub {repo} scan failed: {e}")

        return items

    def _log_intel(self, items):
        """Save today's intelligence to file."""
        if not items:
            return

        date_str = datetime.now().strftime('%Y-%m-%d')
        log_file = os.path.join(self.intel_dir, f'intel_{date_str}.jsonl')
        with open(log_file, 'a', encoding='utf-8') as f:
            for item in items:
                f.write(json.dumps(item.to_dict()) + '\n')

    def get_daily_digest(self, min_significance=SIGNIFICANCE_MEDIUM):
        """Get today's intelligence digest."""
        date_str = datetime.now().strftime('%Y-%m-%d')
        log_file = os.path.join(self.intel_dir, f'intel_{date_str}.jsonl')

        items = []
        if os.path.exists(log_file):
            with open(log_file, 'r') as f:
                for line in f:
                    if line.strip():
                        try:
                            item = json.loads(line)
                            if item.get('significance', 0) >= min_significance:
                                items.append(item)
                        except json.JSONDecodeError:
                            pass

        return sorted(items, key=lambda x: -x.get('significance', 0))

    def compile_digest_markdown(self):
        """Compile intelligence digest as markdown for morning brief."""
        items = self.get_daily_digest()
        if not items:
            return None

        lines = [
            f"## Intelligence Digest — {datetime.now().strftime('%Y-%m-%d')}",
            f"{len(items)} significant items found",
            "",
        ]

        # Group by significance
        high = [i for i in items if i['significance'] >= SIGNIFICANCE_HIGH]
        medium = [i for i in items if SIGNIFICANCE_MEDIUM <= i['significance'] < SIGNIFICANCE_HIGH]

        if high:
            lines.append("### 🔴 High Priority")
            for item in high[:5]:
                lines.append(f"- **{item['title']}**")
                lines.append(f"  [{item['source']}]({item['url']})")
                lines.append(f"  Keywords: {', '.join(item.get('keywords', []))}")
            lines.append("")

        if medium:
            lines.append("### 🟡 Worth Knowing")
            for item in medium[:10]:
                lines.append(f"- [{item['title']}]({item['url']}) ({item['source']})")
            lines.append("")

        digest_file = os.path.join(
            self.config['paths']['workspace'], 'scratch', 'intel-digest.md'
        )
        with open(digest_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))

        return digest_file

