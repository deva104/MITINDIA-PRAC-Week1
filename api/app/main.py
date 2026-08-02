"""Phase 0 connectivity spike for the NAMASTE <-> ICD-11 terminology service.

/spike returns one hard-coded dual-coded diagnosis, except for the ICD-11 biomedicine
display, which is fetched live from the WHO container to prove the wiring works.
Facts (system URIs, endpoints, anchor code) come from docs/CONTEXT.md.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Literal
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

LookupPath = Literal["fhir", "native"]


class WhoLookupError(RuntimeError):
    """Neither the FHIR nor the native WHO lookup could resolve a display."""


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


def _display_from_parameters(payload: Any) -> str | None:
    """Pull the `display` value out of a FHIR Parameters resource."""
    if not isinstance(payload, dict):
        return None
    for parameter in payload.get("parameter") or []:
        if isinstance(parameter, dict) and parameter.get("name") == "display":
            value = parameter.get("valueString")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _label(value: Any) -> str | None:
    """Read an ICD-11 label, which is either a plain string or {"@value": "..."}."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        inner = value.get("@value")
        if isinstance(inner, str) and inner.strip():
            return inner.strip()
    return None


def _local_url(absolute_uri: str) -> str:
    """Re-point an id.who.int URI at our own container.

    Native responses carry absolute `stemId` URIs on id.who.int. Following one verbatim would
    leave the local container and hit the public API, so keep only the path.
    """
    return f"{WHO_BASE}{urlsplit(absolute_uri).path}"


async def _lookup_via_fhir(client: httpx.AsyncClient, code: str) -> str | None:
    response = await client.get(
        f"{WHO_BASE}/fhir/CodeSystem/$lookup",
        params={"system": ICD11_MMS_SYSTEM, "code": code},
        headers={"Accept": "application/fhir+json, application/json"},
    )
    if response.status_code != 200:
        logger.warning("WHO FHIR $lookup for %s returned HTTP %s", code, response.status_code)
        return None
    return _display_from_parameters(response.json())


async def _lookup_via_native(client: httpx.AsyncClient, code: str) -> str | None:
    if not ICD_RELEASE_ID:
        logger.error("ICD_RELEASE_ID is unset, cannot use the native WHO endpoint")
        return None

    headers = {"API-Version": "v2", "Accept": "application/json", "Accept-Language": "en"}
    response = await client.get(
        f"{WHO_BASE}/icd/release/11/{ICD_RELEASE_ID}/mms/codeinfo/{code}",
        headers=headers,
    )
    if response.status_code != 200:
        logger.warning("WHO codeinfo for %s returned HTTP %s", code, response.status_code)
        return None

    payload = response.json()
    if not isinstance(payload, dict):
        return None

    title = _label(payload.get("title"))
    if title:
        return title

    # codeinfo often answers with only a stemId pointing at the entity that owns the title,
    # so spend one more request to resolve it.
    stem_id = payload.get("stemId")
    if not isinstance(stem_id, str) or not stem_id:
        return None

    entity = await client.get(_local_url(stem_id), headers=headers)
    if entity.status_code != 200:
        logger.warning("WHO entity %s returned HTTP %s", stem_id, entity.status_code)
        return None
    body = entity.json()
    return _label(body.get("title")) if isinstance(body, dict) else None


async def who_lookup(client: httpx.AsyncClient, code: str) -> tuple[str, LookupPath]:
    """Resolve an ICD-11 MMS display, preferring the FHIR endpoint over the native one.

    Returns the display and which path produced it. Raises WhoLookupError if both fail.
    """
    if not WHO_BASE:
        raise WhoLookupError("WHO_BASE is not set")

    failures: list[str] = []

    for path, attempt in (("fhir", _lookup_via_fhir), ("native", _lookup_via_native)):
        try:
            display = await attempt(client, code)
        except httpx.TimeoutException:
            logger.warning("WHO %s lookup for %s timed out", path, code)
            failures.append(f"{path}: timeout")
            continue
        except httpx.RequestError as exc:
            # Expected while the WHO container is still loading the ICD-11 release.
            logger.warning("WHO %s lookup for %s failed to connect: %s", path, code, exc)
            failures.append(f"{path}: {type(exc).__name__}")
            continue
        except ValueError:
            logger.warning("WHO %s lookup for %s returned malformed JSON", path, code)
            failures.append(f"{path}: invalid JSON")
            continue

        if display:
            logger.info("resolved %s to %r via the %s path", code, display, path)
            return display, path  # type: ignore[return-value]
        failures.append(f"{path}: no display in response")

    raise WhoLookupError(f"lookup of {code} failed ({'; '.join(failures)})")


@app.get("/spike")
async def spike(request: Request) -> dict[str, Any]:
    """One dual-coded diagnosis: NAMASTE + ICD-11 TM2 + ICD-11 biomedicine.

    Only the biomedicine display is live. The NAMASTE and TM2 codings are illustrative
    placeholders, replaced in later phases by the real NAMASTE CSV export and the TM2
    concepts pulled from the WHO release.
    """
    try:
        live_display, _ = await who_lookup(request.app.state.http, DEMO_ANCHOR_CODE)
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
