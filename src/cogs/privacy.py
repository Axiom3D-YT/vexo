"""
Privacy Cog - User privacy commands (GDPR compliance)
"""
import io
import json
import logging
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)


class PrivacyCog(commands.Cog):
    """Privacy and data management commands."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    privacy_group = app_commands.Group(name="privacy", description="Privacy and data management")
    
    @privacy_group.command(name="export", description="Export all your data")
    async def export_data(self, interaction: discord.Interaction):
        """Export all user data as JSON."""
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        
        if not hasattr(self.bot, "db") or not self.bot.db:
            await interaction.followup.send("❌ Database not available", ephemeral=True)
            return
        
        from src.database.crud import PreferenceCRUD
        
        try:
            pref_crud = PreferenceCRUD(self.bot.db)
            data = await pref_crud.export_all(interaction.user.id)
            
            # Convert to JSON
            def json_serializer(obj):
                if isinstance(obj, datetime):
                    return obj.isoformat()
                if hasattr(obj, "__dict__"):
                    return obj.__dict__
                return str(obj)
            
            json_str = json.dumps(data, indent=2, default=json_serializer)
            
            # Send as file
            file = discord.File(
                io.BytesIO(json_str.encode()),
                filename=f"your_data_{interaction.user.id}.json"
            )
            
            await interaction.followup.send(
                "📦 Here's all your data:",
                file=file,
                ephemeral=True
            )
        
        except Exception as e:
            logger.error(f"Error exporting data for user {interaction.user.id}: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
    
    @privacy_group.command(name="delete", description="Delete all your data")
    async def delete_data(self, interaction: discord.Interaction):
        """Delete all user data."""
        # Create confirmation view
        view = DeleteConfirmView(self.bot, interaction.user.id)
        
        await interaction.response.send_message(
            "⚠️ **Are you sure you want to delete all your data?**\n\n"
            "This will permanently delete:\n"
            "• Your music preferences\n"
            "• Your song reactions (likes/dislikes)\n"
            "• Your imported playlists\n"
            "• Your listening history participation\n\n"
            "This action cannot be undone!",
            view=view,
            ephemeral=True
        )
    
    @privacy_group.command(name="optout", description="Opt out of preference tracking")
    async def opt_out(self, interaction: discord.Interaction):
        """Opt out of preference tracking."""
        if not hasattr(self.bot, "db") or not self.bot.db:
            await interaction.response.send_message("❌ Database not available", ephemeral=True)
            return
        
        from src.database.crud import UserCRUD
        
        try:
            user_crud = UserCRUD(self.bot.db)
            await user_crud.get_or_create(interaction.user.id, interaction.user.name)
            await user_crud.set_opt_out(interaction.user.id, True)
            
            await interaction.response.send_message(
                "✅ **Opted out of preference tracking.**\n\n"
                "The bot will no longer learn from your listening habits.\n"
                "You can still use all features, but discovery won't personalize for you.\n\n"
                "Use `/privacy optin` to re-enable tracking.",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error opting out user {interaction.user.id}: {e}")
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
    
    @privacy_group.command(name="optin", description="Re-enable preference tracking")
    async def opt_in(self, interaction: discord.Interaction):
        """Opt back into preference tracking."""
        if not hasattr(self.bot, "db") or not self.bot.db:
            await interaction.response.send_message("❌ Database not available", ephemeral=True)
            return
        
        from src.database.crud import UserCRUD
        
        try:
            user_crud = UserCRUD(self.bot.db)
            await user_crud.get_or_create(interaction.user.id, interaction.user.name)
            await user_crud.set_opt_out(interaction.user.id, False)
            
            await interaction.response.send_message(
                "✅ **Re-enabled preference tracking.**\n\n"
                "The bot will now learn from your listening habits again.",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error opting in user {interaction.user.id}: {e}")
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)


class DeleteConfirmView(discord.ui.View):
    """Confirmation view for data deletion."""
    
    def __init__(self, bot: commands.Bot, user_id: int):
        super().__init__(timeout=60)
        self.bot = bot
        self.user_id = user_id
    
    @discord.ui.button(label="Delete My Data", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Confirm deletion."""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Not your data!", ephemeral=True)
            return
        
        from src.database.crud import UserCRUD
        
        try:
            user_crud = UserCRUD(self.bot.db)
            await user_crud.delete_all_data(self.user_id)
            
            await interaction.response.edit_message(
                content="✅ **All your data has been deleted.**",
                view=None
            )
        except Exception as e:
            await interaction.response.edit_message(
                content=f"❌ Error deleting data: {e}",
                view=None
            )
    
    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Cancel deletion."""
        await interaction.response.edit_message(
            content="❌ Deletion cancelled.",
            view=None
        )


async def setup(bot: commands.Bot):
    """Load the privacy cog."""
    await bot.add_cog(PrivacyCog(bot))
