from __future__ import annotations

import argparse
import hashlib
import json
import os
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
    """
    Minimal LinkedIn post extraction record.
    """

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
    """
    Return the current UTC timestamp in ISO-8601 format.
    """

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_text(value: str | None) -> str | None:
    """
    Collapse repeated whitespace and convert blank strings to None.
    """

    if value is None:
        return None

    cleaned = re.sub(r"\s+", " ", value).strip()

    return cleaned or None


def normalize_url(url: str) -> str | None:
    """
    Find and normalize a LinkedIn post URL.

    Query parameters and fragments are removed.
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
    Create a stable identifier for a LinkedIn post.
    """

    activity_match = ACTIVITY_ID_RE.search(url)

    if activity_match:
        return f"linkedin_{activity_match.group(1)}"

    url_hash = hashlib.sha1(
        url.encode("utf-8")
    ).hexdigest()[:16]

    return f"linkedin_{url_hash}"


def find_latest_run(runs_dir: Path) -> Path:
    """
    Find the latest timestamped folder under runs/.

    A folder is valid only when:

    1. Its name matches YYYYMMDD_HHMMSS.
    2. It contains urls.txt.
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
            "No timestamped collection folder containing urls.txt "
            f"was found under {runs_dir.resolve()}"
        )

    return max(
        candidates,
        key=lambda path: path.name,
    )


def load_urls(urls_path: Path) -> list[str]:
    """
    Read and deduplicate LinkedIn post URLs from urls.txt.

    The original order is preserved.
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

        # Limited support for tab-separated source files.
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
    """
    Return text from the first selector with a non-empty value.
    """

    for selector in selectors:
        try:
            locator = page.locator(selector).first

            if locator.count() == 0:
                continue

            value = clean_text(
                locator.inner_text(timeout=1800)
            )

            if value:
                return value

        except Exception:
            continue

    return None


def first_attribute(
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
    Create a persistent Chromium context.

    Cookies and session data are retained in profile_dir.
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
    )


def login_wall_detected(page: Page) -> bool:
    """
    Detect common LinkedIn login and authentication pages.
    """

    current_url = page.url.lower()

    blocked_url_tokens = (
        "/login",
        "/checkpoint/",
        "/authwall",
        "/uas/login",
    )

    if any(
        token in current_url
        for token in blocked_url_tokens
    ):
        return True

    try:
        body_text = page.locator("body").inner_text(
            timeout=3000
        ).lower()

    except Exception:
        return False

    return (
        "sign in" in body_text
        and "join now" in body_text
    )


