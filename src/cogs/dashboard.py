"""
Web Dashboard Cog - Modern analytics dashboard
Runs on localhost only - no authentication required
"""
import asyncio
import json
import logging
from collections import deque
from datetime import datetime, UTC
from pathlib import Path

from aiohttp import web

from discord.ext import commands

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent.parent / "web" / "static"
TEMPLATE_DIR = Path(__file__).parent.parent / "web" / "templates"


class WebSocketLogHandler(logging.Handler):
    """Log handler that broadcasts to WebSocket clients."""
    
    def __init__(self, ws_manager, loop):
        super().__init__()
        self.ws_manager = ws_manager
        self.loop = loop
        self._last_emit = 0
        self._count_this_second = 0
    
    def emit(self, record):
        try:
            log_entry = {
                "timestamp": record.created,
                "level": record.levelname,
                "message": record.getMessage(),
                "logger": record.name,
                "guild_id": getattr(record, "guild_id", None),
            }
            
            # Always add to recent history buffer
            self.ws_manager.record_locally(log_entry)
            
            # Rate limiting for broadcasting to active clients (Pi 3 optimization)
            if not self.ws_manager.clients:
                return

            import time
            now = time.time()
            if int(now) == int(self._last_emit):
                self._count_this_second += 1
            else:
                self._count_this_second = 1
                self._last_emit = now
            
            if self._count_this_second > 10:
                return # Drop log burst to prevent event loop starvation
                
            # Check if we're in the same loop
            try:
                current_loop = asyncio.get_running_loop()
            except RuntimeError:
                current_loop = None

            if current_loop == self.loop:
                asyncio.create_task(self.ws_manager.broadcast(log_entry))
            else:
                asyncio.run_coroutine_threadsafe(
                    self.ws_manager.broadcast(log_entry), 
                    self.loop
                )
        except Exception:
            pass


class WebSocketManager:
    """Manages WebSocket connections for live logs."""
    
    def __init__(self):
        self.clients: set[web.WebSocketResponse] = set()
        self.recent_logs: deque = deque(maxlen=1000)
    
    def record_locally(self, message: dict):
        """Add to the history buffer for new connections."""
        self.recent_logs.append(message)

    async def broadcast(self, message: dict):
        """Send message to all active clients."""
        disconnected = set()
        for ws in self.clients:
            try:
                await ws.send_json(message)
            except Exception:
                disconnected.add(ws)
        self.clients -= disconnected

    async def close_all(self):
        """Close all active websocket connections."""
        for ws in list(self.clients):
            try:
                await ws.close(code=1001, message=b"Server shutting down")
            except Exception:
                pass
        self.clients.clear()


