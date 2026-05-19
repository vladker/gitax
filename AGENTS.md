# GitHub Archiver — AGENTS.md

Simple Python CLI tool that downloads GitHub repositories as ZIP and sends them to the MAX messenger via browser automation.

## Quick Start

```bash
pip install -r requirements.txt
npm install -g agent-browser
agent-browser install

# Create .env with your tokens (see .env.example)
copy .env.example .env
# Edit .env file with your GITHUB_TOKEN and MAX_CHANNEL_URL

python github_archiver.py
```

## Key Files

| File | Purpose |
|------|---------|
| `.env` | Tokens/URLs — **not committed** (gitignored) |
| `.env.example` | Template for .env (safe to commit) |
| `github_archiver.py` | Entry point — run this |
| `github_api.py` | GitHub API (REST v3) |
| `browser_max.py` | MAX browser automation via CDP |
| `journal.py` | Tracks processed repos (JSON) |
| `config.yaml` | Non-sensitive settings — optional |

## Token Setup

Create a `.env` file in the project root (or set environment variables):

```env
GITHUB_TOKEN=github_pat_...
MAX_CHANNEL_URL=https://web.max.ru/...
```

Priority: `.env` file / env var > `config.yaml`

## Browser Automation

- Uses `agent-browser` CLI with CDP to existing Chrome (port 9222)
- Browser must be open at the MAX channel URL before running
- Hard-coded element refs exist (`e23` for message input, `e13` for attach button) — these may need updating if MAX UI changes
- Full upload flow: connect → type text → click attach → wait for `input[type=file]` → upload → send → confirm

## .gitignore

Sensitive files that must never be committed:
- `.env`, `.env.*` (tokens, secrets)
- `config.yaml`, `*.yaml`, `*.yml`
- `*.json` (journal, auth-state)
- `*.log`, `archiver.log`
- `temp/`, `*.zip`

## Agent-Browser Skill

For browser automation details: `C:/Users/vldkr/.agents/skills/agent-browser/SKILL.md`