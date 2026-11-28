"""Azure Functions entry point: every /api/* request goes to the FastAPI app.

Static Web Apps managed functions use the classic (function.json) model, so
this one catch-all function hands requests to FastAPI through the ASGI
middleware.
"""

import azure.functions as func

from aig.app import app as fastapi_app

_asgi = func.AsgiMiddleware(fastapi_app)


async def main(req: func.HttpRequest, context: func.Context) -> func.HttpResponse:
    return await _asgi.handle_async(req, context)
