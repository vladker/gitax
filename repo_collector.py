"""
Модуль постраничного сбора базы репозиториев GitHub.

4 стратегии для обхода лимитов GitHub API:
  1. GraphQL   — пагинация через cursor, bypass 1000 cap
  2. REST      — tiered search (stars + languages)
  3. Crawler   — Playwright scraping trending.github.com
  4. Topics    — search by popular topics (ML, react, devops…)

Оркестратор `collect_all_strategies` запускает их по очереди
пока база не достигнет целевого лимита.

Файлы:
  repo_database.json           — база всех собранных репозиториев
  repo_collector_state.json    — состояние коллектора (какие тира собраны)
"""

import json
import os
import time
from datetime import datetime
from typing import Optional

from logging_config import LogMixin
from shared_journal import BaseJournal


# ── REST tier definitions ──────────────────────────────────────

DEFAULT_TIERS = [
    # ── Star-based tiers (top repos regardless of language) ──
    {"name": "legend",    "query": "stars:>50000",  "label": "Легенды (50k+)"},
    {"name": "popular",   "query": "stars:>10000",  "label": "Популярные (10k+)"},
    {"name": "well-known","query": "stars:>5000",   "label": "Известные (5k+)"},

    # ── Language-specific tiers (diversifies beyond star overlap) ──
    {"name": "lang-js",     "query": "language:JavaScript stars:>500",  "label": "JavaScript (500+)"},
    {"name": "lang-py",     "query": "language:Python stars:>500",      "label": "Python (500+)"},
    {"name": "lang-ts",     "query": "language:TypeScript stars:>500",  "label": "TypeScript (500+)"},
    {"name": "lang-rust",   "query": "language:Rust stars:>500",        "label": "Rust (500+)"},
    {"name": "lang-go",     "query": "language:Go stars:>500",          "label": "Go (500+)"},
    {"name": "lang-java",   "query": "language:Java stars:>500",        "label": "Java (500+)"},
    {"name": "lang-cpp",    "query": "language:C++ stars:>500",         "label": "C++ (500+)"},
    {"name": "lang-rb",     "query": "language:Ruby stars:>500",        "label": "Ruby (500+)"},
    {"name": "lang-php",    "query": "language:PHP stars:>500",         "label": "PHP (500+)"},
    {"name": "lang-swift",  "query": "language:Swift stars:>500",       "label": "Swift (500+)"},
    {"name": "lang-kotlin", "query": "language:Kotlin stars:>500",      "label": "Kotlin (500+)"},
    {"name": "lang-cs",     "query": "language:C# stars:>500",          "label": "C# (500+)"},
]

# ── GraphQL tier definitions ───────────────────────────────────

DEFAULT_GRAPHQL_TIERS = [
    {"name": "gql-stars-100",  "star_threshold": 100,  "max_repos": 1000, "label": "GraphQL: 100+ stars"},
    {"name": "gql-stars-50",   "star_threshold": 50,   "max_repos": 1000, "label": "GraphQL: 50+ stars"},
    {"name": "gql-stars-10",   "star_threshold": 10,   "max_repos": 1000, "label": "GraphQL: 10+ stars"},
]

# ── Topic tier definitions ─────────────────────────────────────

DEFAULT_TOPIC_TIERS = [
    {"name": "topic-ml",        "topic": "machine-learning",   "label": "ML/АИ"},
    {"name": "topic-react",     "topic": "react",              "label": "React"},
    {"name": "topic-devops",    "topic": "devops",             "label": "DevOps"},
    {"name": "topic-docker",    "topic": "docker",             "label": "Docker"},
    {"name": "topic-ai",        "topic": "artificial-intelligence", "label": "AI"},
    {"name": "topic-web",       "topic": "web-development",    "label": "Web Dev"},
    {"name": "topic-data",      "topic": "data-science",       "label": "Data Science"},
    {"name": "topic-cloud",     "topic": "cloud",              "label": "Cloud"},
    {"name": "topic-security",  "topic": "security",           "label": "Security"},
    {"name": "topic-mobile",    "topic": "mobile-development", "label": "Mobile"},
]


