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

# Expected folder format:
# runs/20260722_104530/
RUN_DIR_RE = re.compile(r"^\d{8}_\d{6}$")


@dataclass
class PostRecord:
    post_id: str
    url: str

    author: str | None
    published_date: str | None
    post_text: str | None

    source_collection_run_id: str
    extracted_at: str

    extraction_status: str
    extraction_error: str | None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None

    cleaned = re.sub(r"\s+", " ", value).strip()

    return cleaned or None


def normalize_url(raw_value: str) -> str | None:
    match = LINKEDIN_POST_RE.search(raw_value.strip())

    if not match:
        return None

    value = match.group(0).rstrip(
        "/.,);]}>\"'"
    )

    parsed = urlparse(value)

    if "/posts/" not in parsed.path:
        return None

    return (
        f"https://www.linkedin.com"
        f"{parsed.path.rstrip('/')}"
    )


def stable_post_id(url: str) -> str:
    activity_match = ACTIVITY_ID_RE.search(url)

    if activity_match:
        return f"linkedin_{activity_match.group(1)}"

    url_hash = hashlib.sha1(
        url.encode("utf-8")
    ).hexdigest()[:16]

    return f"linkedin_{url_hash}"


def find_latest_run(runs_dir: Path) -> Path:
    if not runs_dir.exists():
        raise FileNotFoundError(
            f"Runs directory not found: "
            f"{runs_dir.resolve()}"
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
            "No timestamped run containing urls.txt "
            f"was found under {runs_dir.resolve()}"
        )

    return max(
        candidates,
        key=lambda path: path.name,
    )


def load_urls(urls_path: Path) -> list[str]:
    if not urls_path.exists():
        raise FileNotFoundError(
            f"URL file not found: "
            f"{urls_path.resolve()}"
        )

    seen: set[str] = set()
    urls: list[str] = []

    lines = urls_path.read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines()

    for raw_line in lines:
        candidate = normalize_url(raw_line)

        if not candidate and "\t" in raw_line:
            candidate = normalize_url(
                raw_line.split("\t")[-1]
            )

        if candidate and candidate not in seen:
            seen.add(candidate)
            urls.append(candidate)

    if not urls:
        raise ValueError(
            "No LinkedIn post URLs were found in "
            f"{urls_path.resolve()}"
        )

    return urls


def first_text(
    page: Page,
    selectors: list[str],
) -> str | None:
    for selector in selectors:
        try:
            locator = page.locator(selector).first

            if locator.count() == 0:
                continue

            text = clean_text(
                locator.inner_text(timeout=1800)
            )

            if text:
                return text

        except Exception:
            continue

    return None


def first_attribute(
    page: Page,
    selectors: list[str],
    attribute: str,
) -> str | None:
    for selector in selectors:
        try:
            locator = page.locator(selector).first

            if locator.count() == 0:
                continue

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


def create_browser_context(
    playwright,
    profile_dir: Path,
    headless: bool,
) -> BrowserContext:
    """
    Launch exactly one persistent browser context.

    Cookies and authentication data are saved under
    .linkedin-browser-profile and reused by future runs.
    """

    profile_dir.mkdir(
        parents=True,
        exist_ok=True,
    )
        
    return playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        channel="chrome",
        headless=headless,
        viewport={
            "width": 1440,
            "height": 1100,
        },
        locale="en-US",
    )

def authentication_page_detected(
    page: Page,
) -> bool:
    current_url = page.url.lower()

    authentication_url_tokens = (
        "/login",
        "/checkpoint/",
        "/authwall",
        "/uas/login",
    )

    return any(
        token in current_url
        for token in authentication_url_tokens
    )


def login_wall_detected(
    page: Page,
) -> bool:
    if authentication_page_detected(page):
        return True

    try:
        body_text = page.locator(
            "body"
        ).inner_text(
            timeout=3000
        ).lower()

    except Exception:
        return False

    explicit_login_wall = (
        "sign in" in body_text
        and "join now" in body_text
    )

    return explicit_login_wall


