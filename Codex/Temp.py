# -*- coding: utf-8 -*-

import math
import re
from collections import defaultdict
import maya.cmds as cmds
import maya.mel as mel

# ------------------------------------------------------------
# Optional: maya.api.OpenMaya (for PR Select robustness)
# ------------------------------------------------------------
try:
    import maya.api.OpenMaya as om2
except Exception:
    om2 = None

# ------------------------------------------------------------
# PySide2/6
# ------------------------------------------------------------
try:
    from PySide2 import QtWidgets, QtCore, QtGui
    from shiboken2 import wrapInstance
    PYSIDE_VER = 2
except Exception:
    from PySide6 import QtWidgets, QtCore, QtGui
    from shiboken6 import wrapInstance
    PYSIDE_VER = 6

import maya.OpenMayaUI as omui


# =============================================================================
# SHARED - Maya main window
# =============================================================================
def get_maya_main_window():
    """Get Maya main window as Qt object."""
    try:
        ptr = omui.MQtUtil.mainWindow()
        if ptr:
            return wrapInstance(int(ptr), QtWidgets.QWidget)
    except Exception:
        pass
    return None


# =============================================================================
# =============================================================================
#                               TAB 1 - MESH TOOLKIT (réparé)
# =============================================================================
# =============================================================================

# =============================================================================
# POPUP NOTIFICATION SYSTEM (réparé)
# =============================================================================
def show_inview_message(message, duration=2.0, color="info"):
    """
    Affiche une boîte de dialogue popup qui disparaît automatiquement.
    color: "info" (bleu), "success" (vert), "warning" (orange), "error" (rouge)
    """
    icons = {"info": "i", "success": "OK", "warning": "!", "error": "X"}
    titles = {"info": "Info", "success": "Succes", "warning": "Attention", "error": "Erreur"}
    icon = icons.get(color, "i")
    title = titles.get(color, "Info")

    popup_win = "meshToolkitNotif"
    if cmds.window(popup_win, exists=True):
        try:
            cmds.deleteUI(popup_win)
        except Exception:
            pass

    bg_colors = {
        "info": [0.22, 0.35, 0.50],
        "success": [0.22, 0.45, 0.28],
        "warning": [0.55, 0.45, 0.20],
        "error": [0.55, 0.25, 0.25],
    }
    bg = bg_colors.get(color, bg_colors["info"])

    cmds.window(popup_win, title=title, widthHeight=(300, 70),
                sizeable=False, toolbox=True, titleBarMenu=False)
    cmds.columnLayout(adjustableColumn=True, bgc=bg, rowSpacing=5)
    cmds.separator(height=15, style="none")
    cmds.text(label="{}  {}".format(icon, message), align="center", font="boldLabelFont", height=25)
    cmds.separator(height=15, style="none")
    cmds.setParent("..")
    cmds.showWindow(popup_win)

    try:
        cmds.window(popup_win, e=True, topLeftCorner=[400, 600])
    except Exception:
        pass

    # QTimer safe import
    QTimer = None
    try:
        from PySide2.QtCore import QTimer as _QT
        QTimer = _QT
    except Exception:
        try:
            from PySide6.QtCore import QTimer as _QT
            QTimer = _QT
        except Exception:
            try:
                from PyQt5.QtCore import QTimer as _QT
                QTimer = _QT
            except Exception:
                QTimer = None

    if QTimer:
        def close_popup():
            if cmds.window(popup_win, exists=True):
                try:
                    cmds.deleteUI(popup_win)
                except Exception:
                    pass

        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(close_popup)
        timer.start(int(duration * 1000))

        if not hasattr(show_inview_message, "_timers"):
            show_inview_message._timers = []
        show_inview_message._timers.append(timer)
        show_inview_message._timers = [t for t in show_inview_message._timers if t.isActive()]
    else:
        import threading
        import time

        def delayed_close():
            time.sleep(duration)
            if cmds.window(popup_win, exists=True):
                cmds.evalDeferred(lambda: cmds.deleteUI(popup_win) if cmds.window(popup_win, exists=True) else None)

        t = threading.Thread(target=delayed_close)
        t.daemon = True
        t.start()


# =============================================================================
# BASIC UTILS
# =============================================================================
def set_selection_mode(mode):
    """Change le mode de sélection Maya."""
    if mode == "edge":
        cmds.selectMode(component=True)
        cmds.selectType(edge=True)
    elif mode == "face":
        cmds.selectMode(component=True)
        cmds.selectType(facet=True)
    elif mode == "vertex":
        cmds.selectMode(component=True)
        cmds.selectType(vertex=True)
    else:
        cmds.selectMode(object=True)


def get_mesh_from_component(component):
    """Extrait le nom du mesh depuis un composant."""
    try:
        return component.split(".")[0]
    except Exception:
        return None


def get_selected_mesh():
    """Retourne le mesh sélectionné ou None (premier trouvé)."""
    sel = cmds.ls(selection=True, flatten=True) or []
    if not sel:
        return None

    first = sel[0]
    if cmds.objectType(first) == "transform":
        shapes = cmds.listRelatives(first, shapes=True, type="mesh") or []
        return first if shapes else None

    mesh = get_mesh_from_component(first)
    if mesh and cmds.objExists(mesh):
        shapes = cmds.listRelatives(mesh, shapes=True, type="mesh") or []
        return mesh if shapes else None

    return None


def get_all_selected_meshes():
    """Retourne tous les mesh transforms présents dans la sélection (objets + composants)."""
    sel = cmds.ls(selection=True, long=True, flatten=True) or []
    meshes = []
    for it in sel:
        m = it.split(".")[0] if "." in it else it
        if not cmds.objExists(m):
            continue
        if cmds.objectType(m) == "transform":
            shapes = cmds.listRelatives(m, shapes=True, type="mesh") or []
            if shapes and m not in meshes:
                meshes.append(m)
        elif cmds.objectType(m) == "mesh":
            p = cmds.listRelatives(m, parent=True, fullPath=True) or []
            if p and p[0] not in meshes:
                meshes.append(p[0])
    return meshes


def filter_edges(components):
    return cmds.filterExpand(components, selectionMask=32) or []


def filter_faces(components):
    return cmds.filterExpand(components, selectionMask=34) or []


def filter_verts(components):
    return cmds.filterExpand(components, selectionMask=31) or []


def edge_to_faces(edge):
    if not cmds.objExists(edge):
        return []
    faces = cmds.polyListComponentConversion(edge, toFace=True)
    return cmds.filterExpand(faces, selectionMask=34) or []


def edge_to_verts(edge):
    if not cmds.objExists(edge):
        return []
    verts = cmds.polyListComponentConversion(edge, toVertex=True)
    return cmds.filterExpand(verts, selectionMask=31) or []


def vert_to_edges(vtx):
    if not cmds.objExists(vtx):
        return []
    edges = cmds.polyListComponentConversion(vtx, toEdge=True)
    return cmds.filterExpand(edges, selectionMask=32) or []


def vert_to_faces(vtx):
    if not cmds.objExists(vtx):
        return []
    faces = cmds.polyListComponentConversion(vtx, toFace=True)
    return cmds.filterExpand(faces, selectionMask=34) or []


def face_to_edges(face):
    if not cmds.objExists(face):
        return []
    edges = cmds.polyListComponentConversion(face, toEdge=True)
    return cmds.filterExpand(edges, selectionMask=32) or []


def face_to_verts(face):
    if not cmds.objExists(face):
        return []
    verts = cmds.polyListComponentConversion(face, toVertex=True)
    return cmds.filterExpand(verts, selectionMask=31) or []


def face_vertex_count(face):
    try:
        if not cmds.objExists(face):
            return 0
        return len(face_to_verts(face))
    except Exception:
        return 0


def get_vertex_position(vtx):
    try:
        if not cmds.objExists(vtx):
            return None
        return tuple(cmds.pointPosition(vtx, world=True))
    except Exception:
        return None


def normalize(v):
    length = math.sqrt(v[0]*v[0] + v[1]*v[1] + v[2]*v[2])
    if length < 1e-12:
        return (0.0, 0.0, 0.0)
    return (v[0]/length, v[1]/length, v[2]/length)


def subtract(a, b):
    return (a[0]-b[0], a[1]-b[1], a[2]-b[2])


def dot(a, b):
    return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]


def clamp(x, a, b):
    return max(a, min(b, x))


def is_border_edge(edge):
    try:
        if not cmds.objExists(edge):
            return False
        return len(edge_to_faces(edge)) == 1
    except Exception:
        return False


def is_border_vertex(vtx):
    if not cmds.objExists(vtx):
        return False
    for e in vert_to_edges(vtx):
        if is_border_edge(e):
            return True
    return False


# =============================================================================
# SCOPE UTILS (faces sélectionnées OU meshes)
# =============================================================================
def get_work_scope():
    """
    Scope-safe:
      - Si faces sélectionnées -> travaille seulement sur ces faces (multi-mesh)
      - Sinon -> travaille sur mesh(s) sélectionnés entier(s)

    Returns:
      work_on_selection (bool)
      meshes (list[str])
      faces_by_mesh (dict[str, list[str]])
      sel_faces (list[str])
    """
    sel = cmds.ls(sl=True, fl=True) or []
    sel_faces = filter_faces(sel)

    faces_by_mesh = defaultdict(list)
    if sel_faces:
        for f in sel_faces:
            m = get_mesh_from_component(f)
            if m and cmds.objExists(m):
                faces_by_mesh[m].append(f)
        meshes = list(faces_by_mesh.keys())
        return True, meshes, faces_by_mesh, sel_faces

    meshes = get_all_selected_meshes()
    return False, meshes, faces_by_mesh, sel_faces


def _faces_in_scope(mesh, work_on_selection, faces_by_mesh):
    if work_on_selection:
        return [f for f in faces_by_mesh.get(mesh, []) if cmds.objExists(f)]
    fc = cmds.polyEvaluate(mesh, face=True) or 0
    return ["{}.f[{}]".format(mesh, i) for i in range(int(fc))]


# =============================================================================
# NORMALS / ANGLES
# =============================================================================
def get_face_normal(face):
    try:
        if not cmds.objExists(face):
            return (0.0, 1.0, 0.0)
        info = cmds.polyInfo(face, fn=True) or []
        if not info:
            return (0.0, 1.0, 0.0)
        s = info[0].strip().replace("\t", " ")
        parts = [p for p in s.split(" ") if p]
        x, y, z = float(parts[-3]), float(parts[-2]), float(parts[-1])
        return normalize((x, y, z))
    except Exception:
        return (0.0, 1.0, 0.0)


def get_edge_face_angle(edge):
    faces = edge_to_faces(edge)
    if len(faces) != 2:
        return 180.0
    n1 = get_face_normal(faces[0])
    n2 = get_face_normal(faces[1])
    d = abs(dot(n1, n2))
    d = clamp(d, 0.0, 1.0)
    return math.degrees(math.acos(d))


