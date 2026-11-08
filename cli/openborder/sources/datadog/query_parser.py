"""Hand-written parser for Datadog metric queries, formulas and monitor queries.

Parsing decides nothing about meaning: it produces a Datadog-shaped AST. All semantics live in lower.py.
"""

import re
from dataclasses import dataclass


class ParseError(Exception):
    def __init__(self, message: str, pos: int):
        super().__init__(f"{message} (at position {pos})")
        self.pos = pos


# ---- AST ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class TagFilter:
    key: str
    values: tuple[str, ...]
    negate: bool = False


@dataclass(frozen=True)
class TagVar:
    """A bare `$var` in a filter: Datadog expands it to `<prefix>:<value>`."""

    name: str


@dataclass(frozen=True)
class RawFilter:
    """A filter we do not understand (OR/AND expressions, bare tags)."""

    text: str


@dataclass(frozen=True)
class Method:
    name: str
    args: tuple  # str | float


@dataclass(frozen=True)
class Query:
    space_agg: str
    metric: str
    filters: tuple
    group_by: tuple[str, ...]
    methods: tuple[Method, ...]


@dataclass(frozen=True)
class Num:
    value: float


@dataclass(frozen=True)
class Str:
    value: str


@dataclass(frozen=True)
class Ref:
    name: str


@dataclass(frozen=True)
class Neg:
    arg: object


@dataclass(frozen=True)
class Bin:
    op: str
    lhs: object
    rhs: object


@dataclass(frozen=True)
class Func:
    name: str
    args: tuple


@dataclass(frozen=True)
class MonitorQuery:
    time_agg: str
    window: str  # PromQL duration, e.g. "5m"
    body: object
    comparator: str
    threshold: float


# ---- lexer -------------------------------------------------------------------------------

