"""One process, one port: the product API under /api and the built console at /.

The console talks to `/api` (as it does behind nginx or the Vite proxy), so mounting the API
there keeps the browser code identical in every setup.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles


class SPAFiles(StaticFiles):
    """Static files with a fallback to index.html, so client-side routes survive a refresh."""

    async def get_response(self, path: str, scope):  # type: ignore[no-untyped-def]
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            return FileResponse(Path(self.directory) / "index.html")  # type: ignore[arg-type]


def build(dist: Path | None) -> FastAPI:
    from app.main import app as api

    root = FastAPI(title="Validate demo", docs_url=None, redoc_url=None, openapi_url=None)
    root.mount("/api", api)
    if dist and (dist / "index.html").exists():
        root.mount("/", SPAFiles(directory=str(dist), html=True), name="console")
    else:

        @root.get("/")
        def no_ui() -> dict[str, str]:
            return {
                "message": "The console is not built. Run `npm ci && npm run build` in frontend/, "
                "then restart. The API is available at /api/docs."
            }

    return root