def get_edge_direction(edge):
    vs = edge_to_verts(edge)
    if len(vs) != 2:
        return None
    p1 = get_vertex_position(vs[0])
    p2 = get_vertex_position(vs[1])
    if not p1 or not p2:
        return None
    return normalize(subtract(p2, p1))


def angle_between_edges(edge1, edge2):
    d1 = get_edge_direction(edge1)
    d2 = get_edge_direction(edge2)
    if not d1 or not d2:
        return 90.0
    d = abs(dot(d1, d2))
    d = clamp(d, 0.0, 1.0)
    return math.degrees(math.acos(d))


# =============================================================================
# CONNECTIVITY / CHAINS
# =============================================================================
def connected_edge_components(edges):
    edges = list(edges)
    edge_set = set(edges)
    comps = []
    visited = set()

    def bfs(start):
        q = [start]
        comp = set()
        while q:
            cur = q.pop()
            if cur in comp:
                continue
            comp.add(cur)
            for v in edge_to_verts(cur):
                for ne in vert_to_edges(v):
                    if ne in edge_set and ne not in comp:
                        q.append(ne)
        return comp

    for e in edges:
        if e in visited:
            continue
        comp = bfs(e)
        visited |= comp
        comps.append(list(comp))
    return comps


def boundary_edges_from_faces(faces):
    face_set = set(faces)
    all_edges = set()
    for f in faces:
        all_edges.update(face_to_edges(f))

    boundary = []
    for e in all_edges:
        ef = edge_to_faces(e)
        in_sel = sum(1 for f in ef if f in face_set)
        if in_sel == 1:
            boundary.append(e)
    return boundary


def build_boundary_adjacency(boundary_edges):
    edge_set = set(boundary_edges)
    adj = defaultdict(list)
    for e in boundary_edges:
        for v in edge_to_verts(e):
            for ne in vert_to_edges(v):
                if ne in edge_set and ne != e:
                    if ne not in adj[e]:
                        adj[e].append(ne)
    return adj


def split_boundary_by_angle(boundary_edges, angle_threshold=45.0):
    if not boundary_edges:
        return []

    adj = build_boundary_adjacency(boundary_edges)
    visited = set()
    chains = []

    def walk_chain(start_edge):
        chain = [start_edge]
        visited.add(start_edge)

        for direction in [0, 1]:
            cur = start_edge
            while True:
                neighbors = [n for n in adj[cur] if n not in visited]
                if not neighbors:
                    break
                valid = [n for n in neighbors if angle_between_edges(cur, n) < angle_threshold]
                if not valid:
                    break
                nxt = min(valid, key=lambda n: angle_between_edges(cur, n))
                visited.add(nxt)
                if direction == 0:
                    chain.append(nxt)
                else:
                    chain.insert(0, nxt)
                cur = nxt
        return chain

    for e in boundary_edges:
        if e not in visited:
            ch = walk_chain(e)
            if ch:
                chains.append(ch)

    return chains


def ordered_chain_vertices(chain_edges):
    if not chain_edges:
        return []

    v_adj = defaultdict(list)
    for e in chain_edges:
        vs = edge_to_verts(e)
        if len(vs) != 2:
            continue
        a, b = vs
        v_adj[a].append(b)
        v_adj[b].append(a)

    if not v_adj:
        return []

    endpoints = [v for v, nbs in v_adj.items() if len(nbs) == 1]
    start = endpoints[0] if endpoints else next(iter(v_adj.keys()))

    ordered = [start]
    prev = None
    cur = start
    while True:
        nbs = v_adj.get(cur, [])
        nxt = None
        for nb in nbs:
            if nb != prev:
                nxt = nb
                break
        if not nxt:
            break
        ordered.append(nxt)
        prev, cur = cur, nxt
        if len(ordered) > len(v_adj) + 2:
            break

    return ordered


def best_align_vertex_lists(vlist_a, vlist_b):
    if not vlist_a or not vlist_b:
        return vlist_a, vlist_b

    n = min(len(vlist_a), len(vlist_b))
    a = vlist_a[:n]
    b0 = vlist_b[:n]
    b1 = list(reversed(vlist_b))[:n]

    def total_dist(va, vb):
        s = 0.0
        for x, y in zip(va, vb):
            px = get_vertex_position(x)
            py = get_vertex_position(y)
            if not px or not py:
                continue
            dx = px[0]-py[0]
            dy = px[1]-py[1]
            dz = px[2]-py[2]
            s += dx*dx + dy*dy + dz*dz
        return s

    return (a, b1) if total_dist(a, b1) < total_dist(a, b0) else (a, b0)


# =============================================================================
# REMOVE / CLEAN HELPERS
# =============================================================================
def _edge_merge_vertex_count(edge):
    faces = edge_to_faces(edge)
    if len(faces) != 2:
        return None
    v1 = face_vertex_count(faces[0])
    v2 = face_vertex_count(faces[1])
    return (v1 + v2 - 2)


def _is_edge_merge_allowed(edge, allow_ngons=False, max_ngon=6):
    merged = _edge_merge_vertex_count(edge)
    if merged is None:
        return False
    if allow_ngons:
        return merged <= max_ngon
    return merged <= 4


def _collect_edges_in_faces_region(faces):
    edges = set()
    for f in faces:
        for e in face_to_edges(f):
            edges.add(e)
    return list(edges)


# =============================================================================
# CLEAN TOOLS
# =============================================================================
def _is_edge_merge_allowed(edge, allow_ngons=False):
    """
    Vérifie si on peut supprimer une edge.
    - Si allow_ngons=False : UNIQUEMENT si résultat <= 4 vertices (quad ou moins)
    - Si allow_ngons=True : Autorise jusqu'à 8 vertices
    """
    faces = edge_to_faces(edge)
    if len(faces) != 2:
        return False
    
    v1 = face_vertex_count(faces[0])
    v2 = face_vertex_count(faces[1])
    merged = v1 + v2 - 2
    
    if allow_ngons:
        return merged <= 8  # Mode agressif
    else:
        return merged <= 4  # Mode conservateur (quads only)


def select_removable_edges(angle_threshold=5.0, allow_ngons=False, only_selected_faces=False):
    """
    Sélectionne les edges supprimables.
    
    Args:
        angle_threshold: Angle max entre faces (°)
        allow_ngons: Si True, autorise création de ngons (jusqu'à 8 sides)
        only_selected_faces: Si True, travaille seulement sur faces sélectionnées
    """
    mesh = get_selected_mesh()
    if not mesh:
        cmds.warning("Sélectionne un mesh!")
        show_inview_message("Aucun mesh selectionne!", 2.0, "error")
        return
    
    sel = cmds.ls(selection=True, flatten=True) or []
    sel_faces = filter_faces(sel)
    
    if only_selected_faces and sel_faces:
        candidate_edges = _collect_edges_in_faces_region(sel_faces)
    else:
        edge_count = cmds.polyEvaluate(mesh, edge=True)
        candidate_edges = ["{}.e[{}]".format(mesh, i) for i in range(edge_count)]
    
    removable = []
    for e in candidate_edges:
        if not cmds.objExists(e):
            continue
        if len(edge_to_faces(e)) != 2:
            continue
        ang = get_edge_face_angle(e)
        if ang > angle_threshold:
            continue
        if not _is_edge_merge_allowed(e, allow_ngons=allow_ngons):
            continue
        removable.append(e)
    
    if removable:
        set_selection_mode("edge")
        cmds.select(removable, r=True)
        mode_txt = "ngons ok" if allow_ngons else "quads only"
        show_inview_message("{} edges ({})".format(len(removable), mode_txt), 2.0, "info")
    else:
        cmds.select(mesh, r=True)
        show_inview_message("Aucune edge supprimable", 2.0, "success")


def auto_clean(angle_threshold=5.0, allow_ngons=False, max_passes=50, only_selected_faces=False):
    """
    Supprime automatiquement les edges coplanaires.
    """
    mesh = get_selected_mesh()
    if not mesh:
        cmds.warning("Sélectionne un mesh!")
        show_inview_message("Aucun mesh selectionne!", 2.0, "error")
        return

    sel = cmds.ls(selection=True, flatten=True) or []
    sel_faces = filter_faces(sel)

    cmds.undoInfo(openChunk=True, chunkName="AutoClean")
    try:
        total = 0
        for _p in range(max_passes):
            if not cmds.objExists(mesh):
                break

            if only_selected_faces and sel_faces:
                candidate_edges = _collect_edges_in_faces_region(sel_faces)
            else:
                edge_count = cmds.polyEvaluate(mesh, edge=True)
                candidate_edges = ["{}.e[{}]".format(mesh, i) for i in range(edge_count)]

            removable = []
            for e in candidate_edges:
                if not cmds.objExists(e):
                    continue
                if len(edge_to_faces(e)) != 2:
                    continue
                ang = get_edge_face_angle(e)
                if ang > angle_threshold:
                    continuea
                if not _is_edge_merge_allowed(e, allow_ngons=allow_ngons):
                    continue
                removable.append(e)

            if not removable:
                break

            try:
                cmds.polyDelEdge(removable, cleanVertices=True, constructionHistory=False)
                total += len(removable)
            except Exception:
                cnt = 0
                for e in removable:
                    if not cmds.objExists(e):
                        continue
                    try:
                        cmds.polyDelEdge(e, cleanVertices=True, constructionHistory=False)
                        cnt += 1
                    except Exception:
                        pass
                if cnt == 0:
                    break
                total += cnt

        if cmds.objExists(mesh):
            cmds.select(mesh, r=True)

        mode_txt = "ngons ok" if allow_ngons else "quads only"
        show_inview_message("{} edges ({})".format(total, mode_txt), 2.0, "success" if total > 0 else "info")
    except Exception as e:
        cmds.warning("AutoClean erreur: {}".format(e))
        show_inview_message("Erreur AutoClean!", 2.0, "error")
        import traceback
        traceback.print_exc()
    finally:
        cmds.undoInfo(closeChunk=True)

