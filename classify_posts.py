from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


LOGGER = logging.getLogger("classify_posts")

DEFAULT_MODEL = "gpt-5.5"
EXTRACTED_POST_GLOB = "extracted_posts_*.jsonl"
OUTPUT_PREFIX = "capability_index"

KNOWLEDGE_FILES = (
    "specsight_overview.md",
    "product_capabilities.md",
    "specsight_product_taxonomy.json",
    "positioning.md",
    "target_customers.md",
    "buying_triggers.md",
    "workflow_model.md",
    "workflow_failure_modes.md",
    "classification_rules.md",
    "specsight_classification_taxonomy.json",
)

Relevance = Literal["direct", "indirect", "adjacent", "none"]
Confidence = Literal["high", "medium", "low"]
Persona = Literal[
    "primary_champion",
    "economic_buyer_or_sponsor",
    "important_collaborators",
]


class ClassificationPayload(BaseModel):
    """Structured content returned by the LLM for one post."""

    model_config = ConfigDict(extra="forbid")

    post_id: str
    url: str
    primary_workflow_stage: str | None = None
    secondary_workflow_stages: list[str] = Field(default_factory=list, max_length=3)
    primary_pain_point: str | None = None
    secondary_pain_points: list[str] = Field(default_factory=list, max_length=3)
    failure_modes: list[str] = Field(default_factory=list)
    root_causes: list[str] = Field(default_factory=list)
    business_consequences: list[str] = Field(default_factory=list)
    persona_relevance: list[Persona] = Field(default_factory=list)
    buying_trigger: str | None = None
    jtbd_relevance: Relevance
    specsight_relevance: Relevance
    relevant_product_areas: list[str] = Field(default_factory=list)
    relevant_product_capabilities: list[str] = Field(default_factory=list)
    fit_explanation: str
    product_boundary_or_caveat: str | None = None
    evidence_quotes: list[str] = Field(default_factory=list)
    evidence_summary: str
    classification_confidence: Confidence
    review_required: bool

    @field_validator(
        "secondary_workflow_stages",
        "secondary_pain_points",
        "failure_modes",
        "root_causes",
        "business_consequences",
        "persona_relevance",
        "relevant_product_areas",
        "relevant_product_capabilities",
        "evidence_quotes",
    )
    @classmethod
    def deduplicate_lists(cls, values: list[Any]) -> list[Any]:
        seen: set[Any] = set()
        result: list[Any] = []
        for value in values:
            if value not in seen:
                seen.add(value)
                result.append(value)
        return result


class OutputRecord(BaseModel):
    """Persisted record: classification plus source and run metadata."""

    model_config = ConfigDict(extra="forbid")

    post_id: str
    url: str
    author: str | None = None
    published_date: str | None = None

    primary_workflow_stage: str | None = None
    secondary_workflow_stages: list[str] = Field(default_factory=list)
    primary_pain_point: str | None = None
    secondary_pain_points: list[str] = Field(default_factory=list)
    failure_modes: list[str] = Field(default_factory=list)
    root_causes: list[str] = Field(default_factory=list)
    business_consequences: list[str] = Field(default_factory=list)
    persona_relevance: list[Persona] = Field(default_factory=list)
    buying_trigger: str | None = None
    jtbd_relevance: Relevance | None = None
    specsight_relevance: Relevance | None = None
    relevant_product_areas: list[str] = Field(default_factory=list)
    relevant_product_capabilities: list[str] = Field(default_factory=list)
    fit_explanation: str | None = None
    product_boundary_or_caveat: str | None = None
    evidence_quotes: list[str] = Field(default_factory=list)
    evidence_summary: str | None = None
    classification_confidence: Confidence | None = None
    review_required: bool = True

    classification_status: Literal["success", "failed"]
    classification_error: str | None = None
    source_collection_run_id: str | None = None
    source_extracted_at: str | None = None
    classified_at: str
    classifier_model: str


class TaxonomySets(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    workflow_stages: set[str]
    pain_points: set[str]
    failure_modes: set[str]
    buying_triggers: set[str]
    product_areas: set[str]
    product_capabilities: set[str]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def timestamp_for_filename() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Required file not found: {path.resolve()}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path.resolve()}: {exc}") from exc

    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path.resolve()}")
    return value


