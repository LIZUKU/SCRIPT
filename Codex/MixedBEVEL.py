# -*- coding: utf-8 -*-
"""
BevelUnbevel - Script fusionné
1. Sélectionner des edges → lancer le script
2. Régler le bevel interactivement (segments, depth, etc.)
3. Appuyer sur Q → les edges résultantes sont auto-sélectionnées
4. Le dragger UnBevel démarre automatiquement
   - Drag          : ajuste uniformément A et B
   - Shift + Drag  : ajuste A uniquement
   - Ctrl + Drag   : ajuste B uniquement
   - Ctrl+Shift    : pas à pas fin
   - Ctrl+Alt      : reset à 0
"""

import maya.cmds as cmds
import maya.mel as mel
import maya.api.OpenMaya as om2
import math
import re
import __main__

# --------------------------------------------------------------------------
# Constantes
# --------------------------------------------------------------------------

SOURCE_SET_NAME    = "TMP_bevel_sourceEdges_SET"
ALL_EDGES_SET_NAME = "TMP_bevel_allEdges_SET"

PERP_DOT_THRESHOLD      = 0.35
COLLINEAR_DOT_THRESHOLD = 0.92

# --------------------------------------------------------------------------
# Utils généraux
# --------------------------------------------------------------------------

def _delete_if_exists(node):
    if cmds.objExists(node):
        try:
            cmds.delete(node)
        except Exception:
            pass

def _flatten(items):
    return cmds.ls(items, fl=True) if items else []

def _cleanup():
    for s in [SOURCE_SET_NAME, ALL_EDGES_SET_NAME]:
        _delete_if_exists(s)

def _kill_existing_job():
    state = getattr(__main__, "_bevel_listener_state", None)
    if not state:
        return
    jid = state.get("scriptJob")
    if jid and cmds.scriptJob(exists=jid):
        try:
            cmds.scriptJob(kill=jid, force=True)
        except Exception:
            pass

def _kill_existing_unbevel_job():
    state = getattr(__main__, "_unbevel_state", None)
    if not state:
        return
    jid = state.get("toolChangeJob")
    if jid and cmds.scriptJob(exists=jid):
        try:
            cmds.scriptJob(kill=jid, force=True)
        except Exception:
            pass

def _transform_from_edge(edge):
    obj = edge.split(".e[")[0]
    if cmds.objExists(obj) and cmds.nodeType(obj) == "mesh":
        parents = cmds.listRelatives(obj, p=True, f=False) or []
        if parents:
            return parents[0]
    return obj

def _transforms_from_edges(edges):
    seen = set()
    out = []
    for e in edges:
        if ".e[" not in e:
            continue
        t = _transform_from_edge(e)
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out

def _all_edges_pattern(transforms):
    return ["{}.e[*]".format(t) for t in transforms if cmds.objExists(t)]

def _make_set(name, members):
    _delete_if_exists(name)
    if not members:
        raise RuntimeError("Cannot create set '{}': no members.".format(name))
    return cmds.sets(members, n=name)

# --------------------------------------------------------------------------
# Vecteurs
# --------------------------------------------------------------------------

def _vsub(a, b):  return [a[0]-b[0], a[1]-b[1], a[2]-b[2]]
def _vadd(a, b):  return [a[0]+b[0], a[1]+b[1], a[2]+b[2]]
def _vmul(v, s):  return [v[0]*s,    v[1]*s,    v[2]*s   ]
def _vlen(v):     return math.sqrt(v[0]**2 + v[1]**2 + v[2]**2)
def _vnorm(v):
    l = _vlen(v)
    return [v[0]/l, v[1]/l, v[2]/l] if l > 1e-8 else [0.0, 0.0, 0.0]
def _vdot(a, b):  return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]
def _dsq(a, b):   return sum((a[i]-b[i])**2 for i in range(3))

# --------------------------------------------------------------------------
# Géométrie edges
# --------------------------------------------------------------------------

def _edge_verts(edge):
    verts = _flatten(cmds.polyListComponentConversion(edge, fe=True, tv=True))
    return verts[:2]

def _vpos(vtx):
    return cmds.pointPosition(vtx, world=True)

def _edge_data(edge):
    verts = _edge_verts(edge)
    if len(verts) != 2:
        return None
    p1 = _vpos(verts[0])
    p2 = _vpos(verts[1])
    raw = _vsub(p2, p1)
    length = _vlen(raw)
    if length < 1e-8:
        return None
    return {
        "edge": edge,
        "transform": _transform_from_edge(edge),
        "verts": verts,
        "v1": p1, "v2": p2,
        "mid": _vmul(_vadd(p1, p2), 0.5),
        "dir": _vnorm(raw),
        "len": length,
    }

# --------------------------------------------------------------------------
# Groupes source
# --------------------------------------------------------------------------