def auto_clean(angle_threshold=5.0, allow_ngons=False, max_ngon=6, max_passes=50, only_selected_faces=False):
    mesh = get_selected_mesh()
    if not mesh:
        cmds.warning("Sélectionne un mesh!")
        show_inview_message("Aucun mesh sélectionné!", 2.0, "error")
        return

    sel = cmds.ls(selection=True, flatten=True) or []
    sel_faces = filter_faces(sel)

    cmds.undoInfo(openChunk=True, chunkName="AutoClean")
    try:
        total = 0
        for _p in range(max_passes):
            if not cmds.objExists(mesh):
                break

            if only_selected_faces and sel_faces:
                candidate_edges = _collect_edges_in_faces_region(sel_faces)
            else:
                edge_count = cmds.polyEvaluate(mesh, edge=True)
                candidate_edges = ["{}.e[{}]".format(mesh, i) for i in range(edge_count)]

            removable = []
            for e in candidate_edges:
                if not cmds.objExists(e):
                    continue
                if len(edge_to_faces(e)) != 2:
                    continue
                ang = get_edge_face_angle(e)
                if ang > angle_threshold:
                    continue
                if not _is_edge_merge_allowed(e, allow_ngons=allow_ngons, max_ngon=max_ngon):
                    continue
                removable.append(e)

            if not removable:
                break

            try:
                cmds.polyDelEdge(removable, cleanVertices=True)
                total += len(removable)
            except Exception:
                cnt = 0
                for e in removable:
                    if not cmds.objExists(e):
                        continue
                    try:
                        cmds.polyDelEdge(e, cleanVertices=True)
                        cnt += 1
                    except Exception:
                        pass
                if cnt == 0:
                    break
                total += cnt

        if cmds.objExists(mesh):
            cmds.select(mesh, r=True)

        show_inview_message("{} edges supprimées".format(total), 2.0, "success" if total > 0 else "info")
    except Exception as e:
        cmds.warning("AutoClean erreur: {}".format(e))
        show_inview_message("Erreur AutoClean!", 2.0, "error")
        import traceback
        traceback.print_exc()
    finally:
        cmds.undoInfo(closeChunk=True)


# =============================================================================
# BRIDGE - Smart Bridge Hole (multi-mesh safe)
# =============================================================================
def mesh_smart_bridge_hole(corner_threshold=35.0, merge_dist=0.001, debug=True):
    """
    Quad Patch pour boucher un trou (loop fermé de border edges).
    Supporte plusieurs meshes si tu as des edges de plusieurs meshes sélectionnées.
    """
    def _clamp(x, a, b):
        return max(a, min(b, x))

    def _vpos(v):
        if not cmds.objExists(v):
            return None
        return cmds.pointPosition(v, world=True)

    def _sub(a, b):
        return (a[0] - b[0], a[1] - b[1], a[2] - b[2])

    def _len(v):
        return math.sqrt(v[0]*v[0] + v[1]*v[1] + v[2]*v[2])

    def _norm(v):
        l = _len(v)
        if l < 1e-12:
            return (0.0, 0.0, 0.0)
        return (v[0]/l, v[1]/l, v[2]/l)

    def _dot(a, b):
        return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]

    def _dist2(a, b):
        dx = a[0]-b[0]
        dy = a[1]-b[1]
        dz = a[2]-b[2]
        return dx*dx + dy*dy + dz*dz

    def _get_mesh_from_comp(comp):
        return comp.split(".")[0]

    def _is_border_edge(e):
        if not cmds.objExists(e):
            return False
        faces = cmds.polyListComponentConversion(e, toFace=True)
        faces = cmds.filterExpand(faces, sm=34) or []
        return len(faces) == 1

    def _edge_verts(e):
        if not cmds.objExists(e):
            return []
        v = cmds.polyListComponentConversion(e, toVertex=True)
        return cmds.filterExpand(v, sm=31) or []

    def _order_border_loop_edges(border_edges):
        edge_set = set(border_edges)
        if not border_edges:
            return []
        v2e = {}
        for e in border_edges:
            for vv in _edge_verts(e):
                v2e.setdefault(vv, []).append(e)

        start = border_edges[0]
        ordered = [start]
        used = {start}

        vs = _edge_verts(start)
        if len(vs) != 2:
            return ordered
        cur_v = vs[1]

        for _ in range(len(border_edges) * 4):
            next_e = None
            for cand in v2e.get(cur_v, []):
                if cand in edge_set and cand not in used:
                    next_e = cand
                    break
            if not next_e:
                break

            ordered.append(next_e)
            used.add(next_e)
            nvs = _edge_verts(next_e)
            if len(nvs) != 2:
                break
            cur_v = nvs[0] if nvs[0] != cur_v else nvs[1]
            if len(used) == len(edge_set):
                break

        return ordered

    def _ordered_loop_vertices_from_edges(ordered_edges):
        if not ordered_edges:
            return []
        e0 = ordered_edges[0]
        vs0 = _edge_verts(e0)
        if len(vs0) != 2:
            return []
        verts = [vs0[0], vs0[1]]
        cur = vs0[1]
        for e in ordered_edges[1:]:
            vs = _edge_verts(e)
            if len(vs) != 2:
                break
            nxt = vs[0] if vs[0] != cur else vs[1]
            verts.append(nxt)
            cur = nxt
        if len(verts) > 2 and verts[-1] == verts[0]:
            verts.pop()
        return verts

    def _find_corners_from_vertex_loop(v_loop, threshold_deg):
        n = len(v_loop)
        if n < 4:
            return []
        corners = []
        for i in range(n):
            v_prev = v_loop[(i - 1) % n]
            v_cur = v_loop[i]
            v_next = v_loop[(i + 1) % n]
            p_prev = _vpos(v_prev)
            p_cur = _vpos(v_cur)
            p_next = _vpos(v_next)
            if not p_prev or not p_cur or not p_next:
                continue
            d1 = _norm(_sub(p_cur, p_prev))
            d2 = _norm(_sub(p_next, p_cur))
            dp = abs(_dot(d1, d2))
            dp = _clamp(dp, 0.0, 1.0)
            ang = math.degrees(math.acos(dp))
            if ang > threshold_deg:
                corners.append(i)

        # clean clusters
        if len(corners) > 4:
            cleaned = []
            for idx in corners:
                if not cleaned:
                    cleaned.append(idx)
                else:
                    if (idx - cleaned[-1]) % n != 1:
                        cleaned.append(idx)
            corners = cleaned

        return corners

    def _fallback_bbox_corners(v_loop):
        pts = []
        for v in v_loop:
            p = _vpos(v)
            if p:
                pts.append(p)
        if len(pts) < 4:
            return []

        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        zs = [p[2] for p in pts]
        rx = max(xs) - min(xs)
        ry = max(ys) - min(ys)
        rz = max(zs) - min(zs)

        axes = sorted([(rx, 0), (ry, 1), (rz, 2)], reverse=True)
        a0 = axes[0][1]
        a1 = axes[1][1]

        mn0, mx0 = min(p[a0] for p in pts), max(p[a0] for p in pts)
        mn1, mx1 = min(p[a1] for p in pts), max(p[a1] for p in pts)

        targets = [(mn0, mn1), (mx0, mn1), (mx0, mx1), (mn0, mx1)]
        picked = []
        for t0, t1 in targets:
            best_i = None
            best_d = 1e30
            for i, p in enumerate(pts):
                d = (p[a0] - t0) ** 2 + (p[a1] - t1) ** 2
                if d < best_d:
                    best_d = d
                    best_i = i
            picked.append(best_i)

        picked = sorted(set(picked))
        return picked[:4] if len(picked) >= 4 else picked

    def _split_segments(v_loop, corner_ids):
        corner_ids = sorted(corner_ids)
        segs = []
        for i in range(4):
            a = corner_ids[i]
            b = corner_ids[(i + 1) % 4]
            if b > a:
                seg = v_loop[a:b + 1]
            else:
                seg = v_loop[a:] + v_loop[:b + 1]
            segs.append(seg)
        return segs

    def _align_opposite_segments(segA, segB):
        if not segA or not segB:
            return segA, segB
        pa0 = _vpos(segA[0])
        pb0 = _vpos(segB[0])
        pb1 = _vpos(segB[-1])
        if not pa0 or not pb0 or not pb1:
            return segA, segB
        if _dist2(pa0, pb1) < _dist2(pa0, pb0):
            return segA, list(reversed(segB))
        return segA, segB

    def _create_curve_from_verts(verts, name):
        pts = []
        for v in verts:
            p = _vpos(v)
            if p:
                pts.append(p)
        if len(pts) < 2:
            return None
        return cmds.curve(d=1, p=pts, name=name)

    # Selection
    sel = cmds.ls(sl=True, fl=True) or []
    edges = cmds.filterExpand(sel, sm=32) or []
    if not edges:
        cmds.warning("Sélectionne des border edges (closed loop)!")
        show_inview_message("Sélectionne des border edges!", 2.0, "warning")
        return

    mesh_edges = defaultdict(list)
    for e in edges:
        m = _get_mesh_from_comp(e)
        if m:
            mesh_edges[m].append(e)

    cmds.undoInfo(openChunk=True, chunkName="SmartBridgeQuadPatch")
    try:
        success = 0
        fail = 0

        for mesh_name, e_list in mesh_edges.items():
            border_edges = [e for e in e_list if _is_border_edge(e)]
            if len(border_edges) < 4:
                fail += 1
                continue

            try:
                full_loop = cmds.polySelectSp(border_edges[0], loop=True, q=True) or []
                full_loop = cmds.ls(full_loop, fl=True) or []
                full_loop = [e for e in full_loop if _is_border_edge(e)]
                if len(full_loop) >= len(border_edges):
                    border_edges = full_loop
            except Exception:
                pass

            ordered_edges = _order_border_loop_edges(border_edges)
            v_loop = _ordered_loop_vertices_from_edges(ordered_edges)
            if len(v_loop) < 4:
                fail += 1
                continue

            corners = _find_corners_from_vertex_loop(v_loop, corner_threshold)
            if len(corners) != 4:
                corners = _fallback_bbox_corners(v_loop)
                if len(corners) != 4:
                    fail += 1
                    continue

            segs = _split_segments(v_loop, corners)
            segs[0], segs[2] = _align_opposite_segments(segs[0], segs[2])
            segs[1], segs[3] = _align_opposite_segments(segs[1], segs[3])

            if debug:
                print("[SmartBridge] Mesh={} edges={} corners={}".format(mesh_name, len(border_edges), corners))

            curves = []
            try:
                cmds.nurbsToPolygonsPref(polyType=1, format=2, uType=3, uNumber=1, vType=3, vNumber=1)
                curves.append(_create_curve_from_verts(segs[0], "sb_sideA_crv"))
                curves.append(_create_curve_from_verts(segs[1], "sb_sideB_crv"))
                curves.append(_create_curve_from_verts(segs[2], "sb_sideC_crv"))
                curves.append(_create_curve_from_verts(segs[3], "sb_sideD_crv"))
                curves = [c for c in curves if c]

                if len(curves) != 4:
                    if curves:
                        cmds.delete(curves)
                    fail += 1
                    continue

                patch = cmds.boundary(curves[0], curves[1], curves[2], curves[3],
                                      ch=0, ep=0, po=1, order=0)
                patch_xform = patch[0] if patch else None

                cmds.delete(curves)
                curves = []

                if not patch_xform or not cmds.objExists(patch_xform):
                    fail += 1
                    continue

                cmds.select([mesh_name, patch_xform], r=True)
                united = cmds.polyUnite(ch=0, mergeUVSets=1, name=mesh_name)[0]
                if united != mesh_name and cmds.objExists(mesh_name):
                    try:
                        cmds.delete(mesh_name)
                    except Exception:
                        pass
                    cmds.rename(united, mesh_name)
                    united = mesh_name

                cmds.select("{}.vtx[*]".format(united), r=True)
                cmds.polyMergeVertex(distance=merge_dist, am=True, ch=0)

                try:
                    cmds.polyNormal(united, normalMode=2, userNormalMode=0, ch=0)
                    cmds.SetToFaceNormals()
                except Exception:
                    pass

                success += 1

            except Exception:
                try:
                    if curves:
                        cmds.delete(curves)
                except Exception:
                    pass
                fail += 1

        final_meshes = [m for m in mesh_edges.keys() if cmds.objExists(m)]
        if final_meshes:
            cmds.select(final_meshes, r=True)

        if success > 0:
            msg = "{} trou(s) bouché(s)".format(success)
            if fail > 0:
                msg += " ({} échec)".format(fail)
            show_inview_message(msg, 2.0, "success")
        else:
            show_inview_message("Aucun trou bouché!", 2.0, "error")

    finally:
        cmds.undoInfo(closeChunk=True)


