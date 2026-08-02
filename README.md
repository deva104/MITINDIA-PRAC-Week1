# NAMASTE ↔ ICD-11 Terminology Microservice

FHIR R4 terminology microservice integrating NAMASTE codes with WHO ICD-11 TM2 + Biomedicine,
for India's 2016 EHR Standards. SIH problem SIH25026.

The authoritative project facts (service names, ports, endpoints, canonical system URIs, demo
anchor code) live in [`docs/CONTEXT.md`](docs/CONTEXT.md). Treat that file as the single source
of truth; do not duplicate those values elsewhere.

## Layout

```
api/                FastAPI service (Python 3.12)
  app/main.py       ASGI entrypoint: /health and the /spike connectivity probe
  scripts/          one-off verification scripts
  requirements.txt  pinned dependencies
  Dockerfile        container image
data/namaste/       NAMASTE CSV export drop point (git-ignored except .gitkeep)
docs/CONTEXT.md     project facts / source of truth
```

## Phase 0

Scaffolding only — no business logic yet. Phase 0 covers the repository skeleton and the shared
context document so that later phases have a fixed set of facts to build against.

Done in Phase 0:

- [x] Repository layout (`api/`, `data/namaste/`, `docs/`)
- [x] FastAPI service skeleton with a `/health` endpoint, pinned `requirements.txt`, and a
      Python 3.12 `Dockerfile`
- [x] `docs/CONTEXT.md` recording the services, ports, key endpoints, canonical system URIs,
      and the verified demo anchor code
- [x] `.gitignore` covering Python artifacts and data exports
- [x] `docker-compose.yml` wiring `db`, `who-icd`, `hapi-fhir`, and `api` on one bridge network
- [x] `GET /spike`: one hard-coded dual-coded diagnosis whose ICD-11 biomedicine display is
      fetched live from the WHO container, proving connectivity
- [x] `api/scripts/who_probe.py`: confirms TM2 and biomedicine codes resolve against the
      loaded release via the WHO native API
- [x] `api/scripts/hapi_smoke_test.py`: proves a dual-coded `Condition` round-trips through HAPI
- [x] `api/scripts/parse_namaste.py`: read-only recon over the NAMASTE .xls export, writing
      `data/namaste/derived/namc_clean.csv` (2749 leaf codes) and `namc_tm2_seed.csv`
      (767 NAMASTE→TM2 pairs harvested from the packed `NAMC_CODE` column)

Not yet done (later phases):

- NAMASTE CSV ingestion and `CodeSystem` generation
- WHO ICD-11 lookup client and NAMASTE → ICD-11 `ConceptMap`
- FHIR `$lookup` / `$translate` operations and HAPI persistence
- Retry-with-backoff around the WHO and HAPI clients (both are slow to become ready)

### Run the whole stack

```bash
docker compose pull        # first run pulls the large WHO ICD-11 image; expect several minutes
docker compose up -d
docker compose ps
```

Only `db` has a healthcheck, so `api` waits for Postgres but merely for the *start* of
`who-icd` and `hapi-fhir`. Those two need minutes more before they serve traffic — the WHO
container loads the ICD-11 release and HAPI runs its schema migrations — so the API's HTTP
clients are responsible for retrying with backoff. Confirm WHO is up by opening
`:8081/swagger/index.html` in a browser.

Endpoints once running: API on `:8000`, HAPI FHIR on `:8080/fhir`, WHO ICD-11 Swagger on
`:8081/swagger/index.html`, Postgres on `:5432`.

### Verify the wiring

```bash
curl http://localhost:8000/spike                                  # live WHO lookup of FA01.0
docker compose exec api python scripts/who_probe.py               # SP12 / SP9Y / FA01.0 resolve
docker compose exec api python scripts/hapi_smoke_test.py         # Condition round trip in HAPI
docker compose exec api python scripts/parse_namaste.py           # NAMASTE .xls -> derived CSVs
```

The parser expects exactly one `.xls` in `data/namaste/`, reads it without modifying it, and
writes only into `data/namaste/derived/`. Both the export and the derived CSVs are git-ignored.

`/spike` answers 503 while the WHO container is still loading the release; retry until it
returns the FA01.0 display.

The WHO container is used only through its **native** API (`/icd/release/11/{release}/mms/...`).
Its `/fhir` endpoints are disabled by default and are pre-release, FHIR R5, and pinned to a
different classification release, so this service — not the container — is the FHIR R4 layer.
See `docs/CONTEXT.md` for the two-hop lookup (`codeinfo` → `stemId` → `title`).

### Run the API locally

```bash
cd api
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Then check `http://localhost:8000/health` and the interactive docs at `http://localhost:8000/docs`.

### Build the image

```bash
docker build -t namaste-icd-api ./api
docker run --rm -p 8000:8000 namaste-icd-api
```
