"""IR expression -> PromQL text. The only place in the codebase that emits PromQL."""

from openborder.ir import (
    Aggregate,
    BinOp,
    Call,
    Expr,
    Number,
    Placeholder,
    RangeFn,
    Selector,
    Subquery,
)

_PREC = {">": 0, "<": 0, ">=": 0, "<=": 0, "==": 0, "!=": 0, "+": 1, "-": 1, "*": 2, "/": 2}


def _esc(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _num(value: float) -> str:
    return str(int(value)) if value == int(value) else repr(float(value))


def _selector(s: Selector, window: str | None = None) -> str:
    parts = [f'otel_metric_name="{_esc(s.metric)}"']
    parts += [f'{m.label}{m.op}"{_esc(m.value)}"' for m in s.matchers]
    out = "{" + ", ".join(parts) + "}"
    if window:
        out += f"[{window}]"
    if s.offset:
        out += f" offset {s.offset}"
    return out


def render(e: Expr, parent_prec: int = -1, right: bool = False) -> str:
    if isinstance(e, Selector):
        return _selector(e)
    if isinstance(e, Number):
        return _num(e.value)
    if isinstance(e, Placeholder):
        return e.text
    if isinstance(e, RangeFn):
        if isinstance(e.arg, Selector):
            return f"{e.fn}({_selector(e.arg, e.window)})"
        return f"{e.fn}({render(e.arg)})"
    if isinstance(e, Subquery):
        step = e.step or ""
        return f"{render(e.arg, parent_prec=99)}[{e.range}:{step}]"
    if isinstance(e, Aggregate):
        by = f" by ({', '.join(e.by)}) " if e.by else ""
        param = f"{render(e.param)}, " if e.param is not None else ""
        return f"{e.op}{by}({param}{render(e.arg)})"
    if isinstance(e, Call):
        return f"{e.fn}({', '.join(render(a) for a in e.args)})"
    if isinstance(e, BinOp):
        p = _PREC[e.op]
        matching = ""
        if e.on is not None:
            matching = f" on ({', '.join(e.on)})" + (f" {e.group}" if e.group else "")
        text = f"{render(e.lhs, p)} {e.op}{matching} {render(e.rhs, p, right=True)}"
        if parent_prec >= 0 and (p < parent_prec or (p == parent_prec and right)):
            return f"({text})"
        return text
    raise TypeError(f"cannot render {type(e).__name__}")
