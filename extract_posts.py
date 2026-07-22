from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

from playwright.sync_api import BrowserContext, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright


LINKEDIN_POST_RE = re.compile(r"https?://(?:www\.)?linkedin\.com/posts/[^\s?#]+", re.I)
ACTIVITY_ID_RE = re.compile(r"activity-(\d+)")
DATE_PATTERNS = [
    re.compile(r"\b(\d+)\s*(?:m|min|minute|minutes)\b", re.I),
    re.compile(r"\b(\d+)\s*(?:h|hr|hour|hours)\b", re.I),
    re.compile(r"\b(\d+)\s*(?:d|day|days)\b", re.I),
    re.compile(r"\b(\d+)\s*(?:w|week|weeks)\b", re.I),
    re.compile(r"\b(\d+)\s*(?:mo|month|months)\b", re.I),
    re.compile(r"\b(\d+)\s*(?:y|yr|year|years)\b", re.I),
]


@dataclass
class PostRecord:
    id: str
    url: str
    author: str | None
    headline: str | None
    company: str | None
    date_text: str | None
    likes: int | None
    comments: int | None
    post_text: str | None
    page_title: str | None
    source_file: str
    source_query: str | None
    failure_mode_seed: str | None
    symptom_seed: str | None
    retrieved_at: str
    extraction_status: str
    extraction_error: str | None


def normalize_url(url: str) -> str | None:
    match = LINKEDIN_POST_RE.search(url.strip())
    if not match:
        return None
    value = match.group(0).rstrip("/.,)")
    parsed = urlparse(value)
    return f"https://www.linkedin.com{parsed.path.rstrip('/')}"


def stable_id(url: str) -> str:
    activity_match = ACTIVITY_ID_RE.search(url)
    if activity_match:
        return f"linkedin_{activity_match.group(1)}"
    return "linkedin_" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def parse_url_files(paths: Iterable[Path]) -> list[dict]:
    records: list[dict] = []
    seen: set[str] = set()

    for path in paths:
        current_query: str | None = None
        failure_mode: str | None = None
        symptom: str | None = None

        for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line:
                continue

            if line.startswith("QUERY\t"):
                parts = line.split("\t")
                if len(parts) >= 4:
                    failure_mode = parts[1] or None
                    symptom = parts[2] or None
                    current_query = parts[3] or None
                continue

            if line.startswith("SERPER_QUERY\t"):
                continue

            candidate = normalize_url(line)
            if not candidate and "\t" in line:
                candidate = normalize_url(line.split("\t", 1)[-1])

            if candidate and candidate not in seen:
                seen.add(candidate)
                records.append(
                    {
                        "url": candidate,
                        "source_file": str(path),
                        "source_query": current_query,
                        "failure_mode_seed": failure_mode,
                        "symptom_seed": symptom,
                    }
                )

    return records


def first_text(page: Page, selectors: list[str]) -> str | None:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() > 0:
                text = locator.inner_text(timeout=1500).strip()
                if text:
                    return text
        except Exception:
            continue
    return None


def first_attr(page: Page, selectors: list[str], attribute: str) -> str | None:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() > 0:
                value = locator.get_attribute(attribute, timeout=1500)
                if value:
                    return value.strip()
        except Exception:
            continue
    return None


def parse_count(text: str | None) -> int | None:
    if not text:
        return None
    cleaned = text.replace(",", "").strip().lower()
    match = re.search(r"(\d+(?:\.\d+)?)\s*([km]?)", cleaned)
    if not match:
        return None
    value = float(match.group(1))
    suffix = match.group(2)
    if suffix == "k":
        value *= 1_000
    elif suffix == "m":
        value *= 1_000_000
    return int(value)


