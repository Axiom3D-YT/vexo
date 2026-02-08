"""
Discovery Engine - Smart song selection based on user preferences
"""
import logging
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.services.youtube import YouTubeService, YTTrack
from src.services.spotify import SpotifyService
from src.services.normalizer import SongNormalizer

if TYPE_CHECKING:
    from src.database.crud import PreferenceCRUD, PlaybackCRUD, ReactionCRUD

logger = logging.getLogger(__name__)


@dataclass
class DiscoveredSong:
    """A discovered song with metadata."""
    video_id: str
    title: str
    artist: str
    strategy: str  # 'similar', 'artist', 'wildcard', 'library'
    reason: str  # Human-readable discovery reason
    for_user_id: int  # The user this song was picked for
    duration_seconds: int | None = None
    genre: str | None = None
    year: int | None = None


class TurnTracker:
    """Tracks democratic turn-based song selection per guild."""
    
    def __init__(self):
        self.guild_members: dict[int, list[int]] = {}  # guild_id -> ordered [user_ids]
        self.guild_index: dict[int, int] = {}  # guild_id -> current index
    
    def update_members(self, guild_id: int, member_ids: list[int]) -> None:
        """Update member list, preserving order for existing members."""
        if guild_id not in self.guild_members:
            self.guild_members[guild_id] = list(member_ids)
            self.guild_index[guild_id] = 0
            return
        
        current = self.guild_members[guild_id]
        
        # Keep existing members in order, add new ones at end
        new_list = [m for m in current if m in member_ids]
        for m in member_ids:
            if m not in new_list:
                new_list.append(m)
        
        self.guild_members[guild_id] = new_list
        
        # Adjust index if members left
        if self.guild_index[guild_id] >= len(new_list):
            self.guild_index[guild_id] = 0
    
    def get_current_user(self, guild_id: int) -> int | None:
        """Get the user whose turn it is."""
        if guild_id not in self.guild_members or not self.guild_members[guild_id]:
            return None
        
        idx = self.guild_index.get(guild_id, 0)
        return self.guild_members[guild_id][idx]
    
    def advance(self, guild_id: int) -> None:
        """Move to the next user."""
        if guild_id not in self.guild_members or not self.guild_members[guild_id]:
            return
        
        self.guild_index[guild_id] = (self.guild_index[guild_id] + 1) % len(self.guild_members[guild_id])


