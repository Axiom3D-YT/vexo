"""
Import Cog - Playlist import commands
"""
import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

from src.services.spotify import SpotifyService
from src.services.youtube import YouTubeService
from src.services.normalizer import SongNormalizer

logger = logging.getLogger(__name__)


class ImportCog(commands.Cog):
    """Playlist import commands."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    @app_commands.command(name="import", description="Import a playlist to learn your preferences")
    @app_commands.describe(url="Spotify or YouTube playlist URL")
    async def import_playlist(self, interaction: discord.Interaction, url: str):
        """Import a playlist and learn preferences from it."""
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        
        # Detect platform
        if "spotify.com" in url:
            await self._import_spotify(interaction, url)
        elif "youtube.com" in url or "youtu.be" in url or "music.youtube.com" in url:
            await self._import_youtube(interaction, url)
        else:
            await interaction.followup.send(
                "❌ Unrecognized URL. Please use a Spotify or YouTube playlist link.",
                ephemeral=True
            )
    
    async def _import_spotify(self, interaction: discord.Interaction, url: str):
        """Import a Spotify playlist."""
        from src.config import config
        
        spotify = SpotifyService(config.SPOTIFY_CLIENT_ID, config.SPOTIFY_CLIENT_SECRET)
        youtube = YouTubeService()
        normalizer = SongNormalizer(youtube)
        
        try:
            # Get playlist tracks
            await interaction.followup.send("📥 Fetching Spotify playlist...", ephemeral=True)
            tracks = await spotify.get_playlist_tracks(url)
            
            if not tracks:
                await interaction.edit_original_response(content="❌ No tracks found in playlist")
                return
            
            await interaction.edit_original_response(
                content=f"📥 Found {len(tracks)} tracks. Fetching artist genres..."
            )
            
            # Batch fetch artist genres
            artist_ids = list(set(t.artist_id for t in tracks))
            artists = await spotify.get_artists_batch(artist_ids)
            artist_genres = {a.artist_id: a.genres for a in artists}
            
            # Attach genres to tracks
            for track in tracks:
                track.genres = artist_genres.get(track.artist_id, [])
            
            await interaction.edit_original_response(
                content=f"📥 Learning preferences from {len(tracks)} tracks..."
            )
            
            # Learn preferences
            if hasattr(self.bot, "preferences") and self.bot.preferences:
                stats = await self.bot.preferences.learn_from_playlist(
                    interaction.user.id,
                    tracks
                )
                
                # Store imported playlist in database
                if hasattr(self.bot, "db") and self.bot.db:
                    from src.database.crud import SongCRUD, LibraryCRUD
                    song_crud = SongCRUD(self.bot.db)
                    lib_crud = LibraryCRUD(self.bot.db)
                    
                    # Record playlist metadata
                    await self.bot.db.execute(
                        """INSERT INTO imported_playlists 
                           (user_id, platform, platform_id, name, track_count)
                           VALUES (?, ?, ?, ?, ?)""",
                        (interaction.user.id, "spotify", url, "Spotify Playlist", len(tracks))
                    )
                    
                    # Record individual songs in library
                    for track in tracks:
                        try:
                            song = await song_crud.get_or_create_by_spotify_id(
                                spotify_id=track.spotify_id,
                                title=track.title,
                                artist_name=track.artist,
                                album=track.album,
                                release_year=track.release_year,
                                duration_seconds=track.duration_seconds
                            )
                            if song:
                                await lib_crud.add_to_library(interaction.user.id, song["id"], "import")
                                # Add genres if available
                                if track.genres:
                                    for g in track.genres:
                                        await song_crud.add_genre(song["id"], g, "spotify")
                        except Exception as e:
                            logger.error(f"Failed to record imported song {track.title}: {e}")
                
                await interaction.edit_original_response(
                    content=f"✅ **Playlist imported!**\n"
                            f"• Learned {stats['genres']} genres\n"
                            f"• Learned {stats['artists']} artists\n"
                            f"• Learned {stats['decades']} decades\n\n"
                            f"Use `/preferences` to see your updated tastes!"
                )
            else:
                await interaction.edit_original_response(
                    content="⚠️ Preference system not available"
                )
        
        except Exception as e:
            logger.error(f"Error importing Spotify playlist: {e}")
            await interaction.edit_original_response(content=f"❌ Error: {e}")
    
    async def _import_youtube(self, interaction: discord.Interaction, url: str):
        """Import a YouTube playlist."""
        from src.config import config
        
        youtube = YouTubeService()
        spotify = SpotifyService(config.SPOTIFY_CLIENT_ID, config.SPOTIFY_CLIENT_SECRET)
        
        # Extract playlist ID
        playlist_id = self._extract_yt_playlist_id(url)
        if not playlist_id:
            await interaction.followup.send(
                "❌ Could not extract playlist ID from URL",
                ephemeral=True
            )
            return
        
        try:
            await interaction.followup.send("📥 Fetching YouTube playlist...", ephemeral=True)
            tracks = await youtube.get_playlist_tracks(playlist_id, limit=100)
            
            if not tracks:
                await interaction.edit_original_response(content="❌ No tracks found in playlist")
                return
            
            await interaction.edit_original_response(
                content=f"📥 Found {len(tracks)} tracks. Looking up metadata..."
            )
            
            # For each track, try to get metadata from Spotify
            spotify_tracks = []
            for i, track in enumerate(tracks):
                if i % 10 == 0 and i > 0:
                    await interaction.edit_original_response(
                        content=f"📥 Processing track {i}/{len(tracks)}..."
                    )
                
                # Search Spotify for metadata
                sp_track = await spotify.search_track(f"{track.artist} {track.title}")
                if sp_track:
                    # Get artist genres
                    artist = await spotify.get_artist(sp_track.artist_id)
                    if artist:
                        sp_track.genres = artist.genres
                    spotify_tracks.append(sp_track)
            
            await interaction.edit_original_response(
                content=f"📥 Found metadata for {len(spotify_tracks)} tracks. Learning preferences..."
            )
            
            # Learn preferences
            if hasattr(self.bot, "preferences") and self.bot.preferences:
                stats = await self.bot.preferences.learn_from_playlist(
                    interaction.user.id,
                    spotify_tracks
                )
                
                # Store imported playlist
                if hasattr(self.bot, "db") and self.bot.db:
                    from src.database.crud import SongCRUD, LibraryCRUD
                    song_crud = SongCRUD(self.bot.db)
                    lib_crud = LibraryCRUD(self.bot.db)

                    await self.bot.db.execute(
                        """INSERT INTO imported_playlists 
                           (user_id, platform, platform_id, name, track_count)
                           VALUES (?, ?, ?, ?, ?)""",
                        (interaction.user.id, "youtube", playlist_id, "YouTube Playlist", len(tracks))
                    )

                    # Record individual tracks in library
                    for track in spotify_tracks:
                        try:
                            song = await song_crud.get_or_create_by_spotify_id(
                                spotify_id=track.spotify_id,
                                title=track.title,
                                artist_name=track.artist,
                                album=track.album,
                                release_year=track.release_year,
                                duration_seconds=track.duration_seconds
                            )
                            if song:
                                await lib_crud.add_to_library(interaction.user.id, song["id"], "import")
                                # Add genres
                                if track.genres:
                                    for g in track.genres:
                                        await song_crud.add_genre(song["id"], g, "spotify")
                        except Exception as e:
                            logger.error(f"Failed to record imported YT song {track.title}: {e}")
                
                await interaction.edit_original_response(
                    content=f"✅ **Playlist imported!**\n"
                            f"• Found {len(spotify_tracks)}/{len(tracks)} tracks on Spotify\n"
                            f"• Learned {stats['genres']} genres\n"
                            f"• Learned {stats['artists']} artists\n\n"
                            f"Use `/preferences` to see your updated tastes!"
                )
            else:
                await interaction.edit_original_response(
                    content="⚠️ Preference system not available"
                )
        
        except Exception as e:
            logger.error(f"Error importing YouTube playlist: {e}")
            await interaction.edit_original_response(content=f"❌ Error: {e}")
    
    def _extract_yt_playlist_id(self, url: str) -> str | None:
        """Extract playlist ID from YouTube URL."""
        # Patterns for YouTube playlist URLs
        patterns = [
            r"list=([a-zA-Z0-9_-]+)",  # Standard playlist param
            r"playlist/([a-zA-Z0-9_-]+)",  # YouTube Music format
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
        return None


async def setup(bot: commands.Bot):
    """Load the import cog."""
    await bot.add_cog(ImportCog(bot))
