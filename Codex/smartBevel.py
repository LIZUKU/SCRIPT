# -*- coding: utf-8 -*-
import maya.cmds as cmds
import maya.mel as mel
import math
import __main__

SOURCE_SET_NAME    = "TMP_bevel_sourceEdges_SET"
ALL_EDGES_SET_NAME = "TMP_bevel_allEdges_SET"

PARALLEL_DOT_THRESHOLD = 0.70
# New edges too short compared to nearest source edge are rejected
MIN_LENGTH_RATIO = 0.35
# New edges too long compared to nearest source edge are rejected
MAX_LENGTH_RATIO = 2.25
# New edges too far from source mids are rejected
MAX_MID_DISTANCE_RATIO = 1.75
# Endpoint locality fallback: max distance from edge endpoints to source vertices
MAX_VERTEX_DISTANCE_RATIO = 1.25

# -- Utils --------------------------------------------------------------------

def _delete_if_exists(node):
    if cmds.objExists(node):
        try: cmds.delete(node)
        except: pass

def _flatten(items):
    return cmds.ls(items, fl=True) if items else []

def _cleanup():
    for s in [SOURCE_SET_NAME, ALL_EDGES_SET_NAME]:
        _delete_if_exists(s)

def _kill_existing_job():
    state = getattr(__main__, "_bevel_listener_state", None)
    if not state: return
    jid = state.get("scriptJob")
    if jid and cmds.scriptJob(exists=jid):
        try: cmds.scriptJob(kill=jid, force=True)
        except: pass

def _transform_from_edge(edge):
    obj = edge.split(".e[")[0]
    if cmds.objExists(obj) and cmds.nodeType(obj) == "mesh":
        p = cmds.listRelatives(obj, p=True, f=False) or []
        if p: return p[0]
    return obj

def _transforms_from_edges(edges):
    seen, out = set(), []
    for e in edges:
        if ".e[" not in e: continue
        t = _transform_from_edge(e)
        if t and t not in seen:
            seen.add(t); out.append(t)
    return out

def _all_edges_pattern(transforms):
    return ["{}.e[*]".format(t) for t in transforms if cmds.objExists(t)]

def _make_set(name, members):
    _delete_if_exists(name)
    if not members:
        raise RuntimeError("Cannot create set '{}': no members.".format(name))
    return cmds.sets(members, n=name)

# -- Vector math --------------------------------------------------------------

def _vsub(a, b): return [a[0]-b[0], a[1]-b[1], a[2]-b[2]]
def _vadd(a, b): return [a[0]+b[0], a[1]+b[1], a[2]+b[2]]
def _vmul(v, s): return [v[0]*s, v[1]*s, v[2]*s]
def _vlen(v):    return math.sqrt(v[0]**2 + v[1]**2 + v[2]**2)
def _vnorm(v):
    l = _vlen(v)
    return [v[0]/l, v[1]/l, v[2]/l] if l > 1e-8 else [0,0,0]
def _vdot(a, b): return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]
def _dsq(a, b):  return sum((a[i]-b[i])**2 for i in range(3))

# -- Geometry -----------------------------------------------------------------

def _edge_verts(edge):
    v = _flatten(cmds.polyListComponentConversion(edge, fe=True, tv=True))
    return v[:2]

def _vpos(vtx):
    return cmds.pointPosition(vtx, world=True)

def _edge_data(edge):
    verts = _edge_verts(edge)
    if len(verts) != 2: return None
    p1, p2 = _vpos(verts[0]), _vpos(verts[1])
    raw = _vsub(p2, p1)
    l   = _vlen(raw)
    if l < 1e-8: return None
    return {
        "edge"     : edge,
        "transform": _transform_from_edge(edge),
        "verts"    : verts,
        "v1": p1, "v2": p2,
        "mid"      : _vmul(_vadd(p1, p2), 0.5),
        "dir"      : _vnorm(raw),
        "len"      : l,
    }

def _edge_count_per_vertex(edges):
    counts = {}
    for e in edges:
        for v in _edge_verts(e):
            counts[v] = counts.get(v, 0) + 1
    return counts

