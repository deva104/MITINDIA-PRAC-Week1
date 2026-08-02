## Project
FHIR R4 terminology microservice integrating NAMASTE codes with WHO ICD-11 TM2 + Biomedicine, for India's 2016 EHR Standards. SIH problem SIH25026.

## Services (docker compose, internal network names)
- db: postgres:16
- who-icd: image `whoicd/icd-api`, needs env `acceptLicense=true`, listens on container port 80, mapped to host 8081. NO auth when run locally. Swagger at /swagger/index.html. Default bundled release: 2026-01 English.
- hapi-fhir: image `hapiproject/hapi:latest`, listens on container port 8080 (host 8080), FHIR base path is /fhir.
- api: our FastAPI service, host port 8000.

## Key endpoints
- WHO code lookup (native API, PRIMARY and only supported path): GET {WHO_BASE}/icd/release/11/{ICD_RELEASE_ID}/mms/codeinfo/{code}  with headers `API-Version: v2`, `Accept: application/json`, `Accept-Language: en`
- codeinfo does NOT return a title. It returns an entity URI: follow `stemId` with the same headers and read `title.@value`. Beware `@id` on a codeinfo response is the codeinfo request URL itself, not the entity. `stemId` points at id.who.int, so swap the host for {WHO_BASE} to stay on the local container. Residual codes resolve to a `/other` or `/unspecified` entity URI, which is normal.
  - Example: codeinfo/FA01.0 -> stemId .../mms/1196073446 -> title "Primary osteoarthritis of knee".
- Do NOT use the WHO container's /fhir endpoints. They are disabled by default (HTTP 500), and even when enabled they are pre-release, FHIR R5, and pinned to the 2025-01 classification while we load 2026-01. The WHO container is a data source reached through its native API; OUR FastAPI service is the FHIR R4 layer.
- HAPI: POST/GET {hapi}/fhir/... standard FHIR R4 REST

## Verified anchor code
ICD-11 biomedicine FA01.0 = "Primary osteoarthritis of knee". Use this as the demo anchor.

## Canonical system URIs
- NAMASTE: https://namaste.ayush.gov.in/fhir/CodeSystem/namaste
- ICD-11 MMS (TM2 + biomedicine live here): http://id.who.int/icd/release/11/mms