_TOKEN = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<num>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
  | (?P<str>'[^']*'|"[^"]*")
  | (?P<braces>\{[^}]*\})
  | (?P<ident>[A-Za-z_][A-Za-z0-9_.]*)
  | (?P<punct>[(),:+\-*/.])
    """,
    re.X,
)


def _tokenize(text: str) -> list[tuple[str, str, int]]:
    tokens, pos = [], 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise ParseError(f"unexpected character {text[pos]!r}", pos)
        if m.lastgroup != "ws":
            tokens.append((m.lastgroup, m.group(), pos))
        pos = m.end()
    return tokens


# ---- filters -----------------------------------------------------------------------------


def _split_top_level(text: str) -> list[str]:
    parts, depth, current = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


_VAR = re.compile(r"\$[\w.\-]+")
_IN = re.compile(r"([!-]?)\s*([\w./\-]+)\s+(NOT\s+)?IN\s*\((.*)\)", re.I)
_KV = re.compile(r"([!-]?)([\w./\-]+):(.+)")
_BOOL = re.compile(r"\s(OR|AND)\s", re.I)


def parse_filters(braces: str) -> tuple:
    out = []
    for item in _split_top_level(braces[1:-1]):
        if item == "*":
            continue
        if _VAR.fullmatch(item):
            out.append(TagVar(item[1:]))
        elif _BOOL.search(item):
            out.append(RawFilter(item))
        elif m := _IN.fullmatch(item):
            negate = bool(m.group(1)) or bool(m.group(3))
            values = tuple(v.strip() for v in m.group(4).split(",") if v.strip())
            out.append(TagFilter(m.group(2), values, negate))
        elif m := _KV.fullmatch(item):
            out.append(TagFilter(m.group(2), (m.group(3).strip(),), bool(m.group(1))))
        else:
            out.append(RawFilter(item))
    return tuple(out)


# ---- parser ------------------------------------------------------------------------------


class _Parser:
    def __init__(self, tokens, text):
        self.tokens, self.i, self.text = tokens, 0, text

    def peek(self, offset=0):
        j = self.i + offset
        return self.tokens[j] if j < len(self.tokens) else (None, None, len(self.text))

    def next(self):
        tok = self.peek()
        if tok[0] is None:
            raise ParseError("unexpected end of query", tok[2])
        self.i += 1
        return tok

    def accept(self, kind, value=None):
        tok = self.peek()
        if tok[0] == kind and (value is None or tok[1] == value):
            self.i += 1
            return tok
        return None

    def expect(self, kind, value=None):
        tok = self.accept(kind, value)
        if not tok:
            got = self.peek()
            raise ParseError(f"expected {value or kind}, found {got[1]!r}", got[2])
        return tok

    def parse(self):
        node = self.addsub()
        if self.peek()[0] is not None:
            tok = self.peek()
            raise ParseError(f"unexpected {tok[1]!r}", tok[2])
        return node

    def addsub(self):
        node = self.muldiv()
        while (tok := self.peek())[0] == "punct" and tok[1] in "+-":
            self.i += 1
            node = Bin(tok[1], node, self.muldiv())
        return node

    def muldiv(self):
        node = self.unary()
        while (tok := self.peek())[0] == "punct" and tok[1] in "*/":
            self.i += 1
            node = Bin(tok[1], node, self.unary())
        return node

    def unary(self):
        if self.accept("punct", "-"):
            return Neg(self.unary())
        return self.primary()

    def primary(self):
        kind, value, pos = self.next()
        if kind == "num":
            return Num(float(value))
        if kind == "str":
            return Str(value[1:-1])
        if kind == "punct" and value == "(":
            node = self.addsub()
            self.expect("punct", ")")
            return node
        if kind == "ident":
            if self.peek()[:2] == ("punct", ":") and self.peek(1)[0] == "ident":
                return self.query(value)
            if self.peek()[:2] == ("punct", "("):
                return self.func(value)
            return Ref(value)
        raise ParseError(f"unexpected {value!r}", pos)

    def func(self, name):
        self.expect("punct", "(")
        args = []
        if not self.accept("punct", ")"):
            while True:
                args.append(self.addsub())
                if self.accept("punct", ")"):
                    break
                self.expect("punct", ",")
        return Func(name, tuple(args))

    def query(self, space_agg):
        self.expect("punct", ":")
        metric = self.expect("ident")[1]
        filters = parse_filters(self.expect("braces")[1])
        group_by: tuple[str, ...] = ()
        if self.peek()[:2] == ("ident", "by") and self.peek(1)[0] == "braces":
            self.i += 1
            group_by = tuple(_split_top_level(self.next()[1][1:-1]))
        methods = []
        while self.accept("punct", "."):
            name = self.expect("ident")[1]
            self.expect("punct", "(")
            args = []
            if not self.accept("punct", ")"):
                while True:
                    kind, value, pos = self.next()
                    if kind == "num":
                        args.append(float(value))
                    elif kind == "str":
                        args.append(value[1:-1])
                    elif kind == "ident":
                        args.append(value)
                    else:
                        raise ParseError(f"unexpected {value!r} in .{name}()", pos)
                    if self.accept("punct", ")"):
                        break
                    self.expect("punct", ",")
            methods.append(Method(name, tuple(args)))
        return Query(space_agg, metric, filters, group_by, tuple(methods))


def parse_expression(text: str):
    """Parse a metric query or a formula (arithmetic over queries, `queryN` refs and functions)."""
    return _Parser(_tokenize(text), text).parse()


# ---- monitors ----------------------------------------------------------------------------

_MONITOR = re.compile(
    r"^\s*(?P<agg>avg|max|min|sum)\(last_(?P<n>\d+)(?P<unit>[smhdw])\)\s*:\s*(?P<body>.+?)\s*"
    r"(?P<cmp>>=|<=|==|!=|>|<)\s*(?P<thr>-?\d+(?:\.\d+)?)\s*$",
    re.S,
)


def parse_monitor_query(text: str) -> MonitorQuery:
    m = _MONITOR.match(text)
    if not m:
        raise ParseError(
            "only `avg|max|min|sum(last_N<unit>):<query> <comparator> <number>` monitor queries are supported", 0
        )
    return MonitorQuery(
        time_agg=m["agg"],
        window=f"{m['n']}{m['unit']}",
        body=parse_expression(m["body"]),
        comparator=m["cmp"],
        threshold=float(m["thr"]),
    )
