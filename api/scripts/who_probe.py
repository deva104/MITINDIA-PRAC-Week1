#!/usr/bin/env python
"""Probe the WHO container's native API for the codes our TM2 seed depends on.

    docker compose exec api python scripts/who_probe.py

For each test code: GET codeinfo, print the HTTP status, and on 200 follow the entity URI and
print the code and title. Confirms the TM2 refs harvested from the NAMASTE export actually
resolve against the release loaded in the container. Exits non-zero if any code fails.

Deliberately reads the raw JSON rather than reusing the app's parsing, so a change in the WHO
response shape shows up here as a failure instead of being absorbed.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.main import (  # noqa: E402
    ICD_RELEASE_ID,
    NATIVE_HEADERS,
    WHO_BASE,
    codeinfo_url,
    local_entity_url,
)

# SP12 and SP9Y come from the NAMASTE TM2 seed (SP9Y is a residual "other specified" code,
# whose entity URI ends in /other); FA01.0 is the biomedicine demo anchor.
TEST_CODES = ["SP12", "SP9Y", "FA01.0"]
TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def title_of(body: Any) -> str | None:
    if not isinstance(body, dict):
        return None
    title = body.get("title")
    if isinstance(title, dict):
        title = title.get("@value")
    return title.strip() if isinstance(title, str) and title.strip() else None


def probe(client: httpx.Client, code: str) -> bool:
    print(f"\n{code}")

    info = client.get(codeinfo_url(code), headers=NATIVE_HEADERS)
    print(f"  codeinfo : HTTP {info.status_code}  {codeinfo_url(code)}")
    if info.status_code != 200:
        print(f"  FAIL {code}: codeinfo returned HTTP {info.status_code}")
        return False

    payload = info.json()
    # @id on a codeinfo response is the codeinfo URL itself; stemId is the entity.
    stem_id = payload.get("stemId")
    print(f"  stemId   : {stem_id or '(missing)'}")
    if not stem_id:
        print(f"  FAIL {code}: codeinfo carried no stemId")
        return False

    entity_url = local_entity_url(stem_id)
    entity = client.get(entity_url, headers=NATIVE_HEADERS)
    print(f"  entity   : HTTP {entity.status_code}  {entity_url}")
    if entity.status_code != 200:
        print(f"  FAIL {code}: entity returned HTTP {entity.status_code}")
        return False

    body = entity.json()
    title = title_of(body)
    print(f"  resolved : {{'code': {body.get('code')!r}, 'title': {title!r}}}")
    if not title:
        print(f"  FAIL {code}: entity carried no title")
        return False

    print(f"  PASS {code}")
    return True


def main() -> None:
    if not WHO_BASE:
        sys.exit("WHO_BASE is not set (expected e.g. http://who-icd:80)")
    if not ICD_RELEASE_ID:
        sys.exit("ICD_RELEASE_ID is not set (expected e.g. 2026-01)")

    print(f"who base : {WHO_BASE}")
    print(f"release  : {ICD_RELEASE_ID}")
    print(f"headers  : {NATIVE_HEADERS}")

    results: dict[str, bool] = {}
    with httpx.Client(timeout=TIMEOUT) as client:
        for code in TEST_CODES:
            try:
                results[code] = probe(client, code)
            except httpx.HTTPError as exc:
                print(f"  FAIL {code}: {type(exc).__name__}: {exc}")
                results[code] = False
            except ValueError:
                print(f"  FAIL {code}: response was not valid JSON")
                results[code] = False

    passed = sum(results.values())
    print(f"\n{passed}/{len(results)} codes resolved")
    for code, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {code}")

    if passed != len(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