# =============================================================================
# BRIDGE - Concentric Bridge (faces or edges)
# =============================================================================
def bridge_concentric_from_faces_or_edges(divisions=0, twist=0):
    sel = cmds.ls(selection=True, flatten=True) or []
    if not sel:
        cmds.warning("Sélectionne des faces entre 2 loops OU les edges des 2 loops.")
        show_inview_message("Aucune sélection!", 2.0, "warning")
        return

    mesh = get_selected_mesh()
    if not mesh:
        cmds.warning("Sélectionne un mesh / composants.")
        show_inview_message("Aucun mesh!", 2.0, "warning")
        return

    faces = filter_faces(sel)
    edges = filter_edges(sel)

    if faces:
        boundary = boundary_edges_from_faces(faces)
        comps = connected_edge_components(boundary)
        if len(comps) < 2:
            cmds.warning("Je ne trouve pas 2 loops de bordure sur ces faces.")
            show_inview_message("2 loops requis!", 2.0, "warning")
            return

        comps = sorted(comps, key=lambda c: len(c), reverse=True)
        loop1 = comps[0]
        loop2 = comps[1]

        cmds.undoInfo(openChunk=True, chunkName="ConcentricBridgeFaces")
        try:
            cmds.delete(faces)
            cmds.select(loop1 + loop2, r=True)
            cmds.polyBridgeEdge(divisions=divisions, twist=twist)
            cmds.select(mesh, r=True)
            show_inview_message("Bridge: {} + {} edges".format(len(loop1), len(loop2)), 2.0, "success")
        except Exception as e:
            cmds.warning("ConcentricBridgeFaces erreur: {}".format(e))
            show_inview_message("Erreur Bridge!", 2.0, "error")
        finally:
            cmds.undoInfo(closeChunk=True)
        return

    if edges:
        comps = connected_edge_components(edges)
        if len(comps) != 2:
            cmds.warning("Sélectionne exactement 2 loops d'edges.")
            show_inview_message("2 loops d'edges requis!", 2.0, "warning")
            return

        loop1 = comps[0]
        loop2 = comps[1]

        cmds.undoInfo(openChunk=True, chunkName="ConcentricBridgeEdges")
        try:
            cmds.select(loop1 + loop2, r=True)
            cmds.polyBridgeEdge(divisions=divisions, twist=twist)
            cmds.select(mesh, r=True)
            show_inview_message("Bridge: {} + {} edges".format(len(loop1), len(loop2)), 2.0, "success")
        except Exception as e:
            cmds.warning("ConcentricBridgeEdges erreur: {}".format(e))
            show_inview_message("Erreur Bridge!", 2.0, "error")
        finally:
            cmds.undoInfo(closeChunk=True)
        return

    cmds.warning("Sélectionne des faces OU des edges.")
    show_inview_message("Sélectionne faces ou edges!", 2.0, "warning")


# =============================================================================
# SMART CONCENTRIC (Bridge OR Connect)
# =============================================================================
corner_angle_threshold = 45.0

clean_after_connect = True
clean_after_connect_angle = 1.0
clean_after_connect_allow_ngons = True
clean_after_connect_max_ngon = 6
clean_after_connect_passes = 10

angle_threshold_value = 5.0
allow_ngons_value = True
max_ngon_value = 6
only_selected_faces_value = False

do_concentric_face_bridge = True
do_concentric_edge_bridge = True
concentric_bridge_divisions = 0
concentric_bridge_twist = 0

merge_threshold_value = 0.001


def smart_concentric_bridge_or_connect():
    sel = cmds.ls(selection=True, flatten=True) or []
    if not sel:
        cmds.warning("Sélectionne des faces ou des edges.")
        show_inview_message("Aucune sélection!", 2.0, "warning")
        return

    mesh = get_selected_mesh()
    if not mesh:
        cmds.warning("Sélectionne un mesh / composants.")
        show_inview_message("Aucun mesh!", 2.0, "warning")
        return

    faces = filter_faces(sel)
    edges = filter_edges(sel)

    cmds.undoInfo(openChunk=True, chunkName="SmartConcentric")
    try:
        if faces:
            boundary = boundary_edges_from_faces(faces)
            comps = connected_edge_components(boundary)

            if len(comps) >= 2:
                comps = sorted(comps, key=lambda c: len(c), reverse=True)
                loop1 = comps[0]
                loop2 = comps[1]
                if do_concentric_face_bridge:
                    cmds.delete(faces)
                    cmds.select(loop1 + loop2, r=True)
                    cmds.polyBridgeEdge(divisions=concentric_bridge_divisions, twist=concentric_bridge_twist)
                    cmds.select(mesh, r=True)
                    show_inview_message("Bridge: {} + {} edges".format(len(loop1), len(loop2)), 2.0, "success")
                    return

            chains = split_boundary_by_angle(boundary, angle_threshold=corner_angle_threshold)
            if len(chains) < 2:
                cmds.warning("Je ne trouve pas 2 côtés à connecter.")
                cmds.select(mesh, r=True)
                show_inview_message("2 côtés requis!", 2.0, "warning")
                return

            chains = sorted(chains, key=lambda c: len(c), reverse=True)
            side1, side2 = chains[0], chains[1]

            v1 = ordered_chain_vertices(side1)
            v2 = ordered_chain_vertices(side2)
            v1, v2 = best_align_vertex_lists(v1, v2)

            cmds.select(v1 + v2, r=True)
            cmds.polyConnectComponents()
            show_inview_message("Connect: {} + {} vertices".format(len(v1), len(v2)), 2.0, "success")

            if clean_after_connect:
                cmds.select(faces, r=True)
                auto_clean(
                    angle_threshold=clean_after_connect_angle,
                    allow_ngons=clean_after_connect_allow_ngons,
                    max_ngon=clean_after_connect_max_ngon,
                    max_passes=clean_after_connect_passes,
                    only_selected_faces=True,
                )

            cmds.select(mesh, r=True)
            return

        if edges:
            comps = connected_edge_components(edges)

            if len(comps) == 2 and do_concentric_edge_bridge:
                cmds.select(comps[0] + comps[1], r=True)
                cmds.polyBridgeEdge(divisions=concentric_bridge_divisions, twist=concentric_bridge_twist)
                cmds.select(mesh, r=True)
                show_inview_message("Bridge: {} + {} edges".format(len(comps[0]), len(comps[1])), 2.0, "success")
                return

            if len(comps) == 2:
                vA = ordered_chain_vertices(comps[0])
                vB = ordered_chain_vertices(comps[1])
                vA, vB = best_align_vertex_lists(vA, vB)
                cmds.select(vA + vB, r=True)
                cmds.polyConnectComponents()
                cmds.select(mesh, r=True)
                show_inview_message("Connect effectué!", 2.0, "success")
                return

            cmds.warning("Sélectionne 2 groupes d'edges.")
            show_inview_message("2 groupes d'edges requis!", 2.0, "warning")
            return

        cmds.warning("Sélectionne des faces ou des edges.")
        show_inview_message("Sélectionne faces ou edges!", 2.0, "warning")

    except Exception as e:
        cmds.warning("SmartConcentric erreur: {}".format(e))
        show_inview_message("Erreur SmartConcentric!", 2.0, "error")
        import traceback
        traceback.print_exc()
    finally:
        cmds.undoInfo(closeChunk=True)


# =============================================================================
# DETECT TOOLS (multi-mesh)
# =============================================================================
def mesh_detect_ngons():
    meshes = get_all_selected_meshes()
    if not meshes:
        cmds.warning("Sélectionne un mesh!")
        show_inview_message("Aucun mesh sélectionné!", 2.0, "error")
        return

    ngons = []
    for mesh in meshes:
        face_count = cmds.polyEvaluate(mesh, face=True)
        for i in range(face_count):
            f = "{}.f[{}]".format(mesh, i)
            if face_vertex_count(f) > 4:
                ngons.append(f)

    if ngons:
        set_selection_mode("face")
        cmds.select(ngons, r=True)
        show_inview_message("{} N-gons détectés".format(len(ngons)), 2.0, "warning")
    else:
        cmds.select(meshes, r=True)
        show_inview_message("Aucun N-gon", 2.0, "success")


def mesh_detect_triangles():
    meshes = get_all_selected_meshes()
    if not meshes:
        cmds.warning("Sélectionne un mesh!")
        show_inview_message("Aucun mesh sélectionné!", 2.0, "error")
        return

    tris = []
    for mesh in meshes:
        face_count = cmds.polyEvaluate(mesh, face=True)
        for i in range(face_count):
            f = "{}.f[{}]".format(mesh, i)
            if face_vertex_count(f) == 3:
                tris.append(f)

    if tris:
        set_selection_mode("face")
        cmds.select(tris, r=True)
        show_inview_message("{} triangles détectés".format(len(tris)), 2.0, "warning")
    else:
        cmds.select(meshes, r=True)
        show_inview_message("Aucun triangle", 2.0, "success")


def mesh_detect_quads():
    meshes = get_all_selected_meshes()
    if not meshes:
        cmds.warning("Sélectionne un mesh!")
        show_inview_message("Aucun mesh sélectionné!", 2.0, "error")
        return

    quads = []
    for mesh in meshes:
        face_count = cmds.polyEvaluate(mesh, face=True)
        for i in range(face_count):
            f = "{}.f[{}]".format(mesh, i)
            if face_vertex_count(f) == 4:
                quads.append(f)

    if quads:
        set_selection_mode("face")
        cmds.select(quads, r=True)
        show_inview_message("{} quads détectés".format(len(quads)), 2.0, "info")
    else:
        cmds.select(meshes, r=True)
        show_inview_message("Aucun quad", 2.0, "warning")


