"""B7: Postgres-backed persistence for task graphs."""

import json
from typing import Dict, List, Optional
import asyncpg
import logging

from .task_graph import TaskGraph, PersistenceBackend


logger = logging.getLogger(__name__)


class PostgresPersistence(PersistenceBackend):
    """Task graph persistence in Postgres."""
    
    def __init__(self, db_url: str):
        """
        Args:
            db_url: asyncpg connection string (e.g. 'postgresql://user:pass@localhost/dbname')
        """
        self.db_url = db_url
        self.pool: Optional[asyncpg.pool.Pool] = None
    
    async def init(self) -> None:
        """Initialize connection pool and create schema."""
        self.pool = await asyncpg.create_pool(
            self.db_url,
            min_size=2,
            max_size=10,
            command_timeout=60,
        )
        
        # Create tables if they don't exist
        async with self.pool.acquire() as conn:
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                goal TEXT NOT NULL,
                purpose TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'planning',
                graph_json JSONB NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                started_at TIMESTAMPTZ,
                completed_at TIMESTAMPTZ,
                indexed_at TIMESTAMPTZ
            );
            
            CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks(user_id);
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC);
            """)
            
            logger.info("Postgres schema initialized for tasks")
    
    async def close(self) -> None:
        """Close connection pool."""
        if self.pool:
            await self.pool.close()
    
    async def save(self, task: TaskGraph) -> None:
        """Persist task graph (upsert)."""
        if not self.pool:
            raise RuntimeError("Pool not initialized; call init() first")
        
        task_dict = task.to_dict()
        
        async with self.pool.acquire() as conn:
            await conn.execute("""
            INSERT INTO tasks (task_id, user_id, goal, purpose, status, graph_json, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, NOW())
            ON CONFLICT (task_id)
            DO UPDATE SET
                status = EXCLUDED.status,
                graph_json = EXCLUDED.graph_json,
                updated_at = NOW()
            """,
            task.task_id,
            task.user_id,
            task.goal,
            task.purpose,
            task.status.value,
            json.dumps(task_dict),
            )
    
    async def load(self, task_id: str) -> Optional[TaskGraph]:
        """Load task graph from storage."""
        if not self.pool:
            raise RuntimeError("Pool not initialized; call init() first")
        
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT graph_json FROM tasks WHERE task_id = $1",
                task_id
            )
        
        if not row:
            return None
        
        task_dict = json.loads(row['graph_json'])
        return TaskGraph.from_dict(task_dict)
    
    async def create(self, task: TaskGraph) -> None:
        """Create a new task in storage."""
        if not self.pool:
            raise RuntimeError("Pool not initialized; call init() first")
        
        task_dict = task.to_dict()
        
        async with self.pool.acquire() as conn:
            await conn.execute("""
            INSERT INTO tasks (task_id, user_id, goal, purpose, status, graph_json, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, NOW(), NOW())
            """,
            task.task_id,
            task.user_id,
            task.goal,
            task.purpose,
            task.status.value,
            json.dumps(task_dict),
            )
    
    async def list_tasks(self, user_id: str, limit: int = 10) -> List[TaskGraph]:
        """List recent tasks for a user."""
        if not self.pool:
            raise RuntimeError("Pool not initialized; call init() first")
        
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
            SELECT graph_json FROM tasks
            WHERE user_id = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            user_id,
            limit,
            )
        
        tasks = []
        for row in rows:
            task_dict = json.loads(row['graph_json'])
            tasks.append(TaskGraph.from_dict(task_dict))
        
        return tasks
