"""
Database CRUD Operations
"""
import json
import sqlite3
import uuid
from datetime import datetime, UTC
from typing import Any

from .connection import DatabaseManager


class SongCRUD:
    """CRUD operations for songs."""
    
    def __init__(self, db: DatabaseManager):
        self.db = db
    
    async def get_or_create_by_yt_id(
        self,
        canonical_yt_id: str,
        title: str,
        artist_name: str,
        album: str | None = None,
        release_year: int | None = None,
        duration_seconds: int | None = None,
        spotify_id: str | None = None,
        is_ephemeral: bool = False,
    ) -> dict:
        """Get existing song by YT ID or create new one."""
        existing = await self.db.fetch_one(
            "SELECT * FROM songs WHERE canonical_yt_id = ?",
            (canonical_yt_id,)
        )
        if existing:
            # Update missing metadata if provided
            updates = []
            params = []
            if album and not existing.get("album"):
                updates.append("album = ?")
                params.append(album)
            if release_year and not existing.get("release_year"):
                updates.append("release_year = ?")
                params.append(release_year)
            if duration_seconds and not existing.get("duration_seconds"):
                updates.append("duration_seconds = ?")
                params.append(duration_seconds)
            if spotify_id and not existing.get("spotify_id"):
                updates.append("spotify_id = ?")
                params.append(spotify_id)
            
            if updates:
                params.append(existing["id"])
                await self.db.execute(
                    f"UPDATE songs SET {', '.join(updates)} WHERE id = ?",
                    tuple(params)
                )
                return await self.db.fetch_one(
                    "SELECT * FROM songs WHERE id = ?",
                    (existing["id"],)
                )
            return existing
        
        await self.db.execute(
            """INSERT INTO songs 
               (canonical_yt_id, title, artist_name, album, release_year, duration_seconds, spotify_id, is_ephemeral)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (canonical_yt_id, title, artist_name, album, release_year, duration_seconds, spotify_id, is_ephemeral)
        )
        return await self.db.fetch_one(
            "SELECT * FROM songs WHERE canonical_yt_id = ?",
            (canonical_yt_id,)
        )

    async def make_permanent(self, song_id: int) -> None:
        """Mark a song as permanent (not ephemeral)."""
        await self.db.execute(
            "UPDATE songs SET is_ephemeral = 0 WHERE id = ?",
            (song_id,)
        )
    
    async def get_by_yt_id(self, canonical_yt_id: str) -> dict | None:
        """Get existing song by YT ID without creating."""
        return await self.db.fetch_one(
            "SELECT * FROM songs WHERE canonical_yt_id = ?",
            (canonical_yt_id,)
        )

    async def get_genres(self, song_id: int) -> list[str]:
        """Get genres for a song."""
        rows = await self.db.fetch_all(
            "SELECT genre FROM song_genres WHERE song_id = ?",
            (song_id,)
        )
        return [row["genre"] for row in rows]

    async def get_by_id(self, song_id: int) -> dict | None:
        """Get song by ID."""
        return await self.db.fetch_one("SELECT * FROM songs WHERE id = ?", (song_id,))

    async def get_or_create_by_spotify_id(
        self,
        spotify_id: str,
        title: str,
        artist_name: str,
        album: str = None,
        release_year: int = None,
        duration_seconds: int = None
    ) -> dict:
        """Get existing song by Spotify ID or create a placeholder."""
        existing = await self.db.fetch_one("SELECT * FROM songs WHERE spotify_id = ?", (spotify_id,))
        if existing:
            return existing
        
        # Create with placeholder YT ID
        placeholder_yt = f"spotify:{spotify_id}"
        return await self.get_or_create_by_yt_id(
            canonical_yt_id=placeholder_yt,
            title=title,
            artist_name=artist_name,
            album=album,
            release_year=release_year,
            duration_seconds=duration_seconds,
            spotify_id=spotify_id,
            is_ephemeral=True
        )
    
    async def add_genre(self, song_id: int, genre: str, source: str = "unknown") -> None:
        """Add a genre to a song."""
        try:
            await self.db.execute(
                "INSERT OR IGNORE INTO song_genres (song_id, genre) VALUES (?, ?)",
                (song_id, genre.lower())
            )
        except Exception:
            pass

    async def clear_genres(self, song_id: int) -> None:
        """Clear all genres for a song."""
        await self.db.execute("DELETE FROM song_genres WHERE song_id = ?", (song_id,))
    
    
    async def get_genres(self, song_id: int) -> list[str]:
        """Get all genres for a song."""
        rows = await self.db.fetch_all(
            "SELECT genre FROM song_genres WHERE song_id = ?",
            (song_id,)
        )
        return [row["genre"] for row in rows]


class UserCRUD:
    """CRUD operations for users."""
    
    def __init__(self, db: DatabaseManager):
        self.db = db
    
    async def get_or_create(self, user_id: int, username: str | None = None) -> dict:
        """Get existing user or create new one."""
        existing = await self.db.fetch_one(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        )
        if existing:
            # Update last active
            await self.db.execute(
                "UPDATE users SET last_active = ?, username = COALESCE(?, username) WHERE id = ?",
                (datetime.now(UTC), username, user_id)
            )
            return existing
        
        await self.db.execute(
            "INSERT INTO users (id, username, last_active) VALUES (?, ?, ?)",
            (user_id, username, datetime.now(UTC))
        )
        return await self.db.fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))
    
    async def set_opt_out(self, user_id: int, opted_out: bool) -> None:
        """Set user opt-out status for preference tracking."""
        await self.db.execute(
            "UPDATE users SET opted_out = ? WHERE id = ?",
            (opted_out, user_id)
        )
    
    async def is_opted_out(self, user_id: int) -> bool:
        """Check if user has opted out of preference tracking."""
        row = await self.db.fetch_one(
            "SELECT opted_out FROM users WHERE id = ?", (user_id,)
        )
        return row["opted_out"] if row else False
    
    async def delete_all_data(self, user_id: int) -> None:
        """Delete all user data (GDPR compliance)."""
        await self.db.execute("DELETE FROM user_preferences WHERE user_id = ?", (user_id,))
        await self.db.execute("DELETE FROM song_reactions WHERE user_id = ?", (user_id,))
        await self.db.execute("DELETE FROM imported_playlists WHERE user_id = ?", (user_id,))
        await self.db.execute("DELETE FROM session_listeners WHERE user_id = ?", (user_id,))
        await self.db.execute("DELETE FROM users WHERE id = ?", (user_id,))


class GuildCRUD:
    """CRUD operations for guilds."""
    
    def __init__(self, db: DatabaseManager):
        self.db = db
    
    async def get_or_create(self, guild_id: int, name: str | None = None) -> dict:
        """Get existing guild or create new one."""
        existing = await self.db.fetch_one(
            "SELECT * FROM guilds WHERE id = ?", (guild_id,)
        )
        if existing:
            if name and existing.get("name") != name:
                await self.db.execute(
                    "UPDATE guilds SET name = ? WHERE id = ?",
                    (name, guild_id)
                )
            return existing
        
        await self.db.execute(
            "INSERT INTO guilds (id, name) VALUES (?, ?)",
            (guild_id, name)
        )
        return await self.db.fetch_one("SELECT * FROM guilds WHERE id = ?", (guild_id,))
    
    async def get_setting(self, guild_id: int, key: str) -> Any | None:
        """Get a guild setting value."""
        row = await self.db.fetch_one(
            "SELECT setting_value FROM guild_settings WHERE guild_id = ? AND setting_key = ?",
            (guild_id, key)
        )
        if row and row["setting_value"]:
            try:
                return json.loads(row["setting_value"])
            except json.JSONDecodeError:
                return row["setting_value"]
        return None
    
    async def set_setting(self, guild_id: int, key: str, value: Any) -> None:
        """Set a guild setting value."""
        value_str = json.dumps(value) if not isinstance(value, str) else value
        await self.db.execute(
            """INSERT INTO guild_settings (guild_id, setting_key, setting_value)
               VALUES (?, ?, ?)
               ON CONFLICT(guild_id, setting_key) DO UPDATE SET setting_value = ?""",
            (guild_id, key, value_str, value_str)
        )
    
    async def get_all_settings(self, guild_id: int) -> dict[str, Any]:
        """Get all settings for a guild."""
        rows = await self.db.fetch_all(
            "SELECT setting_key, setting_value FROM guild_settings WHERE guild_id = ?",
            (guild_id,)
        )
        result = {}
        for row in rows:
            try:
                result[row["setting_key"]] = json.loads(row["setting_value"])
            except (json.JSONDecodeError, TypeError):
                result[row["setting_key"]] = row["setting_value"]
        return result


class PlaybackCRUD:
    """CRUD operations for playback sessions and history."""
    
    def __init__(self, db: DatabaseManager):
        self.db = db
    
    async def create_session(self, guild_id: int, channel_id: int) -> str:
        """Create a new playback session."""
        session_id = str(uuid.uuid4())
        await self.db.execute(
            "INSERT INTO playback_sessions (id, guild_id, channel_id) VALUES (?, ?, ?)",
            (session_id, guild_id, channel_id)
        )
        return session_id
    
    async def end_session(self, session_id: str) -> None:
        """End a playback session."""
        await self.db.execute(
            "UPDATE playback_sessions SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
            (datetime.now(UTC), session_id)
        )

    async def update_session_message(self, session_id: str, message_id: int | None) -> None:
        """Update the last message ID for a session."""
        msg_id_str = str(message_id) if message_id else None
        await self.db.execute(
            "UPDATE playback_sessions SET last_message_id = ? WHERE id = ?",
            (msg_id_str, session_id)
        )

    async def get_stale_sessions(self) -> list[dict]:
        """Get sessions that haven't been ended."""
        return await self.db.fetch_all(
            "SELECT * FROM playback_sessions WHERE ended_at IS NULL"
        )
    
    async def add_listener(self, session_id: str, user_id: int) -> None:
        """Add a listener to a session."""
        await self.db.execute(
            "INSERT INTO session_listeners (session_id, user_id) VALUES (?, ?)",
            (session_id, user_id)
        )
    
    async def remove_listener(self, session_id: str, user_id: int) -> None:
        """Mark a listener as having left the session."""
        await self.db.execute(
            """UPDATE session_listeners SET left_at = ? 
               WHERE session_id = ? AND user_id = ? AND left_at IS NULL""",
            (datetime.now(UTC), session_id, user_id)
        )
    
    async def log_track(
        self,
        session_id: str,
        song_id: int,
        discovery_source: str = "user_request",
        discovery_reason: str | None = None,
        for_user_id: int | None = None,
    ) -> int:
        """Log a track being played."""
        cursor = await self.db.execute(
            """INSERT INTO playback_history 
               (session_id, song_id, discovery_source, discovery_reason, for_user_id)
               VALUES (?, ?, ?, ?, ?)""",
            (session_id, song_id, discovery_source, discovery_reason, for_user_id)
        )
        return cursor.lastrowid
    
    async def mark_completed(self, history_id: int, completed: bool, skip_reason: str | None = None) -> None:
        """Mark a track as completed or skipped."""
        await self.db.execute(
            "UPDATE playback_history SET completed = ?, skip_reason = ? WHERE id = ?",
            (completed, skip_reason, history_id)
        )
    
    async def get_recent_history(self, guild_id: int, limit: int = 30) -> list[dict]:
        """Get recent playback history for a guild by count."""
        return await self.db.fetch_all(
            """SELECT ph.*, s.canonical_yt_id, s.title, s.artist_name
               FROM playback_history ph
               JOIN playback_sessions ps ON ph.session_id = ps.id
               JOIN songs s ON ph.song_id = s.id
               WHERE ps.guild_id = ?
               ORDER BY ph.played_at DESC
               LIMIT ?""",
            (guild_id, limit)
        )
        
    async def get_recent_history_window(self, guild_id: int, seconds: int) -> list[str]:
        """Get list of YouTube IDs played in the last N seconds for a guild."""
        query = """
            SELECT s.canonical_yt_id
            FROM playback_history ph
            JOIN playback_sessions ps ON ph.session_id = ps.id
            JOIN songs s ON ph.song_id = s.id
            WHERE ps.guild_id = ? 
            AND ph.played_at > datetime('now', ?)
        """
        modifier = f"-{seconds} seconds"
        rows = await self.db.fetch_all(query, (guild_id, modifier))
        return [row["canonical_yt_id"] for row in rows]


class PreferenceCRUD:
    """CRUD operations for user preferences."""
    
    def __init__(self, db: DatabaseManager):
        self.db = db
    
    async def get_preference(
        self, user_id: int, preference_type: str, preference_key: str
    ) -> float:
        """Get a specific preference score."""
        row = await self.db.fetch_one(
            """SELECT affinity_score FROM user_preferences 
               WHERE user_id = ? AND preference_type = ? AND preference_key = ?""",
            (user_id, preference_type, preference_key.lower())
        )
        return row["affinity_score"] if row else 0.0
    
    async def update_preference(
        self, user_id: int, preference_type: str, preference_key: str, score: float
    ) -> None:
        """Update or create a preference."""
        await self.db.execute(
            """INSERT INTO user_preferences (user_id, preference_type, preference_key, affinity_score, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id, preference_type, preference_key) 
               DO UPDATE SET affinity_score = ?, updated_at = ?""",
            (user_id, preference_type, preference_key.lower(), score, datetime.now(UTC), score, datetime.now(UTC))
        )
    
    async def get_all_preferences(self, user_id: int) -> dict[str, dict[str, float]]:
        """Get all preferences for a user, grouped by type."""
        rows = await self.db.fetch_all(
            """SELECT preference_type, preference_key, affinity_score 
               FROM user_preferences WHERE user_id = ? ORDER BY affinity_score DESC""",
            (user_id,)
        )
        result: dict[str, dict[str, float]] = {}
        for row in rows:
            ptype = row["preference_type"]
            if ptype not in result:
                result[ptype] = {}
            result[ptype][row["preference_key"]] = row["affinity_score"]
        return result
    
    async def get_top_preferences(
        self, user_id: int, preference_type: str, limit: int = 5
    ) -> list[tuple[str, float]]:
        """Get top preferences of a specific type."""
        rows = await self.db.fetch_all(
            """SELECT preference_key, affinity_score FROM user_preferences 
               WHERE user_id = ? AND preference_type = ? 
               ORDER BY affinity_score DESC LIMIT ?""",
            (user_id, preference_type, limit)
        )
        return [(row["preference_key"], row["affinity_score"]) for row in rows]
    
    async def clear_preferences(self, user_id: int) -> None:
        """Clear all preferences for a user."""
        await self.db.execute(
            "DELETE FROM user_preferences WHERE user_id = ?",
            (user_id,)
        )
    
    async def export_all(self, user_id: int) -> dict:
        """Export all user data."""
        user = await self.db.fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))
        preferences = await self.get_all_preferences(user_id)
        reactions = await self.db.fetch_all(
            """SELECT s.title, s.artist_name, sr.reaction, sr.created_at
               FROM song_reactions sr
               JOIN songs s ON sr.song_id = s.id
               WHERE sr.user_id = ?""",
            (user_id,)
        )
        playlists = await self.db.fetch_all(
            "SELECT * FROM imported_playlists WHERE user_id = ?",
            (user_id,)
        )
        
        return {
            "user": user,
            "preferences": preferences,
            "reactions": [dict(r) for r in reactions],
            "imported_playlists": [dict(p) for p in playlists],
        }


