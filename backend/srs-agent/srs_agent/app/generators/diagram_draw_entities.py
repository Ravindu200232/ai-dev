"""Use-case, sequence and ERD native drawings."""
from __future__ import annotations

from .diagram_draw_primitives import *
from .diagram_draw_primitives import (
    _arrow, _clean, _drawing, _fit, _line, _not_applicable, _orth_arrow,
    _overlap, _stick_figure, _text_block, _tokenize, _txt,
)

def _use_case(doc: dict, W: float) -> Drawing:
    from .diagrams import actors_and_use_cases
    actors, cases = actors_and_use_cases(doc)
    actors, cases = actors[:6], cases[:14]
    if not cases:
        return _not_applicable(W, "Use Case Diagram", "No actor goals or system use cases are specified in the approved SRS.")

    actor_x = 62
    bx = 142
    bw = W - bx - 24
    uc_w = min(280, bw - 52)
    uc_h = 38
    row_gap = 11
    top_pad = 42
    bottom_pad = 26
    H = max(300, top_pad + len(cases)*(uc_h+row_gap) + bottom_pad)
    d, g = _drawing(W, H)

    # System boundary: quiet, white
    g.add(Rect(bx, 18, bw, H-36, fillColor=WHITE, strokeColor=LINE, strokeWidth=1.15))
    title = _clean(doc.get("project_name") or "System")
    _txt(g, bx+14, H-34, _fit(title, FB, 9.2, bw-28), 9.2, FB, MUTED)

    case_pos: dict[str, tuple[float,float,float,float]] = {}
    cx = bx + bw*.57
    for i, uc in enumerate(cases):
        cy = H - top_pad - i*(uc_h+row_gap) - uc_h/2
        g.add(Ellipse(cx, cy, uc_w/2, uc_h/2, fillColor=GREEN_BG,
                      strokeColor=GREEN, strokeWidth=1.25))
        _text_block(g, cx, cy, uc.get("label", "Use case"), maxw=uc_w-26,
                    size=8.8, max_lines=2)
        case_pos[str(uc.get("id"))] = (cx-uc_w/2, cy, uc_w, uc_h)

    # Center each actor beside its use cases.
    fallback_gap = (H-110)/max(len(actors),1)
    for i, actor in enumerate(actors):
        ys = [case_pos[u][1] for u in actor.get("does", []) if u in case_pos]
        ay = (sum(ys)/len(ys)) if ys else (H-70-i*fallback_gap)
        ay = max(65, min(H-70, ay))
        _stick_figure(g, actor_x, ay, actor.get("label", "Actor"), .95)
        # One routing lane per actor keeps the system edge orderly.
        route_x = bx - 12 - i*3
        for uid in actor.get("does", []):
            if uid not in case_pos:
                continue
            tx, ty, _, _ = case_pos[uid]
            _orth_arrow(g, actor_x+17, ay+4, tx, ty, via_x=route_x,
                        color=LINE_DARK, sw=.9, head=False)

    d.add(g)
    return d


# Sequence Diagram — UML interaction.
def _api_endpoints(doc: dict) -> list[dict]:
    out = []
    for a in doc.get("api_design") or []:
        if not isinstance(a, dict):
            continue
        method = str(a.get("method") or "").upper().strip()
        path = str(a.get("path") or "").strip()
        if method and path:
            out.append({"method": method, "path": path, "raw": a})
    return out


def _match_endpoint(step: str, endpoints: list[dict]) -> dict | None:
    if not endpoints:
        return None
    st = _tokenize(step)
    verb = str(step).lower()
    method_pref = None
    if re.search(r"\b(create|add|submit|book|place|register|pay|write|post)\b", verb): method_pref = "POST"
    elif re.search(r"\b(update|edit|change|mark|set)\b", verb): method_pref = "PATCH"
    elif re.search(r"\b(delete|remove|cancel)\b", verb): method_pref = "DELETE"
    elif re.search(r"\b(view|browse|list|filter|track|read|get|search)\b", verb): method_pref = "GET"
    ranked = []
    for ep in endpoints:
        overlap = len(st & _tokenize(ep["path"]))
        if overlap <= 0:
            continue
        # Match endpoint methods to the workflow verb.
        if method_pref and ep["method"] != method_pref:
            continue
        score = overlap * 4 + (2 if method_pref else 0)
        ranked.append((score, ep))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return ranked[0][1] if ranked else None


