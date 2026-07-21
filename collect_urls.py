from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from playwright.async_api import (
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)


def load_queries(path: Path) -> list[tuple[str, str, str]]:
    """
    Read queries from a tab-separated file.

    Expected format:
        failure_mode<TAB>symptom<TAB>query

    Plain query-only lines are also supported.
    """
    if not path.exists():
        raise FileNotFoundError(f"Query file not found: {path.resolve()}")

    rows: list[tuple[str, str, str]] = []

    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        parts = line.split("\t", maxsplit=2)

        if len(parts) == 3:
            group, symptom, query = parts
            rows.append(
                (
                    group.strip(),
                    symptom.strip(),
                    query.strip(),
                )
            )
        else:
            rows.append(
                (
                    "uncategorized",
                    f"line_{line_number}",
                    line,
                )
            )

    return rows


def clean_linkedin_url(url: str) -> str | None:
    """
    Return a normalized public LinkedIn post URL.

    Only URLs containing /posts/ are retained.
    Tracking query parameters and fragments are removed.
    """
    if not url:
        return None

    url = unquote(url).strip()

    if not url.startswith("http"):
        return None

    parsed = urlparse(url)

    if "linkedin.com" not in parsed.netloc.lower():
        return None

    if "/posts/" not in parsed.path:
        return None

    return (
        f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    ).rstrip("/")


def decode_possible_base64_url(value: str) -> str | None:
    """
    Bing sometimes stores its destination URL in a base64-like `u` parameter.

    The encoded value may begin with `a1`.
    """
    if not value:
        return None

    candidate = value

    if candidate.startswith("a1"):
        candidate = candidate[2:]

    padding = "=" * (-len(candidate) % 4)

    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            decoded = decoder(candidate + padding).decode(
                "utf-8",
                errors="ignore",
            )

            cleaned = clean_linkedin_url(decoded)

            if cleaned:
                return cleaned

        except Exception:
            continue

    return None


def decode_bing_result_url(href: str) -> str | None:
    """
    Convert a Bing result URL into a clean LinkedIn post URL.

    Handles:
    - direct LinkedIn links
    - Bing tracking URLs
    - base64-encoded Bing destination URLs
    """
    if not href:
        return None

    href = unquote(href).strip()

    direct = clean_linkedin_url(href)

    if direct:
        return direct

    parsed = urlparse(href)

    if "bing.com" not in parsed.netloc.lower():
        return None

    params = parse_qs(parsed.query)

    for key in ("u", "url", "q", "r"):
        values = params.get(key, [])

        for value in values:
            direct = clean_linkedin_url(value)

            if direct:
                return direct

            decoded = decode_possible_base64_url(value)

            if decoded:
                return decoded

    return None


def decode_google_result_url(href: str) -> str | None:
    """
    Convert a Google result URL into a clean LinkedIn post URL.

    Handles:
    - direct LinkedIn links
    - /url?q=<destination> links
    """
    if not href:
        return None

    href = unquote(href).strip()

    direct = clean_linkedin_url(href)

    if direct:
        return direct

    parsed = urlparse(href)

    if href.startswith("/url?") or "google." in parsed.netloc.lower():
        params = parse_qs(parsed.query)

        for key in ("q", "url"):
            values = params.get(key, [])

            for value in values:
                direct = clean_linkedin_url(value)

                if direct:
                    return direct

    return None


async def maybe_handle_google_consent(page: Page) -> None:
    """
    Attempt to dismiss common Google consent prompts.

    This does not bypass CAPTCHAs or access controls.
    """
    labels = (
        "Accept all",
        "I agree",
        "Reject all",
        "Accept",
        "Agree",
    )

    for label in labels:
        button = page.get_by_role("button", name=label)

        try:
            if await button.count() > 0:
                await button.first.click(timeout=2000)
                await page.wait_for_timeout(1000)
                return
        except Exception:
            continue


async def search_bing(
    page: Page,
    query: str,
    top_n: int,
) -> list[str]:
    """
    Search Bing and return up to top_n LinkedIn post URLs.
    """
    search_url = (
        "https://www.bing.com/search"
        f"?q={quote_plus(query)}"
        "&count=30"
        "&setlang=en-us"
    )

    await page.goto(
        search_url,
        wait_until="domcontentloaded",
        timeout=60000,
    )

    await page.wait_for_timeout(2000)

    selector = "li.b_algo h2 a"

    try:
        await page.wait_for_selector(
            selector,
            timeout=20000,
        )
    except PlaywrightTimeoutError:
        return []

    links = page.locator(selector)
    link_count = await links.count()

    urls: list[str] = []

    for index in range(link_count):
        try:
            href = await links.nth(index).get_attribute("href")
        except Exception:
            continue

        normalized = decode_bing_result_url(href or "")

        if normalized and normalized not in urls:
            urls.append(normalized)

        if len(urls) >= top_n:
            break

    return urls


