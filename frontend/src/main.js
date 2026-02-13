import { signal, effect } from '@preact/signals';
import './style.css';

// Dashboard JavaScript
const API = {
    status: '/api/status',
    guilds: '/api/guilds',
    guild: (id) => `/api/guilds/${id}`,
    settings: (id) => `/api/guilds/${id}/settings`,
    analytics: '/api/analytics',
    users: '/api/users',
    songs: '/api/songs',
    library: '/api/library',
    topSongs: '/api/analytics/top-songs',
    userPrefs: (id) => `/api/users/${id}/preferences`,
    notifications: '/api/notifications',
    settings_global: '/api/settings/global',
    leave_guild: (id) => `/api/guilds/${id}/leave`,
};

// Reactive State
const currentGuild = signal(null);
const currentScope = signal('global');
let voiceChannels = [];
let currentGroqPrompts = [];

const DEFAULT_PROMPT_TEMPLATE = {
    name: "New Prompt",
    role: `You are a Cool, Knowledgeable Music Curator. You're not a radio DJ with a "voice"; you're that friend who always knows the perfect song for the moment. Your vibe is authentic, relaxed, and conversational.`,
    task: "Write a short, natural intro for the specified track.",
    word_count: "Keep it under 40 words.",
    output_format: `Return a valid JSON object with the following keys:
- "song": The song title (string)
- "artist": The artist name (string)
- "genre": The inferred genre (string)
- "release_date": The release year (string)
- "text": The intro script (string)`,
    guidelines: `Natural Flow: Avoid "radio announcer" clichés. Talk like a real person.
Connection: Focus on how the song *feels* or the specific moment it fits.
The Reveal: Have 1/3 chance to mention the Artist and Song (naturally).
Rhythm: Use natural pauses.`,
    vocal_cues: "Do NOT include any stage directions or bracketed text.",
    enabled: true
};
let ttsVoices = { tiktok_voices: [], gtts_voices: [] };
const libraryData = signal([]); // Full library cache
const libraryPage = signal(1);
const LIBRARY_PAGE_SIZE = 50;
const statusData = signal({ status: 'offline' });
const notifications = signal([]);

// Effects for reactive updates
effect(() => {
    fetchAnalytics();
    fetchLibrary();
});

effect(() => {
    if (currentScope.value !== 'global') {
        fetchSongs();
    }
});

effect(() => {
    updateStatus(statusData.value);
});

effect(() => {
    renderLibraryPage();
});

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    initWebSocket();
    fetchDashboardInit(); // Batch fetch
    fetchSongs();
    fetchLibrary();
    fetchUsers();

    setInterval(fetchStatus, 5000);
    setInterval(fetchGuilds, 10000);
    setInterval(fetchAnalytics, 15000);
    setInterval(fetchSongs, 30000);
    setInterval(fetchNotifications, 15000);

    // Tab handling
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', (e) => {
            e.preventDefault();
            switchTab(tab.dataset.tab);
        });
    });
});

// WebSocket and Log State
let ws = null;
let displayedLogIds = new Set();

