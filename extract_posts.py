from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import (
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)


LINKEDIN_POST_RE = re.compile(
    r"https?://(?:www\.)?linkedin\.com/posts/[^\s?#]+",
    re.IGNORECASE,
)

ACTIVITY_ID_RE = re.compile(r"activity-(\d+)")

# Expected collection-run folder format:
# runs/20260722_104530/
RUN_DIR_RE = re.compile(r"^\d{8}_\d{6}$")

EXTRACTOR_VERSION = "1.0"


@dataclass
class PostRecord:
    source_collection_run_id: str
    source_urls_file: str
    extraction_run_id: str

    post_id: str
    url: str

    author: str | None
    headline: str | None
    company: str | None
    published_date: str | None

    likes: int | None
    comments: int | None

    post_text: str | None
    hashtags: list[str]
    images: list[str]

    page_title: str | None

    extracted_at: str
    extraction_status: str
    extraction_error: str | None
    extractor_version: str


def utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_url(url: str) -> str | None:
    """
    Find and normalize a LinkedIn post URL.

    Tracking parameters, query strings, and fragments are removed.
    """
    match = LINKEDIN_POST_RE.search(url.strip())

    if not match:
        return None

    value = match.group(0).rstrip("/.,);]}>\"'")
    parsed = urlparse(value)

    if "/posts/" not in parsed.path:
        return None

    return f"https://www.linkedin.com{parsed.path.rstrip('/')}"


def stable_post_id(url: str) -> str:
    """
    Return a stable post identifier.

    Uses the LinkedIn activity ID when available. Otherwise, it creates a
    deterministic hash from the normalized URL.
    """
    match = ACTIVITY_ID_RE.search(url)

    if match:
        return f"linkedin_{match.group(1)}"

    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return f"linkedin_{digest}"


def find_latest_run(runs_dir: Path) -> Path:
    """
    Find the newest timestamped folder under runs/.

    Only folders containing urls.txt are considered valid collection runs.
    """
    if not runs_dir.exists():
        raise FileNotFoundError(
            f"Runs directory not found: {runs_dir.resolve()}"
        )

    candidates = [
        path
        for path in runs_dir.iterdir()
        if path.is_dir()
        and RUN_DIR_RE.fullmatch(path.name)
        and (path / "urls.txt").is_file()
    ]

    if not candidates:
        raise FileNotFoundError(
            "No timestamped run folder containing urls.txt was found under "
            f"{runs_dir.resolve()}"
        )

    # Because folder names use YYYYMMDD_HHMMSS, lexical ordering is also
    # chronological ordering.
    latest_run = max(candidates, key=lambda path: path.name)

    return latest_run


def load_urls(urls_path: Path) -> list[str]:
    """
    Read unique LinkedIn post URLs from urls.txt.

    The order of first appearance is preserved.
    """
    if not urls_path.exists():
        raise FileNotFoundError(
            f"URL file not found: {urls_path.resolve()}"
        )

    seen: set[str] = set()
    urls: list[str] = []

    lines = urls_path.read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines()

    for raw_line in lines:
        candidate = normalize_url(raw_line)

        # Allows limited compatibility with tab-separated lines.
        if not candidate and "\t" in raw_line:
            candidate = normalize_url(raw_line.split("\t")[-1])

        if candidate and candidate not in seen:
            seen.add(candidate)
            urls.append(candidate)

    if not urls:
        raise ValueError(
            f"No LinkedIn post URLs found in {urls_path.resolve()}"
        )

    return urls


def clean_text(value: str | None) -> str | None:
    """Collapse repeated whitespace and return None for empty values."""
    if value is None:
        return None

    cleaned = re.sub(r"\s+", " ", value).strip()

    return cleaned or None


def first_text(page: Page, selectors: list[str]) -> str | None:
    """
    Return text from the first selector that produces a non-empty value.
    """
    for selector in selectors:
        try:
            locator = page.locator(selector).first

            if locator.count() > 0:
                value = clean_text(
                    locator.inner_text(timeout=1800)
                )

                if value:
                    return value

        except Exception:
            continue

    return None