class ReactionCRUD:
    """CRUD operations for song reactions."""
    
    def __init__(self, db: DatabaseManager):
        self.db = db
    
    async def add_reaction(self, user_id: int, song_id: int, reaction: str) -> None:
        """Add or update a reaction."""
        await self.db.execute(
            """INSERT INTO song_reactions (user_id, song_id, reaction)
               VALUES (?, ?, ?)
               ON CONFLICT(user_id, song_id) DO UPDATE SET reaction = ?, created_at = ?""",
            (user_id, song_id, reaction, reaction, datetime.now(UTC))
        )
    
    async def get_reaction(self, user_id: int, song_id: int) -> str | None:
        """Get user's reaction to a song."""
        row = await self.db.fetch_one(
            "SELECT reaction FROM song_reactions WHERE user_id = ? AND song_id = ?",
            (user_id, song_id)
        )
        return row["reaction"] if row else None
    
    async def get_liked_songs(self, user_id: int, limit: int = 50) -> list[dict]:
        """Get user's liked songs."""
        return await self.db.fetch_all(
            """SELECT s.* FROM songs s
               JOIN song_reactions sr ON s.id = sr.song_id
               WHERE sr.user_id = ? AND sr.reaction IN ('like', 'love')
               ORDER BY sr.created_at DESC
               LIMIT ?""",
            (user_id, limit)
        )


