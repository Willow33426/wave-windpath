import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

load_dotenv(Path(__file__).with_name(".env"))

app = FastAPI(title="Wave 바람길")


@app.get("/", response_class=HTMLResponse)
async def index():
    return """<!doctype html>
<html lang="ko">
<head>
    <meta charset="utf-8">
    <title>Wave 바람길</title>
</head>
<body>
    <h1>Wave 바람길</h1>
    <p>바람 데이터로 잇는 우리 동네 공기 예보 × AI 데이터센터 RE100 시뮬레이터 (개발 중)</p>
</body>
</html>"""


@app.get("/api/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.getenv("INTERNAL_HOST", "127.0.0.1"),
        port=int(os.getenv("INTERNAL_PORT", "8000")),
    )
