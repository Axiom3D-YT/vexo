import discord
from discord import app_commands
from discord.ext import commands
import os
from pathlib import Path

class AdminCog(commands.Cog):
    """Administrative commands for bot management."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        
    @app_commands.command(name="logs", description="Get the latest bot logs (Admin only)")
    @app_commands.default_permissions(administrator=True)
    async def get_logs(self, interaction: discord.Interaction):
        """Send the log file as an attachment."""
        log_path = Path("data/bot.log")
        
        if not log_path.exists():
            await interaction.response.send_message("Log file not found.", ephemeral=True)
            return
            
        # Check file size (Discord limit is 25MB for most)
        if log_path.stat().st_size > 25 * 1024 * 1024:
            await interaction.response.send_message("Log file is too large to send via Discord.", ephemeral=True)
            return
            
        await interaction.response.send_message("Here are the latest logs:", file=discord.File(log_path), ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