def _build_groups(source_edges):
    datas = [d for d in (_edge_data(e) for e in source_edges) if d]
    if not datas:
        return []

    by_transform = {}
    for d in datas:
        by_transform.setdefault(d["transform"], []).append(d)

    groups = []
    gid = 0
    for transform, tdatas in by_transform.items():
        v2edges = {}
        for d in tdatas:
            for v in d["verts"]:
                v2edges.setdefault(v, []).append(d["edge"])

        lookup = {d["edge"]: d for d in tdatas}
        visited = set()

        for d in tdatas:
            if d["edge"] in visited:
                continue
            stack = [d["edge"]]
            comp = []
            while stack:
                cur = stack.pop()
                if cur in visited:
                    continue
                visited.add(cur)
                comp.append(cur)
                for v in lookup[cur]["verts"]:
                    for nb in v2edges.get(v, []):
                        if nb not in visited:
                            stack.append(nb)

            comp_datas = [lookup[e] for e in comp if e in lookup]
            mids = [x["mid"] for x in comp_datas]
            avg_len = sum(x["len"] for x in comp_datas) / float(len(comp_datas))
            groups.append({
                "id": gid,
                "transform": transform,
                "source_edges": comp[:],
                "source_datas": comp_datas[:],
                "mids": mids[:],
                "avg_len": avg_len,
            })
            gid += 1
    return groups

def _best_group(edge_data, groups):
    candidates = [g for g in groups if g["transform"] == edge_data["transform"]]
    if not candidates:
        return None
    return min(candidates,
               key=lambda g: min(_dsq(edge_data["mid"], m) for m in g["mids"]) if g["mids"] else float("inf"))

def _best_local_source_data(edge_data, group):
    return min(group["source_datas"], key=lambda d: _dsq(edge_data["mid"], d["mid"]))

# --------------------------------------------------------------------------
# Filtres
# --------------------------------------------------------------------------

def _edge_matches_group(edge, group, threshold=PERP_DOT_THRESHOLD):
    data = _edge_data(edge)
    if not data:
        return False
    if data["transform"] != group["transform"]:
        return False
    source_ref = _best_local_source_data(data, group)
    if not source_ref:
        return False
    dot = abs(_vdot(data["dir"], source_ref["dir"]))
    return dot <= threshold

def _filter_edges(new_edges, groups, threshold=PERP_DOT_THRESHOLD):
    kept_by_group = {g["id"]: [] for g in groups}
    removed = []
    for edge in new_edges:
        data = _edge_data(edge)
        if not data:
            removed.append(edge)
            continue
        group = _best_group(data, groups)
        if not group:
            removed.append(edge)
            continue
        if _edge_matches_group(edge, group, threshold):
            kept_by_group[group["id"]].append(edge)
        else:
            removed.append(edge)
    return kept_by_group, removed

def _collect_final_edges(kept_by_group, groups):
    final = []
    seen = set()
    for group in groups:
        for e in kept_by_group.get(group["id"], []):
            if e not in seen:
                seen.add(e)
                final.append(e)
    return final

# --------------------------------------------------------------------------
# Extension collinéaire aux extrémités border
# --------------------------------------------------------------------------

def _vertex_id(vtx):
    m = re.search(r'\.vtx\[(\d+)\]', vtx)
    return int(m.group(1)) if m else None

def _edge_vertex_ids(edge):
    verts = _edge_verts(edge)
    ids = []
    for v in verts:
        vid = _vertex_id(v)
        if vid is not None:
            ids.append(vid)
    return ids

def _build_edge_connectivity(edges):
    edge_to_vids = {}
    vtx_to_edges = {}
    for e in edges:
        vids = _edge_vertex_ids(e)
        if len(vids) != 2:
            continue
        edge_to_vids[e] = vids
        for vid in vids:
            vtx_to_edges.setdefault(vid, []).append(e)
    return edge_to_vids, vtx_to_edges

def _connected_edge_components(edges):
    edge_to_vids, vtx_to_edges = _build_edge_connectivity(edges)
    visited = set()
    comps = []
    for e in edge_to_vids:
        if e in visited:
            continue
        stack = [e]
        comp = []
        while stack:
            cur = stack.pop()
            if cur in visited:
                continue
            visited.add(cur)
            comp.append(cur)
            for vid in edge_to_vids[cur]:
                for nb in vtx_to_edges.get(vid, []):
                    if nb not in visited:
                        stack.append(nb)
        comps.append(comp)
    return comps

def _component_endpoint_vertices(comp):
    edge_to_vids, vtx_to_edges = _build_edge_connectivity(comp)
    endpoints = []
    for vid, edges in vtx_to_edges.items():
        if len(edges) == 1:
            endpoints.append((vid, edges[0]))
    return endpoints

def _is_border_edge(edge):
    info = cmds.polyInfo(edge, edgeToFace=True) or []
    if not info:
        return False
    nums = [int(x) for x in re.findall(r'\d+', info[0])]
    return len(nums) == 2

def _is_border_vertex(vtx):
    try:
        edges = _flatten(cmds.polyListComponentConversion(vtx, fv=True, te=True)) or []
        for e in edges:
            faces = cmds.polyInfo(e, edgeToFace=True) or []
            if not faces:
                continue
            nums = [int(x) for x in re.findall(r'\d+', faces[0])]
            if len(nums) == 2:
                return True
    except Exception:
        pass
    return False

def _component_border_endpoints(comp):
    endpoints = _component_endpoint_vertices(comp)
    out = []
    for vid, end_edge in endpoints:
        mesh = end_edge.split(".e[")[0]
        vtx = "{}.vtx[{}]".format(mesh, vid)
        if _is_border_vertex(vtx):
            out.append((vid, end_edge))
    return out

