from __future__ import annotations

import argparse
import os
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import requests


SERPER_ENDPOINT = "https://google.serper.dev/search"


def load_queries(path: Path) -> list[tuple[str, str, str]]:
    """
    Read queries from a tab-separated file.

    Expected format:
        failure_mode<TAB>symptom<TAB>query

    Plain query-only lines are also supported.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Query file not found: {path.resolve()}"
        )

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
            rows.append(
                (
                    "uncategorized",
                    f"line_{line_number}",
                    line,
                )
            )

    return rows


def normalize_linkedin_url(url: str) -> str | None:
    """
    Normalize and validate a LinkedIn post URL.

    Accepts URLs such as:
        https://www.linkedin.com/posts/...
        https://linkedin.com/posts/...

    Query parameters and fragments are removed.
    """
    if not url:
        return None

    url = url.strip()

    try:
        parsed = urlparse(url)
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


def extract_linkedin_results(
    response_data: dict,
    top_n: int,
) -> list[dict]:
    """
    Extract LinkedIn post results from Serper's organic results.

    Returns dictionaries containing:
        position
        title
        url
        snippet
    """
    results: list[dict] = []
    seen_urls: set[str] = set()

    organic_results = response_data.get("organic", [])

    if not isinstance(organic_results, list):
        return results

    for item in organic_results:
        if not isinstance(item, dict):
            continue

        raw_url = str(item.get("link", "")).strip()
        normalized_url = normalize_linkedin_url(raw_url)

        if not normalized_url:
            continue

        if normalized_url in seen_urls:
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
            }
        )

        if len(results) >= top_n:
            break

    return results


def search_serper(
    query: str,
    api_key: str,
    num_results: int,
    timeout_seconds: int,
    country: str,
    language: str,
) -> dict:
    """
    Submit one search query to Serper and return parsed JSON.
    """
    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json",
    }

    payload = {
        "q": query,
        "num": num_results,
        "gl": country,
        "hl": language,
    }

    response = requests.post(
        SERPER_ENDPOINT,
        headers=headers,
        json=payload,
        timeout=timeout_seconds,
    )

    if response.status_code == 401:
        raise RuntimeError(
            "Serper rejected the API key. Check SERPER_API_KEY."
        )

    if response.status_code == 403:
        raise RuntimeError(
            "Serper denied the request. Check your account, "
            "API key, and available credits."
        )

    if response.status_code == 429:
        raise RuntimeError(
            "Serper rate limit reached. Increase --delay or retry later."
        )

    response.raise_for_status()

    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(
            "Serper returned a response that was not valid JSON."
        ) from exc

    if not isinstance(data, dict):
        raise RuntimeError(
            "Serper returned an unexpected response format."
        )

    return data


def run(
    queries_path: Path,
    output_dir: Path,
    api_key: str,
    top_n: int,
    request_num: int,
    delay_seconds: float,
    timeout_seconds: int,
    country: str,
    language: str,
    include_metadata: bool,
) -> Path:
    queries = load_queries(queries_path)

    if not queries:
        raise ValueError(
            f"No queries found in {queries_path.resolve()}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"urls_{timestamp}.txt"

    with output_path.open(
        "w",
        encoding="utf-8",
    ) as output:
        for index, (failure_mode, symptom, query) in enumerate(
            queries,
            start=1,
        ):
            started_at = datetime.now().isoformat(
                timespec="seconds"
            )

            print(
                f"[{index}/{len(queries)}] "
                f"{failure_mode} / {symptom}"
            )
            print(f"  {query}")

            output.write(
                f"QUERY\t{failure_mode}\t{symptom}\t"
                f"{query}\t{started_at}\n"
            )

            try:
                response_data = search_serper(
                    query=query,
                    api_key=api_key,
                    num_results=request_num,
                    timeout_seconds=timeout_seconds,
                    country=country,
                    language=language,
                )

                results = extract_linkedin_results(
                    response_data=response_data,
                    top_n=top_n,
                )

                if not results:
                    output.write("NO_RESULTS\n")
                    print(
                        "  No matching LinkedIn post URLs found"
                    )
                else:
                    for rank, result in enumerate(
                        results,
                        start=1,
                    ):
                        output.write(
                            f"{rank}\t{result['url']}\n"
                        )

                        if include_metadata:
                            if result["title"]:
                                output.write(
                                    f"TITLE\t{result['title']}\n"
                                )

                            if result["snippet"]:
                                output.write(
                                    f"SNIPPET\t{result['snippet']}\n"
                                )

                    print(
                        f"  Found {len(results)} LinkedIn URL(s)"
                    )

            except requests.Timeout:
                message = (
                    f"Request timed out after "
                    f"{timeout_seconds} seconds"
                )
                output.write(f"ERROR\tTimeoutError: {message}\n")
                print(f"  Timeout: {message}")

            except requests.ConnectionError as exc:
                output.write(
                    f"ERROR\tConnectionError: {exc}\n"
                )
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
                    f"ERROR\tHTTPError {status_code}: "
                    f"{response_text}\n"
                )

                print(
                    f"  HTTP error {status_code}: "
                    f"{response_text}"
                )

            except Exception as exc:
                output.write(
                    f"ERROR\t{type(exc).__name__}: {exc}\n"
                )
                print(
                    f"  Error: {type(exc).__name__}: {exc}"
                )

            output.write("\n")
            output.flush()

            if index < len(queries):
                time.sleep(delay_seconds)

    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Search for LinkedIn post URLs using the "
            "Serper API and save them to a timestamped file."
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
        help=(
            "Maximum number of LinkedIn post URLs "
            "to save per query."
        ),
    )

    parser.add_argument(
        "--request-num",
        type=int,
        default=20,
        help=(
            "Number of organic search results to request "
            "from Serper per query."
        ),
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Seconds to wait between API requests.",
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="HTTP timeout in seconds.",
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
        help=(
            "Write result titles and snippets below each URL."
        ),
    )

    args = parser.parse_args()

    if args.top_n < 1:
        raise ValueError("--top-n must be at least 1")

    if args.request_num < 1:
        raise ValueError("--request-num must be at least 1")

    if args.delay < 0:
        raise ValueError("--delay cannot be negative")

    if args.timeout < 1:
        raise ValueError("--timeout must be at least 1")

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
        timeout_seconds=args.timeout,
        country=args.country,
        language=args.language,
        include_metadata=args.include_metadata,
    )

    print()
    print(f"Saved results to: {output_path.resolve()}")


if __name__ == "__main__":
    main()