def _matching_store(step: str, doc: dict) -> str | None:
    tables = ((doc.get("database_design") or {}).get("tables") or [])
    ranked = sorted(((_overlap(step, str(t.get("table_name") or "")), str(t.get("table_name") or ""))
                     for t in tables), reverse=True)
    return ranked[0][1] if ranked and ranked[0][0] > 0 else None


def _sequence(doc: dict, W: float) -> Drawing:
    workflows = doc.get("business_workflows") or []
    wf = workflows[0] if workflows else {}
    steps = [str(s) for s in (wf.get("steps") or []) if str(s).strip()][:7]
    if not steps:
        steps = [str(f) for f in ((doc.get("effective_plan") or doc.get("approved_plan") or {}).get("features") or []) if str(f).strip()][:5]
    if not steps:
        return _not_applicable(W, "Sequence Diagram", "No ordered user/system interaction is specified in the approved SRS.")

    roles = [str(r.get("role_name") or r.get("role_key")) for r in (doc.get("roles") or [])
             if r.get("role_name") or r.get("role_key")]
    scored = [(sum(1 for s in steps if re.search(rf"\b{re.escape(r)}\b", s, re.I)), r) for r in roles]
    role = max(scored, default=(0, "User"))[1]
    if scored and max(scored)[0] == 0:
        role = "User"
    endpoints = _api_endpoints(doc)
    has_db = bool(((doc.get("database_design") or {}).get("tables") or []))

    participants = [("U", role), ("WEB", "Web Application")]
    if endpoints:
        participants.append(("API", "Application API"))
    if has_db:
        participants.append(("DB", "Database"))

    n = len(participants)
    margin = 36
    xs = [margin + i*((W-2*margin)/(n-1 if n>1 else 1)) for i in range(n)]
    step_band = 88 if endpoints and has_db else 66
    H = 94 + len(steps)*step_band + 42
    d, g = _drawing(W, H)
    top = H-34

    # Participant heads and lifelines.
    for i, (_, label) in enumerate(participants):
        bw = min(132, (W-36)/n - 8)
        g.add(Rect(xs[i]-bw/2, top-13, bw, 26, fillColor=BLUE_BG,
                   strokeColor=LINE_DARK, strokeWidth=1.05))
        _text_block(g, xs[i], top, label, maxw=bw-12, size=8.4, font=FB, max_lines=2)
        _line(g, xs[i], top-13, xs[i], 24, LINE, .8, True)

    # Main application activation, narrow and unobtrusive.
    web_x = xs[1]
    g.add(Rect(web_x-4, 34, 8, top-61, fillColor=GREEN_BG, strokeColor=GREEN, strokeWidth=.7))

    y = top - 44
    api_index = next((i for i,p in enumerate(participants) if p[0]=="API"), None)
    db_index = next((i for i,p in enumerate(participants) if p[0]=="DB"), None)
    for i, step in enumerate(steps, 1):
        message = re.sub(rf"^\s*{re.escape(role)}\s+", "", _clean(step), flags=re.I) if role != "User" else _clean(step)
        _arrow(g, xs[0], y, web_x-5, y, color=INK, sw=.95,
               label=f"{i}. {message}", open_head=False, label_size=7.2)
        y -= 21
        ep = _match_endpoint(step, endpoints)
        store = _matching_store(step, doc)
        if ep and api_index is not None:
            ax = xs[api_index]
            _arrow(g, web_x+5, y, ax-4, y, color=INK, sw=.95,
                   label=f"{ep['method']} {ep['path']}", open_head=False, label_size=6.8)
            y -= 18
            if db_index is not None and store:
                dx = xs[db_index]
                op = "read/write" if ep["method"] not in {"GET"} else "read"
                _arrow(g, ax+4, y, dx, y, color=INK, sw=.9,
                       label=f"{op} {store}", open_head=False, label_size=6.6)
                y -= 16
                _arrow(g, dx, y, ax+4, y, color=INK, sw=.85, dashed=True,
                       label="record/result", open_head=True, label_size=6.5)
                y -= 16
            _arrow(g, ax-4, y, web_x+5, y, color=INK, sw=.85, dashed=True,
                   label="result", open_head=True, label_size=6.5)
            y -= 18
        elif db_index is not None and store:
            dx = xs[db_index]
            _arrow(g, web_x+5, y, dx, y, color=INK, sw=.9,
                   label=f"access {store}", open_head=False, label_size=6.7)
            y -= 17
            _arrow(g, dx, y, web_x+5, y, color=INK, sw=.85, dashed=True,
                   label="record/result", open_head=True, label_size=6.5)
            y -= 18
        _arrow(g, web_x-5, y, xs[0], y, color=INK, sw=.85, dashed=True,
               label="present outcome", open_head=True, label_size=6.6)
        y -= 28

    d.add(g)
    return d


