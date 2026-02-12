import logging
import musicbrainzngs
from typing import Optional

logger = logging.getLogger(__name__)

class MusicBrainzService:
    """
    Service for retrieving metadata from MusicBrainz as a tertiary fallback.
    Uses musicbrainzngs library with a custom User-Agent.
    """
    
    def __init__(self):
        # Identify the application to MusicBrainz (Required)
        musicbrainzngs.set_useragent(
            "VexoBot", 
            "1.0", 
            "contact@example.com"
        )
        self.enabled = True
        self._cache = {} # Simple in-memory cache: { "artist - title": ["list", "of", "tags"] }

    def get_genre(self, artist: str, title: str) -> list[str]:
        """
        Search for a track metadata on MusicBrainz and return tags as genres.
        Prioritizes Artist tags if Recording tags are sparse.
        This is blocking, should be run in an executor.
        """
        if not self.enabled:
            return []

        cache_key = f"{artist.lower()} - {title.lower()}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        try:
            # 1. Search for Artist to get broad genres
            artist_tags = []
            artists = musicbrainzngs.search_artists(artist=artist, limit=1).get('artist-list', [])
            
            if artists:
                # Get the best match
                artist_obj = artists[0]
                if 'tag-list' in artist_obj:
                    artist_tags = [t['name'].title() for t in artist_obj['tag-list']]
            
            # 2. Search for Recording to get specific tags (optional but good)
            recording_tags = []
            recordings = musicbrainzngs.search_recordings(artist=artist, recording=title, limit=1).get('recording-list', [])
            
            if recordings:
                 rec_obj = recordings[0]
                 if 'tag-list' in rec_obj:
                     recording_tags = [t['name'].title() for t in rec_obj['tag-list']]
            
            # Combine: Prefer Recording tags, then Artist tags
            # MusicBrainz tags can be messy, so we prioritize the most descriptive
            
            combined = list(set(recording_tags + artist_tags))
            
            if combined:
                logger.info(f"MusicBrainz found tags for '{artist} - {title}': {combined}")
                self._cache[cache_key] = combined
                return combined
                
        except Exception as e:
            logger.error(f"MusicBrainz search failed for '{artist} - {title}': {e}")
            
        return []
