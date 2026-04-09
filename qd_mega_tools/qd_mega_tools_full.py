# -*- coding: utf-8 -*-
"""
=============================================================================
QD Mega Tools - Maya 2023-2027 Compatible
=============================================================================
Script complet (A à Z) avec UI et outils mesh principaux.
- Bridge / Detect / Vertices / Topology / Clean
- Correctif robuste de `mesh_remove_useless_vertices`
=============================================================================
"""

import math
import re
from collections import defaultdict

import maya.cmds as cmds
import maya.mel as mel

try:
    import maya.api.OpenMaya as om2
except Exception:
    om2 = None

try:
    from PySide2 import QtWidgets, QtCore, QtGui
    from shiboken2 import wrapInstance
except Exception:
    from PySide6 import QtWidgets, QtCore, QtGui
    from shiboken6 import wrapInstance

import maya.OpenMayaUI as omui


def get_maya_main_window():
    try:
        ptr = omui.MQtUtil.mainWindow()
        if ptr:
            return wrapInstance(int(ptr), QtWidgets.QWidget)
    except Exception:
        pass
    return None


def show_inview_message(message, duration=2.0, color="info"):
    icons = {"info": "i", "success": "OK", "warning": "!", "error": "X"}
    titles = {"info": "Info", "success": "Succes", "warning": "Attention", "error": "Erreur"}

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

    cmds.window(popup_win, title=titles.get(color, "Info"), widthHeight=(320, 70), sizeable=False, toolbox=True)
    cmds.columnLayout(adjustableColumn=True, bgc=bg, rowSpacing=5)
    cmds.separator(height=15, style="none")
    cmds.text(label="{}  {}".format(icons.get(color, "i"), message), align="center", font="boldLabelFont", height=25)
    cmds.separator(height=15, style="none")
    cmds.showWindow(popup_win)

    try:
        from PySide2.QtCore import QTimer
    except Exception:
        try:
            from PySide6.QtCore import QTimer
        except Exception:
            QTimer = None

    if QTimer:
        def _close():
            if cmds.window(popup_win, exists=True):
                cmds.deleteUI(popup_win)

        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(_close)
        timer.start(int(duration * 1000))
        if not hasattr(show_inview_message, "_timers"):
            show_inview_message._timers = []
        show_inview_message._timers.append(timer)


def set_selection_mode(mode):
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
    try:
        return component.split(".")[0]
    except Exception:
        return None


def get_all_selected_meshes():
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


def get_selected_mesh():
    meshes = get_all_selected_meshes()
    return meshes[0] if meshes else None


def filter_edges(components):
    return cmds.filterExpand(components, selectionMask=32) or []


def filter_faces(components):
    return cmds.filterExpand(components, selectionMask=34) or []


def filter_verts(components):
    return cmds.filterExpand(components, selectionMask=31) or []


def edge_to_faces(edge):
    faces = cmds.polyListComponentConversion(edge, toFace=True)
    return cmds.filterExpand(faces, selectionMask=34) or []


def edge_to_verts(edge):
    verts = cmds.polyListComponentConversion(edge, toVertex=True)
    return cmds.filterExpand(verts, selectionMask=31) or []


def vert_to_edges(vtx):
    edges = cmds.polyListComponentConversion(vtx, toEdge=True)
    return cmds.filterExpand(edges, selectionMask=32) or []


def vert_to_faces(vtx):
    faces = cmds.polyListComponentConversion(vtx, toFace=True)
    return cmds.filterExpand(faces, selectionMask=34) or []


def face_to_verts(face):
    verts = cmds.polyListComponentConversion(face, toVertex=True)
    return cmds.filterExpand(verts, selectionMask=31) or []


def face_to_edges(face):
    edges = cmds.polyListComponentConversion(face, toEdge=True)
    return cmds.filterExpand(edges, selectionMask=32) or []


def face_vertex_count(face):
    return len(face_to_verts(face)) if cmds.objExists(face) else 0


def get_vertex_position(vtx):
    try:
        return tuple(cmds.pointPosition(vtx, world=True))
    except Exception:
        return None


