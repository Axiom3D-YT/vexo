"""
YouTube Music API Wrapper
"""
import asyncio
import logging
import re
from dataclasses import dataclass
from functools import partial
from typing import Any
from concurrent.futures import ThreadPoolExecutor

import yt_dlp
from ytmusicapi import YTMusic

import random
import time
from functools import wraps

logger = logging.getLogger(__name__)


def retry_with_backoff(retries=3, backoff_in_seconds=1):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            x = 0
            while True:
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    if x == retries:
                        logger.error(f"Failed after {retries} retries: {e}")
                        raise
                    else:
                        sleep = (backoff_in_seconds * 2 ** x + random.uniform(0, 1))
                        logger.warning(f"Retry {x + 1}/{retries} for {func.__name__} after {sleep:.2f}s due to: {e}")
                        await asyncio.sleep(sleep)
                        x += 1
        return wrapper
    return decorator


@dataclass
class YTTrack:
    """YouTube track info."""
    video_id: str
    title: str
    artist: str
    duration_seconds: int | None = None
    album: str | None = None
    year: int | None = None
    thumbnail_url: str | None = None


class YouTubeService:
    """YouTube Music API wrapper."""
    
    def __init__(self, cookies_path: str | None = None, po_token: str | None = None):
        self.yt = YTMusic()
        self.cookies_path = cookies_path
        self.po_token = po_token
        
        # Dedicated executor for YouTube operations to prevent blocking main thread pool
        self.executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="YouTubeWorker")
        
        self._ydl_opts = {
            "format": "bestaudio/best",
            "source_address": "0.0.0.0",
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "socket_timeout": 10,  # Strict socket timeout
            "nocheckcertificate": True,
            "ignoreerrors": True,
            "logtostderr": False,
            "noplaylist": True,
        }
        if cookies_path:
            self._ydl_opts["cookiefile"] = cookies_path
        if po_token:
            self._ydl_opts["extractor_args"] = {"youtube": {"po_token": [po_token]}}

    def parse_url(self, url: str) -> tuple[str, str] | None:
        """Parse YouTube URL to (type, id)."""
        import re
        
        # Check domain first to avoid false positives (e.g. Spotify)
        if not any(domain in url for domain in ["youtube.com", "youtu.be", "music.youtube.com"]):
            return None
        
        # Video ID
        # Matches:
        # - youtube.com/watch?v=ID
        # - youtube.com/v/ID
        # - youtube.com/embed/ID
        # - youtu.be/ID
        # - music.youtube.com/watch?v=ID
        video_pattern = r"(?:v=|\/|embed\/|youtu\.be\/)([0-9A-Za-z_-]{11})"
        match = re.search(video_pattern, url)
        if match:
            # If it's a playlist URL, it might have a video ID too (watch?v=...&list=...), 
            # but usually we want the video if 'v=' is present, UNLESS the user explicitly wants the playlist.
            # However, the command logic checks parse_url. 
            # If I return video, it plays video. 
            # If I return playlist, it plays playlist.
            # Let's prioritize playlist if 'list=' is present AND it's not a watch URL? 
            # actually standard behavior is usually:
            # - watch?v=ID&list=PID -> Play Video (and maybe queue playlist? but here we obey the return type)
            # The user asked for "link", if they give a watch link in a playlist context, 
            # they probably want the song. If they give a playlist link `youtube.com/playlist?list=...`, 
            # there is no `v=`.
            
            # So, if `v=` exists, treat as video.
            return "video", match.group(1)
            
        # Playlist ID
        playlist_pattern = r"(?:list=)([a-zA-Z0-9_-]+)"
        match = re.search(playlist_pattern, url)
        if match:
            return "playlist", match.group(1)
            
        return None
            
    async def shutdown(self):
        """Shutdown the executor."""
        self.executor.shutdown(wait=False)
    
    @retry_with_backoff()
    async def search(self, query: str, filter_type: str = "songs", limit: int = 5) -> list[YTTrack]:
        """Search YouTube Music for tracks."""
        loop = asyncio.get_event_loop()
        try:
            # Wrap blocking call in executor with timeout
            results = await asyncio.wait_for(
                loop.run_in_executor(
                    self.executor,
                    partial(self.yt.search, query, filter=filter_type, limit=limit)
                ),
                timeout=15.0
            )
            
            tracks = []
            for r in results:
                if not r.get("videoId"):
                    continue
                
                duration = None
                if r.get("duration_seconds"):
                    duration = r["duration_seconds"]
                elif r.get("duration"):
                    # Parse duration string like "3:45"
                    duration = self._parse_duration(r["duration"])
                
                artist = "Unknown"
                if r.get("artists") and len(r["artists"]) > 0:
                    artist = r["artists"][0].get("name", "Unknown")
                
                tracks.append(YTTrack(
                    video_id=r["videoId"],
                    title=r.get("title", "Unknown"),
                    artist=artist,
                    duration_seconds=duration,
                    album=r.get("album", {}).get("name") if r.get("album") else None,
                    year=r.get("year"),
                    thumbnail_url=r.get("thumbnails", [{}])[-1].get("url"),
                ))
            
            return tracks
        except asyncio.TimeoutError:
            logger.error(f"YouTube search timed out for query: {query}")
            return []
        except Exception as e:
            logger.error(f"YouTube search error: {e}")
            return []
    
    @retry_with_backoff()
    async def get_watch_playlist(self, video_id: str, limit: int = 20) -> list[YTTrack]:
        """Get related tracks from a video's watch playlist."""
        loop = asyncio.get_event_loop()
        try:
            results = await asyncio.wait_for(
                loop.run_in_executor(
                    self.executor,
                    partial(self.yt.get_watch_playlist, videoId=video_id, limit=limit)
                ),
                timeout=15.0
            )
            
            tracks = []
            for t in results.get("tracks", []):
                if not t.get("videoId"):
                    continue
                
                artist = "Unknown"
                if t.get("artists") and len(t["artists"]) > 0:
                    artist = t["artists"][0].get("name", "Unknown")
                
                tracks.append(YTTrack(
                    video_id=t["videoId"],
                    title=t.get("title", "Unknown"),
                    artist=artist,
                    duration_seconds=t.get("length_seconds") or t.get("duration_seconds") or (self._parse_duration(t.get("length")) if t.get("length") else None),
                    year=t.get("year"),
                ))
            
            return tracks
        except asyncio.TimeoutError:
            logger.error(f"YouTube watch playlist timed out for video: {video_id}")
            return []
        except Exception as e:
            logger.error(f"Error getting watch playlist: {e}")
            return []
    
    @retry_with_backoff()
    async def get_playlist_tracks(self, playlist_id: str, limit: int = 100) -> list[YTTrack]:
        """Get tracks from a YouTube Music playlist."""
        loop = asyncio.get_event_loop()
        try:
            results = await asyncio.wait_for(
                loop.run_in_executor(
                    self.executor,
                    partial(self.yt.get_playlist, playlist_id, limit=limit)
                ),
                timeout=20.0
            )
            
            tracks = []
            for t in results.get("tracks", []):
                if not t.get("videoId"):
                    continue
                
                artist = "Unknown"
                if t.get("artists") and len(t["artists"]) > 0:
                    artist = t["artists"][0].get("name", "Unknown")
                
                tracks.append(YTTrack(
                    video_id=t["videoId"],
                    title=t.get("title", "Unknown"),
                    artist=artist,
                    duration_seconds=t.get("duration_seconds") or (self._parse_duration(t.get("duration")) if t.get("duration") else None),
                ))
            
            return tracks
        except asyncio.TimeoutError:
            logger.error(f"YouTube playlist fetch timed out for: {playlist_id}")
            return []
        except Exception as e:
            logger.error(f"Error getting playlist: {e}")
            return []
    
    @retry_with_backoff()
    async def get_track_info(self, video_id: str) -> YTTrack | None:
        """Get full track info for a specific video."""
        loop = asyncio.get_event_loop()
        try:
            r = await asyncio.wait_for(
                loop.run_in_executor(
                    self.executor,
                    partial(self.yt.get_song, videoId=video_id)
                ),
                timeout=10.0
            )
            
            video_details = r.get("videoDetails", {})
            if not video_details:
                return None
                
            artist = "Unknown"
            if video_details.get("artists") and len(video_details["artists"]) > 0:
                artist = video_details["artists"][0].get("name", "Unknown")
            elif video_details.get("author"):
                artist = video_details["author"]

            return YTTrack(
                video_id=video_details.get("videoId"),
                title=video_details.get("title", "Unknown"),
                artist=artist,
                duration_seconds=int(video_details["lengthSeconds"]) if video_details.get("lengthSeconds") else None,
                thumbnail_url=video_details.get("thumbnail", {}).get("thumbnails", [{}])[-1].get("url")
            )
        except asyncio.TimeoutError:
            logger.error(f"YouTube track info timed out for: {video_id}")
            return None
        except Exception as e:
            logger.error(f"Error getting track info: {e}")
            return None

    async def get_stream_url(self, video_id: str) -> str | None:
        """Get the audio stream URL for a video using yt-dlp."""
        loop = asyncio.get_event_loop()
        url = f"https://www.youtube.com/watch?v={video_id}"
        
        try:
            def extract():
                with yt_dlp.YoutubeDL(self._ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    return info.get("url")
            
            # Use dedicated executor and longer timeout for extraction
            return await asyncio.wait_for(
                loop.run_in_executor(self.executor, extract),
                timeout=25.0
            )
        except asyncio.TimeoutError:
            logger.error(f"YouTube stream URL extraction timed out for: {video_id}")
            return None
        except Exception as e:
            logger.error(f"Error getting stream URL for {video_id}: {e}")
            return None
    
    @retry_with_backoff()
    async def search_playlists(self, query: str, limit: int = 5) -> list[dict]:
        """Search for playlists."""
        loop = asyncio.get_event_loop()
        try:
            results = await asyncio.wait_for(
                loop.run_in_executor(
                    self.executor,
                    partial(self.yt.search, query, filter="playlists", limit=limit)
                ),
                timeout=15.0
            )
            return [
                {
                    "browse_id": r.get("browseId"),
                    "title": r.get("title"),
                    "author": r.get("author"),
                }
                for r in results if r.get("browseId")
            ]
        except asyncio.TimeoutError:
            logger.error(f"YouTube playlist search timed out for: {query}")
            return []
        except Exception as e:
            logger.error(f"Error searching playlists: {e}")
            return []
    
    def _parse_duration(self, duration_str: str) -> int | None:
        """Parse duration string like '3:45' to seconds."""
        if not duration_str:
            return None
        try:
            parts = duration_str.split(":")
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            elif len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        except ValueError:
            pass
        return None
