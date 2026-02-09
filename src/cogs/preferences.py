"""
Preferences Cog - User preference commands
"""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.services.preferences import PreferenceManager, SongInfo

logger = logging.getLogger(__name__)


class PreferencesCog(commands.Cog):
    """User preference commands."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    @property
    def preferences(self) -> PreferenceManager | None:
        """Get preference manager from bot."""
        return getattr(self.bot, "preferences", None)
    
    @property
    def music_cog(self):
        """Get music cog for current song info."""
        return self.bot.get_cog("MusicCog")
    
    @app_commands.command(name="preferences", description="Show your music preferences")
    async def show_preferences(self, interaction: discord.Interaction):
        """Show user's learned preferences."""
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        
        if not self.preferences:
            await interaction.followup.send("❌ Preference system not initialized", ephemeral=True)
            return
        
        summary = await self.preferences.get_user_preferences_summary(interaction.user.id)
        
        embed = discord.Embed(
            title="🎵 Your Music Preferences",
            color=discord.Color.purple()
        )
        
        # Top genres
        if summary["top_genres"]:
            genres_text = "\n".join(
                f"• **{genre.title()}** ({score:.0%})"
                for genre, score in summary["top_genres"]
            )
            embed.add_field(name="🎸 Top Genres", value=genres_text, inline=True)
        else:
            embed.add_field(name="🎸 Top Genres", value="No data yet", inline=True)
        
        # Top artists
        if summary["top_artists"]:
            artists_text = "\n".join(
                f"• **{artist.title()}** ({score:.0%})"
                for artist, score in summary["top_artists"]
            )
            embed.add_field(name="🎤 Top Artists", value=artists_text, inline=True)
        else:
            embed.add_field(name="🎤 Top Artists", value="No data yet", inline=True)
        
        # Top decades
        if summary["top_decades"]:
            decades_text = ", ".join(
                f"**{decade}**"
                for decade, _ in summary["top_decades"]
            )
            embed.add_field(name="📅 Favorite Eras", value=decades_text, inline=False)
        
        embed.set_footer(text=f"Total preferences tracked: {summary['total_preferences']}")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
    
    @app_commands.command(name="like", description="Like the current song")
    async def like(self, interaction: discord.Interaction):
        """Like the currently playing song."""
        music = self.music_cog
        if not music:
            await interaction.response.send_message("❌ Music not available", ephemeral=True)
            return
        
        player = music.get_player(interaction.guild_id)
        if not player.current:
            await interaction.response.send_message("❌ Nothing is playing", ephemeral=True)
            return
        
        # Record the like
        if self.preferences:
            song = player.current
            # Get genres from database if available
            genres = []
            if hasattr(self.bot, "db") and self.bot.db:
                from src.database.crud import SongCRUD
                song_crud = SongCRUD(self.bot.db)
                if song.song_db_id:
                    genres = await song_crud.get_genres(song.song_db_id)
            
            song_info = SongInfo(
                song_id=song.song_db_id or 0,
                title=song.title,
                artist=song.artist,
                genres=genres,
            )
            await self.preferences.record_like(interaction.user.id, song_info)
            
            # Also record reaction in database
            if hasattr(self.bot, "db") and song.song_db_id:
                from src.database.crud import ReactionCRUD
                reaction_crud = ReactionCRUD(self.bot.db)
                await reaction_crud.add_reaction(interaction.user.id, song.song_db_id, "like")
        
        await interaction.response.send_message(
            f"❤️ Liked **{player.current.title}**!",
            ephemeral=True
        )
    
    @app_commands.command(name="dislike", description="Dislike the current song")
    async def dislike(self, interaction: discord.Interaction):
        """Dislike the currently playing song."""
        music = self.music_cog
        if not music:
            await interaction.response.send_message("❌ Music not available", ephemeral=True)
            return
        
        player = music.get_player(interaction.guild_id)
        if not player.current:
            await interaction.response.send_message("❌ Nothing is playing", ephemeral=True)
            return
        
        # Record the dislike
        if self.preferences:
            song = player.current
            genres = []
            if hasattr(self.bot, "db") and self.bot.db:
                from src.database.crud import SongCRUD
                song_crud = SongCRUD(self.bot.db)
                if song.song_db_id:
                    genres = await song_crud.get_genres(song.song_db_id)
            
            song_info = SongInfo(
                song_id=song.song_db_id or 0,
                title=song.title,
                artist=song.artist,
                genres=genres,
            )
            await self.preferences.record_dislike(interaction.user.id, song_info)
            
            if hasattr(self.bot, "db") and song.song_db_id:
                from src.database.crud import ReactionCRUD
                reaction_crud = ReactionCRUD(self.bot.db)
                await reaction_crud.add_reaction(interaction.user.id, song.song_db_id, "dislike")
        
        await interaction.response.send_message(
            f"👎 Disliked **{player.current.title}**",
            ephemeral=True
        )


async def setup(bot: commands.Bot):
    """Load the preferences cog."""
    await bot.add_cog(PreferencesCog(bot))
