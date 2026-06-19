"""
Scraper для GitHub Trending страниц.

Собирает репозитории с github.com/trending (разные языки, разные периоды).
Запускает собственный Chromium, не трогая MAX-браузер.
"""

import re
import time
from typing import Optional

from playwright.sync_api import sync_playwright
from logging_config import LogMixin


class GitHubTrendingCrawler(LogMixin):
    """Скалолаз по GitHub Trending.

    Запускает отдельный Chromium через Playwright, парсит trending страницы
    для разных языков и периодов. Возвращает список репо-словарей.
    """

    LANGUAGES = [
        "python", "javascript", "typescript", "rust", "go",
        "java", "cpp", "ruby", "php", "swift", "kotlin",
    ]
    SPANS = ["today", "weekly", "monthly"]

    TRENDING_URL = "https://github.com/trending"

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._page = None

    # ── Lifecycle ──────────────────────────────────────────────

    def start(self):
        """Запустить браузер."""
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox"],
        )
        self._page = self._browser.new_page()
        self._page.set_default_timeout(30000)
        self.logger.info("Trending crawler browser started")

    def stop(self):
        """Закрыть браузер."""
        if self._page:
            self._page.close()
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()
        self.logger.info("Trending crawler browser stopped")

    # ── Scraping ───────────────────────────────────────────────

    def crawl(
        self,
        languages: Optional[list[str]] = None,
        spans: Optional[list[str]] = None,
    ) -> list[dict]:
        """Собрать репо со всех trending страниц.

        Args:
            languages: Языки для сканирования (None = все)
            spans: Периоды (None = все)

        Returns:
            Список репо-словарей
        """
        languages = languages or self.LANGUAGES
        spans = spans or self.SPANS

        all_repos = []
        seen = set()

        for lang in languages:
            for span in spans:
                repos = self._crawl_page(lang, span)
                for r in repos:
                    fn = r.get("full_name", "")
                    if fn and fn not in seen:
                        seen.add(fn)
                        all_repos.append(r)

                self.logger.info(
                    f"Crawler {lang}/{span}: {len(repos)} found, "
                    f"{len(all_repos)} total unique"
                )
                time.sleep(1)  # polite delay between pages

        self.logger.info(f"Crawler complete: {len(all_repos)} unique repos")
        return all_repos

    def _crawl_page(self, language: str, span: str) -> list[dict]:
        """Собрать одну страницу trending."""
        url = self.TRENDING_URL
        if language and language != "":
            url = f"{self.TRENDING_URL}/{language}"
        if span and span != "daily":
            url = f"{self.TRENDING_URL}?since={span}"
            if language and language != "":
                url = f"{self.TRENDING_URL}/{language}?since={span}"

        try:
            self._page.goto(url, wait_until="networkidle")
            time.sleep(2)  # let JS render

            # Parse repo cards
            repos = []

            # Trending repos are in <article> tags
            articles = self._page.query_selector_all("article")

            for article in articles:
                repo = self._parse_article(article)
                if repo:
                    repos.append(repo)

            return repos

        except Exception as e:
            self.logger.error(f"Crawler error on {url}: {e}")
            return []

    def _parse_article(self, article) -> Optional[dict]:
        """Извлечь данные из <article> элемента."""
        try:
            # Repo link: <h2 class="h3 lh-condensed">
            #   <a href="/owner/repo">repo</a>
            # </h2>
            h2 = article.query_selector("h2 a[href]")
            if not h2:
                return None

            href = h2.get_attribute("href") or ""
            # href like "/owner/repo"
            full_name = href.strip("/").split("?")[0]
            if "/" not in full_name:
                return None

            name = full_name.split("/", 1)[1]

            # Description
            desc_el = article.query_selector("p")
            description = ""
            if desc_el:
                description = desc_el.inner_text().strip()

            # Stars (shown at bottom of card)
            stars = 0
            stars_el = article.query_selector(
                "a[href$='/stargazers']"
            ) or article.query_selector("a[href*='/stargazers']")
            if stars_el:
                stars_text = stars_el.inner_text().strip()
                # Parse "⭐ 12.3k" or "12,345"
                stars = self._parse_number(stars_text)

            # Language
            language = None
            lang_el = article.query_selector("span[itemprop='programmingLanguage']")
            if lang_el:
                language = lang_el.inner_text().strip()

            return {
                "name": name,
                "full_name": full_name,
                "html_url": f"https://github.com/{full_name}",
                "description": description,
                "stargazers_count": stars,
                "forks_count": 0,
                "default_branch": "main",
                "language": language,
                "updated_at": "",
                "pushed_at": "",
                "_source": "trending_crawler",
            }

        except Exception as e:
            self.logger.debug(f"Parse error: {e}")
            return None

    @staticmethod
    def _parse_number(text: str) -> int:
        """Parse '12.3k', '1.2m', '12,345' → int."""
        text = text.lower().replace(",", "").strip()
        try:
            if "m" in text:
                return int(float(text.replace("m", "")) * 1_000_000)
            if "k" in text:
                return int(float(text.replace("k", "")) * 1_000)
            return int(float(text))
        except (ValueError, TypeError):
            return 0