def linkedin_session_is_authenticated(
    page: Page,
) -> bool:
    if authentication_page_detected(page):
        return False

    authenticated_selectors = [
        "nav.global-nav",
        "header.global-nav",
        "a[href*='/feed/']",
        "a[href*='/mynetwork/']",
        "button[aria-label*='Me']",
    ]

    for selector in authenticated_selectors:
        try:
            locator = page.locator(selector).first

            if (
                locator.count() > 0
                and locator.is_visible()
            ):
                return True

        except Exception:
            continue

    return not login_wall_detected(page)


def ensure_linkedin_session(
    page: Page,
    *,
    timeout_ms: int,
    headless: bool,
) -> None:
    """
    Verify LinkedIn authentication before reading urls.txt.

    When the persistent profile is not authenticated, the script waits
    while the user completes login and MFA in the single Playwright
    browser window.

    This happens once before extraction begins.
    """

    print(
        "Step 1: Checking LinkedIn authentication..."
    )

    try:
        page.goto(
            "https://www.linkedin.com/feed/",
            wait_until="domcontentloaded",
            timeout=timeout_ms,
        )

    except PlaywrightTimeoutError:
        pass

    page.wait_for_timeout(2500)

    if linkedin_session_is_authenticated(page):
        print(
            "LinkedIn session is already authenticated."
        )
        return

    if headless:
        raise RuntimeError(
            "LinkedIn authentication is required, but the "
            "browser is running in headless mode. Run without "
            "--headless, complete login once, and then retry."
        )

    print()
    print(
        "LinkedIn authentication is required."
    )
    print(
        "Use the Playwright browser window that just opened."
    )
    print(
        "Complete LinkedIn login, MFA, CAPTCHA, or any "
        "security verification."
    )
    print(
        "Wait until your LinkedIn feed is visible."
    )
    print()

    try:
        page.goto(
            "https://www.linkedin.com/login",
            wait_until="domcontentloaded",
            timeout=timeout_ms,
        )

    except PlaywrightTimeoutError:
        pass

    input(
        "After the LinkedIn feed is visible, return to "
        "PowerShell and press Enter..."
    )

    try:
        page.goto(
            "https://www.linkedin.com/feed/",
            wait_until="domcontentloaded",
            timeout=timeout_ms,
        )

    except PlaywrightTimeoutError:
        pass

    page.wait_for_timeout(2500)

    if not linkedin_session_is_authenticated(page):
        raise RuntimeError(
            "LinkedIn authentication could not be confirmed. "
            "Make sure the feed is visible before pressing Enter."
        )

    print(
        "LinkedIn authentication completed. "
        "The session was saved in the persistent profile."
    )


def expand_post(page: Page) -> None:
    selectors = [
        "button:has-text('see more')",
        "button:has-text('…more')",
        "button[aria-label*='see more' i]",
    ]

    for selector in selectors:
        try:
            locator = page.locator(selector).first

            if (
                locator.count() > 0
                and locator.is_visible()
            ):
                locator.click(timeout=1500)
                page.wait_for_timeout(500)
                return

        except Exception:
            continue


def create_failed_record(
    *,
    url: str,
    source_collection_run_id: str,
    extracted_at: str,
    status: str,
    error: str,
) -> PostRecord:
    return PostRecord(
        post_id=stable_post_id(url),
        url=url,
        author=None,
        published_date=None,
        post_text=None,
        source_collection_run_id=(
            source_collection_run_id
        ),
        extracted_at=extracted_at,
        extraction_status=status,
        extraction_error=error,
    )


