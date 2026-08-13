# GitHub Archiver — AGENTS.md

Simple Python CLI tool that downloads GitHub repositories as ZIP and sends them to the MAX messenger via browser automation.

## Quick Start

```bash
pip install -r requirements.txt

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
| `browser_max.py` | MAX browser automation via Playwright + CDP |
| `journal.py` | Tracks processed repos (JSON) |
| `logging_config.py` | Logging setup (archiver.log) |
| `config.yaml` | Non-sensitive settings — optional |

## Token Setup

Create a `.env` file in the project root (or set environment variables):

```env
GITHUB_TOKEN=github_pat_...
MAX_CHANNEL_URL=https://web.max.ru/...
```

Priority: `.env` file / env var > `config.yaml`

## Browser Automation

- Uses **Playwright** with CDP to an existing Chrome instance (port 9222)
- Browser must be open at the MAX channel URL before running
- Connects via `connect_over_cdp` — preserves existing session/cookies, no login needed

### Upload Flow (single file)

1. Connect to Chrome via CDP (`localhost:9222`)
2. Navigate to the MAX channel URL
3. Type repository description message, send it (Enter)
4. Click upload button → file chooser → select file
5. Wait for upload to complete (MutationObserver monitors DOM)
6. Press Enter to send the file message
7. Wait for file message confirmation in the feed (scans new messages with **filename matching**)

### Multi-Volume (7z split)

Files larger than **49 MB** are split into `.7z.001`, `.7z.002`, etc. volumes using 7-Zip (threshold via `config.yaml`):

- Each volume is uploaded as a separate message.
- Volumes are sent sequentially (`.001` → `.002` → `.003` ...).
- Each volume is **deleted immediately** after upload confirmation.

### Upload Confirmation

The confirmation system checks that the **specific filename** (including volume number `.7z.003`) appears in the feed, preventing false matches from previously uploaded volumes:

1. MutationObserver tracks DOM changes for new file messages
2. `_check_upload_in_lenta(expected_filename)` scans the feed for a message containing the exact filename
3. `_wait_for_file_message()` monitors new messages from a baseline count, matching by filename
4. All three checkpoints validate the **current file's name**, not just any `.zip` message

## Config Options (config.yaml)

```yaml
archiver:
  limit: 1000               # How many repos to fetch from GitHub
  retries: 3                # Upload retry count
  retry_delay: 10           # Delay between retries (seconds)
  repo_delay: 30            # Pause between repos (seconds)
  split_threshold_mb: 49    # Files above this size are split into 7z volumes
  use_local_browser: false  # false = CDP to existing Chrome, true = launch new
  output_dir: "./temp"      # Temp directory for downloads
