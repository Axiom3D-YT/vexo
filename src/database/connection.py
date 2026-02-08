"""
Database Connection Manager - SQLite Async
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import aiosqlite

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Async SQLite database connection manager."""
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._connection: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()
    
    @classmethod
    async def create(cls, db_path: Path) -> "DatabaseManager":
        """Create and initialize the database manager."""
        manager = cls(db_path)
        await manager._init_db()
        return manager
    
    async def _init_db(self) -> None:
        """Initialize the database with schema."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        async with aiosqlite.connect(self.db_path) as db:
            # Enable WAL mode for better concurrency on slow storage (Pi 3)
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")
            
            # Read and execute schema
            schema_path = Path(__file__).parent / "migrations" / "init_schema.sql"
            if schema_path.exists():
                schema = schema_path.read_text()
                await db.executescript(schema)
                await db.commit()
                logger.info("Database schema initialized with WAL mode")
            else:
                logger.warning(f"Schema file not found: {schema_path}")
            
            # Automatic Migrations
            # 1. Add is_ephemeral to songs if missing
            try:
                await db.execute("SELECT is_ephemeral FROM songs LIMIT 1")
            except Exception:
                logger.info("Migrating: Adding is_ephemeral column to songs table")
                try:
                    await db.execute("ALTER TABLE songs ADD COLUMN is_ephemeral BOOLEAN DEFAULT 0")
                    await db.commit()
                except Exception as e:
                    logger.error(f"Migration failed: {e}")
            
            # 2. Update playback_history CHECK constraint
            try:
                # Check for old constraint by looking at the schema SQL
                cursor = await db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='playback_history'")
                row = await cursor.fetchone()
                if row and "'same_artist'" in row[0] or (row and "'library'" not in row[0]):
                    logger.info("Migrating: Updating playback_history constraints")
                    # SQLite doesn't support ALTER TABLE for constraints, must recreate
                    await db.execute("ALTER TABLE playback_history RENAME TO playback_history_old")
                    
                    # Create new table (schema from init_schema.sql will be applied if we re-run it, 
                    # but let's be explicit and safe here)
                    await db.execute("""
                        CREATE TABLE playback_history (
                            id INTEGER PRIMARY KEY,
                            session_id TEXT REFERENCES playback_sessions(id),
                            song_id INTEGER REFERENCES songs(id),
                            played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            completed BOOLEAN DEFAULT FALSE,
                            skip_reason TEXT CHECK(skip_reason IN ('user', 'vote', 'error') OR skip_reason IS NULL),
                            discovery_source TEXT CHECK(discovery_source IN ('user_request', 'similar', 'artist', 'wildcard', 'library')),
                            discovery_reason TEXT,
                            for_user_id INTEGER REFERENCES users(id)
                        )
                    """)
                    
                    # Copy data, mapping same_artist to artist
                    await db.execute("""
                        INSERT INTO playback_history (id, session_id, song_id, played_at, completed, skip_reason, discovery_source, discovery_reason, for_user_id)
                        SELECT id, session_id, song_id, played_at, completed, skip_reason, 
                               CASE WHEN discovery_source = 'same_artist' THEN 'artist' ELSE discovery_source END,
                               discovery_reason, for_user_id
                        FROM playback_history_old
                    """)
                    
                    await db.execute("DROP TABLE playback_history_old")
                    await db.commit()
                    logger.info("Database migration: playback_history updated successfully")
            except Exception as e:
                logger.error(f"Migration for playback_history failed: {e}")
                await db.rollback()
    
    @asynccontextmanager
    async def connection(self) -> AsyncGenerator[aiosqlite.Connection, None]:
        """Get a database connection."""
        # Removed global self._lock. sqlite3 (and aiosqlite) handles its own 
        # internal locking. Removing the asyncio.Lock prevents the dashboard 
        # from blocking the bot's commands during heavy reads.
        if self._connection is None:
            self._connection = await aiosqlite.connect(self.db_path)
            self._connection.row_factory = aiosqlite.Row
            # Enable persistent PRAGMAs
            await self._connection.execute("PRAGMA foreign_keys = ON")
            await self._connection.execute("PRAGMA journal_mode=WAL")
        
        try:
            yield self._connection
        except Exception:
            await self._connection.rollback()
            raise

    async def execute(self, query: str, params: tuple = ()) -> aiosqlite.Cursor:
        """Execute a query and return the cursor."""
        async with self.connection() as db:
            cursor = await db.execute(query, params)
            await db.commit()
            return cursor
    
    async def fetch_one(self, query: str, params: tuple = ()) -> dict | None:
        """Fetch a single row as a dictionary."""
        async with self.connection() as db:
            cursor = await db.execute(query, params)
            row = await cursor.fetchone()
            return dict(row) if row else None
    
    async def fetch_all(self, query: str, params: tuple = ()) -> list[dict]:
        """Fetch all rows as a list of dictionaries."""
        async with self.connection() as db:
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    
    async def close(self) -> None:
        """Close the database connection."""
        if self._connection:
            await self._connection.close()
            self._connection = None
            logger.info("Database connection closed")