def mesh_detect_stuck_extrusions():
    meshes = get_all_selected_meshes()
    if not meshes:
        cmds.warning("Sélectionne un mesh!")
        show_inview_message("Aucun mesh sélectionné!", 2.0, "error")
        return

    all_faces = []
    total_overlapping = 0

    for mesh in meshes:
        vcount = cmds.polyEvaluate(mesh, vertex=True)
        pos_to_verts = defaultdict(list)

        for i in range(vcount):
            vtx = "{}.vtx[{}]".format(mesh, i)
            pos = get_vertex_position(vtx)
            if pos:
                key = (round(pos[0], 5), round(pos[1], 5), round(pos[2], 5))
                pos_to_verts[key].append(vtx)

        overlapping = []
        for vs in pos_to_verts.values():
            if len(vs) > 1:
                overlapping.extend(vs)

        if overlapping:
            total_overlapping += len(overlapping)
            faces = set()
            for v in overlapping:
                for f in vert_to_faces(v):
                    faces.add(f)
            all_faces.extend(list(faces))

    if all_faces:
        set_selection_mode("face")
        cmds.select(all_faces, r=True)
        show_inview_message("{} vtx superposés, {} faces".format(total_overlapping, len(all_faces)), 2.0, "warning")
    else:
        cmds.select(meshes, r=True)
        show_inview_message("Aucune extrusion collée", 2.0, "success")


def mesh_detect_lamina_faces():
    meshes = get_all_selected_meshes()
    if not meshes:
        cmds.warning("Sélectionne un mesh!")
        show_inview_message("Aucun mesh sélectionné!", 2.0, "error")
        return

    lamina_faces = []
    for mesh in meshes:
        cmds.select(mesh, r=True)
        try:
            mel.eval('polyCleanupArgList 4 { "0","2","1","0","0","0","0","0","0","1e-05","0","1e-05","0","1e-05","0","-1","1","0" }')
        except Exception:
            pass
        sel = cmds.ls(selection=True, flatten=True) or []
        faces = filter_faces(sel)
        lamina_faces.extend(faces)

    if lamina_faces:
        set_selection_mode("face")
        cmds.select(lamina_faces, r=True)
        show_inview_message("{} faces lamina".format(len(lamina_faces)), 2.0, "warning")
    else:
        cmds.select(meshes, r=True)
        show_inview_message("Aucune face lamina", 2.0, "success")


# =============================================================================
# VERTEX TOOLS (multi-mesh)
# =============================================================================

# =============================================================================
# VERTEX TOOLS (multi-mesh)
# =============================================================================
def mesh_remove_useless_vertices(angle_tolerance=0.9998, max_passes=20):
    """
    Supprime les vertices inutiles (alignés sur une arête droite).
    Version corrigée et stable.
    """
    selection = cmds.ls(selection=True, type='transform')
    if not selection:
        cmds.warning("Sélectionne un mesh!")
        show_inview_message("Aucun mesh sélectionné!", 2.0, "error")
        return

    total_supprimes = 0
    tolerance = 0.00001   # <--- C'est ici que tolerance est défini

    for obj in selection:
        shapes = cmds.listRelatives(obj, shapes=True, type='mesh')
        if not shapes:
            continue

        mesh = shapes[0]
        print("Traitement de :", obj)

        verts = cmds.ls(mesh + ".vtx[*]", flatten=True)
        a_supprimer = []

        for v in verts:
            edges = cmds.polyListComponentConversion(v, toEdge=True, fromVertex=True)
            if not edges:
                continue
            edges = cmds.ls(edges, flatten=True)

            if len(edges) != 2:
                continue

            voisins = []
            for edge in edges:
                edge_verts = cmds.polyListComponentConversion(edge, toVertex=True)
                edge_verts = cmds.ls(edge_verts, flatten=True)
                autre = [ev for ev in edge_verts if ev != v]
                if autre:
                    voisins.append(autre[0])

            if len(voisins) != 2:
                continue

            pos_v = cmds.pointPosition(v, world=True)
            pos_a = cmds.pointPosition(voisins[0], world=True)
            pos_c = cmds.pointPosition(voisins[1], world=True)

            vec1 = [pos_v[i] - pos_a[i] for i in range(3)]
            vec2 = [pos_c[i] - pos_v[i] for i in range(3)]

            cross = [
                vec1[1] * vec2[2] - vec1[2] * vec2[1],
                vec1[2] * vec2[0] - vec1[0] * vec2[2],
                vec1[0] * vec2[1] - vec1[1] * vec2[0]
            ]

            if all(abs(val) < tolerance for val in cross):
                a_supprimer.append(v)

        if a_supprimer:
            # Tri inverse pour éviter les problèmes d'index
            a_supprimer.sort(key=lambda x: int(x.split('[')[-1].rstrip(']')), reverse=True)

            cmds.select(a_supprimer, replace=True)
            try:
                cmds.polyDelVertex(constructionHistory=False)
                print(len(a_supprimer), "vertex inutiles supprimes sur", obj)
                total_supprimes += len(a_supprimer)
            except:
                # Fallback
                removed = 0
                for v in a_supprimer:
                    if cmds.objExists(v):
                        try:
                            cmds.select(v, r=True)
                            cmds.delete(v)
                            removed += 1
                        except:
                            pass
                print(removed, "vertex inutiles supprimes sur", obj, "(fallback)")
                total_supprimes += removed
        else:
            print("Aucun vertex inutile trouve sur", obj)

    if total_supprimes > 0:
        print("FINI :", total_supprimes, "vertex inutiles supprimes au total.")
        show_inview_message(f"{total_supprimes} vertices supprimés", 2.0, "success")
    else:
        print("Aucun vertex inutile detecte.")
        show_inview_message("Aucun vertex inutile détecté", 2.0, "info")
# =============================================================================
# GEOMETRY HELPERS (pour triangulate)
# =============================================================================
def get_shared_vertices(face1, face2):
    """Retourne les vertices partagés entre deux faces."""
    if not cmds.objExists(face1) or not cmds.objExists(face2):
        return []
    
    verts1 = set(face_to_verts(face1))
    verts2 = set(face_to_verts(face2))
    
    return list(verts1.intersection(verts2))


def would_form_valid_quad(face1, face2):
    """
    Vérifie si deux triangles adjacents formeraient un quad valide.
    Un quad valide doit avoir exactement 4 vertices uniques.
    """
    if not cmds.objExists(face1) or not cmds.objExists(face2):
        return False
    
    if face_vertex_count(face1) != 3 or face_vertex_count(face2) != 3:
        return False
    
    # Récupérer tous les vertices des deux faces
    verts1 = set(face_to_verts(face1))
    verts2 = set(face_to_verts(face2))
    
    # Un quad valide = 2 vertices partagés + 2 uniques par face = 4 vertices total
    shared = verts1.intersection(verts2)
    all_verts = verts1.union(verts2)
    
    # Doit avoir exactement 2 vertices partagés (l'edge commune)
    # Et exactement 4 vertices au total
    return len(shared) == 2 and len(all_verts) == 4


def get_triangle_aspect_ratio(face):
    """Calcule le ratio d'aspect d'un triangle (longueur max / longueur min)."""
    if not cmds.objExists(face):
        return 0.0
    
    if face_vertex_count(face) != 3:
        return 0.0
    
    try:
        verts = cmds.ls(cmds.polyListComponentConversion(face, toVertex=True), flatten=True)
        if len(verts) != 3:
            return 0.0
        
        positions = [get_vertex_position(v) for v in verts]
        if None in positions:
            return 0.0
        
        def distance(p1, p2):
            return math.sqrt(sum((a - b) ** 2 for a, b in zip(p1, p2)))
        
        edge_lengths = [
            distance(positions[0], positions[1]),
            distance(positions[1], positions[2]),
            distance(positions[2], positions[0])
        ]
        
        max_length = max(edge_lengths)
        min_length = min(edge_lengths)
        
        if min_length < 0.0001:
            return 999.0
        
        return max_length / min_length
    except Exception:
        return 0.0


# =============================================================================
# TOPOLOGY TOOLS (multi-mesh) -- FULLY FIXED
# =============================================================================
def _quadify_tri_pairs_in_scope(mesh, scope_faces_set, check_stretch=False, stretch_threshold=3.0):
    """
    Détecte et convertit intelligemment les paires de triangles en quads.
    
    LOGIQUE:
    - Parcourt toutes les edges internes (non-border)
    - Pour chaque edge, vérifie si elle connecte 2 triangles
    - Vérifie que les 2 triangles sont dans le scope
    - Vérifie qu'ils formeraient un quad valide (4 vertices)
    - Option: vérifie le ratio d'aspect si check_stretch
    - Supprime l'edge pour merger en quad
    """
    try:
        edge_count = cmds.polyEvaluate(mesh, edge=True) or 0
    except Exception:
        return 0
    
    edges_to_remove = []

    for i in range(int(edge_count)):
        e = "{}.e[{}]".format(mesh, i)
        
        if not cmds.objExists(e):
            continue
        
        # Skip les edges de bord
        if is_border_edge(e):
            continue

        # Récupérer les faces connectées
        fs = edge_to_faces(e)
        if len(fs) != 2:
            continue

        f1, f2 = fs[0], fs[1]
        
        # Les 2 faces doivent être dans le scope
        if f1 not in scope_faces_set or f2 not in scope_faces_set:
            continue

        # Les 2 faces doivent être des triangles
        count1 = face_vertex_count(f1)
        count2 = face_vertex_count(f2)
        
        if count1 != 3 or count2 != 3:
            continue
        
        # Vérifier qu'ils formeraient un quad valide (pas un bowtie ou autre)
        if not would_form_valid_quad(f1, f2):
            continue
        
        # Si on vérifie le stretch, on évite de merger des triangles trop étirés
        if check_stretch:
            ratio1 = get_triangle_aspect_ratio(f1)
            ratio2 = get_triangle_aspect_ratio(f2)
            if ratio1 > stretch_threshold or ratio2 > stretch_threshold:
                continue

        edges_to_remove.append(e)

    # Suppression des edges en batch pour performance
    if edges_to_remove:
        try:
            cmds.polyDelEdge(edges_to_remove, cleanVertices=True, constructionHistory=False)
        except Exception as e:
            cmds.warning("Erreur lors de la suppression d'edges: {}".format(e))
            return 0
    
    return len(edges_to_remove)


