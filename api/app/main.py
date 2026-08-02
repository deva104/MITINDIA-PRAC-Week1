"""Phase 0 connectivity spike for the NAMASTE <-> ICD-11 terminology service.

/spike returns one hard-coded dual-coded diagnosis, except for the ICD-11 biomedicine
display, which is fetched live from the WHO container to prove the wiring works.
Facts (system URIs, endpoints, anchor code) come from docs/CONTEXT.md.

The WHO container is a data source reached through its native API only; its /fhir endpoints
are disabled, pre-release, R5, and pinned to a different release. This service is the FHIR
R4 layer.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("namaste.spike")

# Canonical system URIs (docs/CONTEXT.md). These identify code systems in FHIR payloads and
# are not network addresses, so they are constants rather than configuration.
NAMASTE_SYSTEM = "https://namaste.ayush.gov.in/fhir/CodeSystem/namaste"
ICD11_MMS_SYSTEM = "http://id.who.int/icd/release/11/mms"

# Verified demo anchor: ICD-11 biomedicine FA01.0 = "Primary osteoarthritis of knee".
DEMO_ANCHOR_CODE = "FA01.0"

# Every address is configuration so the same image runs under docker compose and locally.
WHO_BASE = os.getenv("WHO_BASE", "").rstrip("/")
HAPI_BASE = os.getenv("HAPI_BASE", "").rstrip("/")  # unused until FHIR persistence lands
ICD_RELEASE_ID = os.getenv("ICD_RELEASE_ID", "")

TIMEOUT = httpx.Timeout(10.0, connect=3.0)

# Required by the WHO native API; without API-Version it answers with a different shape.
NATIVE_HEADERS = {
    "API-Version": "v2",
    "Accept": "application/json",
    "Accept-Language": "en",
}


class WhoLookupError(RuntimeError):
    """The WHO native lookup could not resolve a display for a code."""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        app.state.http = client
        logger.info("who_base=%r hapi_base=%r icd_release_id=%r", WHO_BASE, HAPI_BASE, ICD_RELEASE_ID)
        yield


app = FastAPI(
    title="NAMASTE / ICD-11 Terminology Service",
    description="FHIR R4 terminology microservice for NAMASTE <-> ICD-11 TM2 + Biomedicine.",
    version="0.0.0",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict[str, str]:
    # Deliberately does not touch WHO/HAPI/Postgres: the container is healthy as soon as it
    # serves traffic, even while its slow-starting dependencies are still warming up.
    return {"status": "ok"}


def _label(value: Any) -> str | None:
    """Read an ICD-11 label, which is either a plain string or {"@value": "..."}."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        inner = value.get("@value")
        if isinstance(inner, str) and inner.strip():
            return inner.strip()
    return None


def local_entity_url(absolute_uri: str) -> str:
    """Re-point an id.who.int URI at our own container.

    Native responses carry absolute URIs on id.who.int. Following one verbatim would leave the
    local container and hit the public API, so keep only the path.
    """
    return f"{WHO_BASE}{urlsplit(absolute_uri).path}"


def codeinfo_url(code: str) -> str:
    return f"{WHO_BASE}/icd/release/11/{ICD_RELEASE_ID}/mms/codeinfo/{code}"


async def who_lookup(client: httpx.AsyncClient, code: str) -> str:
    """Resolve an ICD-11 MMS code to its title using the WHO native API.

    Two hops, because codeinfo carries no title: codeinfo -> follow `stemId` -> read `title`.
    Raises WhoLookupError on any failure.
    """
    if not WHO_BASE:
        raise WhoLookupError("WHO_BASE is not set")
    if not ICD_RELEASE_ID:
        raise WhoLookupError("ICD_RELEASE_ID is not set")

    try:
        info = await client.get(codeinfo_url(code), headers=NATIVE_HEADERS)
        if info.status_code != 200:
            raise WhoLookupError(f"codeinfo for {code} returned HTTP {info.status_code}")

        payload = info.json()
        if not isinstance(payload, dict):
            raise WhoLookupError(f"codeinfo for {code} returned a non-object body")

        # Follow stemId, not @id: on a codeinfo response @id is the codeinfo URL itself, so
        # following it would just loop back here.
        stem_id = payload.get("stemId")
        if not isinstance(stem_id, str) or not stem_id:
            raise WhoLookupError(f"codeinfo for {code} carried no stemId")

        entity_url = local_entity_url(stem_id)
        entity = await client.get(entity_url, headers=NATIVE_HEADERS)
        if entity.status_code != 200:
            raise WhoLookupError(f"entity {entity_url} returned HTTP {entity.status_code}")

        body = entity.json()
        title = _label(body.get("title")) if isinstance(body, dict) else None
        if not title:
            raise WhoLookupError(f"entity {entity_url} carried no title")
    except httpx.TimeoutException as exc:
        raise WhoLookupError(f"WHO lookup of {code} timed out") from exc
    except httpx.RequestError as exc:
        # Expected while the WHO container is still loading the ICD-11 release.
        raise WhoLookupError(f"WHO lookup of {code} could not connect: {exc}") from exc
    except ValueError as exc:
        raise WhoLookupError(f"WHO lookup of {code} returned malformed JSON") from exc

    logger.info("resolved %s to %r via native codeinfo -> %s", code, title, stem_id)
    return title


@app.get("/spike")
async def spike(request: Request) -> dict[str, Any]:
    """One dual-coded diagnosis: NAMASTE + ICD-11 TM2 + ICD-11 biomedicine.

    Only the biomedicine display is live. The NAMASTE and TM2 codings are illustrative
    placeholders, replaced in later phases by the real NAMASTE CSV export and the TM2
    concepts pulled from the WHO release.
    """
    try:
        live_display = await who_lookup(request.app.state.http, DEMO_ANCHOR_CODE)
    except WhoLookupError as exc:
        # 503, not 500: the usual cause is the WHO container still warming up.
        raise HTTPException(status_code=503, detail=f"WHO ICD-11 lookup unavailable -- {exc}") from exc

    return {
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
            # Real code, display fetched live. `source` is not a FHIR Coding element -- it is a
            # spike-only marker and becomes an extension (or is dropped) once this returns
            # actual FHIR resources.
            {
                "system": ICD11_MMS_SYSTEM,
                "code": DEMO_ANCHOR_CODE,
                "display": live_display,
                "source": "who-live",
            },
        ],
    }