def _best_collinear_neighbor(edge, vertex_id, selected_set, dot_threshold=COLLINEAR_DOT_THRESHOLD):
    data = _edge_data(edge)
    if not data:
        return None
    vids = _edge_vertex_ids(edge)
    if len(vids) != 2:
        return None
    mesh = edge.split(".e[")[0]
    vtx = "{}.vtx[{}]".format(mesh, vertex_id)
    connected_edges = _flatten(cmds.polyListComponentConversion(vtx, fv=True, te=True)) or []
    candidates = [e for e in connected_edges if e != edge and e not in selected_set]
    best = None
    best_dot = -1.0
    for cand in candidates:
        cdata = _edge_data(cand)
        if not cdata:
            continue
        if cdata["transform"] != data["transform"]:
            continue
        dot = abs(_vdot(data["dir"], cdata["dir"]))
        if dot >= dot_threshold and dot > best_dot:
            best_dot = dot
            best = cand
    return best

def _extend_final_edges_once(final_edges):
    final_edges = _flatten(final_edges)
    if not final_edges:
        return final_edges
    selected_set = set(final_edges)
    comps = _connected_edge_components(final_edges)
    added = []
    for comp in comps:
        endpoints = _component_border_endpoints(comp)
        for vid, end_edge in endpoints:
            extra = _best_collinear_neighbor(end_edge, vid, selected_set)
            if extra and extra not in selected_set and _is_border_edge(extra):
                added.append(extra)
                selected_set.add(extra)
    return final_edges + added

# --------------------------------------------------------------------------
# Nœuds bevel
# --------------------------------------------------------------------------

def _bevel_nodes(transforms):
    nodes = set()
    for t in transforms:
        for node in (cmds.listHistory(t, pruneDagObjects=True) or []):
            if (cmds.nodeType(node) or "").startswith("polyBevel"):
                nodes.add(node)
    return nodes

def _configure_bevel(nodes):
    for node in nodes:
        if not cmds.objExists(node):
            continue
        for attr, val in [("subdivideNgons", 1), ("mitering", 0), ("miterAlong", 0)]:
            if cmds.attributeQuery(attr, node=node, exists=True):
                try:
                    cmds.setAttr("{}.{}".format(node, attr), val)
                except Exception:
                    pass

# --------------------------------------------------------------------------
# UnBevel – maths
# --------------------------------------------------------------------------

def _distance_between_vtx(p1_name, p2_name):
    pA = cmds.pointPosition(p1_name, w=True)
    pB = cmds.pointPosition(p2_name, w=True)
    return math.sqrt(sum((pA[i]-pB[i])**2 for i in range(3)))

def _angle_between_three_p(pA_name, pB_name, pC_name):
    a = cmds.pointPosition(pA_name, w=True)
    b = cmds.pointPosition(pB_name, w=True)
    c = cmds.pointPosition(pC_name, w=True)
    ba = [a[i]-b[i] for i in range(3)]
    bc = [c[i]-b[i] for i in range(3)]
    nba = math.sqrt(sum(x**2 for x in ba))
    nbc = math.sqrt(sum(x**2 for x in bc))
    ba = [x/nba for x in ba]
    bc = [x/nbc for x in bc]
    scalar = max(-1.0, min(1.0, sum(aa*bb for aa, bb in zip(ba, bc))))
    return math.acos(scalar)

def _vtx_loop_order_check(edgelist):
    selEdges = cmds.ls(edgelist, fl=True) or []
    if not selEdges:
        return (0, [])

    shapeNode = cmds.listRelatives(selEdges[0], fullPath=True, parent=True)
    if not shapeNode:
        return (0, [])
    transformNode = cmds.listRelatives(shapeNode[0], fullPath=True, parent=True)
    if not transformNode:
        return (0, [])
    edgeNumberList = []
    for a in selEdges:
        checkNumber = a.split('.')[1].split('\n')[0].split(' ')
        for c in checkNumber:
            findNumber = ''.join([n for n in c.split('|')[-1] if n.isdigit()])
            if findNumber:
                edgeNumberList.append(findNumber)

    getNumber = []
    for s in selEdges:
        evlist = cmds.polyInfo(s, ev=True)
        if not evlist:
            continue
        checkNumber = evlist[0].split(':')[1].split('\n')[0].split(' ')
        for c in checkNumber:
            findNumber = ''.join([n for n in c.split('|')[-1] if n.isdigit()])
            if findNumber:
                getNumber.append(findNumber)

    dup = set([x for x in getNumber if getNumber.count(x) > 1])
    getHeadTail = list(set(getNumber) - dup)
    checkCircleState = 0
    if not getNumber:
        return (0, [])
    if not getHeadTail:
        checkCircleState = 1
        getHeadTail.append(getNumber[0])
    vftOrder = []
    vftOrder.append(getHeadTail[0])
    count = 0
    while len(dup) > 0 and count < 1000:
        checkVtx = transformNode[0] + '.vtx[' + vftOrder[-1] + ']'
        velist = cmds.polyInfo(checkVtx, ve=True)
        if not velist:
            break
        getNumber = []
        checkNumber = velist[0].split(':')[1].split('\n')[0].split(' ')
        for c in checkNumber:
            findNumber = ''.join([n for n in c.split('|')[-1] if n.isdigit()])
            if findNumber:
                getNumber.append(findNumber)
        findNextEdge = None
        for g in getNumber:
            if g in edgeNumberList:
                findNextEdge = g
        if not findNextEdge:
            break
        edgeNumberList.remove(findNextEdge)
        checkVtx = transformNode[0] + '.e[' + findNextEdge + ']'
        findVtx = cmds.polyInfo(checkVtx, ev=True)
        if not findVtx:
            break
        getNumber = []
        checkNumber = findVtx[0].split(':')[1].split('\n')[0].split(' ')
        for c in checkNumber:
            findNumber = ''.join([n for n in c.split('|')[-1] if n.isdigit()])
            if findNumber:
                getNumber.append(findNumber)
        gotNextVtx = None
        for g in getNumber:
            if g in dup:
                gotNextVtx = g
        if not gotNextVtx:
            break
        dup.remove(gotNextVtx)
        vftOrder.append(gotNextVtx)
        count += 1

    if checkCircleState == 0:
        if len(getHeadTail) < 2:
            return (checkCircleState, [transformNode[0] + '.vtx[' + v + ']' for v in vftOrder if v])
        vftOrder.append(getHeadTail[1])
    elif len(vftOrder) > 1 and vftOrder[0] == vftOrder[1]:
        vftOrder = vftOrder[1:]
    elif len(vftOrder) > 1 and vftOrder[0] == vftOrder[-1]:
        vftOrder = vftOrder[0:-1]

    finalList = [transformNode[0] + '.vtx[' + v + ']' for v in vftOrder]
    return (checkCircleState, finalList)