def _end_vertices(edges):
    counts = _edge_count_per_vertex(edges)
    return set(v for v, c in counts.items() if c == 1)

def _is_closed_group(edges):
    counts = _edge_count_per_vertex(edges)
    return bool(counts) and all(c == 2 for c in counts.values())

def _chain_direction(edges):
    datas = [d for d in (_edge_data(e) for e in edges) if d]
    if not datas: return None
    if len(datas) == 1: return datas[0]["dir"]
    mids = [d["mid"] for d in datas]
    best = max(((i,j) for i in range(len(mids)) for j in range(i+1, len(mids))),
               key=lambda p: _dsq(mids[p[0]], mids[p[1]]))
    return _vnorm(_vsub(mids[best[1]], mids[best[0]]))

# -- Groups -------------------------------------------------------------------

def _build_groups(source_edges):
    datas = [d for d in (_edge_data(e) for e in source_edges) if d]
    if not datas: return []

    by_transform = {}
    for d in datas: by_transform.setdefault(d["transform"], []).append(d)

    groups, gid = [], 0
    for transform, tdatas in by_transform.items():
        v2edges = {}
        for d in tdatas:
            for v in d["verts"]: v2edges.setdefault(v, []).append(d["edge"])

        lookup  = {d["edge"]: d for d in tdatas}
        visited = set()

        for d in tdatas:
            if d["edge"] in visited: continue
            stack, comp = [d["edge"]], []
            while stack:
                cur = stack.pop()
                if cur in visited: continue
                visited.add(cur); comp.append(cur)
                for v in lookup[cur]["verts"]:
                    for nb in v2edges.get(v, []):
                        if nb not in visited: stack.append(nb)

            comp_datas  = [lookup[e] for e in comp if e in lookup]
            mids        = [x["mid"] for x in comp_datas]
            closed      = _is_closed_group(comp)
            avg_len     = sum(x["len"] for x in comp_datas) / len(comp_datas)

            groups.append({
                "id"           : gid,
                "transform"    : transform,
                "source_edges" : comp[:],
                "source_datas" : comp_datas[:],
                "source_dir"   : _chain_direction(comp) if not closed else None,
                "end_vertices" : _end_vertices(comp),
                "mids"         : mids[:],
                "source_count" : len(comp),
                "closed"       : closed,
                "avg_len"      : avg_len,
                "source_verts" : list({v for d in comp_datas for v in d["verts"]}),
            })
            gid += 1

    return groups

def _best_group(edge_data, groups):
    candidates = [g for g in groups if g["transform"] == edge_data["transform"]]
    if not candidates: return None
    return min(candidates,
               key=lambda g: min(_dsq(edge_data["mid"], m) for m in g["mids"]) if g["mids"] else float("inf"))

def _best_local_source(edge_data, group):
    if not group.get("source_datas"):
        return None
    return min(group["source_datas"], key=lambda d: _dsq(edge_data["mid"], d["mid"]))

# -- Filter -------------------------------------------------------------------

def _passes_locality_and_length(edge_data, group):
    local_source = _best_local_source(edge_data, group)
    if not local_source:
        return False

    # Gate 0 — locality (reject branches/fans that drift away from source mids)
    src_len = max(local_source["len"], 1e-8)
    max_mid_dist = src_len * MAX_MID_DISTANCE_RATIO
    nearest_mid_dist = math.sqrt(_dsq(edge_data["mid"], local_source["mid"]))
    near_mid_ok = nearest_mid_dist <= max_mid_dist

    # Fallback locality for corner/complex bevels:
    # keep edges whose endpoints remain close to nearest source edge endpoints.
    ref_v1, ref_v2 = local_source["v1"], local_source["v2"]
    end_to_source = min(
        math.sqrt(_dsq(edge_data["v1"], ref_v1)),
        math.sqrt(_dsq(edge_data["v1"], ref_v2)),
        math.sqrt(_dsq(edge_data["v2"], ref_v1)),
        math.sqrt(_dsq(edge_data["v2"], ref_v2))
    )

    max_vtx_dist = src_len * MAX_VERTEX_DISTANCE_RATIO
    near_vtx_ok = end_to_source <= max_vtx_dist

    if not (near_mid_ok or near_vtx_ok):
        return False

    # Gate 1 — length
    edge_to_source_ratio = edge_data["len"] / src_len
    return MIN_LENGTH_RATIO <= edge_to_source_ratio <= MAX_LENGTH_RATIO