class DashboardCog(commands.Cog):
    """Web dashboard for stats and analytics."""
    
    def __init__(self, bot: commands.Bot, host: str = "127.0.0.1", port: int = 8080):
        self.bot = bot
        self.host = host
        self.port = port
        self.app: web.Application | None = None
        self.runner: web.AppRunner | None = None
        self.ws_manager = WebSocketManager()
        self._log_handler: WebSocketLogHandler | None = None
    
    async def cog_load(self):
        self.app = web.Application()
        self._setup_routes()
        
        self._log_handler = WebSocketLogHandler(self.ws_manager, self.bot.loop)
        self._log_handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(self._log_handler)
        logger.info("Web dashboard log handler initialized and active.")
        
        # Periodic heartbeat log to verify dashboard connectivity
        self.bot.loop.create_task(self._heartbeat())
        
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, self.host, self.port)
        await site.start()
        
        logger.info(f"Dashboard at http://{self.host}:{self.port}")
    
    async def _heartbeat(self):
        """Periodic log entry to help verify the dashboard is receiving logs."""
        await asyncio.sleep(5) # Wait for bot to settle
        while True:
            logger.info("Dashboard Heartbeat: Bot is running and logging handler is active.")
            await asyncio.sleep(60) # Every minute
    
    async def cog_unload(self):
        if self._log_handler:
            logging.getLogger().removeHandler(self._log_handler)
        
        # Close websockets first
        await self.ws_manager.close_all()

        if self.runner:
            await self.runner.cleanup()
    
    def _setup_routes(self):
        # Static files
        if STATIC_DIR.exists():
            self.app.router.add_static('/static', STATIC_DIR)
        
        # Pages
        self.app.router.add_get("/", self._handle_index)
        
        # API
        self.app.router.add_get("/api/status", self._handle_status)
        self.app.router.add_get("/api/guilds", self._handle_guilds)
        self.app.router.add_get("/api/guilds/{guild_id}", self._handle_guild_detail)
        self.app.router.add_get("/api/guilds/{guild_id}/settings", self._handle_guild_settings)
        self.app.router.add_post("/api/guilds/{guild_id}/settings", self._handle_update_settings)
        self.app.router.add_post("/api/guilds/{guild_id}/control/{action}", self._handle_control)
        self.app.router.add_get("/api/dashboard-init", self._api_dashboard_init)
        self.app.router.add_get("/api/logs", self._api_get_logs)
        self.app.router.add_get("/ws/logs", self._handle_websocket)
        self.app.router.add_get("/api/analytics", self._handle_analytics)
        self.app.router.add_get("/api/songs", self._handle_songs)
        self.app.router.add_get("/api/library", self._handle_library)
        self.app.router.add_get("/api/users", self._handle_users)
        self.app.router.add_get("/api/users/{user_id}/preferences", self._handle_user_prefs)
        
        # Global & System
        self.app.router.add_get("/api/settings/global", self._handle_global_settings)
        self.app.router.add_post("/api/settings/global", self._handle_global_settings)
        self.app.router.add_get("/api/notifications", self._handle_notifications)
        self.app.router.add_post("/api/guilds/{guild_id}/leave", self._handle_leave_guild)
        self.app.router.add_get("/api/dashboard-init", self._handle_dashboard_init)
    
    async def _handle_index(self, request: web.Request) -> web.Response:
        html_file = TEMPLATE_DIR / "index.html"
        if html_file.exists():
            return web.Response(text=html_file.read_text(encoding='utf-8'), content_type="text/html")
        return web.Response(text="Dashboard template not found", status=404)
    
    async def _get_status_data(self):
        import psutil
        process = psutil.Process()
        return {
            "status": "online",
            "guilds": len(self.bot.guilds),
            "voice_connections": len(self.bot.voice_clients),
            "latency_ms": round(self.bot.latency * 1000, 2),
            "cpu_percent": psutil.cpu_percent(),
            "ram_percent": psutil.virtual_memory().percent,
            "process_ram_mb": round(process.memory_info().rss / 1024 / 1024, 2),
            "uptime_seconds": int((datetime.now(UTC) - self.bot.start_time).total_seconds())
        }

    async def _handle_status(self, request: web.Request) -> web.Response:
        data = await self._get_status_data()
        return web.json_response(data)
    
    async def _get_guilds_data(self):
        music = self.bot.get_cog("MusicCog")
        guilds = []
        for guild in self.bot.guilds:
            player = music.get_player(guild.id) if music else None
            # Queue stats
            queue_size = 0
            queue_duration = 0
            if player:
                queue_size = player.queue.qsize()
                total_secs = 0
                for _, _, item in list(player.queue._queue):
                    if hasattr(item, 'duration_seconds') and item.duration_seconds:
                        total_secs += item.duration_seconds
                if player.current and player.current.duration_seconds:
                    total_secs += player.current.duration_seconds
                queue_duration = round(total_secs / 60, 1)

            data = {
                "id": str(guild.id),
                "name": guild.name,
                "member_count": guild.member_count,
                "is_playing": bool(player and player.is_playing),
                "queue_size": queue_size,
                "queue_duration": queue_duration
            }
            if player and player.current:
                data["current_song"] = player.current.title
                data["current_artist"] = player.current.artist
                data["video_id"] = player.current.video_id
                data["discovery_reason"] = player.current.discovery_reason
                data["duration_seconds"] = player.current.duration_seconds
                data["genre"] = player.current.genre
                data["year"] = player.current.year
                if player.current.for_user_id:
                    user = self.bot.get_user(player.current.for_user_id)
                    data["for_user"] = user.display_name if user else str(player.current.for_user_id)
            guilds.append(data)
        return {"guilds": guilds}

    async def _handle_guilds(self, request: web.Request) -> web.Response:
        data = await self._get_guilds_data()
        return web.json_response(data)
    
    async def _handle_guild_detail(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info["guild_id"])
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return web.json_response({"error": "Not found"}, status=404)
        
        music = self.bot.get_cog("MusicCog")
        player = music.get_player(guild_id) if music else None
        
        queue_size = 0
        queue_duration_mins = 0
        if player:
            queue_size = player.queue.qsize()
            # Calculate total duration of remaining songs in queue
            total_seconds = 0
            # PriorityQueue doesn't allow direct iteration easily without consuming
            # but we can look at the internal _queue list
            for _, _, item in list(player.queue._queue):
                if hasattr(item, 'duration_seconds') and item.duration_seconds:
                    total_seconds += item.duration_seconds
            
            # Add current song duration if any
            if player.current and player.current.duration_seconds:
                 total_seconds += player.current.duration_seconds
                 
            queue_duration_mins = round(total_seconds / 60, 1)

        return web.json_response({
            "id": str(guild.id),
            "name": guild.name,
            "member_count": guild.member_count,
            "queue_size": queue_size,
            "queue_duration": queue_duration_mins
        })
    
    async def _handle_guild_settings(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info["guild_id"])
        if not hasattr(self.bot, "db"):
            return web.json_response({})
        from src.database.crud import GuildCRUD
        crud = GuildCRUD(self.bot.db)
        settings = await crud.get_all_settings(guild_id)
        return web.json_response(settings)
    
    async def _handle_update_settings(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info["guild_id"])
        data = await request.json()
        
        if hasattr(self.bot, "db"):
            from src.database.crud import GuildCRUD
            crud = GuildCRUD(self.bot.db)
            
            # Ensure guild exists in DB to prevent Foreign Key errors
            guild = self.bot.get_guild(guild_id)
            guild_name = guild.name if guild else f"Guild {guild_id}"
            await crud.get_or_create(guild_id, guild_name)
            
            # Save settings
            if "pre_buffer" in data:
                await crud.set_setting(guild_id, "pre_buffer", str(data["pre_buffer"]).lower())
            if "buffer_amount" in data:
                 await crud.set_setting(guild_id, "buffer_amount", str(data["buffer_amount"]))
            if "replay_cooldown" in data:
                 await crud.set_setting(guild_id, "replay_cooldown", str(data["replay_cooldown"]))
            if "max_song_duration" in data:
                 await crud.set_setting(guild_id, "max_song_duration", str(data["max_song_duration"]))
            if "ephemeral_duration" in data:
                 await crud.set_setting(guild_id, "ephemeral_duration", str(data["ephemeral_duration"]))
            if "discovery_weights" in data:
                 await crud.set_setting(guild_id, "discovery_weights", data["discovery_weights"])
            if "metadata_config" in data:
                 logger.debug(f"Dashboard: Saving metadata_config for guild {guild_id}: {data['metadata_config']}")
                 await crud.set_setting(guild_id, "metadata_config", data["metadata_config"])
                 
            # Apply to active player if exists
            music = self.bot.get_cog("MusicCog")
            if music:
                player = music.get_player(guild_id)
                if player:
                    if "pre_buffer" in data:
                        player.pre_buffer = bool(data["pre_buffer"])
                        
        return web.json_response({"status": "ok"})
    
    async def _handle_control(self, request: web.Request) -> web.Response:
        """Handle playback controls."""
        guild_id = int(request.match_info["guild_id"])
        action = request.match_info["action"]
        
        music = self.bot.get_cog("MusicCog")
        if not music:
            return web.json_response({"error": "Music cog not loaded"}, status=503)
        
        player = music.get_player(guild_id)
        if not player.voice_client:
            return web.json_response({"error": "Not connected"}, status=400)
        
        try:
            if action == "pause":
                if player.voice_client.is_playing():
                    player.voice_client.pause()
                elif player.voice_client.is_paused():
                    player.voice_client.resume()
            
            elif action == "skip":
                player.voice_client.stop()
            
            elif action == "stop":
                # Clear queue and stop
                while not player.queue.empty():
                    try:
                        player.queue.get_nowait()
                    except (asyncio.QueueEmpty, Exception):
                        break
                
                if player.voice_client.is_playing() or player.voice_client.is_paused():
                    player.voice_client.stop()
                
                await player.voice_client.disconnect()
            
            return web.json_response({"status": "ok", "action": action})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    async def _handle_songs(self, request: web.Request) -> web.Response:
        """Get song library."""
        if not hasattr(self.bot, "db"):
            return web.json_response({"songs": []})
        
        guild_id = request.query.get("guild_id")
        params = []
        where_clause = ""
        
        if guild_id:
            # Filter by playback history in this guild
            where_clause = "WHERE ps.guild_id = ?"
            params.append(int(guild_id))
        
        query = f"""
            SELECT 
                ph.played_at,
                s.title,
                s.artist_name,
                s.duration_seconds,
                (SELECT GROUP_CONCAT(DISTINCT sg.genre) FROM song_genres sg WHERE sg.song_id = s.id) as genre,
                CASE WHEN ph.discovery_source = 'user_request' THEN u.username ELSE NULL END as requested_by,
                (SELECT GROUP_CONCAT(DISTINCT u2.username) 
                 FROM song_reactions sr 
                 JOIN users u2 ON sr.user_id = u2.id 
                 WHERE sr.song_id = s.id AND sr.reaction = 'like') as liked_by,
                (SELECT GROUP_CONCAT(DISTINCT u2.username) 
                 FROM song_reactions sr 
                 JOIN users u2 ON sr.user_id = u2.id 
                 WHERE sr.song_id = s.id AND sr.reaction = 'dislike') as disliked_by
            FROM playback_history ph
            JOIN songs s ON ph.song_id = s.id
            JOIN playback_sessions ps ON ph.session_id = ps.id
            LEFT JOIN users u ON ph.for_user_id = u.id
            {where_clause}
            ORDER BY ph.played_at DESC
            LIMIT 100
        """
        songs = await self.bot.db.fetch_all(query, tuple(params))
        
        # Serialize for JSON
        data = []
        for s in songs:
            item = dict(s)
            # Handle datetime fields if they exist as objects
            for key in ["created_at", "last_played"]:
                if key in item and item[key]:
                    if hasattr(item[key], "isoformat"): # datetime object
                        item[key] = item[key].isoformat()
                    # If string, leave as is
            data.append(item)
            
        return web.json_response({"songs": data})
    
    async def _get_analytics_data(self, guild_id=None):
        if not hasattr(self.bot, "db"):
            return {"error": "No database"}
        
        from src.database.crud import AnalyticsCRUD
        crud = AnalyticsCRUD(self.bot.db)
        
        gid = int(guild_id) if guild_id else None
        
        top_songs = await crud.get_top_songs(limit=5, guild_id=gid)
        top_users = await crud.get_top_users(limit=5, guild_id=gid)
        stats = await crud.get_total_stats(guild_id=gid)
        
        top_liked_songs = await crud.get_top_liked_songs(limit=5)
        top_liked_artists = await crud.get_top_liked_artists(limit=5)
        top_liked_genres = await crud.get_top_liked_genres(limit=5)
        top_played_artists = await crud.get_top_played_artists(limit=5, guild_id=gid)
        top_played_genres = await crud.get_top_played_genres(limit=5, guild_id=gid)
        top_useful_users = await crud.get_top_useful_users(limit=5)
        
        # Get 7-day trends
        playback_trends = await crud.get_playback_trends(days=7, guild_id=gid)
        peak_hours = await crud.get_peak_hours(days=30, guild_id=gid)

        formatted_users = []
        for u in top_users:
            d = dict(u) if not isinstance(u, dict) else u
            formatted_users.append({
                "id": str(d["id"]),
                "name": d["username"],
                "plays": d["plays"],
                "total_likes": d["reactions"],
                "playlists_imported": d["playlists"],
            })

        return {
            "total_songs": stats["total_songs"],
            "total_users": stats["total_users"],
            "total_plays": stats["total_plays"],
            "playback_trends": playback_trends,
            "peak_hours": peak_hours,
            "top_songs": [dict(r) for r in top_songs],
            "top_users": formatted_users,
            "top_liked_songs": [dict(r) for r in top_liked_songs],
            "top_liked_artists": [dict(r) for r in top_liked_artists],
            "top_liked_genres": [dict(r) for r in top_liked_genres],
            "top_played_artists": [dict(r) for r in top_played_artists],
            "top_played_genres": [dict(r) for r in top_played_genres],
            "top_useful_users": [dict(r) for r in top_useful_users],
        }

    async def _handle_analytics(self, request: web.Request) -> web.Response:
        guild_id = request.query.get("guild_id")
        data = await self._get_analytics_data(guild_id)
        return web.json_response(data)
    
    async def _handle_top_songs(self, request: web.Request) -> web.Response:
        """Get top songs list."""
        if not hasattr(self.bot, "db"):
             return web.json_response({"songs": []})
        
        from src.database.crud import AnalyticsCRUD
        crud = AnalyticsCRUD(self.bot.db)
        
        guild_id = request.query.get("guild_id")
        gid = int(guild_id) if guild_id else None
        
        songs = await crud.get_top_songs(limit=10, guild_id=gid)
        return web.json_response({"songs": [dict(r) for r in songs]})
    
    async def _handle_users(self, request: web.Request) -> web.Response:
        """Get users list."""
        if not hasattr(self.bot, "db"):
             return web.json_response({"users": []})
             
        from src.database.crud import AnalyticsCRUD
        crud = AnalyticsCRUD(self.bot.db)
        
        guild_id = request.query.get("guild_id")
        gid = int(guild_id) if guild_id else None
        
        users = await crud.get_top_users(limit=50, guild_id=gid)
        
        # Format
        data = []
        for u in users:
            d = dict(u)
            d["formatted_id"] = str(d["id"])
            data.append(d)
        return web.json_response({"users": data})

    async def _handle_global_settings(self, request: web.Request) -> web.Response:
        """Get or update global settings."""
        if not hasattr(self.bot, "db"):
            return web.json_response({})
        
        from src.database.crud import SystemCRUD
        crud = SystemCRUD(self.bot.db)
        
        if request.method == "POST":
            data = await request.json()
            for key, value in data.items():
                await crud.set_global_setting(key, value)
            return web.json_response({"status": "ok"})
        else:
            limit = await crud.get_global_setting("max_concurrent_servers")
            test_mode = await crud.get_global_setting("test_mode")
            test_duration = await crud.get_global_setting("playback_duration")
            
            return web.json_response({
                "max_concurrent_servers": limit,
                "test_mode": test_mode,
                "playback_duration": test_duration or 30
            })

    async def _get_notifications_data(self):
        if not hasattr(self.bot, "db"):
            return {"notifications": []}
        
        from src.database.crud import SystemCRUD
        crud = SystemCRUD(self.bot.db)
        notifications = await crud.get_recent_notifications()
        data = []
        for n in notifications:
            d = dict(n)
            if isinstance(n["created_at"], str):
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(n["created_at"])
                    d["created_at"] = dt.timestamp()
                except ValueError:
                    d["created_at"] = 0
            elif isinstance(n["created_at"], datetime):
                d["created_at"] = n["created_at"].timestamp()
            else:
                d["created_at"] = 0
            data.append(d)
        return {"notifications": data}

    async def _handle_notifications(self, request: web.Request) -> web.Response:
        data = await self._get_notifications_data()
        return web.json_response(data)

    async def _handle_dashboard_init(self, request: web.Request) -> web.Response:
        """Consolidated endpoint for dashboard initialization."""
        status_data = await self._get_status_data()
        guilds_data = await self._get_guilds_data()
        analytics_data = await self._get_analytics_data()
        notifications_data = await self._get_notifications_data()
        
        return web.json_response({
            "status": status_data,
            "guilds": guilds_data["guilds"],
            "analytics": analytics_data,
            "notifications": notifications_data["notifications"]
        })

    async def _handle_leave_guild(self, request: web.Request) -> web.Response:
        """Force bot to leave a guild."""
        guild_id = int(request.match_info["guild_id"])
        guild = self.bot.get_guild(guild_id)
        if guild:
            await guild.leave()
            
            # Log notification
            if hasattr(self.bot, "db"):
                from src.database.crud import SystemCRUD
                crud = SystemCRUD(self.bot.db)
                await crud.add_notification("info", f"Manually left server: {guild.name}")
                
            return web.json_response({"status": "ok"})
        return web.json_response({"error": "Guild not found"}, status=404)

    async def _handle_library(self, request: web.Request) -> web.Response:
        """Get unified song library."""
        if not hasattr(self.bot, "db"):
            return web.json_response({"library": []})
        
        guild_id = request.query.get("guild_id")
        if guild_id:
            guild_id = int(guild_id)
            
        from src.database.crud import LibraryCRUD
        crud = LibraryCRUD(self.bot.db)
        library = await crud.get_library(guild_id=guild_id)
        
        log_guild = f"guild {guild_id}" if guild_id else "Global Library"
        logger.info(f"Fetched library for {log_guild}: {len(library)} entries")
        
        # Serialize timestamps
        for entry in library:
            if "last_added" in entry and isinstance(entry["last_added"], datetime):
                entry["last_added"] = entry["last_added"].isoformat()
                
        return web.json_response({"library": library})

    
    async def _handle_user_prefs(self, request: web.Request) -> web.Response:
        user_id = int(request.match_info["user_id"])
        if not hasattr(self.bot, "db"):
            return web.json_response({})
        
        from src.database.crud import PreferenceCRUD
        crud = PreferenceCRUD(self.bot.db)
        prefs = await crud.get_all_preferences(user_id)
        return web.json_response(prefs)
    
    async def _api_get_logs(self, request: web.Request) -> web.Response:
        """Endpoint to fetch the latest logs for fallback polling."""
        return web.json_response({"logs": list(self.ws_manager.recent_logs)})

    async def _handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.ws_manager.clients.add(ws)
        for log in self.ws_manager.recent_logs:
            await ws.send_json(log)
        try:
            async for _ in ws:
                pass
        finally:
            self.ws_manager.clients.discard(ws)
        return ws


async def setup(bot: commands.Bot):
    from src.config import config
    await bot.add_cog(DashboardCog(bot, config.WEB_HOST, config.WEB_PORT))