class SystemCRUD:
    """CRUD operations for system settings and notifications."""
    
    def __init__(self, db: DatabaseManager):
        self.db = db

    async def get_playlist_import_count(self, user_id: int) -> int:
        """Get number of playlists imported by user."""
        row = await self.db.fetch_one("SELECT COUNT(*) as count FROM imported_playlists WHERE user_id = ?", (user_id,))
        return row["count"] if row else 0

    async def get_global_setting(self, key: str) -> Any | None:
        """Get a global setting value."""
        row = await self.db.fetch_one(
            "SELECT setting_value FROM global_settings WHERE setting_key = ?", (key,)
        )
        if row:
            try:
                return json.loads(row["setting_value"])
            except json.JSONDecodeError:
                return row["setting_value"]
        return None
    
    async def set_global_setting(self, key: str, value: Any) -> None:
        """Set a global setting value."""
        value_str = json.dumps(value) if not isinstance(value, str) else value
        await self.db.execute(
            """INSERT INTO global_settings (setting_key, setting_value, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(setting_key) DO UPDATE SET setting_value = ?, updated_at = ?""",
            (key, value_str, datetime.now(UTC), value_str, datetime.now(UTC))
        )
            
    async def add_notification(self, level: str, message: str) -> None:
        """Add a notification."""
        await self.db.execute(
            "INSERT INTO notifications (level, message) VALUES (?, ?)",
            (level, message)
        )
            
    async def get_recent_notifications(self, limit: int = 20) -> list[dict]:
        """Get recent notifications."""
        return await self.db.fetch_all(
            "SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?", (limit,)
        )
    
    async def mark_read(self, notification_id: int) -> None:
        """Mark a notification as read."""
        await self.db.execute(
            "UPDATE notifications SET read = 1 WHERE id = ?", (notification_id,)
        )