# ERD — Crow's Foot.
def _table_name_map(tables: list[dict]) -> dict[str, str]:
    out = {}
    for t in tables:
        name = str(t.get("table_name") or "")
        if not name:
            continue
        variants = {name.lower(), name.lower().rstrip("s"), name.lower().replace("_", "")}
        for v in variants:
            out[v] = name
    return out


def _resolve_ref(ref, tables: list[dict], field_name: str = "") -> str | None:
    if isinstance(ref, dict):
        ref = ref.get("table") or ref.get("entity") or ref.get("collection") or ref.get("target")
    text = str(ref or "").strip()
    name_map = _table_name_map(tables)
    if text:
        first = re.split(r"[./:]", text)[0].strip().lower()
        for cand in (first, first.rstrip("s"), first.replace("_", "")):
            if cand in name_map:
                return name_map[cand]
    stem = re.sub(r"(?:_id|Id|ID)$", "", str(field_name or "")).lower().strip("_")
    for cand in (stem, stem.rstrip("s"), stem.replace("_", "")):
        if cand in name_map:
            return name_map[cand]
    return None


def _relations(doc: dict, tables: list[dict]) -> list[dict]:
    names = {str(t.get("table_name")) for t in tables}
    out: list[dict] = []
    seen = set()
    for r in ((doc.get("database_design") or {}).get("relationships") or []):
        a = str(r.get("from") or "").split(".")[0]
        b = str(r.get("to") or "").split(".")[0]
        if a in names and b in names and a != b:
            key = (a,b,str(r.get("type") or "one_to_many"))
            if key not in seen:
                seen.add(key)
                out.append({"from": a, "to": b, "type": str(r.get("type") or "one_to_many"),
                            "description": str(r.get("description") or "")})
    # Prefer explicit field references over name guessing.
    for child in tables:
        cname = str(child.get("table_name") or "")
        for fld in child.get("fields") or []:
            if not (fld.get("references") or str(fld.get("type") or "").lower() == "foreign_key"):
                continue
            parent = _resolve_ref(fld.get("references"), tables, str(fld.get("name") or ""))
            if not parent or parent == cname:
                continue
            typ = "one_to_one" if fld.get("unique") else "many_to_one"
            key = (cname,parent,typ)
            if key not in seen and (parent,cname,"one_to_many") not in seen:
                seen.add(key)
                out.append({"from": cname, "to": parent, "type": typ,
                            "description": str(fld.get("name") or "")})
    return out[:24]


def _entity_size(table: dict, width: float) -> tuple[float,float]:
    fields = (table.get("fields") or [])[:10]
    return width, 28 + max(2, len(fields))*14 + 12


