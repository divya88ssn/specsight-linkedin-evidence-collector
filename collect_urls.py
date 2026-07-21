from __future__ import annotations

import argparse
import os
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import requests


SERPER_ENDPOINT = "https://google.serper.dev/search"


def load_queries(path: Path) -> list[tuple[str, str, str]]:
    """Read queries from a tab-separated file.

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
            failure_mode, symptom, query = parts
            rows.append(
                (
                    failure_mode.strip(),
                    symptom.strip(),
                    query.strip(),
                )
            )
        else:
            rows.append(("uncategorized", f"line_{line_number}", line))

    return rows


def prepare_serper_query(query: str, remove_quotes: bool = True) -> str:
    """Prepare a query for Serper while keeping LinkedIn site targeting.

    Serper returned useful LinkedIn results when the site restriction was kept
    but exact-phrase quotation marks were removed. This function therefore:

    - normalizes site:www.linkedin.com/posts to site:linkedin.com/posts
    - adds site:linkedin.com/posts when no LinkedIn site restriction exists
    - removes straight and curly quotation marks by default
    - collapses repeated whitespace
    """
    cleaned = query.strip()

    cleaned = re.sub(
        r"site:(?:www\.)?linkedin\.com/posts/?",
        "site:linkedin.com/posts",
        cleaned,
        flags=re.IGNORECASE,
    )

    if not re.search(
        r"site:(?:www\.)?linkedin\.com(?:/posts)?/?",
        cleaned,
        flags=re.IGNORECASE,
    ):
        cleaned = f"site:linkedin.com/posts {cleaned}"

    if remove_quotes:
        cleaned = cleaned.translate(
            str.maketrans(
                {
                    '"': "",
                    "“": "",
                    "”": "",
                    "‘": "",
                    "’": "",
                }
            )
        )

    return " ".join(cleaned.split())


def normalize_linkedin_url(url: str) -> str | None:
    """Normalize and validate a LinkedIn post URL."""
    if not url:
        return None

    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None

    hostname = parsed.netloc.lower()

    if not (
        hostname == "linkedin.com"
        or hostname == "www.linkedin.com"
        or hostname.endswith(".linkedin.com")
    ):
        return None

    path = parsed.path.rstrip("/")

    if "/posts/" not in path:
        return None

    normalized = parsed._replace(
        scheme="https",
        netloc="www.linkedin.com",
        path=path,
        params="",
        query="",
        fragment="",
    )

    return urlunparse(normalized)


def extract_linkedin_results(response_data: dict, top_n: int) -> list[dict]:
    """Extract deduplicated LinkedIn post results from Serper organic results."""
    results: list[dict] = []
    seen_urls: set[str] = set()

    organic_results = response_data.get("organic", [])

    if not isinstance(organic_results, list):
        return results

    for item in organic_results:
        if not isinstance(item, dict):
            continue

        normalized_url = normalize_linkedin_url(str(item.get("link", "")))

        if not normalized_url or normalized_url in seen_urls:
            continue

        seen_urls.add(normalized_url)

        position = item.get("position")
        if not isinstance(position, int):
            position = len(results) + 1

        results.append(
            {
                "position": position,
                "title": str(item.get("title", "")).strip(),
                "url": normalized_url,
                "snippet": str(item.get("snippet", "")).strip(),
                "date": str(item.get("date", "")).strip(),
            }
        )

        if len(results) >= top_n:
            break

    return results


def search_serper(
    query: str,
    api_key: str,
    num_results: int,
    connect_timeout: int,
    read_timeout: int,
    country: str,
    language: str,
) -> dict:
    """Submit one search query to Serper and return parsed JSON."""
    response = requests.post(
        SERPER_ENDPOINT,
        headers={
            "X-API-KEY": api_key,
            "Content-Type": "application/json",
        },
        json={
            "q": query,
            "num": num_results,
            "gl": country,
            "hl": language,
        },
        timeout=(connect_timeout, read_timeout),
    )

    if response.status_code == 401:
        raise RuntimeError("Serper rejected the API key. Check SERPER_API_KEY.")

    if response.status_code == 403:
        raise RuntimeError(
            "Serper denied the request. Check your account, API key, and credits."
        )

    if response.status_code == 429:
        raise RuntimeError(
            "Serper rate limit reached. Increase --delay or retry later."
        )

    if response.status_code == 400:
        raise RuntimeError(f"Serper rejected the query: {response.text[:500]}")

    response.raise_for_status()

    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError("Serper returned invalid JSON.") from exc

    if not isinstance(data, dict):
        raise RuntimeError("Serper returned an unexpected response format.")

    return data


def run(
    queries_path: Path,
    output_dir: Path,
    api_key: str,
    top_n: int,
    request_num: int,
    delay_seconds: float,
    connect_timeout: int,
    read_timeout: int,
    country: str,
    language: str,
    include_metadata: bool,
    keep_quotes: bool,
) -> Path:
    queries = load_queries(queries_path)

    if not queries:
        raise ValueError(f"No queries found in {queries_path.resolve()}")

    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"urls_{timestamp}.txt"

    with output_path.open("w", encoding="utf-8") as output:
        for index, (failure_mode, symptom, original_query) in enumerate(
            queries,
            start=1,
        ):
            api_query = prepare_serper_query(
                original_query,
                remove_quotes=not keep_quotes,
            )
            started_at = datetime.now().isoformat(timespec="seconds")

            print(f"[{index}/{len(queries)}] {failure_mode} / {symptom}")
            print(f"  Original: {original_query}")
            print(f"  Serper:   {api_query}")

            output.write(
                f"QUERY\t{failure_mode}\t{symptom}\t{original_query}\t{started_at}\n"
            )
            output.write(f"SERPER_QUERY\t{api_query}\n")

            try:
                response_data = search_serper(
                    query=api_query,
                    api_key=api_key,
                    num_results=request_num,
                    connect_timeout=connect_timeout,
                    read_timeout=read_timeout,
                    country=country,
                    language=language,
                )

                results = extract_linkedin_results(response_data, top_n)

                if not results:
                    output.write("NO_RESULTS\n")
                    print("  No matching LinkedIn post URLs found")
                else:
                    for rank, result in enumerate(results, start=1):
                        output.write(f"{rank}\t{result['url']}\n")

                        if include_metadata:
                            if result["title"]:
                                output.write(f"TITLE\t{result['title']}\n")
                            if result["snippet"]:
                                output.write(f"SNIPPET\t{result['snippet']}\n")
                            if result["date"]:
                                output.write(f"DATE\t{result['date']}\n")

                    print(f"  Found {len(results)} LinkedIn URL(s)")

            except requests.Timeout:
                message = (
                    f"Request timed out (connect={connect_timeout}s, "
                    f"read={read_timeout}s)"
                )
                output.write(f"ERROR\tTimeoutError: {message}\n")
                print(f"  Timeout: {message}")

            except requests.ConnectionError as exc:
                output.write(f"ERROR\tConnectionError: {exc}\n")
                print(f"  Connection error: {exc}")

            except requests.HTTPError as exc:
                status_code = (
                    exc.response.status_code
                    if exc.response is not None
                    else "unknown"
                )
                response_text = (
                    exc.response.text[:500]
                    if exc.response is not None
                    else str(exc)
                )
                output.write(
                    f"ERROR\tHTTPError {status_code}: {response_text}\n"
                )
                print(f"  HTTP error {status_code}: {response_text}")

            except Exception as exc:
                output.write(f"ERROR\t{type(exc).__name__}: {exc}\n")
                print(f"  Error: {type(exc).__name__}: {exc}")

            output.write("\n")
            output.flush()

            if index < len(queries):
                time.sleep(delay_seconds)

    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read queries.txt, search Serper, and save LinkedIn post URLs "
            "to a timestamped output file."
        )
    )

    parser.add_argument(
        "--queries",
        default="queries.txt",
        help="Path to the tab-separated query file.",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory for timestamped output files.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="Maximum LinkedIn post URLs to save per query.",
    )
    parser.add_argument(
        "--request-num",
        type=int,
        default=20,
        help="Number of organic results requested from Serper per query.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Seconds to wait between API requests.",
    )
    parser.add_argument(
        "--connect-timeout",
        type=int,
        default=10,
        help="Connection timeout in seconds.",
    )
    parser.add_argument(
        "--read-timeout",
        type=int,
        default=30,
        help="Response-read timeout in seconds.",
    )
    parser.add_argument(
        "--country",
        default="us",
        help="Two-letter country code passed to Serper.",
    )
    parser.add_argument(
        "--language",
        default="en",
        help="Language code passed to Serper.",
    )
    parser.add_argument(
        "--include-metadata",
        action="store_true",
        help="Write titles, snippets, and dates below each URL.",
    )
    parser.add_argument(
        "--keep-quotes",
        action="store_true",
        help=(
            "Keep quotation marks from queries.txt. By default, quotation "
            "marks are removed because unquoted queries returned better results."
        ),
    )

    args = parser.parse_args()

    if args.top_n < 1:
        raise ValueError("--top-n must be at least 1")
    if args.request_num < 1:
        raise ValueError("--request-num must be at least 1")
    if args.delay < 0:
        raise ValueError("--delay cannot be negative")
    if args.connect_timeout < 1:
        raise ValueError("--connect-timeout must be at least 1")
    if args.read_timeout < 1:
        raise ValueError("--read-timeout must be at least 1")

    api_key = os.getenv("SERPER_API_KEY")

    if not api_key:
        raise RuntimeError(
            "SERPER_API_KEY is not set.\n\n"
            "In PowerShell, run:\n"
            '$env:SERPER_API_KEY="YOUR_KEY"\n\n'
            "Then run this script again."
        )

    output_path = run(
        queries_path=Path(args.queries),
        output_dir=Path(args.output_dir),
        api_key=api_key,
        top_n=args.top_n,
        request_num=args.request_num,
        delay_seconds=args.delay,
        connect_timeout=args.connect_timeout,
        read_timeout=args.read_timeout,
        country=args.country,
        language=args.language,
        include_metadata=args.include_metadata,
        keep_quotes=args.keep_quotes,
    )

    print()
    print(f"Saved results to: {output_path.resolve()}")


if __name__ == "__main__":
    main()
