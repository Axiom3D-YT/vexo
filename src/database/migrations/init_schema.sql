-- Smart Discord Music Bot - Database Schema
-- SQLite

-- songs (canonical - normalized to YouTube video ID)
CREATE TABLE IF NOT EXISTS songs (
    id INTEGER PRIMARY KEY,
    canonical_yt_id TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    artist_name TEXT NOT NULL,
    album TEXT,
    release_year INTEGER,
    duration_seconds INTEGER,
    spotify_id TEXT,
    is_ephemeral BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- song_genres
CREATE TABLE IF NOT EXISTS song_genres (
    song_id INTEGER REFERENCES songs(id) ON DELETE CASCADE,
    genre TEXT NOT NULL,
    source TEXT CHECK(source IN ('spotify', 'inferred', 'user_tagged')),
    PRIMARY KEY (song_id, genre)
);

-- users
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active TIMESTAMP,
    is_banned BOOLEAN DEFAULT FALSE,
    opted_out BOOLEAN DEFAULT FALSE
);

-- user_preferences
CREATE TABLE IF NOT EXISTS user_preferences (
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    preference_type TEXT CHECK(preference_type IN ('genre', 'artist', 'decade', 'energy')),
    preference_key TEXT NOT NULL,
    affinity_score REAL DEFAULT 0.0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, preference_type, preference_key)
);

-- guilds
CREATE TABLE IF NOT EXISTS guilds (
    id INTEGER PRIMARY KEY,
    name TEXT,
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE
);

-- guild_settings
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id INTEGER REFERENCES guilds(id) ON DELETE CASCADE,
    setting_key TEXT NOT NULL,
    setting_value TEXT,
    PRIMARY KEY (guild_id, setting_key)
);

-- playback_sessions
CREATE TABLE IF NOT EXISTS playback_sessions (
    id TEXT PRIMARY KEY,
    guild_id INTEGER REFERENCES guilds(id),
    channel_id INTEGER,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP
);

-- session_listeners
CREATE TABLE IF NOT EXISTS session_listeners (
    session_id TEXT REFERENCES playback_sessions(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id),
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    left_at TIMESTAMP,
    PRIMARY KEY (session_id, user_id, joined_at)
);

-- playback_history
CREATE TABLE IF NOT EXISTS playback_history (
    id INTEGER PRIMARY KEY,
    session_id TEXT REFERENCES playback_sessions(id),
    song_id INTEGER REFERENCES songs(id),
    played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed BOOLEAN DEFAULT FALSE,
    skip_reason TEXT CHECK(skip_reason IN ('user', 'vote', 'error') OR skip_reason IS NULL),
    discovery_source TEXT CHECK(discovery_source IN ('user_request', 'similar', 'artist', 'wildcard', 'library')),
    discovery_reason TEXT,
    for_user_id INTEGER REFERENCES users(id)
);

-- song_reactions
CREATE TABLE IF NOT EXISTS song_reactions (
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    song_id INTEGER REFERENCES songs(id) ON DELETE CASCADE,
    reaction TEXT CHECK(reaction IN ('like', 'dislike', 'love', 'ban')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, song_id)
);

-- imported_playlists
CREATE TABLE IF NOT EXISTS imported_playlists (
    id INTEGER PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    platform TEXT CHECK(platform IN ('spotify', 'youtube')),
    platform_id TEXT,
    name TEXT,
    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    track_count INTEGER
);

-- global_settings
CREATE TABLE IF NOT EXISTS global_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- notifications
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY,
    level TEXT CHECK(level IN ('info', 'warning', 'error', 'success')),
    message TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    read BOOLEAN DEFAULT FALSE
);

-- New table for tracking song additions to the library
CREATE TABLE IF NOT EXISTS song_library_entries (
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    song_id INTEGER REFERENCES songs(id) ON DELETE CASCADE,
    source TEXT CHECK(source IN ('request', 'like', 'import')),
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, song_id, source)
);

-- Index for library queries
CREATE INDEX IF NOT EXISTS idx_library_song ON song_library_entries(song_id);
CREATE INDEX IF NOT EXISTS idx_library_user ON song_library_entries(user_id);


-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_songs_yt_id ON songs(canonical_yt_id);
CREATE INDEX IF NOT EXISTS idx_history_session ON playback_history(session_id);
CREATE INDEX IF NOT EXISTS idx_history_song ON playback_history(song_id);
CREATE INDEX IF NOT EXISTS idx_history_played_at ON playback_history(played_at);
CREATE INDEX IF NOT EXISTS idx_prefs_user ON user_preferences(user_id);
CREATE INDEX IF NOT EXISTS idx_reactions_user ON song_reactions(user_id);
CREATE INDEX IF NOT EXISTS idx_reactions_song ON song_reactions(song_id);
