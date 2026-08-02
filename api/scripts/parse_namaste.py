#!/usr/bin/env python
"""Read-only reconnaissance over the NAMASTE Ayurveda morbidity code export.

    docker compose exec api python scripts/parse_namaste.py

Reads the legacy .xls, splits the packed NAMC_CODE column into a NAMASTE code plus an
optional ICD-11 TM2 reference, and writes two CSVs under data/namaste/derived/. It touches
no database and never modifies the source workbook -- the point is to understand the export
before any schema exists.

Observed shape of the supplied export (2910 rows, 12 columns): NAMC_CODE packs a TM2 code and
a NAMASTE code together in either order, e.g. "SP12(AAE-16)", "SR11 (AAA-1)", "AAB-3 (SP9Y)",
"AAA-2.2", "SK00 (F)", with non-breaking spaces sprinkled in.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

# A bare branch node in the NAMASTE hierarchy: AYU, DIS, A, AA, AAA.
HIERARCHY_RE = re.compile(r"^[A-Z]{1,4}$")

# A NAMASTE leaf code: AAE-16, AAA-2.2, ED-4.3.10 -- and single-letter branches like F-1, H-8,
# which a {2,4} prefix would miss (285 rows in the supplied export).
LEAF_RE = re.compile(r"[A-Z]{1,4}-\d+(?:\.\d+)*")

# An ICD-11 TM2 stem code. The export uses far more than SP/SR: SK, SL, SM, SN, SQ, SS, ST all
# appear, and the fourth character may be a letter (SP9Y, SM1P, SK6A), so match the general
# ICD-11 chapter-26 shape instead of enumerating prefixes.
TM2_REF_RE = re.compile(r"\bS[A-Z][0-9][0-9A-Z]\b")

# The workbook uses "-" as its null sentinel in every text column, not just Long_definition.
NULL_SENTINELS = {"", "-", "nan", "none"}

SOURCE_COLUMNS = {
    "namc_id": "NAMC_ID",
    "raw_code": "NAMC_CODE",
    "term_roman": "NAMC_term",
    "term_diacritical": "NAMC_term_diacritical",
    "term_devanagari": "NAMC_term_DEVANAGARI",
    "long_definition": "Long_definition",
    "ontology_branches": "Ontology_branches",
    "name_english": "Name English",
    "primary_index": "Primary Index Related",
}

CLEAN_COLUMNS = [
    "namc_id",
    "namc_code",
    "term_roman",
    "term_diacritical",
    "term_devanagari",
    "name_english",
    "long_definition",
    "primary_index",
    "ontology_branches",
]

SEED_COLUMNS = ["namc_code", "tm2_ref", "name_english"]

SANITY_CODE = "AAE-16"  # Sandhigata Vata / osteoarthritis -- the demo anchor's NAMASTE side.


def resolve_data_dir() -> Path:
    """Locate data/namaste both inside the container and on a developer machine."""
    container = Path("/data/namaste")
    if container.is_dir():
        return container
    return Path(__file__).resolve().parents[2] / "data" / "namaste"


def find_workbook(data_dir: Path) -> Path:
    candidates = sorted(p for p in data_dir.glob("*.xls") if not p.name.startswith("~$"))
    if not candidates:
        sys.exit(f"no .xls export found in {data_dir}")
    if len(candidates) > 1:
        names = ", ".join(p.name for p in candidates)
        sys.exit(f"expected exactly one .xls in {data_dir}, found: {names}")
    return candidates[0]


def text(value: Any) -> str | None:
    """Normalize a cell: non-breaking spaces to spaces, collapse runs, sentinel to None."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    collapsed = re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip()
    return None if collapsed.lower() in NULL_SENTINELS else collapsed


def split_code(cleaned: str) -> tuple[str | None, str | None, str]:
    """Split a packed NAMC_CODE into (tm2_ref, namc_code, kind).

    The two parts appear in either order -- "SP12(AAE-16)" and "AAB-3 (SP9Y)" are both real --
    so each part is located independently rather than by position.
    """
    tm2_match = TM2_REF_RE.search(cleaned)
    tm2_ref = tm2_match.group(0) if tm2_match else None

    leaf_match = LEAF_RE.search(cleaned)
    if leaf_match:
        return tm2_ref, leaf_match.group(0), "leaf"

    # Not a leaf: strip the TM2 token and any punctuation and see if a branch node remains,
    # which catches rows like "SK00 (F)" and "GG (SM1Y)".
    remainder = cleaned.replace(tm2_ref, "") if tm2_ref else cleaned
    remainder = re.sub(r"[()\s]", "", remainder)
    if HIERARCHY_RE.fullmatch(remainder):
        return tm2_ref, None, "hierarchy"

    return tm2_ref, None, "unknown"


def normalize_primary_index(value: Any) -> str | None:
    """Primary/primary -> primary, Index -> index, "-" -> None."""
    cleaned = text(value)
    return cleaned.lower() if cleaned else None


