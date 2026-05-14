# GitHub Archiver — AGENTS.md

Simple Python CLI tool that downloads GitHub repositories as ZIP and sends them to the MAX messenger via browser automation.

## Quick Start

```bash
pip install -r requirements.txt
npm install -g agent-browser
agent-browser install
python github_archiver.py
```

## Key Files

| File | Purpose |
|------|---------|
| `github_archiver.py` | Entry point — run this |
| `github_api.py` | GitHub API (REST v3) |
| `browser_max.py` | MAX browser automation via CDP |
| `journal.py` | Tracks processed repos (JSON) |
| `config.yaml` | Tokens/URLs — not committed |

## Browser Automation

- Uses `agent-browser` CLI with CDP to existing Chrome (port 9222)
- Browser must be open at the MAX channel URL before running
- Hard-coded element refs exist (`e23` for message input, `e13` for attach button) — these may need updating if MAX UI changes
- Full upload flow: connect → type text → click attach → wait for `input[type=file]` → upload → send → confirm

## .gitignore

Sensitive files that must never be committed:
- `config.yaml`, `*.yaml`, `*.yml` (contains GitHub PAT)
- `*.json` (journal, auth-state)
- `*.log`, `archiver.log`
- `temp/`, `*.zip`

## Agent-Browser Skill

For browser automation details: `C:/Users/vldkr/.agents/skills/agent-browser/SKILL.md`