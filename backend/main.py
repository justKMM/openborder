"""OpenBorder web API: the CLI's engine behind HTTP, for a GUI. Run it from backend/: `uvicorn main:app --reload`.

Stateless apart from the plan folders in store.py. Credentials come in per request as headers (credentials.py)
and are never stored or logged. Interactive docs: /docs.
"""

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cli"))  # the engine is not pip-installed

import credentials  # noqa: E402
import errors  # noqa: E402
import store  # noqa: E402
from credentials import Dash0Creds, DatadogCreds  # noqa: E402
from errors import ApiError  # noqa: E402
from fastapi import APIRouter, Depends, FastAPI, Response  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from openborder import __version__, service  # noqa: E402
from openborder import explain as explainer  # noqa: E402
from openborder.apply import load_plan  # noqa: E402
from openborder.dash0_client import Dash0Client, Dash0Error  # noqa: E402
from openborder.sources.datadog.files import load_documents  # noqa: E402
from schemas import (  # noqa: E402
    ApplyRequest,
    ApplyResponse,
    ExplainPlanRequest,
    ExplainQueryRequest,
    PlanRequest,
    PlanResponse,
)

router = APIRouter(prefix="/api")

# Routes are plain `def`: FastAPI runs them in a thread pool, which is what the blocking engine and HTTP clients need.


def _plan_folder(plan_id: str) -> Path:
    folder = store.get(plan_id)
    if folder is None:
        raise ApiError(404, "plan_not_found", "No such plan. It may have expired; create it again.")
    return folder


def _read_plan(folder: Path) -> dict:
    return json.loads((folder / "plan.json").read_bytes())


@router.get("/health")
def health() -> dict:
    # Only "is the docker command installed": whether its engine is running shows up in a plan's validation.promtool.
    return {"status": "ok", "version": __version__, "docker_installed": shutil.which("docker") is not None}


@router.get("/discover")
def discover(
    creds: Annotated[DatadogCreds, Depends(credentials.required_datadog)], name: str | None = None, tag: str | None = None
) -> dict:
    """List the dashboards and monitors of the source Datadog org (read-only). `tag` filters monitors."""
    found = service.run_discover(creds.api_key, creds.app_key, creds.site, name, tag)
    return service.discovery_document(found)


@router.post("/plans", status_code=201, response_model=PlanResponse)
def create_plan(body: PlanRequest, creds: Annotated[DatadogCreds | None, Depends(credentials.optional_datadog)]) -> dict:
    """Translate dashboards and monitors and store the result for review. Nothing is imported anywhere."""
    if body.source == "datadog":
        datadog = credentials.required_datadog(creds)  # headers are optional only for uploads
        inputs = service.fetch_from_datadog(
            datadog.api_key,
            datadog.app_key,
            datadog.site,
            dashboard_ids=body.dashboard_ids,
            monitor_tag=body.monitor_tag,
            dashboards=body.include_dashboards,
            monitors=body.include_monitors,
        )
    else:
        loaded = load_documents([(f.name, f.content) for f in body.files])
        inputs = service.Inputs(loaded.dashboards, loaded.monitors, loaded.skipped, {"kind": "datadog", "mode": "upload"})
    inputs = service.select_inputs(
        inputs,
        name=body.name,
        monitor_tag=body.monitor_tag,
        dashboards=body.include_dashboards,
        monitors=body.include_monitors,
    )
    store.prune()
    plan_id, folder = store.create()
    try:
        run = service.run_plan(inputs, folder, enable_approximate=body.enable_approximate, promtool_check=body.promtool)
    except Exception:
        store.delete(plan_id)
        raise
    return {"id": plan_id, "failed": run.failed, "plan": run.document}


@router.get("/plans/{plan_id}", response_model=PlanResponse)
def get_plan(plan_id: str) -> dict:
    document = _read_plan(_plan_folder(plan_id))
    return {"id": plan_id, "failed": service.plan_failed(document), "plan": document}


@router.delete("/plans/{plan_id}", status_code=204)
def delete_plan(plan_id: str) -> Response:
    """Remove a plan and everything stored with it."""
    if not store.delete(plan_id):
        raise ApiError(404, "plan_not_found", "No such plan.")
    return Response(status_code=204)


