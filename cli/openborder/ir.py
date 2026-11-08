"""Intermediate representation.

Frozen dataclasses throughout: they are built only by our own code, so there is nothing to
validate, and they stay cheap. The query IR is deliberately PromQL-shaped (PromQL is the only target).
Translation problems are Findings, never exceptions.
"""

from dataclasses import dataclass, field
from enum import IntEnum


class Tier(IntEnum):
    """Ordered so that the worst tier of several items is simply max()."""

    EXACT = 0
    EQUIVALENT = 1
    APPROXIMATE = 2
    MANUAL = 3

    @property
    def symbol(self) -> str:
        return {0: "✓", 1: "≈", 2: "~", 3: "✗"}[int(self)]

    @property
    def label(self) -> str:
        return self.name.lower()


def worst(tiers) -> Tier:
    return max(tiers, default=Tier.EXACT)


@dataclass(frozen=True)
class Finding:
    tier: Tier
    rule_id: str
    message: str
    source_ref: str = ""


@dataclass(frozen=True)
class TraceStep:
    rule_id: str
    tier: Tier
    before: str
    after: str


# ---- query expressions -------------------------------------------------------------------


@dataclass(frozen=True)
class Matcher:
    label: str
    op: str  # = != =~ !~
    value: str


@dataclass(frozen=True)
class Selector:
    metric: str  # OTel metric name, addressed as {otel_metric_name="..."} in Dash0
    matchers: tuple[Matcher, ...] = ()
    offset: str | None = None


@dataclass(frozen=True)
class RangeFn:
    """rate(sel[w]), avg_over_time(sel[w]); `window` is None when `arg` is a Subquery."""

    fn: str
    arg: "Expr"
    window: str | None = None


@dataclass(frozen=True)
class Subquery:
    arg: "Expr"
    range: str
    step: str | None = None


@dataclass(frozen=True)
class Aggregate:
    op: str
    by: tuple[str, ...]
    arg: "Expr"
    param: "Expr | None" = None  # topk / bottomk


@dataclass(frozen=True)
class BinOp:
    op: str
    lhs: "Expr"
    rhs: "Expr"
    on: tuple[str, ...] | None = None
    group: str | None = None  # group_left | group_right


@dataclass(frozen=True)
class Call:
    fn: str
    args: tuple["Expr", ...]


@dataclass(frozen=True)
class Number:
    value: float


@dataclass(frozen=True)
class Placeholder:
    """Verbatim text, e.g. Dash0's $__threshold."""

    text: str


Expr = Selector | RangeFn | Subquery | Aggregate | BinOp | Call | Number | Placeholder


@dataclass(frozen=True)
class Translation:
    """Result of translating one Datadog query or formula."""

    expr: Expr | None  # None <=> tier is MANUAL
    findings: tuple[Finding, ...]
    trace: tuple[TraceStep, ...]
    original: str

    @property
    def tier(self) -> Tier:
        return worst(f.tier for f in self.findings)


# ---- dashboards --------------------------------------------------------------------------


@dataclass(frozen=True)
class GridPos:
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class PanelQuery:
    expr: Expr
    legend: str | None = None  # Perses seriesNameFormat


@dataclass(frozen=True)
class Panel:
    key: str
    title: str
    kind: str  # TimeSeriesChart | StatChart | Markdown
    grid: GridPos
    queries: tuple[PanelQuery, ...] = ()
    options: dict = field(default_factory=dict)
    findings: tuple[Finding, ...] = ()
    trace: tuple[TraceStep, ...] = ()
    original: dict = field(default_factory=dict)
    source_queries: tuple[str, ...] = ()  # the Datadog query strings, quoted in the panel description
    placeholder: bool = False  # True when the widget could not be translated

    @property
    def tier(self) -> Tier:
        return worst(f.tier for f in self.findings)


@dataclass(frozen=True)
class Section:
    title: str | None
    panels: tuple[Panel, ...]


@dataclass(frozen=True)
class Variable:
    name: str
    label: str  # OTel/Prometheus label whose values populate the variable
    default: str  # "$__all" or a literal
    findings: tuple[Finding, ...] = ()


@dataclass(frozen=True)
class Dashboard:
    source_id: str
    title: str
    description: str
    variables: tuple[Variable, ...]
    sections: tuple[Section, ...]
    findings: tuple[Finding, ...] = ()
    original: dict = field(default_factory=dict)

    @property
    def panels(self) -> list[Panel]:
        return [p for s in self.sections for p in s.panels]


# ---- alerts ------------------------------------------------------------------------------


@dataclass(frozen=True)
class AlertRule:
    source_id: str
    name: str
    expr: Expr | None
    thresholds: dict  # {"degraded": float, "failed": float}
    interval: str
    labels: dict
    annotations: dict
    findings: tuple[Finding, ...]
    trace: tuple[TraceStep, ...]
    original: dict
    enabled: bool = True

    @property
    def tier(self) -> Tier:
        return worst(f.tier for f in self.findings)
