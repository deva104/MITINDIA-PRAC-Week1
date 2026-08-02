#!/usr/bin/env python
"""Prove a dual-coded Condition survives a round trip through HAPI FHIR.

    docker compose exec api python scripts/hapi_smoke_test.py

POSTs one Condition carrying the three codings from /spike, then reads it back and prints
code.coding so we can see the codes were stored, not silently dropped. Exits non-zero and
dumps the response body on any unexpected status.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

# Reuse the canonical system URIs from the app rather than restating them here; docs/CONTEXT.md
# is the source of truth and one copy in the codebase is enough.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.main import DEMO_ANCHOR_CODE, ICD11_MMS_SYSTEM, NAMASTE_SYSTEM  # noqa: E402

FHIR_JSON = "application/fhir+json"
SUBJECT_REFERENCE = "Patient/example"
TIMEOUT = httpx.Timeout(30.0, connect=5.0)


def hapi_base() -> str:
    base = os.getenv("HAPI_BASE", "").rstrip("/")
    if not base:
        sys.exit("HAPI_BASE is not set (expected e.g. http://hapi-fhir:8080/fhir)")
    return base


def build_condition() -> dict[str, Any]:
    return {
        "resourceType": "Condition",
        # clinicalStatus keeps the resource valid under Condition's con-3 invariant.
        "clinicalStatus": {
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                    "code": "active",
                    "display": "Active",
                }
            ]
        },
        "code": {
            "text": "Sandhigata Vata (osteoarthritis of knee)",
            "coding": [
                # PLACEHOLDER: illustrative NAMASTE code, not a real NAMC identifier.
                {
                    "system": NAMASTE_SYSTEM,
                    "code": "NAMC-PLACEHOLDER-001",
                    "display": "Sandhigata Vata",
                },
                # PLACEHOLDER: illustrative ICD-11 TM2 code; real TM2 codes live in MMS too.
                {
                    "system": ICD11_MMS_SYSTEM,
                    "code": "TM2-PLACEHOLDER",
                    "display": "Vata joint disorder (TM2 placeholder)",
                },
                # The verified anchor. /spike tags this with a non-FHIR "source" key; omitted
                # here because a real server would reject the unknown element.
                {
                    "system": ICD11_MMS_SYSTEM,
                    "code": DEMO_ANCHOR_CODE,
                    "display": "Primary osteoarthritis of knee",
                },
            ],
        },
        "subject": {"reference": SUBJECT_REFERENCE},
    }


def fail(message: str, response: httpx.Response) -> None:
    print(f"\nFAILED: {message}", file=sys.stderr)
    print(f"  {response.request.method} {response.request.url} -> HTTP {response.status_code}", file=sys.stderr)
    print(f"  response body:\n{response.text}", file=sys.stderr)
    sys.exit(1)


def ensure_subject(client: httpx.Client, base: str) -> None:
    """Upsert Patient/example.

    HAPI enforces referential integrity on write by default, so posting a Condition that
    points at a missing Patient is rejected. PUT to a known id is idempotent, so repeated
    runs of this script stay clean.
    """
    response = client.put(
        f"{base}/{SUBJECT_REFERENCE}",
        content=json.dumps({"resourceType": "Patient", "id": "example"}),
        headers={"Content-Type": FHIR_JSON, "Accept": FHIR_JSON},
    )
    if response.status_code not in (200, 201):
        fail(f"could not upsert {SUBJECT_REFERENCE}", response)
    print(f"subject      : {SUBJECT_REFERENCE} ready (HTTP {response.status_code})")


def main() -> None:
    base = hapi_base()
    print(f"hapi base    : {base}")

    with httpx.Client(timeout=TIMEOUT) as client:
        ensure_subject(client, base)

        created = client.post(
            f"{base}/Condition",
            content=json.dumps(build_condition()),
            headers={"Content-Type": FHIR_JSON, "Accept": FHIR_JSON},
        )
        if created.status_code != 201:
            fail("expected HTTP 201 from POST /Condition", created)

        body = created.json()
        resource_id = body.get("id")
        if not resource_id:
            fail("POST succeeded but the response carried no resource id", created)

        print(f"POST status  : {created.status_code} {created.reason_phrase}")
        print(f"resource id  : {resource_id}")
        print(f"location     : {created.headers.get('Location', '(none)')}")

        fetched = client.get(
            f"{base}/Condition/{resource_id}",
            headers={"Accept": FHIR_JSON},
        )
        if fetched.status_code != 200:
            fail(f"could not read back Condition/{resource_id}", fetched)

        codings = (fetched.json().get("code") or {}).get("coding") or []
        print(f"GET status   : {fetched.status_code} {fetched.reason_phrase}")
        print(f"code.coding  : {len(codings)} coding(s) round-tripped")
        print(json.dumps(codings, indent=2))

        if len(codings) != 3:
            sys.exit(f"expected 3 codings back, got {len(codings)}")

    print("\nOK: dual-coded Condition round-tripped through HAPI.")


if __name__ == "__main__":
    main()