def normalize(v):
    l = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if l < 1e-12:
        return (0.0, 0.0, 0.0)
    return (v[0] / l, v[1] / l, v[2] / l)


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def is_border_edge(edge):
    return len(edge_to_faces(edge)) == 1


def is_border_vertex(vtx):
    for e in vert_to_edges(vtx):
        if is_border_edge(e):
            return True
    return False


def get_face_normal(face):
    try:
        info = cmds.polyInfo(face, fn=True) or []
        if not info:
            return (0.0, 1.0, 0.0)
        parts = [p for p in info[0].strip().replace("\t", " ").split(" ") if p]
        return normalize((float(parts[-3]), float(parts[-2]), float(parts[-1])))
    except Exception:
        return (0.0, 1.0, 0.0)


def get_edge_face_angle(edge):
    faces = edge_to_faces(edge)
    if len(faces) != 2:
        return 180.0
    n1 = get_face_normal(faces[0])
    n2 = get_face_normal(faces[1])
    d = abs(dot(n1, n2))
    d = max(0.0, min(1.0, d))
    return math.degrees(math.acos(d))


def mesh_detect_ngons():
    meshes = get_all_selected_meshes()
    if not meshes:
        show_inview_message("Aucun mesh sélectionné!", 2.0, "error")
        return
    ngons = []
    for mesh in meshes:
        fc = int(cmds.polyEvaluate(mesh, face=True) or 0)
        for i in range(fc):
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
        show_inview_message("Aucun mesh sélectionné!", 2.0, "error")
        return
    tris = []
    for mesh in meshes:
        fc = int(cmds.polyEvaluate(mesh, face=True) or 0)
        for i in range(fc):
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
        show_inview_message("Aucun mesh sélectionné!", 2.0, "error")
        return
    quads = []
    for mesh in meshes:
        fc = int(cmds.polyEvaluate(mesh, face=True) or 0)
        for i in range(fc):
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


def _remove_vertex_safely(vtx):
    if not cmds.objExists(vtx):
        return False
    try:
        cmds.polyDelVertex(vtx, constructionHistory=False)
        return True
    except Exception:
        pass
    try:
        edges = vert_to_edges(vtx)
        if edges:
            cmds.polyDelEdge(edges[0], cleanVertices=True, constructionHistory=False)
            return True
    except Exception:
        pass
    return False


def mesh_remove_useless_vertices():
    """Correctif robuste Maya 2023-2027."""
    meshes = get_all_selected_meshes()
    if not meshes:
        show_inview_message("Aucun mesh sélectionné!", 2.0, "error")
        return

    cmds.undoInfo(openChunk=True, chunkName="RemoveUselessVerts")
    try:
        total_removed = 0
        for mesh in meshes:
            for _pass in range(10):
                if not cmds.objExists(mesh):
                    break

                vcount = int(cmds.polyEvaluate(mesh, vertex=True) or 0)
                if vcount <= 0:
                    break

                to_delete = []
                for i in range(vcount - 1, -1, -1):
                    vtx = "{}.vtx[{}]".format(mesh, i)
                    if not cmds.objExists(vtx):
                        continue

                    faces = vert_to_faces(vtx)
                    edges = vert_to_edges(vtx)

                    if len(faces) == 0:
                        to_delete.append(vtx)
                        continue

                    if is_border_vertex(vtx):
                        continue

                    if len(edges) == 2:
                        p = get_vertex_position(vtx)
                        if not p:
                            continue

                        neighbors = []
                        for e in edges:
                            vs = edge_to_verts(e)
                            for vv in vs:
                                if vv != vtx and vv not in neighbors:
                                    neighbors.append(vv)

                        if len(neighbors) != 2:
                            continue

                        p0 = get_vertex_position(neighbors[0])
                        p1 = get_vertex_position(neighbors[1])
                        if not p0 or not p1:
                            continue

                        n1 = normalize((p[0] - p0[0], p[1] - p0[1], p[2] - p0[2]))
                        n2 = normalize((p1[0] - p[0], p1[1] - p[1], p1[2] - p[2]))
                        if abs(dot(n1, n2)) >= 0.999:
                            to_delete.append(vtx)

                if not to_delete:
                    break

                removed_this_pass = 0
                for vtx in to_delete:
                    if _remove_vertex_safely(vtx):
                        removed_this_pass += 1

                total_removed += removed_this_pass
                if removed_this_pass == 0:
                    break

        valid_meshes = [m for m in meshes if cmds.objExists(m)]
        if valid_meshes:
            cmds.select(valid_meshes, r=True)

        show_inview_message("{} vertices supprimés".format(total_removed), 2.0, "success" if total_removed > 0 else "info")

    except Exception as e:
        cmds.warning("RemoveUseless erreur: {}".format(e))
        show_inview_message("Erreur RemoveUseless!", 2.0, "error")
    finally:
        cmds.undoInfo(closeChunk=True)