class RepoDatabase(BaseJournal):
    """База данных собранных репозиториев (repo_database.json).

    Хранит метаданные всех собранных репо. Dedup по full_name.
    """

    def __init__(self, file_path: str = "repo_database.json"):
        super().__init__(file_path)

    def _create_empty(self) -> dict:
        return {
            "repositories": [],
            "total_collected": 0,
            "last_updated": "",
            "tiers_completed": [],
        }

    def _pre_save(self):
        self.data["last_updated"] = datetime.now().isoformat()
        self.data["total_collected"] = len(self.data.get("repositories", []))

    def add_repository(self, repo: dict) -> bool:
        """Добавить репозиторий в базу. Dedup по full_name.

        Returns True если добавлен/обновлён, False если пропущен.
        """
        full_name = repo.get("full_name")
        if not full_name:
            return False

        # Обновить существующую запись (звёзды могли измениться)
        for i, existing in enumerate(self.data["repositories"]):
            if existing.get("full_name") == full_name:
                self.data["repositories"][i].update(repo)
                return True

        # Новый репозиторий
        self.data["repositories"].append(repo)
        return True

    def add_batch(self, repos: list[dict]) -> int:
        """Добавить партию репозиториев. Returns количество новых."""
        added = 0
        for repo in repos:
            if self.add_repository(repo):
                added += 1
        if added:
            self.save()
        return added

    def contains(self, full_name: str) -> bool:
        """Проверить наличие репозитория в базе."""
        return any(r.get("full_name") == full_name
                   for r in self.data.get("repositories", []))

    def get_all_sorted(self, sort_by: str = "stargazers_count", reverse: bool = True) -> list[dict]:
        """Получить все репозитории, отсортированные."""
        repos = self.data.get("repositories", [])
        return sorted(repos, key=lambda r: r.get(sort_by, 0), reverse=reverse)

    def get_count(self) -> int:
        return len(self.data.get("repositories", []))

    def get_stats(self) -> dict:
        repos = self.data.get("repositories", [])
        if not repos:
            return {"total": 0, "max_stars": 0, "avg_stars": 0}
        stars = [r.get("stargazers_count", 0) for r in repos]
        return {
            "total": len(repos),
            "max_stars": max(stars),
            "avg_stars": sum(stars) // len(stars),
        }


class RepoCollectorState(BaseJournal):
    """Состояние коллектора (repo_collector_state.json).

    Отслеживает какие тира собраны, когда и сколько репо получено.
    """

    def __init__(self, file_path: str = "repo_collector_state.json"):
        super().__init__(file_path)

    def _create_empty(self) -> dict:
        return {
            "tiers": {},
            "last_run": "",
            "total_runs": 0,
        }

    def mark_tier_done(self, tier_name: str, count: int, timestamp: Optional[str] = None):
        """Отметить тир как собранный."""
        self.data["tiers"][tier_name] = {
            "count": count,
            "collected_at": timestamp or datetime.now().isoformat(),
        }
        self.data["last_run"] = datetime.now().isoformat()
        self.data["total_runs"] = self.data.get("total_runs", 0) + 1
        self.save()

    def is_tier_done(self, tier_name: str) -> bool:
        """Проверить, собран ли тир."""
        return tier_name in self.data.get("tiers", {})

    def get_next_tier_index(self, tier_names: list[str]) -> int:
        """Найти индекс первого не собранного тира."""
        for i, name in enumerate(tier_names):
            if not self.is_tier_done(name):
                return i
        return len(tier_names)  # все собраны

    def get_stats(self) -> dict:
        tiers_data = self.data.get("tiers", {})
        return {
            "total_tiers_done": len(tiers_data),
            "total_runs": self.data.get("total_runs", 0),
            "last_run": self.data.get("last_run", "never"),
        }


