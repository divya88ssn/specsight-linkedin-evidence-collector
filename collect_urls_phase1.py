from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import requests


SERPER_ENDPOINT = "https://google.serper.dev/search"


def utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def stable_hash(prefix: str, value: str, length: int = 20) -> str:
    """Return a deterministic identifier based on a SHA-256 digest."""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}_{digest}"


def create_post_id(url: str) -> str:
    """Create a stable post ID using LinkedIn's activity ID when available."""
    activity_match = re.search(r"activity-(\d+)", url, flags=re.IGNORECASE)

    if activity_match:
        return f"linkedin_{activity_match.group(1)}"

    return stable_hash("linkedin", url)


def create_discovery_id(
    url: str,
    source_query: str,
    failure_mode: str,
    symptom: str,
) -> str:
    """Create one stable ID per query-to-post discovery."""
    identity = "|".join(
        [
            url.strip(),
            source_query.strip(),
            failure_mode.strip(),
            symptom.strip(),
        ]
    )
    return stable_hash("discovery", identity)


def load_existing_discovery_ids(path: Path) -> set[str]:
    """Load discovery IDs already present in the JSONL index.

    Malformed lines are reported and skipped so one bad line does not make the
    whole collector unusable.
    """
    discovery_ids: set[str] = set()

    if not path.exists():
        return discovery_ids

    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()

        if not line:
            continue

        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            print(
                f"Warning: skipped malformed JSONL line {line_number} "
                f"in {path.resolve()}"
            )
            continue

        discovery_id = record.get("discovery_id")
        if isinstance(discovery_id, str) and discovery_id:
            discovery_ids.add(discovery_id)

    return discovery_ids


def build_post_index_record(
    *,
    result: dict,
    original_query: str,
    api_query: str,
    failure_mode: str,
    symptom: str,
    collection_run_id: str,
    query_started_at: str,
    collected_at: str,
    country: str,
    language: str,
) -> dict:
    """Build a Phase 1 query-to-post discovery record.

    Page-level LinkedIn fields remain null until extract_posts.py enriches them.
    """
    url = result["url"]

    return {
        "discovery_id": create_discovery_id(
            url=url,
            source_query=original_query,
            failure_mode=failure_mode,
            symptom=symptom,
        ),
        "post_id": create_post_id(url),
        "url": url,
        "source": "linkedin",
        "source_type": "serper_search_result",
        "headline": result.get("title") or None,
        "search_snippet": result.get("snippet") or None,
        "search_date": result.get("date") or None,
        "source_query": original_query,
        "serper_query": api_query,
        "failure_mode_seed": failure_mode,
        "symptom_seed": symptom,
        "search_position": result.get("position"),
        "country": country,
        "language": language,
        "query_started_at": query_started_at,
        "collected_at": collected_at,
        "collection_run_id": collection_run_id,
        "author": None,
        "company": None,
        "likes": None,
        "comments": None,
        "published_date": None,
        "post_text": None,
        "extraction_status": "pending",
        "classification_status": "pending",
    }


def run(
    queries_path: Path,
    output_dir: Path,
    post_index_path: Path,
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
) -> tuple[Path, Path, int]:
    queries = load_queries(queries_path)

    if not queries:
        raise ValueError(f"No queries found in {queries_path.resolve()}")

    output_dir.mkdir(parents=True, exist_ok=True)
    post_index_path.parent.mkdir(parents=True, exist_ok=True)

    collection_run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"urls_{collection_run_id}.txt"

    existing_discovery_ids = load_existing_discovery_ids(post_index_path)
    new_records_written = 0

    with (
        output_path.open("w", encoding="utf-8") as output,
        post_index_path.open("a", encoding="utf-8") as post_index,
    ):
        for index, (failure_mode, symptom, original_query) in enumerate(
            queries,
            start=1,
        ):
            api_query = prepare_serper_query(
                original_query,
                remove_quotes=not keep_quotes,
            )
            query_started_at = utc_now_iso()

            print(f"[{index}/{len(queries)}] {failure_mode} / {symptom}")
            print(f"  Original: {original_query}")
            print(f"  Serper:   {api_query}")

            output.write(
                f"QUERY\t{failure_mode}\t{symptom}\t{original_query}\t"
                f"{query_started_at}\n"
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
                    query_new_records = 0

                    for rank, result in enumerate(results, start=1):
                        output.write(f"{rank}\t{result['url']}\n")

                        if include_metadata:
                            if result["title"]:
                                output.write(f"TITLE\t{result['title']}\n")
                            if result["snippet"]:
                                output.write(f"SNIPPET\t{result['snippet']}\n")
                            if result["date"]:
                                output.write(f"DATE\t{result['date']}\n")

                        record = build_post_index_record(
                            result=result,
                            original_query=original_query,
                            api_query=api_query,
                            failure_mode=failure_mode,
                            symptom=symptom,
                            collection_run_id=collection_run_id,
                            query_started_at=query_started_at,
                            collected_at=utc_now_iso(),
                            country=country,
                            language=language,
                        )

                        discovery_id = record["discovery_id"]

                        if discovery_id in existing_discovery_ids:
                            continue

                        post_index.write(
                            json.dumps(record, ensure_ascii=False) + "\n"
                        )
                        post_index.flush()

                        existing_discovery_ids.add(discovery_id)
                        new_records_written += 1
                        query_new_records += 1

                    print(
                        f"  Found {len(results)} LinkedIn URL(s); "
                        f"added {query_new_records} new index record(s)"
                    )

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

    return output_path, post_index_path, new_records_written


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read queries.txt, search Serper, save LinkedIn post URLs to a "
            "timestamped text file, and append Phase 1 discovery records to "
            "data/post_index.jsonl."
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
        help="Directory for timestamped URL output files.",
    )
    parser.add_argument(
        "--post-index",
        default="data/post_index.jsonl",
        help="Path to the append-only Phase 1 JSONL evidence index.",
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
        default=10,
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
        help="Also write Serper titles, snippets, and dates to the URL text file.",
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

    output_path, post_index_path, new_records_written = run(
        queries_path=Path(args.queries),
        output_dir=Path(args.output_dir),
        post_index_path=Path(args.post_index),
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
    print(f"Saved URL results to: {output_path.resolve()}")
    print(f"Updated post index:    {post_index_path.resolve()}")
    print(f"New JSONL records:     {new_records_written}")


if __name__ == "__main__":
    main()