def extract_post(page: Page, item: dict, timeout_ms: int) -> PostRecord:
    url = item["url"]
    retrieved_at = datetime.now(timezone.utc).isoformat()

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(2500)

        # Expand truncated post copy when possible.
        for selector in [
            "button:has-text('…more')",
            "button:has-text('more')",
            "button[aria-label*='see more' i]",
        ]:
            try:
                locator = page.locator(selector).first
                if locator.count() > 0 and locator.is_visible():
                    locator.click(timeout=1200)
                    page.wait_for_timeout(400)
                    break
            except Exception:
                continue

        page_title = page.title().strip() or None

        author = first_text(
            page,
            [
                ".update-components-actor__name",
                ".feed-shared-actor__name",
                "a[href*='/in/'] span[aria-hidden='true']",
                "main h1",
            ],
        )

        company = first_text(
            page,
            [
                ".update-components-actor__description",
                ".feed-shared-actor__description",
                ".update-components-actor__sub-description",
            ],
        )

        post_text = first_text(
            page,
            [
                ".update-components-text",
                ".feed-shared-update-v2__description",
                ".feed-shared-inline-show-more-text",
                "article div[dir='ltr']",
                "main",
            ],
        )

        headline = None
        if post_text:
            headline = next((line.strip() for line in post_text.splitlines() if line.strip()), None)
            if headline and len(headline) > 180:
                headline = headline[:177].rstrip() + "..."

        date_text = first_text(
            page,
            [
                ".update-components-actor__sub-description span[aria-hidden='true']",
                ".feed-shared-actor__sub-description span[aria-hidden='true']",
                "time",
            ],
        ) or first_attr(page, ["time"], "datetime")

        likes_text = first_text(
            page,
            [
                "button[aria-label*='reaction' i]",
                ".social-details-social-counts__reactions-count",
                "span[aria-label*='reaction' i]",
            ],
        )
        comments_text = first_text(
            page,
            [
                "button[aria-label*='comment' i]",
                ".social-details-social-counts__comments",
                "span[aria-label*='comment' i]",
            ],
        )

        # If LinkedIn served a login wall, preserve the record as blocked rather than
        # pretending the visible page chrome is post content.
        body_text = page.locator("body").inner_text(timeout=3000)
        login_wall = (
            "sign in" in body_text.lower()
            and "join now" in body_text.lower()
            and (not post_text or len(post_text) < 80)
        )

        if login_wall:
            return PostRecord(
                id=stable_id(url),
                url=url,
                author=author,
                headline=headline,
                company=company,
                date_text=date_text,
                likes=parse_count(likes_text),
                comments=parse_count(comments_text),
                post_text=None,
                page_title=page_title,
                source_file=item["source_file"],
                source_query=item.get("source_query"),
                failure_mode_seed=item.get("failure_mode_seed"),
                symptom_seed=item.get("symptom_seed"),
                retrieved_at=retrieved_at,
                extraction_status="login_required",
                extraction_error="LinkedIn login wall detected",
            )

        return PostRecord(
            id=stable_id(url),
            url=url,
            author=author,
            headline=headline,
            company=company,
            date_text=date_text,
            likes=parse_count(likes_text),
            comments=parse_count(comments_text),
            post_text=post_text,
            page_title=page_title,
            source_file=item["source_file"],
            source_query=item.get("source_query"),
            failure_mode_seed=item.get("failure_mode_seed"),
            symptom_seed=item.get("symptom_seed"),
            retrieved_at=retrieved_at,
            extraction_status="success" if post_text else "partial",
            extraction_error=None if post_text else "Post text selector did not match",
        )

    except PlaywrightTimeoutError as exc:
        return PostRecord(
            id=stable_id(url), url=url, author=None, headline=None, company=None,
            date_text=None, likes=None, comments=None, post_text=None, page_title=None,
            source_file=item["source_file"], source_query=item.get("source_query"),
            failure_mode_seed=item.get("failure_mode_seed"), symptom_seed=item.get("symptom_seed"),
            retrieved_at=retrieved_at, extraction_status="timeout", extraction_error=str(exc),
        )
    except Exception as exc:
        return PostRecord(
            id=stable_id(url), url=url, author=None, headline=None, company=None,
            date_text=None, likes=None, comments=None, post_text=None, page_title=None,
            source_file=item["source_file"], source_query=item.get("source_query"),
            failure_mode_seed=item.get("failure_mode_seed"), symptom_seed=item.get("symptom_seed"),
            retrieved_at=retrieved_at, extraction_status="error", extraction_error=f"{type(exc).__name__}: {exc}",
        )


def load_completed(output_path: Path) -> set[str]:
    completed: set[str] = set()
    if not output_path.exists():
        return completed
    for line in output_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            data = json.loads(line)
            if data.get("url"):
                completed.add(data["url"])
        except json.JSONDecodeError:
            continue
    return completed


def create_context(playwright, profile_dir: Path, headless: bool) -> BrowserContext:
    profile_dir.mkdir(parents=True, exist_ok=True)
    return playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=headless,
        viewport={"width": 1440, "height": 1100},
        locale="en-US",
        args=["--disable-blink-features=AutomationControlled"],
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract LinkedIn post text and metadata from collected URL files into JSONL."
    )
    parser.add_argument(
        "--inputs",
        nargs="+",
        default=["output/urls_*.txt"],
        help="Input files or glob patterns. Default: output/urls_*.txt",
    )
    parser.add_argument(
        "--output",
        default="data/raw_posts.jsonl",
        help="JSONL output path.",
    )
    parser.add_argument(
        "--profile-dir",
        default=".linkedin-browser-profile",
        help="Persistent Chromium profile used for an optional LinkedIn login.",
    )
    parser.add_argument("--delay", type=float, default=4.0, help="Seconds between URLs.")
    parser.add_argument("--timeout", type=int, default=45, help="Navigation timeout in seconds.")
    parser.add_argument("--limit", type=int, default=0, help="Process only N URLs; 0 means all.")
    parser.add_argument("--headless", action="store_true", help="Run without showing the browser.")
    parser.add_argument(
        "--login-only",
        action="store_true",
        help="Open LinkedIn using the persistent profile, then exit after you press Enter.",
    )
    args = parser.parse_args()

    input_paths: list[Path] = []
    for pattern in args.inputs:
        matches = sorted(Path().glob(pattern)) if any(ch in pattern for ch in "*?[]") else [Path(pattern)]
        input_paths.extend(path for path in matches if path.exists())

    if not input_paths:
        raise FileNotFoundError(f"No input files matched: {args.inputs}")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    completed = load_completed(output_path)

    items = [item for item in parse_url_files(input_paths) if item["url"] not in completed]
    if args.limit > 0:
        items = items[: args.limit]

    print(f"Input files: {len(input_paths)}")
    print(f"Unique pending URLs: {len(items)}")
    print(f"Output: {output_path.resolve()}")

    with sync_playwright() as playwright:
        context = create_context(playwright, Path(args.profile_dir), args.headless)
        page = context.pages[0] if context.pages else context.new_page()

        if args.login_only:
            page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
            print("Log in to LinkedIn in the opened browser window.")
            input("Press Enter here after login is complete...")
            context.close()
            return

        with output_path.open("a", encoding="utf-8") as output:
            for index, item in enumerate(items, start=1):
                print(f"[{index}/{len(items)}] {item['url']}")
                record = extract_post(page, item, args.timeout * 1000)
                output.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
                output.flush()
                print(f"  {record.extraction_status}: {record.author or 'unknown author'}")

                if index < len(items):
                    time.sleep(args.delay)

        context.close()


if __name__ == "__main__":
    main()
