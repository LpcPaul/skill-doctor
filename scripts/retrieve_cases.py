#!/usr/bin/env python3
"""
AgentRX — Deterministic case retrieval.

Given an intake card (or case-like JSON), retrieve top-k candidate cases
from the case library using deterministic matching. This script does NOT
recommend a final route — it surfaces candidates for the agent to reason
over.

Default retrieval mode (no route leakage):
  Only uses evidence-driven fields:
  1. task exact match (highest weight)
  2. journey_stage exact match
  3. problem_family exact match
  4. symptom word overlap (weak match)
  5. tags overlap (weak match)
  6. environment as rerank signal (very weak)

Optional route hint mode (--use-route-hint):
  When the intake has a known best_candidate_route_id and the caller
  explicitly passes --use-route-hint, route id is used as an additional
  weak rerank signal (weight < problem_family).

Filtering:
  Results below --min-score (default 20) are dropped.
  If no candidates remain, output [] with exit code 0.

Usage:
    python3 scripts/retrieve_cases.py --intake /path/to/intake.json --top-k 5
    python3 scripts/retrieve_cases.py --intake /path/to/intake.json --top-k 3 --use-route-hint
    python3 scripts/retrieve_cases.py --intake /path/to/intake.json --top-k 3 --min-score 40 --exclude-seeds
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = ROOT / "cases"


def load_cases(cases_dir: Path, exclude_seeds: bool = False) -> list[dict]:
    """Load all case JSON files from the cases directory."""
    cases = []
    scan_paths = [cases_dir]
    seeds_dir = cases_dir / "seeds"
    if seeds_dir.exists():
        scan_paths.append(seeds_dir)

    for scan_dir in scan_paths:
        for f in sorted(scan_dir.glob("*.json")):
            if f.name in ("index.json",):
                continue
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    case = json.load(fh)
                    case["_source_file"] = f.name
                    case["_is_seed"] = case.get("source") == "synthetic-seed" or "seeds" in str(f.parent)
                    if exclude_seeds and case["_is_seed"]:
                        continue
                    cases.append(case)
            except (json.JSONDecodeError, IOError):
                pass
    return cases


def score_case(intake: dict, candidate: dict, use_route_hint: bool = False) -> dict[str, Any]:
    """Score a candidate case against the intake. Returns score dict."""
    score = 0.0
    matched_on = []

    intake_evidence = intake.get("evidence", {})
    candidate_evidence = candidate.get("evidence", {})
    intake_inference = intake.get("inference", {})
    candidate_inference = candidate.get("inference", {})

    # 1. Task exact match (highest weight)
    intake_task = intake_evidence.get("task", "")
    candidate_task = candidate_evidence.get("task", "")
    if intake_task and candidate_task and intake_task == candidate_task:
        score += 40.0
        matched_on.append("task")

    # 2. Journey stage exact match
    intake_stage = intake_inference.get("journey_stage", "")
    candidate_stage = candidate_inference.get("journey_stage", "")
    if intake_stage and candidate_stage and intake_stage == candidate_stage:
        score += 25.0
        matched_on.append("journey_stage")

    # 3. Problem family exact match
    intake_family = intake_inference.get("problem_family", "")
    candidate_family = candidate_inference.get("problem_family", "")
    if intake_family and candidate_family and intake_family == candidate_family:
        score += 20.0
        matched_on.append("problem_family")

    # 4. Route hint (optional, only when --use-route-hint is set)
    if use_route_hint:
        intake_route = intake_inference.get("best_candidate_route_id", "")
        candidate_route = candidate_inference.get("best_candidate_route_id", "")
        if intake_route and candidate_route and intake_route == candidate_route:
            score += 8.0
            matched_on.append("best_candidate_route_id")

    # 5. Symptom / tags weak match
    intake_symptom = intake_evidence.get("symptom", "").lower()
    candidate_symptom = candidate_evidence.get("symptom", "").lower()
    intake_tags = set(t.lower() for t in intake.get("tags", []))
    candidate_tags = set(t.lower() for t in candidate.get("tags", []))

    if intake_symptom and candidate_symptom:
        intake_words = set(intake_symptom.split())
        candidate_words = set(candidate_symptom.split())
        common = intake_words & candidate_words
        if common:
            symptom_overlap = len(common) / max(len(intake_words), len(candidate_words))
            score += symptom_overlap * 5.0
            matched_on.append("symptom")

    if intake_tags and candidate_tags:
        common_tags = intake_tags & candidate_tags
        if common_tags:
            tag_overlap = len(common_tags) / max(len(intake_tags), len(candidate_tags))
            score += tag_overlap * 3.0
            matched_on.append("tags")

    # 6. Environment rerank signal (very weak)
    intake_env = intake_evidence.get("environment", {})
    candidate_env = candidate_evidence.get("environment", {})
    env_match_info = {}
    if intake_env and candidate_env:
        if intake_env.get("platform") and candidate_env.get("platform"):
            if intake_env["platform"] == candidate_env["platform"]:
                score += 2.0
                env_match_info["platform_match"] = True

        for key in ["requires_login", "requires_dynamic_render",
                    "requires_local_filesystem", "requires_network",
                    "requires_deterministic_execution"]:
            if key in intake_env and key in candidate_env:
                if intake_env[key] == candidate_env[key]:
                    score += 0.5

    confidence = "low"
    if score >= 80:
        confidence = "high"
    elif score >= 40:
        confidence = "medium"

    return {
        "score": round(score, 2),
        "matched_on": matched_on,
        "confidence": confidence,
        "env_match_info": env_match_info,
    }


def retrieve(intake: dict, cases: list[dict], top_k: int,
             min_score: float = 20.0, use_route_hint: bool = False) -> list[dict]:
    """Retrieve top-k candidate cases, filtering by min_score."""
    scored = []
    for candidate in cases:
        scoring = score_case(intake, candidate, use_route_hint)
        scored.append((scoring, candidate))

    scored.sort(key=lambda x: x[0]["score"], reverse=True)

    results = []
    for scoring, candidate in scored:
        if scoring["score"] < min_score:
            continue
        if len(results) >= top_k:
            break
        result = {
            "case_id": candidate.get("id", ""),
            "title": candidate.get("title", ""),
            "summary": candidate.get("summary", ""),
            "task": candidate.get("evidence", {}).get("task", ""),
            "journey_stage": candidate.get("inference", {}).get("journey_stage", ""),
            "problem_family": candidate.get("inference", {}).get("problem_family", ""),
            "best_candidate_route_id": candidate.get("inference", {}).get("best_candidate_route_id", ""),
            "confidence": scoring["confidence"],
            "score": scoring["score"],
            "matched_on": scoring["matched_on"],
            "is_seed": candidate.get("_is_seed", False),
        }
        if scoring.get("env_match_info"):
            result["environment_match"] = scoring["env_match_info"]
        results.append(result)

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Retrieve top-k candidate cases from the AgentRX case library."
    )
    parser.add_argument(
        "--intake", required=True,
        help="Path to the intake card / case-like JSON file"
    )
    parser.add_argument(
        "--top-k", type=int, default=5,
        help="Number of top candidates to return (default: 5)"
    )
    parser.add_argument(
        "--cases-dir", default=None,
        help="Path to the cases directory (default: ./cases/)"
    )
    parser.add_argument(
        "--exclude-seeds", action="store_true",
        help="Exclude synthetic-seed cases from retrieval"
    )
    parser.add_argument(
        "--use-route-hint", action="store_true", default=False,
        help="Allow best_candidate_route_id from intake as a weak rerank signal"
    )
    parser.add_argument(
        "--min-score", type=float, default=20.0,
        help="Minimum score threshold for candidates (default: 20)"
    )
    args = parser.parse_args()

    intake_path = Path(args.intake)
    if not intake_path.exists():
        print(f"ERROR: Intake file not found: {intake_path}", file=sys.stderr)
        sys.exit(1)

    with open(intake_path, "r", encoding="utf-8") as f:
        try:
            intake = json.load(f)
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid JSON in intake file: {e}", file=sys.stderr)
            sys.exit(1)

    cases_dir = Path(args.cases_dir) if args.cases_dir else CASES_DIR
    if not cases_dir.exists():
        print(f"ERROR: Cases directory not found: {cases_dir}", file=sys.stderr)
        sys.exit(1)

    cases = load_cases(cases_dir, args.exclude_seeds)
    if not cases:
        print("[]")
        sys.exit(0)

    results = retrieve(intake, cases, args.top_k, args.min_score, args.use_route_hint)

    print(json.dumps(results, indent=2, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
