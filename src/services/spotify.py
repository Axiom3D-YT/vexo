import asyncio
import logging
import random
from dataclasses import dataclass
from functools import partial, wraps

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

logger = logging.getLogger(__name__)


def retry_with_backoff(retries=3, initial_backoff=1):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            x = 0
            while True:
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    if x == retries:
                        logger.error(f"Spotify failed after {retries} retries: {e}")
                        raise
                    # User requested 1s retry logic
                    sleep = initial_backoff + random.uniform(0, 0.5)
                    logger.warning(f"Spotify Retry {x + 1}/{retries} for {func.__name__} after {sleep:.2f}s due to: {e}")
                    await asyncio.sleep(sleep)
                    x += 1
        return wrapper
    return decorator


@dataclass
class SpotifyTrack:
    """Spotify track info."""
    spotify_id: str
    title: str
    artist: str
    artist_id: str
    album: str | None = None
    release_year: int | None = None
    duration_seconds: int | None = None
    popularity: int = 0
    genres: list[str] | None = None


@dataclass
class SpotifyArtist:
    """Spotify artist info."""
    artist_id: str
    name: str
    genres: list[str]
    popularity: int = 0


class SpotifyService:
    """Spotify API wrapper."""
    
    def __init__(self, client_id: str, client_secret: str):
        self.sp = spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(
                client_id=client_id,
                client_secret=client_secret
            ),
            requests_timeout=1  # User requested ultra-short 1s timeout
        )
    
    @retry_with_backoff(retries=3, initial_backoff=1)
    async def search_track(self, query: str) -> SpotifyTrack | None:
        """Search for a track."""
        loop = asyncio.get_event_loop()
        try:
            results = await loop.run_in_executor(
                None,
                partial(self.sp.search, q=query, limit=1, type="track")
            )
            
            if not results["tracks"]["items"]:
                return None
            
            track = results["tracks"]["items"][0]
            return SpotifyTrack(
                spotify_id=track["id"],
                title=track["name"],
                artist=track["artists"][0]["name"],
                artist_id=track["artists"][0]["id"],
                album=track["album"]["name"],
                release_year=int(track["album"]["release_date"][:4]) if track["album"]["release_date"] else None,
                duration_seconds=track["duration_ms"] // 1000,
                popularity=track["popularity"],
            )
        except Exception as e:
            logger.error(f"Spotify search error: {e}")
            return None

    @retry_with_backoff(retries=3, initial_backoff=1)
    async def search_artist(self, query: str) -> SpotifyArtist | None:
        """Search for an artist."""
        loop = asyncio.get_event_loop()
        try:
            results = await loop.run_in_executor(
                None,
                partial(self.sp.search, q=query, limit=1, type="artist")
            )
            
            if not results["artists"]["items"]:
                return None
            
            artist = results["artists"]["items"][0]
            return SpotifyArtist(
                artist_id=artist["id"],
                name=artist["name"],
                genres=artist.get("genres", []),
                popularity=artist.get("popularity", 0),
            )
        except Exception as e:
            logger.error(f"Spotify artist search error: {e}")
            return None
    
    @retry_with_backoff(retries=3, initial_backoff=1)
    async def get_artist(self, artist_id: str) -> SpotifyArtist | None:
        """Get artist info including genres."""
        loop = asyncio.get_event_loop()
        try:
            artist = await loop.run_in_executor(
                None,
                partial(self.sp.artist, artist_id)
            )
            return SpotifyArtist(
                artist_id=artist["id"],
                name=artist["name"],
                genres=artist.get("genres", []),
                popularity=artist.get("popularity", 0),
            )
        except Exception as e:
            logger.error(f"Error getting artist {artist_id}: {e}")
            return None
    
    @retry_with_backoff(retries=3, initial_backoff=1)
    async def get_artists_batch(self, artist_ids: list[str]) -> list[SpotifyArtist]:
        """Get multiple artists in batch (max 50)."""
        if not artist_ids:
            return []
        
        loop = asyncio.get_event_loop()
        artists = []
        
        # Spotify API allows max 50 artists per request
        for i in range(0, len(artist_ids), 50):
            batch = artist_ids[i:i+50]
            try:
                results = await loop.run_in_executor(
                    None,
                    partial(self.sp.artists, batch)
                )
                for a in results["artists"]:
                    if a:
                        artists.append(SpotifyArtist(
                            artist_id=a["id"],
                            name=a["name"],
                            genres=a.get("genres", []),
                            popularity=a.get("popularity", 0),
                        ))
            except Exception as e:
                logger.error(f"Error getting artist batch: {e}")
        
        return artists
    
    @retry_with_backoff(retries=3, initial_backoff=1)
    async def get_artist_top_tracks(self, artist_id: str, country: str = "US") -> list[SpotifyTrack]:
        """Get artist's top tracks."""
        loop = asyncio.get_event_loop()
        try:
            results = await loop.run_in_executor(
                None,
                partial(self.sp.artist_top_tracks, artist_id, country=country)
            )
            
            tracks = []
            for track in results["tracks"]:
                tracks.append(SpotifyTrack(
                    spotify_id=track["id"],
                    title=track["name"],
                    artist=track["artists"][0]["name"],
                    artist_id=track["artists"][0]["id"],
                    album=track["album"]["name"],
                    release_year=int(track["album"]["release_date"][:4]) if track["album"]["release_date"] else None,
                    duration_seconds=track["duration_ms"] // 1000,
                    popularity=track["popularity"],
                ))
            return tracks
        except Exception as e:
            logger.error(f"Error getting top tracks: {e}")
            return []
    
    @retry_with_backoff(retries=3, initial_backoff=1)
    async def get_related_artists(self, artist_id: str) -> list[SpotifyArtist]:
        """Get related artists."""
        loop = asyncio.get_event_loop()
        try:
            results = await loop.run_in_executor(
                None,
                partial(self.sp.artist_related_artists, artist_id)
            )
            return [
                SpotifyArtist(
                    artist_id=a["id"],
                    name=a["name"],
                    genres=a.get("genres", []),
                    popularity=a.get("popularity", 0),
                )
                for a in results["artists"]
            ]
        except Exception as e:
            logger.error(f"Error getting related artists: {e}")
            return []
    
    @retry_with_backoff(retries=3, initial_backoff=1)
    async def get_playlist_tracks(self, playlist_url: str) -> list[SpotifyTrack]:
        """Get all tracks from a Spotify playlist."""
        loop = asyncio.get_event_loop()
        try:
            # Extract playlist ID from URL
            playlist_id = self._extract_playlist_id(playlist_url)
            if not playlist_id:
                return []
            
            results = await loop.run_in_executor(
                None,
                partial(self.sp.playlist, playlist_id)
            )
            
            tracks = []
            items = results["tracks"]["items"]
            
            # Handle pagination
            next_url = results["tracks"]["next"]
            while next_url:
                next_results = await loop.run_in_executor(
                    None,
                    partial(self.sp.next, results["tracks"])
                )
                items.extend(next_results["items"])
                next_url = next_results.get("next")
                results["tracks"] = next_results
            
            for item in items:
                track = item.get("track")
                if not track or not track.get("id"):
                    continue
                
                tracks.append(SpotifyTrack(
                    spotify_id=track["id"],
                    title=track["name"],
                    artist=track["artists"][0]["name"],
                    artist_id=track["artists"][0]["id"],
                    album=track["album"]["name"],
                    release_year=int(track["album"]["release_date"][:4]) if track["album"].get("release_date") else None,
                    duration_seconds=track["duration_ms"] // 1000,
                    popularity=track["popularity"],
                ))
            
            return tracks
        except Exception as e:
            logger.error(f"Error getting playlist: {e}")
            return []
    
    def _extract_playlist_id(self, url_or_id: str) -> str | None:
        """Extract playlist ID from URL or return as-is if already an ID."""
        if "spotify.com" in url_or_id:
            # URL format: https://open.spotify.com/playlist/{id}?...
            parts = url_or_id.split("/playlist/")
            if len(parts) > 1:
                return parts[1].split("?")[0]
        elif len(url_or_id) == 22:  # Spotify IDs are 22 chars
            return url_or_id
        return url_or_id