```

## PyPI Archiver

Downloads top Python packages from Hugovk dataset and sends them to a separate MAX channel.

### Configuration
- `pypi.channel_url` — MAX channel URL for PyPI packages
- `pypi_archiver.limit` — Default number of packages
- `pypi_archiver.output_dir` — Temp download directory
- Both .tar.gz (source) and .whl (wheel) distributions are downloaded

### Token Setup
Add to `.env`:
```env
PYPI_CHANNEL_URL=https://web.max.ru/...
```

## .gitignore

Sensitive files that must never be committed:
- `.env`, `.env.*` (tokens, secrets)
- `config.yaml`, `*.yaml`, `*.yml`
- `*.json` (journal, auth-state)
- `*.log`, `archiver.log`
- `temp/`, `*.zip`

## Agent-Browser Skill

For browser automation details: `C:/Users/vldkr/.agents/skills/agent-browser/SKILL.md`

<skills_system priority="1">

## Available Skills

<!-- SKILLS_TABLE_START -->
<usage>
When users ask you to perform tasks, check if any of the available skills below can help complete the task more effectively. Skills provide specialized capabilities and domain knowledge.

How to use skills:
- Invoke: `npx openskills read <skill-name>` (run in your shell)
  - For multiple: `npx openskills read skill-one,skill-two`
- The skill content will load with detailed instructions on how to complete the task
- Base directory provided in output for resolving bundled resources (references/, scripts/, assets/)

Usage notes:
- Only use skills listed in <available_skills> below
- Do not invoke a skill that is already loaded in your context
- Each skill invocation is stateless
</usage>

<available_skills>

<skill>
<name>algorithmic-art</name>
<description>Creating algorithmic art using p5.js with seeded randomness and interactive parameter exploration. Use this when users request creating art using code, generative art, algorithmic art, flow fields, or particle systems. Create original algorithmic art rather than copying existing artists' work to avoid copyright violations.</description>
<location>project</location>
</skill>

<skill>
<name>brand-guidelines</name>
<description>Applies Anthropic's official brand colors and typography to any sort of artifact that may benefit from having Anthropic's look-and-feel. Use it when brand colors or style guidelines, visual formatting, or company design standards apply.</description>
<location>project</location>
</skill>

<skill>
<name>canvas-design</name>
<description>Create beautiful visual art in .png and .pdf documents using design philosophy. You should use this skill when the user asks to create a poster, piece of art, design, or other static piece. Create original visual designs, never copying existing artists' work to avoid copyright violations.</description>
<location>project</location>
</skill>

<skill>
<name>claude-api</name>
<description>|-</description>
<location>project</location>
</skill>

<skill>
<name>doc-coauthoring</name>
<description>Guide users through a structured workflow for co-authoring documentation. Use when user wants to write documentation, proposals, technical specs, decision docs, or similar structured content. This workflow helps users efficiently transfer context, refine content through iteration, and verify the doc works for readers. Trigger when user mentions writing docs, creating proposals, drafting specs, or similar documentation tasks.</description>
<location>project</location>
</skill>

<skill>
<name>docx</name>
<description>"Use this skill whenever the user wants to create, read, edit, or manipulate Word documents (.docx files) or Word templates (.dotx files). Triggers include: any mention of 'Word doc', 'word document', '.docx', '.dotx', or requests to produce professional documents with formatting like tables of contents, headings, page numbers, or letterheads. Also use when extracting or reorganizing content from .docx or .dotx files, inserting or replacing images in documents, performing find-and-replace in Word files, working with tracked changes or comments, or converting content into a polished Word document. If the user asks for a 'report', 'memo', 'letter', 'template', or similar deliverable as a Word or .docx file, use this skill. Do NOT use for PDFs, spreadsheets, Google Docs, or general coding tasks unrelated to document generation."</description>
<location>project</location>
</skill>

<skill>
<name>frontend-design</name>
<description>Guidance for distinctive, intentional visual design when building new UI or reshaping an existing one. Helps with aesthetic direction, typography, and making choices that don't read as templated defaults.</description>
<location>project</location>
</skill>

<skill>
<name>internal-comms</name>
<description>A set of resources to help me write all kinds of internal communications, using the formats that my company likes to use. Claude should use this skill whenever asked to write some sort of internal communications (status reports, leadership updates, 3P updates, company newsletters, FAQs, incident reports, project updates, etc.).</description>
<location>project</location>
</skill>

<skill>
<name>mcp-builder</name>
<description>Guide for creating high-quality MCP (Model Context Protocol) servers that enable LLMs to interact with external services through well-designed tools. Use when building MCP servers to integrate external APIs or services, whether in Python (FastMCP) or Node/TypeScript (MCP SDK).</description>
<location>project</location>
</skill>

<skill>
<name>pdf</name>
<description>Use this skill whenever the user wants to do anything with PDF files. This includes reading or extracting text/tables from PDFs, combining or merging multiple PDFs into one, splitting PDFs apart, rotating pages, adding watermarks, creating new PDFs, filling PDF forms, encrypting/decrypting PDFs, extracting images, and OCR on scanned PDFs to make them searchable. If the user mentions a .pdf file or asks to produce one, use this skill.</description>
<location>project</location>
</skill>

<skill>
<name>pptx</name>
<description>"Use this skill any time a .pptx or .potx file is involved in any way — as input, output, or both. This includes: creating slide decks, pitch decks, or presentations; reading, parsing, or extracting text from any .pptx or .potx file (even if the extracted content will be used elsewhere, like in an email or summary); editing, modifying, or updating existing presentations; combining or splitting slide files; working with templates (.potx), layouts, speaker notes, or comments. Trigger whenever the user mentions \"deck,\" \"slides,\" \"presentation,\" or references a .pptx or .potx filename, regardless of what they plan to do with the content afterward. If a .pptx or .potx file needs to be opened, created, or touched, use this skill."</description>
<location>project</location>
</skill>

<skill>
<name>skill-creator</name>
<description>Create new skills, modify and improve existing skills, and measure skill performance. Use when users want to create a skill from scratch, edit, or optimize an existing skill, run evals to test a skill, benchmark skill performance with variance analysis, or optimize a skill's description for better triggering accuracy.</description>
<location>project</location>
</skill>

<skill>
<name>slack-gif-creator</name>
<description>Knowledge and utilities for creating animated GIFs optimized for Slack. Provides constraints, validation tools, and animation concepts. Use when users request animated GIFs for Slack like "make me a GIF of X doing Y for Slack."</description>
<location>project</location>
</skill>

<skill>
<name>template</name>
<description>Replace with description of the skill and when Claude should use it.</description>
<location>project</location>
</skill>

<skill>
<name>theme-factory</name>
<description>Toolkit for styling artifacts with a theme. These artifacts can be slides, docs, reportings, HTML landing pages, etc. There are 10 pre-set themes with colors/fonts that you can apply to any artifact that has been creating, or can generate a new theme on-the-fly.</description>
<location>project</location>
</skill>

<skill>
<name>web-artifacts-builder</name>
<description>Suite of tools for creating elaborate, multi-component claude.ai HTML artifacts using modern frontend web technologies (React, Tailwind CSS, shadcn/ui). Use for complex artifacts requiring state management, routing, or shadcn/ui components - not for simple single-file HTML/JSX artifacts.</description>
<location>project</location>
</skill>

<skill>
<name>webapp-testing</name>
<description>Toolkit for interacting with and testing local web applications using Playwright. Supports verifying frontend functionality, debugging UI behavior, capturing browser screenshots, and viewing browser logs.</description>
<location>project</location>
</skill>

<skill>
<name>xlsx</name>
<description>"Use this skill any time a spreadsheet file is the primary input or output. This means any task where the user wants to: open, read, edit, or fix an existing .xlsx, .xlsm, .xltx, .csv, or .tsv file (e.g., adding columns, computing formulas, formatting, charting, cleaning messy data); create a new spreadsheet from scratch or from other data sources; or convert between tabular file formats. Trigger especially when the user references a spreadsheet file by name or path — even casually (like \"the xlsx in my downloads\") — and wants something done to it or produced from it. Also trigger for cleaning or restructuring messy tabular data files (malformed rows, misplaced headers, junk data) into proper spreadsheets. The deliverable must be a spreadsheet file. Do NOT trigger when the primary deliverable is a Word document, HTML report, standalone Python script, database pipeline, or Google Sheets API integration, even if tabular data is involved."</description>
<location>project</location>
</skill>

</available_skills>
<!-- SKILLS_TABLE_END -->

</skills_system>
