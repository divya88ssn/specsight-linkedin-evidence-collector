# Specsight LinkedIn Evidence Collector

## Purpose

This project builds a structured evidence pipeline from public LinkedIn conversations related to Specsight's target value proposition.

The pipeline starts with a reusable pain-point query library, generates search queries, retrieves LinkedIn post URLs through Serper, creates a durable evidence index, extracts full post content, and finally uses an LLM to classify each post into workflow problems, missing capabilities, current solution patterns, and potential Specsight applicability.

The intended end product is an interactive web application where users can explore:

- current product-management conversation topics,
- workflow stages where failures occur,
- recurring failure modes,
- likely root causes and business impacts,
- missing or required product capabilities,
- current tools and solution patterns,
- and where Specsight may be especially useful.

---

## End-to-End Pipeline

```text
query_db.yaml
    ↓
build_queries.py
    ↓
queries.txt
    ↓
collect_urls.py
    ↓
Serper API / Google Search
    ↓
output/urls_<timestamp>.txt
    ├── data/post_index.jsonl
    └── data/discoveries.jsonl
            ↓
extract_posts.py
            ↓
data/post_content.jsonl
            ↓
classify_posts.py
            ↓
data/capability_evidence.jsonl
            ↓
API / database / static build
            ↓
Interactive Specsight evidence web app
```

---

# 1. Query Library

## File

```text
query_db.yaml
```

## Purpose

`query_db.yaml` is the source-of-truth library for pain areas, workflow stages, symptoms, practitioner language, and query variants.

It should describe the product-management problems Specsight is intended to help uncover or resolve.

Example:

```yaml
failure_modes:
  story_reopened:
    workflow_stage: qa_validation
    symptoms:
      reopened_after_qa:
        phrases:
          - story reopened after QA
          - story returned to development after QA
          - product owner rejected after QA
        capability_hypothesis:
          - acceptance criteria traceability
          - continuous validation
          - implementation to specification comparison

  late_validation:
    workflow_stage: delivery_validation
    symptoms:
      uat_discovery:
        phrases:
          - acceptance criteria validated too late
          - UAT found requirement gaps
          - continuous validation acceptance criteria
        capability_hypothesis:
          - continuous acceptance validation
          - shared product specification
```

## Design Principle

The query library should focus on language practitioners actually use in public discussions.

Prefer:

```text
acceptance criteria rework QA
continuous validation acceptance criteria
requirements misunderstood during development
```

Avoid overly rigid exact-phrase searches unless intentionally testing a known phrase:

```text
"continuous validation of acceptance criteria"
```

Quoted phrases can reduce recall substantially.

---

# 2. Query Generation

## File

```text
build_queries.py
```

## Purpose

`build_queries.py` parses `query_db.yaml` and creates the tab-separated `queries.txt` file consumed by the collector.

## Expected Output Format

```text
failure_mode<TAB>symptom<TAB>query
```

Example:

```text
story_reopened	reopened_after_qa	site:linkedin.com/posts story reopened after QA
story_reopened	reopened_after_qa	site:linkedin.com/posts product owner rejected after QA
late_validation	uat_discovery	site:linkedin.com/posts continuous validation acceptance criteria
```

Plain query-only lines may also be supported:

```text
site:linkedin.com/posts requirements drift product management
```

These should be assigned fallback metadata such as:

```text
failure_mode = uncategorized
symptom = line_<number>
```

## Responsibilities

`build_queries.py` should:

1. Load and validate `query_db.yaml`.
2. Iterate through failure modes and symptoms.
3. Convert phrase variants into unquoted Google searches.
4. Add `site:linkedin.com/posts` if not already present.
5. Deduplicate identical queries.
6. Write deterministic output to `queries.txt`.

---

# 3. URL Collection Through Serper

## File

```text
collect_urls.py
```

## Input

```text
queries.txt
```

## Purpose

The collector sends each query to Serper and preserves both the LinkedIn URL and the search-result metadata returned by Google.

Serper provides:

- result URL,
- title,
- snippet,
- relative date when available,
- Google result position,
- and search parameters.

Serper does not reliably provide:

- full post text,
- complete author metadata,
- complete engagement counts,
- comments,
- or company information.

Those fields are populated later by full post extraction.

