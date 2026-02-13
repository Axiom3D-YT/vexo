"""
Music Cog - Playback commands and audio streaming
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, UTC
from typing import Optional, List
from collections import Counter
import discord
from discord import app_commands
from discord.ext import commands

from src.services.youtube import YouTubeService, YTTrack
from src.services.discogs import DiscogsService
from src.services.musicbrainz import MusicBrainzService
from src.services.groq import GroqService
from src.services.tts import VexoTTSService
from src.database.crud import SongCRUD, UserCRUD, PlaybackCRUD, ReactionCRUD, GuildCRUD, AnalyticsCRUD

logger = logging.getLogger(__name__)


@dataclass
class QueueItem:
    """Item in the music queue."""
    video_id: str
    title: str
    artist: str
    url: str | None = None  # Stream URL, resolved when needed
    requester_id: int | None = None
    discovery_source: str = "user_request"
    discovery_reason: str | None = None
    for_user_id: int | None = None  # Democratic turn tracking
    song_db_id: int | None = None  # Database ID after insertion
    history_id: int | None = None  # Playback history ID
    duration_seconds: int | None = None
    genre: str | None = None
    year: int | None = None
    script_text: str | None = None # Cache for AI script
    metadata_attempted: bool = False


@dataclass
class GuildPlayer:
    """Per-guild music player state."""
    guild_id: int
    voice_client: discord.VoiceClient | None = None
    queue: asyncio.PriorityQueue = field(default_factory=asyncio.PriorityQueue)
    current: QueueItem | None = None
    session_id: str | None = None
    is_playing: bool = False
    autoplay: bool = True
    pre_buffer: bool = True
    last_activity: datetime = field(default_factory=lambda: datetime.now(UTC))
    skip_votes: set = field(default_factory=set)
    _next_url: str | None = None  # Pre-buffered URL
    text_channel_id: int | None = None  # For Now Playing messages
    last_np_msg: discord.Message | None = None
    last_script_msg: discord.Message | None = None # Track the DJ Script message
    _queue_counter: int = 0  # To maintain FIFO in PriorityQueue


class NowPlayingView(discord.ui.View):
    """Interactive Now Playing controls."""
    
    def __init__(self, cog: "MusicCog", guild_id: int):
        super().__init__(timeout=300)  # 5 minute timeout
        self.cog = cog
        self.guild_id = guild_id
    
    @discord.ui.button(emoji="⏸️", style=discord.ButtonStyle.secondary)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self.cog.get_player(self.guild_id)
        if player.voice_client:
            if player.voice_client.is_playing():
                player.voice_client.pause()
                button.emoji = "▶️"
                await interaction.response.edit_message(view=self)
            elif player.voice_client.is_paused():
                player.voice_client.resume()
                button.emoji = "⏸️"
                await interaction.response.edit_message(view=self)
            else:
                if not interaction.response.is_done():
                    await interaction.response.defer()
        else:
            if not interaction.response.is_done():
                await interaction.response.defer()
    
    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self.cog.get_player(self.guild_id)
        if player.voice_client:
            # Ensure process is stopped properly
            if player.voice_client.is_playing() or player.voice_client.is_paused():
                player.voice_client.stop()
            
            # Additional cleanup for FFmpeg process if needed
            # (discord.py's FFmpegAudio usually handles this, but we can be defensive)
            
            # Clear queue
            while not player.queue.empty():
                try:
                    player.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            
            # Stop playing (this will break the loop in _play_loop)
            if player.is_playing or player.voice_client.is_playing():
                player.voice_client.stop()
            
            # End database session and send recap
            await self.cog._end_session(player)

            # Disconnect
            await player.voice_client.disconnect()
            player.voice_client = None

            duration = await self.cog._get_ephemeral_duration(self.guild_id)
            await interaction.response.send_message("⏹️ Stopped and cleared queue!", delete_after=duration)
            self.stop()  # Stop the view from listening for more interactions
        else:
            if not interaction.response.is_done():
                await interaction.response.defer()
    
    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self.cog.get_player(self.guild_id)
        if player.voice_client and player.is_playing:
            player.voice_client.stop()
            duration = await self.cog._get_ephemeral_duration(self.guild_id)
            await interaction.response.send_message("⏭️ Skipped!", delete_after=duration)
        else:
            if not interaction.response.is_done():
                await interaction.response.defer()
    
    @discord.ui.button(emoji="❤️", style=discord.ButtonStyle.secondary)
    async def like(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self.cog.get_player(self.guild_id)
        if player.current:
            # Database: Log Reaction
            if hasattr(self.cog.bot, "db") and self.cog.bot.db and player.current.song_db_id:
                try:
                    song_crud = SongCRUD(self.cog.bot.db)
                    reaction_crud = ReactionCRUD(self.cog.bot.db)
                    
                    # Make permanent if it was ephemeral
                    await song_crud.make_permanent(player.current.song_db_id)

                    # Log reaction
                    await reaction_crud.add_reaction(interaction.user.id, player.current.song_db_id, "like")
                    
                    # Library: Record as 'like'
                    from src.database.crud import LibraryCRUD
                    lib_crud = LibraryCRUD(self.cog.bot.db)
                    await lib_crud.add_to_library(interaction.user.id, player.current.song_db_id, "like")
                except Exception as e:
                    logger.error(f"Failed to log like: {e}")
            
            await interaction.response.send_message(
                f"❤️ Liked **{player.current.title}**!",
                ephemeral=True
            )
        else:
            await interaction.response.defer()
    
    @discord.ui.button(emoji="👎", style=discord.ButtonStyle.secondary)
    async def dislike(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self.cog.get_player(self.guild_id)
        if player.current:
            # Database: Log Reaction
            if hasattr(self.cog.bot, "db") and self.cog.bot.db and player.current.song_db_id:
                try:
                    song_crud = SongCRUD(self.cog.bot.db)
                    reaction_crud = ReactionCRUD(self.cog.bot.db)
                    
                    # Make permanent (even disliking counts as interaction so we keep record)
                    await song_crud.make_permanent(player.current.song_db_id)

                    await reaction_crud.add_reaction(interaction.user.id, player.current.song_db_id, "dislike")
                except Exception as e:
                    logger.error(f"Failed to log dislike: {e}")
            
            await interaction.response.send_message(
                f"👎 Disliked **{player.current.title}**",
                ephemeral=True
            )
        else:
            await interaction.response.defer()


class MusicCog(commands.Cog):
    """Music playback commands and queue management."""
    
    FFMPEG_OPTIONS = {
        "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin -timeout 10000000",
        "options": "-vn",
    }
    IDLE_TIMEOUT = 300  # 5 minutes
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.players: dict[int, GuildPlayer] = {}
        self.youtube = YouTubeService()
        self.discogs = DiscogsService()
        self.musicbrainz = MusicBrainzService()
        self.groq = GroqService()
        self.tts = VexoTTSService()
        
        # FFMPEG Options
        self.FFMPEG_OPTIONS = {
            "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin -timeout 10000000",
            "options": "-vn",
        }
        self._idle_check_task: asyncio.Task | None = None
        self._auto_connect_task: asyncio.Task | None = None
    
    async def cog_load(self):
        """Called when the cog is loaded."""
        self._idle_check_task = asyncio.create_task(self._idle_check_loop())
        self._auto_connect_task = asyncio.create_task(self._auto_connect_loop())
        logger.info("Music cog loaded")
    
    async def cog_unload(self):
        """Called when the cog is unloaded."""
        if self._idle_check_task:
            self._idle_check_task.cancel()
        if self._auto_connect_task:
            self._auto_connect_task.cancel()
        
        # Disconnect from all voice channels
        for player in list(self.players.values()):
            if player.voice_client:
                # Stop playing immediately to kill ffmpeg
                if player.voice_client.is_playing() or player.voice_client.is_paused():
                    player.voice_client.stop()
                
                # End session before disconnect
                await self._end_session(player)
                
                try:
                    await player.voice_client.disconnect(force=True)
                except Exception:
                    pass
        
        logger.info("Music cog unloaded")
    
    async def _get_ephemeral_duration(self, guild_id: int) -> int:
        """Get the auto-delete duration for ephemeral/confirmation messages."""
        if hasattr(self.bot, "db") and self.bot.db:
            try:
                from src.database.crud import GuildCRUD
                guild_crud = GuildCRUD(self.bot.db)
                duration = await guild_crud.get_setting(guild_id, "ephemeral_duration")
                if duration:
                    return int(duration)
            except:
                pass
        return 10  # Default 10 seconds

    def get_player(self, guild_id: int) -> GuildPlayer:
        """Get or create a player for a guild."""
        if guild_id not in self.players:
            self.players[guild_id] = GuildPlayer(guild_id=guild_id)
        return self.players[guild_id]
    
    # ==================== COMMANDS ====================
    
    play_group = app_commands.Group(name="play", description="Play music commands")
    
    @play_group.command(name="song", description="Search and play a specific song")
    @app_commands.describe(query="Song name or search query")
    async def play_song(self, interaction: discord.Interaction, query: str):
        """Search for a song and add it to the queue."""
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        
        # Check if user is in a voice channel
        if not interaction.user.voice:
            await interaction.followup.send("❌ You need to be in a voice channel!", ephemeral=True)
            return
        
        voice_channel = interaction.user.voice.channel
        player = self.get_player(interaction.guild_id)
        
        # Connect to voice channel if not already
        if not player.voice_client or not player.voice_client.is_connected():
            try:
                player.voice_client = await voice_channel.connect(self_deaf=True, timeout=20.0)
                logger.info(f"Connected to {voice_channel.name} in {interaction.guild.name}")
            except Exception as e:
                await interaction.followup.send(f"❌ Failed to connect: {e}", ephemeral=True)
                return
        
        # Search for the song
        # For specific song request, we want the BEST match, so limit 1
        results = await self.youtube.search(query, filter_type="songs", limit=1)
        
        if not results:
            await interaction.followup.send(f"❌ No results found for: `{query}`", ephemeral=True)
            return
        
        track = results[0]
    
        # Ensure duration is present
        if track.duration_seconds is None:
            details = await self.youtube.get_track_info(track.video_id)
            if details and details.duration_seconds:
                track.duration_seconds = details.duration_seconds
        # Check max duration
        if hasattr(self.bot, "db") and self.bot.db:
            from src.database.crud import GuildCRUD
            guild_crud = GuildCRUD(self.bot.db)
            max_duration = await guild_crud.get_setting(interaction.guild_id, "max_song_duration")
            
            # Default to 6 minutes if not set
            if max_duration is None:
                max_duration = "6"
            
            if max_duration and track.duration_seconds:
                try:
                    max_seconds = int(max_duration) * 60
                    if max_seconds > 0 and track.duration_seconds > max_seconds:
                        await interaction.followup.send(
                            f"❌ Song is too long! (Limit: {max_duration} mins)",
                            ephemeral=True
                        )
                        return
                except (ValueError, TypeError):
                    pass
                    
        logger.info(f"Selected track: {track.title}")
        
        # Database persistence
        song_db_id = None
        if hasattr(self.bot, "db") and self.bot.db:
            try:
                user_crud = UserCRUD(self.bot.db)
                song_crud = SongCRUD(self.bot.db)
                
                # Ensure user exists
                await user_crud.get_or_create(interaction.user.id, interaction.user.name)
                
                # Ensure song exists
                song = await song_crud.get_or_create_by_yt_id(
                    canonical_yt_id=track.video_id,
                    title=track.title,
                    artist_name=track.artist,
                    duration_seconds=track.duration_seconds,
                    release_year=track.year,
                    album=track.album
                )
                song_db_id = song["id"]
                
                # Library: Record as 'request'
                from src.database.crud import LibraryCRUD
                lib_crud = LibraryCRUD(self.bot.db)
                await lib_crud.add_to_library(interaction.user.id, song_db_id, "request")
            except Exception as e:
                logger.error(f"Failed to persist song/user data: {e}")

        # Add to queue
        item = QueueItem(
            video_id=track.video_id,
            title=track.title,
            artist=track.artist,
            requester_id=interaction.user.id,
            discovery_source="user_request",
            song_db_id=song_db_id,
            duration_seconds=track.duration_seconds,
            year=track.year
        )
        # Add to priority queue
        # Priority 0: User Request
        player._queue_counter += 1
        player.queue.put_nowait((0, player._queue_counter, item))
        player.last_activity = datetime.now(UTC)
        player.text_channel_id = interaction.channel_id  # Store for Now Playing
        
        # Start playback if not already playing
        if not player.is_playing:
            asyncio.create_task(self._play_loop(player))
        
        # Create embed
        embed = discord.Embed(
            title="🎵 Added to Queue",
            description=f"**{track.title}**\nby {track.artist}",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"Requested by {interaction.user.display_name}")
        
        await interaction.followup.send(embed=embed, ephemeral=True)

    @play_group.command(name="artist", description="Play top songs by an artist and learn your preference")
    @app_commands.describe(artist_name="Artist name")
    async def play_artist(self, interaction: discord.Interaction, artist_name: str):
        """Search for an artist, boost preference, and queue top 5 songs."""
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        
        # Check if user is in a voice channel
        if not interaction.user.voice:
            await interaction.followup.send("❌ You need to be in a voice channel!", ephemeral=True)
            return
            
        voice_channel = interaction.user.voice.channel
        player = self.get_player(interaction.guild_id)
        
        # Connect to voice channel if not already
        if not player.voice_client or not player.voice_client.is_connected():
            try:
                player.voice_client = await voice_channel.connect(self_deaf=True, timeout=20.0)
            except Exception as e:
                await interaction.followup.send(f"❌ Failed to connect: {e}", ephemeral=True)
                return

        # 1. Search artist on Spotify
        sp_artist = await self.bot.spotify.search_artist(artist_name)
        if not sp_artist:
            await interaction.followup.send(f"❌ Artist not found on Spotify: `{artist_name}`", ephemeral=True)
            return

        # 2. Boost preference
        if hasattr(self.bot, "preferences") and self.bot.preferences:
            await self.bot.preferences.boost_artist(interaction.user.id, sp_artist.name)

        # 3. Fetch top tracks
        top_tracks = await self.bot.spotify.get_artist_top_tracks(sp_artist.artist_id)
        if not top_tracks:
            await interaction.followup.send(f"❌ No top tracks found for artist: `{sp_artist.name}`", ephemeral=True)
            return

        # 4. Filter and add top 5 songs
        tracks_to_add = top_tracks[:5]
        
        queued_count = 0
        from src.database.crud import SongCRUD, UserCRUD, LibraryCRUD, GuildCRUD
        song_crud = SongCRUD(self.bot.db) if hasattr(self.bot, "db") else None
        lib_crud = LibraryCRUD(self.bot.db) if hasattr(self.bot, "db") else None
        guild_crud = GuildCRUD(self.bot.db) if hasattr(self.bot, "db") else None
        
        max_seconds = 0
        if guild_crud:
            try:
                max_dur = await guild_crud.get_setting(interaction.guild_id, "max_song_duration")
                
                # Default to 6 minutes if not set
                if max_dur is None:
                    max_dur = "6"
                    
                if max_dur:
                    max_seconds = int(max_dur) * 60
            except (ValueError, TypeError):
                pass
        
        for track in tracks_to_add:
            # We need to find the YouTube ID for these Spotify tracks to play them
            # Use the normalizer to get the canonical YT data
            yt_track = await self.bot.normalizer.normalize_to_yt(track.title, track.artist)
            if not yt_track:
                continue
            
            # Check duration
            if max_seconds > 0 and yt_track.duration_seconds and yt_track.duration_seconds > max_seconds:
                continue
                
            song_db_id = None
            if song_crud:
                # Ensure user exists
                user_crud = UserCRUD(self.bot.db)
                await user_crud.get_or_create(interaction.user.id, interaction.user.name)
                
                # Ensure song exists
                song = await song_crud.get_or_create_by_yt_id(
                    canonical_yt_id=yt_track.video_id,
                    title=yt_track.title,
                    artist_name=yt_track.artist,
                    duration_seconds=yt_track.duration_seconds,
                    release_year=yt_track.year,
                    album=yt_track.album
                )
                song_db_id = song["id"]
                
                # Library: Record as 'request'
                if lib_crud:
                    await lib_crud.add_to_library(interaction.user.id, song_db_id, "request")

            item = QueueItem(
                video_id=yt_track.video_id,
                title=yt_track.title,
                artist=yt_track.artist,
                requester_id=interaction.user.id,
                discovery_source="user_request",
                discovery_reason=f"Top track by {sp_artist.name}",
                song_db_id=song_db_id,
                duration_seconds=yt_track.duration_seconds,
                year=yt_track.year
            )
            # Add to priority queue (Priority 0: User Request)
            player._queue_counter += 1
            player.queue.put_nowait((0, player._queue_counter, item))
            queued_count += 1

        if queued_count == 0:
            await interaction.followup.send(f"❌ Failed to find playable tracks for: `{sp_artist.name}`", ephemeral=True)
            return

        player.last_activity = datetime.now(UTC)
        player.text_channel_id = interaction.channel_id
        
        # Start playback if not already playing
        if not player.is_playing:
            asyncio.create_task(self._play_loop(player))

        # Create embed
        embed = discord.Embed(
            title="👩‍🎤 Artist Radio Queued",
            description=f"Added **{queued_count}** top tracks by **{sp_artist.name}**\nAlso boosted your preference for this artist! ❤️",
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Requested by {interaction.user.display_name}")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
    
    @play_group.command(name="any", description="Start playing with discovery mode")
    async def play_any(self, interaction: discord.Interaction):
        """Start discovery playback without a specific song."""
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            # Interaction might have expired or been acknowledged already, just log and continue if possible or return
            logger.warning("Interaction expired (404) in play_any")
            return
        except Exception as e:
            logger.error(f"Failed to defer interaction: {e}")
            return
        
        if not interaction.user.voice:
            await interaction.followup.send("❌ You need to be in a voice channel!", ephemeral=True)
            return
        
        voice_channel = interaction.user.voice.channel
        player = self.get_player(interaction.guild_id)
        
        # Connect to voice channel
        if not player.voice_client or not player.voice_client.is_connected():
            try:
                player.voice_client = await voice_channel.connect(self_deaf=True, timeout=20.0)
            except Exception as e:
                await interaction.followup.send(f"❌ Failed to connect: {e}", ephemeral=True)
                return
        
        player.autoplay = True
        player.last_activity = datetime.now(UTC)
        player.text_channel_id = interaction.channel_id  # Store for Now Playing
        
        # Start playback if not playing - discovery will kick in
        if not player.is_playing:
            asyncio.create_task(self._play_loop(player))
        
        duration = await self._get_ephemeral_duration(interaction.guild_id)
        msg = await interaction.followup.send(f"🎲 **Discovery mode activated!** Finding songs for you...")
        if msg and duration > 0:
            try:
                await msg.delete(delay=duration)
            except: pass
    
    @app_commands.command(name="pause", description="Pause the current song")
    async def pause(self, interaction: discord.Interaction):
        """Pause playback."""
        player = self.get_player(interaction.guild_id)
        
        if player.voice_client and player.voice_client.is_playing():
            player.voice_client.pause()
            duration = await self._get_ephemeral_duration(interaction.guild_id)
            if not interaction.response.is_done():
                await interaction.response.send_message("⏸️ Paused", delete_after=duration)
        else:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Nothing is playing", ephemeral=True)

    @app_commands.command(name="resume", description="Resume the paused song")
    async def resume(self, interaction: discord.Interaction):
        """Resume playback."""
        player = self.get_player(interaction.guild_id)
        
        if player.voice_client and player.voice_client.is_paused():
            player.voice_client.resume()
            duration = await self._get_ephemeral_duration(interaction.guild_id)
            if not interaction.response.is_done():
                await interaction.response.send_message("▶️ Resumed", delete_after=duration)
        else:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Nothing is paused", ephemeral=True)
    
    @app_commands.command(name="skip", description="Skip the current song")
    async def skip(self, interaction: discord.Interaction):
        """Skip the current song."""
        player = self.get_player(interaction.guild_id)
        
        if not player.voice_client or not player.is_playing:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Nothing is playing", ephemeral=True)
            return
        
        duration = await self._get_ephemeral_duration(interaction.guild_id)
        player.voice_client.stop()
        if not interaction.response.is_done():
            await interaction.response.send_message("⏭️ Skipped!", delete_after=duration)
    
    @app_commands.command(name="forceskip", description="Force skip (DJ only)")
    @app_commands.default_permissions(manage_channels=True)
    async def forceskip(self, interaction: discord.Interaction):
        """Force skip without voting."""
        player = self.get_player(interaction.guild_id)
        
        if player.voice_client and player.is_playing:
            player.voice_client.stop()
            duration = await self._get_ephemeral_duration(interaction.guild_id)
            if not interaction.response.is_done():
                await interaction.response.send_message("⏭️ Force skipped!", delete_after=duration)
        else:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Nothing is playing", ephemeral=True)
    
    @app_commands.command(name="queue", description="Show the current queue")
    async def queue(self, interaction: discord.Interaction):
        """Show the queue."""
        player = self.get_player(interaction.guild_id)
        
        embed = discord.Embed(title="🎵 Queue", color=discord.Color.blue())
        
        # Current song
        if player.current:
            embed.add_field(
                name="Now Playing",
                value=f"**{player.current.title}**\nby {player.current.artist}",
                inline=False
            )
        
        # Upcoming songs
        if player.queue.empty():
            embed.add_field(name="Up Next", value="Queue is empty", inline=False)
        else:
            # Convert queue to list for display (peek without removing)
            # PriorityQueue stores (priority, counter, item)
            items = [item for _, _, item in list(player.queue._queue)[:10]]
            upcoming = []
            for i, item in enumerate(items, 1):
                upcoming.append(f"{i}. **{item.title}** - {item.artist}")
            embed.add_field(name="Up Next", value="\n".join(upcoming), inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @app_commands.command(name="nowplaying", description="Show the current song")
    async def nowplaying(self, interaction: discord.Interaction):
        """Show current song with discovery info."""
        player = self.get_player(interaction.guild_id)
        
        if not player.current:
            await interaction.response.send_message("❌ Nothing is playing", ephemeral=True)
            return
        
        embed = discord.Embed(
            title="🎵 Now Playing",
            description=f"**{player.current.title}**\nby {player.current.artist}",
            color=discord.Color.green()
        )
        
        if player.current.discovery_reason:
            embed.add_field(name="Discovery", value=player.current.discovery_reason, inline=False)
        
        if player.current.for_user_id:
            user = self.bot.get_user(player.current.for_user_id)
            if user:
                embed.set_footer(text=f"🎲 Playing for {user.display_name}")
        elif player.current.requester_id:
            user = self.bot.get_user(player.current.requester_id)
            if user:
                embed.set_footer(text=f"Requested by {user.display_name}")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @app_commands.command(name="clear", description="Clear the queue (DJ only)")
    @app_commands.default_permissions(manage_channels=True)
    async def clear(self, interaction: discord.Interaction):
        """Clear the queue."""
        player = self.get_player(interaction.guild_id)
        
        # Clear the queue
        while not player.queue.empty():
            try:
                player.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        
        await interaction.response.send_message("🗑️ Queue cleared!", ephemeral=True)
    
    @app_commands.command(name="autoplay", description="Toggle autoplay/discovery mode")
    @app_commands.describe(enabled="Enable or disable autoplay")
    async def autoplay(self, interaction: discord.Interaction, enabled: bool):
        """Toggle autoplay mode."""
        player = self.get_player(interaction.guild_id)
        player.autoplay = enabled
        duration = await self._get_ephemeral_duration(interaction.guild_id)
        msg = "✅ Autoplay enabled!" if enabled else "❌ Autoplay disabled!"
        await interaction.response.send_message(msg, delete_after=duration)
    
    # ==================== PLAYBACK LOOP ====================
    
    async def _play_loop(self, player: GuildPlayer):
        """Main playback loop for a guild."""
        player.is_playing = True
        
        try:
            while player.voice_client and player.voice_client.is_connected():
                player.skip_votes.clear()
                # Get next from priority queue
                try:
                    # PriorityQueue returns (priority, counter, item)
                    _, _, item = player.queue.get_nowait()
                except asyncio.QueueEmpty:
                    # If queue is empty, trigger emergency discovery
                    # (This handles the very first play or if prep failed)
                    await self._prepare_next_song(player)
                    try:
                        _, _, item = player.queue.get_nowait()
                    except asyncio.QueueEmpty:
                        player.is_playing = False
                        break # Truly nothing available
                
                player.current = item
                player.last_activity = datetime.now(UTC)
                
                # Database: Ensure session and log playback
                history_id = None
                if hasattr(self.bot, "db") and self.bot.db:
                    try:
                        playback_crud = PlaybackCRUD(self.bot.db)
                        song_crud = SongCRUD(self.bot.db)
                        guild_crud = GuildCRUD(self.bot.db)
                        user_crud = UserCRUD(self.bot.db)

                        # 1. Ensure Guild & Session
                        if not player.session_id:
                            # Verify guild exists
                            if player.voice_client and player.voice_client.guild:
                                await guild_crud.get_or_create(
                                    player.guild_id, 
                                    player.voice_client.guild.name
                                )
                            
                            player.session_id = await playback_crud.create_session(
                                guild_id=player.guild_id,
                                channel_id=player.voice_client.channel.id
                            )

                            # Log initial listeners
                            for m in player.voice_client.channel.members:
                                if not m.bot:
                                    await user_crud.get_or_create(m.id, m.name)
                                    await playback_crud.add_listener(player.session_id, m.id)
                        
                        # 2. Check Song Existence and Persistence Policy
                        if not item.song_db_id:
                            # User requests are PERMANENT (is_ephemeral=False)
                            # Discovery songs are EPHEMERAL (is_ephemeral=True)
                            is_ephemeral = (item.discovery_source != "user_request")
                            
                            song = await song_crud.get_or_create_by_yt_id(
                                canonical_yt_id=item.video_id,
                                title=item.title,
                                artist_name=item.artist,
                                is_ephemeral=is_ephemeral,
                                duration_seconds=item.duration_seconds,
                                release_year=item.year
                            )
                            item.song_db_id = song["id"]
                            
                            # If it was ephemeral and now requested by user, make it permanent
                            # If it was ephemeral and now requested by user, make it permanent
                            if not is_ephemeral and song.get("is_ephemeral"):
                                await song_crud.make_permanent(song["id"])
                        
                        # Metadata Enrichment Logic (Configurable)
                        metadata_config = None
                        if hasattr(self.bot, "db") and self.bot.db:
                            metadata_config = await guild_crud.get_setting(player.guild_id, "metadata_config")
                        
                        await self._resolve_metadata(item, metadata_config)
                        
                        # Fallback: Populate from DB if Spotify failed or was unavailable
                        if (not item.year or not item.genre) and item.song_db_id:
                            if 'song' not in locals():
                                song = await song_crud.get_by_id(item.song_db_id)
                            
                            if song:
                                if not item.year: item.year = song.get("release_year")
                                if not item.duration_seconds: item.duration_seconds = song.get("duration_seconds")
                                
                                if not item.genre:
                                    genres = await song_crud.get_genres(item.song_db_id)
                                    if genres:
                                        item.genre = genres[0].title()

                        # 3. Log play
                        if item.song_db_id:
                             # Ensure user exists for FK constraint
                             target_user_id = item.for_user_id or item.requester_id
                             if target_user_id:
                                 # Try to find user in guild
                                 member = player.voice_client.guild.get_member(target_user_id)
                                 username = member.name if member else "Unknown User"
                                 await user_crud.get_or_create(target_user_id, username)
                             
                             history_id = await playback_crud.log_track(
                                 session_id=player.session_id,
                                 song_id=item.song_db_id,
                                 discovery_source=item.discovery_source,
                                 discovery_reason=item.discovery_reason,
                                 for_user_id=target_user_id
                             )
                             item.history_id = history_id

                             # Library: Record as 'request' if discovery source is user_request
                             if item.discovery_source == "user_request" and target_user_id and item.song_db_id:
                                 from src.database.crud import LibraryCRUD
                                 lib_crud = LibraryCRUD(self.bot.db)
                                 await lib_crud.add_to_library(target_user_id, item.song_db_id, "request")
                    except Exception as e:
                        logger.error(f"Failed to log playback start: {e}")
                        # Ensure we don't crash playback if DB logging fails
                        pass
                
                # 2. Get stream URL (Use pre-fetched if available)
                url = item.url
                if not url:
                    url = await self.youtube.get_stream_url(item.video_id)
                
                if not url:
                    logger.error(f"Failed to get stream URL for {item.video_id}")
                    continue
                
                item.url = url
                
                # Prepare next song immediately (Gate for gapless)
                asyncio.create_task(self._prepare_next_song(player))
                
                # Play the audio
                try:
                    logger.info(f"Starting audio probe for {item.title}...")
                    try:
                        source = await asyncio.wait_for(
                            discord.FFmpegOpusAudio.from_probe(url, **self.FFMPEG_OPTIONS),
                            timeout=10.0
                        )
                        logger.info(f"Audio probe finished for {item.title}")
                    except asyncio.TimeoutError:
                        logger.error(f"Audio probe timed out for {item.title}")
                        continue
                    except asyncio.CancelledError:
                        logger.warning(f"Audio probe cancelled for {item.title}")
                        continue
                    except Exception as e:
                        logger.error(f"Audio probe failed for {item.title}: {e}")
                        continue

                    play_complete = asyncio.Event()
                    
                    def after_play(error):
                        if error:
                            logger.error(f"Playback error: {error}")
                        play_complete.set()
                    
                    player.voice_client.play(source, after=after_play)
                    
                    # Detailed log entry
                    log_user = f"User:{item.for_user_id}" if item.for_user_id else f"Requester:{item.requester_id}"
                    log_source = f"{item.discovery_source} ({item.discovery_reason})" if item.discovery_reason else item.discovery_source
                    logger.info(f"Playing: {item.title} | {item.artist} | {item.genre or 'Unknown Genre'} | {log_user} | {log_source}")
                    # Send Now Playing embed
                    await self._send_now_playing(player)

                    # Trigger DJ Script Generation (Fire-and-forget task)
                    asyncio.create_task(self._send_dj_script(player, item))
                    
                    # ---------------- PLAYBACK WATCHDOG ----------------
                    test_mode = False
                    test_duration = 30
                    if hasattr(self.bot, "db") and self.bot.db:
                        try:
                            from src.database.crud import SystemCRUD
                            system_crud = SystemCRUD(self.bot.db)
                            test_mode = await system_crud.get_global_setting("test_mode")
                            test_duration = await system_crud.get_global_setting("playback_duration") or 30
                        except Exception as e:
                            logger.error(f"Failed to fetch test mode settings: {e}")

                    # Calculate safety timeout: Test duration OR (Song duration + 20s buffer)
                    if test_mode:
                        timeout_duration = float(test_duration)
                    else:
                        # Default to 10 mins if duration is unknown
                        timeout_duration = float(item.duration_seconds or 600) + 20

                    # Wait for song to finish (or timeout via Watchdog)
                    try:
                        if test_mode:
                            logger.info(f"TEST MODE ACTIVE: playing for {timeout_duration}s")
                        
                        await asyncio.wait_for(play_complete.wait(), timeout=timeout_duration)
                    except asyncio.TimeoutError:
                        if test_mode:
                            logger.info(f"TEST MODE: Time limit reached ({timeout_duration}s), skipping...")
                        else:
                            logger.warning(f"WATCHDOG: Song {item.title} timed out after {timeout_duration}s. Recovering event loop...")
                            # Proactively log this event
                            logger.error(f"Playback stuck detected for {item.title} (Duration: {item.duration_seconds}s). Force stopping.")
                        
                        if player.voice_client and player.voice_client.is_playing():
                            player.voice_client.stop()
                        
                        # Wait a tiny bit for after_play to trigger and play_complete to set
                        try:
                            await asyncio.wait_for(play_complete.wait(), timeout=2.0)
                        except asyncio.TimeoutError:
                             # If it still hasn't set, manually set it to break any other waiters
                             play_complete.set()
                    # ---------------------------------------------------

                    # Database: Log Playback End
                    if hasattr(self.bot, "db") and self.bot.db and item.history_id:
                         try:
                             playback_crud = PlaybackCRUD(self.bot.db)
                             # If we were in test mode and timed out, it's NOT a full completion in normal sense but fine for analytics
                             completed = True
                             
                             # Check if skipped via votes
                             if player.skip_votes and len(player.skip_votes) > 0:
                                 completed = False
                                 
                             await playback_crud.mark_completed(item.history_id, completed)
                         except Exception as e:
                             logger.error(f"Failed to log playback end: {e}")
                    
                except Exception as e:
                    logger.error(f"Error playing {item.title}: {e}")
                    continue
                
                player.current = None
        
        finally:
            player.is_playing = False
            player.current = None
    
    async def _get_discovery_song(self, player: GuildPlayer) -> QueueItem | None:
        """Get next song from discovery engine."""
        # Get voice channel members
        if not player.voice_client or not player.voice_client.channel:
            return None
        
        voice_members = [m.id for m in player.voice_client.channel.members if not m.bot]
        
        # Try discovery engine first
        if voice_members and hasattr(self.bot, "discovery") and self.bot.discovery:
            try:
                # Get Cooldown and Weights Setting
                cooldown = 7200 # Default 2 hours
                weights = None
                if hasattr(self.bot, "db"):
                    from src.database.crud import GuildCRUD
                    guild_crud = GuildCRUD(self.bot.db)
                    
                    # Fetch cooldown
                    setting_cooldown = await guild_crud.get_setting(player.guild_id, "replay_cooldown")
                    if setting_cooldown:
                        try:
                            cooldown = int(setting_cooldown)
                        except ValueError:
                            pass
                    
                    # Fetch discovery weights
                    setting_weights = await guild_crud.get_setting(player.guild_id, "discovery_weights")
                    if setting_weights:
                        weights = setting_weights

                discovered = await self.bot.discovery.get_next_song(
                    player.guild_id,
                    voice_members,
                    weights=weights,
                    cooldown_seconds=cooldown
                )
                if discovered:
                    return QueueItem(
                        video_id=discovered.video_id,
                        title=discovered.title,
                        artist=discovered.artist,
                        discovery_source=discovered.strategy,
                        discovery_reason=discovered.reason,
                        for_user_id=discovered.for_user_id,
                        duration_seconds=discovered.duration_seconds,
                        genre=discovered.genre,
                        year=discovered.year,
                    )
            except Exception as e:
                logger.error(f"Discovery engine error: {e}")
        else:
            logger.warning("Discovery engine not initialized")
        
        # Fallback: Get random track from charts
        logger.info(f"Discovery failed or returned None for guild {player.guild_id}. Falling back to charts.")
        return await self._get_chart_fallback()
    
    async def _get_chart_fallback(self) -> QueueItem | None:
        """Get a random track from Top 100 US/UK charts as fallback."""
        import random
        
        region = random.choice(["US", "UK"])
        query = f"Top 100 Songs {region} 2024"
        
        logger.info(f"Searching for chart playlist: {query}")
        
        # Try to find a chart playlist
        playlists = await self.youtube.search_playlists(query, limit=3)
        
        if playlists:
            playlist = random.choice(playlists)
            logger.info(f"Found chart playlist: {playlist.get('title', 'Unknown')}")
            
            # Get tracks from playlist
            tracks = await self.youtube.get_playlist_tracks(playlist["browse_id"], limit=50)
            if tracks:
                track = random.choice(tracks)
                return QueueItem(
                    video_id=track.video_id,
                    title=track.title,
                    artist=track.artist,
                    discovery_source="wildcard",
                    discovery_reason=f"🎲 Random from {region} Top 100",
                    duration_seconds=track.duration_seconds,
                    year=track.year
                )
        
        # Direct search fallback - search for popular songs
        logger.info("Playlist not found, trying direct search")
        results = await self.youtube.search("top hits 2024 popular", filter_type="songs", limit=20)
        
        if results:
            track = random.choice(results)
            logger.info(f"Found fallback track via search: {track.title}")
            return QueueItem(
                video_id=track.video_id,
                title=track.title,
                artist=track.artist,
                discovery_source="wildcard",
                discovery_reason="🎲 Popular track from charts",
                duration_seconds=track.duration_seconds,
                year=track.year
            )
        
        logger.warning("Could not find any chart tracks via playlist OR direct search")
        return None
    
    async def _send_dj_script(self, player: GuildPlayer, item: QueueItem):
        """Generate and send/edit a DJ script for the current song."""
        # 1. Check if we have a text channel
        if not player.text_channel_id:
            return
            
        channel = self.bot.get_channel(player.text_channel_id)
        if not channel:
            return

        # 2. Fetch Guild Settings for Groq and TTS
        groq_enabled = True
        groq_send_text = True
        groq_offset = 0
        groq_offset = 0
        groq_custom_prompts = []
        groq_model = None
        tts_enabled = False
        tts_voice = "en_us_001"
        tts_slow = False

        if hasattr(self.bot, "db") and self.bot.db:
            try:
                from src.database.crud import GuildCRUD, SystemCRUD
                guild_crud = GuildCRUD(self.bot.db)
                system_crud = SystemCRUD(self.bot.db)
                
                # Fetch guild-specific settings
                guild_settings = await guild_crud.get_all_settings(player.guild_id)
                groq_enabled = guild_settings.get("groq_enabled", True)
                groq_send_text = guild_settings.get("groq_send_text", True)
                groq_offset = int(guild_settings.get("groq_offset", 0))
                groq_offset = int(guild_settings.get("groq_offset", 0))
                groq_custom_prompts = guild_settings.get("groq_custom_prompts", [])
                groq_model = guild_settings.get("groq_model")
                groq_model_fallback = guild_settings.get("groq_model_fallback")
                
                tts_enabled = guild_settings.get("tts_enabled", False)
                tts_voice = guild_settings.get("tts_voice", "en_us_001")
                tts_slow = guild_settings.get("tts_slow", False)
                
                if not groq_enabled and not tts_enabled:
                    return
            except Exception as e:
                logger.error(f"Failed to fetch Groq/TTS settings: {e}")

        # 3. Apply Timing Offset
        if groq_offset > 0:
            await asyncio.sleep(groq_offset)
        elif groq_offset < 0:
            # For negative offset, we can't really go back in time, 
            # but usually this is used to start early if possible.
            # In this implementation, we just proceed immediately for negative.
            pass

        # 4. Generate Script (Non-blocking)
        try:
            # Check cache first
            if item.script_text:
                script_text = item.script_text
                logger.info(f"Using cached DJ script for {item.title}")
            else:
                # Determine which system prompt to use
                import random
                system_prompt = None
                if groq_custom_prompts:
                    # Handle both old (string) and new (dict) prompt formats
                    # We prioritize properly structured dicts now
                    valid_prompts = []
                    for p in groq_custom_prompts:
                        if isinstance(p, dict):
                            if p.get("enabled", True):
                                # If it has 'role' it's a new structure, pass the whole dict
                                if "role" in p:
                                    valid_prompts.append(p)
                                # Fallback for old style dicts if any exist during migration
                                elif "text" in p:
                                    valid_prompts.append(p["text"])
                        elif isinstance(p, str):
                            valid_prompts.append(p)
                    
                    if valid_prompts:
                        system_prompt = random.choice(valid_prompts)
                
                # Call Groq Service
                # Note: generate_script now returns a DICT with metadata+text
                result = await self.groq.generate_script(
                    item.title, 
                    item.artist, 
                    system_prompt=system_prompt, 
                    model=groq_model,
                    fallback_model=groq_model_fallback
                )
                
                if result and "text" in result:
                    script_text = result["text"]
                    # We could also update metadata here if it was missing?
                    # But usually this happens in _resolve_metadata now.
                    logger.info(f"Requested DJ script with model: {groq_model}")
                else:
                    script_text = None
            
            if not script_text:
                return
                
            # 5. TTS Output
            if tts_enabled:
                # We need a voice channel to speak in
                if player.voice_client and player.voice_client.channel:
                    await self.tts.speak(
                        guild_id=player.guild_id,
                        channel_id=player.voice_client.channel.id,
                        message=script_text,
                        voice=tts_voice,
                        slow=tts_slow
                    )

            # 6. Text Output
            if groq_send_text:
                content = f"```\n{script_text}\n```"
                
                msg_sent = False
                if player.last_script_msg:
                    try:
                        await player.last_script_msg.edit(content=content)
                        msg_sent = True
                    except (discord.NotFound, discord.Forbidden):
                        player.last_script_msg = None
                    except Exception as e:
                        logger.warning(f"Failed to edit DJ script message: {e}")
                
                if not msg_sent:
                    try:
                        player.last_script_msg = await channel.send(content)
                    except Exception as e:
                        logger.error(f"Failed to send DJ script message: {e}")
                        
        except Exception as e:
            logger.error(f"Error in DJ script flow: {e}")

    async def _send_now_playing(self, player: GuildPlayer):
        """Send the Now Playing embed to the text channel."""
        if not player.current or not player.text_channel_id:
            return
        
        channel = self.bot.get_channel(player.text_channel_id)
        if not channel:
            return
            
        # Smart Update Logic: Check if we can just edit the last message
        can_edit = False
        if player.last_np_msg:
            try:
                # Check if the last message in the channel is our NP message
                async for message in channel.history(limit=1):
                    if message.id == player.last_np_msg.id:
                        can_edit = True
                    break
            except Exception as e:
                logger.debug(f"Failed to check channel history: {e}")

        # If we can't edit, delete the old message if it exists
        if not can_edit and player.last_np_msg:
            try:
                await player.last_np_msg.delete()
            except:
                pass
            player.last_np_msg = None
        
        try:
            item = player.current
            
            embed = discord.Embed(
                title="🎵 Now Playing",
                color=discord.Color.from_rgb(124, 58, 237)
            )
            
            embed.add_field(name="🎶 Track", value=f"**{item.title}**", inline=True)
            embed.add_field(name="🎤 Artist", value=item.artist, inline=True)
            
            if item.duration_seconds:
                minutes, seconds = divmod(item.duration_seconds, 60)
                duration_str = f"{minutes}:{seconds:02d}"
                embed.add_field(name="⏳ Duration", value=duration_str, inline=True)
            
            if item.genre:
                embed.add_field(name="🏷️ Genre", value=item.genre, inline=True)
            
            if item.year:
                embed.add_field(name="📅 Year", value=str(item.year), inline=True)
            
            if item.discovery_reason:
                embed.add_field(name="✨ Discovery", value=item.discovery_reason, inline=False)
            
            if item.for_user_id:
                embed.add_field(name="🎯 Playing for", value=f"<@{item.for_user_id}>", inline=True)
            elif item.requester_id:
                embed.add_field(name="📨 Requested by", value=f"<@{item.requester_id}>", inline=True)
            
            embed.set_thumbnail(url=f"https://img.youtube.com/vi/{item.video_id}/hqdefault.jpg")
            
            embed.add_field(name="📜 Queue", value=f"{player.queue.qsize()} songs", inline=True)
            yt_url = f"https://youtube.com/watch?v={item.video_id}"
            embed.add_field(name="🔗 Link", value=f"[YouTube]({yt_url})", inline=True)
            
            # ⏭️ NEXT SONG DETAILS
            if not player.queue.empty():
                # PriorityQueue stores (priority, counter, item)
                _, _, next_item = list(player.queue._queue)[0]
                
                # Format next duration
                next_dur_str = "Unknown"
                if next_item.duration_seconds:
                    m, s = divmod(next_item.duration_seconds, 60)
                    next_dur_str = f"{m}:{s:02d}"
                else:
                    # Try proactive fetch for next item if missing (since we are here anyway)
                    # Note: _prepare_next_song usually handles this but we want to be SURE
                    details = await self.youtube.get_track_info(next_item.video_id)
                    if details and details.duration_seconds:
                        next_item.duration_seconds = details.duration_seconds
                        m, s = divmod(next_item.duration_seconds, 60)
                        next_dur_str = f"{m}:{s:02d}"

                # Format "For Who"
                next_for = "Nobody"
                if next_item.for_user_id:
                    next_for = f"<@{next_item.for_user_id}>"
                elif next_item.requester_id:
                    next_for = f"<@{next_item.requester_id}>"

                # Format "Reason" (Strategy)
                next_reason = next_item.discovery_reason or "Requested"
                
                next_details = (
                    f"**{next_item.title}**\n"
                    f"🎤 {next_item.artist} | ⏳ {next_dur_str}\n"
                    f"🎯 For: {next_for}\n"
                    f"✨ Why: {next_reason}"
                )
                embed.add_field(name="⏭️ Up Next", value=next_details, inline=False)
            
            # Create view with buttons
            view = NowPlayingView(self, player.guild_id)
            
            if can_edit and player.last_np_msg:
                try:
                    await player.last_np_msg.edit(embed=embed, view=view)
                except discord.NotFound:
                    # Message was deleted since we checked
                    player.last_np_msg = await channel.send(embed=embed, view=view)
                except Exception as e:
                    logger.debug(f"Failed to edit Now Playing embed: {e}")
                    player.last_np_msg = await channel.send(embed=embed, view=view)
            else:
                player.last_np_msg = await channel.send(embed=embed, view=view)
            
            # Save the message ID to the database session
            if player.session_id and player.last_np_msg:
                try:
                    from src.database.crud import PlaybackCRUD
                    playback_crud = PlaybackCRUD(self.bot.db)
                    await playback_crud.update_session_message(player.session_id, player.last_np_msg.id)
                except Exception as e:
                    logger.debug(f"Failed to record NP message ID in DB: {e}")
        except Exception as e:
            logger.debug(f"Failed to send Now Playing embed: {e}")
    
    async def _prepare_next_song(self, player: GuildPlayer):
        """
        Ensures the next song is ready for playback.
        Triggers discovery if queue is empty and extracts URLs in advance.
        """
        try:
            # 1. If queue is empty, trigger discovery immediately
            if player.queue.empty():
                if not player.autoplay:
                    return
                # We need to find a song within duration limit
                max_seconds = 0
                if hasattr(self.bot, "db"):
                    try:
                        from src.database.crud import GuildCRUD
                        guild_crud = GuildCRUD(self.bot.db)
                        max_dur = await guild_crud.get_setting(player.guild_id, "max_song_duration")
                        
                        # Default to 6 minutes if not set
                        if max_dur is None:
                            max_dur = "6"
                            
                        if max_dur:
                            max_seconds = int(max_dur) * 60
                    except: pass
                
                logger.info(f"Proactive discovery triggered for guild {player.guild_id}")
                for _ in range(3):
                    item = await self._get_discovery_song(player)
                    if not item:
                        break
                    
                    if max_seconds > 0 and item.duration_seconds and item.duration_seconds > max_seconds:
                        logger.info(f"Skipping proactive discovery song {item.title} (duration {item.duration_seconds}s > {max_seconds}s)")
                        continue
                    
                    # Add to queue (Priority 1: Autoplay)
                    player._queue_counter += 1
                    player.queue.put_nowait((1, player._queue_counter, item))
                    
                    # USER REQUEST: Log confirmed proactive discovery item
                    logger.info(f"⏭️ Next song confirmed for guild {player.guild_id}: {item.title} by {item.artist} | Strategy: {item.discovery_source} ({item.discovery_reason})")
                    break

            # 2. Extract stream URL for the first item in queue if missing
            # Only do this if pre_buffer setting is on, OR if we just added it to an empty queue as discovery
            if not player.queue.empty():
                # Peek at next item without removing
                # PriorityQueue stores (priority, counter, item)
                _, _, next_item = list(player.queue._queue)[0]
                
                if not next_item.url:
                    # We always extract for the direct next song if it was a discovery item
                    # to ensure gapless, even if pre-buffering is 'off' for resource reasons
                    # because the user explicitly asked for 'lowest gap'
                    url = await self.youtube.get_stream_url(next_item.video_id)
                    if url:
                        next_item.url = url
                        logger.debug(f"Gapless Pre-fetch: Prepared URL for: {next_item.title}")
                        
                        # Pre-resolve metadata to avoid gaps later
                        try:
                            # Ensure song exists in DB so we can save the genre
                            if not next_item.song_db_id:
                                from src.database.crud import SongCRUD
                                song_crud = SongCRUD(self.bot.db)
                                # Create or get existing
                                song = await song_crud.get_or_create_by_yt_id(
                                    canonical_yt_id=next_item.video_id,
                                    title=next_item.title,
                                    artist_name=next_item.artist,
                                    duration_seconds=next_item.duration_seconds
                                )
                                next_item.song_db_id = song["id"]

                            # We need config
                            metadata_config = None
                            if hasattr(self.bot, "db") and self.bot.db:
                                from src.database.crud import GuildCRUD
                                guild_crud = GuildCRUD(self.bot.db)
                                metadata_config = await guild_crud.get_setting(player.guild_id, "metadata_config")
                            
                            await self._resolve_metadata(next_item, metadata_config)
                            logger.debug(f"Gapless Pre-fetch: Resolved metadata for: {next_item.title}")
                        except Exception as e:
                            logger.warning(f"Gapless Pre-fetch metadata failed: {e}")
                    
        except Exception as e:
            logger.debug(f"Song preparation failed: {e}")

    async def _end_session(self, player: GuildPlayer):
        """End a playback session, send recap, and cleanup."""
        if not player.session_id:
            return

        session_id = player.session_id
        player.session_id = None
        player.autoplay = False # Stop discovery

        if not hasattr(self.bot, "db") or not self.bot.db:
            return

        try:
            async with asyncio.timeout(5):
                playback_crud = PlaybackCRUD(self.bot.db)
                analytics_crud = AnalyticsCRUD(self.bot.db)
                
                # 1. End in DB
                await playback_crud.end_session(session_id)
                
                # 2. Get Stats for Recap
                stats = await analytics_crud.get_session_stats(session_id)
                
                # 3. Send Recap Embed
                if player.text_channel_id:
                    channel = self.bot.get_channel(player.text_channel_id)
                    if channel:
                        embed = discord.Embed(
                            title="🏁 Session Recap",
                            description=f"This music session has concluded.",
                            color=discord.Color.from_rgb(124, 58, 237)
                        )
                        
                        # Formatting stats
                        total_tracks = stats.get("total_tracks", 0)
                        if total_tracks > 0:
                            total_secs = stats.get("total_seconds") or 0
                            mins, secs = divmod(total_secs, 60)
                            hours, mins = divmod(mins, 60)
                            
                            duration_str = f"{secs}s"
                            if mins > 0: duration_str = f"{mins}m {duration_str}"
                            if hours > 0: duration_str = f"{hours}h {duration_str}"
                            
                            embed.add_field(name="📊 Stats", value=f"**{total_tracks}** tracks played\n**{duration_str}** total time", inline=True)
                            embed.add_field(name="👥 Listeners", value=f"**{stats.get('unique_listeners', 0)}** unique users", inline=True)
                            
                            if stats.get("top_artist"):
                                embed.add_field(name="🎤 Top Artist", value=stats["top_artist"], inline=True)
                            
                            if stats.get("top_genre"):
                                embed.add_field(name="🏷️ Top Genre", value=stats["top_genre"], inline=True)
                            
                            # Discovery breakdown
                            breakdown = stats.get("discovery_breakdown", {})
                            if breakdown:
                                requested = breakdown.get("user_request", 0)
                                discovered = sum(v for k, v in breakdown.items() if k != "user_request")
                                total = requested + discovered
                                if total > 0:
                                    req_pct = round((requested / total) * 100)
                                    disc_pct = 100 - req_pct
                                    embed.add_field(
                                        name="✨ Discovery Rate", 
                                        value=f"🙋 {req_pct}% Requests\n🎲 {disc_pct}% Autoplay", 
                                        inline=False
                                    )
                        else:
                            embed.description = "No tracks were played during this session."

                        embed.set_footer(text="Vexo Music • Quality Audio Discovery")
                        embed.timestamp = datetime.now(UTC)

                        # Update Now Playing message if possible, else send new
                        updated = False
                        if player.last_np_msg:
                            try:
                                await player.last_np_msg.edit(embed=embed, view=SessionEndedView(self, player.guild_id))
                                updated = True
                            except: pass
                        
                        if not updated:
                            await channel.send(embed=embed, view=SessionEndedView(self, player.guild_id))
                        
                        player.last_np_msg = None
        except TimeoutError:
            logger.warning(f"Session end processing timed out for guild {player.guild_id}")
        except Exception as e:
            logger.error(f"Failed to generate session recap: {e}")

    async def send_recap_for_session(self, session_id: str, guild_id: int):
        """Send a recap for a session that has ended (especially for stale sessions)."""
        if not hasattr(self.bot, "db") or not self.bot.db:
            return

        try:
            analytics_crud = AnalyticsCRUD(self.bot.db)
            stats = await analytics_crud.get_session_stats(session_id)
            
            guild = self.bot.get_guild(guild_id)
            if not guild:
                return

            # Try to find the original text channel
            channel = None
            if stats.get("channel_id"):
                 try:
                     channel = guild.get_channel(int(stats["channel_id"]))
                 except: pass

            if not channel:
                # Fallback: Find system channel or first available
                channel = guild.system_channel
                # Need both send_messages AND embed_links
                if not channel or not (channel.permissions_for(guild.me).send_messages and channel.permissions_for(guild.me).embed_links):
                    # Find first channel we can send to with embeds
                    channels = [
                        c for c in guild.text_channels 
                        if c.permissions_for(guild.me).send_messages and c.permissions_for(guild.me).embed_links
                    ]
                    if not channels:
                        logger.warning(f"Could not find a suitable text channel to send recap in guild {guild.name}")
                        return
                    channel = channels[0]

            embed = discord.Embed(
                title="🏁 Interrupted Session Recap",
                description=f"This session was recently recovered and closed.",
                color=discord.Color.orange()
            )
            
            total_tracks = stats.get("total_tracks", 0)
            if total_tracks > 0:
                total_secs = stats.get("total_seconds") or 0
                mins, secs = divmod(total_secs, 60)
                hours, mins = divmod(mins, 60)
                
                duration_str = f"{secs}s"
                if mins > 0: duration_str = f"{mins}m {duration_str}"
                if hours > 0: duration_str = f"{hours}h {duration_str}"
                
                embed.add_field(name="📊 Stats", value=f"**{total_tracks}** tracks played\n**{duration_str}** total time", inline=True)
                embed.add_field(name="👥 Listeners", value=f"**{stats.get('unique_listeners', 0)}** unique users", inline=True)
                
                if stats.get("top_artist"):
                    embed.add_field(name="🎤 Top Artist", value=stats["top_artist"], inline=True)
                
                # Discovery breakdown
                breakdown = stats.get("discovery_breakdown", {})
                if breakdown:
                    requested = breakdown.get("user_request", 0)
                    discovered = sum(v for k, v in breakdown.items() if k != "user_request")
                    total = requested + discovered
                    if total > 0:
                        req_pct = round((requested / total) * 100)
                        disc_pct = 100 - req_pct
                        embed.add_field(
                            name="✨ Discovery Rate", 
                            value=f"🙋 {req_pct}% Requests\n🎲 {disc_pct}% Autoplay", 
                            inline=False
                        )
            else:
                embed.description = "This session ended abruptly with no tracks played."

            embed.set_footer(text="Vexo Music • Quality Audio Discovery")
            embed.timestamp = datetime.now(UTC)

            # Try to delete the old Now Playing message if we have its ID
            last_msg_id = stats.get("last_message_id")
            if last_msg_id:
                try:
                    old_msg = await channel.fetch_message(int(last_msg_id))
                    if old_msg:
                        await old_msg.delete()
                except Exception:
                    pass

            await channel.send(embed=embed, view=SessionEndedView(self, guild_id))

        except Exception as e:
            logger.error(f"Failed to send stale session recap: {e}")
    
    async def _idle_check_loop(self):
        """Check for idle players and disconnect."""
        while True:
            await asyncio.sleep(60)  # Check every minute
            
            now = datetime.now(UTC)
            for guild_id, player in list(self.players.items()):
                if player.voice_client and player.voice_client.is_connected():
                    # Check if idle for too long
                    if not player.is_playing and (now - player.last_activity).seconds > self.IDLE_TIMEOUT:
                        logger.info(f"Disconnecting from {guild_id} due to inactivity")
                        
                        # Check 24/7 mode before disconnecting for inactivity
                        is_247 = False
                        if hasattr(self.bot, "db") and self.bot.db:
                            try:
                                from src.database.crud import GuildCRUD
                                guild_crud = GuildCRUD(self.bot.db)
                                is_247 = await guild_crud.get_setting(guild_id, "twenty_four_seven")
                            except: pass
                        
                        if is_247:
                            # In 24/7 mode, we still "end session" if it was discovery and we want a break, 
                            # but we don't disconnect.
                            # However, 24/7 mode usually implies we KEEP PLAYING.
                            # If autoplay is on, it shouldn't even reach idle.
                            # If it reached idle, it means autoplay is off.
                            if player.autoplay:
                                player.last_activity = datetime.now(UTC)
                                continue
                            
                            logger.info(f"Bot is idle in {guild_id}, but 24/7 mode is active. Staying connected.")
                            continue

                        await self._end_session(player)
                        await player.voice_client.disconnect()
                        player.voice_client = None
    
    # ==================== EVENTS ====================
    
    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState
    ):
        """Handle voice state changes."""
        if member.bot:
            return
        
        player = self.players.get(member.guild.id)
        if not player or not player.voice_client:
            return
        
        # Handle session listener joins/leaves
        if player.session_id and hasattr(self.bot, "db"):
            try:
                playback_crud = PlaybackCRUD(self.bot.db)
                user_crud = UserCRUD(self.bot.db)
                
                # Case: Joined our channel
                if after.channel and player.voice_client and after.channel.id == player.voice_client.channel.id:
                    if not before.channel or before.channel.id != after.channel.id:
                        await user_crud.get_or_create(member.id, member.name)
                        await playback_crud.add_listener(player.session_id, member.id)
                
                # Case: Left our channel
                elif before.channel and player.voice_client and before.channel.id == player.voice_client.channel.id:
                    if not after.channel or after.channel.id != before.channel.id:
                        await playback_crud.remove_listener(player.session_id, member.id)
            except Exception as e:
                logger.debug(f"Failed to update listener status: {e}")

        # Check if bot is alone in voice channel
        if player.voice_client and player.voice_client.channel:
            members = [m for m in player.voice_client.channel.members if not m.bot]
            if not members:
                # Check for 24/7 mode
                is_247 = False
                if hasattr(self.bot, "db") and self.bot.db:
                    try:
                        from src.database.crud import GuildCRUD
                        guild_crud = GuildCRUD(self.bot.db)
                        is_247 = await guild_crud.get_setting(member.guild.id, "twenty_four_seven")
                    except: pass
                
                if is_247:
                    logger.info(f"Everyone left {member.guild.name}, but 24/7 mode is ON. Staying.")
                    return

                # Everyone left, stop and disconnect
                if player.voice_client.is_playing():
                    player.voice_client.stop()
                
                await self._end_session(player)
                await player.voice_client.disconnect()
                player.voice_client = None
                logger.info(f"Disconnected from {member.guild.name} - everyone left")

    async def _auto_connect_loop(self):
        """Check and handle AutoConnect settings for guilds."""
        while True:
            await asyncio.sleep(60) # Check every minute
            
            if not hasattr(self.bot, "db") or not self.bot.db:
                continue

            try:
                from src.database.crud import GuildCRUD
                guild_crud = GuildCRUD(self.bot.db)

                # Fetch all guilds the bot is in
                for guild in self.bot.guilds:
                    # Check if bot is already in a voice channel in this guild
                    if guild.voice_client:
                        continue

                    # Check settings
                    settings = await guild_crud.get_all_settings(guild.id)
                    auto_connect = settings.get("auto_connect", False)
                    auto_channel_id = settings.get("auto_connect_channel")

                    if auto_connect and auto_channel_id:
                        channel = guild.get_channel(int(auto_channel_id))
                        if channel and isinstance(channel, discord.VoiceChannel):
                            logger.info(f"AutoConnect: Joining channel {channel.name} in {guild.name}")
                            player = self.get_player(guild.id)
                            try:
                                player.voice_client = await channel.connect(self_deaf=True, timeout=20.0)
                                player.autoplay = True
                                player.text_channel_id = channel.id # Initial text channel can be same as voice or a dedicated one
                                
                                if not player.is_playing:
                                    player.is_playing = True
                                    asyncio.create_task(self._play_loop(player))
                            except Exception as e:
                                logger.error(f"AutoConnect failed in {guild.name}: {e}")

            except Exception as e:
                logger.error(f"Error in AutoConnect loop: {e}")

    async def _resolve_metadata(self, item: QueueItem, config: dict | None):
        """
        Resolve metadata (Genre/Year) using all available sources to find the earliest year and best genre.
        """
        # Strict One-Time Check
        if item.metadata_attempted:
            logger.debug(f"Metadata resolution already attempted for '{item.title}'. Skipping.")
            return
        
        item.metadata_attempted = True

        # Optimize: Return early if metadata is already present AND validated
        if item.genre and item.year:
            logger.debug(f"Metadata already resolved for '{item.title}' (Genre: {item.genre}, Year: {item.year}). Skipping.")
            return

        logger.debug(f"DEBUG: _resolve_metadata called for '{item.title}'")

        # Default Config
        if not config:
            config = {
                "strategy": "consensus", # Default to consensus for accuracy
                "engines": {
                    "spotify": {"enabled": True, "priority": 1},
                    "discogs": {"enabled": True, "priority": 2},
                    "musicbrainz": {"enabled": True, "priority": 3}
                }
            }
        
        engines_config = config.get("engines", {})
        
        # Sort enabled engines by priority
        active_engines = []
        for name, settings in engines_config.items():
            if settings.get("enabled", True):
                active_engines.append((name, settings.get("priority", 99)))
        
        active_engines.sort(key=lambda x: x[1]) # Sort by priority
        
        if not active_engines:
            return

        # Prepare Clean Query
        clean_title = self.bot.normalizer.clean_title(item.title)
        logger.info(f"Resolving metadata for '{item.title}' (Clean: '{clean_title}') using sources: {[e[0] for e in active_engines]}")

        # --- Helper Functions (Now returning dicts) ---
        async def fetch_spotify():
            spotify = getattr(self.bot, "spotify", None)
            if not spotify: return {"genres": [], "year": None}
            try:
                # Use Clean Title!
                query = f"{item.artist} {clean_title}"
                sp_track = await spotify.search_track(query)
                if sp_track:
                    genres = []
                    artist = await spotify.get_artist(sp_track.artist_id)
                    if artist and artist.genres:
                        genres = artist.genres
                    
                    return {"genres": genres, "year": sp_track.release_year}
            except Exception as e:
                logger.error(f"Spotify fetch failed: {e}")
            return {"genres": [], "year": None}

        async def fetch_discogs():
            if not hasattr(self, "discogs"): return {"genres": [], "year": None}
            try:
                return await self.discogs.get_metadata(item.artist, clean_title)
            except Exception as e:
                logger.error(f"Discogs fetch failed: {e}")
            return {"genres": [], "year": None}

        async def fetch_musicbrainz():
            if not hasattr(self, "musicbrainz"): return {"genres": [], "year": None}
            try:
                return await self.bot.loop.run_in_executor(
                    None, self.musicbrainz.get_metadata, item.artist, clean_title
                )
            except Exception as e:
                logger.error(f"MusicBrainz fetch failed: {e}")
            return {"genres": [], "year": None}

        async def fetch_groq_metadata():
            g_crud = GuildCRUD(self.bot.db)
            # This is a bit expensive doing it per song?
            # Maybe just fetch specific keys
            guild_settings = await g_crud.get_all_settings(item.guild_id)
            
            if not guild_settings.get("groq_enabled", True):
                return {"genres": [], "year": None}

            groq_model = guild_settings.get("groq_model")
            groq_model_fallback = guild_settings.get("groq_model_fallback")
            groq_custom_prompts = guild_settings.get("groq_custom_prompts", [])
            
            # Select Prompt
            import random
            system_prompt = None
            if groq_custom_prompts:
                 valid_prompts = []
                 for p in groq_custom_prompts:
                    if isinstance(p, dict):
                        if p.get("enabled", True):
                            if "role" in p: valid_prompts.append(p)
                            elif "text" in p: valid_prompts.append(p["text"])
                    elif isinstance(p, str):
                        valid_prompts.append(p)
                 if valid_prompts:
                    system_prompt = random.choice(valid_prompts)

            try:
                # Generate!
                result = await self.bot.groq.generate_script(
                    item.title, 
                    item.artist, 
                    system_prompt=system_prompt, 
                    model=groq_model,
                    fallback_model=groq_model_fallback
                )
                
                if result:
                    # Store the script logic IMMEDIATELY
                    if "text" in result:
                        item.script_text = result["text"]
                        
                    # Return metadata votes
                    genres = []
                    if result.get("genre"):
                        genres.append(result["genre"])
                    
                    year = None
                    if result.get("release_date"):
                        try:
                            # Clean up year (sometimes "2023" or "Released 2023")
                            import re
                            # Find 4 digits
                            match = re.search(r'\b(19|20)\d{2}\b', str(result["release_date"]))
                            if match:
                                year = int(match.group(0))
                        except: pass
                        
                    return {"genres": genres, "year": year}
                    
            except Exception as e:
                logger.error(f"Groq metadata fetch failed: {e}")
            return {"genres": [], "year": None}

        engine_map = {
            "spotify": fetch_spotify,
            "discogs": fetch_discogs,
            "musicbrainz": fetch_musicbrainz,
            "groq": fetch_groq_metadata
        }
        
        # Add Groq to active engines if not present but 'groq' source enabled in config?
        # Actually, user needs to enable it in 'engines' config.
        # But for now, let's inject it if not present, as low priority?
        # No, let's respect the config. If user wants Groq metadata, they add it to metadata_config.
        # BUT... we want the script pre-generation to happen ALWAYS if Groq is enabled.
        # So we should force run it even if it's not in 'engines' for metadata voting.
        
        # Check if Groq is already in active_engines
        groq_in_engines = any(e[0] == "groq" for e in active_engines)
        if not groq_in_engines:
            # Add it as a hidden task just for script generation?
            # Or just append it with low priority
            active_engines.append(("groq", 999))
            
        # Execute Parallel Requests (Consensus/Aggregation)
        tasks = []
        source_names = []
        
        for name, _ in active_engines:
            if name in engine_map:
                # MUST wrap coroutines in Tasks for asyncio.wait in Python 3.11+
                # AND to keep track of them for result mapping
                task = asyncio.create_task(engine_map[name]())
                tasks.append(task)
                source_names.append(name)
        
        if not tasks:
            return

        mapped_results = []
        try:
            # Use asyncio.wait to support partial results (timeout=5.0)
            # This allows us to keep Spotify/Discogs results even if MusicBrainz hangs
            done, pending = await asyncio.wait(tasks, timeout=5.0)
            
            # Cancel pending (stuck) tasks
            for p in pending:
                p.cancel()
            
            # Reconstruct results IN ORDER of source_names
            # This is critical because aggregation logic uses index matching
            for i, task in enumerate(tasks):
                try:
                    if task in done and not task.cancelled():
                        exc = task.exception()
                        if exc:
                            logger.warning(f"Metadata start task for {source_names[i]} failed: {exc}")
                            mapped_results.append(exc)
                        else:
                            mapped_results.append(task.result())
                    else:
                        logger.warning(f"Metadata source {source_names[i]} timed out or was cancelled")
                        mapped_results.append(None)
                except Exception as e:
                    logger.error(f"Error retrieving task result for {source_names[i]}: {e}")
                    mapped_results.append(e)

        except Exception as e:
            logger.error(f"Metadata resolution critical failure: {e}")
            return
        
        # Aggregation Logic
        found_years = []
        genre_votes = Counter()
        
        for i, res in enumerate(mapped_results):
            source = source_names[i]
            if isinstance(res, Exception):
                logger.warning(f"Engine {source} failed: {res}")
                continue
            
            if not res:
                continue
                
            # Collect Year
            y = res.get("year")
            if y and isinstance(y, int) and y > 1900 and y <= datetime.now().year + 1:
                found_years.append(y)
                logger.info(f"Source {source} returned year: {y}")
            
            # Collect Genres
            g_list = res.get("genres", [])
            for g in g_list:
                normalized = str(g).title()
                genre_votes[normalized] += 1
        
        # 1. Update Year (Consensus: Most Common Year Wins, Earliest Year breaks ties)
        if found_years:
            # Count occurrences of each year
            year_counts = Counter(found_years)
            
            # Find the maximum count
            max_count = max(year_counts.values())
            
            # Get all years that have the maximum count (candidates for tie-breaking)
            candidates = [year for year, count in year_counts.items() if count == max_count]
            
            # Tie-breaker: Pick the earliest year among the candidates
            consensus_year = min(candidates)
            
            logger.info(f"Year Consensus Analysis: found={found_years}, counts={dict(year_counts)}, candidates={candidates}, winner={consensus_year}")

            if not item.year or consensus_year != item.year:
                logger.info(f"Updating year for '{item.title}': {item.year} -> {consensus_year} (Consensus Wrapper)")
                item.year = consensus_year
            else:
                 logger.info(f"Kept existing year {item.year} (Matches consensus)")
        
        # 2. Update Genre (Most Common)
        if genre_votes:
            winner, count = genre_votes.most_common(1)[0]
            if not item.genre or (item.discovery_source == "wildcard" and count > 1):
                 # Overwrite if we have no genre, or if we have a strong consensus
                 item.genre = winner
                 logger.info(f"Consensus genre winner: {winner} ({count} votes)")
        
        # 3. Persistence
        if (item.year or item.genre) and item.song_db_id and hasattr(self.bot, "db"):
             try:
                from src.database.crud import SongCRUD
                song_crud = SongCRUD(self.bot.db)
                
                # Update genre
                if item.genre:
                    await song_crud.clear_genres(item.song_db_id)
                    await song_crud.add_genre(item.song_db_id, item.genre)
                
                # Update year
                if item.year:
                    # We can't easily update just the year with crud.get_or_create_by_yt_id without refetching logic
                    # So we'll run a direct update query or use the crud smartly
                    # Ideally SongCRUD should have update_metadata(id, year=...)
                    # For now, re-calling get_or_create with the NEW year should update it if implemented right, 
                    # or we can add a specific update method. 
                    # Checking SongCRUD... it usually updates on conflict or we can just ignore for now if too complex.
                    # Let's try to update via get_or_create which might handle upsert
                    await song_crud.get_or_create_by_yt_id(
                        canonical_yt_id=item.video_id,
                        title=item.title,
                        artist_name=item.artist,
                        release_year=item.year
                    )
             except Exception as e:
                logger.error(f"Failed to persist resolved metadata: {e}")

class SessionEndedView(discord.ui.View):
    """View shown when a playback session has ended."""
    def __init__(self, cog, guild_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id

    @discord.ui.button(label="Start New Session", emoji="🎲", style=discord.ButtonStyle.success)
    async def relaunch(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Relaunch discovery session."""
        player = self.cog.get_player(self.guild_id)
        
        # 1. Update the current message to remove buttons (make it static)
        try:
            # Create a static version of the embed
            embed = interaction.message.embeds[0]
            embed.title = "🏁 Playback Finished (Archived)"
            embed.color = discord.Color.default()
            await interaction.message.edit(embed=embed, view=None)
        except Exception as e:
            logger.debug(f"Failed to archieve session end message: {e}")

        # 2. Trigger /play any logic
        # This is similar to the code in play_any command
        if not interaction.user.voice:
            await interaction.response.send_message("❌ You must be in a voice channel to start a session!", ephemeral=True)
            return

        # Defer to allow discovery to work
        await interaction.response.defer()
        
        # Connect to voice if not connected
        if not player.voice_client:
            player.voice_client = await interaction.user.voice.channel.connect()
        
        player.autoplay = True
        player.text_channel_id = interaction.channel_id
        
        if not player.is_playing:
            player.is_playing = True
            asyncio.create_task(self.cog._play_loop(player))
            
            # Followup to confirm start
            duration = await self.cog._get_ephemeral_duration(self.guild_id)
            msg = await interaction.followup.send("🚀 Starting new discovery session!")
            try:
                await msg.delete(delay=duration)
            except: pass
        else:
            duration = await self.cog._get_ephemeral_duration(self.guild_id)
            msg = await interaction.followup.send("ℹ️ A session is already active!")
            try:
                await msg.delete(delay=duration)
            except: pass


async def setup(bot: commands.Bot):
    """Load the music cog."""
    await bot.add_cog(MusicCog(bot))

