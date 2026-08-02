# AYUSH EMR demo UI

A judge-facing single-screen demo EMR: an AYUSH clinician searches for a diagnosis, sees the
NAMASTE / ICD-11 TM2 / ICD-11 Biomedicine dual coding, confirms the biomedicine code, and records a
FHIR Condition. Vite + React + Tailwind. All logic lives in one component, `src/App.jsx`.

## Run it

The terminology API must be up first (from the repository root):

```bash
docker compose up -d
```

Then:

```bash
cd web
npm install
npm run dev        # http://localhost:5173
```

## CORS: why requests go to /api

The FastAPI service sends no `Access-Control-Allow-Origin` header, so a browser on
`http://localhost:5173` is not allowed to read a response from `http://localhost:8000`. Rather than
change the backend, `vite.config.js` proxies `/api/*` to `http://localhost:8000`, so every request
the browser makes is same-origin. `API_BASE` in `src/App.jsx` is therefore `/api` by default.

To call the backend directly instead, add `CORSMiddleware` to FastAPI and run:

```bash
VITE_API_BASE=http://localhost:8000 npm run dev
```

## What the screen does

| Step | Backend call | Notes |
| --- | --- | --- |
| Autocomplete | `GET /ValueSet/$expand?filter=&count=12` | 250 ms debounce. Rows badged NAMASTE (teal), ICD-11 TM2 (amber), ICD-11 Bio (blue) from the expansion-source extension. |
| Select a NAMASTE row | `GET /ConceptMap/$translate?code=` | Curated TM2 target plus live WHO biomedicine candidates with confidence. |
| Record diagnosis | `POST /diagnosis` | Sends the chosen biomedicine code, or none to record it as pending. |
| Problem list | `GET /Patient/{id}/problem-list` | Loaded after a successful save. |

Clicking an **ICD-11 Bio** row in the dropdown attaches that code as the biomedicine candidate. This
matters because `$translate` ranks candidates from the concept's English display, which for `AAE-16`
returns `FA0Z` / `FA05` / `FA8Z` — the more specific `FA01.0` "Primary osteoarthritis of knee" is
only reachable by searching for it. TM2 rows are not clickable: TM2 is linked automatically from the
curated NAMASTE mapping, never chosen by hand.

## Honesty notes shown in the UI

- The ABHA number, the consent step and the practitioner identity are simulated; there is no real
  ABDM authentication or consent artefact.
- Biomedicine mapping is many-to-many. Every candidate is shown with its confidence and the
  clinician confirms one; the service never silently picks one, and never invents a code. Choosing
  none records NAMASTE + TM2 with biomedicine marked pending.
- A concept with no curated TM2 mapping cannot be recorded, because `POST /diagnosis` requires a
  TM2 code. The UI says so rather than fabricating a mapping.

## Demo path that shows everything

1. Type `osteoarthritis of knee`.
2. Click the NAMASTE row `AAE-16`.
3. Click the `FA01.0` ICD-11 Bio row in the dropdown to attach it.
4. Record diagnosis, then open **View raw FHIR** to show one `code.coding` array with three codings.
