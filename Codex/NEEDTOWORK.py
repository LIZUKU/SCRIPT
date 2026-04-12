# -*- coding: utf-8 -*-
"""Smart Paneling v2 - Maya Tool Suite."""

import maya.cmds as cmds
import maya.mel  as mel
import maya.api.OpenMaya as om2
import maya.OpenMayaUI as omui
import math, re

try:
    from PySide2 import QtWidgets, QtCore, QtGui
    from shiboken2 import wrapInstance
except ImportError:
    from PySide6 import QtWidgets, QtCore, QtGui
    from shiboken6 import wrapInstance


#  DESIGN TOKENS
C_BG        = "#252525"       
C_PANEL     = "#2c2c2c"
C_PANEL2    = "#303030"
C_BORDER    = "#3a3a3a"
C_RED       = "#e84d4d"
C_RED_DIM   = "#5a2a2a"
C_RED_DARK  = "#3d1818"
C_TEXT      = "#d0d0d0"       
C_TEXT_DIM  = "#707070"
C_GREEN     = "#4ecb71"
C_ORANGE    = "#e8964d"

GLOBAL_STYLE = f"""
QDialog, QWidget {{
    background: {C_BG};
    color: {C_TEXT};
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 12px;
}}
QTabWidget::pane {{
    border: 1px solid {C_BORDER};
    background: {C_PANEL};
    border-radius: 4px;
}}
QTabBar::tab {{
    background: {C_BG};
    color: {C_TEXT_DIM};
    border: 1px solid {C_BORDER};
    border-bottom: none;
    padding: 6px 18px;
    margin-right: 2px;
    font-weight: bold;
    font-size: 11px;
    letter-spacing: 1px;
}}
QTabBar::tab:selected {{
    background: {C_PANEL};
    color: {C_RED};
    border-top: 2px solid {C_RED};
}}
QTabBar::tab:hover:!selected {{
    background: {C_PANEL2};
    color: {C_TEXT};
}}
QPushButton {{
    background: {C_PANEL2};
    color: {C_TEXT};
    border: 1px solid {C_BORDER};
    border-radius: 3px;
    padding: 5px 10px;
    font-family: 'Consolas', monospace;
    font-size: 11px;
}}
QPushButton:hover {{
    background: #3a3a3a;
    border-color: #585858;
    color: #ffffff;
}}
QPushButton:pressed {{ background: {C_BG}; }}
QPushButton#redBtn {{
    background: {C_RED_DIM};
    color: {C_RED};
    border: 1px solid {C_RED};
    font-weight: bold;
    font-size: 12px;
}}
QPushButton#redBtn:hover {{ background: #6e2a2a; color: #ff7070; }}
QPushButton#greenBtn {{
    background: #1e3a28;
    color: {C_GREEN};
    border: 1px solid {C_GREEN};
    font-weight: bold;
}}
QPushButton#greenBtn:hover {{ background: #254a30; }}
QPushButton#orangeBtn {{
    background: #3a2a1e;
    color: {C_ORANGE};
    border: 1px solid {C_ORANGE};
    font-weight: bold;
}}
QPushButton#orangeBtn:hover {{ background: #4a3020; }}
QPushButton#dimBtn {{
    background: {C_BG};
    color: {C_TEXT_DIM};
    border: 1px solid #2e2e2e;
    font-size: 11px;
}}
QPushButton#dimBtn:hover {{
    background: {C_PANEL2};
    color: {C_TEXT};
    border-color: {C_BORDER};
}}
QPushButton#stepBtn {{
    background: {C_PANEL2};
    color: {C_RED};
    border: 1px solid {C_BORDER};
    font-size: 13px;
    font-weight: bold;
    min-width: 26px;
    max-width: 26px;
    min-height: 22px;
    max-height: 22px;
    padding: 0;
    border-radius: 3px;
}}
QPushButton#stepBtn:hover {{ background: {C_RED_DIM}; border-color:{C_RED}; }}
QLabel#sectionLabel {{
    color: {C_TEXT_DIM};
    font-size: 10px;
    font-weight: bold;
    letter-spacing: 2px;
    padding: 5px 0 2px 0;
}}
QLabel#statusLabel {{
    color: {C_RED};
    font-size: 10px;
    font-weight: bold;
    letter-spacing: 1px;
    padding: 2px 8px;
    border: 1px solid {C_RED_DIM};
    border-radius: 2px;
    background: {C_RED_DARK};
}}
QLabel#statusOk {{
    color: {C_GREEN};
    font-size: 10px;
    font-weight: bold;
    letter-spacing: 1px;
    padding: 2px 8px;
    border: 1px solid #1e3a28;
    border-radius: 2px;
    background: #0e1e14;
}}
QDoubleSpinBox, QSpinBox {{
    background: #1e1e1e;
    color: {C_TEXT};
    border: 1px solid {C_BORDER};
    border-radius: 3px;
    padding: 3px 6px;
    font-size: 12px;
}}
QDoubleSpinBox:focus, QSpinBox:focus {{ border-color: {C_RED}; }}
QSlider::groove:horizontal {{
    height: 3px; background: #111; border-radius: 1px;
}}
QSlider::sub-page:horizontal {{
    background: {C_RED_DIM}; border-radius: 1px;
}}
QSlider::handle:horizontal {{
    background: {C_RED}; width: 13px; height: 13px;
    margin: -5px 0; border-radius: 6px;
}}
QSlider::handle:horizontal:hover {{ background: #ff7070; }}
QCheckBox {{ color: {C_TEXT}; spacing: 6px; font-size: 12px; }}
QCheckBox::indicator {{
    width: 13px; height: 13px;
    border: 1px solid {C_BORDER}; border-radius: 2px; background: #1a1a1a;
}}
QCheckBox::indicator:checked {{
    background: {C_RED_DIM}; border-color: {C_RED};
}}
QComboBox {{
    background: #1e1e1e; color: {C_TEXT};
    border: 1px solid {C_BORDER}; border-radius: 3px;
    padding: 3px 8px; font-size: 12px;
}}
QComboBox:focus {{ border-color: {C_RED}; }}
QComboBox::drop-down {{ border: none; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{
    background: {C_BG}; width: 8px; border-radius: 4px;
}}
QScrollBar::handle:vertical {{
    background: #484848; border-radius: 4px; min-height: 20px;
}}
QScrollBar::handle:vertical:hover {{ background: #666; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""


def _maya_main_window():
    ptr = omui.MQtUtil.mainWindow()
    return wrapInstance(int(ptr), QtWidgets.QWidget) if ptr else None

def _section(text):
    lbl = QtWidgets.QLabel(text.upper())
    lbl.setObjectName("sectionLabel")
    return lbl

def _sep():
    line = QtWidgets.QFrame()
    line.setFrameShape(QtWidgets.QFrame.HLine)
    line.setStyleSheet(f"color: {C_BORDER}; margin: 3px 0;")
    return line

def _icon_pixmap(itype, size=20):
    pix = QtGui.QPixmap(size, size)
    pix.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pix)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    pen = QtGui.QPen(QtGui.QColor(C_RED)); pen.setWidth(2)
    pen.setCapStyle(QtCore.Qt.RoundCap); pen.setJoinStyle(QtCore.Qt.RoundJoin)
    p.setPen(pen); p.setBrush(QtCore.Qt.NoBrush)
    s = size
    if itype == "play":
        poly = QtGui.QPolygon([QtCore.QPoint(int(s*.35),int(s*.22)),
                               QtCore.QPoint(int(s*.35),int(s*.78)),
                               QtCore.QPoint(int(s*.78),int(s*.50))])
        p.setBrush(QtGui.QColor(C_RED)); p.setPen(QtCore.Qt.NoPen); p.drawPolygon(poly)
    elif itype == "stop":
        p.setBrush(QtGui.QColor(C_RED)); p.setPen(QtCore.Qt.NoPen)
        p.drawRect(int(s*.28),int(s*.28),int(s*.44),int(s*.44))
    elif itype == "reset":
        p.setPen(pen)
        p.drawArc(int(s*.18),int(s*.18),int(s*.64),int(s*.64), 45*16, 270*16)
        p.drawLine(int(s*.52),int(s*.12),int(s*.76),int(s*.22))
        p.drawLine(int(s*.76),int(s*.22),int(s*.60),int(s*.40))
    elif itype == "close":
        p.setPen(pen)
        p.drawLine(int(s*.22),int(s*.22),int(s*.78),int(s*.78))
        p.drawLine(int(s*.78),int(s*.22),int(s*.22),int(s*.78))
    elif itype == "drag":
        p.setPen(pen)
        p.drawLine(int(s*.12),int(s*.5),int(s*.88),int(s*.5))
        p.drawLine(int(s*.22),int(s*.34),int(s*.12),int(s*.5)); p.drawLine(int(s*.22),int(s*.66),int(s*.12),int(s*.5))
        p.drawLine(int(s*.78),int(s*.34),int(s*.88),int(s*.5)); p.drawLine(int(s*.78),int(s*.66),int(s*.88),int(s*.5))
        p.setBrush(QtGui.QColor(C_RED)); p.setPen(QtCore.Qt.NoPen)
        p.drawEllipse(QtCore.QPoint(int(s*.5),int(s*.5)),int(s*.1),int(s*.1))
    p.end()
    return pix

def _icon_btn(itype, tooltip="", size=32):
    b = QtWidgets.QPushButton()
    b.setObjectName("dimBtn")
    b.setFixedSize(size, size)
    b.setIcon(QtGui.QIcon(_icon_pixmap(itype, size-10)))
    b.setIconSize(QtCore.QSize(size-10, size-10))
    b.setToolTip(tooltip); return b


def _step_btn(symbol):
    b = QtWidgets.QPushButton(symbol)
    b.setObjectName("stepBtn"); return b


def _slider_row(label, spinbox, slider, step_slow=None, step_fast=None):
    """Return a layout: label + spinbox + optional slow/fast step buttons + slider."""
    col = QtWidgets.QVBoxLayout(); col.setSpacing(3)
    top = QtWidgets.QHBoxLayout(); top.setSpacing(4)
    top.addWidget(QtWidgets.QLabel(label))
    top.addStretch()
    if step_slow is not None:
        bm_slow = _step_btn("-")
        bp_slow = _step_btn("+")
        bm_fast = _step_btn("--")
        bp_fast = _step_btn("++")
        for b in [bm_fast, bm_slow]: top.addWidget(b)
        top.addWidget(spinbox)
        for b in [bp_slow, bp_fast]: top.addWidget(b)

        def _adj(delta):
            v = spinbox.value() + delta
            v = max(spinbox.minimum(), min(spinbox.maximum(), v))
            spinbox.setValue(v)

        bm_slow.clicked.connect(lambda: _adj(-step_slow))
        bp_slow.clicked.connect(lambda: _adj(+step_slow))
        bm_fast.clicked.connect(lambda: _adj(-step_fast))
        bp_fast.clicked.connect(lambda: _adj(+step_fast))
    else:
        top.addWidget(spinbox)
    col.addLayout(top)
    col.addWidget(slider)
    return col



def _pt_apply_bevel_state(bn, state):
    for attr, key, val in [
        (".offsetAsFraction", None, 0),
        (".offset",           "bevel_offset",     None),
        (".segments",         "bevel_segments",   None),
        (".mitering",         "bevel_mitering",   None),
        (".miterAlong",       "bevel_miter_along",None),
        (".chamfer",          "bevel_chamfer",    None),
    ]:
        try:
            v = val if key is None else state[key]
            cmds.setAttr(bn + attr, v)
        except Exception: pass

def _pt_store_bevel_from_node(state):
    bn = state.get("bevel_node")
    if not bn or not cmds.objExists(bn): return
    for attr, key in [(".offset","bevel_offset"),(".segments","bevel_segments"),
                      (".mitering","bevel_mitering"),(".miterAlong","bevel_miter_along"),
                      (".chamfer","bevel_chamfer")]:
        try: state[key] = cmds.getAttr(bn + attr)
        except Exception: pass

def _pt_merge_unique(base, extra):
    seen = set(); out = []
    for item in (base or []) + (extra or []):
        if item not in seen: seen.add(item); out.append(item)
    return out

def _pt_component_set(name, components):
    if cmds.objExists(name):
        try:
            cmds.delete(name)
        except Exception:
            pass
    clean = [c for c in (components or []) if cmds.objExists(c)]
    if clean:
        cmds.sets(clean, name=name)

def _pt_face_normal(face):
    try:
        info = cmds.polyInfo(face, faceNormals=True) or []
        if not info: return None
        nums = re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", info[0])
        if len(nums) < 3: return None
        return tuple(map(float, nums[-3:]))
    except Exception: return None

def _pt_angle_between(n1, n2):
    def norm(v):
        l = math.sqrt(sum(x*x for x in v))
        return tuple(x/l for x in v) if l else (0,0,0)
    a, b = norm(n1), norm(n2)
    dot = max(-1.0, min(1.0, sum(a[i]*b[i] for i in range(3))))
    return math.degrees(math.acos(dot))


def _pt_safe_set_members(set_name):
    """Return members of a Maya set without raising if the set is missing/corrupted."""
    if not set_name or not cmds.objExists(set_name):
        return []
    try:
        return cmds.ls(cmds.sets(set_name, q=True), fl=True) or []
    except Exception:
        return []


def _pt_restore_mesh_selection(obj):
    """Restore transform selection to keep tool UX stable."""
    if obj and cmds.objExists(obj):
        try:
            cmds.select(obj, r=True)
        except Exception:
            pass



def _pt_get_internal_uv_seam_edges(obj):
    selection_list = om2.MSelectionList()
    selection_list.add(obj)
    dag_path = selection_list.getDagPath(0)

    mesh = om2.MFnMesh(dag_path)
    seam_edges = []

    edge_it = om2.MItMeshEdge(dag_path)

    while not edge_it.isDone():
        edge_index = edge_it.index()
        connected_faces = edge_it.getConnectedFaces()

        if len(connected_faces) != 2:
            edge_it.next()
            continue

        v1 = edge_it.vertexId(0)
        v2 = edge_it.vertexId(1)

        uv_pairs = []

        for face_id in connected_faces:
            face_vertices = mesh.getPolygonVertices(face_id)

            local_indices = []
            for i, vid in enumerate(face_vertices):
                if vid == v1 or vid == v2:
                    local_indices.append(i)

            if len(local_indices) != 2:
                continue

            try:
                uv_a = mesh.getPolygonUVid(face_id, local_indices[0])
                uv_b = mesh.getPolygonUVid(face_id, local_indices[1])
            except Exception:
                continue

            uv_pairs.append(tuple(sorted((uv_a, uv_b))))

        if len(uv_pairs) == 2 and uv_pairs[0] != uv_pairs[1]:
            seam_edges.append("{}.e[{}]".format(obj, edge_index))

        edge_it.next()

    return seam_edges


def _pt_extract_corner_vertices_from_edges(edges):
    """Guess corner/end vertices from an edge selection."""
    edge_list = [e for e in (edges or []) if ".e[" in e and cmds.objExists(e)]
    if not edge_list:
        return []

    counts = {}
    for edge in edge_list:
        verts = cmds.ls(
            cmds.polyListComponentConversion(edge, fromEdge=True, toVertex=True),
            fl=True
        ) or []
        for v in verts:
            counts[v] = counts.get(v, 0) + 1

    corners = [v for v, c in counts.items() if c <= 2 and cmds.objExists(v)]
    if not corners:
        corners = [v for v, c in counts.items() if c == 1 and cmds.objExists(v)]
    return corners





def _pt_get_connected_shells(faces):
    visited = set(); shells = []
    for f in faces:
        if f in visited: continue
        stack = [f]; shell = []
        while stack:
            cur = stack.pop()
            if cur in visited: continue
            visited.add(cur); shell.append(cur)
            for n in (cmds.ls(cmds.polyListComponentConversion(cur, ff=True, tf=True), fl=True) or []):
                if n not in visited: stack.append(n)
        shells.append(shell)
    return shells

def _pt_center(faces):
    verts = cmds.ls(cmds.polyListComponentConversion(faces, tv=True), fl=True) or []
    if not verts: return [0.0, 0.0, 0.0]
    pos = [cmds.xform(v, q=True, ws=True, t=True) for v in verts]
    return [sum(x)/len(x) for x in zip(*pos)]

def _pt_pair_shells_by_center(front, back):
    pairs = []; remaining = list(back)
    for f in front:
        if not remaining: break
        fc = _pt_center(f)
        best = min(remaining, key=lambda b: sum((_pt_center(b)[i]-fc[i])**2 for i in range(3)))
        pairs.append((f, best)); remaining.remove(best)
    return pairs

def _pt_delete_side_faces(short_name):
    front = cmds.ls(cmds.sets(short_name + "_panelFrontFaces_set", q=True), fl=True) or []
    back  = cmds.ls(cmds.sets(short_name + "_panelBackFaces_set",  q=True), fl=True) or []
    if not front or not back: return
    cmds.select(front + back, r=True)
    mel.eval("InvertSelection;")
    side = cmds.ls(sl=True, fl=True) or []
    if side: cmds.delete(side)

def _pt_detach(short_name):
    edges = cmds.ls(cmds.sets(short_name + "_panelSeams_set", q=True), fl=True) or []
    if not edges: return
    cmds.select(edges, r=True)
    mel.eval("performDetachComponents;")


def _pt_get_border_edges_of_shell(faces):
    """Return robust border edges for a shell of faces."""
    face_set = set(faces)
    all_edges = cmds.ls(
        cmds.polyListComponentConversion(faces, fromFace=True, toEdge=True),
        fl=True) or []

    border = []
    for e in all_edges:
        adj_faces = cmds.ls(
            cmds.polyListComponentConversion(e, fromEdge=True, toFace=True),
            fl=True) or []
        inside = sum(1 for f in adj_faces if f in face_set)
        if inside == 1:
            border.append(e)
    return border

def _pt_filter_open_border_edges(edges):
    """Keep only mesh border edges (adjacent to one face in the full mesh)."""
    out = []
    for e in (edges or []):
        if not cmds.objExists(e):
            continue
        adj_faces = cmds.ls(
            cmds.polyListComponentConversion(e, fromEdge=True, toFace=True),
            fl=True
        ) or []
        adj_faces = [f for f in adj_faces if ".f[" in f and cmds.objExists(f)]
        if len(adj_faces) == 1:
            out.append(e)
    return out

def _pt_split_edges_into_loops(edges):
    """Split edges into connected loops."""
    remaining = set(edges or [])
    loops = []
    while remaining:
        seed = next(iter(remaining))
        stack = [seed]
        loop = []
        while stack:
            cur = stack.pop()
            if cur not in remaining:
                continue
            remaining.remove(cur)
            loop.append(cur)
            linked = cmds.ls(
                cmds.polyListComponentConversion(cur, fromEdge=True, toEdge=True),
                fl=True
            ) or []
            for e in linked:
                if e in remaining:
                    stack.append(e)
        if loop:
            loops.append(loop)
    return loops

def _pt_center_from_edges(edges):
    verts = cmds.ls(
        cmds.polyListComponentConversion(edges or [], fromEdge=True, toVertex=True),
        fl=True
    ) or []
    if not verts:
        return [0.0, 0.0, 0.0]
    pts = [cmds.xform(v, q=True, ws=True, t=True) for v in verts]
    return [sum(c)/len(c) for c in zip(*pts)]

def _pt_pair_edge_loops_by_center(front_loops, back_loops):
    pairs = []
    remaining = list(back_loops)
    for fl in front_loops:
        if not remaining:
            break
        fc = _pt_center_from_edges(fl)
        best = min(
            remaining,
            key=lambda bl: sum((_pt_center_from_edges(bl)[i] - fc[i]) ** 2 for i in range(3))
        )
        pairs.append((fl, best))
        remaining.remove(best)
    return pairs

def _pt_get_perimeter_edges_from_faces(faces):
    faces = [f for f in (faces or []) if ".f[" in f and cmds.objExists(f)]
    if not faces:
        return []

    cmds.select(faces, r=True)

    try:
        mel.eval("ConvertSelectionToEdgePerimeter;")
    except Exception:
        return []

    edges = cmds.ls(sl=True, fl=True) or []
    edges = [e for e in edges if ".e[" in e and cmds.objExists(e)]
    return edges

def _pt_get_bridge_source_direction(is_negative):
    return 1 if is_negative else 0

def _pt_debug(state, message, level="INFO"):
    line = "[PANEL][{}] {}".format(level, message)
    print(line)
    if isinstance(state, dict):
        state.setdefault("debug_log", []).append(line)


def _pt_validation_box(title, message, icon="information"):
    print("[PANEL][{}] {}".format(title, message))


def _pt_print_summary(state, context="RUN"):
    obj = state.get("obj")
    short_name = state.get("short_name")
    bridge_nodes = state.get("bridge_nodes", []) or []
    bevel_node = state.get("bevel_node")
    debug_lines = state.get("debug_log", []) or []

    print("\n========== PANEL TOOL SUMMARY ({}) ==========".format(context))
    print("Object          : {}".format(obj))
    print("Short name      : {}".format(short_name))
    print("Started         : {}".format(state.get("started")))
    print("Is negative     : {}".format(state.get("is_negative")))
    print("Bridge nodes    : {} -> {}".format(len(bridge_nodes), bridge_nodes))
    print("Bevel node      : {}".format(bevel_node))
    print("Debug entries   : {}".format(len(debug_lines)))
    if debug_lines:
        print("---- Last debug lines ----")
        for line in debug_lines[-8:]:
            print(line)
    print("============================================\n")







def _pt_bridge(short_name, state):
    front = cmds.ls(cmds.sets(short_name + "_panelFrontFaces_set", q=True), fl=True) or []
    back  = cmds.ls(cmds.sets(short_name + "_panelBackFaces_set",  q=True), fl=True) or []

    if not front or not back:
        cmds.warning("Front/back face sets not found.")
        _pt_debug(state, "Front/back face sets missing before bridge.", "ERROR")
        _pt_validation_box("Bridge Error", "Front/back face sets not found.", icon="warning")
        return

    front_shells = _pt_get_connected_shells(front)
    back_shells  = _pt_get_connected_shells(back)
    pairs = _pt_pair_shells_by_center(front_shells, back_shells)
    _pt_debug(
        state,
        "Bridge shells: front={} back={} pairs={}".format(
            len(front_shells), len(back_shells), len(pairs)
        )
    )

    created = []

    for i, (f_shell, b_shell) in enumerate(pairs, start=1):
        f_border = _pt_get_border_edges_of_shell(f_shell)
        b_border = _pt_get_border_edges_of_shell(b_shell)

        f_border = [e for e in f_border if cmds.objExists(e)]
        b_border = [e for e in b_border if cmds.objExists(e)]
        f_border = _pt_filter_open_border_edges(f_border)
        b_border = _pt_filter_open_border_edges(b_border)
        _pt_debug(
            state,
            "Pair {} -> front edges: {} | back edges: {}".format(
                i, len(f_border), len(b_border)
            )
        )

        if not f_border or not b_border:
            cmds.warning("Skipping shell: invalid perimeter.")
            _pt_validation_box(
                "Bridge Skip",
                "Pair {} skipped: invalid perimeter (front={}, back={}).".format(
                    i, len(f_border), len(b_border)
                ),
                icon="warning"
            )
            continue

        f_loops = _pt_split_edges_into_loops(f_border)
        b_loops = _pt_split_edges_into_loops(b_border)
        loop_pairs = _pt_pair_edge_loops_by_center(f_loops, b_loops)
        _pt_debug(
            state,
            "Pair {} -> loops front: {} | back: {} | paired: {}".format(
                i, len(f_loops), len(b_loops), len(loop_pairs)
            )
        )

        for j, (f_loop, b_loop) in enumerate(loop_pairs, start=1):
            if len(f_loop) != len(b_loop):
                cmds.warning(
                    "Bridge skip pair {} loop {} : {} edges front vs {} edges back".format(
                        i, j, len(f_loop), len(b_loop)
                    )
                )
                _pt_debug(
                    state,
                    "Pair {} loop {} skipped: {} vs {} edges.".format(
                        i, j, len(f_loop), len(b_loop)
                    ),
                    "WARNING"
                )
                continue

            try:
                cmds.select(f_loop + b_loop, r=True)

                bridge = cmds.polyBridgeEdge(
                    f_loop + b_loop,
                    divisions=0,
                    twist=0,
                    taper=1,
                    curveType=0
                )[0]

                cmds.setAttr(
                    bridge + ".sourceDirection",
                    _pt_get_bridge_source_direction(state["is_negative"])
                )
                created.append(bridge)
                _pt_debug(
                    state,
                    "Pair {} loop {} bridged (node: {}).".format(i, j, bridge),
                    "OK"
                )

            except Exception as e:
                cmds.warning("polyBridgeEdge failed: {}".format(e))
                _pt_validation_box(
                    "Bridge Failed",
                    "Pair {} loop {}: polyBridgeEdge failed.\n{}".format(i, j, e),
                    icon="critical"
                )
                _pt_debug(state, "Pair {} loop {} bridge failed: {}".format(i, j, e), "ERROR")

    state["bridge_nodes"] = created
    cmds.select(clear=True)
    _pt_debug(state, "Bridge complete. {} node(s) created.".format(len(created)))


def _pt_edge_index(edge):
    match = re.search(r"\.e\[(\d+)\]$", edge or "")
    return int(match.group(1)) if match else None


def _pt_expand_to_edge_loops(obj, edges):
    """Expand edge candidates to their full edge loops."""
    expanded = set(e for e in (edges or []) if cmds.objExists(e))
    for edge in list(expanded):
        edge_idx = _pt_edge_index(edge)
        if edge_idx is None:
            continue
        try:
            loop_indices = cmds.polySelect(obj, edgeLoop=edge_idx, ns=True) or []
        except Exception:
            loop_indices = []
        for idx in loop_indices:
            loop_edge = "{}.e[{}]".format(obj, idx)
            if cmds.objExists(loop_edge):
                expanded.add(loop_edge)
    return list(expanded)


def _pt_get_side_hard_edges_by_angle(obj, short_name, threshold):
    """Return side-related hard edges and expand them to complete loops."""
    all_faces  = cmds.ls(obj + ".f[*]", fl=True) or []
    front_raw  = _pt_safe_set_members(short_name + "_panelFrontFaces_set")
    back_raw   = _pt_safe_set_members(short_name + "_panelBackFaces_set")
    front_set  = set(cmds.ls(front_raw, fl=True) or [])
    back_set   = set(cmds.ls(back_raw,  fl=True) or [])
    exclude    = front_set | back_set
    side_faces = [f for f in all_faces if f not in exclude and cmds.objExists(f)]
    if not side_faces: return []

    side_edges = cmds.ls(
        cmds.polyListComponentConversion(side_faces, fromFace=True, toEdge=True),
        fl=True) or []
    side_edges = [e for e in side_edges if ".e[" in e and cmds.objExists(e)]

    result = []
    for edge in side_edges:
        connected = cmds.ls(
            cmds.polyListComponentConversion(edge, fromEdge=True, toFace=True),
            fl=True) or []
        connected = [f for f in connected if ".f[" in f and cmds.objExists(f)]
        if len(connected) != 2: continue
        n1 = _pt_face_normal(connected[0])
        n2 = _pt_face_normal(connected[1])
        if n1 is None or n2 is None: continue
        if _pt_angle_between(n1, n2) >= threshold:
            result.append(edge)

    corner_set = short_name + "_panelCornerVerts_set"
    corner_verts = _pt_safe_set_members(corner_set)
    for vtx in corner_verts:
        linked = cmds.ls(
            cmds.polyListComponentConversion(vtx, fromVertex=True, toEdge=True),
            fl=True
        ) or []
        for edge in linked:
            if edge in side_edges and cmds.objExists(edge):
                result.append(edge)

    return _pt_expand_to_edge_loops(obj, result)


def _pt_bevel(short_name, state):
    """Bevel front/back perimeter edges with optional extra side hard edges."""
    src_set = (short_name + "_panelBackFaces_set"
               if state["is_negative"] else short_name + "_panelFrontFaces_set")
    faces = [f for f in _pt_safe_set_members(src_set)
             if ".f[" in f and cmds.objExists(f)]
    if not faces:
        cmds.warning("No faces available for bevel.")
        _pt_restore_mesh_selection(state.get("obj"))
        return

    base_faces = list(faces)
    cmds.select(base_faces, r=True)
    try:
        mel.eval("ShrinkPolygonSelectionRegion;")
        bevel_faces = [f for f in (cmds.ls(sl=True, fl=True) or [])
                       if ".f[" in f and cmds.objExists(f)]
    except Exception:
        bevel_faces = []
    if not bevel_faces: bevel_faces = base_faces

    bevel_set = short_name + "_panelBevelFaces_set"
    if cmds.objExists(bevel_set): cmds.delete(bevel_set)
    cmds.sets(bevel_faces, name=bevel_set)

    bevel_edges = _pt_get_border_edges_of_shell(bevel_faces)
    bevel_edges = [e for e in bevel_edges if cmds.objExists(e)]

    if state.get("angle_bevel_enabled", False):
        extra = _pt_get_side_hard_edges_by_angle(
            state["obj"], short_name, state.get("angle_bevel_threshold", 90.0))
        bevel_edges = _pt_merge_unique(bevel_edges, extra)

    if not bevel_edges:
        cmds.warning("No edges available for bevel.")
        _pt_restore_mesh_selection(state.get("obj"))
        return

    try:
        if state.get("bevel_node") and cmds.objExists(state["bevel_node"]):
            cmds.delete(state["bevel_node"]); state["bevel_node"] = None
        cmds.select(bevel_edges, r=True)
        bevel_node = cmds.polyBevel3(bevel_edges)[0]
        state["bevel_node"] = bevel_node
        _pt_apply_bevel_state(bevel_node, state)
    except Exception as e:
        cmds.warning(f"Bevel failed: {e}")
        _pt_restore_mesh_selection(state.get("obj"))
        return

    _pt_restore_mesh_selection(state.get("obj"))


def _pt_rebuild_bevel(short_name, state):
    _pt_store_bevel_from_node(state)
    if state.get("bevel_node") and cmds.objExists(state["bevel_node"]):
        try: cmds.delete(state["bevel_node"])
        except Exception: pass
        state["bevel_node"] = None
    _pt_bevel(short_name, state)


def _pt_bridge_direction_update(state):
    direction = _pt_get_bridge_source_direction(state["is_negative"])
    for bn in state["bridge_nodes"]:
        if cmds.objExists(bn):
            try: cmds.setAttr(bn + ".sourceDirection", direction)
            except Exception: pass


def _pt_validate(state):
    if not state["started"]: return
    obj = state.get("obj"); sn = state.get("short_name"); backup = state.get("backup")
    if not obj or not cmds.objExists(obj): return
    for node in [sn+"_panelEdges_set", sn+"_panelFrontFaces_set",
                 sn+"_panelBackFaces_set", sn+"_panelSeams_set",
                 sn+"_panelBevelFaces_set", sn+"_panelCornerVerts_set"]:
        if cmds.objExists(node):
            try: cmds.delete(node)
            except Exception: pass
    if backup and cmds.objExists(backup):
        try: cmds.delete(backup)
        except Exception: pass
    try: cmds.delete(obj, ch=True)
    except Exception: pass
    state.update({"backup": None, "bridge_nodes": [], "bevel_node": None})
    cmds.select(obj, r=True)
    _pt_print_summary(state, context="VALIDATE")
    _pt_validation_box("Validation", "Panel tool validated.\nSummary printed in the Script Editor.")
    print("Panel tool validated.")

def _pt_revert(state):
    backup = state.get("backup"); obj = state.get("obj"); sn = state.get("short_name")
    if not backup or not cmds.objExists(backup): cmds.warning("Backup not found."); return
    if obj and cmds.objExists(obj):
        try: cmds.delete(obj)
        except Exception: return
    restored = cmds.rename(cmds.duplicate(backup, rr=True)[0], sn)
    try: cmds.showHidden(restored)
    except Exception: pass
    try: cmds.delete(backup)
    except Exception: pass
    for node in [sn+"_panelEdges_set", sn+"_panelFrontFaces_set",
                 sn+"_panelBackFaces_set", sn+"_panelSeams_set",
                 sn+"_panelBevelFaces_set", sn+"_panelCornerVerts_set"]:
        if cmds.objExists(node):
            try: cmds.delete(node)
            except Exception: pass
    state.update({
        "started": False, "obj": restored, "short_name": restored.split("|")[-1],
        "backup": None, "extrude_node": None, "bridge_nodes": [], "bevel_node": None,
        "safe_selection": [restored]
    })
    cmds.select(restored, r=True)
    print("Revert complete.")

def _pt_delete_opposite_faces(state):
    if not state["started"]: return
    sn = state["short_name"]
    set_name = (sn+"_panelFrontFaces_set" if state["is_negative"] else sn+"_panelBackFaces_set")
    faces = [f for f in _pt_safe_set_members(set_name)
             if ".f[" in f and cmds.objExists(f)]
    if not faces: return
    cmds.select(faces, r=True)
    for _ in range(max(0, int(state.get("bevel_segments", 1)))):
        try: mel.eval("ShrinkPolygonSelectionRegion;")
        except Exception: break
    final = [f for f in (cmds.ls(sl=True, fl=True) or []) if ".f[" in f and cmds.objExists(f)]
    if final: cmds.delete(final)

def _pt_grow_delete_opposite_faces(state):
    if not state["started"]: return
    sn = state["short_name"]
    set_name = (sn+"_panelFrontFaces_set" if state["is_negative"] else sn+"_panelBackFaces_set")
    faces = [f for f in _pt_safe_set_members(set_name)
             if ".f[" in f and cmds.objExists(f)]
    if not faces: return
    cmds.select(faces, r=True)
    for _ in range(max(0, int(state.get("bevel_segments", 1)))):
        try: mel.eval("ShrinkPolygonSelectionRegion;")
        except Exception: break
    try: mel.eval("GrowPolygonSelectionRegion;")
    except Exception: return
    final = [f for f in (cmds.ls(sl=True, fl=True) or []) if ".f[" in f and cmds.objExists(f)]
    if final: cmds.delete(final)


_UNIT_MAP = {
    "mm": om2.MDistance.kMillimeters, "cm": om2.MDistance.kCentimeters,
    "m":  om2.MDistance.kMeters,      "in": om2.MDistance.kInches,
    "ft": om2.MDistance.kFeet,        "yd": om2.MDistance.kYards,
}
def _om_scene_to_cm(val):
    unit = cmds.currentUnit(q=True, linear=True)
    return om2.MDistance(float(val), _UNIT_MAP.get(unit, om2.MDistance.kCentimeters)).asCentimeters()
def _om_scene_unit(): return cmds.currentUnit(q=True, linear=True)

class _UnionFind:
    __slots__ = ("p","r")
    def __init__(self,n): self.p=list(range(n)); self.r=[0]*n
    def find(self,x):
        while self.p[x]!=x: self.p[x]=self.p[self.p[x]]; x=self.p[x]
        return x
    def union(self,a,b):
        ra,rb=self.find(a),self.find(b)
        if ra==rb: return
        if self.r[ra]<self.r[rb]: self.p[ra]=rb
        elif self.r[ra]>self.r[rb]: self.p[rb]=ra
        else: self.p[rb]=ra; self.r[ra]+=1

def _om_dagpath(name):
    sel=om2.MSelectionList(); sel.add(name); return sel.getDagPath(0)

def _om_mesh_shape(obj):
    if not obj or not cmds.objExists(obj): return None
    nt=cmds.nodeType(obj)
    if nt=="mesh": return obj
    if nt=="transform":
        for s in (cmds.listRelatives(obj,shapes=True,fullPath=True) or []):
            if cmds.nodeType(s)=="mesh": return s
    return None

def _om_selected_vertices_by_mesh():
    sel=cmds.ls(sl=True,fl=True,l=True) or []
    if not sel: return {}
    vtx=cmds.filterExpand(sel,sm=31) or []
    if vtx: vtx=cmds.ls(vtx,fl=True,l=True) or []
    else:
        vtx=[]
        for t in [s for s in sel if cmds.nodeType(s)=="transform"]:
            mesh=_om_mesh_shape(t)
            if not mesh: continue
            n=cmds.polyEvaluate(mesh,vertex=True)
            vtx.extend([f"{t}.vtx[{i}]" for i in range(n)])
        vtx=cmds.ls(vtx,fl=True,l=True) or []
    out={}
    for comp in vtx:
        if ".vtx[" not in comp: continue
        owner=comp.split(".",1)[0]; mesh=_om_mesh_shape(owner)
        if not mesh: continue
        i0=comp.rfind("["); i1=comp.rfind("]")
        out.setdefault(mesh,set()).add(int(comp[i0+1:i1]))
    return {k:sorted(list(v)) for k,v in out.items()}

def _om_overlay_merge_no_weld(mesh_to_vids, thr_scene):
    thr_cm=_om_scene_to_cm(thr_scene)
    if thr_cm<=0: return
    thr2=thr_cm*thr_cm
    for mesh,vids in mesh_to_vids.items():
        if not vids: continue
        dag=_om_dagpath(mesh); fn=om2.MFnMesh(dag)
        all_pts=fn.getPoints(om2.MSpace.kWorld)
        pts=[all_pts[i] for i in vids]; n=len(pts)
        if n<2: continue
        cell=thr_cm; inv=1.0/cell
        def key(p): return (int(p.x*inv),int(p.y*inv),int(p.z*inv))
        bkts={}
        for i,p in enumerate(pts): bkts.setdefault(key(p),[]).append(i)
        uf=_UnionFind(n)
        for (cx,cy,cz),lst in bkts.items():
            for dx in(-1,0,1):
                for dy in(-1,0,1):
                    for dz in(-1,0,1):
                        nb=(cx+dx,cy+dy,cz+dz)
                        if nb not in bkts: continue
                        lst2=bkts[nb]
                        for i in lst:
                            pi=pts[i]
                            for j in lst2:
                                if nb==(cx,cy,cz) and j<=i: continue
                                pj=pts[j]
                                ddx=pi.x-pj.x; ddy=pi.y-pj.y; ddz=pi.z-pj.z
                                if ddx*ddx+ddy*ddy+ddz*ddz<=thr2: uf.union(i,j)
        clusters={}
        for i in range(n): clusters.setdefault(uf.find(i),[]).append(i)
        for members in clusters.values():
            if len(members)<2: continue
            sx=sy=sz=0.0
            for idx in members: p=pts[idx]; sx+=p.x; sy+=p.y; sz+=p.z
            invn=1.0/len(members); np_=om2.MPoint(sx*invn,sy*invn,sz*invn)
            for idx in members: all_pts[vids[idx]]=np_
        fn.setPoints(all_pts,om2.MSpace.kWorld)

_OM_STATE={
    "running":False,"stored":False,"mesh_to_vids":{},"orig_points":{},
    "threshold":0.1,"updating":False,"soft":{"mn":0.0,"mx":1.0},
    "dragger":"qd_overlayMerge_smartPanel_dragCtx",
}
def _om_restore():
    if not _OM_STATE["stored"]: return
    for mesh,vid_map in _OM_STATE["orig_points"].items():
        if not cmds.objExists(mesh): continue
        dag=_om_dagpath(mesh); fn=om2.MFnMesh(dag)
        pts=fn.getPoints(om2.MSpace.kWorld)
        for vid,p in vid_map.items():
            if vid<len(pts): pts[vid]=p
        fn.setPoints(pts,om2.MSpace.kWorld)

def _om_snapshot():
    if _OM_STATE["stored"]: _om_restore()
    cur=_om_selected_vertices_by_mesh()
    if not cur: return False
    _OM_STATE["mesh_to_vids"]=cur; _OM_STATE["orig_points"]={}
    for mesh,vids in cur.items():
        dag=_om_dagpath(mesh); fn=om2.MFnMesh(dag)
        pts=fn.getPoints(om2.MSpace.kWorld)
        _OM_STATE["orig_points"][mesh]={vid:om2.MPoint(pts[vid]) for vid in vids}
    _OM_STATE["stored"]=True; return True

def _om_ensure_snapshot():
    cur=_om_selected_vertices_by_mesh()
    if not cur: return False
    if not _OM_STATE["stored"] or cur!=_OM_STATE["mesh_to_vids"]: return _om_snapshot()
    return True

def _om_preview_update():
    if _OM_STATE["updating"] or not _OM_STATE["running"]: return
    if not _om_ensure_snapshot(): cmds.warning("Select vertices first."); return
    _OM_STATE["updating"]=True
    try:
        prev=cmds.undoInfo(q=True,state=True); cmds.undoInfo(state=False)
        _om_restore(); _om_overlay_merge_no_weld(_OM_STATE["mesh_to_vids"],_OM_STATE["threshold"])
        cmds.undoInfo(state=prev); cmds.refresh(f=True)
    finally: _OM_STATE["updating"]=False

def _om_stop_keep():
    _OM_STATE.update({"running":False,"stored":False,"mesh_to_vids":{},"orig_points":{}})
def _om_reset(): _om_restore(); cmds.refresh(f=True)
def _om_kill_dragger():
    name=_OM_STATE["dragger"]
    if cmds.draggerContext(name,exists=True):
        try: cmds.deleteUI(name)
        except Exception: pass
def _om_close_restore():
    if _OM_STATE["stored"]: _om_restore(); cmds.refresh(f=True)
    _om_kill_dragger()
def _om_expand_soft(v):
    mn,mx=_OM_STATE["soft"]["mn"],_OM_STATE["soft"]["mx"]
    if mx<=mn: mx=mn+1.0
    span=mx-mn
    if v<mn: mn=v; mx=mn+span
    elif v>mx: mx=v; mn=mx-span
    _OM_STATE["soft"]["mn"]=mn; _OM_STATE["soft"]["mx"]=mx

def _om_start_mmb_dragger(get_fn,set_fn,change_fn):
    _om_kill_dragger(); start={"v":float(get_fn())}
    def base_sens():
        mn,mx=_OM_STATE["soft"]["mn"],_OM_STATE["soft"]["mx"]
        return max(abs(mx-mn),1e-12)/800.0
    def on_press(): start["v"]=float(get_fn())
    def on_drag():
        anchor=cmds.draggerContext(_OM_STATE["dragger"],q=True,anchorPoint=True)
        drag=cmds.draggerContext(_OM_STATE["dragger"],q=True,dragPoint=True)
        dx=float(drag[0]-anchor[0])
        mods=cmds.draggerContext(_OM_STATE["dragger"],q=True,modifier=True)
        sens=base_sens()
        if "shift" in mods: sens*=0.2
        if "ctrl"  in mods: sens*=5.0
        nv=start["v"]+dx*sens; _OM_STATE["threshold"]=float(nv)
        set_fn(nv); change_fn()
    cmds.draggerContext(_OM_STATE["dragger"],
        pressCommand=on_press,dragCommand=on_drag,
        releaseCommand=lambda:None,cursor="hand",undoMode="step")
    cmds.setToolTo(_OM_STATE["dragger"])


def _cg_ev(e): return cmds.ls(cmds.polyListComponentConversion(e,fromEdge=True,toVertex=True),flatten=True)
def _cg_pos(v): p=cmds.pointPosition(v,world=True); return om2.MVector(p[0],p[1],p[2])
def _cg_dist(v1,v2): return (_cg_pos(v2)-_cg_pos(v1)).length()
def _cg_dist_vec(p1,p2):
    d = p2 - p1
    return d.length()
def _cg_positions(vertices):
    out = {}
    for v in (vertices or []):
        try:
            p = cmds.pointPosition(v, world=True)
            out[v] = om2.MVector(p[0], p[1], p[2])
        except Exception:
            continue
    return out
def _cg_build_pairs(v1, v2, max_dist, pos):
    pairs = []
    for a in v1:
        pa = pos.get(a)
        if pa is None:
            continue
        best = None
        best_d = None
        for b in v2:
            pb = pos.get(b)
            if pb is None:
                continue
            d = _cg_dist_vec(pa, pb)
            if best_d is None or d < best_d:
                best_d = d
                best = b
        if best is not None and best_d is not None and best_d <= max_dist:
            pairs.append((a, best, best_d))

    matched = {p[1] for p in pairs}
    for b in v2:
        if b in matched:
            continue
        pb = pos.get(b)
        if pb is None:
            continue
        best = None
        best_d = None
        for a in v1:
            pa = pos.get(a)
            if pa is None:
                continue
            d = _cg_dist_vec(pa, pb)
            if best_d is None or d < best_d:
                best_d = d
                best = a
        if best is not None and best_d is not None and best_d <= max_dist:
            pairs.append((best, b, best_d))
    return pairs
def _cg_edges_conn(e1,e2): return bool(set(_cg_ev(e1))&set(_cg_ev(e2)))
def _cg_verts_conn(v1,v2):
    e1=set(cmds.ls(cmds.polyListComponentConversion(v1,fromVertex=True,toEdge=True),flatten=True))
    e2=set(cmds.ls(cmds.polyListComponentConversion(v2,fromVertex=True,toEdge=True),flatten=True))
    return bool(e1&e2)
def _cg_sep_edge_groups(edges):
    if not edges: return []
    rem=set(edges); groups=[]
    while rem:
        grp=[]; chk=[rem.pop()]
        while chk:
            e=chk.pop(); grp.append(e)
            for o in list(rem):
                if _cg_edges_conn(e,o): chk.append(o); rem.discard(o)
        groups.append(grp)
    return groups
def _cg_sep_vert_groups(verts):
    if not verts: return []
    rem=set(verts); groups=[]
    while rem:
        grp=[]; chk=[rem.pop()]
        while chk:
            v=chk.pop(); grp.append(v)
            for o in list(rem):
                if _cg_verts_conn(v,o): chk.append(o); rem.discard(o)
        groups.append(grp)
    return groups
def _cg_as_vertices():
    sel=cmds.ls(selection=True,flatten=True)
    edges=[s for s in sel if '.e[' in s]; verts=[s for s in sel if '.vtx[' in s]
    if edges:
        gs=_cg_sep_edge_groups(edges)
        if len(gs)<2: return None,None
        gs.sort(key=len,reverse=True)
        v1=set(); v2=set()
        for e in gs[0]: v1.update(_cg_ev(e))
        for e in gs[1]: v2.update(_cg_ev(e))
        return list(v1),list(v2)
    elif verts:
        gs=_cg_sep_vert_groups(verts)
        if len(gs)<2: return None,None
        gs.sort(key=len,reverse=True); return gs[0],gs[1]
    return None,None
def _cg_estimate_gap():
    v1,v2=_cg_as_vertices()
    if not v1 or not v2: return 10.0
    pos = _cg_positions(set(v1 + v2))
    md = min(
        (_cg_dist_vec(pos[a], pos[b]) for a in v1 for b in v2 if a in pos and b in pos),
        default=float("inf")
    )
    return 10.0 if(md==float('inf') or md<0.0001) else md*1.5
def _cg_close_gaps(max_dist=None):
    v1,v2=_cg_as_vertices()
    if not v1 or not v2: cmds.warning("Two groups are required."); return 0
    if max_dist is None: max_dist=_cg_estimate_gap()
    pos = _cg_positions(set(v1 + v2))
    pairs = _cg_build_pairs(v1, v2, max_dist, pos)
    if not pairs: cmds.warning(f"No pairs found within distance {max_dist:.3f}"); return 0
    vtgt={}
    for a,b,_ in pairs:
        mid=(pos[a]+pos[b])*0.5
        vtgt.setdefault(a,[]).append(mid); vtgt.setdefault(b,[]).append(mid)
    cmds.undoInfo(openChunk=True)
    try:
        for vtx,tgts in vtgt.items():
            avg=sum(tgts,om2.MVector(0,0,0))/len(tgts)
            cmds.xform(vtx,worldSpace=True,translation=[avg.x,avg.y,avg.z])
        cmds.inViewMessage(amg=f'<span style="color:#66FF66;">{len(vtgt)} vertices moved.</span>',
                           pos='midCenter',fade=True,fadeStayTime=1200)
        return len(vtgt)
    finally: cmds.undoInfo(closeChunk=True)
def _cg_close_dir(max_dist,direction):
    v1,v2=_cg_as_vertices()
    if not v1 or not v2: return 0
    if max_dist is None: max_dist=_cg_estimate_gap()
    pos = _cg_positions(set(v1 + v2))
    pairs = _cg_build_pairs(v1, v2, max_dist, pos)
    if not pairs: return 0
    vtgt={}
    for a,b,_ in pairs:
        tgt=pos[a] if direction=="g1" else pos[b]
        vtgt.setdefault(a,[]).append(tgt); vtgt.setdefault(b,[]).append(tgt)
    cmds.undoInfo(openChunk=True)
    try:
        for vtx,tgts in vtgt.items():
            avg=sum(tgts,om2.MVector(0,0,0))/len(tgts)
            cmds.xform(vtx,worldSpace=True,translation=[avg.x,avg.y,avg.z])
        return len(vtgt)
    finally: cmds.undoInfo(closeChunk=True)


class PanelToolTab(QtWidgets.QWidget):
    MITERING_OPTIONS = [
        ("Auto", 0),
        ("Uniform", 1),
        ("Patch", 2),
        ("Radial", 3),
        ("Proximity", 4),
    ]
    MITER_ALONG_OPTIONS = [
        ("Auto", 0),
        ("Center", 1),
        ("Forward", 2),
        ("Backward", 3),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = {
            "started":False,"obj":None,"short_name":None,"backup":None,
            "extrude_node":None,"is_negative":False,"pending_is_negative":False,
            "internal_restore":False,"safe_selection":[],"bridge_nodes":[],
            "bevel_node":None,
            "bevel_offset":0.01,"bevel_segments":1,"bevel_mitering":0,
            "bevel_miter_along":0,"bevel_chamfer":1,
            "angle_bevel_enabled":False,"angle_bevel_threshold":90.0,
            "ui":{},
        }
        self._live_apply_timer = QtCore.QTimer(self)
        self._live_apply_timer.setSingleShot(True)
        self._live_apply_timer.timeout.connect(self._apply_bevel_live_now)

        self._rebuild_timer = QtCore.QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.timeout.connect(self._rebuild_bevel_now)
        self._build()

    def _sync_bevel_state(self):
        s = self.state
        s["bevel_offset"]      = self.w_boff.value()
        s["bevel_segments"]    = self.w_bseg.value()
        s["bevel_mitering"]    = self.w_mitering.currentData()
        s["bevel_miter_along"] = self.w_miter_along.currentData()
        s["bevel_chamfer"]     = 1 if self.w_chamfer.isChecked() else 0
        s["angle_bevel_enabled"]   = self.w_angle_check.isChecked()
        s["angle_bevel_threshold"] = self.w_angle_val.value()

    def _apply_bevel_live(self):
        self._live_apply_timer.start(30)

    def _apply_bevel_live_now(self):
        if not self.state["started"]: return
        self._sync_bevel_state()
        bn = self.state.get("bevel_node")
        if bn and cmds.objExists(bn):
            _pt_apply_bevel_state(bn, self.state)

    def _rebuild_bevel(self):
        self._rebuild_timer.start(120)

    def _rebuild_bevel_now(self):
        if not self.state["started"]: return
        self._sync_bevel_state()
        _pt_rebuild_bevel(self.state["short_name"], self.state)

    def _enable_controls(self, en):
        for w in [self.w_extr, self.w_extr_sl,
                  self.w_boff,  self.w_boff_sl,
                  self.w_bseg,  self.w_bseg_sl,
                  self.w_mitering, self.w_miter_along, self.w_chamfer,
                  self.w_angle_check, self.w_angle_val, self.w_angle_sl]:
            w.setEnabled(en)

    def _build(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(10,10,10,10); root.setSpacing(5)

        self.btn_start = QtWidgets.QPushButton("START")
        self.btn_start.setObjectName("redBtn"); self.btn_start.setMinimumHeight(34)
        self.btn_start.clicked.connect(self._on_start)
        root.addWidget(self.btn_start)

        self.w_auto_bevel = QtWidgets.QCheckBox("Auto Bevel on Start")
        self.w_auto_bevel.setChecked(True)
        root.addWidget(self.w_auto_bevel)

        root.addWidget(_sep())
        root.addWidget(_section("Extrude"))

        self.w_extr = QtWidgets.QDoubleSpinBox()
        self.w_extr.setRange(-1000,1000); self.w_extr.setValue(1.0)
        self.w_extr.setSingleStep(0.01); self.w_extr.setDecimals(3)
        self.w_extr.setEnabled(False)
        self.w_extr_sl = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.w_extr_sl.setRange(-500,500); self.w_extr_sl.setValue(100)
        self.w_extr_sl.setEnabled(False)
        self.w_extr_sl.sliderMoved.connect(self._on_extr_drag)
        self.w_extr_sl.sliderReleased.connect(self._on_extr_release)
        self.w_extr.valueChanged.connect(self._on_extr_field)
        root.addLayout(_slider_row("Distance", self.w_extr, self.w_extr_sl))

        root.addWidget(_sep())
        root.addWidget(_section("Bevel"))

        self.w_boff = QtWidgets.QDoubleSpinBox()
        self.w_boff.setRange(0,1000); self.w_boff.setValue(0.01)
        self.w_boff.setSingleStep(0.001); self.w_boff.setDecimals(4)
        self.w_boff.setEnabled(False)
        self.w_boff_sl = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.w_boff_sl.setRange(0,50); self.w_boff_sl.setValue(10)
        self.w_boff_sl.setEnabled(False)
        self.w_boff.valueChanged.connect(self._on_boff_field)
        self.w_boff_sl.sliderMoved.connect(self._on_boff_slider)
        self.w_boff_sl.sliderReleased.connect(self._apply_bevel_live)
        root.addLayout(_slider_row("Offset", self.w_boff, self.w_boff_sl,
                                   step_slow=0.001, step_fast=0.01))

        self.w_bseg = QtWidgets.QSpinBox()
        self.w_bseg.setRange(0,100); self.w_bseg.setValue(1)
        self.w_bseg.setEnabled(False)
        self.w_bseg_sl = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.w_bseg_sl.setRange(0,3); self.w_bseg_sl.setValue(1)
        self.w_bseg_sl.setEnabled(False)
        self.w_bseg.valueChanged.connect(self._on_bseg_field)
        self.w_bseg_sl.sliderMoved.connect(self._on_bseg_slider)
        self.w_bseg_sl.sliderReleased.connect(self._apply_bevel_live)
        root.addLayout(_slider_row("Segments", self.w_bseg, self.w_bseg_sl,
                                   step_slow=1, step_fast=2))

        grid = QtWidgets.QGridLayout(); grid.setSpacing(4)

        self.w_mitering = QtWidgets.QComboBox(); self.w_mitering.setEnabled(False)
        for label, value in self.MITERING_OPTIONS:
            self.w_mitering.addItem(label, value)
        self.w_mitering.currentIndexChanged.connect(self._apply_bevel_live)
        grid.addWidget(QtWidgets.QLabel("Mitering"),0,0); grid.addWidget(self.w_mitering,0,1)

        self.w_miter_along = QtWidgets.QComboBox(); self.w_miter_along.setEnabled(False)
        for label, value in self.MITER_ALONG_OPTIONS:
            self.w_miter_along.addItem(label, value)
        self.w_miter_along.currentIndexChanged.connect(self._apply_bevel_live)
        grid.addWidget(QtWidgets.QLabel("Miter Along"),1,0); grid.addWidget(self.w_miter_along,1,1)

        self.w_chamfer = QtWidgets.QCheckBox("Chamfer")
        self.w_chamfer.setChecked(True); self.w_chamfer.setEnabled(False)
        self.w_chamfer.toggled.connect(self._apply_bevel_live)
        grid.addWidget(self.w_chamfer,2,0,1,2)
        root.addLayout(grid)

        root.addWidget(_sep())
        root.addWidget(_section("Side Hard Edges"))

        self.w_angle_check = QtWidgets.QCheckBox("Add Side Hard Edges")
        self.w_angle_check.setChecked(False); self.w_angle_check.setEnabled(False)
        self.w_angle_check.toggled.connect(self._rebuild_bevel)
        root.addWidget(self.w_angle_check)

        self.w_angle_val = QtWidgets.QDoubleSpinBox()
        self.w_angle_val.setRange(0,180); self.w_angle_val.setValue(90.0)
        self.w_angle_val.setSingleStep(1.0); self.w_angle_val.setEnabled(False)
        self.w_angle_sl = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.w_angle_sl.setRange(1,180); self.w_angle_sl.setValue(90)
        self.w_angle_sl.setEnabled(False)
        self.w_angle_val.valueChanged.connect(self._on_angle_field)
        self.w_angle_sl.sliderMoved.connect(self._on_angle_slider)
        self.w_angle_sl.sliderReleased.connect(self._rebuild_bevel)
        root.addLayout(_slider_row("Angle", self.w_angle_val, self.w_angle_sl,
                                   step_slow=1.0, step_fast=5.0))

        presets = QtWidgets.QHBoxLayout()
        for label, angle in [("Preset 90", 90.0), ("Preset 45", 45.0)]:
            b = QtWidgets.QPushButton(label); b.setObjectName("dimBtn")
            b.clicked.connect(lambda _, a=angle: self._set_angle_preset(a))
            presets.addWidget(b)
        root.addLayout(presets)

        root.addWidget(_sep())
        root.addWidget(_section("Operations"))

        btn_bevel = QtWidgets.QPushButton("Bevel"); btn_bevel.setObjectName("dimBtn")
        btn_bevel.clicked.connect(lambda: (self._sync_bevel_state(),
                                           _pt_bevel(self.state["short_name"], self.state)))
        root.addWidget(btn_bevel)

        btn_del = QtWidgets.QPushButton("Delete Opposite Faces"); btn_del.setObjectName("dimBtn")
        btn_del.clicked.connect(lambda: _pt_delete_opposite_faces(self.state))
        root.addWidget(btn_del)

        btn_grow = QtWidgets.QPushButton("Grow + Delete Opposite"); btn_grow.setObjectName("dimBtn")
        btn_grow.clicked.connect(lambda: _pt_grow_delete_opposite_faces(self.state))
        root.addWidget(btn_grow)

        root.addWidget(_sep())
        vrow = QtWidgets.QHBoxLayout()
        bval = QtWidgets.QPushButton("Validate"); bval.setObjectName("greenBtn")
        brev = QtWidgets.QPushButton("Revert");   brev.setObjectName("orangeBtn")
        bval.clicked.connect(lambda: _pt_validate(self.state))
        brev.clicked.connect(self._on_revert)
        vrow.addWidget(bval); vrow.addWidget(brev)
        root.addLayout(vrow)
        root.addStretch()

    def _on_start(self):
        state = self.state
        sel   = cmds.ls(sl=True, fl=True, long=True)
        edges = cmds.filterExpand(sel, sm=32)
        if not edges: cmds.warning("Select edges first."); return

        obj        = edges[0].split(".e[")[0]
        if not cmds.objExists(obj): cmds.warning("Object not found."); return
        short_name = obj.split("|")[-1]

        default_val = self.w_extr.value()
        auto_bevel  = self.w_auto_bevel.isChecked()

        state.update({
            "started":False,"obj":obj,"short_name":short_name,
            "extrude_node":None,
            "is_negative": default_val < 0.0,
            "pending_is_negative": default_val < 0.0,
            "internal_restore":False,"safe_selection":[obj],
            "bridge_nodes":[],"bevel_node":None,"debug_log":[],
        })
        _pt_debug(state, "Starting paneling on object '{}'.".format(short_name))
        _pt_validation_box("Start", "Detected object: {}\nStarting process.".format(short_name))

        cmds.waitCursor(state=True)
        cmds.refresh(suspend=True)
        try:
            backup = cmds.duplicate(obj, name=short_name+"_backup#")[0]
            cmds.hide(backup); state["backup"] = backup
            _pt_debug(state, "Backup created: {}".format(backup), "OK")
            _pt_validation_box("Backup", "Backup created:\n{}".format(backup))

            edge_set = short_name+"_panelEdges_set"
            if cmds.objExists(edge_set): cmds.delete(edge_set)
            cmds.sets(edges, name=edge_set)

            stored = cmds.ls(cmds.sets(edge_set, q=True), fl=True) or []
            corner_verts = _pt_extract_corner_vertices_from_edges(stored)
            _pt_component_set(short_name + "_panelCornerVerts_set", corner_verts)
            _pt_debug(state, "Corner verts stored: {}".format(len(corner_verts)))
            if stored:
                cmds.select(stored, r=True)
                try: cmds.polyMapCut(stored)
                except Exception:
                    try: mel.eval("performPolyMapCut;")
                    except Exception: cmds.warning("UV cut failed."); return
            _pt_debug(state, "UV cut applied to {} edge(s).".format(len(stored)))
            _pt_validation_box("UV Cut", "UV cut completed on {} edge(s).".format(len(stored)))

            cmds.select(obj+".f[*]", r=True)
            extrude_node = cmds.polyExtrudeFacet(localTranslateZ=default_val, keepFacesTogether=True)[0]
            state["extrude_node"] = extrude_node
            _pt_debug(state, "Extrude created: {} (offset={})".format(extrude_node, default_val), "OK")
            _pt_validation_box("Extrude", "Extrude node:\n{}\nOffset: {}".format(extrude_node, default_val))

            if state["is_negative"]:
                cmds.select(obj, r=True); mel.eval("ReversePolygonNormals;"); cmds.select(obj, r=True)

            front_faces = cmds.ls(sl=True, fl=True) or []
            front_set   = short_name+"_panelFrontFaces_set"
            if cmds.objExists(front_set): cmds.delete(front_set)
            if front_faces: cmds.sets(front_faces, name=front_set)
            _pt_debug(state, "Front faces: {}".format(len(front_faces)))
            _pt_validation_box("Front Set", "{} face(s) stored in the front set.".format(len(front_faces)))

            cmds.select(front_faces, r=True)
            mel.eval("GrowPolygonSelectionRegion;"); mel.eval("InvertSelection;")
            back_faces = cmds.ls(sl=True, fl=True) or []
            back_set   = short_name+"_panelBackFaces_set"
            if cmds.objExists(back_set): cmds.delete(back_set)
            if back_faces: cmds.sets(back_faces, name=back_set)
            _pt_debug(state, "Back faces: {}".format(len(back_faces)))
            _pt_validation_box("Back Set", "{} face(s) stored in the back set.".format(len(back_faces)))

            seam_edges = _pt_get_internal_uv_seam_edges(obj)
            seam_set   = short_name+"_panelSeams_set"
            if cmds.objExists(seam_set): cmds.delete(seam_set)
            if seam_edges: cmds.sets(seam_edges, name=seam_set)
            _pt_debug(state, "Seam edges detected: {}".format(len(seam_edges)))
            _pt_validation_box("Seams", "{} seam edge(s) detected.".format(len(seam_edges)))

            cmds.select(obj, r=True); state["safe_selection"] = [obj]

            _pt_delete_side_faces(short_name)
            _pt_debug(state, "Side faces deleted.")
            _pt_detach(short_name)
            _pt_debug(state, "Seam detach complete.")
            _pt_bridge(short_name, state)

            state["started"] = True
            self._enable_controls(True)

            if auto_bevel:
                self._sync_bevel_state()
                _pt_bevel(short_name, state)

            cmds.select(obj, r=True)
            _pt_print_summary(state, context="START")
            _pt_validation_box("Summary", "Process complete.\nSummary printed in the Script Editor.")
        finally:
            cmds.refresh(suspend=False)
            cmds.refresh(f=True)
            cmds.waitCursor(state=False)

    def _on_revert(self):
        _pt_revert(self.state)
        self._enable_controls(False)

    def _on_extr_drag(self, val):
        v = val/100.0
        self.w_extr.blockSignals(True); self.w_extr.setValue(v); self.w_extr.blockSignals(False)
        en = self.state.get("extrude_node")
        if en and cmds.objExists(en): cmds.setAttr(en+".localTranslateZ", v)
        self.state["pending_is_negative"] = v < 0.0

    def _on_extr_release(self):
        v = self.w_extr_sl.value()/100.0
        self._finalize_extrude(v)

    def _on_extr_field(self, v):
        self.w_extr_sl.blockSignals(True); self.w_extr_sl.setValue(int(v*100))
        self.w_extr_sl.blockSignals(False)
        en = self.state.get("extrude_node")
        if en and cmds.objExists(en): cmds.setAttr(en+".localTranslateZ", v)
        self.state["pending_is_negative"] = v < 0.0

    def _finalize_extrude(self, v):
        state = self.state
        en    = state.get("extrude_node"); obj = state.get("obj"); sn = state.get("short_name")
        if not en or not cmds.objExists(en): return
        new_neg = v < 0.0; old_neg = state["is_negative"]
        if new_neg != old_neg:
            try:
                cmds.select(obj, r=True); mel.eval("ReversePolygonNormals;")
            except Exception as e: cmds.warning(f"Reverse failed: {e}")
            state["is_negative"] = new_neg; state["pending_is_negative"] = new_neg
            _pt_bridge_direction_update(state)
            self._sync_bevel_state(); _pt_rebuild_bevel(sn, state)
        else:
            state["is_negative"] = new_neg; state["pending_is_negative"] = new_neg
        _pt_restore_mesh_selection(obj)

    def _on_boff_field(self, v):
        self.w_boff_sl.blockSignals(True); self.w_boff_sl.setValue(int(v*1000))
        self.w_boff_sl.blockSignals(False); self._apply_bevel_live()
    def _on_boff_slider(self, s):
        self.w_boff.blockSignals(True); self.w_boff.setValue(s/1000.0)
        self.w_boff.blockSignals(False); self._apply_bevel_live()

    def _on_bseg_field(self, v):
        self.w_bseg_sl.blockSignals(True); self.w_bseg_sl.setValue(v)
        self.w_bseg_sl.blockSignals(False); self._apply_bevel_live()
    def _on_bseg_slider(self, s):
        self.w_bseg.blockSignals(True); self.w_bseg.setValue(s)
        self.w_bseg.blockSignals(False); self._apply_bevel_live()

    def _on_angle_field(self, v):
        self.w_angle_sl.blockSignals(True); self.w_angle_sl.setValue(int(v))
        self.w_angle_sl.blockSignals(False); self._rebuild_bevel()
    def _on_angle_slider(self, s):
        self.w_angle_val.blockSignals(True); self.w_angle_val.setValue(float(s))
        self.w_angle_val.blockSignals(False); self._rebuild_bevel()

    def _set_angle_preset(self, angle):
        self.w_angle_val.setValue(angle)


class OverlayMergeTab(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._block = False; self._build()

    def _build(self):
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(10,10,10,10); lay.setSpacing(6)

        self.status = QtWidgets.QLabel("IDLE")
        self.status.setObjectName("statusOk")
        self.status.setAlignment(QtCore.Qt.AlignCenter)
        lay.addWidget(self.status)

        lay.addWidget(_section(f"Threshold ({_om_scene_unit()})"))
        row = QtWidgets.QHBoxLayout(); row.setSpacing(6)
        row.addWidget(QtWidgets.QLabel(f"Threshold ({_om_scene_unit()})"))
        self.field = QtWidgets.QDoubleSpinBox()
        self.field.setDecimals(6); self.field.setRange(-1e9,1e9)
        self.field.setSingleStep(0.001); self.field.setValue(0.1); self.field.setFixedWidth(130)
        row.addWidget(self.field)
        self.btn_drag = _icon_btn("drag","Viewport drag (MMB)\nShift=fine | Ctrl=coarse")
        row.addWidget(self.btn_drag); row.addStretch()
        lay.addLayout(row)

        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setMinimum(0); self.slider.setMaximum(1000)
        lay.addWidget(self.slider)

        lay.addWidget(_section("Actions"))
        brow = QtWidgets.QHBoxLayout(); brow.setSpacing(6)
        self.btn_start   = _icon_btn("play",  "START - preview live")
        self.btn_stop    = _icon_btn("stop",  "STOP - keep current result")
        self.btn_reset   = _icon_btn("reset", "Reset to baseline")
        self.btn_close_o = _icon_btn("close", "Close / restore")
        for b in [self.btn_start,self.btn_stop,self.btn_reset,self.btn_close_o]: brow.addWidget(b)
        brow.addStretch(); lay.addLayout(brow)

        hint = QtWidgets.QLabel("Select vertices or meshes, then press START.\nShift=fine | Ctrl=coarse")
        hint.setWordWrap(True); hint.setStyleSheet(f"color:{C_TEXT_DIM}; font-size:11px;")
        lay.addWidget(hint); lay.addStretch()

        self.field.valueChanged.connect(self._on_field)
        self.slider.valueChanged.connect(self._on_slider)
        self.btn_drag.clicked.connect(self._on_drag)
        self.btn_start.clicked.connect(self._on_start)
        self.btn_stop.clicked.connect(self._on_stop)
        self.btn_reset.clicked.connect(self._on_reset)
        self.btn_close_o.clicked.connect(self._on_close_o)

    def _v2s(self,v):
        mn,mx=_OM_STATE["soft"]["mn"],_OM_STATE["soft"]["mx"]
        if mx<=mn: mx=mn+1.0
        return int(max(0.0,min(1.0,(float(v)-mn)/(mx-mn)))*1000)
    def _s2v(self,s):
        mn,mx=_OM_STATE["soft"]["mn"],_OM_STATE["soft"]["mx"]
        if mx<=mn: mx=mn+1.0
        return mn+float(s)/1000.0*(mx-mn)
    def _apply(self,v):
        v=float(v); _OM_STATE["threshold"]=v; _om_expand_soft(v)
        self._block=True
        try: self.field.setValue(v); self.slider.setValue(self._v2s(v))
        finally: self._block=False
        _om_preview_update()
    def _on_field(self,v):
        if self._block: return
        self._apply(v)
    def _on_slider(self,s):
        if self._block: return
        self._apply(self._s2v(s))
    def _on_drag(self):
        def getv(): return float(self.field.value())
        def setv(v):
            _om_expand_soft(float(v))
            self._block=True
            try: self.field.setValue(float(v)); self.slider.setValue(self._v2s(float(v)))
            finally: self._block=False
        _om_start_mmb_dragger(getv,setv,_om_preview_update)
        self._set_status("DRAG (viewport)", "statusLabel")
    def _set_status(self, txt, obj_name):
        self.status.setText(txt); self.status.setObjectName(obj_name)
        self.status.setStyle(self.status.style())
    def _on_start(self):
        _OM_STATE["running"]=True; self._set_status("RUNNING","statusLabel"); _om_preview_update()
    def _on_stop(self):
        _om_stop_keep(); self._set_status("STOPPED - result kept","statusOk")
    def _on_reset(self):
        _om_reset()
        if _OM_STATE["running"]: _om_preview_update()
        self._set_status("RESET","statusOk")
    def _on_close_o(self):
        _om_close_restore(); self._set_status("IDLE","statusOk")


class CloseGapsTab(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()

    def _build(self):
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(10,10,10,10); lay.setSpacing(6)

        info = QtWidgets.QLabel("Select edges or vertices on both sides of the gap.")
        info.setWordWrap(True); info.setStyleSheet(f"color:{C_TEXT_DIM}; font-size:11px;")
        lay.addWidget(info)

        lay.addWidget(_section("Distance max"))
        self.lbl_dist = QtWidgets.QLabel("Distance max: 10.0000")
        self.lbl_dist.setStyleSheet(f"color:{C_RED}; font-weight:bold;")
        btn_auto = QtWidgets.QPushButton("Auto Detect"); btn_auto.setObjectName("dimBtn")
        btn_auto.clicked.connect(self._on_auto)
        row = QtWidgets.QHBoxLayout(); row.addWidget(self.lbl_dist); row.addWidget(btn_auto)
        lay.addLayout(row)

        self.dist_field = QtWidgets.QDoubleSpinBox()
        self.dist_field.setRange(-10.0,10.0); self.dist_field.setValue(10.0)
        self.dist_field.setDecimals(4); self.dist_field.setSingleStep(0.1)
        self.dist_field.valueChanged.connect(self._on_dist_change)
        self.dist_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.dist_slider.setRange(-10000,10000); self.dist_slider.setValue(10000)
        self.dist_slider.sliderMoved.connect(self._on_dist_slider)

        frow = QtWidgets.QHBoxLayout()
        frow.addWidget(QtWidgets.QLabel("Value")); frow.addWidget(self.dist_field)
        lay.addLayout(frow); lay.addWidget(self.dist_slider)

        lay.addWidget(_sep())
        lay.addWidget(_section("Actions"))

        btn_mid = QtWidgets.QPushButton("Close to Midpoint")
        btn_mid.setObjectName("greenBtn"); btn_mid.setMinimumHeight(34)
        btn_mid.clicked.connect(lambda: _cg_close_gaps(abs(self.dist_field.value())))
        lay.addWidget(btn_mid)

        dr = QtWidgets.QHBoxLayout()
        bg1 = QtWidgets.QPushButton("Toward Group 1"); bg1.setObjectName("dimBtn")
        bg2 = QtWidgets.QPushButton("Toward Group 2"); bg2.setObjectName("dimBtn")
        bg1.clicked.connect(lambda: _cg_close_dir(abs(self.dist_field.value()),"g1"))
        bg2.clicked.connect(lambda: _cg_close_dir(abs(self.dist_field.value()),"g2"))
        dr.addWidget(bg1); dr.addWidget(bg2); lay.addLayout(dr)

        note = QtWidgets.QLabel("Only vertices inside the max distance are moved.")
        note.setStyleSheet(f"color:{C_TEXT_DIM}; font-size:10px; font-style:italic;")
        lay.addWidget(note); lay.addStretch()

        if cmds.ls(selection=True): self._on_auto()

    def _update_lbl(self,v): self.lbl_dist.setText(f"Distance max: {v:.4f}")
    def _on_dist_change(self,v):
        self.dist_slider.blockSignals(True); self.dist_slider.setValue(int(v*1000))
        self.dist_slider.blockSignals(False); self._update_lbl(v)
    def _on_dist_slider(self,s):
        self.dist_field.blockSignals(True); self.dist_field.setValue(s/1000.0)
        self.dist_field.blockSignals(False); self._update_lbl(s/1000.0)
    def _on_auto(self):
        d = max(-10.0, min(10.0, _cg_estimate_gap()))
        self.dist_field.blockSignals(True); self.dist_field.setValue(d)
        self.dist_field.blockSignals(False)
        self.dist_slider.setValue(int(d*1000)); self.lbl_dist.setText(f"Distance max: {d:.4f}  (auto)")


class SmartPanelingUI(QtWidgets.QDialog):
    _instance = None

    def __init__(self, parent=_maya_main_window()):
        super().__init__(parent)
        self.setObjectName("smartPanelingUI")
        self.setWindowTitle("Smart Paneling")
        self.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.WindowCloseButtonHint)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setMinimumWidth(420)
        self.setMinimumHeight(520)
        self.setStyleSheet(GLOBAL_STYLE)
        self._build()

    def _build(self):
        main = QtWidgets.QVBoxLayout(self)
        main.setContentsMargins(0,0,0,0); main.setSpacing(0)

        self.tab_panel   = PanelToolTab()
        self.tab_overlay = OverlayMergeTab()
        self.tab_gaps    = CloseGapsTab()

        self.sections = QtWidgets.QToolBox()
        self.sections.setStyleSheet(f"""
            QToolBox::tab {{
                background: {C_BG};
                color: {C_TEXT_DIM};
                border: 1px solid {C_BORDER};
                padding: 6px 10px;
                font-weight: bold;
                letter-spacing: 1px;
            }}
            QToolBox::tab:selected {{
                color: {C_RED};
                background: {C_PANEL};
            }}
        """)
        self.sections.addItem(self.tab_panel,   "Panel Tool")
        self.sections.addItem(self.tab_overlay, "Overlay Merge")
        self.sections.addItem(self.tab_gaps,    "Close Gaps")
        self.sections.setCurrentIndex(0)
        self.sections.currentChanged.connect(self._fit_to_current_tab)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background:transparent;")
        scroll.setWidget(self.sections)

        main.addWidget(scroll)
        self._fit_to_current_tab()

    def _fit_to_current_tab(self):
        current = self.sections.currentWidget()
        if not current:
            return
        hint = self.sections.sizeHint()
        current_hint = current.sizeHint()
        hint.setHeight(max(hint.height(), current_hint.height() + 120))
        margins = self.layout().contentsMargins()
        width = max(self.minimumWidth(), hint.width() + margins.left() + margins.right() + 30)
        height = max(
            self.minimumHeight(),
            hint.height() + margins.top() + margins.bottom() + 40
        )
        self.resize(width, height)

    def closeEvent(self, event):
        _om_close_restore()
        super().closeEvent(event)

    @classmethod
    def show_ui(cls):
        if cls._instance:
            try: cls._instance.close(); cls._instance.deleteLater()
            except Exception: pass
        cls._instance = cls()
        cls._instance.show(); cls._instance.raise_(); cls._instance.activateWindow()
        return cls._instance


def show_ui():
    return SmartPanelingUI.show_ui()

show_ui()
