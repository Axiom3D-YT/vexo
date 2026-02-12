import aiohttp
import logging
import json

logger = logging.getLogger(__name__)

class VexoTTSService:
    """Service to interact with VexoTTS API for speech generation."""
    
    BASE_URL = "http://192.168.1.61:4200"
    SPEAK_URL = f"{BASE_URL}/speak"
    
    async def speak(self, guild_id: int, channel_id: int, message: str, voice: str = "en_us_001", slow: bool = False) -> bool:
        """
        Request the VexoTTS service to speak a message in a voice channel.
        Returns True if successful, False otherwise.
        """
        payload = {
            "guild_id": guild_id,
            "channel_id": channel_id,
            "message": message,
            "voice": voice,
            "slow": slow
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.SPEAK_URL, json=payload, timeout=10) as resp:
                    if resp.status == 200:
                        logger.info(f"VexoTTS success: spoke message in guild {guild_id}")
                        return True
                    else:
                        text = await resp.text()
                        logger.error(f"VexoTTS error {resp.status}: {text}")
                        return False
        except Exception as e:
            logger.error(f"Failed to call VexoTTS API: {e}")
            return False

    async def get_voices(self) -> dict:
        """Fetch available voices from the VexoTTS service (if implemented)."""
        # Note: The user provided a list of voices in documented format, 
        # but if the API has a discovery endpoint, we could use it here.
        # For now, we manually maintain the list or just return a static dict.
        return {
            "tiktok_voices": [
                {"id": "en_us_ghostface", "name": "Ghost Face"},
                {"id": "en_us_c3po", "name": "C3PO"},
                {"id": "en_us_stitch", "name": "Stitch"},
                {"id": "en_us_stormtrooper", "name": "Stormtrooper"},
                {"id": "en_us_rocket", "name": "Rocket"},
                {"id": "en_female_madam_leota", "name": "Madame Leota"},
                {"id": "en_male_ghosthost", "name": "Ghost Host"},
                {"id": "en_male_pirate", "name": "Pirate"},
                {"id": "en_us_001", "name": "English US (Default)"},
                {"id": "en_us_002", "name": "Jessie"},
                {"id": "en_us_006", "name": "Joey"},
                {"id": "en_us_007", "name": "Professor"},
                {"id": "en_us_009", "name": "Scientist"},
                {"id": "en_us_010", "name": "Confidence"},
                {"id": "en_male_jomboy", "name": "Game On"},
                {"id": "en_female_samc", "name": "Empathetic"},
                {"id": "en_male_cody", "name": "Serious"},
                {"id": "en_female_makeup", "name": "Beauty Guru"},
                {"id": "en_female_richgirl", "name": "Bestie"},
                {"id": "en_male_grinch", "name": "Trickster"},
                {"id": "en_male_narration", "name": "Story Teller"},
                {"id": "en_male_deadpool", "name": "Mr. GoodGuy"},
                {"id": "en_male_jarvis", "name": "Alfred"},
                {"id": "en_male_ashmagic", "name": "ashmagic"},
                {"id": "en_male_olantekkers", "name": "olantekkers"},
                {"id": "en_male_ukneighbor", "name": "Lord Cringe"},
                {"id": "en_male_ukbutler", "name": "Mr. Meticulous"},
                {"id": "en_female_shenna", "name": "Debutante"},
                {"id": "en_female_pansino", "name": "Varsity"},
                {"id": "en_male_trevor", "name": "Marty"},
                {"id": "en_female_betty", "name": "Bae"},
                {"id": "en_male_cupid", "name": "Cupid"},
                {"id": "en_female_grandma", "name": "Granny"},
                {"id": "en_male_wizard", "name": "Magician"},
                {"id": "en_uk_001", "name": "Narrator"},
                {"id": "en_uk_003", "name": "Male English UK"},
                {"id": "en_au_001", "name": "Metro"},
                {"id": "en_au_002", "name": "Smooth"},
                {"id": "es_mx_002", "name": "Warm"}
            ],
            "gtts_voices": [
                {"id": "en", "name": "English (gTTS)"},
                {"id": "it", "name": "Italian (gTTS)"},
                {"id": "fr", "name": "French (gTTS)"},
                {"id": "es", "name": "Spanish (gTTS)"},
                {"id": "de", "name": "German (gTTS)"}
            ]
        }