def _get_edge_ring_group(selEdges):
    selEdges = cmds.ls(selEdges, fl=True) or []
    if not selEdges:
        return []
    tVer = cmds.ls(cmds.polyListComponentConversion(selEdges, tv=True), fl=True)
    tFac = cmds.ls(cmds.polyListComponentConversion(tVer, tf=True, internal=1), fl=True)
    tEdg = cmds.ls(cmds.polyListComponentConversion(tFac, te=True, internal=1), fl=True)
    findLoop = list(set(tEdg) - set(selEdges))
    if findLoop:
        oneLoop = cmds.polySelectSp(findLoop[0], q=1, loop=1)
    else:
        oneLoop = selEdges
    oneLoop = cmds.ls(oneLoop, fl=1)
    if not oneLoop:
        return [selEdges]
    getCircleState, getVOrder = _vtx_loop_order_check(oneLoop)
    if not getVOrder:
        return [selEdges]
    trans = selEdges[0].split(".")[0]
    e2vInfos = cmds.polyInfo(selEdges, ev=True)
    if not e2vInfos:
        return [selEdges]
    e2vDict = {}
    for info in e2vInfos:
        evList = [int(i) for i in re.findall(r'\d+', info)]
        e2vDict[evList[0]] = evList[1:]

    fEdges = []
    while True:
        try:
            startEdge, startVtxs = e2vDict.popitem()
        except Exception:
            break
        edgesGrp = [startEdge]
        num = 0
        for vtx in startVtxs:
            curVtx = vtx
            while True:
                nextEdges = [k for k in e2vDict if curVtx in e2vDict[k]]
                if nextEdges:
                    if len(nextEdges) == 1:
                        if num == 0:
                            edgesGrp.append(nextEdges[0])
                        else:
                            edgesGrp.insert(0, nextEdges[0])
                        nextVtxs = e2vDict[nextEdges[0]]
                        curVtx = [v for v in nextVtxs if v != curVtx][0]
                        e2vDict.pop(nextEdges[0])
                    else:
                        break
                else:
                    break
            num += 1
        fEdges.append(edgesGrp)

    retEdges = []
    for f in fEdges:
        collectList = ["{}.e[{}]".format(trans, x) for x in f]
        retEdges.append(collectList)

    newOrderList = []
    for g in getVOrder:
        for e in retEdges:
            tVV = cmds.ls(cmds.polyListComponentConversion(e, tv=True), fl=True, l=1)
            if g in tVV:
                newOrderList.append(e)
    return newOrderList

def _unbevel_edge_loop(edgelist):
    getCircleState, listVtx = _vtx_loop_order_check(edgelist)
    if len(listVtx) < 3:
        return None, None, None
    checkA = _angle_between_three_p(listVtx[1], listVtx[0], listVtx[-1])
    angleA = math.degrees(checkA)
    checkB = _angle_between_three_p(listVtx[-2], listVtx[-1], listVtx[0])
    angleB = math.degrees(checkB)
    angleC = 180 - angleA - angleB
    distanceC = _distance_between_vtx(listVtx[0], listVtx[-1])
    if abs(math.sin(math.radians(angleC))) < 1e-8:
        return None, None, None
    distanceB = distanceC / math.sin(math.radians(angleC)) * math.sin(math.radians(angleB))
    oldDistB = _distance_between_vtx(listVtx[0], listVtx[1])
    if oldDistB <= 1e-8:
        return None, None, None
    scalarB = distanceB / oldDistB
    pA = cmds.pointPosition(listVtx[0], w=True)
    pB = cmds.pointPosition(listVtx[1], w=True)
    newP = [
        ((pB[0]-pA[0])*scalarB) + pA[0],
        ((pB[1]-pA[1])*scalarB) + pA[1],
        ((pB[2]-pA[2])*scalarB) + pA[2],
    ]
    listVtx = listVtx[1:-1]
    storeDist = []
    for l in listVtx:
        p = cmds.xform(l, q=True, t=True, ws=True)
        storeDist.append([
            (newP[0]-p[0]) / 100.0,
            (newP[1]-p[1]) / 100.0,
            (newP[2]-p[2]) / 100.0,
        ])
    return newP, listVtx, storeDist

