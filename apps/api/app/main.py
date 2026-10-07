from fastapi import FastAPI

app = FastAPI(
    title="IncidentOps AI",
    version="0.0.1",
)


@app.get("/health")
async def health():
    return {"status": "ok"}