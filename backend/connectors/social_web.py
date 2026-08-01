"""
FRIS Social & Web Connectors
RSS feeds, web scraping, Twitter/X API, LinkedIn.
These are the "signal" layer — pulling unstructured signals from the web.
"""

import asyncio
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from .base import BaseConnector, ConnectorConfig, ConnectorType


class RSSFeedConnector(BaseConnector):
    """
    RSS/Atom feed connector. Works for news sites, blogs, any RSS source.
    Config keys:
        feed_url: str           - URL of the RSS/Atom feed
        max_items: int          - max items to fetch, default 100
        include_content: bool   - fetch full article content, default False
        keywords: list          - optional keyword filter
    """

    CONNECTOR_TYPE = ConnectorType.RSS_FEED
    DISPLAY_NAME = "RSS / Atom Feed"
    DESCRIPTION = "Subscribe to any news feed, blog, or RSS source"
    ICON = "📡"
    DOCS = (
        "1. Provide the feed_url (the RSS or Atom XML endpoint, often found via a site's "
        "'/feed', '/rss', or '/atom.xml' path).\n"
        "2. No authentication is typically needed for public feeds.\n"
        "3. Each sync pulls current entries — set a sync schedule for ongoing ingestion of "
        "new articles as they're published."
    )

    CONFIG_SCHEMA = {
        "required": ["feed_url"],
        "optional": ["max_items", "include_content", "keywords"],
        "defaults": {"max_items": 100, "include_content": False}
    }

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self.cfg = config.config
        self._client: Optional[httpx.AsyncClient] = None

    async def connect(self) -> bool:
        self._client = httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            headers={"User-Agent": "FRIS-DataConnector/1.0"}
        )
        return True

    async def test_connection(self) -> Dict[str, Any]:
        try:
            if not self._client:
                await self.connect()
            start = time.time()
            response = await self._client.get(self.cfg["feed_url"])
            latency_ms = int((time.time() - start) * 1000)
            if response.status_code == 200 and ("<rss" in response.text[:500] or
                                                  "<feed" in response.text[:500] or
                                                  "<?xml" in response.text[:100]):
                return {"success": True, "message": "RSS feed accessible", "latency_ms": latency_ms}
            return {"success": False, "message": f"Not a valid RSS feed (HTTP {response.status_code})", "latency_ms": latency_ms}
        except Exception as e:
            return {"success": False, "message": str(e), "latency_ms": -1}

    def _parse_feed(self, content: str) -> List[Dict[str, Any]]:
        try:
            import feedparser
        except ImportError:
            raise RuntimeError("feedparser not installed. Run: pip install feedparser")

        feed = feedparser.parse(content)
        records = []
        keywords = self.cfg.get("keywords", [])

        for entry in feed.entries:
            record = {
                "id": getattr(entry, "id", None),
                "title": getattr(entry, "title", ""),
                "link": getattr(entry, "link", ""),
                "summary": getattr(entry, "summary", ""),
                "published": getattr(entry, "published", None),
                "author": getattr(entry, "author", None),
                "tags": [t.term for t in getattr(entry, "tags", [])],
                "source_feed": self.cfg["feed_url"]
            }

            # Keyword filter
            if keywords:
                text = f"{record['title']} {record['summary']}".lower()
                if not any(kw.lower() in text for kw in keywords):
                    continue

            records.append(record)

        return records[:self.cfg.get("max_items", 100)]

    async def fetch_sample(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self._client:
            await self.connect()
        response = await self._client.get(self.cfg["feed_url"])
        response.raise_for_status()
        return self._parse_feed(response.text)[:limit]

    async def fetch_all(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        records = await self.fetch_sample(limit=self.cfg.get("max_items", 100))
        if since:
            # Filter by published date
            filtered = []
            for r in records:
                pub = r.get("published")
                if pub:
                    try:
                        from email.utils import parsedate_to_datetime
                        pub_dt = parsedate_to_datetime(pub)
                        if pub_dt.replace(tzinfo=None) > since:
                            filtered.append(r)
                    except Exception:
                        filtered.append(r)
                else:
                    filtered.append(r)
            return filtered
        return records


class WebScraperConnector(BaseConnector):
    """
    Web scraper connector. Extracts structured data from web pages using CSS selectors.
    Config keys:
        url: str                    - URL to scrape
        record_selector: str        - CSS selector for each record container
        field_selectors: dict       - {field_name: css_selector} for each field
        field_attributes: dict      - {field_name: "attr"} to get attribute instead of text
        pagination_selector: str    - CSS selector for "next page" link
        max_pages: int              - max pages to scrape, default 1
        javascript: bool            - needs JS rendering (requires playwright), default False
        wait_seconds: float         - wait between requests, default 1.0
    """

    CONNECTOR_TYPE = ConnectorType.WEB_SCRAPER
    DISPLAY_NAME = "Web Scraper"
    DESCRIPTION = "Extract structured data from any website using CSS selectors"
    ICON = "🕷️"
    DOCS = (
        "1. Provide the target_url to scrape.\n"
        "2. Define field_selectors as a mapping of field name to CSS selector "
        "(e.g. {\"title\": \"h1.title\", \"price\": \".price-tag\"}).\n"
        "3. If listing multiple items per page, set item_selector to the CSS selector that "
        "wraps each repeating item — field selectors are then evaluated relative to it.\n"
        "4. Respect target sites' robots.txt and terms of service before scraping."
    )

    CONFIG_SCHEMA = {
        "required": ["url", "record_selector", "field_selectors"],
        "optional": ["field_attributes", "pagination_selector", "max_pages",
                     "javascript", "wait_seconds"],
        "defaults": {"max_pages": 1, "javascript": False, "wait_seconds": 1.0}
    }

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self.cfg = config.config
        self._client: Optional[httpx.AsyncClient] = None

    async def connect(self) -> bool:
        self._client = httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; FRIS-Connector/1.0)",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            }
        )
        return True

    async def test_connection(self) -> Dict[str, Any]:
        try:
            if not self._client:
                await self.connect()
            start = time.time()
            response = await self._client.get(self.cfg["url"])
            latency_ms = int((time.time() - start) * 1000)
            if response.status_code == 200:
                return {"success": True, "message": "Page accessible", "latency_ms": latency_ms}
            return {"success": False, "message": f"HTTP {response.status_code}", "latency_ms": latency_ms}
        except Exception as e:
            return {"success": False, "message": str(e), "latency_ms": -1}

    def _scrape_page(self, html: str, base_url: str) -> tuple[List[Dict], Optional[str]]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            raise RuntimeError("beautifulsoup4 not installed. Run: pip install beautifulsoup4 lxml")

        soup = BeautifulSoup(html, "lxml")
        containers = soup.select(self.cfg["record_selector"])
        field_selectors = self.cfg["field_selectors"]
        field_attrs = self.cfg.get("field_attributes", {})

        records = []
        for container in containers:
            record = {}
            for field_name, selector in field_selectors.items():
                element = container.select_one(selector)
                if element:
                    attr = field_attrs.get(field_name)
                    if attr:
                        record[field_name] = element.get(attr, "")
                    else:
                        record[field_name] = element.get_text(strip=True)
                else:
                    record[field_name] = None
            records.append(record)

        # Find next page
        next_url = None
        if self.cfg.get("pagination_selector"):
            next_link = soup.select_one(self.cfg["pagination_selector"])
            if next_link and next_link.get("href"):
                href = next_link["href"]
                if href.startswith("http"):
                    next_url = href
                else:
                    from urllib.parse import urljoin
                    next_url = urljoin(base_url, href)

        return records, next_url

    async def fetch_sample(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self._client:
            await self.connect()
        response = await self._client.get(self.cfg["url"])
        response.raise_for_status()
        records, _ = self._scrape_page(response.text, self.cfg["url"])
        return records[:limit]

    async def fetch_all(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        if not self._client:
            await self.connect()

        all_records = []
        current_url = self.cfg["url"]
        max_pages = self.cfg.get("max_pages", 1)
        wait = self.cfg.get("wait_seconds", 1.0)

        for page_num in range(max_pages):
            response = await self._client.get(current_url)
            response.raise_for_status()
            records, next_url = self._scrape_page(response.text, current_url)
            all_records.extend(records)

            if not next_url or page_num >= max_pages - 1:
                break

            current_url = next_url
            await asyncio.sleep(wait)

        return all_records


class TwitterXConnector(BaseConnector):
    """
    Twitter/X API v2 connector.
    Config keys:
        bearer_token: str       - X API v2 Bearer Token
        query: str              - search query, e.g. "Pakistan startup -is:retweet"
        user_ids: list          - specific user IDs to follow
        max_results: int        - max per request (10-100), default 100
        tweet_fields: list      - extra fields: ["author_id", "created_at", "public_metrics"]
        expansions: list        - ["author_id", "referenced_tweets.id"]
    """

    CONNECTOR_TYPE = ConnectorType.TWITTER_X
    DISPLAY_NAME = "Twitter / X"
    DESCRIPTION = "Pull posts, accounts, and signals from Twitter/X API v2"
    ICON = "𝕏"
    DOCS = (
        "1. Get a Bearer Token from the X Developer Portal (developer.x.com) for your app.\n"
        "2. Set query to a search query (e.g. 'from:username' or keyword search) or "
        "user_id to pull a specific account's timeline.\n"
        "3. API access tier (Free/Basic/Pro) determines your rate limits and historical depth — "
        "check your tier before configuring large backfills."
    )

    CONFIG_SCHEMA = {
        "required": ["bearer_token"],
        "required_one_of_optional": ["query", "user_ids"],
        "optional": ["max_results", "tweet_fields", "expansions", "start_time"],
        "defaults": {
            "max_results": 100,
            "tweet_fields": ["created_at", "author_id", "public_metrics", "lang", "entities"],
            "expansions": ["author_id"]
        }
    }

    BASE_URL = "https://api.twitter.com/2"

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self.cfg = config.config
        self._client: Optional[httpx.AsyncClient] = None

    async def connect(self) -> bool:
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self.cfg['bearer_token']}"},
            timeout=30
        )
        return True

    async def test_connection(self) -> Dict[str, Any]:
        try:
            if not self._client:
                await self.connect()
            start = time.time()
            response = await self._client.get(f"{self.BASE_URL}/tweets/search/recent",
                                               params={"query": "test", "max_results": 10})
            latency_ms = int((time.time() - start) * 1000)
            if response.status_code == 200:
                return {"success": True, "message": "X API connected", "latency_ms": latency_ms}
            elif response.status_code == 401:
                return {"success": False, "message": "Invalid bearer token", "latency_ms": latency_ms}
            else:
                return {"success": False, "message": f"HTTP {response.status_code}", "latency_ms": latency_ms}
        except Exception as e:
            return {"success": False, "message": str(e), "latency_ms": -1}

    def _build_tweet_params(self, next_token: Optional[str] = None, since: Optional[datetime] = None) -> Dict:
        params = {
            "max_results": min(self.cfg.get("max_results", 100), 100),
            "tweet.fields": ",".join(self.cfg.get("tweet_fields", ["created_at", "author_id", "public_metrics"])),
        }
        if self.cfg.get("expansions"):
            params["expansions"] = ",".join(self.cfg["expansions"])
        if next_token:
            params["next_token"] = next_token
        if since:
            params["start_time"] = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        elif self.cfg.get("start_time"):
            params["start_time"] = self.cfg["start_time"]
        return params

    def _flatten_tweet(self, tweet: Dict, includes: Dict) -> Dict[str, Any]:
        flat = {
            "tweet_id": tweet.get("id"),
            "text": tweet.get("text"),
            "created_at": tweet.get("created_at"),
            "author_id": tweet.get("author_id"),
            "lang": tweet.get("lang"),
        }
        metrics = tweet.get("public_metrics", {})
        flat.update({
            "retweet_count": metrics.get("retweet_count", 0),
            "reply_count": metrics.get("reply_count", 0),
            "like_count": metrics.get("like_count", 0),
            "quote_count": metrics.get("quote_count", 0),
        })
        # Enrich with author info if available
        users = {u["id"]: u for u in includes.get("users", [])}
        author = users.get(tweet.get("author_id", ""), {})
        if author:
            flat["author_username"] = author.get("username")
            flat["author_name"] = author.get("name")
        return flat

    async def fetch_sample(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self._client:
            await self.connect()

        query = self.cfg.get("query", "")
        if not query:
            return []

        params = self._build_tweet_params()
        params["max_results"] = min(limit, 100)
        params["query"] = query

        response = await self._client.get(f"{self.BASE_URL}/tweets/search/recent", params=params)
        response.raise_for_status()
        data = response.json()

        tweets = data.get("data", [])
        includes = data.get("includes", {})
        return [self._flatten_tweet(t, includes) for t in tweets]

    async def fetch_all(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        if not self._client:
            await self.connect()

        all_tweets = []
        query = self.cfg.get("query", "")
        if not query:
            return []

        next_token = None
        max_pages = 10  # safety limit

        for _ in range(max_pages):
            params = self._build_tweet_params(next_token=next_token, since=since)
            params["query"] = query
            response = await self._client.get(f"{self.BASE_URL}/tweets/search/recent", params=params)

            if response.status_code == 429:
                await asyncio.sleep(60)
                continue

            response.raise_for_status()
            data = response.json()
            tweets = data.get("data", [])
            includes = data.get("includes", {})

            all_tweets.extend([self._flatten_tweet(t, includes) for t in tweets])

            next_token = data.get("meta", {}).get("next_token")
            if not next_token:
                break
            await asyncio.sleep(0.5)

        return all_tweets


class LinkedInConnector(BaseConnector):
    """
    LinkedIn connector via RapidAPI proxy (official API requires partnership).
    Config keys:
        rapidapi_key: str       - RapidAPI key for LinkedIn scraper API
        search_type: str        - "company" | "person" | "posts"
        keywords: list          - search terms
        company_urls: list      - specific company LinkedIn URLs
        max_results: int        - default 50
    """

    CONNECTOR_TYPE = ConnectorType.LINKEDIN
    DISPLAY_NAME = "LinkedIn"
    DESCRIPTION = "Pull company profiles, people, and posts from LinkedIn"
    ICON = "💼"
    DOCS = (
        "1. LinkedIn's official API has restrictive partner access, so this connector goes "
        "through a RapidAPI proxy — sign up for a LinkedIn data API on RapidAPI and get an "
        "API key.\n"
        "2. Provide the rapidapi_key and rapidapi_host for the specific API you've subscribed to.\n"
        "3. Set the target query (e.g. a company name, profile URL, or search term) depending "
        "on which proxy API you're using — check that API's docs for exact parameter names."
    )

    CONFIG_SCHEMA = {
        "required": ["rapidapi_key", "search_type"],
        "optional": ["keywords", "company_urls", "max_results"],
        "defaults": {"search_type": "company", "max_results": 50}
    }

    RAPIDAPI_HOST = "linkedin-data-api.p.rapidapi.com"

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self.cfg = config.config
        self._client: Optional[httpx.AsyncClient] = None

    async def connect(self) -> bool:
        self._client = httpx.AsyncClient(
            headers={
                "X-RapidAPI-Key": self.cfg["rapidapi_key"],
                "X-RapidAPI-Host": self.RAPIDAPI_HOST
            },
            timeout=30
        )
        return True

    async def test_connection(self) -> Dict[str, Any]:
        try:
            if not self._client:
                await self.connect()
            start = time.time()
            response = await self._client.get(
                f"https://{self.RAPIDAPI_HOST}/get-company-details",
                params={"username": "linkedin"}
            )
            latency_ms = int((time.time() - start) * 1000)
            if response.status_code == 200:
                return {"success": True, "message": "LinkedIn API connected", "latency_ms": latency_ms}
            return {"success": False, "message": f"HTTP {response.status_code}: check your RapidAPI key", "latency_ms": latency_ms}
        except Exception as e:
            return {"success": False, "message": str(e), "latency_ms": -1}

    async def fetch_sample(self, limit: int = 10) -> List[Dict[str, Any]]:
        if not self._client:
            await self.connect()

        records = []
        search_type = self.cfg.get("search_type", "company")

        if search_type == "company" and self.cfg.get("keywords"):
            for keyword in self.cfg["keywords"][:3]:
                response = await self._client.get(
                    f"https://{self.RAPIDAPI_HOST}/search-companies",
                    params={"keywords": keyword, "start": 0}
                )
                if response.status_code == 200:
                    data = response.json()
                    companies = data.get("items", [])
                    records.extend(companies[:limit])
                await asyncio.sleep(0.5)

        return records[:limit]

    async def fetch_all(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        return await self.fetch_sample(limit=self.cfg.get("max_results", 50))
