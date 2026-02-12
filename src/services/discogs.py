import asyncio
import logging
import os
from functools import partial
from typing import Optional

import discogs_client
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

class DiscogsService:
    """
    Service for retrieving metadata from Discogs API as a fallback.
    Wraps the synchronous discogs_client in asyncio executors.
    """
    
    def __init__(self):
        self.client: Optional[discogs_client.Client] = None
        self.enabled = False
        self._cache = {}  # Simple in-memory cache: { "artist - title": ["genre1", "genre2"] }
        
        self._initialize()

    def _initialize(self):
        """Initialize the Discogs client from environment variables."""
        load_dotenv()
        
        user_agent = 'VexoBot/1.0'
        token = os.getenv("DISCOGS_TOKEN")
        
        # Support consumer key/secret pair if token is not available
        key = os.getenv("DISCOGS_KEY") or os.getenv("DISCOGS_CONSUMER_KEY")
        secret = os.getenv("DISCOGS_SECRET") or os.getenv("DISCOGS_CONSUMER_SECRET")
        
        try:
            if token:
                logger.info("Initializing DiscogsService with User Token")
                self.client = discogs_client.Client(user_agent, user_token=token)
                self.enabled = True
            elif key and secret:
                logger.info("Initializing DiscogsService with Consumer Key/Secret")
                self.client = discogs_client.Client(user_agent, consumer_key=key, consumer_secret=secret)
                self.enabled = True
            else:
                logger.warning("DiscogsService disabled: No credentials found (DISCOGS_TOKEN or DISCOGS_KEY/SECRET)")
                self.enabled = False
        except Exception as e:
            logger.error(f"Failed to initialize DiscogsService: {e}")
            self.enabled = False

    async def get_genre(self, artist: str, title: str) -> list[str]:
        """
        Search for a track on Discogs and return its genres/styles.
        Returns a list of strings or empty list if not found.
        """
        if not self.enabled or not self.client:
            return []

        # Check cache
        cache_key = f"{artist.lower()} - {title.lower()}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        loop = asyncio.get_event_loop()
        try:
            # Run blocking search in executor
            result = await loop.run_in_executor(
                None, 
                partial(self._search_sync, artist, title)
            )
            
            if result:
                self._cache[cache_key] = result
                return result
                
        except Exception as e:
            logger.error(f"Discogs search error for '{artist} - {title}': {e}")
            
        return []

    def _search_sync(self, artist: str, title: str) -> list[str]:
        """Synchronous search function to be run in executor."""
        try:
            # Clean up query
            query = f"{artist} - {title}"
            
            # Search for releases
            results = self.client.search(query, type='release')
            
            if not results:
                return []
                
            # Take the first result
            # Note: discogs_client search results are lazy, accessing [0] triggers the API call
            release = results[0]
            
            genres = getattr(release, 'genres', []) or []
            styles = getattr(release, 'styles', []) or []
            
            # Combine genres and styles, deduplicate
            combined = list(set(genres + styles))
            
            if combined:
                logger.info(f"Discogs found genres for '{artist} - {title}': {combined}")
            
            return combined
            
        except IndexError:
            # No results found
            return []
        except Exception as e:
            logger.debug(f"Discogs sync search failed: {e}")
            return []