def mesh_smart_quadrangulate():
    """
    Convertit TOUS les triangles adjacents en quads (sur mesh entier ou faces sélectionnées).
    Utilise polyQuad natif de Maya.
    """
    work_on_selection, meshes, faces_by_mesh, sel_faces = get_work_scope()

    if not meshes:
        cmds.warning("Sélectionne un mesh ou des faces!")
        show_inview_message("Aucun mesh!", 2.0, "error")
        return

    cmds.undoInfo(openChunk=True, chunkName="SmartQuadrangulate")
    try:
        total_quadded = 0

        for mesh in meshes:
            if not cmds.objExists(mesh):
                continue

            # Récupérer les faces dans le scope
            scope_faces = _faces_in_scope(mesh, work_on_selection, faces_by_mesh)
            if not scope_faces:
                continue

            # Trouver TOUS les triangles dans le scope
            tris = []
            for f in scope_faces:
                if not cmds.objExists(f):
                    continue
                if face_vertex_count(f) == 3:
                    tris.append(f)

            if tris:
                # Delete history pour éviter les problèmes
                try:
                    cmds.delete(mesh, constructionHistory=True)
                except:
                    pass
                
                # Quadranguler avec polyQuad
                cmds.select(tris, r=True)
                cmds.polyQuad(angle=30, keepGroupBorder=True, keepHardEdges=True, constructionHistory=False)
                total_quadded += len(tris)

        # Restauration
        if work_on_selection:
            valid = [f for f in sel_faces if cmds.objExists(f)]
            if valid:
                set_selection_mode("face")
                cmds.select(valid, r=True)
        else:
            valid_meshes = [m for m in meshes if cmds.objExists(m)]
            if valid_meshes:
                cmds.select(valid_meshes, r=True)

        if total_quadded > 0:
            show_inview_message("{} tris->quads".format(total_quadded), 2.0, "success")
        else:
            show_inview_message("Aucun triangle a quadranguler", 2.0, "info")

    except Exception as e:
        cmds.warning("SmartQuad erreur: {}".format(e))
        show_inview_message("Erreur!", 2.0, "error")
        import traceback
        traceback.print_exc()
    finally:
        cmds.undoInfo(closeChunk=True)


def select_removable_edges(angle_threshold=5.0, allow_ngons=False, only_selected_faces=False):
    """
    Sélectionne les edges supprimables (coplanaires).
    
    Args:
        angle_threshold: Angle max entre faces (0° = parfaitement plat)
        allow_ngons: Si False, ne supprime QUE les edges entre 2 quads (résultat = quad)
                     Si True, autorise la création de ngons (plus agressif)
        only_selected_faces: Si True, travaille seulement sur les faces sélectionnées
    """
    mesh = get_selected_mesh()
    if not mesh:
        cmds.warning("Sélectionne un mesh!")
        show_inview_message("Aucun mesh selectionne!", 2.0, "error")
        return

    sel = cmds.ls(selection=True, flatten=True) or []
    sel_faces = filter_faces(sel)

    # Définir les edges candidates
    if only_selected_faces and sel_faces:
        # Collecter uniquement les edges dans la région des faces sélectionnées
        candidate_edges = set()
        for f in sel_faces:
            for e in face_to_edges(f):
                candidate_edges.add(e)
        candidate_edges = list(candidate_edges)
    else:
        # Toutes les edges du mesh
        edge_count = cmds.polyEvaluate(mesh, edge=True)
        candidate_edges = ["{}.e[{}]".format(mesh, i) for i in range(edge_count)]

    removable = []
    for e in candidate_edges:
        if not cmds.objExists(e):
            continue
        
        # Skip les edges de bord
        if is_border_edge(e):
            continue

        # Vérifier l'angle
        ang = get_edge_face_angle(e)
        if ang > angle_threshold:
            continue

        # Vérifier le résultat de la fusion
        faces = edge_to_faces(e)
        if len(faces) != 2:
            continue

        v1 = face_vertex_count(faces[0])
        v2 = face_vertex_count(faces[1])
        merged_vertices = v1 + v2 - 2  # Nombre de vertices après fusion

        if allow_ngons:
            # Mode agressif : autorise jusqu'à des polygones raisonnables (ex: 8 sides max)
            if merged_vertices > 8:
                continue
        else:
            # Mode conservateur : UNIQUEMENT quad->quad (4+4-2=6 NON, 3+3-2=4 OUI)
            # Donc on veut que le résultat soit <= 4
            if merged_vertices > 4:
                continue

        removable.append(e)

    if removable:
        set_selection_mode("edge")
        cmds.select(removable, r=True)
        mode_txt = "allow ngons" if allow_ngons else "quads only"
        show_inview_message("{} edges ({}, {}°)".format(len(removable), mode_txt, angle_threshold), 2.0, "info")
    else:
        cmds.select(mesh, r=True)
        show_inview_message("Aucune edge supprimable", 2.0, "success")


def auto_clean(angle_threshold=5.0, allow_ngons=False, max_passes=50, only_selected_faces=False):
    """
    Supprime automatiquement les edges coplanaires.
    
    Args:
        angle_threshold: Angle max entre faces
        allow_ngons: Si True, autorise création de ngons (plus agressif)
        max_passes: Nombre max de passes
        only_selected_faces: Si True, travaille seulement sur faces sélectionnées
    """
    mesh = get_selected_mesh()
    if not mesh:
        cmds.warning("Sélectionne un mesh!")
        show_inview_message("Aucun mesh selectionne!", 2.0, "error")
        return

    sel = cmds.ls(selection=True, flatten=True) or []
    sel_faces = filter_faces(sel)

    cmds.undoInfo(openChunk=True, chunkName="AutoClean")
    try:
        total = 0
        
        for _pass in range(max_passes):
            if not cmds.objExists(mesh):
                break

            # Définir les edges candidates
            if only_selected_faces and sel_faces:
                candidate_edges = set()
                for f in sel_faces:
                    if cmds.objExists(f):
                        for e in face_to_edges(f):
                            candidate_edges.add(e)
                candidate_edges = list(candidate_edges)
            else:
                edge_count = cmds.polyEvaluate(mesh, edge=True)
                candidate_edges = ["{}.e[{}]".format(mesh, i) for i in range(edge_count)]

            removable = []
            for e in candidate_edges:
                if not cmds.objExists(e):
                    continue
                if is_border_edge(e):
                    continue

                ang = get_edge_face_angle(e)
                if ang > angle_threshold:
                    continue

                faces = edge_to_faces(e)
                if len(faces) != 2:
                    continue

                v1 = face_vertex_count(faces[0])
                v2 = face_vertex_count(faces[1])
                merged = v1 + v2 - 2

                if allow_ngons:
                    if merged > 8:
                        continue
                else:
                    if merged > 4:
                        continue

                removable.append(e)

            if not removable:
                break

            try:
                cmds.polyDelEdge(removable, cleanVertices=True, constructionHistory=False)
                total += len(removable)
            except Exception:
                cnt = 0
                for e in removable:
                    if not cmds.objExists(e):
                        continue
                    try:
                        cmds.polyDelEdge(e, cleanVertices=True, constructionHistory=False)
                        cnt += 1
                    except Exception:
                        pass
                if cnt == 0:
                    break
                total += cnt

        if cmds.objExists(mesh):
            cmds.select(mesh, r=True)

        mode_txt = "allow ngons" if allow_ngons else "quads only"
        show_inview_message("{} edges ({})".format(total, mode_txt), 2.0, "success" if total > 0 else "info")

    except Exception as e:
        cmds.warning("AutoClean erreur: {}".format(e))
        show_inview_message("Erreur AutoClean!", 2.0, "error")
        import traceback
        traceback.print_exc()
    finally:
        cmds.undoInfo(closeChunk=True)


def mesh_smart_triangulate(avoid_stretched=False, stretch_threshold=3.0):
    """
    Smart triangulate intelligent (scope-safe, multi-mesh):
      1) QUAD d'abord : utilise polyQuad sur TOUS les triangles
      2) TRIANGULATE uniquement les NGONS (>4 vertices)
         - Si avoid_stretched=True : subdivise les edges trop longues AVANT triangulation
      3) RE-QUAD : re-utilise polyQuad sur les nouveaux triangles
    
    Args:
        avoid_stretched: Si True, subdivise les edges longues avant triangulation
        stretch_threshold: Ratio max edge_length / avg_edge_length (défaut 3.0)
    """
    work_on_selection, meshes, faces_by_mesh, sel_faces = get_work_scope()

    if work_on_selection and not sel_faces:
        cmds.warning("Sélectionne des faces!")
        show_inview_message("Aucune face!", 2.0, "error")
        return
    if not work_on_selection and not meshes:
        cmds.warning("Sélectionne un mesh ou des faces!")
        show_inview_message("Aucun mesh!", 2.0, "error")
        return

    def get_edge_length(edge):
        """Calcule la longueur d'une edge."""
        verts = edge_to_verts(edge)
        if len(verts) != 2:
            return 0.0
        p1 = get_vertex_position(verts[0])
        p2 = get_vertex_position(verts[1])
        if not p1 or not p2:
            return 0.0
        dx = p1[0] - p2[0]
        dy = p1[1] - p2[1]
        dz = p1[2] - p2[2]
        return math.sqrt(dx*dx + dy*dy + dz*dz)

    def subdivide_long_edges(faces, threshold):
        """
        Subdivise les edges trop longues sur les faces données.
        threshold = ratio par rapport à la longueur moyenne
        """
        if not faces:
            return 0
        
        # Collecter toutes les edges des faces
        all_edges = set()
        for f in faces:
            for e in face_to_edges(f):
                all_edges.add(e)
        
        if not all_edges:
            return 0
        
        # Calculer longueur moyenne
        lengths = [get_edge_length(e) for e in all_edges]
        lengths = [l for l in lengths if l > 0]
        if not lengths:
            return 0
        
        avg_length = sum(lengths) / len(lengths)
        max_allowed = avg_length * threshold
        
        # Trouver les edges à subdiviser
        edges_to_subdivide = []
        for e in all_edges:
            if get_edge_length(e) > max_allowed:
                edges_to_subdivide.append(e)
        
        if edges_to_subdivide:
            try:
                cmds.select(edges_to_subdivide, r=True)
                cmds.polySubdivideEdge(divisions=1, constructionHistory=False)
                return len(edges_to_subdivide)
            except:
                return 0
        
        return 0

    cmds.undoInfo(openChunk=True, chunkName="SmartTriangulate")
    try:
        total_quadded_1 = 0
        total_ngons = 0
        total_subdivided = 0
        total_quadded_2 = 0

        for mesh in meshes:
            if not cmds.objExists(mesh):
                continue

            # === PHASE 1: QUAD D'ABORD avec polyQuad ===
            scope_faces = _faces_in_scope(mesh, work_on_selection, faces_by_mesh)
            if not scope_faces:
                continue
            
            # Trouver tous les triangles dans le scope
            tris = [f for f in scope_faces if cmds.objExists(f) and face_vertex_count(f) == 3]
            if tris:
                cmds.select(tris, r=True)
                cmds.polyQuad(angle=30, keepGroupBorder=True, keepHardEdges=True, constructionHistory=False)
                total_quadded_1 += len(tris)

            # === PHASE 2: Triangulation des NGONS ===
            scope_faces = _faces_in_scope(mesh, work_on_selection, faces_by_mesh)
            
            ngons = [f for f in scope_faces if cmds.objExists(f) and face_vertex_count(f) > 4]
            
            if ngons:
                total_ngons += len(ngons)
                
                # Delete history avant
                try:
                    cmds.delete(mesh, constructionHistory=True)
                except:
                    pass
                
                # Si anti-stretch : subdiviser les edges longues AVANT triangulation
                if avoid_stretched:
                    subdivided = subdivide_long_edges(ngons, stretch_threshold)
                    total_subdivided += subdivided
                    
                    # Re-scanner les faces après subdivision
                    scope_faces = _faces_in_scope(mesh, work_on_selection, faces_by_mesh)
                    ngons = [f for f in scope_faces if cmds.objExists(f) and face_vertex_count(f) > 4]
                
                # Triangulation
                if ngons:
                    cmds.select(ngons, r=True)
                    cmds.polyTriangulate(constructionHistory=False)

                # === PHASE 3: RE-QUAD avec polyQuad ===
                scope_faces = _faces_in_scope(mesh, work_on_selection, faces_by_mesh)
                
                tris2 = [f for f in scope_faces if cmds.objExists(f) and face_vertex_count(f) == 3]
                if tris2:
                    cmds.select(tris2, r=True)
                    cmds.polyQuad(angle=30, keepGroupBorder=True, keepHardEdges=True, constructionHistory=False)
                    total_quadded_2 += len(tris2)

        # === RESTAURATION ===
        if work_on_selection:
            valid = [f for f in sel_faces if cmds.objExists(f)]
            if valid:
                set_selection_mode("face")
                cmds.select(valid, r=True)
        else:
            valid_meshes = [m for m in meshes if cmds.objExists(m)]
            if valid_meshes:
                cmds.select(valid_meshes, r=True)

        # === MESSAGES ===
        if total_ngons == 0 and total_quadded_1 == 0 and total_quadded_2 == 0:
            show_inview_message("Rien a traiter (deja optimise)!", 2.0, "info")
        else:
            msg_parts = []
            if total_quadded_1 > 0:
                msg_parts.append("{} tris->quads".format(total_quadded_1))
            if total_ngons > 0:
                msg_parts.append("{} ngons tri".format(total_ngons))
            if total_subdivided > 0:
                msg_parts.append("{} edges subdiv".format(total_subdivided))
            if total_quadded_2 > 0:
                msg_parts.append("{} re-quad".format(total_quadded_2))
            
            msg = "{}".format(", ".join(msg_parts))
            if avoid_stretched:
                msg += " (quality)"
            show_inview_message(msg, 3.0, "success")

    except Exception as e:
        cmds.warning("SmartTri erreur: {}".format(e))
        show_inview_message("Erreur!", 2.0, "error")
        import traceback
        traceback.print_exc()
    finally:
        cmds.undoInfo(closeChunk=True)