def _edge_passes_group(edge_data, group, threshold=PARALLEL_DOT_THRESHOLD):
    if not _passes_locality_and_length(edge_data, group):
        return False

    # Gate 2 — direction (always local, handles cornered/open source selections better)
    local_source = _best_local_source(edge_data, group)
    ref_dir = local_source.get("dir") if local_source else None
    if not ref_dir:
        return False

    return abs(_vdot(edge_data["dir"], ref_dir)) >= threshold

def _filter_edges(new_edges, groups, threshold=PARALLEL_DOT_THRESHOLD):
    """
    Three gates — all must pass:
      1. Parallel  : |cos(angle)| >= threshold
      2. Length    : MIN_LENGTH_RATIO <= edge.len / nearest_source.len <= MAX_LENGTH_RATIO
                     (rejects caps/corner drifts that do not match the local source edge scale)
      3. Group     : same transform, nearest source group
    """
    kept_by_group = {g["id"]: [] for g in groups}
    removed       = []

    for edge in new_edges:
        data = _edge_data(edge)
        if not data: removed.append(edge); continue

        group = _best_group(data, groups)
        if not group: removed.append(edge); continue

        if _edge_passes_group(data, group, threshold=threshold):
            kept_by_group[group["id"]].append(edge)
        else:
            removed.append(edge)

    return kept_by_group, removed

# -- Loop expansion -----------------------------------------------------------

def _expand_loops(kept_by_group, groups, allowed_edges=None):
    allowed = set(allowed_edges or [])
    allowed_data = {e: _edge_data(e) for e in allowed}
    allowed_data = {e: d for e, d in allowed_data.items() if d}

    by_vertex = {}
    for e, d in allowed_data.items():
        for v in d["verts"]:
            by_vertex.setdefault(v, []).append(e)

    final, seen = [], set()
    for group in groups:
        edges = kept_by_group.get(group["id"], [])
        if not edges: continue

        cmds.select(edges, r=True)
        if group["source_count"] > 1 and not group["closed"]:
            try: mel.eval("SelectEdgeLoopSp;")
            except Exception as e:
                print("[Bevel] SelectEdgeLoopSp failed for group {}: {}".format(group["id"], e))

        mel_edges = _flatten(cmds.ls(sl=True)) or []
        for e in mel_edges:
            if allowed and e not in allowed:
                continue
            data = _edge_data(e)
            if not data or not _edge_passes_group(data, group):
                continue
            if e not in seen:
                seen.add(e); final.append(e)

        # Connectivity rescue:
        # Maya's loop selection can miss strips on corner/rounded profiles.
        # Starting from strict seed edges, grow through allowed new edges using only locality+length.
        stack = [e for e in edges if e in allowed_data]
        visited = set(stack)
        while stack:
            cur = stack.pop()
            cur_data = allowed_data.get(cur)
            if not cur_data:
                continue
            if _passes_locality_and_length(cur_data, group) and cur not in seen:
                seen.add(cur); final.append(cur)

            for v in cur_data["verts"]:
                for nb in by_vertex.get(v, []):
                    if nb in visited:
                        continue
                    visited.add(nb)
                    nb_data = allowed_data.get(nb)
                    if nb_data and _passes_locality_and_length(nb_data, group):
                        stack.append(nb)

    return final

# -- Bevel nodes --------------------------------------------------------------

def _bevel_nodes(transforms):
    nodes = set()
    for t in transforms:
        for node in (cmds.listHistory(t, pruneDagObjects=True) or []):
            if (cmds.nodeType(node) or "").startswith("polyBevel"):
                nodes.add(node)
    return nodes

def _configure_bevel(nodes):
    for node in nodes:
        if not cmds.objExists(node): continue
        for attr, val in [("subdivideNgons", 1), ("mitering", 0), ("miterAlong", 0)]:
            if cmds.attributeQuery(attr, node=node, exists=True):
                try: cmds.setAttr("{}.{}".format(node, attr), val)
                except: pass

