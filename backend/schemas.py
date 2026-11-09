"""Request and response bodies. Credentials are never part of them: they travel as headers (credentials.py)."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a misspelt field is an error, not a silently ignored option


class UploadedFile(Strict):
    name: str = Field(min_length=1, max_length=255, description="File name; only used to say which file could not be read.")
    content: Any = Field(description="The parsed JSON of a Datadog dashboard or monitor export (an object, or a list of them).")


class PlanRequest(Strict):
    source: Literal["datadog", "upload"] = Field(
        "datadog", description="`datadog`: pull from the org the X-Datadog-* headers point at. `upload`: translate `files`."
    )
    files: list[UploadedFile] = Field([], description="Exported Datadog JSON; used when source is `upload`.")
    dashboard_ids: list[str] = Field([], description="Only these dashboards (source `datadog`). Empty: all.")
    monitor_tag: str | None = Field(None, description="Only monitors with this tag, e.g. integration:redisdb.")
    name: str | None = Field(None, description="Only dashboards and monitors whose name contains this text.")
    include_dashboards: bool = True
    include_monitors: bool = True
    enable_approximate: bool = Field(False, description="Import approximate (~) alerts enabled instead of disabled.")
    promtool: bool = Field(True, description="Syntax-check the PromQL with promtool. Needs Docker on the server; slow on first use.")

    @model_validator(mode="after")
    def _upload_needs_files(self):
        if self.source == "upload" and not self.files:
            raise ValueError("source 'upload' needs at least one entry in `files`")
        return self


class PlanResponse(BaseModel):
    id: str = Field(description="Use it in every /api/plans/{id}/... call. Plans expire after a day.")
    failed: bool = Field(description="Validation errors: the plan can be reviewed but `apply` will refuse it.")
    plan: dict[str, Any] = Field(description="The plan.json the CLI writes: summary, validation, and every dashboard, panel and monitor with its tier, findings, rule trace, PromQL and original.")


class ExplainPlanRequest(Strict):
    find: str = Field(min_length=1, description="Part of a panel title, a panel key such as panel-12, or a monitor title.")


class ExplainQueryRequest(Strict):
    query: str = Field(min_length=1, description="A Datadog metric query or formula.")
    monitor: bool = Field(False, description="Treat `query` as a monitor query, e.g. avg(last_5m):avg:redis.mem.used{*} > 90.")
    variables: dict[str, str | None] = Field(
        {}, description="Template variables as name -> tag prefix; null or empty for a free-form one, e.g. {\"host\": \"host\", \"scope\": null}."
    )


class ApplyRequest(Strict):
    dry_run: bool = Field(False, description="Only return what would be imported and where. Nothing is sent to Dash0.")
    confirm: bool = Field(False, description="Must be true to import. The UI sets it after the user has reviewed the plan and the target.")


class ApplyResponse(BaseModel):
    dry_run: bool
    summary: dict[str, Any] = Field(description="Target, dashboards, check rules and monitors that cannot be imported.")
    results: dict[str, Any] | None = Field(description="What apply.json records: a status per dashboard and for the rule batch. Null on a dry run.")
    failures: int = Field(description="0 means everything was imported. Items that succeeded are in Dash0 even if others failed.")
