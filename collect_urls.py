from __future__ import annotations

import argparse
import asyncio
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from playwright.async_api import async_playwright, Page


def load_queries(path: Path) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []

    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split("\t", maxsplit=2)
        if len(parts) == 3:
            rows.append((parts[0], parts[1], parts[2]))
        else:
            rows.append(("uncategorized", f"line_{line_number}", line))

    return rows


def normalize_result_url(href: str) -> str | None:
    if not href:
        return None

    if href.startswith("/url?"):
        parsed = urlparse(href)
        target = parse_qs(parsed.query).get("q", [None])[0]
        href = target or href

    href = unquote(href)

    if not href.startswith("http"):
        return None

    parsed = urlparse(href)
    host = parsed.netloc.lower()

    if "linkedin.com" not in host:
        return None

    # Keep only LinkedIn post URLs.
    if "/posts/" not in parsed.path:
        return None

    clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    return clean.rstrip("/")


async def search_bing(page: Page, query: str, top_n: int) -> list[str]:
    await page.goto("https://www.bing.com", wait_until="domcontentloaded")
    box = page.locator('textarea[name="q"], input[name="q"]').first
    await box.fill(query)
    await box.press("Enter")
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(1800)

    hrefs = await page.locator("li.b_algo h2 a").evaluate_all(
        "(els) => els.map((el) => el.href)"
    )

    urls: list[str] = []
    for href in hrefs:
        normalized = normalize_result_url(href)
        if normalized and normalized not in urls:
            urls.append(normalized)
        if len(urls) >= top_n:
            break

    return urls


async def search_google(page: Page, query: str, top_n: int) -> list[str]:
    await page.goto("https://www.google.com", wait_until="domcontentloaded")

    # Consent screens vary by region. This safely ignores them when absent.
    for label in ["Accept all", "I agree", "Reject all"]:
        button = page.get_by_role("button", name=label)
        if await button.count():
            try:
                await button.first.click(timeout=1500)
                break
            except Exception:
                pass

    box = page.locator('textarea[name="q"], input[name="q"]').first
    await box.fill(query)
    await box.press("Enter")
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(1800)

    hrefs = await page.locator('a[href^="http"], a[href^="/url?"]').evaluate_all(
        "(els) => els.map((el) => el.getAttribute('href'))"
    )

    urls: list[str] = []
    for href in hrefs:
        normalized = normalize_result_url(href or "")
        if normalized and normalized not in urls:
            urls.append(normalized)
        if len(urls) >= top_n:
            break

    return urls


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
        raise ValueError(f"No queries found in {queries_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"urls_{timestamp}.txt"

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=headless)
        context = await browser.new_context(
            viewport={"width": 1400, "height": 900},
            locale="en-US",
        )
        page = await context.new_page()

        with output_path.open("w", encoding="utf-8") as output:
            for index, (group, symptom, query) in enumerate(queries, start=1):
                print(f"[{index}/{len(queries)}] {query}")

                try:
                    if engine == "google":
                        urls = await search_google(page, query, top_n)
                    else:
                        urls = await search_bing(page, query, top_n)

                    output.write(
                        f"QUERY\t{group}\t{symptom}\t{query}\t"
                        f"{datetime.now().isoformat(timespec='seconds')}\n"
                    )

                    if not urls:
                        output.write("NO_RESULTS\n")
                    else:
                        for rank, url in enumerate(urls, start=1):
                            output.write(f"{rank}\t{url}\n")

                    output.write("\n")
                    output.flush()

                except Exception as exc:
                    output.write(
                        f"QUERY\t{group}\t{symptom}\t{query}\t"
                        f"{datetime.now().isoformat(timespec='seconds')}\n"
                    )
                    output.write(f"ERROR\t{type(exc).__name__}: {exc}\n\n")
                    output.flush()

                await page.wait_for_timeout(int(delay_seconds * 1000))

        await context.close()
        await browser.close()

    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Open browser searches and save the top LinkedIn post URLs."
    )
    parser.add_argument("--queries", default="queries.txt")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--engine", choices=["bing", "google"], default="bing")
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser while searches run.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=3.0,
        help="Seconds to wait between searches.",
    )
    args = parser.parse_args()

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
    print(f"Saved results to {output_path.resolve()}")


if __name__ == "__main__":
    main()
