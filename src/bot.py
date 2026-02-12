"""
Smart Discord Music Bot - Main Entry Point
"""
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

import discord
from datetime import datetime, UTC
from discord.ext import commands

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
logging.getLogger("musicbrainzngs").setLevel(logging.WARNING)
logger = logging.getLogger("bot")


class MusicBot(commands.Bot):
    """Smart Discord Music Bot with preference learning."""
    
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        intents.guilds = True
        
        super().__init__(
            command_prefix="!",  # Fallback prefix, we use slash commands
            intents=intents,
            help_command=None,
        )
        
        # Will be initialized in setup_hook
        self.db = None
        self.discovery = None
        self.start_time = datetime.now(UTC)
        self.preferences = None
    
    async def setup_hook(self) -> None:
        """Called when the bot is starting up."""
        logger.info("Setting up bot...")
        
        # Database
        from src.config import config
        from src.database.connection import DatabaseManager
        from src.database.crud import SongCRUD, UserCRUD, GuildCRUD, PlaybackCRUD, PreferenceCRUD, ReactionCRUD
        
        self.db = await DatabaseManager.create(config.DATABASE_PATH)
        logger.info(f"Database initialized at {config.DATABASE_PATH}")
        
        # Initialize services
        from src.services.youtube import YouTubeService
        from src.services.spotify import SpotifyService
        from src.services.normalizer import SongNormalizer
        from src.services.discovery import DiscoveryEngine
        from src.services.preferences import PreferenceManager
        
        self.youtube = YouTubeService(config.YTDL_COOKIES_PATH, config.YTDL_PO_TOKEN)
        self.spotify = SpotifyService(config.SPOTIFY_CLIENT_ID, config.SPOTIFY_CLIENT_SECRET)
        self.normalizer = SongNormalizer(self.youtube)
        
        
        # Initialize CRUD helpers
        pref_crud = PreferenceCRUD(self.db)
        playback_crud = PlaybackCRUD(self.db)
        reaction_crud = ReactionCRUD(self.db)
        song_crud = SongCRUD(self.db)
        user_crud = UserCRUD(self.db)
        
        # Initialize discovery engine
        self.discovery = DiscoveryEngine(
            youtube=self.youtube,
            spotify=self.spotify,
            normalizer=self.normalizer,
            preference_crud=pref_crud,
            playback_crud=playback_crud,
            reaction_crud=reaction_crud,
        )
        
        # Initialize preference manager
        self.preferences = PreferenceManager(pref_crud, song_crud, user_crud)
        
        logger.info("Services initialized")
        
        # Load all cogs from the cogs directory
        cogs_dir = Path(__file__).parent / "cogs"
        if cogs_dir.exists():
            for cog_file in cogs_dir.glob("*.py"):
                if cog_file.name.startswith("_"):
                    continue
                cog_name = f"src.cogs.{cog_file.stem}"
                try:
                    await self.load_extension(cog_name)
                    logger.info(f"Loaded cog: {cog_name}")
                except Exception as e:
                    logger.error(f"Failed to load cog {cog_name}: {e}")
        
        # Sync slash commands
        logger.info("Syncing slash commands...")
        await self.tree.sync()
        logger.info("Slash commands synced")
    
    async def on_ready(self) -> None:
        """Called when the bot is fully ready."""
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} guild(s)")
        
        # Set presence
        activity = discord.Activity(
            type=discord.ActivityType.listening,
            name="/play"
        )
        await self.change_presence(activity=activity)

        # Cleanup stale sessions
        await self._cleanup_stale_sessions()

    async def _cleanup_stale_sessions(self) -> None:
        """Mark sessions that were interrupted by a crash/restart as ended."""
        if not self.db:
            return
            
        from src.database.crud import PlaybackCRUD
        playback_crud = PlaybackCRUD(self.db)
        
        try:
            stale_sessions = await playback_crud.get_stale_sessions()
            if stale_sessions:
                logger.info(f"Found {len(stale_sessions)} stale sessions. Cleaning up...")
                
                # Get MusicCog for sending recaps
                music_cog = self.get_cog("MusicCog")
                
                for session in stale_sessions:
                    await playback_crud.end_session(session["id"])
                    
                    # Try to send a recap if MusicCog is available
                    if music_cog:
                        asyncio.create_task(music_cog.send_recap_for_session(session["id"], session["guild_id"]))
                
                logger.info("Stale sessions cleaned up.")
        except Exception as e:
            logger.error(f"Failed to cleanup stale sessions: {e}")
    
    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Called when the bot joins a new guild."""
        logger.info(f"Joined guild: {guild.name} (ID: {guild.id})")
        
        # Check max servers limit
        if self.db:
            from src.database.crud import SystemCRUD
            crud = SystemCRUD(self.db)
            limit = await crud.get_global_setting("max_concurrent_servers")
            
            if limit is not None:
                try:
                    limit_int = int(limit)
                    if len(self.guilds) > limit_int:
                        logger.warning(f"Server limit ({limit_int}) exceeded. Leaving {guild.name}.")
                        await guild.leave()
                        await crud.add_notification(
                            "warning", 
                            f"Auto-left server '{guild.name}' because the limit of {limit_int} servers was exceeded."
                        )
                except ValueError:
                    pass
    
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Called when the bot is removed from a guild."""
        logger.info(f"Left guild: {guild.name} (ID: {guild.id})")
    
    async def close(self) -> None:
        """Cleanup when the bot is shutting down."""
        logger.info("Shutting down...")
        
        # Explicitly unload music cog to ensure proper cleanup (stops FFMPEG)
        try:
            await self.unload_extension("src.cogs.music")
        except Exception:
            pass

        # Explicitly unload dashboard cog to close websockets
        try:
            await self.unload_extension("src.cogs.dashboard")
        except Exception:
            pass

        # Disconnect from all voice channels (cleanup for any remaining)
        for vc in self.voice_clients:
            try:
                await vc.disconnect(force=True)
            except Exception:
                pass
        
        # Close database
        if self.db:
            try:
                await self.db.close()
            except Exception:
                pass

        await super().close()
        logger.info("Shutdown complete.")


async def main():
    """Main entry point."""
    from src.config import config
    
    bot = MusicBot()
    
    # asyncio.run handles signals on its own, and Windows doesn't support loop.add_signal_handler
    # for SIGINT. Removing manual signal logic to prevent conflicts with KeyboardInterrupt.

    
    async with bot:
        try:
            await bot.start(config.DISCORD_TOKEN)
        except KeyboardInterrupt:
            logger.info("Shutdown initiated by user...")
        except Exception as e:
            logger.error(f"Bot error: {e}")
        finally:
            if not bot.is_closed():
                await bot.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # standard exit
        os._exit(0)
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        os._exit(1)
