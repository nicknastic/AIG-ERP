"""Azure Functions entry point: serves the FastAPI app for every /api request."""

import azure.functions as func

from aig.app import app as fastapi_app

app = func.AsgiFunctionApp(app=fastapi_app, http_auth_level=func.AuthLevel.ANONYMOUS)
