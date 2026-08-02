from fastapi import FastAPI

app = FastAPI(
    title="NAMASTE / ICD-11 Terminology Service",
    description="FHIR R4 terminology microservice for NAMASTE <-> ICD-11 TM2 + Biomedicine.",
    version="0.0.0",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
