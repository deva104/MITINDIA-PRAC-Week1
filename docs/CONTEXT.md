## Project
FHIR R4 terminology microservice integrating NAMASTE codes with WHO ICD-11 TM2 + Biomedicine, for India's 2016 EHR Standards. SIH problem SIH25026.

## Services (docker compose, internal network names)
- db: postgres:16
- who-icd: image `whoicd/icd-api`, needs env `acceptLicense=true`, listens on container port 80, mapped to host 8081. NO auth when run locally. Swagger at /swagger/index.html. Default bundled release: 2026-01 English.
- hapi-fhir: image `hapiproject/hapi:latest`, listens on container port 8080 (host 8080), FHIR base path is /fhir.
- api: our FastAPI service, host port 8000.

## Key endpoints
- WHO code lookup (FHIR): GET {who}/fhir/CodeSystem/$lookup?system=http://id.who.int/icd/release/11/mms&code=FA01.0
- WHO code lookup (native fallback): GET {who}/icd/release/11/2026-01/mms/codeinfo/FA01.0  with headers `API-Version: v2`, `Accept: application/json`, `Accept-Language: en`
- HAPI: POST/GET {hapi}/fhir/... standard FHIR R4 REST

## Verified anchor code
ICD-11 biomedicine FA01.0 = "Primary osteoarthritis of knee". Use this as the demo anchor.

## Canonical system URIs
- NAMASTE: https://namaste.ayush.gov.in/fhir/CodeSystem/namaste
- ICD-11 MMS (TM2 + biomedicine live here): http://id.who.int/icd/release/11/mms