def mesh_smart_triangulate_ui():
    """Version UI standard - mode rapide."""
    mesh_smart_triangulate(avoid_stretched=False, stretch_threshold=3.0)


def mesh_smart_triangulate_quality():
    """Version avec anti-stretch activé - mode qualité."""
    mesh_smart_triangulate(avoid_stretched=True, stretch_threshold=3.0)


# =============================================================================
# MeshToolkit - CUSTOM WIDGETS (namespaced)
# =============================================================================
class MTK_ColorBtn(QtWidgets.QPushButton):
    def __init__(self, text="", tip="", bg="#2d2d2d", fg="#a0a0a0", w=None, h=26, parent=None):
        super(MTK_ColorBtn, self).__init__(text, parent)
        if w:
            self.setFixedSize(w, h)
        else:
            self.setFixedHeight(h)
        self.setToolTip(tip)
        self._bg = bg
        self._fg = fg
        self._update_style()

    def _update_style(self):
        self.setStyleSheet(
            """
            QPushButton {{
                background-color: {bg}; color: {fg};
                border: 1px solid #222; border-radius: 3px;
                font-weight: bold; font-size: 10px;
                padding: 2px 8px;
            }}
            QPushButton:hover {{ background-color: {bgh}; border-color: #444; }}
            QPushButton:pressed {{ background-color: #1a1a1a; }}
        """.format(bg=self._bg, fg=self._fg, bgh=QtGui.QColor(self._bg).lighter(130).name())
        )


class MTK_SectionLabel(QtWidgets.QLabel):
    def __init__(self, text, parent=None):
        super(MTK_SectionLabel, self).__init__(text, parent)
        self.setStyleSheet(
            """
            color: #555555;
            font-size: 9px;
            font-weight: bold;
            padding: 4px 0 2px 0;
            border-bottom: 1px solid #2a2a2a;
        """
        )


class MTK_ParamSlider(QtWidgets.QWidget):
    valueChanged = QtCore.Signal(float)

    def __init__(self, label, min_val, max_val, default, decimals=1, parent=None):
        super(MTK_ParamSlider, self).__init__(parent)
        self._decimals = decimals
        self._multiplier = 10 ** decimals

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._label = QtWidgets.QLabel(label)
        self._label.setFixedWidth(70)
        self._label.setStyleSheet("color: #707070; font-size: 9px;")

        self._slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._slider.setRange(int(min_val * self._multiplier), int(max_val * self._multiplier))
        self._slider.setValue(int(default * self._multiplier))

        self._spin = QtWidgets.QDoubleSpinBox()
        self._spin.setRange(min_val, max_val)
        self._spin.setDecimals(decimals)
        self._spin.setValue(default)
        self._spin.setFixedWidth(50)
        self._spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)

        layout.addWidget(self._label)
        layout.addWidget(self._slider)
        layout.addWidget(self._spin)

        self._slider.valueChanged.connect(self._on_slider)
        self._spin.valueChanged.connect(self._on_spin)

    def _on_slider(self, val):
        real_val = val / self._multiplier
        self._spin.blockSignals(True)
        self._spin.setValue(real_val)
        self._spin.blockSignals(False)
        self.valueChanged.emit(real_val)

    def _on_spin(self, val):
        self._slider.blockSignals(True)
        self._slider.setValue(int(val * self._multiplier))
        self._slider.blockSignals(False)
        self.valueChanged.emit(val)

    def value(self):
        return self._spin.value()