@router.get("/plans/{plan_id}/artifacts/{path:path}")
def get_artifact(plan_id: str, path: str) -> FileResponse:
    """A file of the plan folder: perses/<name>.json, check-rules.json, migration.log, plan.json or apply.json."""
    folder = _plan_folder(plan_id).resolve()
    target = (folder / path).resolve()
    if not target.is_relative_to(folder) or not target.is_file():
        raise ApiError(404, "not_found", f"The plan has no file {path!r}.")
    media_type = "application/json" if target.suffix == ".json" else "text/plain; charset=utf-8"
    return FileResponse(target, media_type=media_type)


@router.post("/plans/{plan_id}/explain")
def explain_plan(plan_id: str, body: ExplainPlanRequest) -> dict:
    """The rule-by-rule trace of the panels and monitors of a plan whose name matches `find`."""
    document = _read_plan(_plan_folder(plan_id))
    return {"explanations": explainer.find_in_plan(document, body.find)}


@router.post("/explain")
def explain_query(body: ExplainQueryRequest) -> dict:
    """Translate one Datadog query with the bundled mappings and show every rule that ran."""
    if body.monitor:
        return {"explanations": [explainer.explain_monitor_query(body.query)]}
    variables = {name: prefix or None for name, prefix in body.variables.items()}
    return {"explanations": [explainer.explain_query(body.query, variables)]}


@router.post("/plans/{plan_id}/apply", response_model=ApplyResponse)
def apply_plan(plan_id: str, body: ApplyRequest, creds: Annotated[Dash0Creds, Depends(credentials.dash0)]) -> dict:
    """Import a reviewed plan into Dash0. The only endpoint that writes to a customer's account.

    It imports exactly the files `plan` wrote (hashes and structure are re-checked) and refuses a plan that failed
    validation. Importing needs `confirm: true`; `dry_run: true` returns the summary and sends nothing. Every item has
    a stable ID, so running it again overwrites instead of duplicating.
    """
    folder = _plan_folder(plan_id)
    loaded = load_plan(folder)
    try:
        client = Dash0Client(creds.token, creds.api_url, creds.dataset)
    except Dash0Error as e:
        raise ApiError(400, "invalid_dash0_settings", str(e))
    summary = service.apply_summary(loaded, client.api_url, client.dataset)
    if body.dry_run:
        client.close()
        return {"dry_run": True, "summary": summary, "results": None, "failures": 0}
    if not body.confirm:
        client.close()
        raise ApiError(400, "confirmation_required", "Set `confirm` to true to import, or `dry_run` to preview without sending anything.")
    outcome = service.run_apply(loaded, client, folder)
    return {"dry_run": False, "summary": summary, "results": outcome.results, "failures": outcome.failures}


@router.get("/plans/{plan_id}/apply")
def get_apply_result(plan_id: str) -> dict:
    """What the last import of this plan did (the apply.json the CLI writes)."""
    result = _plan_folder(plan_id) / "apply.json"
    if not result.is_file():
        raise ApiError(404, "not_applied", "This plan has not been imported yet.")
    return json.loads(result.read_bytes())


def create_app() -> FastAPI:
    app = FastAPI(
        title="OpenBorder API",
        version=__version__,
        description="Migrate Datadog dashboards and monitors to Dash0. Credentials are sent per request as headers "
        "(X-Datadog-Api-Key, X-Datadog-App-Key, X-Datadog-Site, X-Dash0-Token, X-Dash0-Api-Url, X-Dash0-Dataset) and are never stored.",
    )
    origins = os.environ.get("OPENBORDER_CORS_ORIGINS") or "http://localhost:5173,http://127.0.0.1:5173"
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in origins.split(",") if o.strip()],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=[
            "Content-Type",
            "X-Datadog-Api-Key",
            "X-Datadog-App-Key",
            "X-Datadog-Site",
            "X-Dash0-Token",
            "X-Dash0-Api-Url",
            "X-Dash0-Dataset",
        ],
    )
    errors.install(app)
    app.include_router(router)
    return app


app = create_app()
