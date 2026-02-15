"""
Song Normalizer - Converts song title/artist to canonical YouTube video ID
"""
import logging
import re
from dataclasses import dataclass

from .youtube import YouTubeService

logger = logging.getLogger(__name__)


@dataclass
class NormalizedSong:
    """Normalized song info with canonical YouTube video ID."""
    canonical_yt_id: str
    clean_title: str
    clean_artist: str
    original_title: str
    original_artist: str


class SongNormalizer:
    """Normalizes song title/artist to canonical YouTube video ID."""
    
    # Patterns to remove from titles
    TITLE_PATTERNS = [
        r"\s*\(Official\s*(Music\s*)?Video\)",
        r"\s*\(Official\s*Audio\)",
        r"\s*\(Lyric\s*Video\)",
        r"\s*\(Lyrics\)",
        r"\s*\(Visualizer\)",
        r"\s*\(Audio\)",
        r"\s*\(HD\)",
        r"\s*\(HQ\)",
        r"\s*\(4K\)",
        r"\s*\[Official\s*(Music\s*)?Video\]",
        r"\s*\[Official\s*Audio\]",
        r"\s*\[Lyrics\]",
        r"\s*\[HD\]",
        r"\s*\[HQ\]",
        r"\s*\[4K\]",
        r"\s*-\s*Topic$",  # YouTube Music "Artist - Topic" channels
        r"\s*\(Remastered\s*\d*\)",
        r"\s*\(.*?Remix\)",
        r"\s*\(.*?Version\)",
        r"\s*\(.*?Edit\)",
    ]
    
    # Patterns to extract primary artist
    ARTIST_SEPARATORS = [
        r"\s+feat\.?\s+",
        r"\s+ft\.?\s+",
        r"\s+featuring\s+",
        r"\s+x\s+",
        r"\s+&\s+",
        r"\s*,\s*",
        r"\s+and\s+",
    ]
    
    def __init__(self, youtube_service: YouTubeService):
        self.youtube = youtube_service
        self._title_regex = re.compile(
            "|".join(self.TITLE_PATTERNS),
            re.IGNORECASE
        )
        self._artist_regex = re.compile(
            "|".join(self.ARTIST_SEPARATORS),
            re.IGNORECASE
        )
    
    def clean_title(self, title: str) -> str:
        """Clean song title by removing common suffixes."""
        cleaned = self._title_regex.sub("", title)
        # Remove extra whitespace
        cleaned = " ".join(cleaned.split())
        return cleaned.strip()
    
    def clean_artist(self, artist: str) -> str:
        """Extract primary artist by removing featured artists."""
        # Split by any separator and take the first part
        parts = self._artist_regex.split(artist, maxsplit=1)
        if parts:
            return parts[0].strip()
        return artist.strip()
    
    async def normalize(self, title: str, artist: str) -> NormalizedSong | None:
        """
        Normalize a song to its canonical YouTube video ID.
        
        This ensures the same song always maps to the same ID,
        avoiding duplicate entries in history.
        """
        clean_title = self.clean_title(title)
        clean_artist = self.clean_artist(artist)
        
        # Search YouTube Music for the canonical version
        search_query = f"{clean_artist} {clean_title}"
        results = await self.youtube.search(search_query, filter_type="songs", limit=1)
        
        if results:
            return NormalizedSong(
                canonical_yt_id=results[0].video_id,
                clean_title=clean_title,
                clean_artist=clean_artist,
                original_title=title,
                original_artist=artist,
            )
        
        # Fallback: try with original title/artist
        fallback_query = f"{artist} {title}"
        results = await self.youtube.search(fallback_query, filter_type="songs", limit=1)
        
        if results:
            return NormalizedSong(
                canonical_yt_id=results[0].video_id,
                clean_title=clean_title,
                clean_artist=clean_artist,
                original_title=title,
                original_artist=artist,
            )
        
        logger.warning(f"Could not normalize song: {artist} - {title}")
        return None
    
    async def normalize_yt_track(self, video_id: str, title: str, artist: str) -> NormalizedSong:
        """
        Create a NormalizedSong from an existing YouTube track.
        Uses the provided video_id as canonical (assumes it's already the right one).
        """
        return NormalizedSong(
            canonical_yt_id=video_id,
            clean_title=self.clean_title(title),
            clean_artist=self.clean_artist(artist),
            original_title=title,
            original_artist=artist,
        )
    async def normalize_to_yt(self, title: str, artist: str) -> "YTTrack | None":
        """
        Normalize and return the full YTTrack object.
        """
        clean_title = self.clean_title(title)
        clean_artist = self.clean_artist(artist)
        
        # Search YouTube Music
        search_query = f"{clean_artist} {clean_title}"
        results = await self.youtube.search(search_query, filter_type="songs", limit=1)
        
        if results:
            return results[0]
            
        # Fallback
        fallback_query = f"{artist} {title}"
        results = await self.youtube.search(fallback_query, filter_type="songs", limit=1)
        
        if results:
            return results[0]
            
        logger.warning(f"Could not normalize to YT track: {artist} - {title}")
        return None