class RepoCollector(LogMixin):
    """Коллектор репозиториев с multi-strategy сбором.

    4 стратегии:
      1. GraphQL   — cursor pagination, bypass 1000 cap
      2. REST      — tiered search (stars + languages)
      3. Crawler   — Playwright scraping trending
      4. Topics    — search by popular topics

    Args:
        github_api: Экземпляр GitHubAPI для запросов
        tiers: REST tier-конфигурации
        graphql_tiers: GraphQL tier-конфигурации
        topic_tiers: Topic tier-конфигурации
        per_page: Количество репо на страницу (max 100)
    """

    def __init__(
        self,
        github_api,
        tiers: Optional[list[dict]] = None,
        graphql_tiers: Optional[list[dict]] = None,
        topic_tiers: Optional[list[dict]] = None,
        per_page: int = 100,
    ):
        self.github = github_api
        self.tiers = tiers or DEFAULT_TIERS
        self.graphql_tiers = graphql_tiers or DEFAULT_GRAPHQL_TIERS
        self.topic_tiers = topic_tiers or DEFAULT_TOPIC_TIERS
        self.per_page = min(per_page, 100)
        self.database = RepoDatabase()
        self.state = RepoCollectorState()

    def collect_tier(self, tier: dict) -> list[dict]:
        """Собрать один тир репозиториев.

        Args:
            tier: Словарь с key 'query' (GitHub search query)

        Returns:
            Список собранных репозиториев (уникальных, не в базе)
        """
        query = tier.get("query", "stars:>100")
        name = tier.get("name", "unknown")
        label = tier.get("label", name)

        self.logger.info(f"Collecting tier '{name}' ({label}): query={query}")
        print(f"\n  📦 Тир: {label}")
        print(f"     Запрос: {query}")

        all_repos = []
        seen_names = set()
        page = 1

        while True:
            try:
                response = self.github._request(
                    "GET",
                    "/search/repositories",
                    params={
                        "q": query,
                        "sort": "stars",
                        "order": "desc",
                        "per_page": self.per_page,
                        "page": page,
                    },
                )
            except Exception as e:
                self.logger.error(f"Tier '{name}' page {page} failed: {e}")
                break

            if response.status_code != 200:
                self.logger.error(f"Tier '{name}' API error: {response.status_code}")
                break

            data = response.json()
            items = data.get("items", [])

            if not items:
                break

            for repo in items:
                fn = repo.get("full_name")
                if fn and fn not in seen_names:
                    seen_names.add(fn)
                    all_repos.append(repo)

            remaining = response.headers.get("X-RateLimit-Remaining", "?")
            self.logger.info(
                f"Tier '{name}' page {page}: got {len(items)} "
                f"(total: {len(all_repos)}, remaining: {remaining})"
            )

            # Progress indicator
            if len(all_repos) % self.per_page == 0:
                print(f"     ⏳ {len(all_repos)} репозиториев...")

            # GitHub Search API cap: max ~1000 results total
            if len(items) < self.per_page:
                break

            page += 1
            if page > 10:  # safety limit (10 * 100 = 1000)
                break

            # Small delay between pages to be nice to the API
            time.sleep(0.5)

        new_count = self.database.add_batch(all_repos)
        self.database.save()

        self.logger.info(
            f"Tier '{name}' complete: {len(all_repos)} total, "
            f"{new_count} new, {self.database.get_count()} in DB"
        )
        print(f"     ✓ Собрано: {len(all_repos)} (новых: {new_count})")

        return all_repos

    # ── Strategy 1: GraphQL ────────────────────────────────────

    def collect_graphql_tier(self, gtier: dict) -> list[dict]:
        """Собрать один GraphQL тир через cursor pagination.

        Args:
            gtier: Словарь с star_threshold, max_repos, label

        Returns:
            Список собранных репозиториев
        """
        name = gtier.get("name", "unknown")
        label = gtier.get("label", name)
        star_threshold = gtier.get("star_threshold", 100)
        max_repos = gtier.get("max_repos", 1000)

        self.logger.info(f"GraphQL tier '{name}' ({label}): stars>{star_threshold}")
        print(f"\n  🔍 GraphQL: {label}")

        all_repos = []
        try:
            all_repos = self.github.search_repos_graphql(
                star_threshold=star_threshold,
                max_repos=max_repos,
            )
        except Exception as e:
            self.logger.error(f"GraphQL tier '{name}' failed: {e}")
            print(f"     ✗ Ошибка: {e}")

        new_count = self.database.add_batch(all_repos)
        self.database.save()

        self.logger.info(
            f"GraphQL '{name}': {len(all_repos)} total, "
            f"{new_count} new, {self.database.get_count()} in DB"
        )
        print(f"     ✓ Собрано: {len(all_repos)} (новых: {new_count})")

        return all_repos

    # ── Strategy 2: Crawler ────────────────────────────────────

    def collect_crawler(self) -> list[dict]:
        """Собрать репо через Playwright crawling trending страниц.

        Returns:
            Список собранных репозиториев
        """
        self.logger.info("Starting trending crawler")
        print(f"\n  🕷️  Crawler: GitHub Trending")

        all_repos = []
        try:
            from github_crawler import GitHubTrendingCrawler
            crawler = GitHubTrendingCrawler()
            crawler.start()
            all_repos = crawler.crawl()
            crawler.stop()
        except ImportError:
            self.logger.warning("github_crawler not available, skipping")
            print("     ⚠ Модуль crawler недоступен, пропускаю")
        except Exception as e:
            self.logger.error(f"Crawler failed: {e}")
            print(f"     ✗ Ошибка: {e}")
            try:
                crawler.stop()
            except Exception:
                pass

        new_count = self.database.add_batch(all_repos)
        self.database.save()

        self.logger.info(
            f"Crawler: {len(all_repos)} total, "
            f"{new_count} new, {self.database.get_count()} in DB"
        )
        print(f"     ✓ Собрано: {len(all_repos)} (новых: {new_count})")

        return all_repos

    # ── Strategy 3: Topics ─────────────────────────────────────

    def collect_topic_tier(self, ttier: dict) -> list[dict]:
        """Собрать репо по topic через REST Search API.

        Args:
            ttier: Словарь с key 'topic' (GitHub topic name)

        Returns:
            Список собранных репозиториев
        """
        topic = ttier.get("topic", "")
        name = ttier.get("name", f"topic-{topic}")
        label = ttier.get("label", topic)

        self.logger.info(f"Topic tier '{name}' ({label}): topic={topic}")
        print(f"\n  🏷️  Topic: {label}")

        all_repos = []
        seen_names = set()
        page = 1

        while True:
            try:
                response = self.github._request(
                    "GET",
                    "/search/repositories",
                    params={
                        "q": f"topic:{topic} stars:>10",
                        "sort": "stars",
                        "order": "desc",
                        "per_page": self.per_page,
                        "page": page,
                    },
                )
            except Exception as e:
                self.logger.error(f"Topic '{name}' page {page} failed: {e}")
                break

            if response.status_code != 200:
                break

            data = response.json()
            items = data.get("items", [])

            if not items:
                break

            for repo in items:
                fn = repo.get("full_name")
                if fn and fn not in seen_names:
                    seen_names.add(fn)
                    all_repos.append(repo)

            if len(items) < self.per_page:
                break

            page += 1
            if page > 10:
                break
            time.sleep(0.5)

        new_count = self.database.add_batch(all_repos)
        self.database.save()

        self.logger.info(
            f"Topic '{name}': {len(all_repos)} total, "
            f"{new_count} new, {self.database.get_count()} in DB"
        )
        print(f"     ✓ Собрано: {len(all_repos)} (новых: {new_count})")

        return all_repos

    # ── Orchestrator: All strategies ───────────────────────────

    def collect_all_strategies(self, target_count: int) -> dict:
        """Собрать базу до target_count репо, используя все стратегии.

        Порядок стратегий (от звёздных к менее звёздным):
          1. GraphQL stars>100 → >50 → >10
          2. REST star tiers (legend, popular, well-known)
          3. REST language tiers
          4. Crawler (trending)
          5. Topic tiers

        После каждой стратегии проверяет: достигли ли лимита?

        Args:
            target_count: Желаемое количество репозиториев в базе

        Returns:
            Словарь со статистикой
        """
        before_count = self.database.get_count()
        original_count = before_count

        if before_count >= target_count:
            print(f"\n  ✓ База уже содержит {before_count} репо (≥ {target_count})")
            return {
                "status": "satisfied",
                "repos_before": before_count,
                "repos_after": before_count,
                "strategies_used": [],
            }

        deficit = target_count - before_count
        print(f"\n  ════════════════════════════════════════════════════")
        print(f"  АВТОСБОР БАЗЫ: {before_count} → {target_count} репо (дефицит: {deficit})")
        print(f"  ════════════════════════════════════════════════════\n")

        strategies_used = []
        step = 0

        # ── Phase 1: GraphQL (highest stars first) ───────────
        print("  ── Фаза 1: GraphQL API ──")
        for gtier in self.graphql_tiers:
            if self.database.get_count() >= target_count:
                break
            if self.state.is_tier_done(gtier["name"]):
                print(f"  ⏭  {gtier['label']} — уже собран")
                continue

            step += 1
            print(f"\n  [{step}] {gtier['label']}")
            try:
                self.collect_graphql_tier(gtier)
                self.state.mark_tier_done(gtier["name"], self.database.get_count())
                strategies_used.append(f"gql:{gtier['name']}")
            except Exception as e:
                self.logger.error(f"GraphQL tier '{gtier['name']}' failed: {e}")

            progress = self.database.get_count()
            print(f"  → Промежуточный итог: {progress}/{target_count}")

        # ── Phase 2: REST star tiers ─────────────────────────
        print(f"\n  ── Фаза 2: REST Search API ──")
        for tier in self.tiers:
            if self.database.get_count() >= target_count:
                break
            if self.state.is_tier_done(tier["name"]):
                print(f"  ⏭  {tier['label']} — уже собран")
                continue

            step += 1
            print(f"\n  [{step}] {tier['label']}")
            try:
                self.collect_tier(tier)
                self.state.mark_tier_done(tier["name"], self.database.get_count())
                strategies_used.append(f"rest:{tier['name']}")
            except Exception as e:
                self.logger.error(f"REST tier '{tier['name']}' failed: {e}")

            progress = self.database.get_count()
            print(f"  → Промежуточный итог: {progress}/{target_count}")

        # ── Phase 3: Crawler ─────────────────────────────────
        if self.database.get_count() < target_count:
            print(f"\n  ── Фаза 3: Trending Crawler ──")
            step += 1
            print(f"\n  [{step}] GitHub Trending Crawler")
            try:
                self.collect_crawler()
                self.state.mark_tier_done("crawler", self.database.get_count())
                strategies_used.append("crawler")
            except Exception as e:
                self.logger.error(f"Crawler failed: {e}")

            progress = self.database.get_count()
            print(f"  → Промежуточный итог: {progress}/{target_count}")

        # ── Phase 4: Topic tiers ─────────────────────────────
        if self.database.get_count() < target_count:
            print(f"\n  ── Фаза 4: Topic Search ──")
            for ttier in self.topic_tiers:
                if self.database.get_count() >= target_count:
                    break
                if self.state.is_tier_done(ttier["name"]):
                    print(f"  ⏭  {ttier['label']} — уже собран")
                    continue

                step += 1
                print(f"\n  [{step}] {ttier['label']}")
                try:
                    self.collect_topic_tier(ttier)
                    self.state.mark_tier_done(ttier["name"], self.database.get_count())
                    strategies_used.append(f"topic:{ttier['name']}")
                except Exception as e:
                    self.logger.error(f"Topic tier '{ttier['name']}' failed: {e}")

                progress = self.database.get_count()
                print(f"  → Промежуточный итог: {progress}/{target_count}")

        # ── Final summary ────────────────────────────────────
        after_count = self.database.get_count()
        status = "satisfied" if after_count >= target_count else "partial"

        print(f"\n  ════════════════════════════════════════════════════")
        print(f"  РЕЗУЛЬТАТ АВТОСБОРА")
        print(f"  ════════════════════════════════════════════════════")
        print(f"  Стратегий использовано: {len(strategies_used)}")
        print(f"  Было репозиториев:  {original_count}")
        print(f"  Стало репозиториев: {after_count}")
        print(f"  Новых добавлено:    {after_count - original_count}")
        print(f"  Целевой лимит:      {target_count}")
        print(f"  Статус:             {'✓ Достигнут' if status == 'satisfied' else '○ Частично'}")
        print(f"  ════════════════════════════════════════════════════")

        return {
            "status": status,
            "repos_before": original_count,
            "repos_after": after_count,
            "repos_new": after_count - original_count,
            "target_count": target_count,
            "strategies_used": strategies_used,
        }

    def collect_next_tiers(self, count: int = 2) -> dict:
        """Собрать следующие несобранные тира.

        Args:
            count: Сколько тир собрать за этот запуск

        Returns:
            Словарь со статистикой запуска
        """
        tier_names = [t["name"] for t in self.tiers]
        start_index = self.state.get_next_tier_index(tier_names)

        if start_index >= len(self.tiers):
            print("\n  ✓ Все тира уже собраны!")
            print(f"     В базе: {self.database.get_count()} репозиториев")
            return {"status": "complete", "tiers_collected": 0}

        end_index = min(start_index + count, len(self.tiers))
        tiers_to_collect = self.tiers[start_index:end_index]

        print(f"\n  Запускаю сбор тиров {start_index + 1}-{end_index} из {len(self.tiers)}")
        print(f"  Текущая база: {self.database.get_count()} репозиториев\n")

        before_count = self.database.get_count()
        collected_tiers = []

        for tier in tiers_to_collect:
            try:
                self.collect_tier(tier)
                tier_name = tier["name"]
                # Count new repos added by this tier
                self.state.mark_tier_done(
                    tier_name,
                    self.database.get_count() - before_count,
                )
                collected_tiers.append(tier_name)
                before_count = self.database.get_count()
            except Exception as e:
                self.logger.error(f"Failed to collect tier '{tier['name']}': {e}")
                print(f"     ✗ Ошибка тира '{tier['name']}': {e}")

        after_count = self.database.get_count()
        stats = {
            "status": "partial" if end_index < len(self.tiers) else "complete",
            "tiers_collected": len(collected_tiers),
            "tier_names": collected_tiers,
            "repos_before": self.database.get_count() - (after_count - before_count),
            "repos_after": after_count,
            "repos_new": after_count - (self.database.get_count() - (after_count - before_count)),
        }

        print(f"\n  === Результат ===")
        print(f"  Собрано тиров: {len(collected_tiers)}")
        print(f"  Всего в базе: {after_count} репозиториев")

        return stats

    def collect_until_count(self, target_count: int) -> dict:
        """Собрать базу до target_count репо (delegate to orchestrator).

        Args:
            target_count: Желаемое количество репозиториев в базе

        Returns:
            Словарь со статистикой
        """
        return self.collect_all_strategies(target_count)

    def get_repos_for_archiver(self, journal) -> list[dict]:
        """Получить репозитории из базы, которых ещё нет в журнале.

        Args:
            journal: Экземпляр Journal для проверки

        Returns:
            Список репозиториев, готовых к скачиванию
        """
        db_repos = self.database.get_all_sorted(sort_by="stargazers_count", reverse=True)
        new_repos = []

        for repo in db_repos:
            fn = repo.get("full_name")
            if fn and not journal.is_in_journal(fn) and not journal.is_ignored(fn):
                new_repos.append(repo)

        self.logger.info(
            f"Found {len(new_repos)} repos in DB not in journal "
            f"(out of {len(db_repos)} total)"
        )
        return new_repos

    def show_status(self):
        """Показать текущее состояние коллектора."""
        db_stats = self.database.get_stats()
        state_stats = self.state.get_stats()

        total_tiers = len(self.tiers) + len(self.graphql_tiers) + len(self.topic_tiers) + 1  # +1 crawler

        print("\n" + "═" * 60)
        print("  Состояние коллектора репозиториев")
        print("═" * 60)
        print(f"\n  База данных:")
        print(f"    Репозиториев: {db_stats['total']}")
        print(f"    Макс. звёзд:  {db_stats['max_stars']:,}")
        print(f"    Ср. звёзд:    {db_stats['avg_stars']:,}")

        print(f"\n  Прогресс сбора:")
        print(f"    Запусков:      {state_stats['total_runs']}")
        print(f"    Тиров собрано: {state_stats['total_tiers_done']}/{total_tiers}")
        print(f"    Последний раз: {state_stats['last_run']}")

        print(f"\n  GraphQL тира:")
        for i, tier in enumerate(self.graphql_tiers):
            done = self.state.is_tier_done(tier["name"])
            status = "✓" if done else "○"
            tier_info = self.state.data.get("tiers", {}).get(tier["name"], {})
            count_str = f" ({tier_info.get('count', '?')} репо)" if done else ""
            print(f"    {status} {i+1}. {tier['label']}{count_str}")

        print(f"\n  REST тира:")
        for i, tier in enumerate(self.tiers):
            done = self.state.is_tier_done(tier["name"])
            status = "✓" if done else "○"
            tier_info = self.state.data.get("tiers", {}).get(tier["name"], {})
            count_str = f" ({tier_info.get('count', '?')} репо)" if done else ""
            print(f"    {status} {i+1}. {tier['label']}{count_str}")

        crawler_done = self.state.is_tier_done("crawler")
        crawler_info = self.state.data.get("tiers", {}).get("crawler", {})
        crawler_count = f" ({crawler_info.get('count', '?')} репо)" if crawler_done else ""
        print(f"\n  Crawler:")
        print(f"    {'✓' if crawler_done else '○'} GitHub Trending{crawler_count}")

        print(f"\n  Topic тира:")
        for i, tier in enumerate(self.topic_tiers):
            done = self.state.is_tier_done(tier["name"])
            status = "✓" if done else "○"
            tier_info = self.state.data.get("tiers", {}).get(tier["name"], {})
            count_str = f" ({tier_info.get('count', '?')} репо)" if done else ""
            print(f"    {status} {i+1}. {tier['label']}{count_str}")
