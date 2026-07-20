from __future__ import annotations

import argparse
import itertools
from pathlib import Path
from typing import Any

import yaml


def quote(term: str) -> str:
    term = term.strip()
    if term.startswith('"') and term.endswith('"'):
        return term
    return f'"{term}"'


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict) or "query_groups" not in data:
        raise ValueError("YAML must contain a top-level 'query_groups' mapping.")
    return data


def build_queries(config: dict[str, Any]) -> list[str]:
    settings = config.get("settings", {})
    site_filter = settings.get("site_filter", "site:linkedin.com/posts")
    max_per_symptom = int(settings.get("max_queries_per_symptom", 8))

    results: list[str] = []

    for group_name, group in config["query_groups"].items():
        topic_phrases = group.get("topic_phrases", [])
        symptoms = group.get("symptoms", {})

        for symptom_name, symptom_phrases in symptoms.items():
            combinations = itertools.product(topic_phrases, symptom_phrases)
            count = 0

            for topic, symptom in combinations:
                query = f"{site_filter} {quote(str(topic))} {quote(str(symptom))}"
                results.append(
                    f"{group_name}\t{symptom_name}\t{query}"
                )
                count += 1
                if count >= max_per_symptom:
                    break

    # Preserve order while removing exact duplicates.
    return list(dict.fromkeys(results))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build browser-ready search queries from query_db.yaml."
    )
    parser.add_argument("--config", default="query_db.yaml")
    parser.add_argument("--output", default="queries.txt")
    args = parser.parse_args()

    config_path = Path(args.config)
    output_path = Path(args.output)

    config = load_config(config_path)
    queries = build_queries(config)

    output_path.write_text("\n".join(queries) + "\n", encoding="utf-8")
    print(f"Wrote {len(queries)} queries to {output_path.resolve()}")


if __name__ == "__main__":
    main()
