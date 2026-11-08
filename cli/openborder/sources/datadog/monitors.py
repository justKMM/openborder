"""Datadog monitor JSON -> IR AlertRule."""

import re

from openborder.ids import slug
from openborder.ir import (
    Aggregate,
    AlertRule,
    BinOp,
    Finding,
    Placeholder,
    RangeFn,
    Selector,
    Subquery,
    Tier,
    worst,
)
from openborder.mappings import Mapping
from openborder.sources.datadog import query_parser as qp
from openborder.sources.datadog.lower import Ctx, Recorder, Unsupported, lower_node
from openborder.targets.promql import render

EXACT, EQUIVALENT, APPROXIMATE, MANUAL = Tier.EXACT, Tier.EQUIVALENT, Tier.APPROXIMATE, Tier.MANUAL

_TIME_FNS = {"avg": "avg_over_time", "max": "max_over_time", "min": "min_over_time", "sum": "sum_over_time"}
_SUPPORTED_TYPES = ("query alert", "metric alert")
EVAL_INTERVAL = "1m"

_DROP_BLOCKS = re.compile(
    r"\{\{#(is_recovery|is_alert_recovery|is_warning_recovery|is_no_data|is_renotify|is_alert_to_warning|is_warning_to_alert)\}\}"
    r".*?\{\{/\1\}\}",
    re.S,
)
_SEVERITY_TAGS = re.compile(r"\{\{[#/](is_alert|is_warning)\}\}")
_MENTION = re.compile(r"(?<![\w.])@[\w.+\-]+(?:@[\w.\-]+)?")
_TEMPLATE = re.compile(r"\{\{[^}]+\}\}")


def convert_message(message: str) -> tuple[str, list[str], list[str]]:
    """Return (text, notes, mentions). Notes describe what was changed or could not be converted."""
    notes: list[str] = []
    text = message or ""
    mentions = sorted(set(_MENTION.findall(text)))
    text = _MENTION.sub("", text)
    if _DROP_BLOCKS.search(text):
        text = _DROP_BLOCKS.sub("", text)
        notes.append("removed recovery/no-data/renotify blocks")
    if _SEVERITY_TAGS.search(text):
        text = _SEVERITY_TAGS.sub("", text)
        notes.append("flattened is_alert/is_warning blocks (both severity texts now appear together)")
    converted = text.replace("{{value}}", "{{ $value }}")
    converted = re.sub(r"\{\{host\.name\}\}", "{{ $labels.host_name }}", converted)
    if converted != text:
        notes.append("converted {{value}}/{{host.name}} to Prometheus template syntax")
    text = converted
    leftover = sorted({t for t in _TEMPLATE.findall(text) if not t.startswith("{{ $")})
    if leftover:
        notes.append("left unconverted Datadog template variables: " + ", ".join(leftover))
    return re.sub(r"\n{3,}", "\n\n", text).strip(), notes, mentions


_NAME_TEMPLATE_PHRASE = re.compile(r"\s+(?:for|on|of|in)(?:\s+[\w.\-]+)?\s*\{\{[^}]+\}\}", re.I)
_UNSUPPORTED_KINDS = (
    (r"\bforecast\(", "forecast monitors"),
    (r"\banomalies\(", "anomaly-detection monitors"),
    (r"\boutliers\(", "outlier-detection monitors"),
    (r"\b(?:pct_)?change\(", "change/pct_change monitors"),
)


def split_name(name: str) -> tuple[str, str]:
    """Return (static check name, templated summary). Dash0 does not expand templates in a check's name,
    so `Disk full for host {{host.name}}` becomes the name `Disk full` and keeps the template in the summary."""
    summary = name.replace("{{value}}", "{{ $value }}")
    summary = re.sub(r"\{\{host\.name\}\}", "{{ $labels.host_name }}", summary)
    static = _NAME_TEMPLATE_PHRASE.sub("", name)
    static = re.sub(r"\s*\{\{[^}]+\}\}\s*", " ", static)
    static = re.sub(r"\s{2,}", " ", static).strip()
    return (static or name), summary


def _unsupported_reason(query: str, error: Exception) -> str:
    for pattern, label in _UNSUPPORTED_KINDS:
        if re.search(pattern, query):
            return f"{label} have no Prometheus equivalent; recreate it manually in Dash0"
    return f"could not translate the monitor query: {error}"


