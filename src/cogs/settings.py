"""
Settings Cog - Server settings commands
"""
import logging

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)


class SettingsCog(commands.Cog):
    """Server settings commands."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    settings_group = app_commands.Group(
        name="settings",
        description="Server settings",
        default_permissions=discord.Permissions(manage_guild=True)
    )
    
    @settings_group.command(name="prebuffer", description="Toggle pre-buffering for next song")
    @app_commands.describe(enabled="Enable or disable pre-buffering")
    async def prebuffer(self, interaction: discord.Interaction, enabled: bool):
        """Toggle pre-buffering for next song URL."""
        music = self.bot.get_cog("MusicCog")
        if music:
            player = music.get_player(interaction.guild_id)
            player.pre_buffer = enabled
        
        # Save to database
        if hasattr(self.bot, "db") and self.bot.db:
            from src.database.crud import GuildCRUD
            guild_crud = GuildCRUD(self.bot.db)
            await guild_crud.set_setting(interaction.guild_id, "prebuffer", enabled)
        
        status = "enabled" if enabled else "disabled"
        await interaction.response.send_message(
            f"⚡ Pre-buffering {status}\n"
            f"{'*May use more CPU/memory but reduces gaps between songs*' if enabled else '*Lower resource usage but may have brief gaps*'}",
            ephemeral=True
        )
    
    @settings_group.command(name="discovery_weights", description="Set discovery strategy weights")
    @app_commands.describe(
        similar="Weight for similar songs (0-100)",
        artist="Weight for same artist (0-100)",
        wildcard="Weight for wildcard/charts (0-100)",
        library="Weight for user library (0-100)"
    )
    async def discovery_weights(
        self,
        interaction: discord.Interaction,
        similar: int,
        artist: int,
        wildcard: int,
        library: int
    ):
        """Set discovery strategy weights for this server."""
        # Validate
        if not all(0 <= w <= 100 for w in [similar, artist, wildcard, library]):
            await interaction.response.send_message(
                "❌ All weights must be between 0 and 100",
                ephemeral=True
            )
            return
        
        total = similar + artist + wildcard + library
        if total == 0:
            await interaction.response.send_message(
                "❌ At least one weight must be greater than 0",
                ephemeral=True
            )
            return
        
        weights = {"similar": similar, "artist": artist, "wildcard": wildcard, "library": library}
        
        # Save to database
        if hasattr(self.bot, "db") and self.bot.db:
            from src.database.crud import GuildCRUD
            guild_crud = GuildCRUD(self.bot.db)
            await guild_crud.set_setting(interaction.guild_id, "discovery_weights", weights)
        
        # Calculate percentages
        pct_similar = (similar / total) * 100
        pct_artist = (artist / total) * 100
        pct_wildcard = (wildcard / total) * 100
        pct_library = (library / total) * 100
        
        await interaction.response.send_message(
            f"🎲 **Discovery weights updated:**\n"
            f"• Similar songs: {pct_similar:.0f}%\n"
            f"• Same artist: {pct_artist:.0f}%\n"
            f"• Wildcard (charts): {pct_wildcard:.0f}%\n"
            f"• My Library: {pct_library:.0f}%",
            ephemeral=True
        )
    
    @settings_group.command(name="show", description="Show current server settings")
    async def show_settings(self, interaction: discord.Interaction):
        """Show current settings for this server."""
        embed = discord.Embed(
            title="⚙️ Server Settings",
            color=discord.Color.blue()
        )
        
        # Get from database
        if hasattr(self.bot, "db") and self.bot.db:
            from src.database.crud import GuildCRUD
            guild_crud = GuildCRUD(self.bot.db)
            all_settings = await guild_crud.get_all_settings(interaction.guild_id)
            
            # Pre-buffer
            prebuffer = all_settings.get("prebuffer", True)
            embed.add_field(
                name="⚡ Pre-buffering",
                value="Enabled" if prebuffer else "Disabled",
                inline=True
            )
            
            # Discovery weights
            weights = all_settings.get("discovery_weights", {"similar": 25, "artist": 25, "wildcard": 25, "library": 25})
            total = sum(weights.values())
            if total > 0:
                weights_text = (
                    f"Similar: {(weights.get('similar', 0) / total) * 100:.0f}%\n"
                    f"Artist: {(weights.get('artist', 0) / total) * 100:.0f}%\n"
                    f"Wildcard: {(weights.get('wildcard', 0) / total) * 100:.0f}%\n"
                    f"Library: {(weights.get('library', 0) / total) * 100:.0f}%"
                )
            else:
                weights_text = "Default (25/25/25/25)"
            embed.add_field(name="🎲 Discovery Weights", value=weights_text, inline=True)
            
            # Autoplay
            autoplay = all_settings.get("autoplay", True)
            embed.add_field(
                name="🔄 Autoplay",
                value="Enabled" if autoplay else "Disabled",
                inline=True
            )
            
            # Max Song Duration
            max_dur = all_settings.get("max_song_duration")
            if max_dur:
                dur_text = f"{max_dur} mins"
            else:
                dur_text = "6 mins (Default)"
                
            embed.add_field(
                name="⏱️ Max Duration",
                value=dur_text,
                inline=True
            )

            # Ephemeral Duration
            ephemeral_dur = all_settings.get("ephemeral_duration", 10)
            embed.add_field(
                name="⏳ Auto-Delete Duration",
                value=f"{ephemeral_dur}s",
                inline=True
            )
        else:
            embed.description = "Settings stored in memory only (database not available)"
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @settings_group.command(name="ephemeral_duration", description="Set auto-delete duration for confirmation messages")
    @app_commands.describe(seconds="Duration in seconds (5-60)")
    async def ephemeral_duration(self, interaction: discord.Interaction, seconds: int):
        """Set how long confirmation messages stay before being deleted."""
        if not 5 <= seconds <= 60:
            await interaction.response.send_message("❌ Duration must be between 5 and 60 seconds", ephemeral=True)
            return
            
        # Save to database
        if hasattr(self.bot, "db") and self.bot.db:
            from src.database.crud import GuildCRUD
            guild_crud = GuildCRUD(self.bot.db)
            await guild_crud.set_setting(interaction.guild_id, "ephemeral_duration", seconds)
        
        await interaction.response.send_message(
            f"⏳ Confirmation messages will now auto-delete after {seconds} seconds.",
            ephemeral=True
        )
    
    @app_commands.command(name="dj", description="Set the DJ role")
    @app_commands.describe(role="The role that can use DJ commands")
    @app_commands.default_permissions(administrator=True)
    async def set_dj_role(self, interaction: discord.Interaction, role: discord.Role):
        """Set the DJ role for this server."""
        if hasattr(self.bot, "db") and self.bot.db:
            from src.database.crud import GuildCRUD
            guild_crud = GuildCRUD(self.bot.db)
            await guild_crud.set_setting(interaction.guild_id, "dj_role_id", role.id)
        
        await interaction.response.send_message(
            f"🎧 DJ role set to {role.mention}",
            ephemeral=True
        )

    @app_commands.command(name="restart", description="Restart the bot (Admin only)")
    @app_commands.default_permissions(administrator=True)
    async def restart(self, interaction: discord.Interaction):
        """Restart the bot process."""
        # Double check permission
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ You need Administrator permission to use this command.", ephemeral=True)
            return

        await interaction.response.send_message("🔄 Restarting bot...", ephemeral=True)
        logger.warning(f"Bot restart requested by {interaction.user} ({interaction.user.id})")
        
        # Try Docker restart first
        import os
        if os.path.exists("/var/run/docker.sock"):
            try:
                hostname = socket.gethostname()
                # Use aiohttp with Unix socket
                connector = aiohttp.UnixConnector(path="/var/run/docker.sock")
                async with aiohttp.ClientSession(connector=connector) as session:
                    url = f"http://localhost/containers/{hostname}/restart"
                    async with session.post(url) as resp:
                        if resp.status == 204:
                            logger.info("Docker restart command sent successfully")
                            return
                        else:
                            text = await resp.text()
                            logger.error(f"Docker restart failed: {resp.status} - {text}")
            except Exception as e:
                logger.error(f"Failed to restart via Docker socket: {e}")

        # Fallback to process exit
        logger.info("Falling back to process exit")
        
        # Try clean shutdown first
        try:
            await self.bot.close()
        except:
            pass
            
        # Force exit
        os._exit(0)


async def setup(bot: commands.Bot):
    """Load the settings cog."""
    await bot.add_cog(SettingsCog(bot))