def _get_vertex_position_om2(vertex_name):
    sel_list = om2.MSelectionList()
    sel_list.add(vertex_name)
    dag_path, component = sel_list.getComponent(0)
    vtx_iter = om2.MItMeshVertex(dag_path, component)
    return vtx_iter.position(om2.MSpace.kWorld)

def _calculate_edge_distances(vertex_list):
    distances = []
    for i in range(len(vertex_list) - 1):
        v1 = _get_vertex_position_om2(vertex_list[i])
        v2 = _get_vertex_position_om2(vertex_list[i+1])
        distances.append((v2-v1).length())
    return distances

def _build_unbevel_runtime_data(sel_edges):
    """Construit les buffers nécessaires au drag UnBevel à partir d'edges."""
    sel_edges = cmds.ls(sel_edges, fl=True) or []
    if not sel_edges:
        return None

    ppData = []
    vLData = []
    cLData = []

    sortGrp = _get_edge_ring_group(sel_edges)
    if not sortGrp:
        return None
    for e in sortGrp:
        newP, listVtx, storeDist = _unbevel_edge_loop(e)
        if newP is None:
            continue
        ppData.append(newP)
        vLData.append(listVtx)
        cLData.append(storeDist)

    if not ppData:
        return None

    tVer = cmds.ls(cmds.polyListComponentConversion(sel_edges, tv=True), fl=True)
    tFac = cmds.ls(cmds.polyListComponentConversion(tVer, tf=True, internal=1), fl=True)
    tEdg = cmds.ls(cmds.polyListComponentConversion(tFac, te=True, internal=1), fl=True)
    findLoop = list(set(tEdg) - set(sel_edges))
    if findLoop:
        oneLoop = cmds.polySelectSp(findLoop[0], q=1, loop=1)
    else:
        oneLoop = sel_edges
    oneLoop = cmds.ls(oneLoop, fl=1)
    _, getVOrder = _vtx_loop_order_check(oneLoop)
    if not getVOrder or len(getVOrder) < 2:
        return None

    distances = _calculate_edge_distances(getVOrder)
    if not distances:
        return None
    distances.insert(0, 0)
    total_distance = sum(distances)
    if total_distance <= 1e-8:
        return None

    cumulative_fractions = []
    cumulative_sum = 0
    for d in distances:
        cumulative_sum += d
        cumulative_fractions.append(round(cumulative_sum / total_distance, 3))

    return {
        "ppData": ppData,
        "vLData": vLData,
        "cLData": cLData,
        "cumulative_fractions": cumulative_fractions,
    }


def _existing_bevel_nodes(nodes):
    return [n for n in (nodes or []) if cmds.objExists(n) and (cmds.nodeType(n) or "").startswith("polyBevel")]


def _query_bevel_segments(nodes):
    valid = _existing_bevel_nodes(nodes)
    if not valid:
        return None
    node = valid[0]
    if not cmds.attributeQuery("segments", node=node, exists=True):
        return None
    try:
        return int(round(cmds.getAttr("{}.segments".format(node))))
    except Exception:
        return None


def _set_bevel_segments(nodes, value):
    valid = _existing_bevel_nodes(nodes)
    if not valid:
        return False
    target = max(1, int(value))
    changed = False
    for node in valid:
        if not cmds.attributeQuery("segments", node=node, exists=True):
            continue
        try:
            current = int(round(cmds.getAttr("{}.segments".format(node))))
            if current != target:
                cmds.setAttr("{}.segments".format(node), target)
                changed = True
        except Exception:
            continue
    return changed


def _resolve_unbevel_edges_from_state(state):
    """Retrouve les edges cibles après changement de topologie (ex: segments bevel)."""
    transforms = state.get("transforms", []) or []
    source_groups = state.get("source_groups", []) or []
    final = []

    if transforms and source_groups:
        all_edges = _flatten(cmds.ls(_all_edges_pattern(transforms), fl=True)) or []
        if all_edges:
            kept_by_group, _ = _filter_edges(all_edges, source_groups)
            final = _collect_final_edges(kept_by_group, source_groups)
            final = _extend_final_edges_once(final)

    if not final and cmds.objExists('saveSel'):
        final = _flatten(cmds.sets('saveSel', q=True))
        final = [e for e in (final or []) if cmds.objExists(e)]

    return final or []


def _rebuild_unbevel_data_from_state(state):
    edges = _resolve_unbevel_edges_from_state(state)
    if not edges:
        return False

    data = _build_unbevel_runtime_data(edges)
    if not data:
        return False

    state.update(data)
    if cmds.objExists('saveSel'):
        cmds.delete('saveSel')
    cmds.sets(edges, name="saveSel", text="saveSel")
    return True

# --------------------------------------------------------------------------
# UnBevel – dragger context
# --------------------------------------------------------------------------