def _labels(tags: list[str]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for tag in tags:
        key, sep, value = tag.partition(":")
        key = re.sub(r"[^A-Za-z0-9_]", "_", key)
        labels.setdefault(key, value if sep else "true")
    return labels


def _option_findings(options: dict, ref: str) -> list[Finding]:
    out: list[Finding] = []
    if options.get("notify_no_data"):
        out.append(Finding(APPROXIMATE, "dd.monitor.no-data", "notify_no_data is on: Datadog alerts when data stops arriving, but the migrated rule stays silent when the series disappears", ref))
    ignored = []
    if options.get("renotify_interval"):
        ignored.append(f"renotify_interval={options['renotify_interval']}")
    if options.get("escalation_message"):
        ignored.append("escalation_message")
    if options.get("evaluation_delay"):
        ignored.append(f"evaluation_delay={options['evaluation_delay']}")
    if options.get("require_full_window"):
        ignored.append("require_full_window")
    if options.get("new_host_delay") or options.get("new_group_delay"):
        ignored.append("new_host_delay/new_group_delay")
    if ignored:
        out.append(Finding(EQUIVALENT, "dd.monitor.options-ignored", "not migrated (no Prometheus equivalent): " + ", ".join(ignored), ref))
    return out


def convert_monitor(data: dict, mapping: Mapping, enable_approximate: bool = False) -> AlertRule:
    d = data.get("definition") if isinstance(data.get("definition"), dict) else data
    raw_name = d.get("name") or data.get("title") or "Unnamed monitor"
    name, summary = split_name(raw_name)
    source_id = str(data.get("id") or d.get("id") or slug(raw_name, "monitor"))
    ref = f"monitor:{name}"
    rec = Recorder(ref)
    query = d.get("query") or ""
    options = d.get("options") or {}
    expr = None
    thresholds: dict = {}

    if d.get("type") not in _SUPPORTED_TYPES:
        rec.step("dd.monitor.unsupported", MANUAL, str(d.get("type")), "", f"monitor type '{d.get('type')}' is not supported; recreate it manually in Dash0")
    else:
        try:
            mq = qp.parse_monitor_query(query)
            ctx = Ctx(mapping, {}, rate_window=mq.window, interval=EVAL_INTERVAL, range=None)
            body = lower_node(mq.body, ctx, rec)
            fn = _TIME_FNS[mq.time_agg]
            if isinstance(body, Aggregate) and body.op == mq.time_agg and isinstance(body.arg, Selector):
                inner = Aggregate(body.op, body.by, RangeFn(fn, body.arg, mq.window))
                rec.step("dd.monitor.window.pushdown", EXACT, f"{mq.time_agg}(last_{mq.window})", render(inner))
            else:
                inner = RangeFn(fn, Subquery(body, mq.window, EVAL_INTERVAL))
                rec.step(
                    "dd.monitor.window.subquery",
                    EQUIVALENT,
                    f"{mq.time_agg}(last_{mq.window})",
                    render(inner),
                    f"{mq.time_agg}(last_{mq.window}) averages the query's value over the window; this is evaluated "
                    f"as a PromQL subquery at {EVAL_INTERVAL} resolution",
                )
            if mq.comparator in ("==", "!="):
                raise Unsupported("dd.monitor.comparator", f"comparator '{mq.comparator}' cannot use Dash0 thresholds")
            expr = BinOp(mq.comparator, inner, Placeholder("$__threshold"))
            if mq.comparator in (">", ">="):
                rec.step("dd.monitor.comparator", EXACT, mq.comparator, mq.comparator)
            else:
                rec.step(
                    "dd.monitor.comparator.below",
                    APPROXIMATE,
                    mq.comparator,
                    mq.comparator,
                    "this fires when the value drops below the threshold; Dash0 documents that the higher threshold "
                    "maps to 'failed', so the degraded/failed severities may be reversed for a below-threshold rule: verify in Dash0",
                )
            tconf = options.get("thresholds") or {}
            critical = tconf.get("critical", mq.threshold)
            thresholds["failed"] = critical
            if tconf.get("warning") is not None:
                thresholds["degraded"] = tconf["warning"]
            if critical != mq.threshold:
                rec.step("dd.monitor.thresholds", APPROXIMATE, str(mq.threshold), str(critical), "the query threshold and thresholds.critical differ; thresholds.critical was used")
            else:
                rec.step("dd.monitor.thresholds", EXACT, "critical/warning", "failed/degraded")
        except qp.ParseError as e:
            rec.step("dd.monitor.unsupported-query", MANUAL, query, "", _unsupported_reason(query, e))
        except Unsupported as e:
            rec.step(e.rule_id, MANUAL, query, "", e.message)

    findings = list(rec.findings) + _option_findings(options, ref)
    if name != raw_name:
        findings.append(Finding(EQUIVALENT, "dd.monitor.name", f"Dash0 does not expand templates in a check's name: '{raw_name}' is named '{name}'; the full title with its templates is kept in the summary", ref))
    description, notes, mentions = convert_message(d.get("message") or "")
    if notes:
        findings.append(Finding(EQUIVALENT, "dd.monitor.message", "message template changed: " + "; ".join(notes) + " (verify the rendering in Dash0)", ref))
    if mentions:
        findings.append(Finding(EQUIVALENT, "dd.monitor.notify", "notification handles removed; set up routing in Dash0 manually: " + ", ".join(mentions), ref))

    tier = worst(f.tier for f in findings)
    labels = _labels(list(d.get("tags") or []) + list(data.get("tags") or []))
    if d.get("priority") is not None:
        labels.setdefault("priority", str(d["priority"]))
    annotations = {
        "summary": summary,
        "description": description,
        "openborder_source": f"datadog:monitor:{source_id}",
        "openborder_tier": tier.label,
        "openborder_original_query": query,
    }
    return AlertRule(
        source_id=source_id,
        name=name,
        expr=None if tier == MANUAL else expr,
        thresholds=thresholds,
        interval=EVAL_INTERVAL,
        labels=labels,
        annotations=annotations,
        findings=tuple(findings),
        trace=tuple(rec.trace),
        original=data,
        enabled=tier <= EQUIVALENT or (enable_approximate and tier < MANUAL),
    )