def linkedin_session_is_authenticated(page: Page) -> bool:
    """
    Determine whether LinkedIn considers the current session authenticated.
    """

    current_url = page.url.lower()

    if any(
        token in current_url
        for token in (
            "/login",
            "/checkpoint/",
            "/authwall",
            "/uas/login",
        )
    ):
        return False

    authenticated_selectors = [
        "nav.global-nav",
        "header.global-nav",
        "a[href*='/feed/']",
        "a[href*='/mynetwork/']",
        "a[href*='/in/']",
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


def wait_for_manual_verification(
    page: Page,
    timeout_ms: int,
) -> None:
    """
    Pause when LinkedIn requests MFA, CAPTCHA, or identity verification.

    The script does not attempt to bypass LinkedIn security controls.
    """

    print()
    print(
        "LinkedIn requested additional verification, MFA, "
        "or a security check."
    )
    print(
        "Complete the verification in the opened browser window."
    )

    input(
        "Press Enter after the LinkedIn verification is complete..."
    )

    try:
        page.goto(
            "https://www.linkedin.com/feed/",
            wait_until="domcontentloaded",
            timeout=timeout_ms,
        )

        page.wait_for_timeout(2000)

    except PlaywrightTimeoutError:
        pass

    if not linkedin_session_is_authenticated(page):
        raise RuntimeError(
            "LinkedIn authentication is still incomplete after "
            "manual verification."
        )


def login_to_linkedin(
    page: Page,
    timeout_ms: int,
) -> None:
    """
    Authenticate with LinkedIn before reading or mining any URLs.

    Credentials are loaded from:

        LINKEDIN_USERNAME
        LINKEDIN_PASSWORD

    LINKEDIN_EMAIL is also accepted as a fallback for the username.
    """

    username = (
        os.getenv("LINKEDIN_USERNAME")
        or os.getenv("LINKEDIN_EMAIL")
    )

    password = os.getenv("LINKEDIN_PASSWORD")

    if not username:
        raise RuntimeError(
            "Missing LinkedIn username. Set the "
            "LINKEDIN_USERNAME environment variable."
        )

    if not password:
        raise RuntimeError(
            "Missing LinkedIn password. Set the "
            "LINKEDIN_PASSWORD environment variable."
        )

    print("Step 1: Authenticating with LinkedIn...")

    # Check whether the persistent browser profile already has
    # an authenticated LinkedIn session.
    try:
        page.goto(
            "https://www.linkedin.com/feed/",
            wait_until="domcontentloaded",
            timeout=timeout_ms,
        )

        page.wait_for_timeout(2000)

    except PlaywrightTimeoutError:
        pass

    if linkedin_session_is_authenticated(page):
        print(
            "LinkedIn session is already authenticated. "
            "No new login was required."
        )
        return

    print("No active LinkedIn session found. Signing in...")

    page.goto(
        "https://www.linkedin.com/login",
        wait_until="domcontentloaded",
        timeout=timeout_ms,
    )

    username_input = page.locator(
        "input#username, input[name='session_key']"
    ).first

    password_input = page.locator(
        "input#password, input[name='session_password']"
    ).first

    submit_button = page.locator(
        "button[type='submit']"
    ).first

    username_input.wait_for(
        state="visible",
        timeout=timeout_ms,
    )

    password_input.wait_for(
        state="visible",
        timeout=timeout_ms,
    )

    username_input.fill(username)
    password_input.fill(password)

    submit_button.click()

    try:
        page.wait_for_load_state(
            "domcontentloaded",
            timeout=timeout_ms,
        )
    except PlaywrightTimeoutError:
        pass

    page.wait_for_timeout(2500)

    current_url = page.url.lower()

    if "/checkpoint/" in current_url:
        wait_for_manual_verification(
            page,
            timeout_ms,
        )
        print("LinkedIn authentication completed.")
        return

    if linkedin_session_is_authenticated(page):
        print("LinkedIn authentication completed.")
        return

    # LinkedIn may show an inline credential error rather than navigating.
    credential_error = first_text(
        page,
        [
            "#error-for-username",
            "#error-for-password",
            ".alert-content",
            ".form__label--error",
            "[role='alert']",
        ],
    )

    if credential_error:
        raise RuntimeError(
            f"LinkedIn login failed: {credential_error}"
        )

    raise RuntimeError(
        "LinkedIn login did not complete. Check the credentials "
        "or complete any security prompt shown in the browser."
    )


def expand_post(page: Page) -> None:
    """
    Expand truncated LinkedIn post text when possible.
    """

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
    """
    Create a minimal record for a failed extraction.
    """

    return PostRecord(
        post_id=stable_post_id(url),
        url=url,
        author=None,
        published_date=None,
        post_text=None,
        source_collection_run_id=source_collection_run_id,
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
    Visit one LinkedIn post and extract essential content.
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
                source_collection_run_id=source_collection_run_id,
                extracted_at=extracted_at,
                status="login_required",
                error=(
                    "LinkedIn returned a login or authentication wall "
                    "after the initial authentication step."
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
                "a[href*='/in/'] span[aria-hidden='true']",
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

        post_text = first_text(
            page,
            [
                ".update-components-text",
                ".feed-shared-update-v2__description",
                ".feed-shared-inline-show-more-text",
                "article div[dir='ltr']",
            ],
        )

        if post_text:
            extraction_status = "success"
            extraction_error = None

        else:
            extraction_status = "partial"
            extraction_error = (
                "The page loaded, but no post text matched "
                "the configured LinkedIn selectors."
            )

        return PostRecord(
            post_id=stable_post_id(url),
            url=url,
            author=author,
            published_date=published_date,
            post_text=post_text,
            source_collection_run_id=source_collection_run_id,
            extracted_at=extracted_at,
            extraction_status=extraction_status,
            extraction_error=extraction_error,
        )

    except PlaywrightTimeoutError as exc:
        return create_failed_record(
            url=url,
            source_collection_run_id=source_collection_run_id,
            extracted_at=extracted_at,
            status="timeout",
            error=str(exc),
        )

    except Exception as exc:
        return create_failed_record(
            url=url,
            source_collection_run_id=source_collection_run_id,
            extracted_at=extracted_at,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Authenticate with LinkedIn first, read urls.txt from the "
            "latest runs/<timestamp>/ folder, extract essential post "
            "content, and write a new timestamped JSONL file."
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
            "Directory where extraction output is written. "
            "Default: data"
        ),
    )

    parser.add_argument(
        "--profile-dir",
        default=".linkedin-browser-profile",
        help=(
            "Persistent Chromium profile used to retain "
            "the LinkedIn session."
        ),
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=4.0,
        help="Seconds to wait between post visits.",
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=45,
        help="Navigation and login timeout in seconds.",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help=(
            "Process only the first N URLs. "
            "Use 0 to process all URLs."
        ),
    )

    parser.add_argument(
        "--headless",
        action="store_true",
        help=(
            "Run Chromium without displaying the browser. "
            "Do not use this for the first login or when MFA may occur."
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

    with sync_playwright() as playwright:
        context = create_browser_context(
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

            timeout_ms = args.timeout * 1000

            # =========================================================
            # STEP 1: LOGIN FIRST
            #
            # No run folder or urls.txt file is read until this succeeds.
            # =========================================================

            login_to_linkedin(
                page,
                timeout_ms=timeout_ms,
            )

            # =========================================================
            # STEP 2: FIND THE LATEST COLLECTION RUN
            # =========================================================

            print("Step 2: Locating the latest collection run...")

            latest_run = find_latest_run(runs_dir)

            # Reads:
            # runs/<latest_timestamp>/urls.txt
            urls_path = latest_run / "urls.txt"

            # =========================================================
            # STEP 3: LOAD AND DEDUPLICATE URLS
            # =========================================================

            print("Step 3: Loading and deduplicating post URLs...")

            urls = load_urls(urls_path)

            if args.limit > 0:
                urls = urls[:args.limit]

            # =========================================================
            # STEP 4: CREATE A NEW EXTRACTION OUTPUT
            # =========================================================

            extraction_timestamp = datetime.now(
                timezone.utc
            ).strftime("%Y%m%d_%H%M%S")

            data_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            output_path = (
                data_dir
                / f"extracted_posts_{extraction_timestamp}.jsonl"
            )

            print()
            print(f"Collection run: {latest_run.name}")
            print(f"Input file: {urls_path.resolve()}")
            print(f"URLs to process: {len(urls)}")
            print(f"Output file: {output_path.resolve()}")
            print()

            success_count = 0
            partial_count = 0
            failed_count = 0

            # "w" creates a fresh output file for each execution.
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
                        source_collection_run_id=latest_run.name,
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