def first_attr(
    page: Page,
    selectors: list[str],
    attribute: str,
) -> str | None:
    """
    Return an attribute from the first matching selector.
    """
    for selector in selectors:
        try:
            locator = page.locator(selector).first

            if locator.count() > 0:
                value = clean_text(
                    locator.get_attribute(
                        attribute,
                        timeout=1800,
                    )
                )

                if value:
                    return value

        except Exception:
            continue

    return None


def parse_count(text: str | None) -> int | None:
    """
    Parse engagement counts such as:

    1,234
    1.2K
    4M
    15 comments
    """
    if not text:
        return None

    match = re.search(
        r"(\d[\d,.]*)(?:\s*)([KMB])?",
        text,
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    number = match.group(1).replace(",", "")
    suffix = (match.group(2) or "").upper()

    try:
        value = float(number)
    except ValueError:
        return None

    multipliers = {
        "": 1,
        "K": 1_000,
        "M": 1_000_000,
        "B": 1_000_000_000,
    }

    return int(value * multipliers[suffix])


def extract_hashtags(post_text: str | None) -> list[str]:
    """Extract unique lowercase hashtags from the post text."""
    if not post_text:
        return []

    seen: set[str] = set()
    hashtags: list[str] = []

    matches = re.findall(
        r"(?<!\w)#([A-Za-z0-9_]+)",
        post_text,
    )

    for match in matches:
        tag = match.lower()

        if tag not in seen:
            seen.add(tag)
            hashtags.append(tag)

    return hashtags


def extract_images(page: Page) -> list[str]:
    """
    Extract non-avatar images found inside likely post containers.
    """
    selectors = [
        "article img",
        "div.feed-shared-update-v2 img",
        "div.update-components-image img",
    ]

    seen: set[str] = set()
    images: list[str] = []

    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = min(locator.count(), 30)

            for index in range(count):
                image = locator.nth(index)

                src = image.get_attribute("src")
                alt = (image.get_attribute("alt") or "").lower()

                if not src:
                    continue

                if src.startswith("data:"):
                    continue

                if any(
                    phrase in alt
                    for phrase in (
                        "profile photo",
                        "profile picture",
                        "avatar",
                    )
                ):
                    continue

                if src not in seen:
                    seen.add(src)
                    images.append(src)

        except Exception:
            continue

    return images


def expand_post(page: Page) -> None:
    """
    Click LinkedIn's post-expansion control when present.
    """
    selectors = [
        "button:has-text('see more')",
        "button:has-text('…more')",
        "button[aria-label*='see more' i]",
    ]

    for selector in selectors:
        try:
            locator = page.locator(selector).first

            if locator.count() > 0 and locator.is_visible():
                locator.click(timeout=1500)
                page.wait_for_timeout(500)
                return

        except Exception:
            continue


def login_wall_detected(page: Page) -> bool:
    """
    Detect common LinkedIn login walls, auth walls, and checkpoints.
    """
    current_url = page.url.lower()

    blocked_url_tokens = (
        "/login",
        "/checkpoint/",
        "/authwall",
        "/uas/login",
    )

    if any(token in current_url for token in blocked_url_tokens):
        return True

    try:
        body_text = page.locator("body").inner_text(
            timeout=3000
        ).lower()
    except Exception:
        return False

    return "sign in" in body_text and "join now" in body_text


def create_context(
    playwright,
    profile_dir: Path,
    headless: bool,
) -> BrowserContext:
    """
    Create a persistent Chromium browser context.

    The profile directory stores the LinkedIn login session between runs.
    """
    profile_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    return playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=headless,
        viewport={
            "width": 1440,
            "height": 1100,
        },
        locale="en-US",
        args=[
            "--disable-blink-features=AutomationControlled",
        ],
    )


