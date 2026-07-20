# LinkedIn Search Query MVP

This first version does only two things:

1. Builds `queries.txt` from `query_db.yaml`.
2. Opens a browser, runs each query, and writes up to 10 LinkedIn post URLs to a timestamped file.

## Windows / VS Code setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
```

## Build the queries

```powershell
python build_queries.py
```

Each line in `queries.txt` has:

```text
failure_mode<TAB>symptom<TAB>browser_query
```

## Run the searches visibly

Bing is the default because its result markup is generally simpler:

```powershell
python collect_urls.py --headed
```

The result is written to:

```text
output\urls_YYYYMMDD_HHMMSS.txt
```

## Use Google instead

```powershell
python collect_urls.py --engine google --headed
```

## Test with only a few queries

Open `queries.txt`, retain 2–3 lines, and run:

```powershell
python collect_urls.py --headed --delay 5
```

Search engines may show consent pages, rate limits, or CAPTCHAs. The script does not attempt to bypass them. Use modest batches and delays.