## Query Normalization

Before sending a query to Serper, the collector should:

1. Remove quotation marks.
2. Collapse repeated whitespace.
3. Add `site:linkedin.com/posts` if missing.
4. Preserve the original query for traceability.

Example:

```text
Original query:
site:linkedin.com/posts "story reopened" "after QA"

Serper query:
site:linkedin.com/posts story reopened after QA
```

## Serper Request

Typical payload:

```json
{
  "q": "site:linkedin.com/posts continuous validation acceptance criteria",
  "num": 10,
  "gl": "us",
  "hl": "en"
}
```

For free-tier compatibility, start with:

```text
num = 10
```

## URL Filtering

Only preserve URLs whose normalized path contains:

```text
linkedin.com/posts/
```

Normalize URLs by:

- forcing HTTPS,
- using `www.linkedin.com`,
- removing query parameters,
- removing fragments,
- removing a trailing slash.

---

# 4. Collector Outputs

The collector should write three outputs during the same run.

## 4.1 Human-Readable URL Log

```text
output/urls_<timestamp>.txt
```

Example:

```text
QUERY	late_validation	uat_discovery	site:linkedin.com/posts continuous validation acceptance criteria	2026-07-21T15:30:00
SERPER_QUERY	site:linkedin.com/posts continuous validation acceptance criteria
1	https://www.linkedin.com/posts/christopherbelknap_a-hard-truth-there-is-no-uat-phase-in-activity-7376948593141784576-Sr4I
TITLE	Chris Belknap's Post
SNIPPET	Continuous validation within Sprints is valuable...
DATE	9 months ago
```

This file is useful for debugging and manual review.

---

## 4.2 Post Index

```text
data/post_index.jsonl
```

One unique record per LinkedIn post.

Example:

```json
{
  "post_id": "linkedin_7376948593141784576",
  "url": "https://www.linkedin.com/posts/christopherbelknap_a-hard-truth-there-is-no-uat-phase-in-activity-7376948593141784576-Sr4I",
  "source": "linkedin",
  "source_type": "search_result",
  "headline": "Chris Belknap's Post",
  "search_snippet": "Continuous validation within Sprints is valuable...",
  "search_date": "9 months ago",
  "author": null,
  "company": null,
  "likes": null,
  "comments": null,
  "post_text": null,
  "first_seen_at": "2026-07-21T19:30:00+00:00",
  "last_seen_at": "2026-07-21T19:30:00+00:00",
  "extraction_status": "pending",
  "classification_status": "pending"
}
```

### Stable Post IDs

Prefer the LinkedIn activity ID when present:

```text
activity-7376948593141784576
```

becomes:

```text
linkedin_7376948593141784576
```

If no activity ID exists, use a deterministic hash of the normalized URL.

---

## 4.3 Discoveries Index

```text
data/discoveries.jsonl
```

One record per query-to-post relationship.

Example:

```json
{
  "discovery_id": "discovery_83cba6c0f1a2",
  "post_id": "linkedin_7376948593141784576",
  "url": "https://www.linkedin.com/posts/christopherbelknap_a-hard-truth-there-is-no-uat-phase-in-activity-7376948593141784576-Sr4I",
  "source_query": "site:linkedin.com/posts continuous validation acceptance criteria",
  "original_query": "site:linkedin.com/posts \"continuous validation of acceptance criteria\"",
  "failure_mode_seed": "late_validation",
  "symptom_seed": "uat_discovery",
  "search_position": 2,
  "search_title": "Chris Belknap's Post",
  "search_snippet": "Continuous validation within Sprints is valuable...",
  "search_date": "9 months ago",
  "retrieved_at": "2026-07-21T19:30:00+00:00"
}
```

This allows one post to be connected to multiple search intents without duplicating the post itself.

---

# 5. Full Post Extraction

## File

```text
extract_posts.py
```

## Inputs

```text
data/post_index.jsonl
```

or, as a fallback:

```text
output/urls_*.txt
```

## Purpose

The extractor opens each LinkedIn post and captures the full visible post content and metadata.

Because LinkedIn content may be dynamically rendered and may require authentication, a browser-based extractor is usually needed.

Recommended browser approach:

```text
Playwright + persistent Chromium profile
```