def load_knowledge_pack(knowledge_dir: Path) -> tuple[str, dict[str, Any], dict[str, Any]]:
    missing = [name for name in KNOWLEDGE_FILES if not (knowledge_dir / name).is_file()]
    if missing:
        formatted = "\n".join(f"  - {name}" for name in missing)
        raise FileNotFoundError(
            f"Knowledge directory is missing required files:\n{formatted}\n"
            f"Directory: {knowledge_dir.resolve()}"
        )

    product_taxonomy = load_json(knowledge_dir / "specsight_product_taxonomy.json")
    classification_taxonomy = load_json(
        knowledge_dir / "specsight_classification_taxonomy.json"
    )

    sections: list[str] = []
    for filename in KNOWLEDGE_FILES:
        path = knowledge_dir / filename
        content = path.read_text(encoding="utf-8").strip()
        sections.append(f"\n===== {filename} =====\n{content}")

    return "\n".join(sections), product_taxonomy, classification_taxonomy


def build_taxonomy_sets(
    product_taxonomy: dict[str, Any],
    classification_taxonomy: dict[str, Any],
) -> TaxonomySets:
    def keys_of(mapping_name: str, source: dict[str, Any]) -> set[str]:
        value = source.get(mapping_name)
        if not isinstance(value, dict):
            raise ValueError(f"Taxonomy field '{mapping_name}' must be a JSON object")
        return set(value.keys())

    return TaxonomySets(
        workflow_stages=keys_of("workflow_stages", classification_taxonomy),
        pain_points=keys_of("market_pain_points", classification_taxonomy),
        failure_modes=keys_of("failure_modes", classification_taxonomy),
        buying_triggers=keys_of("buying_triggers", classification_taxonomy),
        product_areas=keys_of("product_areas", product_taxonomy),
        product_capabilities=keys_of("capabilities", product_taxonomy),
    )


def find_latest_extracted_file(data_dir: Path) -> Path:
    candidates = [path for path in data_dir.glob(EXTRACTED_POST_GLOB) if path.is_file()]
    if not candidates:
        raise FileNotFoundError(
            f"No files matching {EXTRACTED_POST_GLOB!r} found in {data_dir.resolve()}"
        )
    return max(candidates, key=lambda path: (path.stat().st_mtime, path.name))


def iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                LOGGER.warning("Skipping malformed JSON at %s:%d: %s", path, line_number, exc)
                continue
            if not isinstance(record, dict):
                LOGGER.warning("Skipping non-object JSON at %s:%d", path, line_number)
                continue
            yield line_number, record