def mesh_merge_vertices(threshold=0.001):
    meshes = get_all_selected_meshes()
    if not meshes:
        show_inview_message("Aucun mesh sélectionné!", 2.0, "error")
        return
    cmds.undoInfo(openChunk=True, chunkName="MergeVerts")
    try:
        total = 0
        for mesh in meshes:
            before = int(cmds.polyEvaluate(mesh, vertex=True) or 0)
            cmds.select("{}.vtx[*]".format(mesh), r=True)
            cmds.polyMergeVertex(distance=threshold, alwaysMergeTwoVertices=False)
            after = int(cmds.polyEvaluate(mesh, vertex=True) or 0)
            total += (before - after)
        cmds.select(meshes, r=True)
        show_inview_message("{} vertices fusionnés".format(total), 2.0, "success" if total > 0 else "info")
    finally:
        cmds.undoInfo(closeChunk=True)


def select_removable_edges(angle_threshold=5.0, allow_ngons=False, only_selected_faces=False):
    mesh = get_selected_mesh()
    if not mesh:
        show_inview_message("Aucun mesh sélectionné!", 2.0, "error")
        return

    sel = cmds.ls(selection=True, flatten=True) or []
    sel_faces = filter_faces(sel)

    if only_selected_faces and sel_faces:
        candidate = set()
        for f in sel_faces:
            for e in face_to_edges(f):
                candidate.add(e)
        candidate_edges = list(candidate)
    else:
        ec = int(cmds.polyEvaluate(mesh, edge=True) or 0)
        candidate_edges = ["{}.e[{}]".format(mesh, i) for i in range(ec)]

    removable = []
    for e in candidate_edges:
        if not cmds.objExists(e) or is_border_edge(e):
            continue
        if get_edge_face_angle(e) > angle_threshold:
            continue
        faces = edge_to_faces(e)
        if len(faces) != 2:
            continue
        merged = face_vertex_count(faces[0]) + face_vertex_count(faces[1]) - 2
        if allow_ngons:
            if merged > 8:
                continue
        else:
            if merged > 4:
                continue
        removable.append(e)

    if removable:
        set_selection_mode("edge")
        cmds.select(removable, r=True)
        show_inview_message("{} edges supprimables".format(len(removable)), 2.0, "info")
    else:
        cmds.select(mesh, r=True)
        show_inview_message("Aucune edge supprimable", 2.0, "success")