def _start_unbevel(final_edges, bevel_nodes=None, transforms=None, source_groups=None):
    """Lance le dragger UnBevel sur final_edges (liste d'edges déjà sélectionnées)."""

    cmds.select(final_edges, r=True)
    selEdge = cmds.filterExpand(expand=True, sm=32)
    if not selEdge:
        cmds.warning("[BevelUnbevel] Aucune edge valide pour l'UnBevel.")
        return

    # Sauvegarder la sélection
    if cmds.objExists('saveSel'):
        cmds.delete('saveSel')
    cmds.sets(name="saveSel", text="saveSel")

    data = _build_unbevel_runtime_data(selEdge)
    if not data:
        cmds.warning("[BevelUnbevel] Impossible de construire les données UnBevel.")
        return

    # Stocker l'état dans __main__
    seg = _query_bevel_segments(bevel_nodes)
    __main__._unbevel_state = {
        "ppData": data["ppData"],
        "vLData": data["vLData"],
        "cLData": data["cLData"],
        "cumulative_fractions": data["cumulative_fractions"],
        "screenX": 0,
        "lockCount": 50,
        "storeCount": 0,
        "viewPortCount": 0,
        "storeCountA": 100,
        "storeCountB": 100,
        "activeContext": "unBevelCtx",
        "toolChangeJob": None,
        "finalized": False,
        "bevel_nodes": _existing_bevel_nodes(bevel_nodes),
        "transforms": transforms or [],
        "source_groups": source_groups or [],
        "segmentDragStart": seg if seg is not None else 1,
        "segmentDragAnchorX": 0,
        "currentSegments": seg if seg is not None else 1,
    }

    # Dragger context
    ctx = 'unBevelCtx'
    if cmds.draggerContext(ctx, exists=True):
        cmds.deleteUI(ctx)
    cmds.draggerContext(
        ctx,
        pressCommand  = "_unbevel_press()",
        rc            = "_unbevel_release()",
        dragCommand   = "_unbevel_drag()",
        name=ctx,
        cursor='crossHair',
        undoMode='step'
    )
    cmds.setToolTo(ctx)

    _kill_existing_unbevel_job()
    job = cmds.scriptJob(
        event=["ToolChanged",
               "__import__('__main__')._unbevel_tool_changed_callback()"],
        protected=True
    )
    __main__._unbevel_state["toolChangeJob"] = job

    print("[BevelUnbevel] UnBevel démarré. Drag pour ajuster.")
    try:
        cmds.inViewMessage(
            amg='UnBevel actif — Drag: uniforme | <hl>Shift</hl>: A | <hl>Ctrl</hl>: B | <hl>Shift+Alt</hl>: segments | <hl>Ctrl+Alt</hl>: reset',
            pos='midCenterTop', fade=True
        )
    except Exception:
        pass


def _unbevel_current_step():
    state = getattr(__main__, "_unbevel_state", {})
    vc = state.get("viewPortCount", 0)
    if vc >= 1:
        return '%.2f' % (vc / 100.0)
    elif vc > 0:
        return '0.10'
    return '0.00'


def _unbevel_press():
    state = getattr(__main__, "_unbevel_state", None)
    if not state:
        return
    ctx = 'unBevelCtx'
    vpX, vpY, _ = cmds.draggerContext(ctx, query=True, anchorPoint=True)
    state["screenX"] = vpX
    state["segmentDragAnchorX"] = vpX
    state["segmentDragStart"] = state.get("currentSegments", _query_bevel_segments(state.get("bevel_nodes")) or 1)
    # Conserver la continuité entre plusieurs drags :
    # on repart de la moyenne A/B actuellement stockée plutôt que de reset à 50.
    start_a = state.get("storeCountA", 100)
    start_b = state.get("storeCountB", 100)
    state["lockCount"] = (start_a + start_b) * 0.5
    state["storeCount"] = int(state["lockCount"] / 10.0) * 10
    state["viewPortCount"] = state["lockCount"]
    try:
        cmds.headsUpDisplay('HUDunBevelStep', rem=True)
    except Exception:
        pass
    cmds.headsUpDisplay(
        'HUDunBevelStep', section=3, block=1, blockSize='large',
        label='unBevel', labelFontSize='large',
        command='_unbevel_current_step()', atr=1, ao=1
    )


def _apply_unbevel_counts(state, count_a, count_b):
    """Applique la position des vertices à partir des distances A/B."""
    ppData = state["ppData"]
    vLData = state["vLData"]
    cLData = state["cLData"]
    cumFrac = state["cumulative_fractions"]

    count_a = max(0.1, count_a)
    count_b = max(0.1, count_b)

    for i in range(len(ppData)):
        frac = cumFrac[i] if i < len(cumFrac) else 0.0
        blend = count_b + (count_a - count_b) * frac
        for v in range(len(vLData[i])):
            cmds.move(
                ppData[i][0] - cLData[i][v][0] * blend,
                ppData[i][1] - cLData[i][v][1] * blend,
                ppData[i][2] - cLData[i][v][2] * blend,
                vLData[i][v], absolute=1, ws=1
            )


