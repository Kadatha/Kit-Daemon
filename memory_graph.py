"""
Kit Daemon — Conversational Memory Graph
Beyond flat files. Entities, relationships, and reasoning.

Instead of grepping flat files, Kit can traverse relationship chains:
  User --works_at--> Company --division_of--> ParentCorp
  ParentCorp --board_pressure--> AI Solutions --solved_by--> UserProject
  UserProject --pitched_to--> Champion (Director) --reports_to--> Executive
  Adam (IT Architect) --pushes--> AgentForce --competes_with--> Prospectus

One query: "What connects the user to the CIO?" traverses the graph
and returns the full chain without Kit ever being told explicitly.

Storage: SQLite (local, zero dependencies, survives restarts)
"""
import json
import logging
import os
import sqlite3
from datetime import datetime
from typing import List, Optional, Tuple

logger = logging.getLogger('kit-daemon.memory_graph')


class MemoryGraph:
    """Lightweight knowledge graph backed by SQLite."""

    def __init__(self, config):
        db_path = os.path.join(config['paths']['daemon_home'], 'memory_graph.db')
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()
        logger.info(f"Memory graph initialized: {db_path}")

    def _create_tables(self):
        """Create graph tables if they don't exist."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                entity_type TEXT NOT NULL,  -- person, company, project, concept, tool, event
                properties TEXT DEFAULT '{}',  -- JSON blob
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                source TEXT DEFAULT 'manual'  -- manual, inferred, conversation
            );

            CREATE TABLE IF NOT EXISTS relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                target_id INTEGER NOT NULL,
                relation TEXT NOT NULL,  -- works_at, competes_with, built_by, etc.
                properties TEXT DEFAULT '{}',
                confidence REAL DEFAULT 1.0,  -- 0.0-1.0, lower for inferred
                created_at TEXT NOT NULL,
                source TEXT DEFAULT 'manual',
                FOREIGN KEY (source_id) REFERENCES entities(id),
                FOREIGN KEY (target_id) REFERENCES entities(id),
                UNIQUE(source_id, target_id, relation)
            );

            CREATE TABLE IF NOT EXISTS observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id INTEGER,
                content TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                source TEXT DEFAULT 'conversation',
                FOREIGN KEY (entity_id) REFERENCES entities(id)
            );

            CREATE INDEX IF NOT EXISTS idx_entity_name ON entities(name);
            CREATE INDEX IF NOT EXISTS idx_entity_type ON entities(entity_type);
            CREATE INDEX IF NOT EXISTS idx_rel_source ON relationships(source_id);
            CREATE INDEX IF NOT EXISTS idx_rel_target ON relationships(target_id);
            CREATE INDEX IF NOT EXISTS idx_rel_relation ON relationships(relation);
        """)
        self.conn.commit()

    # ─── ENTITIES ──────────────────────────────────────────────

    def add_entity(self, name, entity_type, properties=None, source='manual'):
        """Add or update an entity."""
        now = datetime.now().isoformat()
        props = json.dumps(properties or {})

        try:
            self.conn.execute("""
                INSERT INTO entities (name, entity_type, properties, created_at, updated_at, source)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    properties = ?,
                    updated_at = ?
            """, (name, entity_type, props, now, now, source, props, now))
            self.conn.commit()
            logger.debug(f"Entity added/updated: {name} ({entity_type})")
            return self._get_entity_id(name)
        except Exception as e:
            logger.error(f"Failed to add entity '{name}': {e}")
            return None

    def _get_entity_id(self, name):
        """Get entity ID by name."""
        row = self.conn.execute(
            "SELECT id FROM entities WHERE name = ?", (name,)
        ).fetchone()
        return row['id'] if row else None

    def get_entity(self, name):
        """Get full entity details."""
        row = self.conn.execute(
            "SELECT * FROM entities WHERE name = ?", (name,)
        ).fetchone()
        if row:
            result = dict(row)
            result['properties'] = json.loads(result['properties'])
            return result
        return None

    def find_entities(self, entity_type=None, search=None):
        """Find entities by type or search term."""
        if entity_type and search:
            rows = self.conn.execute(
                "SELECT * FROM entities WHERE entity_type = ? AND name LIKE ?",
                (entity_type, f'%{search}%')
            ).fetchall()
        elif entity_type:
            rows = self.conn.execute(
                "SELECT * FROM entities WHERE entity_type = ?", (entity_type,)
            ).fetchall()
        elif search:
            rows = self.conn.execute(
                "SELECT * FROM entities WHERE name LIKE ?", (f'%{search}%',)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM entities").fetchall()

        return [dict(r) for r in rows]

    # ─── RELATIONSHIPS ─────────────────────────────────────────

    def add_relationship(self, source_name, relation, target_name,
                         properties=None, confidence=1.0, source='manual'):
        """Add a relationship between two entities."""
        source_id = self._get_entity_id(source_name)
        target_id = self._get_entity_id(target_name)

        if not source_id or not target_id:
            logger.warning(f"Cannot create relationship: entity not found "
                          f"({source_name} -> {target_name})")
            return False

        now = datetime.now().isoformat()
        props = json.dumps(properties or {})

        try:
            self.conn.execute("""
                INSERT INTO relationships
                    (source_id, target_id, relation, properties, confidence, created_at, source)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, target_id, relation) DO UPDATE SET
                    properties = ?,
                    confidence = ?
            """, (source_id, target_id, relation, props, confidence, now, source,
                  props, confidence))
            self.conn.commit()
            logger.debug(f"Relationship: {source_name} --{relation}--> {target_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to add relationship: {e}")
            return False

    def get_connections(self, entity_name, direction='both', relation=None):
        """Get all connections for an entity."""
        entity_id = self._get_entity_id(entity_name)
        if not entity_id:
            return []

        results = []

        if direction in ('out', 'both'):
            query = """
                SELECT r.relation, r.confidence, r.properties,
                       e.name as target, e.entity_type as target_type
                FROM relationships r
                JOIN entities e ON r.target_id = e.id
                WHERE r.source_id = ?
            """
            params = [entity_id]
            if relation:
                query += " AND r.relation = ?"
                params.append(relation)

            for row in self.conn.execute(query, params):
                results.append({
                    'direction': 'out',
                    'relation': row['relation'],
                    'entity': row['target'],
                    'entity_type': row['target_type'],
                    'confidence': row['confidence'],
                })

        if direction in ('in', 'both'):
            query = """
                SELECT r.relation, r.confidence, r.properties,
                       e.name as source, e.entity_type as source_type
                FROM relationships r
                JOIN entities e ON r.source_id = e.id
                WHERE r.target_id = ?
            """
            params = [entity_id]
            if relation:
                query += " AND r.relation = ?"
                params.append(relation)

            for row in self.conn.execute(query, params):
                results.append({
                    'direction': 'in',
                    'relation': row['relation'],
                    'entity': row['source'],
                    'entity_type': row['source_type'],
                    'confidence': row['confidence'],
                })

        return results

    # ─── TRAVERSAL ─────────────────────────────────────────────

    def find_path(self, from_name, to_name, max_depth=5):
        """Find the shortest path between two entities.
        This is the magic — discovering connections Kit was never told."""
        from_id = self._get_entity_id(from_name)
        to_id = self._get_entity_id(to_name)

        if not from_id or not to_id:
            return None

        # BFS traversal
        visited = set()
        queue = [(from_id, [(from_name, None)])]

        while queue and len(visited) < 1000:
            current_id, path = queue.pop(0)

            if current_id == to_id:
                return path

            if current_id in visited or len(path) > max_depth:
                continue
            visited.add(current_id)

            # Get all connected entities
            rows = self.conn.execute("""
                SELECT r.relation, r.target_id, e.name
                FROM relationships r
                JOIN entities e ON r.target_id = e.id
                WHERE r.source_id = ?
                UNION
                SELECT r.relation, r.source_id, e.name
                FROM relationships r
                JOIN entities e ON r.source_id = e.id
                WHERE r.target_id = ?
            """, (current_id, current_id)).fetchall()

            for row in rows:
                next_id = row[1]
                if next_id not in visited:
                    queue.append((next_id, path + [(row[2], row[0])]))

        return None  # No path found

    def find_related(self, entity_name, depth=2):
        """Find all entities within N hops. Returns a subgraph."""
        entity_id = self._get_entity_id(entity_name)
        if not entity_id:
            return {'entities': [], 'relationships': []}

        visited = set()
        entities = []
        relationships = []
        queue = [(entity_id, 0)]

        while queue:
            current_id, current_depth = queue.pop(0)
            if current_id in visited or current_depth > depth:
                continue
            visited.add(current_id)

            # Get entity info
            row = self.conn.execute(
                "SELECT * FROM entities WHERE id = ?", (current_id,)
            ).fetchone()
            if row:
                entities.append(dict(row))

            # Get relationships
            rows = self.conn.execute("""
                SELECT r.*, e1.name as source_name, e2.name as target_name
                FROM relationships r
                JOIN entities e1 ON r.source_id = e1.id
                JOIN entities e2 ON r.target_id = e2.id
                WHERE r.source_id = ? OR r.target_id = ?
            """, (current_id, current_id)).fetchall()

            for rel in rows:
                relationships.append(dict(rel))
                next_id = rel['target_id'] if rel['source_id'] == current_id else rel['source_id']
                if next_id not in visited:
                    queue.append((next_id, current_depth + 1))

        return {'entities': entities, 'relationships': relationships}

    # ─── OBSERVATIONS ──────────────────────────────────────────

    def add_observation(self, entity_name, content, source='conversation'):
        """Add a timestamped observation about an entity."""
        entity_id = self._get_entity_id(entity_name)
        if not entity_id:
            return False

        self.conn.execute("""
            INSERT INTO observations (entity_id, content, observed_at, source)
            VALUES (?, ?, ?, ?)
        """, (entity_id, content, datetime.now().isoformat(), source))
        self.conn.commit()
        return True

    def get_observations(self, entity_name, limit=10):
        """Get recent observations about an entity."""
        entity_id = self._get_entity_id(entity_name)
        if not entity_id:
            return []

        rows = self.conn.execute("""
            SELECT * FROM observations
            WHERE entity_id = ?
            ORDER BY observed_at DESC
            LIMIT ?
        """, (entity_id, limit)).fetchall()

        return [dict(r) for r in rows]

    # ─── STATS ─────────────────────────────────────────────────

    def get_stats(self):
        """Get graph statistics."""
        entities = self.conn.execute("SELECT COUNT(*) as c FROM entities").fetchone()['c']
        relationships = self.conn.execute("SELECT COUNT(*) as c FROM relationships").fetchone()['c']
        observations = self.conn.execute("SELECT COUNT(*) as c FROM observations").fetchone()['c']

        types = self.conn.execute(
            "SELECT entity_type, COUNT(*) as c FROM entities GROUP BY entity_type"
        ).fetchall()

        return {
            'total_entities': entities,
            'total_relationships': relationships,
            'total_observations': observations,
            'entity_types': {r['entity_type']: r['c'] for r in types},
        }

    def close(self):
        """Close the database connection."""
        self.conn.close()


def seed_initial_graph(graph):
    """Seed the graph with Kit's existing knowledge from MEMORY.md."""

    # ─── People ───
    graph.add_entity('the user', 'person', {
        'role': 'Outside Sales Rep + AI Strategist',
        'location': 'Wichita, KS',
        'interests': ['AI', 'local LLMs', 'options trading', 'Blade Runner', 'FF7'],
    })
    graph.add_entity('Gary', 'person', {'role': 'IT Director', 'stance': 'champion'})
    graph.add_entity('Adam', 'person', {'role': 'IT Architect', 'stance': 'resistant'})
    graph.add_entity('Kit', 'agent', {'model': 'qwen3.5:9b / claude-opus-4', 'platform': 'OpenClaw'})

    # ─── Companies ───
    graph.add_entity('Example Corp', 'company', {'industry': 'steel distribution', 'type': 'Fortune 500'})
    graph.add_entity('Parent Corp', 'company', {'note': 'heavy gauge group'})
    graph.add_entity('AIProvider', 'company', {'products': ['Claude', 'Opus', 'Sonnet']})
    graph.add_entity('OpenAI', 'company', {'products': ['GPT', 'ChatGPT']})
    graph.add_entity('xAI', 'company', {'products': ['Grok']})

    # ─── Projects ───
    graph.add_entity('UserProject', 'project', {
        'type': 'AI mobile command center',
        'tech': 'Next.js, Supabase, Claude API',
        'status': 'paused',
    })
    graph.add_entity('Kit R2 Evolution', 'project', {'status': 'active', 'priority': 1})
    graph.add_entity('TradingProject', 'project', {'type': 'options flow agent', 'balance': '$2000 paper'})
    graph.add_entity('ResearchProject', 'project', {'status': 'published', 'repo': 'YOUR_GITHUB_USERNAME/Agent-Memory-Harness'})

    # ─── Tools/Concepts ───
    graph.add_entity('CompetitorTool', 'tool', {'vendor': 'CRMPlatform', 'type': 'AI agent platform'})
    graph.add_entity('CRMPlatform', 'tool', {'type': 'CRM'})
    graph.add_entity('OpenClaw', 'tool', {'type': 'AI agent gateway', 'version': '2026.3.8'})
    graph.add_entity('JARVIS', 'hardware', {'specs': 'Ryzen 9 3950X, RTX 5070 12GB, 48GB DDR4'})
    graph.add_entity('Unusual Whales', 'tool', {'type': 'options flow data'})

    # ─── Relationships ───
    graph.add_relationship('the user', 'works_at', 'Example Corp')
    graph.add_relationship('Example Corp', 'restructured_under', 'Parent Corp')
    graph.add_relationship('the user', 'built', 'UserProject')
    graph.add_relationship('the user', 'operates', 'Kit')
    graph.add_relationship('the user', 'built', 'ResearchProject')
    graph.add_relationship('the user', 'building', 'TradingProject')
    graph.add_relationship('Kit', 'runs_on', 'JARVIS')
    graph.add_relationship('Kit', 'powered_by', 'OpenClaw')
    graph.add_relationship('Kit', 'evolving_into', 'Kit R2 Evolution')

    graph.add_relationship('UserProject', 'pitched_to', 'Gary')
    graph.add_relationship('Gary', 'champions', 'UserProject')
    graph.add_relationship('Adam', 'opposes', 'UserProject')
    graph.add_relationship('Adam', 'pushes', 'CompetitorTool')
    graph.add_relationship('CompetitorTool', 'competes_with', 'UserProject')
    graph.add_relationship('UserProject', 'feeds_data_to', 'CRMPlatform')
    graph.add_relationship('UserProject', 'uses_api', 'AIProvider')

    graph.add_relationship('TradingProject', 'uses_data', 'Unusual Whales')
    graph.add_relationship('TradingProject', 'runs_under', 'Kit')
    graph.add_relationship('ResearchProject', 'runs_on', 'JARVIS')

    graph.add_relationship('AIProvider', 'competes_with', 'OpenAI')
    graph.add_relationship('xAI', 'competes_with', 'OpenAI')
    graph.add_relationship('OpenAI', 'acquired_creator_of', 'OpenClaw')

    # ─── Observations ───
    graph.add_observation('UserProject', 'Pitched to IT Feb 21, 2026 — received extremely well')
    graph.add_observation('UserProject', 'Website down per corporate request — tool still works')
    graph.add_observation('Gary', 'Wants to escalate Prospectus to CIO')
    graph.add_observation('Adam', 'Pushed AgentForce twice, feels threatened by Prospectus')
    graph.add_observation('the user', 'Active in AI community')
    graph.add_observation('the user', 'Industry engagement with published work')
    graph.add_observation('TradingProject', 'Layer 1 substantially complete — 17 Python modules')
    graph.add_observation('Kit', 'Daemon v1 deployed with 9 async loops, zero API cost')

    stats = graph.get_stats()
    logger.info(f"Graph seeded: {stats['total_entities']} entities, "
                f"{stats['total_relationships']} relationships, "
                f"{stats['total_observations']} observations")

    return stats

