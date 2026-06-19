---
date: 2026-06-19
topic: "GitHub No-Token Unlimited Collection"
status: validated
---

## Problem Statement

GitHub Search API has a hard limit of 1000 results per query. The current tiered
approach (13 tiers x 1000 = 13,000) works but requires a token. User wants:

1. **No-token collection** — ability to gather repos without a GitHub token
2. **Unlimited limit** — remove the 1000 cap to collect arbitrary amounts
3. **Interactive limit** — user specifies target count at runtime

## Constraints

- GitHub Search API: 1000 results max per query (hard limit)
- GitHub GraphQL API: Requires token, 5000 query point budget/hr
- Unauthenticated requests: 60/hr rate limit
- Trending page: No API, requires scraping
- Must maintain backward compatibility with existing tiered collection

## Approach

**Multi-strategy collection** via orchestrator that runs 4 strategies in sequence
until the target count is reached:

1. **GraphQL** — Cursor-based pagination, bypasses 1000 cap (requires token)
2. **REST** — Existing tiered search (requires token)
3. **Crawler** — Playwright scraping of trending.github.com (no token needed)
4. **Topics** — REST search by topic (requires token)

After each strategy, orchestrator checks: `if db_count >= target: stop`

**Why this approach:**
- GraphQL provides the most efficient path to large numbers (cursor pagination)
- REST tiers provide diversity (language-specific queries)
- Crawler works without token — fallback for unauthenticated use
- Topics add niche coverage (ML, React, DevOps, etc.)

## Architecture

```
github_archiver.py (menu)
    |
    v
RepoCollector.collect_all_strategies(target_count)
    |
    +-- Phase 1: GraphQL tiers (stars >100, >50, >10)
    |       |
    |       v
    |   GitHubAPI.search_repos_graphql()
    |
    +-- Phase 2: REST tiers (legend, popular, lang-*)
    |       |
    |       v
    |   GitHubAPI._request("/search/repositories")
    |
    +-- Phase 3: Crawler (trending)
    |       |
    |       v
    |   GitHubTrendingCrawler.crawl()
    |
    +-- Phase 4: Topic tiers (ML, react, devops...)
            |
            v
        GitHubAPI._request("/search/repositories?topic:X")
```

## Components

### GitHubAPI.search_repos_graphql (new)

- **Location:** `github_api.py`
- **Purpose:** GraphQL search with cursor pagination
- **Key features:**
  - Query: `search(type: REPOSITORY, first: N, after: cursor, query: "stars:>X")`
  - Returns repo data matching REST API format
  - Respects 5000 query point budget
  - No 1000 result limit

### GitHubTrendingCrawler (new)

- **Location:** `github_crawler.py`
- **Purpose:** Scrape trending repos via Playwright
- **Key features:**
  - Connects to existing Chrome via CDP (port 9222)
  - Scrapes `trending.github.com` (daily, weekly, monthly)
  - Scrapes `trending.github.com/by/developers`
  - Extracts `owner/repo` from links, fetches details via REST
  - Works without token (60/hr unauthenticated limit)

### RepoCollector.collect_all_strategies (new)

- **Location:** `repo_collector.py`
- **Purpose:** Orchestrates all 4 strategies
- **Key features:**
  - Takes `target_count` parameter
  - Runs strategies in order, checking count after each
  - Skips already-completed tiers (state tracking)
  - Returns statistics dict

### Menu integration (updated)

- **Location:** `github_archiver.py`
- **Changes:**
  - New menu option [4] "Собрать базу (автосбор до лимита)"
  - Interactive prompt: "Целевой лимит репозиториев (по умолчанию X):"
  - Calls `collect_all_strategies(target_count)`

## Data Flow

1. User selects menu option [4]
2. System shows current DB count
3. User enters target count (or accepts default)
4. Orchestrator runs Phase 1 (GraphQL):
   - For each GraphQL tier not yet done:
     - Call `search_repos_graphql(star_threshold, max_repos)`
     - Add results to database, deduplicate
     - Check count >= target → exit if satisfied
5. Orchestrator runs Phase 2 (REST tiers) — same pattern
6. Orchestrator runs Phase 3 (Crawler) — same pattern
7. Orchestrator runs Phase 4 (Topics) — same pattern
8. Summary displayed, user presses Enter

## Error Handling

- **GraphQL failures:** Log error, continue to next tier/strategy
- **Crawler unavailable:** Graceful skip (ImportError caught)
- **REST rate limit:** Existing retry logic handles this
- **Strategy failure:** Logged, orchestrator continues to next strategy
- **Partial completion:** Returns `"status": "partial"` with stats

## Testing Strategy

- Manual testing via menu option [4]
- Verify GraphQL returns data (check logs)
- Verify Crawler works with Chrome on port 9222
- Verify deduplication (same repo from multiple strategies)
- Verify early exit when target is reached

## Open Questions

- **Crawler without CDP:** Should we launch a headless browser if CDP fails?
  - Decision: No — requires Playwright install, adds complexity. Graceful skip is fine.
- **GraphQL query points:** Current implementation uses 1 point per request.
  - With 5000/hr budget and 100 repos/page, that's 50,000 repos/hr theoretical max.
  - Decision: Acceptable for current use case.
- **Topic list:** 10 topics chosen. Should this be configurable?
  - Decision: Hardcoded for now. Can add to config.yaml later if needed.