def _entity_box(g: Group, x, y, w, h, table: dict):
    fields = (table.get("fields") or [])[:10]
    head = 27
    g.add(Rect(x, y, w, h, fillColor=WHITE, strokeColor=LINE_DARK, strokeWidth=1.15))
    g.add(Rect(x, y+h-head, w, head, fillColor=BLUE_BG, strokeColor=LINE_DARK, strokeWidth=1.15))
    _txt(g, x+w/2, y+h-18, _fit(str(table.get("table_name") or "Entity").replace("_"," ").title(), FB, 9.2, w-14),
         9.2, FB, INK, "middle")
    fy = y+h-head-15
    for fld in fields:
        pk = bool(fld.get("primary_key"))
        fk = bool(fld.get("references") or str(fld.get("type") or "").lower()=="foreign_key")
        key = "PK" if pk else ("FK" if fk else "")
        name = str(fld.get("name") or "field")
        typ = str(fld.get("type") or "")
        if typ == "foreign_key": typ = "ObjectId / FK"
        keyw = 24
        if key:
            _txt(g, x+7, fy, key, 6.8, FB, GREEN if key=="PK" else ORANGE)
        _txt(g, x+7+keyw, fy, _fit(name, FM, 7.2, w*0.48), 7.2, FM, INK)
        if typ:
            _txt(g, x+w-7, fy, _fit(typ, F, 6.8, w*0.33), 6.8, F, MUTED, "end")
        fy -= 14


def _crow_end(g: Group, x, y, angle: float, *, one=False, many=False, optional=False):
    ux, uy = math.cos(angle), math.sin(angle)
    px, py = -uy, ux
    if optional:
        cx, cy = x-7*ux, y-7*uy
        g.add(Circle(cx, cy, 3.2, fillColor=WHITE, strokeColor=INK, strokeWidth=.9))
    if one:
        bx, by = x-11*ux, y-11*uy
        _line(g, bx-5*px, by-5*py, bx+5*px, by+5*py, INK, .9)
    if many:
        bx, by = x-8*ux, y-8*uy
        _line(g, x, y, bx+7*px, by+7*py, INK, .9)
        _line(g, x, y, bx-7*px, by-7*py, INK, .9)
        _line(g, x, y, bx-8*ux, by-8*uy, INK, .9)


def _erd_layout(tables: list[dict], rels: list[dict], W: float):
    """Relation-aware layered layout."""
    names = [str(t.get("table_name") or f"T{i}") for i,t in enumerate(tables)]
    parents: dict[str,set[str]] = defaultdict(set)
    children: dict[str,set[str]] = defaultdict(set)
    for r in rels:
        a,b,typ = r["from"], r["to"], r["type"]
        if typ == "many_to_one": child,parent = a,b
        elif typ == "one_to_many": parent,child = a,b
        elif typ == "one_to_one": parent,child = b,a
        else: parent,child = a,b
        parents[child].add(parent); children[parent].add(child)
    depth = {n:0 for n in names}
    for _ in range(len(names)+1):
        changed=False
        for child, ps in parents.items():
            nd = 1 + max((depth.get(p,0) for p in ps), default=0)
            if nd > depth.get(child,0):
                depth[child]=min(nd,3); changed=True
        if not changed: break
    # Keep at most 3 columns for readability.
    cols = min(3 if W >= 640 and len(tables) >= 6 else 2, max(1, 1+max(depth.values(), default=0)))
    groups: dict[int,list[str]] = defaultdict(list)
    for n in names:
        groups[min(depth.get(n,0), cols-1)].append(n)
    degree = {n:len(parents[n])+len(children[n]) for n in names}
    for c in groups:
        groups[c].sort(key=lambda n:(-degree[n],n.lower()))
    if len(groups) < cols:
        for c in range(cols): groups[c] = groups[c]
    return cols, groups


