# 🌌 Vexo - Smart Discord Music Bot

Vexo is a self-hosted Discord music bot designed for discovery and democratic listening. It doesn't just play music; it learns what your server loves and helps you find the next great track.

## 🚀 The Vision
Vexo is evolving toward a **modern, reactive web-based interface**. While the bot remains highly functional via Discord slash commands, our roadmap focuses on a seamless, real-time dashboard for managing queues, viewing detailed music analytics, and configuring discovery engines without leaving your browser.

## 🧠 Metadata & Discovery Strategy
Vexo employs a multi-layered metadata strategy to ensure every song has its context:
- **Primary Heuristics**: Extracts metadata directly from YouTube/Spotify streams.
- **Discogs Integration**: Used as the primary fallback for genre and artist attribution when primary sources are ambiguous.
- **MusicBrainz Fallback**: An additional layer for deep-catalog metadata.

### Discovery Engines
- **Similar Song (60%)**: Finds tracks with matching genres and vibes to your liked history.
- **Same Artist (10%)**: Surfacing deeper cuts from your favorite creators.
- **Wildcard (30%)**: Keeping things fresh with curated global charts.

---

## ✨ Key Features
- **🎵 High-Quality Audio** - Optimized Opus passthrough for crystal-clear sound.
- **👥 Democratic Selection** - Intelligent turn-based queue management.
- **❤️ Reaction Learning** - Likes directly influence the discovery engine's future suggestions.
- **📊 Live Dashboard** - Real-time statistics and system logs.

## 🛠️ Setup & Deployment

### 1. Prerequisites
- Python 3.11+
- FFmpeg
- Discord Bot Token
- Spotify API Credentials (Client ID & Secret)
- Discogs Token (Highly Recommended for Discovery)

### 2. Local Installation
```bash
git clone https://github.com/Axiom3D-YT/vexo.git
cd vexo
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your credentials
python -m src.bot
```

### 3. Docker (Recommended)
```bash
docker-compose up -d
```

---

## 📋 Commands & Usage

| Category | Commands |
|----------|----------|
| **Music** | `/play`, `/play any`, `/pause`, `/resume`, `/skip`, `/queue`, `/nowplaying` |
| **Learning** | `/like`, `/dislike`, `/preferences`, `/import <spotify_url>` |
| **Settings** | `/settings show`, `/settings discovery_weights`, `/dj @role` |
| **Privacy** | `/privacy export`, `/privacy delete`, `/privacy optout` |

---

## 🤝 Contributing
We welcome contributions to the Vexo ecosystem! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for our coding standards and guidelines for the new Web UI.

## 📝 License
Vexo is released under the MIT License. See [LICENSE](LICENSE) for details.