def make_empty_record(
    *,
    url: str,
    source_collection_run_id: str,
    source_urls_file: Path,
    extraction_run_id: str,
    extracted_at: str,
    status: str,
    error: str | None,
    page_title: str | None = None,
) -> PostRecord:
    """
    Create a record for a failed, blocked, or timed-out extraction.
    """
    return PostRecord(
        source_collection_run_id=source_collection_run_id,
        source_urls_file=str(source_urls_file),
        extraction_run_id=extraction_run_id,
        post_id=stable_post_id(url),
        url=url,
        author=None,
        headline=None,
        company=None,
        published_date=None,
        likes=None,
        comments=None,
        post_text=None,
        hashtags=[],
        images=[],
        page_title=page_title,
        extracted_at=extracted_at,
        extraction_status=status,
        extraction_error=error,
        extractor_version=EXTRACTOR_VERSION,
    )


def extract_post(
    page: Page,
    *,
    url: str,
    source_collection_run_id: str,
    source_urls_file: Path,
    extraction_run_id: str,
    timeout_ms: int,
) -> PostRecord:
    """
    Visit and extract one LinkedIn post.
    """
    extracted_at = utc_now_iso()

    try:
        page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=timeout_ms,
        )

        page.wait_for_timeout(2500)

        if login_wall_detected(page):
            return make_empty_record(
                url=url,
                source_collection_run_id=source_collection_run_id,
                source_urls_file=source_urls_file,
                extraction_run_id=extraction_run_id,
                extracted_at=extracted_at,
                status="login_required",
                error="LinkedIn login wall detected",
                page_title=clean_text(page.title()),
            )

        expand_post(page)

        page_title = clean_text(page.title())

        author = first_text(
            page,
            [
                ".update-components-actor__name",
                ".feed-shared-actor__name",
                "article a[href*='/in/'] span[aria-hidden='true']",
                "a[href*='/in/'] span[aria-hidden='true']",
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
            ],
        )

        headline = None

        if post_text:
            headline = post_text[:177].rstrip()

            if len(post_text) > 177:
                headline += "..."

        published_date = (
            first_attr(
                page,
                ["time"],
                "datetime",
            )
            or first_text(
                page,
                [
                    (
                        ".update-components-actor__sub-description "
                        "span[aria-hidden='true']"
                    ),
                    (
                        ".feed-shared-actor__sub-description "
                        "span[aria-hidden='true']"
                    ),
                    "time",
                ],
            )
        )

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

        likes = parse_count(likes_text)
        comments = parse_count(comments_text)

        if post_text:
            status = "success"
            error = None
        else:
            status = "partial"
            error = "Post text selector did not match"

        return PostRecord(
            source_collection_run_id=source_collection_run_id,
            source_urls_file=str(source_urls_file),
            extraction_run_id=extraction_run_id,
            post_id=stable_post_id(url),
            url=url,
            author=author,
            headline=headline,
            company=company,
            published_date=published_date,
            likes=likes,
            comments=comments,
            post_text=post_text,
            hashtags=extract_hashtags(post_text),
            images=extract_images(page),
            page_title=page_title,
            extracted_at=extracted_at,
            extraction_status=status,
            extraction_error=error,
            extractor_version=EXTRACTOR_VERSION,
        )

    except PlaywrightTimeoutError as exc:
        return make_empty_record(
            url=url,
            source_collection_run_id=source_collection_run_id,
            source_urls_file=source_urls_file,
            extraction_run_id=extraction_run_id,
            extracted_at=extracted_at,
            status="timeout",
            error=str(exc),
        )

    except Exception as exc:
        return make_empty_record(
            url=url,
            source_collection_run_id=source_collection_run_id,
            source_urls_file=source_urls_file,
            extraction_run_id=extraction_run_id,
            extracted_at=extracted_at,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read urls.txt from the latest runs/<timestamp>/ folder, "
            "extract LinkedIn posts, and write a new timestamped JSONL file."
        )
    )

    parser.add_argument(
        "--runs-dir",
        default="runs",
        help=(
            "Directory containing timestamped collection runs. "
            "Default: runs"
        ),
    )

    parser.add_argument(
        "--data-dir",
        default="data",
        help=(
            "Directory where extracted_posts_<timestamp>.jsonl is written. "
            "Default: data"
        ),
    )

    parser.add_argument(
        "--profile-dir",
        default=".linkedin-browser-profile",
        help=(
            "Persistent Chromium profile used to retain LinkedIn login."
        ),
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=4.0,
        help="Seconds to wait between LinkedIn post visits.",
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=45,
        help="Navigation timeout in seconds.",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process only N URLs. Use 0 to process all URLs.",
    )

    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Chromium without displaying the browser window.",
    )

    parser.add_argument(
        "--login-only",
        action="store_true",
        help=(
            "Open LinkedIn for interactive login and exit after Enter "
            "is pressed."
        ),
    )

    args = parser.parse_args()

    if args.delay < 0:
        raise ValueError("--delay cannot be negative")

    if args.timeout < 1:
        raise ValueError("--timeout must be at least 1 second")

    if args.limit < 0:
        raise ValueError("--limit cannot be negative")

    if args.login_only and args.headless:
        raise ValueError(
            "--login-only cannot be used together with --headless"
        )

    runs_dir = Path(args.runs_dir)
    data_dir = Path(args.data_dir)
    profile_dir = Path(args.profile_dir)

    with sync_playwright() as playwright:
        context = create_context(
            playwright,
            profile_dir=profile_dir,
            headless=args.headless,
        )

        try:
            page = (
                context.pages[0]
                if context.pages
                else context.new_page()
            )

            if args.login_only:
                page.goto(
                    "https://www.linkedin.com/login",
                    wait_until="domcontentloaded",
                )

                print(
                    "Log in to LinkedIn in the opened browser window."
                )

                input(
                    "Press Enter here after LinkedIn login is complete..."
                )

                return

            # ---------------------------------------------------------
            # INPUT SELECTION
            #
            # This is the section that reads from runs/.
            # ---------------------------------------------------------

            latest_run = find_latest_run(runs_dir)

            # Reads:
            # runs/<latest_timestamp>/urls.txt
            urls_path = latest_run / "urls.txt"

            # Loads and deduplicates URLs from that file.
            urls = load_urls(urls_path)

            if args.limit > 0:
                urls = urls[: args.limit]

            # ---------------------------------------------------------
            # OUTPUT CREATION
            # ---------------------------------------------------------

            extraction_run_id = datetime.now(
                timezone.utc
            ).strftime("%Y%m%d_%H%M%S")

            data_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            output_path = (
                data_dir
                / f"extracted_posts_{extraction_run_id}.jsonl"
            )

            print()
            print(f"Latest collection run: {latest_run.name}")
            print(f"Input file: {urls_path.resolve()}")
            print(f"Unique URLs: {len(urls)}")
            print(f"Output file: {output_path.resolve()}")
            print()

            success_count = 0
            partial_count = 0
            failed_count = 0

            # "w" creates a new file for every extraction run.
            # It does not append to a previous extraction file.
            with output_path.open(
                "w",
                encoding="utf-8",
            ) as output:
                for index, url in enumerate(
                    urls,
                    start=1,
                ):
                    print(f"[{index}/{len(urls)}] {url}")

                    record = extract_post(
                        page,
                        url=url,
                        source_collection_run_id=latest_run.name,
                        source_urls_file=urls_path,
                        extraction_run_id=extraction_run_id,
                        timeout_ms=args.timeout * 1000,
                    )

                    output.write(
                        json.dumps(
                            asdict(record),
                            ensure_ascii=False,
                        )
                        + "\n"
                    )

                    output.flush()

                    print(
                        f"  {record.extraction_status}: "
                        f"{record.author or 'unknown author'}"
                    )

                    if record.extraction_status == "success":
                        success_count += 1

                    elif record.extraction_status == "partial":
                        partial_count += 1

                    else:
                        failed_count += 1

                    if index < len(urls):
                        time.sleep(args.delay)

            print()
            print("Extraction complete")
            print(f"Successful: {success_count}")
            print(f"Partial: {partial_count}")
            print(f"Failed or blocked: {failed_count}")
            print(f"Saved to: {output_path.resolve()}")

        finally:
            context.close()


if __name__ == "__main__":
    main()
