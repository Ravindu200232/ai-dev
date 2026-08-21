"""Import/export parsing and local module resolution."""
from .exports_common import *

def _clause_names(body: str) -> list:
    """`a, b as c, default as D` -> ['a', 'c', 'D'] — the exported names."""
    names = []
    for part in body.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(
            r"(?:[A-Za-z_$][\w$]*|default)\s+as\s+([A-Za-z_$][\w$]*)", part)
        if m:
            names.append(m.group(1))
        elif re.fullmatch(r"[A-Za-z_$][\w$]*", part) and part != "default":
            names.append(part)
    return names


def _parse_exports_one(src: str) -> ModuleExports:
    ex = ModuleExports()
    for m in _EXPORT_DECL_RE.finditer(src):
        ex.named.add(m.group(1))
    for m in _EXPORT_DESTRUCT_RE.finditer(src):
        for part in m.group(2).split(","):
            part = part.split(":")[-1].split("=")[0].strip().lstrip(". ")
            if re.fullmatch(r"[A-Za-z_$][\w$]*", part):
                ex.named.add(part)
    ex.has_default = bool(_EXPORT_DEFAULT_RE.search(src))
    for m in _EXPORT_STAR_RE.finditer(src):
        if m.group(1):
            ex.named.add(m.group(1))
        else:
            ex.star_from.append(m.group(2))
    for m in _EXPORT_BLOCK_RE.finditer(src):
        names = _clause_names(m.group(1))
        if m.group(2):
            ex.named_from.update({nm: m.group(2) for nm in names})
        else:
            ex.named.update(names)
    return ex


def parse_exports(src: str) -> ModuleExports:
    """Union of what the raw and the stripped source declare — see module doc."""
    a = _parse_exports_one(src)
    b = _parse_exports_one(strip_noncode(src))
    return ModuleExports(
        named=a.named | b.named,
        has_default=a.has_default or b.has_default,
        star_from=sorted(set(a.star_from) | set(b.star_from)),
        named_from={**b.named_from, **a.named_from},
    )


@dataclass
class ImportStmt:
    spec: str
    line: int
    names: list = field(default_factory=list)
    default: str = ""
    namespace: str = ""


# The clause between `import` and `from` may span lines.
_IMPORT_RE = re.compile(
    r"""\bimport\s+(?!\()((?:(?!\bimport\b)[^;])*?)\s*from\s*['"]([^'"]+)['"]""",
    re.S)


def parse_imports(src: str) -> list:
    """Import statements, read from the stripped source only — see module doc."""
    clean = strip_noncode(src)
    out = []
    for m in _IMPORT_RE.finditer(clean):
        clause, spec = m.group(1).strip(), m.group(2)
        if "import" in clause or "'" in clause or '"' in clause:
            continue
        st = ImportStmt(spec=spec, line=clean[:m.start()].count("\n") + 1)
        block = re.search(r"\{([^}]*)\}", clause)
        if block:
            for part in block.group(1).split(","):
                part = part.strip()
                as_m = re.fullmatch(
                    r"([A-Za-z_$][\w$]*)\s+as\s+([A-Za-z_$][\w$]*)", part)
                if as_m:
                    st.names.append((as_m.group(1), as_m.group(2)))
                elif re.fullmatch(r"[A-Za-z_$][\w$]*", part):
                    st.names.append((part, part))
            clause = clause[:block.start()] + clause[block.end():]
        ns = re.search(r"\*\s*as\s+([A-Za-z_$][\w$]*)", clause)
        if ns:
            st.namespace = ns.group(1)
            clause = clause[:ns.start()] + clause[ns.end():]
        head = clause.strip().strip(",").strip()
        if re.fullmatch(r"[A-Za-z_$][\w$]*", head):
            st.default = head
        out.append(st)
    return out


def _normalise(path: str) -> str:
    parts = []
    for seg in path.split("/"):
        if seg == "..":
            if parts:
                parts.pop()
        elif seg not in (".", ""):
            parts.append(seg)
    return "/".join(parts)


def resolve_local(importer_rel: str, spec: str, files: dict) -> str | None:
    """The project-relative path `spec` refers to, or None if unresolvable."""
    if spec.startswith("@/"):
        target = _normalise(spec[2:])
    elif spec.startswith(("./", "../")):
        target = _normalise((PurePosixPath(importer_rel).parent / spec).as_posix())
    else:
        return None
    if not target:
        return None

    for cand in (target, f"{target}.jsx", f"{target}.js",
                 f"{target}/index.jsx", f"{target}/index.js"):
        if cand in files:
            return cand
    return None


def effective_exports(rel: str, files: dict, _seen: set = None) -> set | None:
    """Every name `rel` exports, following re-exports transitively."""
    _seen = _seen or set()
    if rel in _seen:
        return set()
    src = files.get(rel)
    if not isinstance(src, str):
        return None
    ex = parse_exports(src)
    names = set(ex.named) | set(ex.named_from)
    for spec in ex.star_from:
        target = resolve_local(rel, spec, files)
        if target is None:
            return None
        inner = effective_exports(target, files, _seen | {rel})
        if inner is None:
            return None
        names |= inner
    return names

__all__ = [name for name in globals() if not name.startswith("__")]