def extract_post(
    page: Page,
    *,
    url: str,
    source_collection_run_id: str,
    timeout_ms: int,
) -> PostRecord:
    """
    Navigate the existing browser tab to one post.

    This function does not create a browser, context, page, or login
    session. Every URL is visited in the same authenticated tab.
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
            return create_failed_record(
                url=url,
                source_collection_run_id=(
                    source_collection_run_id
                ),
                extracted_at=extracted_at,
                status="login_required",
                error=(
                    "LinkedIn returned an authentication "
                    "wall during extraction."
                ),
            )

        expand_post(page)

        author = first_text(
            page,
            [
                ".update-components-actor__name",
                ".feed-shared-actor__name",
                (
                    "article a[href*='/in/'] "
                    "span[aria-hidden='true']"
                ),
                (
                    "a[href*='/in/'] "
                    "span[aria-hidden='true']"
                ),
            ],
        )

        published_date = (
            first_attribute(
                page,
                selectors=["time"],
                attribute="datetime",
            )
            or first_text(
                page,
                [
                    (
                        ".update-components-actor__"
                        "sub-description "
                        "span[aria-hidden='true']"
                    ),
                    (
                        ".feed-shared-actor__"
                        "sub-description "
                        "span[aria-hidden='true']"
                    ),
                    "time",
                ],
            )
        )

        post_text = first_text(
            page,
            [
                ".update-components-text",
                (
                    ".feed-shared-update-v2__"
                    "description"
                ),
                (
                    ".feed-shared-inline-"
                    "show-more-text"
                ),
                "article div[dir='ltr']",
            ],
        )

        if post_text:
            status = "success"
            error = None

        else:
            status = "partial"
            error = (
                "The page loaded, but no post text matched "
                "the configured selectors."
            )

        return PostRecord(
            post_id=stable_post_id(url),
            url=url,
            author=author,
            published_date=published_date,
            post_text=post_text,
            source_collection_run_id=(
                source_collection_run_id
            ),
            extracted_at=extracted_at,
            extraction_status=status,
            extraction_error=error,
        )

    except PlaywrightTimeoutError as exc:
        return create_failed_record(
            url=url,
            source_collection_run_id=(
                source_collection_run_id
            ),
            extracted_at=extracted_at,
            status="timeout",
            error=str(exc),
        )

    except Exception as exc:
        return create_failed_record(
            url=url,
            source_collection_run_id=(
                source_collection_run_id
            ),
            extracted_at=extracted_at,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Authenticate once with LinkedIn, read urls.txt "
            "from the latest runs/<timestamp>/ folder, and "
            "write essential post content to a timestamped "
            "JSONL file."
        )
    )

    parser.add_argument(
        "--runs-dir",
        default="runs",
        help=(
            "Directory containing timestamped collection "
            "runs. Default: runs"
        ),
    )

    parser.add_argument(
        "--data-dir",
        default="data",
        help=(
            "Directory for extraction output. "
            "Default: data"
        ),
    )

    parser.add_argument(
        "--profile-dir",
        default=".linkedin-browser-profile",
        help=(
            "Persistent Playwright browser profile. "
            "Default: .linkedin-browser-profile"
        ),
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=8.0,
        help=(
            "Seconds to wait between post visits. "
            "Default: 8"
        ),
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=45,
        help=(
            "Navigation timeout in seconds. "
            "Default: 45"
        ),
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help=(
            "Process the first N URLs. "
            "Use 0 for all URLs."
        ),
    )

    parser.add_argument(
        "--headless",
        action="store_true",
        help=(
            "Run without a visible browser. Use only after "
            "a visible run has saved an authenticated session."
        ),
    )

    args = parser.parse_args()

    if args.delay < 0:
        raise ValueError(
            "--delay cannot be negative"
        )

    if args.timeout < 1:
        raise ValueError(
            "--timeout must be at least 1 second"
        )

    if args.limit < 0:
        raise ValueError(
            "--limit cannot be negative"
        )

    runs_dir = Path(args.runs_dir)
    data_dir = Path(args.data_dir)
    profile_dir = Path(args.profile_dir)

    context: BrowserContext | None = None

    try:
        with sync_playwright() as playwright:
            context = create_browser_context(
                playwright,
                profile_dir=profile_dir,
                headless=args.headless,
            )

            # Exactly one page is created and reused for all URLs.
            page = (
                context.pages[0]
                if context.pages
                else context.new_page()
            )

            timeout_ms = args.timeout * 1000

            # ==================================================
            # STEP 1: LOGIN BEFORE READING OR MINING ANY URLS
            # ==================================================

            ensure_linkedin_session(
                page,
                timeout_ms=timeout_ms,
                headless=args.headless,
            )

            # ==================================================
            # STEP 2: FIND THE LATEST COLLECTION RUN
            # ==================================================

            print(
                "Step 2: Locating the latest "
                "collection run..."
            )

            latest_run = find_latest_run(
                runs_dir
            )

            urls_path = latest_run / "urls.txt"

            # ==================================================
            # STEP 3: LOAD AND DEDUPLICATE URLS
            # ==================================================

            print(
                "Step 3: Loading and deduplicating URLs..."
            )

            urls = load_urls(urls_path)

            if args.limit > 0:
                urls = urls[:args.limit]

            # ==================================================
            # STEP 4: CREATE A NEW EXTRACTION FILE
            # ==================================================

            extraction_timestamp = datetime.now(
                timezone.utc
            ).strftime("%Y%m%d_%H%M%S")

            data_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            output_path = (
                data_dir
                / (
                    "extracted_posts_"
                    f"{extraction_timestamp}.jsonl"
                )
            )

            print()
            print(
                f"Collection run: {latest_run.name}"
            )
            print(
                f"Input file: {urls_path.resolve()}"
            )
            print(
                f"URLs to process: {len(urls)}"
            )
            print(
                f"Output file: {output_path.resolve()}"
            )
            print(
                "One browser and one tab will be reused "
                "for the entire run."
            )
            print()

            success_count = 0
            partial_count = 0
            failed_count = 0
            stopped_for_authentication = False

            with output_path.open(
                "w",
                encoding="utf-8",
            ) as output:
                for index, url in enumerate(
                    urls,
                    start=1,
                ):
                    print(
                        f"[{index}/{len(urls)}] {url}"
                    )

                    record = extract_post(
                        page,
                        url=url,
                        source_collection_run_id=(
                            latest_run.name
                        ),
                        timeout_ms=timeout_ms,
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

                    if (
                        record.extraction_status
                        == "success"
                    ):
                        success_count += 1

                    elif (
                        record.extraction_status
                        == "partial"
                    ):
                        partial_count += 1

                    else:
                        failed_count += 1

                    # Do not continue writing hundreds of
                    # login_required records.
                    if (
                        record.extraction_status
                        == "login_required"
                    ):
                        stopped_for_authentication = True

                        print()
                        print(
                            "LinkedIn authentication was "
                            "lost or challenged."
                        )
                        print(
                            "The extraction has been stopped "
                            "instead of continuing through "
                            "the remaining URLs."
                        )
                        print(
                            "Run the script visibly again to "
                            "restore the saved session."
                        )

                        break

                    if index < len(urls):
                        time.sleep(args.delay)

            print()
            print("Extraction finished")
            print(
                f"Successful: {success_count}"
            )
            print(
                f"Partial: {partial_count}"
            )
            print(
                f"Failed or blocked: {failed_count}"
            )
            print(
                f"Saved to: {output_path.resolve()}"
            )

            if stopped_for_authentication:
                print(
                    "Status: stopped because LinkedIn "
                    "requested authentication."
                )

    except KeyboardInterrupt:
        print()
        print(
            "Extraction interrupted by the user."
        )
        print(
            "Any records already written remain saved."
        )

    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                # Avoid secondary shutdown errors masking the
                # original exception or Ctrl+C.
                pass


if __name__ == "__main__":
    main()