class AnalyticsCRUD:
    def __init__(self, db: DatabaseManager):
        self.db = db

    async def get_top_songs(self, limit: int = 10, guild_id: int = None) -> list[dict]:
        """Get most played songs, optionally filtered by user affinity in a guild."""
        # Note: We filter by guild_id via playback_history sessions
        params = []
        where_clause = ""
        
        if guild_id:
            where_clause = "WHERE ps.guild_id = ?"
            params.append(guild_id)
            
        query = f"""
            SELECT 
                s.title, 
                s.canonical_yt_id as yt_id,
                s.artist_name as artist, 
                COUNT(*) as plays,
                (SELECT COUNT(*) FROM song_reactions r WHERE r.song_id = s.id AND r.reaction = 'like') as likes
            FROM playback_history ph
            JOIN songs s ON ph.song_id = s.id
            JOIN playback_sessions ps ON ph.session_id = ps.id
            {where_clause}
            GROUP BY s.id
            ORDER BY plays DESC
            LIMIT ?
        """
        params.append(limit)
        
        return await self.db.fetch_all(query, tuple(params))

    async def get_top_users(self, limit: int = 10, guild_id: int = None) -> list[dict]:
        """Get most active users based on weighted activity score."""
        params = []
        where_clause = ""
        
        if guild_id:
            where_clause = "WHERE ps.guild_id = ?"
            params.append(guild_id)

        # Weighted Score: plays*2 + reactions*3 + imports*5
        query = f"""
            SELECT 
                u.id,
                u.username,
                COUNT(DISTINCT ph.id) as plays,
                (SELECT COUNT(*) FROM song_reactions r WHERE r.user_id = u.id) as reactions,
                (SELECT COUNT(*) FROM imported_playlists ip WHERE ip.user_id = u.id) as playlists,
                (COUNT(DISTINCT ph.id) * 2 + 
                 (SELECT COUNT(*) FROM song_reactions r WHERE r.user_id = u.id) * 3 +
                 (SELECT COUNT(*) FROM imported_playlists ip WHERE ip.user_id = u.id) * 5) as score
            FROM users u
            LEFT JOIN playback_history ph ON ph.for_user_id = u.id
            LEFT JOIN playback_sessions ps ON ph.session_id = ps.id
            {where_clause}
            GROUP BY u.id
            ORDER BY score DESC
            LIMIT ?
        """
        params.append(limit)
        return await self.db.fetch_all(query, tuple(params))

    async def get_total_stats(self, guild_id: int = None) -> dict:
        """Get total statistics (songs, users, plays)."""
        params = []
        where_clause_plays = ""
        where_clause_users = ""
        
        if guild_id:
            where_clause_plays = "WHERE ps.guild_id = ?"
            where_clause_users = "JOIN playback_history ph ON ph.for_user_id = u.id JOIN playback_sessions ps ON ph.session_id = ps.id WHERE ps.guild_id = ?"
            params.append(guild_id)

        # Total Plays
        query_plays = f"""
            SELECT COUNT(*) as count 
            FROM playback_history ph
            JOIN playback_sessions ps ON ph.session_id = ps.id
            {where_clause_plays}
        """
        plays_row = await self.db.fetch_one(query_plays, tuple(params) if guild_id else ())
        total_plays = plays_row["count"] if plays_row else 0

        # Total Songs (unique songs played)
        query_songs = f"""
            SELECT COUNT(DISTINCT song_id) as count 
            FROM playback_history ph
            JOIN playback_sessions ps ON ph.session_id = ps.id
            {where_clause_plays}
        """
        songs_row = await self.db.fetch_one(query_songs, tuple(params) if guild_id else ())
        total_songs = songs_row["count"] if songs_row else 0

        # Total Users
        if guild_id:
            query_users = f"""
                SELECT COUNT(DISTINCT u.id) as count
                FROM users u
                {where_clause_users}
            """
            users_row = await self.db.fetch_one(query_users, tuple(params))
        else:
             query_users = "SELECT COUNT(*) as count FROM users"
             users_row = await self.db.fetch_one(query_users)
        
        total_users = users_row["count"] if users_row else 0

        return {
            "total_plays": total_plays,
            "total_songs": total_songs,
            "total_users": total_users
        }

    async def get_top_liked_songs(self, limit: int = 5) -> list[dict]:
        """Get songs with most likes."""
        query = """
            SELECT s.title, s.artist_name as artist, COUNT(r.song_id) as likes
            FROM songs s
            JOIN song_reactions r ON s.id = r.song_id
            WHERE r.reaction = 'like'
            GROUP BY s.id
            ORDER BY likes DESC
            LIMIT ?
        """
        return await self.db.fetch_all(query, (limit,))

    async def get_top_liked_artists(self, limit: int = 5) -> list[dict]:
        """Get artists with most likes."""
        query = """
            SELECT s.artist_name as name, COUNT(r.song_id) as likes
            FROM songs s
            JOIN song_reactions r ON s.id = r.song_id
            WHERE r.reaction = 'like'
            GROUP BY s.artist_name
            ORDER BY likes DESC
            LIMIT ?
        """
        return await self.db.fetch_all(query, (limit,))

    async def get_top_liked_genres(self, limit: int = 5) -> list[dict]:
        """Get genres with most likes."""
        query = """
            SELECT g.genre as name, COUNT(r.song_id) as likes
            FROM song_genres g
            JOIN song_reactions r ON g.song_id = r.song_id
            WHERE r.reaction = 'like'
            GROUP BY g.genre
            ORDER BY likes DESC
            LIMIT ?
        """
        return await self.db.fetch_all(query, (limit,))

    async def get_top_played_artists(self, limit: int = 5, guild_id: int = None) -> list[dict]:
        """Get artists with most plays."""
        params = []
        where_clause = ""
        if guild_id:
            where_clause = "WHERE ps.guild_id = ?"
            params.append(guild_id)
            
        query = f"""
            SELECT s.artist_name as name, COUNT(ph.id) as plays
            FROM songs s
            JOIN playback_history ph ON s.id = ph.song_id
            JOIN playback_sessions ps ON ph.session_id = ps.id
            {where_clause}
            GROUP BY s.artist_name
            ORDER BY plays DESC
            LIMIT ?
        """
        params.append(limit)
        return await self.db.fetch_all(query, tuple(params))

    async def get_top_played_genres(self, limit: int = 5, guild_id: int = None) -> list[dict]:
        """Get genres with most plays."""
        params = []
        where_clause = ""
        if guild_id:
            where_clause = "WHERE ps.guild_id = ?"
            params.append(guild_id)
            
        query = f"""
            SELECT g.genre as name, COUNT(ph.id) as plays
            FROM song_genres g
            JOIN playback_history ph ON g.song_id = ph.song_id
            JOIN playback_sessions ps ON ph.session_id = ps.id
            {where_clause}
            GROUP BY g.genre
            ORDER BY plays DESC
            LIMIT ?
        """
        params.append(limit)
        return await self.db.fetch_all(query, tuple(params))

    async def get_top_useful_users(self, limit: int = 5) -> list[dict]:
        """Get users whose requested songs are liked by others."""
        query = """
            SELECT u.username, COUNT(r.song_id) as score
            FROM users u
            JOIN playback_history ph ON ph.for_user_id = u.id
            JOIN song_reactions r ON ph.song_id = r.song_id
            WHERE ph.discovery_source = 'user_request' 
              AND r.reaction = 'like'
              AND r.user_id != u.id
            GROUP BY u.id
            ORDER BY score DESC
            LIMIT ?
        """
        return await self.db.fetch_all(query, (limit,))

    async def get_session_stats(self, session_id: str) -> dict:
        """Get summary statistics for a specific playback session."""
        stats = {}
        
        # 0. Session Timing (Real Time)
        query_time = """
            SELECT 
                started_at, 
                ended_at,
                last_message_id,
                channel_id,
                (SELECT COUNT(DISTINCT user_id) FROM session_listeners WHERE session_id = ?) as unique_listeners
            FROM playback_sessions 
            WHERE id = ?
        """
        session_info = await self.db.fetch_one(query_time, (session_id, session_id))
        
        real_duration = 0
        if session_info and session_info["started_at"] and session_info["ended_at"]:
            try:
                # Handle both string (SQLite) and datetime objects
                s_at = session_info["started_at"]
                e_at = session_info["ended_at"]
                
                # Convert strings to datetime
                if isinstance(s_at, str):
                    # SQLite stores as 'YYYY-MM-DD HH:MM:SS'
                    s_at = datetime.fromisoformat(s_at.replace(' ', 'T'))
                if isinstance(e_at, str):
                    e_at = datetime.fromisoformat(e_at.replace(' ', 'T'))
                
                # Ensure both are offset-aware for subtraction
                if s_at.tzinfo is None:
                    s_at = s_at.replace(tzinfo=UTC)
                if e_at.tzinfo is None:
                    e_at = e_at.replace(tzinfo=UTC)
                
                real_duration = int((e_at - s_at).total_seconds())
            except Exception as e:
                # Log error but keep 0
                import logging
                logging.getLogger(__name__).error(f"Error calculating session duration: {e}")
                pass
        
        # 1. Track stats
        query_tracks = """
            SELECT 
                COUNT(*) as total_tracks
            FROM playback_history ph
            WHERE ph.session_id = ?
        """
        tracks_info = await self.db.fetch_one(query_tracks, (session_id,))
        
        stats["total_tracks"] = tracks_info["total_tracks"] if tracks_info else 0
        stats["total_seconds"] = real_duration
        stats["unique_listeners"] = session_info["unique_listeners"] if session_info else 0
        stats["last_message_id"] = session_info["last_message_id"] if session_info else None
        stats["channel_id"] = session_info["channel_id"] if session_info else None
        
        # 2. Top Artist
        query_artist = """
            SELECT s.artist_name, COUNT(*) as count
            FROM playback_history ph
            JOIN songs s ON ph.song_id = s.id
            WHERE ph.session_id = ?
            GROUP BY s.artist_name
            ORDER BY count DESC
            LIMIT 1
        """
        artist = await self.db.fetch_one(query_artist, (session_id,))
        stats["top_artist"] = artist["artist_name"] if artist else None
        
        # 3. Top Genre
        query_genre = """
            SELECT sg.genre, COUNT(*) as count
            FROM playback_history ph
            JOIN song_genres sg ON ph.song_id = sg.song_id
            WHERE ph.session_id = ?
            GROUP BY sg.genre
            ORDER BY count DESC
            LIMIT 1
        """
        genre = await self.db.fetch_one(query_genre, (session_id,))
        stats["top_genre"] = genre["genre"].title() if genre else None
        
        # 4. Discovery Breakdown
        query_discovery = """
            SELECT discovery_source, COUNT(*) as count
            FROM playback_history ph
            WHERE ph.session_id = ?
            GROUP BY discovery_source
        """
        discovery = await self.db.fetch_all(query_discovery, (session_id,))
        stats["discovery_breakdown"] = {row["discovery_source"]: row["count"] for row in discovery}
        
        return stats