def _unbevel_drag():
    state = getattr(__main__, "_unbevel_state", None)
    if not state:
        return

    ctx = 'unBevelCtx'
    ppData   = state["ppData"]
    vLData   = state["vLData"]
    cLData   = state["cLData"]

    vpX, vpY, _ = cmds.draggerContext(ctx, query=True, dragPoint=True)
    modifiers = cmds.getModifiers()
    screenX = state["screenX"]
    lockCount = state["lockCount"]
    move_sign = -1 if screenX > vpX else 1

    # Interprétation robuste des modificateurs (bitmask Maya + fallback legacy).
    is_shift = bool(modifiers & 1)
    is_ctrl = bool(modifiers & 4)
    is_alt = bool(modifiers & 8)
    legacy_reset = (modifiers == 5)
    legacy_fine = (modifiers in (8, 13))

    # Shift + Alt: ajuste les segments du polyBevel (rebuild robuste des données de drag)
    if is_shift and is_alt and not is_ctrl:
        nodes = state.get("bevel_nodes", [])
        if not nodes:
            cmds.warning("[BevelUnbevel] Aucun node polyBevel valide pour changer les segments.")
            return
        anchor_x = state.get("segmentDragAnchorX", vpX)
        start_seg = int(max(1, state.get("segmentDragStart", 1)))
        delta = int((vpX - anchor_x) / 18.0)
        target_seg = max(1, start_seg + delta)
        if target_seg != state.get("currentSegments", 1):
            if _set_bevel_segments(nodes, target_seg):
                state["currentSegments"] = target_seg
                if _rebuild_unbevel_data_from_state(state):
                    _apply_unbevel_counts(state, state.get("storeCountA", 100), state.get("storeCountB", 100))
                else:
                    cmds.warning("[BevelUnbevel] Segments changés mais rebuild UnBevel impossible.")
        cmds.refresh(f=True)
        return

    # Alt (ou combo legacy) → reset à 0 explicite
    if (is_alt and not is_shift and not is_ctrl) or legacy_reset or (is_alt and is_ctrl):
        for i in range(len(ppData)):
            cmds.scale(0, 0, 0, vLData[i], cs=1, r=1,
                       p=(ppData[i][0], ppData[i][1], ppData[i][2]))
        state["storeCountA"] = 0
        state["storeCountB"] = 0
        state["lockCount"] = 0
        state["viewPortCount"] = 0
        state["screenX"] = vpX
        cmds.refresh(f=True)
        return

    # Pas fin (legacy + Ctrl+Shift)
    if legacy_fine or (is_ctrl and is_shift):
        lockCount += move_sign * 1
        state["screenX"] = vpX
        if lockCount > 0:
            getX = int(lockCount / 10) * 10
            if state["storeCount"] != getX:
                state["storeCount"] = getX
                state["storeCountA"] = lockCount
                state["storeCountB"] = lockCount
                _apply_unbevel_counts(state, lockCount, lockCount)
            state["viewPortCount"] = state["storeCount"]
        else:
            state["viewPortCount"] = 0.1
        state["lockCount"] = lockCount
        cmds.refresh(f=True)
        return

    # Déplacement standard
    if modifiers == 13:
        step = 0.1
    else:
        step = 5

    lockCount += move_sign * step
    state["screenX"] = vpX

    if lockCount > 0:
        # Shift → ajuste A uniquement
        if is_shift and not is_ctrl:
            lcA = lockCount
            lcB = state["storeCountB"]
            _apply_unbevel_counts(state, lcA, lcB)
            state["storeCountA"] = lockCount

        # Ctrl → ajuste B uniquement
        elif is_ctrl and not is_shift:
            lcA = state["storeCountA"]
            lcB = lockCount
            _apply_unbevel_counts(state, lcA, lcB)
            state["storeCountB"] = lockCount

        # Normal → applique un offset commun sur A/B
        # (préserve l'écart A-B déjà défini via Shift/Ctrl).
        else:
            previous_lock = state.get("lockCount", lockCount)
            delta = lockCount - previous_lock
            state["storeCountA"] = state["storeCountA"] + delta
            state["storeCountB"] = state["storeCountB"] + delta
            _apply_unbevel_counts(state, state["storeCountA"], state["storeCountB"])
            lockCount = (state["storeCountA"] + state["storeCountB"]) * 0.5

        state["viewPortCount"] = max(0.1, lockCount)
    else:
        state["viewPortCount"] = 0.1

    state["lockCount"] = lockCount
    cmds.refresh(f=True)


def _unbevel_release():
    # Important: ne pas finaliser sur mouse release.
    # La finalisation doit arriver uniquement quand l'utilisateur quitte l'outil (ex: touche Q).
    cmds.refresh(f=True)


def _finalize_unbevel_session():
    state = getattr(__main__, "_unbevel_state", None)
    if not state or state.get("finalized"):
        return
    state["finalized"] = True

    try:
        cmds.headsUpDisplay('HUDunBevelStep', rem=True)
    except Exception:
        pass

    vLData = state.get("vLData", [])
    flattenList = [v for group in vLData for v in group]
    if flattenList:
        try:
            cmds.polyMergeVertex(flattenList, d=0.001, am=0, ch=0)
        except Exception:
            cmds.warning("[BevelUnbevel] Échec du merge final des vertices.")

    if cmds.objExists('saveSel'):
        cmds.select('saveSel')
        meshName = ""
        try:
            members = cmds.sets('saveSel', q=True) or []
            if members:
                meshName = members[0].split('.')[0]
        except Exception:
            pass
        if meshName:
            try:
                mel.eval('doMenuNURBComponentSelection("{}", "edge");'.format(meshName))
            except Exception:
                pass
        cmds.delete('saveSel')

    _kill_existing_unbevel_job()
    __main__._unbevel_state = None

    print("[BevelUnbevel] UnBevel terminé.")
    try:
        cmds.inViewMessage(amg='UnBevel terminé.', pos='midCenterTop', fade=True)
    except Exception:
        pass