# =============================================================================
# MeshToolkit - WIDGET (as Tab content)
# =============================================================================
class MeshToolkitWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super(MeshToolkitWidget, self).__init__(parent)
        self._build_ui()
        self._apply_style()
        self._connect_signals()

    def _build_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        # ===== BRIDGE / CONNECT =====
        main_layout.addWidget(MTK_SectionLabel("BRIDGE / CONNECT"))

        bridge_row = QtWidgets.QHBoxLayout()
        bridge_row.setSpacing(3)

        self.smart_concentric_btn = MTK_ColorBtn("? Smart", "Smart Concentric: Bridge or Connect", "#1e3a50", "#70b0ff", 70, 28)
        self.bridge_hole_btn = MTK_ColorBtn("? Hole", "Fill hole from border loop", "#1e4a30", "#70ff90", 65, 28)
        self.bridge_loops_btn = MTK_ColorBtn("? Bridge", "Bridge 2 edge loops", "#1e3a50", "#70b0ff", 65, 28)

        bridge_row.addWidget(self.smart_concentric_btn)
        bridge_row.addWidget(self.bridge_hole_btn)
        bridge_row.addWidget(self.bridge_loops_btn)
        main_layout.addLayout(bridge_row)

        self.bridge_settings = QtWidgets.QWidget()
        bs_layout = QtWidgets.QVBoxLayout(self.bridge_settings)
        bs_layout.setContentsMargins(0, 4, 0, 0)
        bs_layout.setSpacing(3)

        self.corner_slider = MTK_ParamSlider("Corner °", 5, 120, corner_angle_threshold, 0)
        bs_layout.addWidget(self.corner_slider)

        self.clean_after_chk = QtWidgets.QCheckBox("Auto-clean after connect")
        self.clean_after_chk.setChecked(clean_after_connect)
        bs_layout.addWidget(self.clean_after_chk)

        self.bridge_settings.setVisible(False)
        main_layout.addWidget(self.bridge_settings)

        self.bridge_settings_btn = QtWidgets.QPushButton("? Settings")
        self.bridge_settings_btn.setFixedHeight(16)
        self.bridge_settings_btn.setStyleSheet(
            """
            QPushButton {
                background: transparent; color: #404040;
                border: none; font-size: 9px;
            }
            QPushButton:hover { color: #606060; }
        """
        )
        main_layout.addWidget(self.bridge_settings_btn)

        # ===== DETECT =====
        main_layout.addWidget(MTK_SectionLabel("DETECT"))

        detect_row = QtWidgets.QHBoxLayout()
        detect_row.setSpacing(3)

        self.ngon_btn = MTK_ColorBtn("? NGon", "Detect N-gons", "#50301e", "#ffaa70", 58, 26)
        self.tri_btn = MTK_ColorBtn("? Tri", "Detect triangles", "#50301e", "#ffaa70", 50, 26)
        self.quad_btn = MTK_ColorBtn("? Quad", "Detect quads", "#1e4030", "#70c080", 55, 26)
        self.stuck_btn = MTK_ColorBtn("? Stuck", "Stuck extrusions", "#50301e", "#ffaa70", 55, 26)
        self.lamina_btn = MTK_ColorBtn("? Lam", "Lamina faces", "#50301e", "#ffaa70", 50, 26)

        detect_row.addWidget(self.ngon_btn)
        detect_row.addWidget(self.tri_btn)
        detect_row.addWidget(self.quad_btn)
        detect_row.addWidget(self.stuck_btn)
        detect_row.addWidget(self.lamina_btn)
        main_layout.addLayout(detect_row)

        # ===== VERTICES =====
        main_layout.addWidget(MTK_SectionLabel("VERTICES"))

        vert_row = QtWidgets.QHBoxLayout()
        vert_row.setSpacing(3)

        self.remove_verts_btn = MTK_ColorBtn("? Remove Useless", "Remove unnecessary vertices", "#3a2a2a", "#ff9090", 110, 26)
        self.merge_verts_btn = MTK_ColorBtn("? Merge", "Merge overlapping vertices", "#3a2a2a", "#ff9090", 70, 26)

        vert_row.addWidget(self.remove_verts_btn)
        vert_row.addWidget(self.merge_verts_btn)
        vert_row.addStretch()
        main_layout.addLayout(vert_row)

        self.merge_slider = MTK_ParamSlider("Threshold", 0, 0.1, merge_threshold_value, 4)
        main_layout.addWidget(self.merge_slider)

        # ===== TOPOLOGY =====
        main_layout.addWidget(MTK_SectionLabel("TOPOLOGY"))

        topo_row = QtWidgets.QHBoxLayout()
        topo_row.setSpacing(3)

        self.quadrangulate_btn = MTK_ColorBtn("? Quadrangulate", "Convert triangle pairs to quads", "#1e4030", "#70c080", 105, 26)
        self.triangulate_btn = MTK_ColorBtn("? Triangulate", "Smart: Quad->Tri->Quad", "#1e3050", "#7090c0", 100, 26)
        self.triangulate_quality_btn = MTK_ColorBtn("? Tri Quality", "Anti-stretch triangulation", "#2e4050", "#80a0d0", 90, 26)

        topo_row.addWidget(self.quadrangulate_btn)
        topo_row.addWidget(self.triangulate_btn)
        topo_row.addWidget(self.triangulate_quality_btn)
        main_layout.addLayout(topo_row)

        # ===== CLEAN =====
        main_layout.addWidget(MTK_SectionLabel("CLEAN"))

        self.angle_slider = MTK_ParamSlider("Coplanar °", 0, 45, angle_threshold_value, 1)
        main_layout.addWidget(self.angle_slider)

        clean_opts = QtWidgets.QHBoxLayout()
        clean_opts.setSpacing(8)

        self.local_chk = QtWidgets.QCheckBox("Local only")
        self.local_chk.setChecked(only_selected_faces_value)
        self.local_chk.setToolTip("Only operate on selected faces")

        self.ngons_chk = QtWidgets.QCheckBox("Allow N-gons")
        self.ngons_chk.setChecked(allow_ngons_value)
        self.ngons_chk.setToolTip("Allow creating N-gons (more aggressive)")

        clean_opts.addWidget(self.local_chk)
        clean_opts.addWidget(self.ngons_chk)
        clean_opts.addStretch()
        main_layout.addLayout(clean_opts)



        clean_row = QtWidgets.QHBoxLayout()
        clean_row.setSpacing(3)

        self.select_edges_btn = MTK_ColorBtn("? Select Edges", "Select removable edges", "#3a3a20", "#c0c070", 100, 28)
        self.auto_clean_btn = MTK_ColorBtn("? Auto Clean", "Automatically clean mesh", "#4a3a10", "#ffc040", 100, 28)

        clean_row.addWidget(self.select_edges_btn)
        clean_row.addWidget(self.auto_clean_btn)
        main_layout.addLayout(clean_row)

        main_layout.addStretch()

    def _apply_style(self):
        self.setStyleSheet(
            """
            QWidget { background-color: #1e1e1e; }
            QSlider::groove:horizontal { height: 4px; background: #2a2a2a; border-radius: 2px; }
            QSlider::handle:horizontal { background: #5a8a5a; width: 12px; margin: -4px 0; border-radius: 6px; }
            QSlider::handle:horizontal:hover { background: #70a070; }
            QSlider::sub-page:horizontal { background: #3a5a3a; border-radius: 2px; }
            QSpinBox, QDoubleSpinBox {
                background: #252525; color: #a0a0a0;
                border: 1px solid #303030; border-radius: 3px;
                padding: 2px; font-size: 10px;
            }
            QCheckBox { color: #707070; font-size: 10px; spacing: 4px; }
            QCheckBox::indicator {
                width: 14px; height: 14px; border-radius: 3px;
                border: 1px solid #3a3a3a; background: #252525;
            }
            QCheckBox::indicator:checked { background: #5a8a5a; border-color: #70a070; }
            QCheckBox::indicator:hover { border-color: #505050; }
        """
        )

    def _connect_signals(self):
        self.smart_concentric_btn.clicked.connect(smart_concentric_bridge_or_connect)
        self.bridge_hole_btn.clicked.connect(lambda: mesh_smart_bridge_hole())
        self.bridge_loops_btn.clicked.connect(lambda: bridge_concentric_from_faces_or_edges(
            divisions=concentric_bridge_divisions, twist=concentric_bridge_twist
        ))

        self.bridge_settings_btn.clicked.connect(self._toggle_bridge_settings)
        self.corner_slider.valueChanged.connect(self._on_corner_changed)
        self.clean_after_chk.stateChanged.connect(self._on_clean_after_changed)

        self.ngon_btn.clicked.connect(mesh_detect_ngons)
        self.tri_btn.clicked.connect(mesh_detect_triangles)
        self.quad_btn.clicked.connect(mesh_detect_quads)
        self.stuck_btn.clicked.connect(mesh_detect_stuck_extrusions)
        self.lamina_btn.clicked.connect(mesh_detect_lamina_faces)

        self.remove_verts_btn.clicked.connect(mesh_remove_useless_vertices)
        self.merge_verts_btn.clicked.connect(lambda: mesh_merge_vertices(threshold=merge_threshold_value))
        self.merge_slider.valueChanged.connect(self._on_merge_threshold)

        self.quadrangulate_btn.clicked.connect(mesh_smart_quadrangulate)
        self.triangulate_btn.clicked.connect(mesh_smart_triangulate_ui)
        self.triangulate_quality_btn.clicked.connect(mesh_smart_triangulate_quality)

        self.angle_slider.valueChanged.connect(self._on_angle_changed)
        self.local_chk.stateChanged.connect(self._on_local_changed)
        self.ngons_chk.stateChanged.connect(self._on_ngons_changed)

        self.select_edges_btn.clicked.connect(self._on_select_edges)
        self.auto_clean_btn.clicked.connect(self._on_auto_clean)

    def _toggle_bridge_settings(self):
        self.bridge_settings.setVisible(not self.bridge_settings.isVisible())

    def _on_corner_changed(self, val):
        global corner_angle_threshold
        corner_angle_threshold = float(val)

    def _on_clean_after_changed(self, state):
        global clean_after_connect
        clean_after_connect = (state == QtCore.Qt.Checked)

    def _on_merge_threshold(self, val):
        global merge_threshold_value
        merge_threshold_value = float(val)

    def _on_angle_changed(self, val):
        global angle_threshold_value
        angle_threshold_value = float(val)

    def _on_local_changed(self, state):
        global only_selected_faces_value
        only_selected_faces_value = (state == QtCore.Qt.Checked)

    def _on_ngons_changed(self, state):
        global allow_ngons_value
        allow_ngons_value = (state == QtCore.Qt.Checked)



    def _on_select_edges(self):
        select_removable_edges(
            angle_threshold=angle_threshold_value,
            allow_ngons=allow_ngons_value,
            only_selected_faces=only_selected_faces_value,
        )
    def _on_auto_clean(self):
        auto_clean(
            angle_threshold=angle_threshold_value,
            allow_ngons=allow_ngons_value,
            max_passes=50,
            only_selected_faces=only_selected_faces_value,
        )

# =============================================================================
# =============================================================================
#                               TAB 2 - PR SELECT TOOLS v3.8
# =============================================================================
# =============================================================================

# ============================================================
# HELPERS (PR)
# ============================================================
def pr_ensure_face_component_mode():
    try:
        cmds.selectMode(component=True)
    except Exception:
        pass
    try:
        cmds.selectType(facet=True)
    except Exception:
        pass


def pr_force_selection_faces_only():
    try:
        cmds.ConvertSelectionToFaces()
    except Exception:
        pass
    try:
        sel = cmds.ls(sl=True, fl=True) or []
        only_faces = [c for c in sel if ".f[" in c]
        cmds.select(only_faces, replace=True)
    except Exception:
        pass
    pr_ensure_face_component_mode()


def pr_get_mesh_from_selection():
    sel = cmds.ls(selection=True, long=True) or []
    if not sel:
        return None
    obj = sel[0].split(".")[0] if "." in sel[0] else sel[0]
    shapes = cmds.listRelatives(obj, shapes=True, fullPath=True) or []
    for s in shapes:
        if cmds.nodeType(s) == "mesh":
            return obj
    if cmds.nodeType(obj) == "mesh":
        p = cmds.listRelatives(obj, parent=True, fullPath=True)
        return p[0] if p else None
    return None


def pr_get_camera_direction():
    try:
        panel = cmds.getPanel(withFocus=True)
        if not panel or cmds.getPanel(typeOf=panel) != "modelPanel":
            panels = cmds.getPanel(type="modelPanel") or []
            panel = panels[0] if panels else None
        if panel:
            camera = cmds.modelPanel(panel, q=True, camera=True)
            cam_matrix = cmds.xform(camera, q=True, m=True, ws=True)
            direction = [cam_matrix[8], cam_matrix[9], cam_matrix[10]]
            return direction
    except Exception:
        pass
    return [0, 0, 1]


def pr_parse_face_index(comp):
    m = re.search(r"\.f\[(\d+)\]", comp)
    if not m:
        return None
    return int(m.group(1))


def pr_get_dagpath_and_fnmesh(mesh_transform):
    if not om2:
        return None, None
    try:
        sel = om2.MSelectionList()
        sel.add(mesh_transform)
        dag = sel.getDagPath(0)
        if dag.apiType() != om2.MFn.kMesh:
            dag.extendToShape()
        fn = om2.MFnMesh(dag)
        return dag, fn
    except Exception:
        return None, None


# ============================================================
# PR SELECT TOOLS WIDGET (dummy placeholder - ton code complet ici)
# ============================================================
class PRSelectToolsWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super(PRSelectToolsWidget, self).__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        label = QtWidgets.QLabel("PR Select Tools v3.8\n(Placeholder - ajoute ton code ici)")
        label.setStyleSheet("color: #888; font-size: 12px;")
        label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(label)
        layout.addStretch()


# =============================================================================
# MAIN WINDOW (2 TABS)
# =============================================================================
class QDMegaToolsWindow(QtWidgets.QDialog):
    WINDOW_TITLE = "SmartCleaner"

    def __init__(self, parent=None):
        super(QDMegaToolsWindow, self).__init__(parent or get_maya_main_window())
        self.setWindowTitle(self.WINDOW_TITLE)
        self.setWindowFlags(QtCore.Qt.Window)
        self.setMinimumSize(320, 400)
        self.resize(320, 450)

        self._build_ui()
        self._apply_global_style()

    def _build_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.tab_widget = QtWidgets.QTabWidget()
        self.tab_widget.setTabPosition(QtWidgets.QTabWidget.North)

        # TAB 1 - Mesh Toolkit
        self.mesh_toolkit_widget = MeshToolkitWidget()
        self.tab_widget.addTab(self.mesh_toolkit_widget, "Mesh Toolkit")



        main_layout.addWidget(self.tab_widget)

    def _apply_global_style(self):
        self.setStyleSheet(
            """
            QDialog {
                background-color: #1e1e1e;
            }
            QTabWidget::pane {
                border: 1px solid #2a2a2a;
                background: #1e1e1e;
            }
            QTabBar::tab {
                background: #2a2a2a;
                color: #707070;
                padding: 6px 16px;
                border: 1px solid #222;
                border-bottom: none;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background: #1e1e1e;
                color: #aaaaaa;
                border-bottom: 1px solid #1e1e1e;
            }
            QTabBar::tab:hover {
                background: #333;
            }
        """
        )


# =============================================================================
# SHOW UI
# =============================================================================
def show_ui():
    global qd_mega_tools_window
    try:
        qd_mega_tools_window.close()
        qd_mega_tools_window.deleteLater()
    except Exception:
        pass

    qd_mega_tools_window = QDMegaToolsWindow()
    qd_mega_tools_window.show()


# Auto-launch
if __name__ == "__main__":
    show_ui()
