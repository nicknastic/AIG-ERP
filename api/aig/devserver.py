"""Run the whole site on your own computer, without Azure:

    cd api && python -m aig.devserver        # then open http://localhost:8000

Files go to ../data (or AIG_LOCAL_ROOT). There is no sign-in in this mode.
"""

import os
from pathlib import Path

import uvicorn
from starlette.staticfiles import StaticFiles

from .app import app as api_app

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
static = StaticFiles(directory=FRONTEND, html=True)


async def site(scope, receive, send):
    if scope["type"] == "http" and scope["path"].startswith("/api"):
        await api_app(scope, receive, send)
    else:
        await static(scope, receive, send)


if __name__ == "__main__":
    os.environ.setdefault("AIG_LOCAL_ROOT", str(Path(__file__).resolve().parents[2] / "data"))
    uvicorn.run(site, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")))
