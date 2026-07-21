# Specsight Evidence Collector: Environment Setup and Runbook

This guide sets up the full local pipeline from `query_db.yaml` through Serper URL collection, LinkedIn post extraction, evidence-index creation, and LLM capability mining.

The commands below assume PowerShell on Windows because that is the current development environment. Bash equivalents are also included.

---

# 1. Prerequisites

Install:

- Git
- Python 3.11 or newer
- Google Chrome or Chromium
- A Serper account and API key
- An OpenAI API key or another supported LLM provider for classification

Verify:

## PowerShell

```powershell
python --version
git --version
```

## Bash

```bash
python3 --version
git --version
```

---

# 2. Clone the Repository

## PowerShell

```powershell
git clone https://github.com/divya88ssn/specsight-linkedin-evidence-collector.git
cd .\specsight-linkedin-evidence-collector
```

## Bash

```bash
git clone https://github.com/divya88ssn/specsight-linkedin-evidence-collector.git
cd specsight-linkedin-evidence-collector
```

---

# 3. Create a Virtual Environment

## PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

## Bash

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Verify the active environment:

```powershell
python -c "import sys; print(sys.executable)"
```

or:

```bash
python -c 'import sys; print(sys.executable)'
```

---

# 4. Install Python Packages

## Minimal Packages

```text
requests
PyYAML
python-dotenv
playwright
pydantic
jsonschema
openai
```

Optional development packages:

```text
pytest
ruff
mypy
```

## PowerShell or Bash

```bash
python -m pip install --upgrade pip
pip install requests PyYAML python-dotenv playwright pydantic jsonschema openai pytest ruff mypy
```

Install the Playwright Chromium browser:

```bash
python -m playwright install chromium
```

Create `requirements.txt`:

```text
requests>=2.32.0
PyYAML>=6.0.1
python-dotenv>=1.0.1
playwright>=1.45.0
pydantic>=2.8.0
jsonschema>=4.23.0
openai>=1.40.0
pytest>=8.2.0
ruff>=0.5.0
mypy>=1.10.0
```

Install later with:

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

---

# 5. Configure Environment Variables

Create:

```text
.env
```

Example:

```dotenv
SERPER_API_KEY=replace_with_your_serper_key
OPENAI_API_KEY=replace_with_your_openai_key
OPENAI_MODEL=gpt-5-mini
SERPER_COUNTRY=us
SERPER_LANGUAGE=en
```

Create a safe template:

```text
.env.example
```

```dotenv
SERPER_API_KEY=
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5-mini
SERPER_COUNTRY=us
SERPER_LANGUAGE=en
```

Never commit `.env`.

## Temporary PowerShell Variables

```powershell
$env:SERPER_API_KEY="YOUR_SERPER_KEY"
$env:OPENAI_API_KEY="YOUR_OPENAI_KEY"
```

Verify:

```powershell
echo $env:SERPER_API_KEY
echo $env:OPENAI_API_KEY
```

## Temporary Bash Variables

```bash
export SERPER_API_KEY='YOUR_SERPER_KEY'
export OPENAI_API_KEY='YOUR_OPENAI_KEY'
```

Verify:

```bash
printf '%s\n' "$SERPER_API_KEY"
printf '%s\n' "$OPENAI_API_KEY"
```

---

# 6. Configure `.gitignore`

```gitignore
.venv/
.env
__pycache__/
*.pyc
.pytest_cache/
.mypy_cache/
.ruff_cache/

.linkedin-browser-profile/
playwright-report/
test-results/

data/post_content.jsonl
data/evidence_index.jsonl
data/capability_evidence.jsonl
data/failed_urls.jsonl

output/urls_*.txt
```

Decide whether `post_index.jsonl` and `discoveries.jsonl` should be versioned. For a reproducible research corpus, they may be committed. For a private operating dataset, ignore the entire `data/` directory.

---

# 7. Create Required Directories

## PowerShell

```powershell
New-Item -ItemType Directory -Force output | Out-Null
New-Item -ItemType Directory -Force data | Out-Null
New-Item -ItemType Directory -Force schemas | Out-Null
New-Item -ItemType Directory -Force tests | Out-Null
```

## Bash

```bash
mkdir -p output data schemas tests
```

---

# 8. Build `queries.txt`

## Input

```text
query_db.yaml
```

## Command

```bash
python build_queries.py
```

Recommended explicit form:

```bash
python build_queries.py --input query_db.yaml --output queries.txt
```

Validate the first rows.

## PowerShell

```powershell
Get-Content .\queries.txt -TotalCount 10
```