async def search_google(
    page: Page,
    query: str,
    top_n: int,
) -> list[str]:
    """
    Search Google and return up to top_n LinkedIn post URLs.
    """
    await page.goto(
        "https://www.google.com",
        wait_until="domcontentloaded",
        timeout=60000,
    )

    await maybe_handle_google_consent(page)

    search_url = (
        "https://www.google.com/search"
        f"?q={quote_plus(query)}"
        "&num=20"
        "&hl=en"
    )

    await page.goto(
        search_url,
        wait_until="domcontentloaded",
        timeout=60000,
    )

    await page.wait_for_timeout(2000)

    selectors = [
        "div#search a",
        'a[href^="http"]',
        'a[href^="/url?"]',
    ]

    hrefs: list[str] = []

    for selector in selectors:
        locator = page.locator(selector)

        try:
            count = await locator.count()
        except Exception:
            continue

        for index in range(count):
            try:
                href = await locator.nth(index).get_attribute("href")
            except Exception:
                continue

            if href:
                hrefs.append(href)

    urls: list[str] = []

    for href in hrefs:
        normalized = decode_google_result_url(href)

        if normalized and normalized not in urls:
            urls.append(normalized)

        if len(urls) >= top_n:
            break

    return urls


async def replace_page_if_closed(
    context: BrowserContext,
    page: Page,
) -> Page:
    """
    Create a fresh page if the current one was unexpectedly closed.
    """
    if page.is_closed():
        new_page = await context.new_page()
        new_page.set_default_timeout(30000)
        new_page.set_default_navigation_timeout(60000)
        return new_page

    return page


async def run(
    queries_path: Path,
    output_dir: Path,
    engine: str,
    top_n: int,
    headless: bool,
    delay_seconds: float,
) -> Path:
    queries = load_queries(queries_path)

    if not queries:
        raise ValueError(f"No queries found in {queries_path.resolve()}")

    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"urls_{timestamp}.txt"

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=headless,
        )

        context = await browser.new_context(
            viewport={
                "width": 1400,
                "height": 900,
            },
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/124.0.0.0 "
                "Safari/537.36"
            ),
        )

        page = await context.new_page()
        page.set_default_timeout(30000)
        page.set_default_navigation_timeout(60000)

        with output_path.open(
            "w",
            encoding="utf-8",
        ) as output:
            for index, (group, symptom, query) in enumerate(
                queries,
                start=1,
            ):
                print(
                    f"[{index}/{len(queries)}] "
                    f"{group} / {symptom}"
                )
                print(f"  {query}")

                page = await replace_page_if_closed(
                    context,
                    page,
                )

                started_at = datetime.now().isoformat(
                    timespec="seconds"
                )

                output.write(
                    f"QUERY\t{group}\t{symptom}\t"
                    f"{query}\t{started_at}\n"
                )

                try:
                    if engine == "google":
                        urls = await search_google(
                            page,
                            query,
                            top_n,
                        )
                    else:
                        urls = await search_bing(
                            page,
                            query,
                            top_n,
                        )

                    if urls:
                        for rank, url in enumerate(
                            urls,
                            start=1,
                        ):
                            output.write(
                                f"{rank}\t{url}\n"
                            )

                        print(
                            f"  Found {len(urls)} URL(s)"
                        )
                    else:
                        output.write("NO_RESULTS\n")
                        print("  No matching LinkedIn post URLs found")

                except PlaywrightTimeoutError as exc:
                    output.write(
                        f"ERROR\tTimeoutError: {exc}\n"
                    )
                    print(f"  Timeout: {exc}")

                    try:
                        await page.close()
                    except Exception:
                        pass

                    page = await context.new_page()
                    page.set_default_timeout(30000)
                    page.set_default_navigation_timeout(60000)

                except Exception as exc:
                    output.write(
                        f"ERROR\t{type(exc).__name__}: {exc}\n"
                    )
                    print(
                        f"  Error: {type(exc).__name__}: {exc}"
                    )

                    if (
                        "Execution context was destroyed"
                        in str(exc)
                    ):
                        try:
                            await page.wait_for_load_state(
                                "domcontentloaded",
                                timeout=10000,
                            )
                        except Exception:
                            pass

                output.write("\n")
                output.flush()

                await asyncio.sleep(delay_seconds)

        await context.close()
        await browser.close()

    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run browser searches and save the top "
            "LinkedIn post URLs to a timestamped file."
        )
    )

    parser.add_argument(
        "--queries",
        default="queries.txt",
        help="Path to the query input file.",
    )

    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory for timestamped output files.",
    )

    parser.add_argument(
        "--engine",
        choices=["bing", "google"],
        default="bing",
        help="Search engine to use.",
    )

    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="Maximum number of LinkedIn URLs per query.",
    )

    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser while searches run.",
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=5.0,
        help="Seconds to wait between searches.",
    )

    args = parser.parse_args()

    if args.top_n < 1:
        raise ValueError("--top-n must be at least 1")

    if args.delay < 0:
        raise ValueError("--delay cannot be negative")

    output_path = asyncio.run(
        run(
            queries_path=Path(args.queries),
            output_dir=Path(args.output_dir),
            engine=args.engine,
            top_n=args.top_n,
            headless=not args.headed,
            delay_seconds=args.delay,
        )
    )

    print()
    print(f"Saved results to: {output_path.resolve()}")


if __name__ == "__main__":
    main()