def append_jsonl(path: Path, record: OutputRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(record.model_dump_json(exclude_none=False))
        handle.write("\n")
        handle.flush()


def build_system_prompt(knowledge_pack: str) -> str:
    return f"""You are an evidence classifier for Specsight's LinkedIn market-research pipeline.

Your task is to classify exactly one LinkedIn post using the supplied product context,
market context, controlled taxonomy, and classification rules.

NON-NEGOTIABLE RULES
1. Use only evidence contained in the LinkedIn post for claims about the post.
2. Use only identifiers that exist in the supplied taxonomies.
3. Do not force a Specsight connection. Use specsight_relevance='none' when the fit
   requires undocumented functionality or the post is not about a supported context gap.
4. Product mappings must be grounded in documented Specsight capabilities.
5. Evidence quotes must be short, exact substrings copied from the post.
6. Use null or empty arrays when evidence is missing. Never invent a buyer, trigger,
   failure mode, consequence, capability, or workflow stage.
7. Choose one primary workflow stage and one primary pain point when supported.
   Add no more than three secondary workflow stages and three secondary pain points.
8. Set review_required=true when confidence is low, the post is ambiguous, or the
   product mapping is indirect/adjacent and materially debatable.
9. Return only the structured object required by the response schema.

REFERENCE KNOWLEDGE
{knowledge_pack}
"""


def build_user_prompt(post: dict[str, Any], max_post_chars: int) -> str:
    post_text = str(post.get("post_text") or "").strip()
    if len(post_text) > max_post_chars:
        post_text = post_text[:max_post_chars]
        truncation_note = (
            f"\n[Post text truncated to the first {max_post_chars} characters by the classifier.]"
        )
    else:
        truncation_note = ""

    return f"""Classify this LinkedIn post.

POST ID: {post.get('post_id', '')}
POST URL: {post.get('url', '')}
AUTHOR: {post.get('author') or 'unknown'}
PUBLISHED DATE: {post.get('published_date') or 'unknown'}

POST TEXT:
{post_text}{truncation_note}
"""


def validate_taxonomy_membership(payload: ClassificationPayload, taxonomy: TaxonomySets) -> None:
    errors: list[str] = []

    def require_member(value: str | None, allowed: set[str], field: str) -> None:
        if value is not None and value not in allowed:
            errors.append(f"{field} contains unknown identifier: {value!r}")

    def require_members(values: list[str], allowed: set[str], field: str) -> None:
        unknown = sorted(set(values) - allowed)
        if unknown:
            errors.append(f"{field} contains unknown identifiers: {unknown}")

    require_member(payload.primary_workflow_stage, taxonomy.workflow_stages, "primary_workflow_stage")
    require_members(
        payload.secondary_workflow_stages,
        taxonomy.workflow_stages,
        "secondary_workflow_stages",
    )
    require_member(payload.primary_pain_point, taxonomy.pain_points, "primary_pain_point")
    require_members(payload.secondary_pain_points, taxonomy.pain_points, "secondary_pain_points")
    require_members(payload.failure_modes, taxonomy.failure_modes, "failure_modes")
    require_member(payload.buying_trigger, taxonomy.buying_triggers, "buying_trigger")
    require_members(payload.relevant_product_areas, taxonomy.product_areas, "relevant_product_areas")
    require_members(
        payload.relevant_product_capabilities,
        taxonomy.product_capabilities,
        "relevant_product_capabilities",
    )

    if payload.primary_workflow_stage in payload.secondary_workflow_stages:
        errors.append("primary_workflow_stage must not be repeated in secondary_workflow_stages")
    if payload.primary_pain_point in payload.secondary_pain_points:
        errors.append("primary_pain_point must not be repeated in secondary_pain_points")

    if payload.specsight_relevance == "none":
        if payload.relevant_product_areas or payload.relevant_product_capabilities:
            errors.append(
                "specsight_relevance='none' requires empty product area and capability arrays"
            )

    if errors:
        raise ValueError("; ".join(errors))


def validate_source_grounding(payload: ClassificationPayload, post: dict[str, Any]) -> None:
    source_text = str(post.get("post_text") or "")
    errors: list[str] = []

    if payload.post_id != str(post.get("post_id") or ""):
        errors.append("returned post_id does not match the source record")
    if payload.url != str(post.get("url") or ""):
        errors.append("returned url does not match the source record")

    for quote in payload.evidence_quotes:
        if not quote.strip():
            errors.append("evidence_quotes contains a blank quote")
        elif quote not in source_text:
            errors.append(f"evidence quote is not an exact substring of the post: {quote!r}")

    if payload.specsight_relevance != "none" and not payload.evidence_quotes:
        errors.append("a non-none Specsight mapping requires at least one exact evidence quote")

    if errors:
        raise ValueError("; ".join(errors))


def classify_one_post(
    client: OpenAI,
    model: str,
    system_prompt: str,
    post: dict[str, Any],
    taxonomy: TaxonomySets,
    max_post_chars: int,
    retries: int,
) -> ClassificationPayload:
    last_error: Exception | None = None

    for attempt in range(retries + 1):
        try:
            response = client.responses.parse(
                model=model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": build_user_prompt(post, max_post_chars)},
                ],
                text_format=ClassificationPayload,
            )

            payload = response.output_parsed
            if payload is None:
                refusal = getattr(response, "refusal", None)
                raise ValueError(f"The model returned no parsed output. Refusal: {refusal!r}")

            validate_taxonomy_membership(payload, taxonomy)
            validate_source_grounding(payload, post)
            return payload

        except (
            ValidationError,
            ValueError,
            APIConnectionError,
            APITimeoutError,
            RateLimitError,
            APIStatusError,
        ) as exc:
            last_error = exc
            if attempt >= retries:
                break

            wait_seconds = min(8.0, 2.0 ** attempt)
            LOGGER.warning(
                "Classification attempt %d failed for %s: %s. Retrying in %.1fs.",
                attempt + 1,
                post.get("post_id", "unknown"),
                exc,
                wait_seconds,
            )
            time.sleep(wait_seconds)

    assert last_error is not None
    raise last_error


def success_record(
    source: dict[str, Any],
    payload: ClassificationPayload,
    model: str,
) -> OutputRecord:
    data = payload.model_dump()
    data.update(
        {
            "author": source.get("author"),
            "published_date": source.get("published_date"),
            "classification_status": "success",
            "classification_error": None,
            "source_collection_run_id": source.get("source_collection_run_id"),
            "source_extracted_at": source.get("extracted_at"),
            "classified_at": utc_now_iso(),
            "classifier_model": model,
        }
    )
    return OutputRecord.model_validate(data)