def require_columns(frame: pd.DataFrame) -> None:
    missing = [name for name in SOURCE_COLUMNS.values() if name not in frame.columns]
    if missing:
        sys.exit(f"export is missing expected column(s): {missing}\nfound: {list(frame.columns)}")


def build_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        raw = row[SOURCE_COLUMNS["raw_code"]]
        cleaned = text(raw) or ""
        tm2_ref, namc_code, kind = split_code(cleaned)
        namc_id = row[SOURCE_COLUMNS["namc_id"]]
        records.append(
            {
                "namc_id": None if pd.isna(namc_id) else int(namc_id),
                "raw_code": cleaned,
                "namc_code": namc_code,
                "tm2_ref": tm2_ref,
                "kind": kind,
                "term_roman": text(row[SOURCE_COLUMNS["term_roman"]]),
                "term_diacritical": text(row[SOURCE_COLUMNS["term_diacritical"]]),
                "term_devanagari": text(row[SOURCE_COLUMNS["term_devanagari"]]),
                "name_english": text(row[SOURCE_COLUMNS["name_english"]]),
                "long_definition": text(row[SOURCE_COLUMNS["long_definition"]]),
                "primary_index": normalize_primary_index(row[SOURCE_COLUMNS["primary_index"]]),
                "ontology_branches": text(row[SOURCE_COLUMNS["ontology_branches"]]),
            }
        )
    return records


def summarize(records: list[dict[str, Any]], clean: pd.DataFrame, seed: pd.DataFrame, dropped: int) -> None:
    kinds = pd.Series([r["kind"] for r in records])
    with_tm2 = sum(1 for r in records if r["tm2_ref"])
    leaf_with_tm2 = sum(1 for r in records if r["tm2_ref"] and r["kind"] == "leaf")

    print("summary")
    print(f"  total rows          : {len(records)}")
    print(f"  hierarchy rows      : {int((kinds == 'hierarchy').sum())}")
    print(f"  leaf rows           : {int((kinds == 'leaf').sum())}")
    print(f"  unclassified rows   : {int((kinds == 'unknown').sum())}")
    print(f"  rows with tm2_ref   : {with_tm2}  (of which leaves: {leaf_with_tm2})")
    print(f"  duplicate leaf codes collapsed: {dropped}")

    unknown = [r["raw_code"] for r in records if r["kind"] == "unknown"]
    if unknown:
        print(f"  unclassified codes  : {unknown}")

    print(f"\n  namc_clean.csv rows    : {len(clean)}")
    print(f"  namc_tm2_seed.csv rows : {len(seed)}")

    print(f"\nsanity check -- namc_code {SANITY_CODE} (expect Sandhigata Vata / osteoarthritis)")
    hit = clean[clean["namc_code"] == SANITY_CODE]
    if hit.empty:
        print(f"  NOT FOUND -- parsing is wrong, {SANITY_CODE} must be present")
        sys.exit(1)
    record = hit.iloc[0].to_dict()
    for key in CLEAN_COLUMNS:
        value = record.get(key)
        rendered = "" if value is None or (isinstance(value, float) and math.isnan(value)) else str(value)
        if len(rendered) > 100:
            rendered = rendered[:100] + "..."
        print(f"  {key:18} = {rendered}")
    tm2_for_sanity = seed[seed["namc_code"] == SANITY_CODE]["tm2_ref"].tolist()
    print(f"  {'tm2_ref (seed)':18} = {tm2_for_sanity[0] if tm2_for_sanity else '(none)'}")


def main(argv: Iterable[str] | None = None) -> None:
    data_dir = resolve_data_dir()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--xls", type=Path, default=None, help="path to the .xls export (default: the one .xls in the data dir)")
    parser.add_argument("--out-dir", type=Path, default=data_dir / "derived", help="where to write the CSVs")
    args = parser.parse_args(list(argv) if argv is not None else None)

    workbook = args.xls or find_workbook(data_dir)
    print(f"reading  : {workbook}")

    frame = pd.read_excel(workbook, engine="xlrd")
    frame.columns = [str(column).strip() for column in frame.columns]
    require_columns(frame)
    print(f"columns  : {list(frame.columns)}")

    records = build_records(frame)

    leaves = pd.DataFrame([r for r in records if r["kind"] == "leaf"])
    before = len(leaves)
    # "One row per leaf code": the export repeats 17 codes, mostly duplicates that differ only
    # by a non-breaking space, so keep the first occurrence of each.
    leaves = leaves.drop_duplicates(subset="namc_code", keep="first")
    dropped = before - len(leaves)

    clean = leaves[CLEAN_COLUMNS]
    seed = leaves[leaves["tm2_ref"].notna()][SEED_COLUMNS]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    clean_path = args.out_dir / "namc_clean.csv"
    seed_path = args.out_dir / "namc_tm2_seed.csv"
    clean.to_csv(clean_path, index=False, encoding="utf-8")
    seed.to_csv(seed_path, index=False, encoding="utf-8")
    print(f"wrote    : {clean_path}")
    print(f"wrote    : {seed_path}\n")

    summarize(records, clean, seed, dropped)


if __name__ == "__main__":
    main()
