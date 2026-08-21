"""Work out the real image keys behind `seedImage(`x-${row.field}`)`.

Nothing here knows what any of those rows are. It reads the seed file the
build actually wrote, finds the array the template loops over, and derives
each row's key the same way the generated JavaScript does at runtime.
"""
import re

# Fields a row is usually named by, most specific first.
LABEL_FIELDS = ("name", "title", "label", "full_name", "fullName",
                "display_name", "displayName", "heading", "caption", "text")

# Fields that are a slug of the row's label rather than a value of their own.
DERIVED_FIELDS = ("slug", "handle", "key", "code", "ref", "path", "permalink",
                  "identifier", "id", "_id", "uid")

# `slug: slugify(d.name)`, `slug: d.name.toLowerCase()`, `key: kebab(x.title)`
_COMPUTED_RE = re.compile(
    r"[\w$]*\(?\s*(?:[\w$]+\s*\.\s*)?([A-Za-z_$][\w$]*)\s*\)?"
    r"(?:\s*\.\s*[\w$]+\s*\([^)]*\))*")

_STRING_RE = r"(?:'([^'\\]{0,120})'|\"([^\"\\]{0,120})\"|`([^`$\\]{0,120})`)"

_ARRAY_RE = re.compile(
    r"(?:const|let|var|export\s+const)\s+([A-Za-z_$][\w$]*)\s*=\s*\[")

_MAP_RE = re.compile(
    r"([A-Za-z_$][\w$.]*)\s*\.\s*(?:map|forEach|flatMap)\s*\(\s*"
    r"(?:async\s*)?\(?\s*([A-Za-z_$][\w$]*)")

_FOROF_RE = re.compile(
    r"for\s*\(\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s+of\s+"
    r"([A-Za-z_$][\w$.]*)")

_KEY_OK_RE = re.compile(r"[A-Za-z0-9._-]{1,60}")


def slugify(text: str) -> str:
    """`Dr. Smith` -> `dr-smith`, the way generated seeds do it."""
    out = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return re.sub(r"-{2,}", "-", out)


def is_seed_file(rel: str) -> bool:
    """Whether this path is somewhere a build puts its demo rows."""
    low = str(rel or "").lower()
    if not low.endswith((".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")):
        return False
    return ("seed" in low or "fixture" in low or "demo" in low
            or "sample" in low or "bootstrap" in low)


def _match_bracket(body: str, start: int, open_ch: str, close_ch: str) -> int:
    """Index just past the bracket opened at `start`, skipping strings."""
    depth, i, n = 0, start, len(body)
    quote = ""
    while i < n:
        ch = body[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "'\"`":
            quote = ch
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def _literal_fields(span: str) -> dict:
    """Every `field: 'literal'` pair directly inside one object literal."""
    out = {}
    for m in re.finditer(r"([A-Za-z_$][\w$]*)\s*:\s*" + _STRING_RE, span):
        value = next((g for g in m.groups()[1:] if g is not None), "")
        out.setdefault(m.group(1), value.strip())
    return out


def _computed_fields(span: str) -> dict:
    """`slug: slugify(d.name)` -> `{'slug': 'name'}`, the source field."""
    out = {}
    for m in re.finditer(r"([A-Za-z_$][\w$]*)\s*:\s*([^,\n}]{1,160})", span):
        field, expr = m.group(1), m.group(2).strip()
        if expr[:1] in "'\"`" or field in out:
            continue
        src = _COMPUTED_RE.match(expr)
        if src and src.group(1):
            out[field] = src.group(1)
    return out


def _rows_in(body: str, start: int) -> list:
    """Parse the top-level object literals of the array opened at `start`."""
    end = _match_bracket(body, start, "[", "]")
    if end == -1:
        return []
    rows, i = [], start + 1
    while i < end:
        if body[i] == "{":
            close = _match_bracket(body, i, "{", "}")
            if close == -1:
                break
            span = body[i + 1:close - 1]
            fields = _literal_fields(span)
            fields.update({k: v for k, v in _computed_fields(span).items()
                           if k not in fields})
            if fields:
                rows.append((fields, _literal_fields(span)))
            i = close
            continue
        i += 1
    return rows


def seed_arrays(arch) -> dict:
    """`{ARRAY_NAME: [(fields, literals), ...]}` for every seeded array."""
    out = {}
    for rel, body in (getattr(arch, "files", None) or {}).items():
        if not is_seed_file(rel):
            continue
        body = str(body or "")
        for m in _ARRAY_RE.finditer(body):
            rows = _rows_in(body, m.end() - 1)
            if rows:
                out.setdefault(m.group(1), []).extend(rows)
    return out


def loop_array(body: str, at: int, var: str) -> str:
    """The array name the loop variable `var` is iterating over."""
    head = body[:at]
    best = ""
    for rx, gvar, garr in ((_MAP_RE, 2, 1), (_FOROF_RE, 1, 2)):
        for m in rx.finditer(head):
            if m.group(gvar) == var:
                best = m.group(garr)
    return best.split(".")[0] if best else ""


def _label_of(literals: dict) -> str:
    for field in LABEL_FIELDS:
        if literals.get(field):
            return literals[field]
    return ""


def _value_of(fields: dict, literals: dict, field: str) -> str:
    """This row's value for `field`, derived when it is not written down."""
    literal = literals.get(field)
    if literal:
        return literal
    source = fields.get(field)
    if source and source in literals and literals[source]:
        return slugify(literals[source])          # slug: slugify(d.name)
    if field in DERIVED_FIELDS or source:
        label = _label_of(literals)
        if label:
            return slugify(label)
    return ""


def template_keys(arch, body: str, at: int, prefix: str, expr: str,
                  suffix: str, *, strict: bool = True) -> dict:
    """`{key: label}` for one `prefix${expr}suffix` template in `body`.

    `strict` is for `seedImage`, which strips its key down to the safe set.
    A raw `/generated/${row.name}.png` path keeps spaces, so it asks for the
    loose check instead.
    """
    parts = [p.strip() for p in str(expr or "").split(".") if p.strip()]
    if not parts:
        return {}
    field = parts[-1]
    var = parts[0] if len(parts) > 1 else ""
    if not field.isidentifier():
        return {}

    arrays = seed_arrays(arch)
    name = loop_array(body, at, var) if var else ""
    scoped = arrays.get(name)
    if scoped is None:
        # No loop to scope by: only rows that really carry the field.
        scoped = [r for rows in arrays.values() for r in rows
                  if field in r[0] or field in DERIVED_FIELDS]

    out = {}
    for fields, literals in scoped:
        value = _value_of(fields, literals, field)
        if not value:
            continue
        key = f"{prefix}{value}{suffix}"
        ok = (_KEY_OK_RE.fullmatch(key) if strict
              else (0 < len(key) <= 80 and "/" not in key))
        if ok:
            out.setdefault(key, _label_of(literals) or value)
    return out


def family_key(prefix: str, suffix: str) -> str:
    """One shared key so an unresolvable template still renders something."""
    stem = f"{prefix}{suffix}".strip("-_. ")
    if len(stem) < 3 or not _KEY_OK_RE.fullmatch(stem):
        return ""
    return stem