class DiscoveryEngine:
    """Intelligent song discovery engine."""
    
    DEFAULT_WEIGHTS = {"similar": 25, "artist": 25, "wildcard": 25, "library": 25}
    
    def __init__(
        self,
        youtube: YouTubeService,
        spotify: SpotifyService,
        normalizer: SongNormalizer,
        preference_crud: "PreferenceCRUD",
        playback_crud: "PlaybackCRUD",
        reaction_crud: "ReactionCRUD",
    ):
        self.youtube = youtube
        self.spotify = spotify
        self.normalizer = normalizer
        self.preferences = preference_crud
        self.playback = playback_crud
        self.reactions = reaction_crud
        self.turn_tracker = TurnTracker()
    
    async def get_next_song(
        self,
        guild_id: int,
        voice_member_ids: list[int],
        weights: dict[str, int] | None = None,
        cooldown_seconds: int = 7200,
    ) -> DiscoveredSong | None:
        """
        Get the next song for discovery playback.
        
        Uses democratic turn-based selection - each user gets songs picked for them in rotation.
        """
        if not voice_member_ids:
            return None
        
        # Update turn tracker with current members
        self.turn_tracker.update_members(guild_id, voice_member_ids)
        
        # Get current turn user
        turn_user_id = self.turn_tracker.get_current_user(guild_id)
        if not turn_user_id:
            return None
        
        # Get weights (from settings or defaults)
        weights = weights or self.DEFAULT_WEIGHTS
        
        # Migration: If old 3-strategy weights are found, reset to new 4-strategy default
        if weights and "library" not in weights:
            weights = self.DEFAULT_WEIGHTS
        
        # Get recent history (Cooldown check)
        # Using cooldown_seconds parameter (defaults to 2 hours)
        
        # We need to know which songs were played recently to avoid repeats
        # Because we only have YT IDs in the end, we'll fetch recent YT IDs
        recent_yt_ids = set(await self.playback.get_recent_history_window(guild_id, cooldown_seconds))
        
        # Also limit by count just in case time window is empty but we just played something
        recent_by_count = await self.playback.get_recent_history(guild_id, limit=20)
        recent_yt_ids.update(r["canonical_yt_id"] for r in recent_by_count)
        
        # Roll strategy
        strategies = list(weights.keys())
        strategy_weights = [weights[s] for s in strategies]
        strategy = random.choices(strategies, weights=strategy_weights, k=1)[0]
        
        logger.info(f"Discovery for user {turn_user_id} using strategy: {strategy} (avoiding {len(recent_yt_ids)} recent songs)")
        
        # Execute strategy
        track = await self._execute_strategy(strategy, turn_user_id, recent_yt_ids)
        
        # Advance turn for next time
        self.turn_tracker.advance(guild_id)
        
        if track:
            # Avoid missing duration at all costs: fetch if missing
            if track.duration_seconds is None:
                details = await self.youtube.get_track_info(track.video_id)
                if details and details.duration_seconds:
                    track.duration_seconds = details.duration_seconds

            return DiscoveredSong(
                video_id=track.video_id,
                title=track.title,
                artist=track.artist,
                strategy=strategy,
                reason=self._generate_reason(strategy, track),
                for_user_id=turn_user_id,
                duration_seconds=track.duration_seconds,
                year=track.year
            )
        
        return None
    
    async def _execute_strategy(
        self,
        strategy: str,
        user_id: int,
        recent_yt_ids: set[str],
    ) -> YTTrack | None:
        """Execute a discovery strategy."""
        if strategy == "similar":
            return await self._strategy_similar(user_id, recent_yt_ids)
        elif strategy == "artist":
            return await self._strategy_artist(user_id, recent_yt_ids)
        elif strategy == "wildcard":
            return await self._strategy_wildcard(recent_yt_ids)
        elif strategy == "library":
            return await self._strategy_library(user_id, recent_yt_ids)
        return None
    
    async def _strategy_library(self, user_id: int, recent_yt_ids: set[str]) -> YTTrack | None:
        """Fetch a random song from user's liked library."""
        # Get user's liked songs
        liked = await self.reactions.get_liked_songs(user_id, limit=100)
        
        # Filter out recent songs
        candidates = [t for t in liked if t.get("canonical_yt_id") not in recent_yt_ids]
        
        if candidates:
            # Pick a random liked song
            song = random.choice(candidates)
            return YTTrack(
                video_id=song["canonical_yt_id"],
                title=song["title"],
                artist=song["artist_name"],
                duration_seconds=song.get("duration_seconds"),
                year=song.get("release_year")
            )
        
        # Fallback to wildcard if library is empty or all songs recently played
        return await self._strategy_wildcard(recent_yt_ids)
    
    async def _strategy_similar(self, user_id: int, recent_yt_ids: set[str]) -> YTTrack | None:
        """Find a similar song based on user's liked songs."""
        # Get user's liked songs
        liked = await self.reactions.get_liked_songs(user_id, limit=20)
        if not liked:
            # Fallback to wildcard if no liked songs
            return await self._strategy_wildcard(recent_yt_ids)
        
        # Pick a random liked song to base recommendations on
        seed_song = random.choice(liked)
        
        # Get watch playlist (related songs)
        related = await self.youtube.get_watch_playlist(seed_song["canonical_yt_id"], limit=20)
        
        # Filter out recent songs and same artist
        candidates = [
            t for t in related
            if t.video_id not in recent_yt_ids
            and t.artist.lower() != seed_song["artist_name"].lower()
        ]
        
        if candidates:
            # Sort by year proximity if we have year data, then pick randomly from top 3
            # For now just pick randomly
            return random.choice(candidates[:5]) if len(candidates) > 5 else random.choice(candidates)
        
        return None
    
    async def _strategy_artist(self, user_id: int, recent_yt_ids: set[str]) -> YTTrack | None:
        """Find a different song from a liked artist."""
        # Get user's top artists from preferences
        top_artists = await self.preferences.get_top_preferences(user_id, "artist", limit=10)
        
        if not top_artists:
            # Fallback to wildcard
            return await self._strategy_wildcard(recent_yt_ids)
        
        # Try a few artists
        for artist_name, _ in random.sample(top_artists, min(3, len(top_artists))):
            # Search Spotify for artist top tracks
            sp_track = await self.spotify.search_track(artist_name)
            if sp_track:
                top_tracks = await self.spotify.get_artist_top_tracks(sp_track.artist_id)
                
                for track in random.sample(top_tracks, min(5, len(top_tracks))):
                    # Normalize to get YT video ID
                    normalized = await self.normalizer.normalize(track.title, track.artist)
                    if normalized and normalized.canonical_yt_id not in recent_yt_ids:
                        return YTTrack(
                            video_id=normalized.canonical_yt_id,
                            title=normalized.clean_title,
                            artist=normalized.clean_artist,
                        )
        
        return None
    
    async def _strategy_wildcard(self, recent_yt_ids: set[str]) -> YTTrack | None:
        """Get a random song from charts."""
        # Randomly pick US or UK charts
        region = random.choice(["US", "UK"])
        query = f"Top 100 Songs {region} 2024"
        
        # Search for chart playlists
        playlists = await self.youtube.search_playlists(query, limit=3)
        
        if not playlists:
            # Fallback: direct search for popular songs
            results = await self.youtube.search("top hits 2024", filter_type="songs", limit=20)
            candidates = [t for t in results if t.video_id not in recent_yt_ids]
            if candidates:
                return random.choice(candidates)
            return None
        
        # Get tracks from a random chart playlist
        playlist = random.choice(playlists)
        tracks = await self.youtube.get_playlist_tracks(playlist["browse_id"], limit=50)
        
        # Filter out recent
        candidates = [t for t in tracks if t.video_id not in recent_yt_ids]
        
        if candidates:
            return random.choice(candidates)
        
        return None
    
    def _generate_reason(self, strategy: str, song: YTTrack) -> str:
        """Generate a human-readable discovery reason."""
        if strategy == "similar":
            return f"🎵 Similar to songs you like"
        elif strategy == "artist":
            return f"🎤 From artist you enjoy: {song.artist}"
        elif strategy == "wildcard":
            return "🎲 Popular track you might like"
        elif strategy == "library":
            return "📚 From your saved library"
        return "Discovered for you"
