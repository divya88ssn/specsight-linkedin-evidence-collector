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

    LinkedIn page-level fields remain null until a later extraction stage.
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
    runs_dir: Path,
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
) -> tuple[Path, Path, Path, int, int]:
    """Run one immutable collection and write all outputs to one run folder."""
    queries = load_queries(queries_path)

    if not queries:
        raise ValueError(f"No queries found in {queries_path.resolve()}")

    collection_run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = runs_dir / collection_run_id

    # Avoid a rare collision if two runs start in the same second.
    suffix = 1
    while run_dir.exists():
        run_dir = runs_dir / f"{collection_run_id}_{suffix:02d}"
        suffix += 1

    run_dir.mkdir(parents=True, exist_ok=False)

    urls_path = run_dir / "urls.txt"
    post_index_path = run_dir / "post_index.jsonl"

    # Deduplicate only within this run. Historical runs remain immutable.
    seen_discovery_ids: set[str] = set()
    records_written = 0
    results_examined = 0

    with (
        urls_path.open("w", encoding="utf-8") as urls_output,
        post_index_path.open("w", encoding="utf-8") as post_index,
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

            urls_output.write(
                f"QUERY\t{failure_mode}\t{symptom}\t{original_query}\t"
                f"{query_started_at}\n"
            )
            urls_output.write(f"SERPER_QUERY\t{api_query}\n")

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
                results_examined += len(results)

                if not results:
                    urls_output.write("NO_RESULTS\n")
                    print("  No matching LinkedIn post URLs found")
                else:
                    query_records_written = 0

                    for rank, result in enumerate(results, start=1):
                        urls_output.write(f"{rank}\t{result['url']}\n")

                        if include_metadata:
                            if result["title"]:
                                urls_output.write(f"TITLE\t{result['title']}\n")
                            if result["snippet"]:
                                urls_output.write(f"SNIPPET\t{result['snippet']}\n")
                            if result["date"]:
                                urls_output.write(f"DATE\t{result['date']}\n")

                        record = build_post_index_record(
                            result=result,
                            original_query=original_query,
                            api_query=api_query,
                            failure_mode=failure_mode,
                            symptom=symptom,
                            collection_run_id=run_dir.name,
                            query_started_at=query_started_at,
                            collected_at=utc_now_iso(),
                            country=country,
                            language=language,
                        )

                        discovery_id = record["discovery_id"]
                        if discovery_id in seen_discovery_ids:
                            continue

                        post_index.write(
                            json.dumps(record, ensure_ascii=False) + "\n"
                        )
                        seen_discovery_ids.add(discovery_id)
                        records_written += 1
                        query_records_written += 1

                    print(
                        f"  Found {len(results)} LinkedIn URL(s); "
                        f"wrote {query_records_written} JSONL record(s)"
                    )

            except requests.Timeout:
                message = (
                    f"Request timed out (connect={connect_timeout}s, "
                    f"read={read_timeout}s)"
                )
                urls_output.write(f"ERROR\tTimeoutError: {message}\n")
                print(f"  Timeout: {message}")

            except requests.ConnectionError as exc:
                urls_output.write(f"ERROR\tConnectionError: {exc}\n")
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
                urls_output.write(
                    f"ERROR\tHTTPError {status_code}: {response_text}\n"
                )
                print(f"  HTTP error {status_code}: {response_text}")

            except Exception as exc:
                urls_output.write(f"ERROR\t{type(exc).__name__}: {exc}\n")
                print(f"  Error: {type(exc).__name__}: {exc}")

            urls_output.write("\n")
            urls_output.flush()
            post_index.flush()

            if index < len(queries):
                time.sleep(delay_seconds)

    return run_dir, urls_path, post_index_path, records_written, results_examined


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read queries.txt, search Serper, and store each collection run "
            "under runs/<timestamp>/ with urls.txt and post_index.jsonl."
        )
    )

    parser.add_argument(
        "--queries",
        default="queries.txt",
        help="Path to the tab-separated query file.",
    )
    parser.add_argument(
        "--runs-dir",
        default="runs",
        help="Parent directory for timestamped collection-run folders.",
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
        help="Also write Serper titles, snippets, and dates to urls.txt.",
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

    run_dir, urls_path, post_index_path, records_written, results_examined = run(
        queries_path=Path(args.queries),
        runs_dir=Path(args.runs_dir),
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
    print("Collection run complete")
    print(f"Run directory:          {run_dir.resolve()}")
    print(f"URL results:            {urls_path.resolve()}")
    print(f"Post index:             {post_index_path.resolve()}")
    print(f"Serper results examined:{results_examined:>8}")
    print(f"JSONL records written:  {records_written:>8}")


if __name__ == "__main__":
    main()