def auto_clean(angle_threshold=5.0, allow_ngons=False, max_passes=50, only_selected_faces=False):
    mesh = get_selected_mesh()
    if not mesh:
        show_inview_message("Aucun mesh sélectionné!", 2.0, "error")
        return

    cmds.undoInfo(openChunk=True, chunkName="AutoClean")
    try:
        total = 0
        for _ in range(max_passes):
            sel = cmds.ls(selection=True, flatten=True) or []
            sel_faces = filter_faces(sel)

            if only_selected_faces and sel_faces:
                candidate = set()
                for f in sel_faces:
                    for e in face_to_edges(f):
                        candidate.add(e)
                candidate_edges = list(candidate)
            else:
                ec = int(cmds.polyEvaluate(mesh, edge=True) or 0)
                candidate_edges = ["{}.e[{}]".format(mesh, i) for i in range(ec)]

            removable = []
            for e in candidate_edges:
                if not cmds.objExists(e) or is_border_edge(e):
                    continue
                if get_edge_face_angle(e) > angle_threshold:
                    continue
                fs = edge_to_faces(e)
                if len(fs) != 2:
                    continue
                merged = face_vertex_count(fs[0]) + face_vertex_count(fs[1]) - 2
                if (not allow_ngons and merged > 4) or (allow_ngons and merged > 8):
                    continue
                removable.append(e)

            if not removable:
                break

            try:
                cmds.polyDelEdge(removable, cleanVertices=True, constructionHistory=False)
                total += len(removable)
            except Exception:
                removed = 0
                for e in removable:
                    if cmds.objExists(e):
                        try:
                            cmds.polyDelEdge(e, cleanVertices=True, constructionHistory=False)
                            removed += 1
                        except Exception:
                            pass
                if removed == 0:
                    break
                total += removed

        if cmds.objExists(mesh):
            cmds.select(mesh, r=True)
        show_inview_message("{} edges supprimées".format(total), 2.0, "success" if total > 0 else "info")
    finally:
        cmds.undoInfo(closeChunk=True)


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
        self.setStyleSheet(
            """
            QPushButton {{
                background-color: {bg}; color: {fg};
                border: 1px solid #222; border-radius: 3px;
                font-weight: bold; font-size: 10px; padding: 2px 8px;
            }}
            QPushButton:hover {{ background-color: {bgh}; border-color: #444; }}
            """.format(bg=self._bg, fg=self._fg, bgh=QtGui.QColor(self._bg).lighter(130).name())
        )


class MTK_ParamSlider(QtWidgets.QWidget):
    valueChanged = QtCore.Signal(float)

    def __init__(self, label, min_val, max_val, default, decimals=1, parent=None):
        super(MTK_ParamSlider, self).__init__(parent)
        self._mul = 10 ** decimals

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        self.lbl = QtWidgets.QLabel(label)
        self.lbl.setFixedWidth(70)
        self.sld = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.sld.setRange(int(min_val * self._mul), int(max_val * self._mul))
        self.sld.setValue(int(default * self._mul))
        self.spn = QtWidgets.QDoubleSpinBox()
        self.spn.setRange(min_val, max_val)
        self.spn.setDecimals(decimals)
        self.spn.setValue(default)
        self.spn.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.spn.setFixedWidth(58)

        lay.addWidget(self.lbl)
        lay.addWidget(self.sld)
        lay.addWidget(self.spn)

        self.sld.valueChanged.connect(self._on_sld)
        self.spn.valueChanged.connect(self._on_spn)

    def _on_sld(self, v):
        val = float(v) / self._mul
        self.spn.blockSignals(True)
        self.spn.setValue(val)
        self.spn.blockSignals(False)
        self.valueChanged.emit(val)

    def _on_spn(self, v):
        self.sld.blockSignals(True)
        self.sld.setValue(int(v * self._mul))
        self.sld.blockSignals(False)
        self.valueChanged.emit(float(v))


class MeshToolkitWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super(MeshToolkitWidget, self).__init__(parent)
        self._angle = 5.0
        self._merge = 0.001
        self._allow_ngons = True
        self._local = False
        self._build_ui()

    def _build_ui(self):
        main = QtWidgets.QVBoxLayout(self)
        main.setContentsMargins(8, 8, 8, 8)

        detect = QtWidgets.QHBoxLayout()
        self.ngon_btn = MTK_ColorBtn("NGon", "Detect N-gons", "#50301e", "#ffaa70")
        self.tri_btn = MTK_ColorBtn("Tri", "Detect triangles", "#50301e", "#ffaa70")
        self.quad_btn = MTK_ColorBtn("Quad", "Detect quads", "#1e4030", "#70c080")
        detect.addWidget(self.ngon_btn)
        detect.addWidget(self.tri_btn)
        detect.addWidget(self.quad_btn)
        main.addLayout(detect)

        verts = QtWidgets.QHBoxLayout()
        self.remove_btn = MTK_ColorBtn("Remove Useless", "Remove unnecessary vertices", "#3a2a2a", "#ff9090")
        self.merge_btn = MTK_ColorBtn("Merge", "Merge overlapping vertices", "#3a2a2a", "#ff9090")
        verts.addWidget(self.remove_btn)
        verts.addWidget(self.merge_btn)
        main.addLayout(verts)

        self.merge_slider = MTK_ParamSlider("Threshold", 0.0, 0.1, self._merge, 4)
        main.addWidget(self.merge_slider)

        self.angle_slider = MTK_ParamSlider("Coplanar °", 0, 45, self._angle, 1)
        main.addWidget(self.angle_slider)

        options = QtWidgets.QHBoxLayout()
        self.local_chk = QtWidgets.QCheckBox("Local only")
        self.ngon_chk = QtWidgets.QCheckBox("Allow N-gons")
        self.ngon_chk.setChecked(True)
        options.addWidget(self.local_chk)
        options.addWidget(self.ngon_chk)
        options.addStretch()
        main.addLayout(options)

        clean = QtWidgets.QHBoxLayout()
        self.select_edges_btn = MTK_ColorBtn("Select Edges", "Select removable edges", "#3a3a20", "#c0c070")
        self.auto_clean_btn = MTK_ColorBtn("Auto Clean", "Automatically clean mesh", "#4a3a10", "#ffc040")
        clean.addWidget(self.select_edges_btn)
        clean.addWidget(self.auto_clean_btn)
        main.addLayout(clean)

        main.addStretch()

        self.ngon_btn.clicked.connect(mesh_detect_ngons)
        self.tri_btn.clicked.connect(mesh_detect_triangles)
        self.quad_btn.clicked.connect(mesh_detect_quads)
        self.remove_btn.clicked.connect(mesh_remove_useless_vertices)
        self.merge_btn.clicked.connect(lambda: mesh_merge_vertices(self._merge))
        self.merge_slider.valueChanged.connect(self._set_merge)
        self.angle_slider.valueChanged.connect(self._set_angle)
        self.local_chk.stateChanged.connect(self._set_local)
        self.ngon_chk.stateChanged.connect(self._set_ngons)
        self.select_edges_btn.clicked.connect(self._select_edges)
        self.auto_clean_btn.clicked.connect(self._run_clean)

    def _set_merge(self, val):
        self._merge = float(val)

    def _set_angle(self, val):
        self._angle = float(val)

    def _set_local(self, state):
        self._local = state == QtCore.Qt.Checked

    def _set_ngons(self, state):
        self._allow_ngons = state == QtCore.Qt.Checked

    def _select_edges(self):
        select_removable_edges(self._angle, self._allow_ngons, self._local)

    def _run_clean(self):
        auto_clean(self._angle, self._allow_ngons, 50, self._local)


class QDMegaToolsWindow(QtWidgets.QDialog):
    WINDOW_TITLE = "QD Mega Tools - Full"

    def __init__(self, parent=None):
        super(QDMegaToolsWindow, self).__init__(parent or get_maya_main_window())
        self.setWindowTitle(self.WINDOW_TITLE)
        self.setWindowFlags(QtCore.Qt.Window)
        self.setMinimumSize(340, 520)
        self.resize(360, 640)

        main = QtWidgets.QVBoxLayout(self)
        self.tabs = QtWidgets.QTabWidget()
        self.mesh_tab = MeshToolkitWidget()
        self.tabs.addTab(self.mesh_tab, "Mesh Toolkit")

        self.pr_tab = QtWidgets.QWidget()
        pr_layout = QtWidgets.QVBoxLayout(self.pr_tab)
        pr_label = QtWidgets.QLabel("PR Select Tools v3.8\n(Placeholder)")
        pr_label.setAlignment(QtCore.Qt.AlignCenter)
        pr_layout.addWidget(pr_label)
        self.tabs.addTab(self.pr_tab, "PR Select")

        main.addWidget(self.tabs)


def show_ui():
    global qd_mega_tools_window
    try:
        qd_mega_tools_window.close()
        qd_mega_tools_window.deleteLater()
    except Exception:
        pass
    qd_mega_tools_window = QDMegaToolsWindow()
    qd_mega_tools_window.show()


if __name__ == "__main__":
    show_ui()
