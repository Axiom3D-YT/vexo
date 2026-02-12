# Contributing to Vexo

Thank you for your interest in improving Vexo! We aim to maintain a high standard of code quality for both the Discord bot and the modern Web UI.

## 🛠️ Tech Stack
- **Bot**: Python 3.11+, `discord.py`, `yt-dlp`.
- **Backend/API**: `aiohttp` for the dashboard and internal services.
- **Frontend**: Preact, Vite, Vanilla CSS (Modern CSS/Flex/Grid).

## 📏 Coding Standards

### Python (Bot & Backend)
- **Docstrings**: All classes and major functions must have descriptive docstrings.
- **Logging**: Use the standard `logging` module. Avoid `print()` for debugging in production code.
- **Type Hinting**: Use Python type hints for better readability and catch errors early.
- **Asyncio**: Ensure all I/O operations (API calls, DB queries) are non-blocking.

### Frontend (Web UI)
- **Signals**: Use `@preact/signals` for state management.
- **CSS**: Favor Vanilla CSS with CSS Variables for themes. Avoid inline styles.
- **Component-Based**: Keep UI components small, reusable, and logic-lite.

## 🚀 Pull Request Process
1. Fork the repo and create your branch from `main`.
2. Ensure your code follows the standards above.
3. Update the `README.md` or other docs if you've added new features or changed configurations.
4. Open a PR with a clear description of the changes.

## 🐞 Reporting Issues
Ensure you include:
1. A clear title.
2. Steps to reproduce the bug.
3. Expected vs. Actual behavior.
4. Relevant logs.
