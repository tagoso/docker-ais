# health_server.py
from fastapi import FastAPI
import uvicorn

app = FastAPI()

@app.get("/health")
def read_health():
    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run("health_server:app", host="0.0.0.0", port=8080)