def _erd(doc: dict, W: float) -> Drawing:
    db = doc.get("database_design") or {}
    tables = [t for t in (db.get("tables") or []) if isinstance(t,dict) and t.get("table_name")][:10]
    if not tables:
        return _not_applicable(W, "Entity-Relationship Diagram", "No persistent entities are specified in the SRS data model.")
    rels = _relations(doc, tables)
    cols, groups = _erd_layout(tables, rels, W)
    margin_x = 18
    gap_x = 28
    cw = (W - 2*margin_x - gap_x*(cols-1))/cols
    cw = max(150, cw)
    table_by_name = {str(t.get("table_name")):t for t in tables}
    # Determine column heights first.
    sizes = {n:_entity_size(table_by_name[n], cw) for n in table_by_name}
    gap_y = 28
    col_heights = {}
    for c in range(cols):
        items = groups.get(c,[])
        col_heights[c] = sum(sizes[n][1] for n in items) + gap_y*max(0,len(items)-1)
    H = max(300, max(col_heights.values(), default=220) + 52)
    d,g = _drawing(W,H)
    pos: dict[str,tuple[float,float,float,float]] = {}
    for c in range(cols):
        items=groups.get(c,[])
        total=col_heights.get(c,0)
        y = H - 28 - max(0,(H-52-total)/2)
        x = margin_x + c*(cw+gap_x)
        for n in items:
            w,h=sizes[n]
            y -= h
            pos[n]=(x,y,w,h)
            y -= gap_y

    same_col_offsets = defaultdict(int)
    for r in rels:
        a,b = r["from"],r["to"]
        if a not in pos or b not in pos or a==b: continue
        ax,ay,aw,ah=pos[a]; bx,by,bw,bh=pos[b]
        acx,acy=ax+aw/2,ay+ah/2; bcx,bcy=bx+bw/2,by+bh/2
        if abs(acx-bcx) > 8:
            if acx < bcx:
                x1,y1=ax+aw,acy; x2,y2=bx,bcy
            else:
                x1,y1=ax,acy; x2,y2=bx+bw,bcy
            mid=(x1+x2)/2
            _line(g,x1,y1,mid,y1,LINE_DARK,.85)
            _line(g,mid,y1,mid,y2,LINE_DARK,.85)
            _line(g,mid,y2,x2,y2,LINE_DARK,.85)
            ang1=0 if x2>x1 else math.pi
            ang2=math.pi if x2>x1 else 0
        else:
            idx=same_col_offsets[int(round(acx))]; same_col_offsets[int(round(acx))]+=1
            outside = min(W-8, ax+aw+12+idx*8) if acx < W/2 else max(8, ax-12-idx*8)
            x1,y1=(ax+aw,acy) if outside>acx else (ax,acy)
            x2,y2=(bx+bw,bcy) if outside>bcx else (bx,bcy)
            _line(g,x1,y1,outside,y1,LINE_DARK,.85); _line(g,outside,y1,outside,y2,LINE_DARK,.85); _line(g,outside,y2,x2,y2,LINE_DARK,.85)
            ang1=0 if outside>x1 else math.pi; ang2=math.pi if outside>x2 else 0
        typ=r.get("type") or "one_to_many"
        if typ=="one_to_many":
            _crow_end(g,x1,y1,ang1,one=True); _crow_end(g,x2,y2,ang2,many=True)
        elif typ=="many_to_one":
            _crow_end(g,x1,y1,ang1,many=True); _crow_end(g,x2,y2,ang2,one=True)
        elif typ=="many_to_many":
            _crow_end(g,x1,y1,ang1,many=True); _crow_end(g,x2,y2,ang2,many=True)
        else:
            _crow_end(g,x1,y1,ang1,one=True); _crow_end(g,x2,y2,ang2,one=True)

    for n,(x,y,w,h) in pos.items():
        _entity_box(g,x,y,w,h,table_by_name[n])
    if not rels:
        _txt(g, W/2, 14, "No explicit foreign-key relationships are specified; entities are shown independently.",
             7.2, F, MUTED, "middle")
    d.add(g)
    return d


# Activity Diagram — UML behavioral.