## Bash

```bash
head -n 10 queries.txt
```

Expected format:

```text
failure_mode<TAB>symptom<TAB>query
```

Example:

```text
late_validation	uat_discovery	site:linkedin.com/posts continuous validation acceptance criteria
```

---

# 9. Test Serper Independently

Use `test_serper.py` before running all queries.

Example request body:

```python
payload = {
    "q": "site:linkedin.com/posts continuous validation acceptance criteria",
    "num": 10,
    "gl": "us",
    "hl": "en",
}
```

Run:

```bash
python test_serper.py
```

Expected:

```text
200
{"searchParameters": ..., "organic": [...]}
```

If Serper returns:

```text
Query pattern not allowed for free accounts
```

check the following:

1. `num` should be `10`, not `20`.
2. Remove quoted exact phrases.
3. Test the query in `test_serper.py` separately.
4. Confirm available Serper credits.

---

# 10. Run URL Collection

## Small Test File

Create a five-query test set.

### PowerShell

```powershell
Get-Content .\queries.txt -TotalCount 5 | Set-Content .\queries_test.txt
```

### Bash

```bash
head -n 5 queries.txt > queries_test.txt
```

Run:

```bash
python collect_urls.py --queries queries_test.txt --request-num 10 --top-n 10 --delay 1 --include-metadata
```

## Full Run

```bash
python collect_urls.py --queries queries.txt --request-num 10 --top-n 10 --delay 1 --include-metadata
```

Expected outputs:

```text
output/urls_<timestamp>.txt
data/post_index.jsonl
data/discoveries.jsonl
```

## Useful Collector Flags

```text
--queries queries.txt
--output-dir output
--top-n 10
--request-num 10
--delay 1
--timeout 30
--country us
--language en
--include-metadata
```

---

# 11. Validate JSONL Output

Each line must be independently valid JSON.

## PowerShell

```powershell
Get-Content .\data\post_index.jsonl -TotalCount 3
Get-Content .\data\discoveries.jsonl -TotalCount 3
```

## Bash

```bash
head -n 3 data/post_index.jsonl
head -n 3 data/discoveries.jsonl
```

Validate with Python:

```bash
python -c "import json, pathlib; [json.loads(line) for line in pathlib.Path('data/post_index.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]; print('post_index.jsonl valid')"
```

```bash
python -c "import json, pathlib; [json.loads(line) for line in pathlib.Path('data/discoveries.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]; print('discoveries.jsonl valid')"
```

Optional utility installation:

```bash
pip install jsonlines
```

---

# 12. Create a Persistent LinkedIn Browser Session

The full-post extractor may need an authenticated browser profile.

Run:

```bash
python extract_posts.py --login-only
```

Expected flow:

1. Chromium opens.
2. Log into LinkedIn manually.
3. Confirm the session.
4. Close or continue when prompted.

The profile should be stored in:

```text
.linkedin-browser-profile/
```

Never commit this directory.

---

# 13. Test Full Post Extraction

Run only five pending posts:

```bash
python extract_posts.py --input data/post_index.jsonl --output data/post_content.jsonl --limit 5 --delay 4
```

Review:

## PowerShell

```powershell
Get-Content .\data\post_content.jsonl -TotalCount 5
```

## Bash

```bash
head -n 5 data/post_content.jsonl
```

Expected fields:

```text
post_id
url
author
headline
company
published_date
date_text
likes
comments
post_text
page_title
retrieved_at
extraction_status
extraction_error
```

---

# 14. Run Full Post Extraction

```bash
python extract_posts.py --input data/post_index.jsonl --output data/post_content.jsonl --delay 4
```

Recommended behavior:

- skip records already marked `success`,
- append new results,
- write failures to `data/failed_urls.jsonl`,
- pause between pages,
- preserve `null` for unavailable metadata.

To retry only failed records, support a command such as:

```bash
python extract_posts.py --retry-failed --delay 6
```

---

# 15. Build the Evidence Index

Join:

```text
post_index.jsonl
discoveries.jsonl
post_content.jsonl
```

Command:

```bash
python build_evidence_index.py \
  --posts data/post_index.jsonl \
  --discoveries data/discoveries.jsonl \
  --content data/post_content.jsonl \
  --output data/evidence_index.jsonl
```

PowerShell multiline form:

```powershell
python .\build_evidence_index.py `
  --posts .\data\post_index.jsonl `
  --discoveries .\data\discoveries.jsonl `
  --content .\data\post_content.jsonl `
  --output .\data\evidence_index.jsonl
```

Validate:

```bash
python -c "import json, pathlib; [json.loads(line) for line in pathlib.Path('data/evidence_index.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]; print('evidence_index.jsonl valid')"
```

---

# 16. Run LLM Capability Mining

Test on five records:

```bash
python classify_posts.py \
  --input data/evidence_index.jsonl \
  --output data/capability_evidence.jsonl \
  --limit 5
```

PowerShell:

```powershell
python .\classify_posts.py `
  --input .\data\evidence_index.jsonl `
  --output .\data\capability_evidence.jsonl `
  --limit 5
```

Run full classification:

```bash
python classify_posts.py \
  --input data/evidence_index.jsonl \
  --output data/capability_evidence.jsonl
```

Recommended classifier behavior:

- use structured model output,
- validate against JSON Schema,
- retry malformed responses,
- skip successful records,
- preserve raw model output on failure,
- leave deferred scores as `null`.

---

# 17. Run the Entire Pipeline

A future orchestration script can expose:

```bash
python pipeline.py --all
```

Until then, run each stage explicitly:

```bash
python build_queries.py --input query_db.yaml --output queries.txt
python collect_urls.py --queries queries.txt --request-num 10 --top-n 10 --delay 1 --include-metadata
python extract_posts.py --input data/post_index.jsonl --output data/post_content.jsonl --delay 4
python build_evidence_index.py --posts data/post_index.jsonl --discoveries data/discoveries.jsonl --content data/post_content.jsonl --output data/evidence_index.jsonl
python classify_posts.py --input data/evidence_index.jsonl --output data/capability_evidence.jsonl
```

PowerShell:

```powershell
python .\build_queries.py --input .\query_db.yaml --output .\queries.txt
python .\collect_urls.py --queries .\queries.txt --request-num 10 --top-n 10 --delay 1 --include-metadata
python .\extract_posts.py --input .\data\post_index.jsonl --output .\data\post_content.jsonl --delay 4
python .\build_evidence_index.py --posts .\data\post_index.jsonl --discoveries .\data\discoveries.jsonl --content .\data\post_content.jsonl --output .\data\evidence_index.jsonl
python .\classify_posts.py --input .\data\evidence_index.jsonl --output .\data\capability_evidence.jsonl
```

---

# 18. Quality and Development Commands

Run tests:

```bash
pytest -q
```

Lint:

```bash
ruff check .
```

Auto-format:

```bash
ruff format .
```

Type checking:

```bash
mypy build_queries.py collect_urls.py extract_posts.py build_evidence_index.py classify_posts.py
```

Compile check:

```bash
python -m compileall .
```

---

# 19. Git Workflow

Check status:

```bash
git status
```

Stage code and documentation:

```bash
git add query_db.yaml build_queries.py collect_urls.py extract_posts.py build_evidence_index.py classify_posts.py README.md SETUP_AND_RUN.md requirements.txt .env.example .gitignore
```

Commit:

```bash
git commit -m "Add evidence indexing and extraction pipeline"
```

Push:

```bash
git push origin main
```

Do not commit:

```text
.env
.venv/
.linkedin-browser-profile/
private or licensed scraped content
API keys
```

---

# 20. Common Errors

## Serper HTTP 400

Symptom:

```text
Query pattern not allowed for free accounts
```

Actions:

```text
Use --request-num 10
Remove quotes
Test one query with test_serper.py
Confirm credits and API-key status
```

## Serper HTTP 401

```text
Invalid or missing API key
```

Check:

```powershell
echo $env:SERPER_API_KEY
```

## Playwright Browser Missing

```text
Executable does not exist
```

Fix:

```bash
python -m playwright install chromium
```

## LinkedIn Login Page Returned

Run:

```bash
python extract_posts.py --login-only
```

Then rerun extraction.

## JSON Decode Error

Validate the exact line:

```bash
python -c "import json, pathlib; p=pathlib.Path('data/post_content.jsonl'); [(i, json.loads(x)) for i,x in enumerate(p.read_text(encoding='utf-8').splitlines(),1) if x.strip()]"
```

## Interrupted Run

The scripts should be resumable. Restart the same command. Successful post IDs should be skipped.

---

# 21. Recommended Next Implementation Order

1. Update `collect_urls.py` to write `post_index.jsonl` and `discoveries.jsonl`.
2. Confirm stable IDs and duplicate handling.
3. Test `extract_posts.py` against five posts.
4. Build `build_evidence_index.py`.
5. Define JSON schemas and controlled taxonomies.
6. Implement `classify_posts.py` with structured LLM output.
7. Expose `capability_evidence.jsonl` through the web app.

