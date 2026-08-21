"""Broken import checks and human-readable grouping."""
from .exports_common import *
from .exports_parse import *

@dataclass
class BrokenImport:
    importer: str
    line: int
    name: str
    module: str
    spec: str
    available: list

    def close_match(self) -> str | None:
        """A very near neighbour, or None."""
        lower = self.name.lower()
        same = [n for n in self.available if n.lower() == lower]
        if len(same) == 1:
            return same[0]
        hit = difflib.get_close_matches(self.name, self.available, n=1,
                                        cutoff=0.92)
        return hit[0] if hit else None

    def message(self) -> str:
        return (f"{self.importer}:{self.line}: imports {{ {self.name} }} from "
                f"'{self.spec}', which exports only: "
                f"{', '.join(self.available) or '(nothing)'}")


def check_named_imports(files: dict) -> list:
    """Every named import of a local module that the module does not export."""
    cache: dict = {}
    out = []
    for rel, src in sorted(files.items()):
        if not rel.endswith(CODE_SUFFIXES) or not isinstance(src, str):
            continue
        for st in parse_imports(src):
            if not st.names:
                continue
            surface = FRAMEWORK_EXPORTS.get(st.spec)
            if surface is not None:
                for imported, _local in st.names:
                    if imported not in surface:
                        out.append(BrokenImport(
                            importer=rel, line=st.line, name=imported,
                            module=st.spec, spec=st.spec,
                            available=sorted(surface)))
                continue
            if not st.spec.startswith(LOCAL_PREFIXES):
                continue
            if st.spec.endswith(SKIP_SUFFIXES):
                continue
            target = resolve_local(rel, st.spec, files)
            if target is None or not target.endswith(CODE_SUFFIXES):
                continue
            if target not in cache:
                cache[target] = effective_exports(target, files)
            avail = cache[target]
            if avail is None:
                continue
            for imported, _local in st.names:
                if imported not in avail:
                    out.append(BrokenImport(
                        importer=rel, line=st.line, name=imported,
                        module=target, spec=st.spec, available=sorted(avail)))
    return out


_REEXPORT_DEFAULT_RE = re.compile(
    r"""\bexport\s*\{[^}]*\bdefault\b\s*(?:,[^}]*)?\}\s*from\s*['"]""")


def has_default_export(rel: str, files: dict, _seen: set = None) -> bool | None:
    """Whether `rel` has a default export, following `export { default }`."""
    _seen = _seen or set()
    if rel in _seen:
        return False
    src = files.get(rel)
    if not isinstance(src, str):
        return None
    ex = parse_exports(src)
    if ex.has_default or "default" in ex.named or "default" in ex.named_from:
        return True
    if _REEXPORT_DEFAULT_RE.search(src):
        return True
    return False


def check_default_imports(files: dict) -> list:
    """Every default import of a local module that has no default export."""
    cache: dict = {}
    out = []
    for rel, src in sorted(files.items()):
        if not rel.endswith(CODE_SUFFIXES) or not isinstance(src, str):
            continue
        for st in parse_imports(src):
            if not st.default:
                continue
            if not st.spec.startswith(LOCAL_PREFIXES):
                continue
            if st.spec.endswith(SKIP_SUFFIXES):
                continue
            target = resolve_local(rel, st.spec, files)
            if target is None or not target.endswith(CODE_SUFFIXES):
                continue
            if target not in cache:
                cache[target] = has_default_export(target, files)
            got = cache[target]
            if got is None or got:
                continue
            avail = effective_exports(target, files)
            out.append(BrokenImport(
                importer=rel, line=st.line, name=st.default,
                module=target, spec=st.spec,
                available=sorted(avail or ())))
    return out


def group_messages(broken: list) -> list:
    """One line per (importer, module) pair, listing every missing name."""
    groups: dict = {}
    for b in broken:
        groups.setdefault((b.importer, b.module), []).append(b)
    out = []
    for (importer, module), items in sorted(groups.items()):
        first = items[0]
        names = ", ".join(sorted({i.name for i in items}))
        head = (f"{importer}:{first.line}: imports {{ {names} }} from "
                f"'{first.spec}', which exports only: "
                f"{', '.join(first.available) or '(nothing)'}.")
        if module in FRAMEWORK_EXPORTS:

            out.append(f"{head} Use one of those instead (NextResponse.json(…) "
                       f"is almost always what is meant).")
        else:
            out.append(f"{head} Either add the missing export to {module} or "
                       f"use one that exists — do not rename or remove the "
                       f"existing exports, other files import them.")
    return out

__all__ = [name for name in globals() if not name.startswith("__")]