function initWebSocket() {
    try {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/logs`;
        console.log(`[Dashboard] Connecting to WebSocket: ${wsUrl}`);

        ws = new WebSocket(wsUrl);
        ws.onopen = () => {
            console.log('[Dashboard] WebSocket connected');
            // When WS connects, we can rely on it
        };
        ws.onmessage = (e) => {
            const log = JSON.parse(e.data);
            addLogEntry(log);
        };
        ws.onclose = (e) => {
            console.warn('[Dashboard] WebSocket closed, retrying in 3s...', e.reason);
            setTimeout(initWebSocket, 3000);
        };
        ws.onerror = (err) => console.error('[Dashboard] WebSocket error:', err);
    } catch (e) {
        console.error('[Dashboard] WS Init Error', e);
    }
}

// Fallback Polling
setInterval(async () => {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        try {
            const res = await fetch('/api/logs');
            if (res.ok) {
                const data = await res.json();
                if (data.logs) {
                    data.logs.forEach(log => addLogEntry(log));
                }
            }
        } catch (e) {
            console.error('[Dashboard] Polling failed', e);
        }
    }
}, 10000); // Check every 10s if WS is down

let autoscrollEnabled = true;

// Initialize autoscroll toggle listener
document.addEventListener('DOMContentLoaded', () => {
    const toggle = document.getElementById('autoscroll-toggle');
    if (toggle) {
        toggle.addEventListener('change', (e) => {
            autoscrollEnabled = e.target.checked;
            if (autoscrollEnabled) {
                const logsEl = document.getElementById('logs');
                if (logsEl) logsEl.scrollTop = logsEl.scrollHeight;
            }
        });
    }
});

function addLogEntry(log) {
    const logsEl = document.getElementById('logs');
    if (!logsEl) return;

    // Smart autoscroll: Check if user is near the bottom
    // We use a larger threshold (100px) for better UX on high-density logs
    const isAtBottom = logsEl.scrollHeight - logsEl.scrollTop <= logsEl.clientHeight + 100;
    const isEmpty = logsEl.children.length === 0;

    // Create a unique key for the log to prevent duplicates
    const logId = `${log.timestamp}-${log.level}-${log.message.substring(0, 50)}`;
    if (displayedLogIds.has(logId)) return;
    displayedLogIds.add(logId);

    const time = new Date(log.timestamp * 1000).toLocaleTimeString();
    const entry = document.createElement('div');
    entry.className = `log-entry log-${log.level}`;
    entry.innerHTML = `<span class="log-time">${time}</span> [${log.level}] ${log.message}`;
    logsEl.appendChild(entry);

    // Scroll if enabled AND (user is already at bottom OR it's the first log)
    if (autoscrollEnabled && (isAtBottom || isEmpty)) {
        logsEl.scrollTop = logsEl.scrollHeight;
    }

    while (logsEl.children.length > 500) {
        logsEl.removeChild(logsEl.firstChild);
    }
}

// User Profile Functions
async function viewUser(userId) {
    if (!userId || userId === 'null' || userId === 'undefined') return;

    const modal = document.getElementById('user-modal');
    if (!modal) return;

    modal.classList.add('open');
    showModalSkeleton();

    try {
        const res = await fetch(`/api/users/${userId}/details`);
        const data = await res.json();
        renderUserProfile(data);
    } catch (e) {
        console.error('Failed to fetch user details', e);
        document.getElementById('user-modal-content').innerHTML = '<div style="padding: 2rem; text-align: center;">Failed to load profile</div>';
    }
}

function closeUserModal() {
    const modal = document.getElementById('user-modal');
    if (modal) modal.classList.remove('open');
}

function showModalSkeleton() {
    document.getElementById('user-modal-name').innerHTML = '<div class="skeleton skeleton-text" style="width: 150px"></div>';
    document.getElementById('user-modal-id').innerHTML = '<div class="skeleton skeleton-text" style="width: 100px"></div>';
    document.getElementById('user-modal-plays').innerHTML = '...';
    document.getElementById('user-modal-reactions').innerHTML = '...';
    document.getElementById('user-modal-playlists').innerHTML = '...';
}

function renderUserProfile(data) {
    const user = data.user || {};
    document.getElementById('user-modal-name').textContent = user.username || 'Unknown User';
    document.getElementById('user-modal-id').textContent = `ID: ${user.id}`;
    document.getElementById('user-modal-avatar').textContent = (user.username || '?').charAt(0);

    // Summary stats
    document.getElementById('user-modal-plays').textContent = data.reactions.length + (data.imported_playlists.length * 10); // Approximation if we don't have play count here
    document.getElementById('user-modal-reactions').textContent = data.reactions.length;
    document.getElementById('user-modal-playlists').textContent = data.imported_playlists.length;

    // Reactions List
    const reactionsList = document.getElementById('user-modal-reactions-list');
    if (data.reactions.length === 0) {
        reactionsList.innerHTML = '<tr><td colspan="3" style="text-align: center; color: var(--text-muted); padding: 1rem;">No likes yet</td></tr>';
    } else {
        reactionsList.innerHTML = data.reactions.map(r => `
            <tr>
                <td>${r.title}</td>
                <td>${r.artist_name}</td>
                <td>${new Date(r.created_at).toLocaleDateString()}</td>
            </tr>
        `).join('');
    }

    // Playlists List
    const playlistsList = document.getElementById('user-modal-playlists-list');
    if (data.imported_playlists.length === 0) {
        playlistsList.innerHTML = '<div style="text-align: center; color: var(--text-muted); padding: 1rem;">No playlists imported</div>';
    } else {
        playlistsList.innerHTML = data.imported_playlists.map(p => `
            <div class="server-nav-item" style="display: flex; justify-content: space-between; align-items: center; border: 1px solid var(--border);">
                <span>${p.name || 'Untitled Playlist'}</span>
                <span style="font-size: 0.7rem; color: var(--text-muted);">${p.source || 'spotify'}</span>
            </div>
        `).join('');
    }

    // Preferences List
    const prefsList = document.getElementById('user-modal-prefs-list');
    const prefs = data.preferences || {};
    let prefsHtml = '';

    // Flatten and sort preferences
    const allPrefs = [];
    for (const [type, keys] of Object.entries(prefs)) {
        for (const [key, score] of Object.entries(keys)) {
            allPrefs.push({ type, key, score });
        }
    }
    allPrefs.sort((a, b) => b.score - a.score);

    if (allPrefs.length === 0) {
        prefsHtml = '<div style="grid-column: span 2; text-align: center; color: var(--text-muted);">No affinity data</div>';
    } else {
        prefsHtml = allPrefs.slice(0, 20).map(p => `
            <div style="background: var(--bg-card); padding: 0.5rem; border-radius: 6px; font-size: 0.8rem; display: flex; justify-content: space-between;">
                <span style="color: var(--text-muted);">${p.type === 'genre' ? '🏷️' : '👨‍🎤'} ${p.key}</span>
                <span style="font-weight: 600;">${p.score.toFixed(1)}</span>
            </div>
        `).join('');
    }
    prefsList.innerHTML = prefsHtml;
}

window.viewUser = viewUser;
window.closeUserModal = closeUserModal;
window.switchModalTab = function (tabName) {
    document.querySelectorAll('.modal-tab').forEach(t => t.classList.remove('active'));
    document.querySelector(`.modal-tab[data-modal-tab="${tabName}"]`).classList.add('active');

    document.querySelectorAll('.modal-tab-pane').forEach(p => p.style.display = 'none');
    document.getElementById(`modal-${tabName}`).style.display = 'block';
};

async function fetchDashboardInit() {
    showSkeletons();
    try {
        const res = await fetch('/api/dashboard-init');
        const data = await res.json();

        if (data.status) updateStatus(data.status);
        if (data.guilds) {
            updateTopBar(data.guilds);
            // Populate server-specific dashboard stats if in server scope
            if (currentScope.value !== 'global') {
                const g = data.guilds.find(guild => guild.id === currentScope.value);
                if (g) {
                    const membersEl = document.getElementById('server-stat-members');
                    if (membersEl) membersEl.textContent = g.member_count || 0;

                    const queueEl = document.getElementById('server-stat-queue');
                    if (queueEl) queueEl.textContent = g.queue_size || 0;

                    const durationEl = document.getElementById('server-stat-duration');
                    if (durationEl) durationEl.textContent = `${g.queue_duration || 0}m`;
                }
            }
        }
        updateGuildList(data.guilds);
        updateNowPlaying(data.guilds);
        updateAnalytics(data.analytics);
        notifications.value = data.notifications;
        updateNotifications(data.notifications);

        if (!currentGuild.value && data.guilds.length > 0) {
            currentGuild.value = data.guilds[0].id;
        }

        initCharts(data.analytics);
    } catch (e) {
        console.error('Init failed', e);
    }
}

function showSkeletons() {
    // Basic skeleton state for key areas
    const np = document.getElementById('now-playing');
    if (np) np.innerHTML = `
        <div class="np-content">
            <div class="skeleton skeleton-artwork"></div>
            <div class="np-info">
                <div class="skeleton skeleton-text" style="width: 60%"></div>
                <div class="skeleton skeleton-text" style="width: 40%"></div>
                <div class="skeleton skeleton-text" style="width: 80%; height: 2rem; margin-top: 1rem;"></div>
            </div>
        </div>
    `;

    const stats = document.querySelectorAll('.stat-value');
    stats.forEach(s => s.innerHTML = '<span class="skeleton skeleton-text" style="width: 40px; height: 2rem;"></span>');
}

// Fetch functions
async function fetchStatus() {
    try {
        const res = await fetch(API.status);
        const data = await res.json();
        statusData.value = data;
    } catch (e) {
        console.error('Failed to fetch status', e);
        try { statusData.value = { status: 'offline' }; } catch (e2) { }
    }
}

function updateStatus(data) {
    try {
        const dot = document.querySelector('.status-dot');
        const text = document.getElementById('status-text');
        const guildCount = document.getElementById('stat-guilds');
        const voiceCount = document.getElementById('stat-voice');
        const latency = document.getElementById('stat-latency-val');
        const sidebarLatency = document.getElementById('stat-latency');
        const cpu = document.getElementById('stat-cpu');
        const ram = document.getElementById('stat-ram');

        if (dot) dot.className = `status-dot status-${data.status === 'online' ? 'online' : 'offline'}`;
        if (text) text.textContent = data.status === 'online' ? `Online (${data.latency_ms || 0}ms)` : 'Offline';
        if (guildCount) guildCount.textContent = data.guilds || 0;
        if (voiceCount) voiceCount.textContent = data.voice_connections || 0;
        if (latency) latency.textContent = `${data.latency_ms || 0}ms`;
        if (sidebarLatency) sidebarLatency.textContent = `${data.latency_ms || 0}ms`;
        if (cpu) cpu.textContent = data.cpu_percent || 0;
        if (ram) ram.textContent = data.ram_percent || 0;
        const uptime = document.getElementById('stat-uptime');
        if (uptime) uptime.textContent = formatUptime(data.uptime_seconds);
    } catch (e) { console.error('Error updating status', e); }
}

async function fetchGuilds() {
    try {
        const res = await fetch(API.guilds);
        const data = await res.json();

        if (!currentGuild.value && data.guilds && data.guilds.length > 0) {
            currentGuild.value = data.guilds[0].id;
        }

        updateTopBar(data.guilds || []);
        updateGuildList(data.guilds || []);
        updateNowPlaying(data.guilds || []);

        // Sync server stats if in server scope
        if (currentScope.value !== 'global' && data.guilds) {
            const g = data.guilds.find(guild => guild.id === currentScope.value);
            if (g) {
                const membersEl = document.getElementById('server-stat-members');
                if (membersEl) membersEl.textContent = g.member_count || 0;

                const queueEl = document.getElementById('server-stat-queue');
                if (queueEl) queueEl.textContent = g.queue_size || 0;

                const durationEl = document.getElementById('server-stat-duration');
                if (durationEl) durationEl.textContent = `${g.queue_duration || 0}m`;
            }
        }
    } catch (e) {
        console.error('Failed to fetch guilds', e);
    }
}

function updateTopBar(guilds) {
    const nav = document.getElementById('server-nav');
    if (!nav) return;

    let html = `<div class="server-nav-item ${currentScope.value === 'global' ? 'active' : ''}" onclick="switchScope('global')">Global</div>`;

    html += guilds.map(g => `
        <div class="server-nav-item ${currentScope.value === g.id ? 'active' : ''}" onclick="switchScope('${g.id}')">
            ${g.name || 'Server'}
            ${g.is_playing ? ' 🔊' : ''}
        </div>
    `).join('');

    nav.innerHTML = html;
}

function updateGuildList(guilds) {
    const list = document.getElementById('guild-list');
    if (!list) return;

    list.innerHTML = guilds.map(g => `
        <div class="user-item ${g.id === currentGuild.value ? 'active' : ''}" onclick="selectGuild(event, '${g.id}')">
            <div class="user-avatar">${(g.name || '?').charAt(0)}</div>
            <div class="user-info">
                <div class="user-name">${g.name || 'Unknown Server'}</div>
                <div class="user-stats">${g.member_count || 0} members</div>
            </div>
            <div class="user-actions" style="display: flex; gap: 0.5rem; align-items: center;">
                ${g.is_playing ? '<span style="color: var(--success); font-size: 0.8rem;">▶ Playing</span>' : ''}
                <button class="btn btn-secondary" style="padding: 0.2rem 0.5rem; font-size: 0.7rem;" onclick="event.stopPropagation(); leaveGuild('${g.id}')">Leave</button>
            </div>
        </div>
    `).join('');
}

async function leaveGuild(id) {
    if (!confirm('Are you sure you want the bot to leave this server?')) return;
    try {
        const res = await fetch(API.leave_guild(id), { method: 'POST' });
        if (res.ok) fetchGuilds();
        else alert('Failed to leave server');
    } catch (e) {
        console.error(e);
        alert('Error leaving server');
    }
}

async function leaveServer() {
    if (!currentScope.value || currentScope.value === 'global') return;
    const id = currentScope.value;
    if (!confirm('Are you sure you want the bot to leave this server? This action is permanent!')) return;

    try {
        const res = await fetch(API.leave_guild(id), { method: 'POST' });
        if (res.ok) {
            alert('Bot has left the server.');
            switchScope('global');
            fetchDashboardInit();
        } else {
            alert('Failed to leave server');
        }
    } catch (e) {
        console.error(e);
        alert('Error leaving server');
    }
}

function updateNowPlaying(guilds) {
    const np = document.getElementById('now-playing');
    if (!np) return;

    let playing = null;
    if (currentScope.value === 'global') {
        playing = guilds.find(g => g.is_playing && g.current_song);
    } else {
        playing = guilds.find(g => g.id === currentScope.value && g.is_playing && g.current_song);
    }

    if (playing) {
        let durationStr = '';
        if (playing.duration_seconds) {
            const mins = Math.floor(playing.duration_seconds / 60);
            const secs = playing.duration_seconds % 60;
            durationStr = `${mins}:${secs.toString().padStart(2, '0')}`;
        }

        np.innerHTML = `
            <div class="np-content">
                <img class="np-artwork" src="https://img.youtube.com/vi/${playing.video_id || 'dQw4w9WgXcQ'}/hqdefault.jpg" alt="Album art">
                <div class="np-info">
                    <div class="np-title">${playing.current_song || 'Unknown'}</div>
                    <div class="np-artist">${playing.current_artist || 'Unknown Artist'}</div>
                    <div class="np-metadata">
                        ${durationStr ? `<span>⏳ ${durationStr}</span>` : ''}
                        ${playing.genre ? `<span>🏷️ ${playing.genre}</span>` : ''}
                        ${playing.year ? `<span>📅 ${playing.year}</span>` : ''}
                    </div>
                    ${playing.discovery_reason ? `<div class="np-discovery">${playing.discovery_reason}</div>` : ''}
                    <div class="np-controls">
                        <button class="np-btn" onclick="control('pause')">⏸️</button>
                        <button class="np-btn" onclick="control('skip')">⏭️</button>
                        <button class="np-btn" onclick="control('stop')">⏹️</button>
                    </div>
                </div>
            </div>
        `;
        np.style.display = 'block';
    } else {
        np.innerHTML = `<div class="np-content" style="justify-content: center;"><span style="color: var(--text-muted);">Nothing playing</span></div>`;
    }
}

async function fetchAnalytics() {
    try {
        let url = API.analytics;
        if (currentScope.value !== 'global') url += `?guild_id=${currentScope.value}`;
        const res = await fetch(url);
        const data = await res.json();
        updateAnalytics(data);
    } catch (e) { console.error('Failed to fetch analytics', e); }
}

function updateAnalytics(data) {
    try {
        const songsEl = document.getElementById('stat-songs');
        const usersEl = document.getElementById('stat-users');
        const elPlays = document.getElementById('stat-plays');
        if (elPlays) elPlays.textContent = data.total_plays || 0;

        const elUptime = document.getElementById('stat-uptime');
        if (elUptime && data.uptime_seconds) {
            elUptime.textContent = formatUptime(data.uptime_seconds);
        }
        if (songsEl) songsEl.textContent = data.total_songs || 0;
        if (usersEl) usersEl.textContent = data.total_users || 0;

        // Global specific stats
        const songsGlobal = document.getElementById('stat-songs-global');
        if (songsGlobal) songsGlobal.textContent = data.total_songs || 0;
        const playsGlobal = document.getElementById('stat-plays-global');
        if (playsGlobal) playsGlobal.textContent = data.total_plays || 0;
        const usersGlobal = document.getElementById('stat-users-global');
        if (usersGlobal) usersGlobal.textContent = data.total_users || 0;

        // Top songs
        const songTable = document.getElementById('top-songs-table');
        if (songTable) {
            if (!data.top_songs || data.top_songs.length === 0) {
                songTable.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-muted); padding: 2rem;">No songs played yet</td></tr>';
            } else {
                songTable.innerHTML = data.top_songs.slice(0, 10).map((s, i) => `
                    <tr>
                        <td>${i + 1}</td>
                        <td>
                            <div class="song-cell">
                                <img class="song-thumb" src="https://img.youtube.com/vi/${s.yt_id}/default.jpg" alt="">
                                <div class="song-info">
                                    <span class="song-name">${s.title}</span>
                                    <span class="song-artist">${s.artist}</span>
                                </div>
                            </div>
                        </td>
                        <td>${s.plays}</td>
                        <td>${s.likes} ❤️</td>
                    </tr>
                `).join('');
            }
        }

        // Top users
        const userList = document.getElementById('top-users-list');
        if (userList) {
            if (!data.top_users || data.top_users.length === 0) {
                userList.innerHTML = '<div style="text-align: center; color: var(--text-muted); padding: 1rem;">No users active yet</div>';
            } else {
                userList.innerHTML = data.top_users.slice(0, 10).map(u => `
                    <div class="user-item" style="cursor: pointer" onclick="viewUser('${u.id}')">
                        <div class="user-avatar">${(u.name || '?').charAt(0)}</div>
                        <div class="user-info">
                            <div class="user-name">${u.name || 'Unknown'}</div>
                            <div class="user-stats">${u.plays || 0} plays • ${u.total_likes || 0} likes</div>
                        </div>
                    </div>
                `).join('');
            }
        }

        // Insights
        const elements = {
            'insight-liked-genre': data.top_liked_genres?.[0]?.name,
            'insight-liked-artist': data.top_liked_artists?.[0]?.name,
            'insight-liked-song': data.top_liked_songs?.[0] ? `${data.top_liked_songs[0].title} by ${data.top_liked_songs[0].artist}` : null,
            'insight-played-genre': data.top_played_genres?.[0]?.name,
            'insight-played-artist': data.top_played_artists?.[0]?.name
        };
        for (const [id, val] of Object.entries(elements)) {
            const el = document.getElementById(id);
            if (el) el.textContent = val || '-';
        }

        const usefulList = document.getElementById('useful-users-list');
        if (usefulList) {
            if (!data.top_useful_users || data.top_useful_users.length === 0) {
                usefulList.innerHTML = '<div style="text-align: center; color: var(--text-muted); padding: 1rem;">No useful activity yet</div>';
            } else {
                usefulList.innerHTML = data.top_useful_users.map(u => `
                    <div class="user-item">
                        <div class="user-avatar" style="background: var(--gradient-2)">${(u.username || '?').charAt(0)}</div>
                        <div class="user-info">
                            <div class="user-name">${u.username || 'Unknown'}</div>
                            <div class="user-stats">${u.score || 0} helpfulness points</div>
                        </div>
                    </div>
                `).join('');
            }
        }

        updateCharts(data);
        if (currentScope.value !== 'global') {
            updateServerCharts(data);
        }
    } catch (e) {
        console.error('Error updating analytics', e);
    }
}

let charts = {};

function initCharts(data) {
    if (!window.Chart) return;

    const colors = {
        primary: '#8b5cf6',
        secondary: '#ec4899',
        tertiary: '#06b6d4',
        text: '#64748b'
    };

    // Plays Chart
    const ctxPlays = document.getElementById('plays-chart');
    if (ctxPlays) {
        if (charts.plays) charts.plays.destroy();
        charts.plays = new Chart(ctxPlays, {
            type: 'line',
            data: {
                labels: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
                datasets: [{
                    label: 'Plays',
                    data: data.playback_trends || [10, 25, 15, 30, 45, 20, 35],
                    borderColor: colors.primary,
                    backgroundColor: 'rgba(139, 92, 246, 0.1)',
                    fill: true,
                    tension: 0.4
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    y: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: colors.text } },
                    x: { grid: { display: false }, ticks: { color: colors.text } }
                }
            }
        });
    }

    // Genres Chart
    const ctxGenres = document.getElementById('genres-chart');
    if (ctxGenres) {
        if (charts.genres) charts.genres.destroy();
        const topGenres = (data.top_played_genres || []).slice(0, 5);
        charts.genres = new Chart(ctxGenres, {
            type: 'doughnut',
            data: {
                labels: topGenres.map(g => g.name || 'Unknown'),
                datasets: [{
                    data: topGenres.map(g => g.plays || 0),
                    backgroundColor: [colors.primary, colors.secondary, colors.tertiary, '#f59e0b', '#10b981'],
                    borderWidth: 0
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { position: 'right', labels: { color: colors.text, padding: 20 } }
                }
            }
        });
    }

    // Server Plays Chart
    const ctxServerPlays = document.getElementById('server-plays-chart');
    if (ctxServerPlays) {
        if (charts.serverPlays) charts.serverPlays.destroy();
        charts.serverPlays = new Chart(ctxServerPlays, {
            type: 'line',
            data: {
                labels: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
                datasets: [{
                    label: 'Server Plays',
                    data: data.playback_trends || [0, 0, 0, 0, 0, 0, 0],
                    borderColor: colors.secondary,
                    backgroundColor: 'rgba(236, 72, 153, 0.1)',
                    fill: true,
                    tension: 0.4
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    y: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: colors.text } },
                    x: { grid: { display: false }, ticks: { color: colors.text } }
                }
            }
        });
    }

    // Peak Hours Chart
    const ctxPeak = document.getElementById('peak-chart');
    if (ctxPeak) {
        if (charts.peak) charts.peak.destroy();
        charts.peak = new Chart(ctxPeak, {
            type: 'bar',
            data: {
                labels: Array.from({ length: 24 }, (_, i) => `${i}h`),
                datasets: [{
                    label: 'Plays',
                    data: data.peak_hours || Array(24).fill(0),
                    backgroundColor: colors.tertiary,
                    borderRadius: 4
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    y: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: colors.text } },
                    x: { grid: { display: false }, ticks: { color: colors.text, font: { size: 9 } } }
                }
            }
        });
    }
}

function updateCharts(data) {
    if (!data) return;

    if (charts.plays && data.playback_trends) {
        charts.plays.data.datasets[0].data = data.playback_trends;
        charts.plays.update();
    }
    if (charts.genres && data.top_played_genres) {
        const topGenres = data.top_played_genres.slice(0, 5);
        charts.genres.data.labels = topGenres.map(g => g.name);
        charts.genres.data.datasets[0].data = topGenres.map(g => g.plays);
        charts.genres.update();
    }
    if (charts.peak && data.peak_hours) {
        charts.peak.data.datasets[0].data = data.peak_hours;
        charts.peak.update();
    }
}

function updateServerCharts(data) {
    if (charts.serverPlays && data.playback_trends) {
        charts.serverPlays.data.datasets[0].data = data.playback_trends;
        charts.serverPlays.update();
    }
}

async function fetchSongs() {
    try {
        let url = API.songs;
        if (currentScope.value !== 'global') url += `?guild_id=${currentScope.value}`;
        const res = await fetch(url);
        const data = await res.json();
        updateSongsList(data.songs || []);
    } catch (e) { console.error(e); }
}

function updateSongsList(songs) {
    const list = document.getElementById('songs-list');
    if (!list) return;

    if (songs.length === 0) {
        list.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-muted);">No songs found</td></tr>';
        return;
    }

    list.innerHTML = songs.map(s => {
        let durationStr = '-';
        if (s.duration_seconds) {
            const mins = Math.floor(s.duration_seconds / 60);
            const secs = s.duration_seconds % 60;
            durationStr = `${mins}:${secs.toString().padStart(2, '0')}`;
        }
        let timeStr = s.played_at ? new Date(s.played_at).toLocaleString() : 'Never';

        return `
            <tr>
                <td>${s.title}</td>
                <td>${s.artist_name}</td>
                <td>${durationStr}</td>
                <td>${s.genre || '-'}</td>
                <td><span class="user-list" style="cursor: pointer" onclick="viewUser('${s.requested_by_id}')">${s.requested_by || '-'}</span></td>
                <td><span class="user-list liked">${s.liked_by || '-'}</span></td>
                <td><span class="user-list disliked">${s.disliked_by || '-'}</span></td>
                <td>${timeStr}</td>
            </tr>
        `;
    }).join('');
}

async function fetchUsers() {
    try {
        let url = API.users;
        if (currentScope.value !== 'global') url += `?guild_id=${currentScope.value}`;
        const res = await fetch(url);
        const data = await res.json();
        updateUserDirectory(data.users || []);
    } catch (e) { console.error(e); }
}

function updateUserDirectory(users) {
    const list = document.getElementById('users-directory');
    if (!list) return;

    if (users.length === 0) {
        list.innerHTML = '<div style="text-align: center; color: var(--text-muted);">No users found</div>';
        return;
    }

    list.innerHTML = users.map(u => `
        <div class="user-item" style="cursor: pointer" onclick="viewUser('${u.id}')">
            <div class="user-avatar">${(u.username || '?').charAt(0)}</div>
            <div class="user-info">
                <div class="user-name">${u.username || 'Unknown'}</div>
                <div class="user-stats">${u.formatted_id || u.discord_id || 'ID: ' + u.id}</div>
            </div>
            <div class="user-metrics" style="margin-left: auto; text-align: right; font-size: 0.8rem; color: var(--text-muted);">
                <div>${u.reactions || 0} reactions</div>
                <div>${u.playlists || 0} playlists</div>
            </div>
        </div>
    `).join('');
}

async function fetchLibrary() {
    try {
        let url = API.library;
        if (currentScope.value !== 'global') url += `?guild_id=${currentScope.value}`;
        const res = await fetch(url);
        const data = await res.json();
        libraryData.value = data.library || [];
        libraryPage.value = 1;
        renderLibraryPage();
    } catch (e) { console.error(e); }
}

function renderLibraryPage() {
    const list = document.getElementById('library-list');
    const pagination = document.getElementById('library-pagination');
    if (!list || !pagination) return;

    if (libraryData.value.length === 0) {
        list.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-muted);">Library is empty</td></tr>';
        pagination.innerHTML = '';
        return;
    }

    const start = (libraryPage.value - 1) * LIBRARY_PAGE_SIZE;
    const end = start + LIBRARY_PAGE_SIZE;
    const pageData = libraryData.value.slice(start, end);

    list.innerHTML = pageData.map(s => {
        let dateStr = s.last_added ? new Date(s.last_added).toLocaleDateString() : '-';
        const sourceMap = { 'request': '📨 Request', 'like': '❤️ Like', 'import': '📥 Import' };
        const sourcesFormatted = (s.sources || '').split(',').map(src => sourceMap[src] || src).join(', ');

        return `
            <tr>
                <td>${s.title}</td>
                <td>${s.artist_name}</td>
                <td>${s.genre || '-'}</td>
                <td>${sourcesFormatted}</td>
                <td><span class="user-list">${s.contributors || '-'}</span></td>
                <td>${dateStr}</td>
            </tr>
        `;
    }).join('');

    // Pagination controls
    const totalPages = Math.ceil(libraryData.value.length / LIBRARY_PAGE_SIZE);
    pagination.innerHTML = `
        <button class="page-btn" ${libraryPage.value === 1 ? 'disabled' : ''} onclick="changeLibraryPage(-1)">Previous</button>
        <span class="page-info">Page ${libraryPage.value} of ${totalPages} (${libraryData.value.length} items)</span>
        <button class="page-btn" ${libraryPage.value === totalPages ? 'disabled' : ''} onclick="changeLibraryPage(1)">Next</button>
    `;
}

function changeLibraryPage(delta) {
    libraryPage.value += delta;
    document.getElementById('library-table').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function updateLibraryList(library) {
    // Redundant now, but keeping for compatibility if called elsewhere
    libraryData.value = library;
    libraryPage.value = 1;
}

function selectGuild(e, id) {
    currentGuild.value = id;
    document.querySelectorAll('.user-item').forEach(el => el.classList.remove('active'));
    if (e && e.currentTarget) e.currentTarget.classList.add('active');
}


function control(action) {
    if (currentGuild.value) {
        fetch(`/api/guilds/${currentGuild.value}/control/${action}`, { method: 'POST' })
            .then(() => setTimeout(fetchGuilds, 200));
    }
}

function switchTab(tab) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    const tabBtn = document.querySelector(`[data-tab="${tab}"]`);
    if (tabBtn) tabBtn.classList.add('active');

    document.querySelectorAll('.tab-content').forEach(c => c.style.display = 'none');
    const content = document.getElementById(`tab-${tab}`);
    if (content) content.style.display = 'block';

    if (tab === 'settings') {
        loadSettingsTab();
    }
}

async function switchScope(scope, targetTab = 'dashboard') {
    currentScope.value = scope;

    // Toggle Layout Mode
    if (scope === 'global') {
        document.body.classList.remove('view-server');
        document.body.classList.add('view-global');
    } else {
        document.body.classList.remove('view-global');
        document.body.classList.add('view-server');
    }

    document.querySelectorAll('.server-nav-item').forEach(el => {
        el.classList.remove('active');
        if (scope === 'global' && el.textContent.includes('Global')) el.classList.add('active');
        // This handles cases where el.textContent might be different
        if (el.getAttribute('onclick')?.includes(`'${scope}'`)) el.classList.add('active');
    });

    if (scope === 'global') {
        fetchStatus();
        switchTab(targetTab);
        // Reset labels
        const gLabel = document.getElementById('stat-guilds');
        if (gLabel) gLabel.nextElementSibling.textContent = 'Servers';
    } else {
        try {
            const res = await fetch(API.guild(scope));
            const data = await res.json();
            const gCard = document.getElementById('stat-guilds');
            if (gCard) {
                gCard.textContent = data.queue_size || 0;
                gCard.nextElementSibling.textContent = 'In Queue';
            }
            switchTab(targetTab);
        } catch (e) {
            console.error(scope, e);
        }
    }
}

function openGlobalSettings() {
    switchScope('global', 'settings');
}

async function loadSettingsTab() {
    const title = document.getElementById('settings-title');
    const globalBlock = document.getElementById('settings-global');
    const serverBlock = document.getElementById('settings-server');

    if (currentScope.value === 'global') {
        if (title) title.textContent = '⚙️ Global Settings';
        if (globalBlock) globalBlock.style.display = 'block';
        if (serverBlock) serverBlock.style.display = 'none';

        // Fetch global
        try {
            const res = await fetch(API.settings_global);
            const data = await res.json();

            const elMax = document.getElementById('setting-max-servers-tab');
            if (elMax) elMax.value = data.max_concurrent_servers || '';

            const elTest = document.getElementById('setting-test-mode');
            if (elTest) elTest.checked = !!data.test_mode;

            const elDur = document.getElementById('setting-test-duration');
            if (elDur) elDur.value = data.playback_duration || 30;

            const groqEnabled = data.groq_enabled !== false; // This is now removed from global, but keeping logic for cleanliness or removing if totally unused
            // We removed the UI for groq in global settings, so we can skip loading it here.
        } catch (e) {
            console.error(e);
        }
    } else {
        if (title) title.textContent = '⚙️ Server Settings';
        if (globalBlock) globalBlock.style.display = 'none';
        if (serverBlock) serverBlock.style.display = 'block';

        // Fetch server
        try {
            const res = await fetch(API.settings(currentScope.value));
            const data = await res.json();

            const pb = document.getElementById('setting-pre-buffer');
            if (pb) pb.checked = !!data.pre_buffer;

            const ba = document.getElementById('setting-buffer-amount');
            if (ba) {
                ba.value = data.buffer_amount || 1;
                const val = document.getElementById('buffer-val');
                if (val) val.textContent = ba.value;
            }

            // New Playback & Connectivity
            const s247 = document.getElementById('setting-247');
            if (s247) s247.checked = !!data.twenty_four_seven;

            const sAuto = document.getElementById('setting-autoconnect');
            if (sAuto) sAuto.checked = !!data.auto_connect;
            toggleVisible('autoconnect-options', sAuto?.checked);

            // Fetch guild details for voice channels
            try {
                const gRes = await fetch(API.guild(currentScope.value)); // Changed from API.guild_detail
                const gData = await gRes.json();
                voiceChannels = gData.voice_channels || [];
                populateVoiceChannels(data.auto_connect_channel);
            } catch (e) { console.error('Failed to fetch voice channels', e); }

            // Groq Advanced
            const sGroqE = document.getElementById('setting-groq-enabled');
            if (sGroqE) sGroqE.checked = data.groq_enabled !== false;
            toggleVisible('groq-advanced-options', sGroqE?.checked);

            const sGroqT = document.getElementById('setting-groq-text');
            if (sGroqT) sGroqT.checked = data.groq_send_text !== false;

            const sGroqO = document.getElementById('setting-groq-offset');
            if (sGroqO) sGroqO.value = data.groq_offset || 0;

            const sGroqModel = document.getElementById('setting-groq-model');
            const sGroqModelCustom = document.getElementById('setting-groq-model-custom');
            if (sGroqModel && sGroqModelCustom) {
                const model = data.groq_model || 'groq/compound-mini';
                // Check if model is in options
                const options = Array.from(sGroqModel.options).map(o => o.value);
                if (options.includes(model)) {
                    sGroqModel.value = model;
                    sGroqModelCustom.style.display = 'none';
                } else {
                    sGroqModel.value = 'custom';
                    sGroqModelCustom.value = model;
                    sGroqModelCustom.style.display = 'block';
                }
            }

            const sGroqFallback = document.getElementById('setting-groq-model-fallback');
            if (sGroqFallback) sGroqFallback.value = data.groq_model_fallback || '';

            currentGroqPrompts = data.groq_custom_prompts || [];
            renderGroqPrompts();

            // TTS Settings
            const sTtsE = document.getElementById('setting-tts-enabled');
            if (sTtsE) sTtsE.checked = !!data.tts_enabled;
            toggleVisible('tts-options', sTtsE?.checked);

            const sTtsS = document.getElementById('setting-tts-slow');
            if (sTtsS) sTtsS.checked = !!data.tts_slow;

            populateTTSVoices(data.tts_voice);

            const md = document.getElementById('setting-max-duration');
            if (md) md.value = data.max_song_duration || 6;

            const ed = document.getElementById('setting-ephemeral-duration');
            if (ed) ed.value = data.ephemeral_duration || 10;

            // Discovery weights
            const weights = data.discovery_weights || { similar: 25, artist: 25, wildcard: 25, library: 25 };
            const wSimilar = document.getElementById('weight-similar');
            if (wSimilar) wSimilar.value = weights.similar || 0;
            const wArtist = document.getElementById('weight-artist');
            if (wArtist) wArtist.value = weights.artist || 0;
            const wWildcard = document.getElementById('weight-wildcard');
            if (wWildcard) wWildcard.value = weights.wildcard || 0;
            const wLibrary = document.getElementById('weight-library');
            if (wLibrary) wLibrary.value = weights.library || 0;

            validateWeights();

            // Metadata Config
            const meta = data.metadata_config || {
                strategy: 'fallback',
                engines: {
                    spotify: { enabled: true, priority: 1 },
                    discogs: { enabled: true, priority: 2 },
                    musicbrainz: { enabled: true, priority: 3 }
                }
            };

            const elStrategy = document.getElementById('meta-strategy');
            if (elStrategy) elStrategy.value = meta.strategy || 'fallback';

            // Discogs
            const dEnabled = document.getElementById('meta-discogs-enabled');
            if (dEnabled) dEnabled.checked = meta.engines?.discogs?.enabled !== false;
            const dPrio = document.getElementById('meta-discogs-prio');
            if (dPrio) dPrio.value = meta.engines?.discogs?.priority || 2;

            // MusicBrainz
            const mEnabled = document.getElementById('meta-mb-enabled');
            if (mEnabled) mEnabled.checked = meta.engines?.musicbrainz?.enabled !== false;
            const mPrio = document.getElementById('meta-mb-prio');
            if (mPrio) mPrio.value = meta.engines?.musicbrainz?.priority || 3;

            // Spotify (Always enabled, just priority)
            const sPrio = document.getElementById('meta-spotify-prio');
            if (sPrio) sPrio.value = meta.engines?.spotify?.priority || 1;
        } catch (e) { console.error(e); }
    }
}

async function fetchNotifications() {
    try {
        const res = await fetch(API.notifications);
        const data = await res.json();
        updateNotifications(data.notifications || []);
    } catch (e) { console.error(e); }
}

function updateNotifications(list) {
    const container = document.getElementById('notif-list');
    const dot = document.getElementById('notif-dot');
    if (!container) return;

    if (list.length === 0) {
        container.innerHTML = '<div class="notif-item" style="color: var(--text-muted); text-align: center;">No new notifications</div>';
        if (dot) dot.style.display = 'none';
        return;
    }

    if (dot) dot.style.display = 'block';
    container.innerHTML = list.map(n => `
        <div class="notif-item">
            <div style="font-weight: 500; color: var(--${n.level === 'error' ? 'error' : n.level === 'warning' ? 'warning' : 'text-primary'})">${n.level.toUpperCase()}</div>
            <div>${n.message}</div>
            <div class="notif-time">${new Date(n.created_at * 1000).toLocaleString()}</div>
        </div>
    `).join('');
}

function toggleNotifications() {
    if (dd) dd.classList.toggle('show');
}

async function saveServerSettings() {
    if (!currentScope.value || currentScope.value === 'global') return;
    const targetGuild = currentScope.value;

    const body = {
        pre_buffer: document.getElementById('setting-pre-buffer').checked,
        buffer_amount: parseInt(document.getElementById('setting-buffer-amount').value),
        max_song_duration: parseInt(document.getElementById('setting-max-duration').value) || 6,
        ephemeral_duration: parseInt(document.getElementById('setting-ephemeral-duration').value) || 10,
        twenty_four_seven: document.getElementById('setting-247').checked,
        auto_connect: document.getElementById('setting-autoconnect').checked,
        auto_connect_channel: document.getElementById('setting-autoconnect-channel').value,
        groq_enabled: document.getElementById('setting-groq-enabled').checked,
        groq_send_text: document.getElementById('setting-groq-text').checked,
        groq_offset: parseInt(document.getElementById('setting-groq-offset').value || 0),
        groq_model: (document.getElementById('setting-groq-model').value === 'custom')
            ? document.getElementById('setting-groq-model-custom').value
            : document.getElementById('setting-groq-model').value,
        groq_model_fallback: document.getElementById('setting-groq-model-fallback').value,
        groq_model_fallback: document.getElementById('setting-groq-model-fallback').value,
        groq_custom_prompts: currentGroqPrompts,
        tts_enabled: document.getElementById('setting-tts-enabled').checked,
        tts_voice: document.getElementById('setting-tts-voice').value,
        tts_slow: document.getElementById('setting-tts-slow').checked,
        discovery_weights: {
            similar: parseInt(document.getElementById('weight-similar').value) || 0,
            artist: parseInt(document.getElementById('weight-artist').value) || 0,
            wildcard: parseInt(document.getElementById('weight-wildcard').value) || 0,
            library: parseInt(document.getElementById('weight-library').value) || 0
        },
        metadata_config: {
            strategy: document.getElementById('meta-strategy').value,
            engines: {
                groq: {
                    enabled: document.getElementById('meta-groq-enabled').checked,
                    priority: parseInt(document.getElementById('meta-groq-prio').value) || 1
                },
                spotify: { enabled: true, priority: parseInt(document.getElementById('meta-spotify-prio').value) || 2 },
                discogs: {
                    enabled: document.getElementById('meta-discogs-enabled').checked,
                    priority: parseInt(document.getElementById('meta-discogs-prio').value) || 3
                },
                musicbrainz: {
                    enabled: document.getElementById('meta-mb-enabled').checked,
                    priority: parseInt(document.getElementById('meta-mb-prio').value) || 4
                }
            }
        }
    };

    try {
        const res = await fetch(API.settings(targetGuild), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });

        if (res.ok) alert('Settings saved!');
        else alert('Failed to save settings');
    } catch (e) {
        console.error(e);
        alert('Error saving settings');
    }
}

async function saveSettingsTab() {
    const maxServers = document.getElementById('setting-max-servers-tab').value;
    const testMode = document.getElementById('setting-test-mode').checked;
    const testDuration = document.getElementById('setting-test-duration').value;

    try {
        const res = await fetch(API.settings_global, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                max_concurrent_servers: maxServers ? parseInt(maxServers) : null,
                test_mode: testMode,
                playback_duration: testDuration ? parseInt(testDuration) : 30
            })
        });

        if (res.ok) alert('Global settings saved!');
        else alert('Failed to save global settings');
    } catch (e) {
        console.error(e);
        alert('Error saving global settings');
    }
}

function validateWeights() {
    const similar = parseInt(document.getElementById('weight-similar').value) || 0;
    const artist = parseInt(document.getElementById('weight-artist').value) || 0;
    const wildcard = parseInt(document.getElementById('weight-wildcard').value) || 0;
    const library = parseInt(document.getElementById('weight-library').value) || 0;

    const total = similar + artist + wildcard + library;
    const totalEl = document.getElementById('weights-total');
    if (totalEl) {
        totalEl.textContent = total;
        totalEl.style.color = (total === 100) ? 'var(--text-primary)' : 'var(--warning)';
    }

    const errorEl = document.getElementById('weight-error');
    if (errorEl) {
        errorEl.style.display = (total === 100) ? 'none' : 'block';
    }
}

function formatUptime(seconds) {
    if (!seconds) return '0s';
    const days = Math.floor(seconds / (24 * 3600));
    seconds %= (24 * 3600);
    const hours = Math.floor(seconds / 3600);
    seconds %= 3600;
    const minutes = Math.floor(seconds / 60);
    const secs = seconds % 60;

    let parts = [];
    if (days > 0) parts.push(`${days}d`);
    if (hours > 0) parts.push(`${hours}h`);
    if (minutes > 0) parts.push(`${minutes}m`);
    if (secs > 0 || parts.length === 0) parts.push(`${secs}s`);

    return parts.join(' ');
}

// Export to window for global access
// Export to window for global access - moved to end of file

function toggleCustomModelInput() {
    const select = document.getElementById('setting-groq-model');
    const custom = document.getElementById('setting-groq-model-custom');
    if (select && custom) {
        custom.style.display = (select.value === 'custom') ? 'block' : 'none';
    }
}

// Helper functions for advanced settings
function toggleVisible(id, show) {
    const el = document.getElementById(id);
    if (el) el.style.display = show ? 'block' : 'none';
}

// Add listeners for dynamic UI
document.addEventListener('change', (e) => {
    if (e.target.id === 'setting-autoconnect') toggleVisible('autoconnect-options', e.target.checked);
    if (e.target.id === 'setting-groq-enabled') toggleVisible('groq-advanced-options', e.target.checked);
    if (e.target.id === 'setting-tts-enabled') toggleVisible('tts-options', e.target.checked);
});

function populateVoiceChannels(selectedId) {
    const select = document.getElementById('setting-autoconnect-channel');
    if (!select) return;
    select.innerHTML = '<option value="">Select a channel...</option>' +
        voiceChannels.map(ch => `<option value="${ch.id}" ${ch.id === String(selectedId) ? 'selected' : ''}>${ch.name}</option>`).join('');
}

const TTS_VOICES = {
    tiktok: [
        { id: "en_us_ghostface", name: "Ghost Face" },
        { id: "en_us_c3po", name: "C3PO" },
        { id: "en_us_stitch", name: "Stitch" },
        { id: "en_us_stormtrooper", name: "Stormtrooper" },
        { id: "en_us_rocket", name: "Rocket" },
        { id: "en_female_madam_leota", name: "Madame Leota" },
        { id: "en_male_ghosthost", name: "Ghost Host" },
        { id: "en_male_pirate", name: "Pirate" },
        { id: "en_us_001", name: "English US (Default)" },
        { id: "en_us_002", name: "Jessie" },
        { id: "en_us_006", name: "Joey" },
        { id: "en_us_007", name: "Professor" },
        { id: "en_us_009", name: "Scientist" },
        { id: "en_us_010", name: "Confidence" },
        { id: "en_male_jomboy", name: "Game On" },
        { id: "en_female_samc", name: "Empathetic" },
        { id: "en_male_cody", name: "Serious" },
        { id: "en_female_makeup", name: "Beauty Guru" },
        { id: "en_female_richgirl", name: "Bestie" },
        { id: "en_male_grinch", name: "Trickster" },
        { id: "en_male_narration", name: "Story Teller" },
        { id: "en_male_deadpool", name: "Mr. GoodGuy" },
        { id: "en_male_jarvis", name: "Alfred" },
        { id: "en_male_ashmagic", name: "ashmagic" },
        { id: "en_male_olantekkers", name: "olantekkers" },
        { id: "en_male_ukneighbor", name: "Lord Cringe" },
        { id: "en_male_ukbutler", name: "Mr. Meticulous" },
        { id: "en_female_shenna", name: "Debutante" },
        { id: "en_female_pansino", name: "Varsity" },
        { id: "en_male_trevor", name: "Marty" },
        { id: "en_female_betty", name: "Bae" },
        { id: "en_male_cupid", name: "Cupid" },
        { id: "en_female_grandma", name: "Granny" },
        { id: "en_male_wizard", name: "Magician" },
        { id: "en_uk_001", name: "Narrator" },
        { id: "en_uk_003", name: "Male English UK" },
        { id: "en_au_001", name: "Metro" },
        { id: "en_au_002", name: "Smooth" },
        { id: "es_mx_002", name: "Warm" }
    ],
    gtts: [
        { id: "en", name: "English (gTTS)" },
        { id: "it", name: "Italian (gTTS)" },
        { id: "fr", name: "French (gTTS)" },
        { id: "es", name: "Spanish (gTTS)" },
        { id: "de", name: "German (gTTS)" }
    ]
};

function populateTTSVoices(selectedId) {
    const tiktokGroup = document.getElementById('tts-voices-tiktok');
    const gttsGroup = document.getElementById('tts-voices-gtts');
    const select = document.getElementById('setting-tts-voice');

    if (!select || !tiktokGroup || !gttsGroup) return;

    tiktokGroup.innerHTML = TTS_VOICES.tiktok.map(v =>
        `<option value="${v.id}" ${v.id === selectedId ? 'selected' : ''}>${v.name}</option>`
    ).join('');

    gttsGroup.innerHTML = TTS_VOICES.gtts.map(v =>
        `<option value="${v.id}" ${v.id === selectedId ? 'selected' : ''}>${v.name}</option>`
    ).join('');

    // Ensure the correct option is selected if it wasn't caught by the map (e.g. if groupings changed)
    if (selectedId) {
        select.value = selectedId;
    }
}

// Global state for GROQ prompts (Defined at top)

function renderGroqPrompts() {
    const list = document.getElementById('groq-prompts-list');
    if (!list) return;

    list.innerHTML = currentGroqPrompts.map((p, i) => {
        // Handle migration from old string/simple dict format
        let name = p.name || `Custom Prompt ${i + 1}`;
        let enabled = p.enabled !== false;

        // If it's an old string prompt, we label it as legacy
        if (typeof p === 'string' || (p.text && !p.role)) {
            name = `(Legacy) ${name}`;
        }

        return `
            <div class="groq-prompt-item" style="display: flex; gap: 0.5rem; align-items: center; background: rgba(255,255,255,0.03); padding: 0.75rem; border-radius: 6px; border: 1px solid var(--border);">
                <input type="checkbox" onchange="toggleGroqPrompt(${i})" ${enabled ? 'checked' : ''} style="transform: scale(1.2); cursor: pointer;">
                
                <div style="flex: 1; margin-left: 0.5rem;">
                    <div style="font-weight: 500;">${name}</div>
                    <div style="font-size: 0.8rem; color: var(--text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 300px;">
                        ${p.role ? p.role.substring(0, 50) + '...' : (p.text || '').substring(0, 50) + '...'}
                    </div>
                </div>

                <div style="display: flex; gap: 0.5rem;">
                    <button class="btn btn-secondary btn-sm" onclick="openPromptEditor(${i})">Edit</button>
                    <button class="btn btn-icon btn-sm" onclick="deleteGroqPrompt(${i})" style="color: var(--danger); font-size: 1rem;">✕</button>
                </div>
            </div>
        `;
    }).join('');
}

function toggleGroqPrompt(index) {
    if (currentGroqPrompts[index]) {
        currentGroqPrompts[index].enabled = !currentGroqPrompts[index].enabled;
        // Re-render handled by checkbox visual state, but keeping data sync
    }
}

function deleteGroqPrompt(index) {
    if (confirm('Are you sure you want to delete this prompt?')) {
        currentGroqPrompts.splice(index, 1);
        renderGroqPrompts();
    }
}

// EDITOR LOGIC
function openPromptEditor(index = -1) {
    const modal = document.getElementById('prompt-editor-modal');
    if (!modal) return;

    document.getElementById('edit-prompt-index').value = index;
    document.getElementById('prompt-editor-title').textContent = index >= 0 ? 'Edit Prompt' : 'New Prompt';

    let data;
    if (index >= 0) {
        data = currentGroqPrompts[index];
        // Migration check
        if (typeof data === 'string') {
            // Convert string to struct
            data = { ...DEFAULT_PROMPT_TEMPLATE, role: data, name: `Legacy ${index + 1}` };
        } else if (data.text && !data.role) {
            data = { ...DEFAULT_PROMPT_TEMPLATE, role: data.text, name: data.name || `Legacy ${index + 1}` };
        }
    } else {
        // NEW: Populate with DEFAULTS
        data = JSON.parse(JSON.stringify(DEFAULT_PROMPT_TEMPLATE));
    }

    document.getElementById('edit-prompt-name').value = data.name || '';
    document.getElementById('edit-prompt-role').value = data.role || '';
    document.getElementById('edit-prompt-task').value = data.task || '';
    document.getElementById('edit-prompt-guidelines').value = data.guidelines || '';
    document.getElementById('edit-prompt-vocal').value = data.vocal_cues || '';
    document.getElementById('edit-prompt-words').value = data.word_count || '';
    document.getElementById('edit-prompt-format').value = data.output_format || '';

    modal.classList.add('open');
}

function closePromptEditor() {
    document.getElementById('prompt-editor-modal').classList.remove('open');
}

function savePrompt() {
    const index = parseInt(document.getElementById('edit-prompt-index').value);

    const newPrompt = {
        name: document.getElementById('edit-prompt-name').value,
        role: document.getElementById('edit-prompt-role').value,
        task: document.getElementById('edit-prompt-task').value,
        guidelines: document.getElementById('edit-prompt-guidelines').value,
        vocal_cues: document.getElementById('edit-prompt-vocal').value,
        word_count: document.getElementById('edit-prompt-words').value,
        output_format: document.getElementById('edit-prompt-format').value,
        enabled: true
    };

    // Maintain enabled state if editing
    if (index >= 0 && currentGroqPrompts[index]) {
        newPrompt.enabled = currentGroqPrompts[index].enabled;
        currentGroqPrompts[index] = newPrompt;
    } else {
        currentGroqPrompts.push(newPrompt);
    }

    renderGroqPrompts();
    closePromptEditor();
}

// PRESET & AUTOGEN IMPLEMENTATION

function showPresetLoader() {
    const modal = document.getElementById('preset-modal');
    if (modal) modal.classList.add('open');
}

function closePresetModal() {
    const modal = document.getElementById('preset-modal');
    if (modal) modal.classList.remove('open');
}

function loadPreset(type) {
    const presets = {
        friend: {
            name: "The Friend",
            role: "You are a close friend hanging out. Relaxed, casual, and authentic.",
            task: "Introduce the song like you're passing the aux cord.",
            vocal_cues: "Warm, casual tone. Use contractions.",
            guidelines: "Don't sound robotic. Be brief."
        },
        critic: {
            name: "The Critic",
            role: "You are a sophisticated music critic. Knowledgeable but slightly pretentious.",
            task: "Analyze the track's composition or historical context briefly.",
            vocal_cues: "Articulate, slightly haughty but passionate.",
            guidelines: "Focus on technical details or genre context."
        },
        hype: {
            name: "Hype Man",
            role: "You are a high-energy club MC.",
            task: "Get the crowd ready for the drop.",
            vocal_cues: "Loud, energetic, fast-paced.",
            guidelines: "Use exclamation points! shorter sentences."
        },
        jazz: {
            name: "Jazz Cat",
            role: "You are a smooth late-night jazz radio host.",
            task: "Set a moody, atmospheric vibe.",
            vocal_cues: "Deep, slow, smooth voice. Pauses for effect.",
            guidelines: "Use poetic language. 'Cool', 'Groovy', 'Smooth'."
        },
        history: {
            name: "History Buff",
            role: "You are a music historian.",
            task: "Share a fun fact or trivia about the song or year.",
            vocal_cues: "Educational, clearly spoken.",
            guidelines: "Did you know? Start with a fact."
        },
        zen: {
            name: "Zen Master",
            role: "You are a mindfulness guide.",
            task: "Help the listener relax into the soundscape.",
            vocal_cues: "Soft, whispery, calm.",
            guidelines: "Focus on breath and feeling."
        }
    };

    if (presets[type]) {
        // We open the editor and fill it
        openPromptEditor(-1); // New mode

        // Timeout to let modal open (microtask)
        setTimeout(() => {
            const p = presets[type];
            document.getElementById('edit-prompt-name').value = p.name;
            document.getElementById('edit-prompt-role').value = p.role;
            document.getElementById('edit-prompt-task').value = p.task;
            document.getElementById('edit-prompt-vocal').value = p.vocal_cues;
            document.getElementById('edit-prompt-guidelines').value = p.guidelines;
        }, 50);
    }
    closePresetModal();
}

function openAutogenerateModal() {
    const modal = document.getElementById('autogen-modal');
    if (modal) modal.classList.add('open');
}

function closeAutogenModal() {
    const modal = document.getElementById('autogen-modal');
    if (modal) modal.classList.remove('open');
}

async function applyAutogen() {
    const persona = document.getElementById('autogen-persona').value;
    const vibe = document.getElementById('autogen-vibe').value;
    const detail = document.getElementById('autogen-detail').value;

    // Logic to construct a prompt based on these valid values
    // For now, we'll map them to a simple template
    let role = "";
    let guidelines = "";

    switch (persona) {
        case "DJ": role = "You are a professional radio DJ."; break;
        case "Expert": role = "You are a music musicologist with deep knowledge."; break;
        case "Fan": role = "You are the world's biggest fan of this artist."; break;
        case "Rapper": role = "You are a hip-hop MC with flow."; break;
        case "Poet": role = "You are a poet describing the soundscape."; break;
    }

    role += ` Your vibe is ${vibe}.`;

    if (detail === "brief") guidelines = "Keep it extremely short. One punchy sentence.";
    else if (detail === "balanced") guidelines = "Write 2-3 engaging sentences.";
    else if (detail === "deep") guidelines = "Write a rich, detailed paragraph.";

    openPromptEditor(-1);
    setTimeout(() => {
        document.getElementById('edit-prompt-name').value = `Auto: ${persona} (${vibe})`;
        document.getElementById('edit-prompt-role').value = role;
        document.getElementById('edit-prompt-task').value = "Introduce the upcoming track based on your persona.";
        document.getElementById('edit-prompt-guidelines').value = guidelines;
        document.getElementById('edit-prompt-vocal').value = `Match the ${vibe} energy.`;
    }, 50);

    closeAutogenModal();
}

function addGroqPrompt() {
    openPromptEditor(-1);
}

// Final Export to window for global access
Object.assign(window, {
    switchTab,
    switchScope,
    control,
    changeLibraryPage,
    selectGuild,
    leaveGuild,
    leaveServer,
    saveServerSettings,
    saveSettingsTab,
    toggleNotifications,
    viewUser,
    openGlobalSettings,
    initCharts,
    updateCharts,
    showPresetLoader,
    closePresetModal,
    loadPreset,
    openAutogenerateModal,
    closeAutogenModal,
    applyAutogen,
    toggleCustomModelInput,
    // Groq Prompt Editor Functions
    renderGroqPrompts,
    openPromptEditor,
    closePromptEditor,
    savePrompt,
    deleteGroqPrompt,
    toggleGroqPrompt,
    addGroqPrompt
});