def _unbevel_tool_changed_callback():
    state = getattr(__main__, "_unbevel_state", None)
    if not state or state.get("finalized"):
        return
    try:
        ctx = cmds.currentCtx()
    except Exception:
        return
    if ctx != state.get("activeContext", "unBevelCtx"):
        _finalize_unbevel_session()

# --------------------------------------------------------------------------
# Callback bevel → enchaîne avec UnBevel
# --------------------------------------------------------------------------

def _bevel_tool_changed_callback():
    state = getattr(__main__, "_bevel_listener_state", None)
    if not state or state.get("done"):
        return
    try:
        ctx = cmds.currentCtx()
    except Exception:
        return
    if ctx != "selectSuperContext":
        return

    state["done"] = True
    jid = state.get("scriptJob")
    if jid and cmds.scriptJob(exists=jid):
        try:
            cmds.scriptJob(kill=jid, force=True)
        except Exception:
            pass

    transforms     = state.get("transforms", [])
    all_edges_set  = state.get("all_edges_set")
    source_groups  = state.get("source_groups", [])

    if not transforms or not all_edges_set or not cmds.objExists(all_edges_set):
        cmds.warning("[BevelUnbevel] État invalide.")
        _cleanup()
        return
    if not source_groups:
        cmds.warning("[BevelUnbevel] Pas de groupes source.")
        _cleanup()
        return

    # Récupérer les nouvelles edges créées par le bevel
    cmds.select(_all_edges_pattern(transforms), r=True)
    old = _flatten(cmds.sets(all_edges_set, q=True))
    if old:
        cmds.select(old, d=True)

    new_edges = _flatten(cmds.ls(sl=True)) or []
    if not new_edges:
        cmds.warning("[BevelUnbevel] Aucune nouvelle edge détectée.")
        _cleanup()
        return

    kept_by_group, removed = _filter_edges(new_edges, source_groups)
    final = _collect_final_edges(kept_by_group, source_groups)
    final = _extend_final_edges_once(final)

    if not final:
        final = new_edges
        cmds.warning("[BevelUnbevel] Filtre vide, utilisation de toutes les nouvelles edges.")

    _cleanup()

    print("[BevelUnbevel] Bevel: {} nouvelles | {} gardées | {} finales.".format(
        len(new_edges), sum(len(v) for v in kept_by_group.values()), len(final)))

    # ── Enchaîner directement avec UnBevel ──
    _start_unbevel(
        final,
        bevel_nodes=state.get("bevel_nodes", []),
        transforms=transforms,
        source_groups=source_groups
    )

# --------------------------------------------------------------------------
# Point d'entrée
# --------------------------------------------------------------------------

def start_bevel_unbevel():
    sel = _flatten(cmds.ls(sl=True)) or []
    edges = [x for x in sel if ".e[" in x]

    if not edges:
        cmds.warning("Sélectionner au moins une edge avant de lancer le script.")
        return

    _kill_existing_job()
    _kill_existing_unbevel_job()
    _delete_if_exists(SOURCE_SET_NAME)
    _delete_if_exists(ALL_EDGES_SET_NAME)

    transforms = _transforms_from_edges(edges)
    if not transforms:
        cmds.warning("Impossible de résoudre les transforms depuis la sélection.")
        return

    groups = _build_groups(edges)
    if not groups:
        cmds.warning("Impossible de construire les groupes source.")
        return

    _make_set(SOURCE_SET_NAME, edges)

    cmds.select(_all_edges_pattern(transforms), r=True)
    all_flat = _flatten(cmds.ls(sl=True))
    if not all_flat:
        cmds.warning("Impossible de récupérer toutes les edges.")
        return

    all_set = _make_set(ALL_EDGES_SET_NAME, all_flat)
    cmds.select(edges, r=True)

    before_nodes = _bevel_nodes(transforms)
    __main__._bevel_listener_state = {
        "done": False,
        "scriptJob": None,
        "transforms": transforms,
        "all_edges_set": all_set,
        "source_groups": groups,
        "bevel_nodes": [],
    }

    mel.eval('dR_DoCmd("bevelPress");')

    after_nodes = _bevel_nodes(transforms)
    new_nodes = list(after_nodes - before_nodes) or list(after_nodes)
    _configure_bevel(new_nodes)
    __main__._bevel_listener_state["bevel_nodes"] = new_nodes

    jid = cmds.scriptJob(
        event=["ToolChanged",
               "__import__('__main__')._bevel_tool_changed_callback()"],
        protected=True
    )
    __main__._bevel_listener_state["scriptJob"] = jid

    print("[BevelUnbevel] Bevel démarré. Appuyer sur Q pour passer à l'UnBevel.")
    try:
        cmds.inViewMessage(
            amg='Bevel démarré — Appuyer sur <hl>Q</hl> pour passer à l\'UnBevel.',
            pos='midCenterTop', fade=True
        )
    except Exception:
        pass


# Expose les callbacks dans __main__ pour que Maya puisse les appeler via string
__main__._bevel_tool_changed_callback = _bevel_tool_changed_callback
__main__._unbevel_press               = _unbevel_press
__main__._unbevel_drag                = _unbevel_drag
__main__._unbevel_release             = _unbevel_release
__main__._unbevel_current_step        = _unbevel_current_step
__main__._unbevel_tool_changed_callback = _unbevel_tool_changed_callback

start_bevel_unbevel()