def failure_record(source: dict[str, Any], error: Exception | str, model: str) -> OutputRecord:
    return OutputRecord(
        post_id=str(source.get("post_id") or "unknown"),
        url=str(source.get("url") or ""),
        author=source.get("author"),
        published_date=source.get("published_date"),
        classification_status="failed",
        classification_error=str(error),
        source_collection_run_id=source.get("source_collection_run_id"),
        source_extracted_at=source.get("extracted_at"),
        classified_at=utc_now_iso(),
        classifier_model=model,
        review_required=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Classify the newest extracted LinkedIn posts using Specsight context, "
            "controlled taxonomies, and OpenAI Structured Outputs."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        help="Specific extracted_posts_*.jsonl file. Defaults to the newest file in --data-dir.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output JSONL path. Defaults to data/capability_index_<timestamp>.jsonl.",
    )
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--knowledge-dir", type=Path, default=Path("knowledge"))
    parser.add_argument("--limit", type=int, default=None, help="Maximum successful posts to attempt.")
    parser.add_argument(
        "--model",
        default=os.getenv("OPENAI_MODEL", DEFAULT_MODEL),
        help=f"OpenAI model name. Default: OPENAI_MODEL or {DEFAULT_MODEL}.",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=1,
        help="Number of retries after the first failed request. Default: 1.",
    )
    parser.add_argument(
        "--max-post-chars",
        type=int,
        default=30000,
        help="Maximum post characters sent to the model. Default: 30000.",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.0,
        help="Optional pause between successful API calls.",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be at least 1")
    if args.retries < 0:
        raise ValueError("--retries cannot be negative")
    if args.max_post_chars < 1000:
        raise ValueError("--max-post-chars must be at least 1000")

    load_dotenv()
    if not os.getenv("OPENAI_API_KEY"):
        raise EnvironmentError(
            "OPENAI_API_KEY is not set. Add it to the current environment or a local .env file."
        )

    input_path = args.input or find_latest_extracted_file(args.data_dir)
    if not input_path.is_file():
        raise FileNotFoundError(f"Input file not found: {input_path.resolve()}")

    output_path = args.output or (
        args.data_dir / f"{OUTPUT_PREFIX}_{timestamp_for_filename()}.jsonl"
    )
    if output_path.resolve() == input_path.resolve():
        raise ValueError("Output path must not overwrite the extraction input file")
    if output_path.exists():
        raise FileExistsError(
            f"Output file already exists: {output_path.resolve()}\n"
            "Choose a new --output path; existing classifications are never overwritten."
        )

    LOGGER.info("Loading knowledge from %s", args.knowledge_dir.resolve())
    knowledge_pack, product_taxonomy, classification_taxonomy = load_knowledge_pack(
        args.knowledge_dir
    )
    taxonomy_sets = build_taxonomy_sets(product_taxonomy, classification_taxonomy)
    system_prompt = build_system_prompt(knowledge_pack)

    client = OpenAI()
    attempted = 0
    succeeded = 0
    failed = 0
    skipped = 0

    LOGGER.info("Input: %s", input_path.resolve())
    LOGGER.info("Output: %s", output_path.resolve())
    LOGGER.info("Model: %s", args.model)

    for line_number, post in iter_jsonl(input_path):
        if post.get("extraction_status") != "success":
            skipped += 1
            continue

        post_text = str(post.get("post_text") or "").strip()
        if not post_text:
            skipped += 1
            continue

        if args.limit is not None and attempted >= args.limit:
            break

        attempted += 1
        post_id = str(post.get("post_id") or f"line_{line_number}")
        LOGGER.info("[%d] Classifying %s", attempted, post_id)

        try:
            payload = classify_one_post(
                client=client,
                model=args.model,
                system_prompt=system_prompt,
                post=post,
                taxonomy=taxonomy_sets,
                max_post_chars=args.max_post_chars,
                retries=args.retries,
            )
            append_jsonl(output_path, success_record(post, payload, args.model))
            succeeded += 1
        except Exception as exc:  # preserve the failed post and continue the run
            LOGGER.error("Classification failed for %s: %s", post_id, exc)
            append_jsonl(output_path, failure_record(post, exc, args.model))
            failed += 1

        if args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

    LOGGER.info(
        "Finished. attempted=%d succeeded=%d failed=%d skipped=%d output=%s",
        attempted,
        succeeded,
        failed,
        skipped,
        output_path.resolve(),
    )
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        LOGGER.error("Interrupted by user")
        raise SystemExit(130)
    except Exception as exc:
        LOGGER.error("Fatal error: %s", exc)
        raise SystemExit(1)