class LibraryCRUD:
    """CRUD operations for the unified song library."""
    
    def __init__(self, db: DatabaseManager):
        self.db = db
        
    async def add_to_library(self, user_id: int, song_id: int, source: str) -> None:
        """Add a song to a user's library with a specific source."""
        await self.db.execute(
            """INSERT OR IGNORE INTO song_library_entries (user_id, song_id, source)
               VALUES (?, ?, ?)""",
            (user_id, song_id, source)
        )
        
    async def get_library(self, guild_id: int = None, limit: int = 200) -> list[dict]:
        """Get the unified library of songs with contributors and sources."""
        # Note: Guild filtering is tricky because library is user-song, but we can filter
        # by songs that have been played in this guild or users that are in this guild.
        # For simplicity, we'll query all songs in the library.
        
        query = """
            SELECT 
                s.id,
                s.title,
                s.artist_name,
                (SELECT GROUP_CONCAT(DISTINCT sg.genre) FROM song_genres sg WHERE sg.song_id = s.id) as genre,
                GROUP_CONCAT(DISTINCT u.username) as contributors,
                GROUP_CONCAT(DISTINCT l.source) as sources,
                MAX(l.added_at) as last_added
            FROM songs s
            JOIN song_library_entries l ON s.id = l.song_id
            JOIN users u ON l.user_id = u.id
            GROUP BY s.id
            ORDER BY last_added DESC
            LIMIT ?
        """
        return await self.db.fetch_all(query, (limit,))