# -- Callback -----------------------------------------------------------------

def _bevel_tool_changed_callback():
    state = getattr(__main__, "_bevel_listener_state", None)
    if not state or state.get("done"): return

    try:    ctx = cmds.currentCtx()
    except: return
    if ctx != "selectSuperContext": return

    state["done"] = True
    jid = state.get("scriptJob")
    if jid and cmds.scriptJob(exists=jid):
        try: cmds.scriptJob(kill=jid, force=True)
        except: pass

    transforms    = state.get("transforms", [])
    all_edges_set = state.get("all_edges_set")
    source_groups = state.get("source_groups", [])

    if not transforms or not all_edges_set or not cmds.objExists(all_edges_set):
        cmds.warning("[Bevel] Invalid state."); _cleanup(); return
    if not source_groups:
        cmds.warning("[Bevel] No source groups."); _cleanup(); return

    cmds.select(_all_edges_pattern(transforms), r=True)
    old = _flatten(cmds.sets(all_edges_set, q=True))
    if old: cmds.select(old, d=True)

    new_edges = _flatten(cmds.ls(sl=True)) or []
    if not new_edges:
        cmds.warning("[Bevel] No new edges detected."); _cleanup(); return

    kept_by_group, removed = _filter_edges(new_edges, source_groups)
    final = _expand_loops(kept_by_group, source_groups, allowed_edges=new_edges)

    if final:
        cmds.select(final, r=True)
        print("[Bevel] {} new | {} kept | {} removed | {} final.".format(
            len(new_edges),
            sum(len(v) for v in kept_by_group.values()),
            len(removed), len(final)
        ))
        try:
            cmds.inViewMessage(
                amg='Bevel done: <hl>{}</hl> edges selected.'.format(len(final)),
                pos='midCenterTop', fade=True)
        except: pass
    else:
        cmds.select(new_edges, r=True)
        cmds.warning("[Bevel] No edges passed filters, falling back to all new edges.")

    _cleanup()

# -- Entry point --------------------------------------------------------------

def start_bevel_listener():
    sel   = _flatten(cmds.ls(sl=True)) or []
    edges = [x for x in sel if ".e[" in x]
    if not edges:
        cmds.warning("Select at least one edge before running."); return

    _kill_existing_job()
    _delete_if_exists(SOURCE_SET_NAME)
    _delete_if_exists(ALL_EDGES_SET_NAME)

    transforms = _transforms_from_edges(edges)
    if not transforms:
        cmds.warning("Cannot resolve transforms from selection."); return

    groups = _build_groups(edges)
    if not groups:
        cmds.warning("Cannot build source groups."); return

    _make_set(SOURCE_SET_NAME, edges)

    cmds.select(_all_edges_pattern(transforms), r=True)
    all_flat = _flatten(cmds.ls(sl=True))
    if not all_flat:
        cmds.warning("Cannot retrieve all edges."); return

    all_set = _make_set(ALL_EDGES_SET_NAME, all_flat)
    cmds.select(edges, r=True)

    before_nodes = _bevel_nodes(transforms)

    __main__._bevel_listener_state = {
        "done"          : False,
        "scriptJob"     : None,
        "transforms"    : transforms,
        "all_edges_set" : all_set,
        "source_groups" : groups,
    }

    mel.eval('dR_DoCmd("bevelPress");')

    after_nodes = _bevel_nodes(transforms)
    new_nodes   = list(after_nodes - before_nodes) or list(after_nodes)
    _configure_bevel(new_nodes)

    jid = cmds.scriptJob(
        event=["ToolChanged", "__import__('__main__')._bevel_tool_changed_callback()"],
        protected=True
    )
    __main__._bevel_listener_state["scriptJob"] = jid

    print("[Bevel] Bevel started. Press Q to finish.")
    try:
        cmds.inViewMessage(amg='Bevel started. Press <hl>Q</hl> to finish.',
                           pos='midCenterTop', fade=True)
    except: pass

start_bevel_listener()