## Extracted Fields

```json
{
  "post_id": "linkedin_7376948593141784576",
  "url": "...",
  "author": "Chris Belknap",
  "headline": "A hard truth: there is no UAT phase",
  "company": null,
  "published_date": "2025-10-01",
  "date_text": "9mo",
  "likes": 148,
  "comments": 42,
  "post_text": "A hard truth: there is no UAT phase...",
  "page_title": "Chris Belknap's Post | LinkedIn",
  "retrieved_at": "2026-07-21T23:10:00+00:00",
  "extraction_status": "success",
  "extraction_error": null
}
```

## Output

```text
data/post_content.jsonl
```

Use `null` when a field is not available.

Do not convert missing engagement into zero.

```json
{
  "likes": null,
  "comments": null
}
```

means the values were unavailable.

```json
{
  "likes": 0,
  "comments": 0
}
```

means the post was observed to have no engagement.

## Extraction Statuses

Recommended values:

```text
pending
success
partial
login_required
not_found
blocked
error
```

The extractor should be resumable and should skip successful records unless explicitly asked to refresh them.

---

# 6. Evidence Index Before Capability Mining

After post extraction, join:

```text
post_index.jsonl
+
discoveries.jsonl
+
post_content.jsonl
```

into a normalized evidence record.

Recommended output:

```text
data/evidence_index.jsonl
```

Example:

```json
{
  "post_id": "linkedin_7376948593141784576",
  "url": "...",
  "author": "Chris Belknap",
  "headline": "A hard truth: there is no UAT phase",
  "company": null,
  "published_date": "2025-10-01",
  "likes": 148,
  "comments": 42,
  "post_text": "A hard truth: there is no UAT phase...",
  "search_context": [
    {
      "source_query": "site:linkedin.com/posts continuous validation acceptance criteria",
      "failure_mode_seed": "late_validation",
      "symptom_seed": "uat_discovery",
      "search_position": 2
    }
  ],
  "first_seen_at": "2026-07-21T19:30:00+00:00",
  "last_extracted_at": "2026-07-21T23:10:00+00:00",
  "classification_status": "pending"
}
```

This becomes the clean input to the LLM.

---

# 7. Capability Mining With an LLM

## File

```text
classify_posts.py
```

## Input

```text
data/evidence_index.jsonl
```

## Purpose

The classifier converts raw post evidence into a structured product-management problem model.

Recommended target schema:

```json
{
  "id": "linkedin_7376948593141784576",
  "url": "...",
  "author": "Chris Belknap",
  "headline": "A hard truth: there is no UAT phase",
  "company": null,
  "likes": 148,
  "comments": 42,
  "date": "2025-10-01",
  "workflow_stage": "delivery_validation",
  "failure_mode": "late_validation",
  "root_cause": "Acceptance is treated as a downstream phase instead of being validated continuously during delivery.",
  "business_impact": "Requirement gaps are discovered after implementation, increasing rework and delaying releases.",
  "missing_capability": "Continuous validation of implemented behavior against acceptance criteria.",
  "solution_pattern": "Validate requirements and complete workflows incrementally within each sprint.",
  "keywords": [
    "acceptance criteria",
    "continuous validation",
    "UAT"
  ],
  "mentioned_tools": [],
  "companies_mentioned": [],
  "frameworks": [
    "Agile",
    "Scrum",
    "UAT"
  ],
  "quote": "There is no UAT phase.",
  "summary": "The post argues that acceptance should be established continuously rather than deferred to a final UAT phase.",
  "evidence_score": null,
  "specsight_fit": null,
  "should_review": true,
  "classification_status": "success",
  "classification_confidence": 0.88
}
```

Evidence ranking may be deferred. The fields can remain `null` until scoring logic is introduced.

## Controlled Taxonomies

Avoid allowing the model to invent a new label for every post.

Example workflow stages:

```text
discovery
requirements_definition
refinement
development
qa_validation
sprint_review
release
post_release
```

Example failure modes:

```text
ambiguous_requirements
missing_acceptance_criteria
late_validation
stakeholder_misalignment
implementation_drift
story_reopened
uat_rejection
release_scope_drift
low_feature_adoption
missing_business_outcome_validation
```

Example missing capabilities:

```text
continuous_specification
acceptance_criteria_traceability
implementation_to_requirement_mapping
cross_functional_validation
scope_change_detection
release_readiness_validation
post_release_outcome_validation
```

## Output

```text
data/capability_evidence.jsonl
```

---

# 8. Web Application Data Model

The web app should not be organized as a raw list of LinkedIn posts.

It should organize evidence into navigable issue areas.

Suggested hierarchy:

```text
Topic
    ↓
Workflow Stage
    ↓
Failure Mode
    ↓
Observed Root Causes
    ↓
Business Impact
    ↓
Missing Capability
    ↓
Current Solution Patterns
    ↓
Where Specsight Fits
    ↓
Supporting Posts
```

Example user flow:

```text
Latest Conversations
    ↓
Continuous Acceptance Validation
    ↓
Late validation discovered during UAT
    ↓
Why it happens
    ↓
Required capabilities
    ↓
Existing workarounds
    ↓
When Specsight is valuable
    ↓
View supporting LinkedIn evidence
```

## Suggested Web-App Entities

### Topic

```json
{
  "topic_id": "continuous_acceptance_validation",
  "name": "Continuous Acceptance Validation",
  "description": "How product teams validate business intent throughout implementation instead of at the end.",
  "post_count": 42,
  "latest_post_date": "2026-07-20"
}
```

### Failure Mode

```json
{
  "failure_mode_id": "late_validation",
  "name": "Late Validation",
  "workflow_stage": "delivery_validation",
  "summary": "Requirement and workflow gaps are identified only after development or during UAT."
}
```

### Capability

```json
{
  "capability_id": "continuous_spec_validation",
  "name": "Continuous Specification Validation",
  "description": "Compare implementation behavior with product intent throughout delivery.",
  "specsight_relevance": "high"
}
```

### Evidence Post

```json
{
  "post_id": "linkedin_7376948593141784576",
  "topic_ids": ["continuous_acceptance_validation"],
  "failure_mode_ids": ["late_validation"],
  "capability_ids": ["continuous_spec_validation"],
  "url": "...",
  "author": "Chris Belknap",
  "summary": "...",
  "quote": "There is no UAT phase."
}
```

---

# 9. Suggested Repository Structure

```text
specsight-linkedin-evidence-collector/
│
├── query_db.yaml
├── build_queries.py
├── queries.txt
│
├── collect_urls.py
├── extract_posts.py
├── build_evidence_index.py
├── classify_posts.py
│
├── output/
│   └── urls_<timestamp>.txt
│
├── data/
│   ├── post_index.jsonl
│   ├── discoveries.jsonl
│   ├── post_content.jsonl
│   ├── evidence_index.jsonl
│   ├── capability_evidence.jsonl
│   └── failed_urls.jsonl
│
├── schemas/
│   ├── post_index.schema.json
│   ├── discovery.schema.json
│   ├── post_content.schema.json
│   └── capability_evidence.schema.json
│
├── tests/
│   ├── test_build_queries.py
│   ├── test_collect_urls.py
│   ├── test_extract_posts.py
│   └── fixtures/
│
├── .env.example
├── .gitignore
├── requirements.txt
├── README.md
└── SETUP_AND_RUN.md
```

---

# 10. Data Integrity Principles

1. Preserve raw evidence before classification.
2. Keep post records separate from query-discovery records.
3. Use stable IDs.
4. Use `null` for unavailable values.
5. Keep original search queries for traceability.
6. Make every stage resumable.
7. Never overwrite successful extraction records unintentionally.
8. Validate every LLM response against a JSON schema.
9. Keep controlled taxonomies for major analytical fields.
10. Retain the source URL for every generated insight.

---

# 11. Current Implementation Boundary

The current working stage is:

```text
query_db.yaml
    ↓
build_queries.py
    ↓
queries.txt
    ↓
collect_urls.py
    ↓
Serper results
```

The next implementation milestone is:

```text
Serper results
    ↓
post_index.jsonl
+
discoveries.jsonl
```

After that:

```text
post_index.jsonl
    ↓
extract_posts.py
    ↓
post_content.jsonl
```

Then:

```text
evidence_index.jsonl
    ↓
classify_posts.py
    ↓
capability_evidence.jsonl
```
