# -*- coding: utf-8 -*-
"""
Instance Cleaner
----------------
Finds identical or similar meshes, then can replace duplicates with Maya instances.

Scan modes/actions:
  - Scene: compare every mesh in the scene.
  - Selected Mesh(es): compare only the selected mesh roots and their children.
  - Find Selected is a separate fast action: it compares the selected source mesh
    against scene meshes without rebuilding the full scene group cache.

Requires: Maya 2020+, numpy (bundled with Maya), PySide2 or PySide6.
"""

from __future__ import print_function

import hashlib
import itertools
import math
import traceback
from collections import defaultdict

try:
    STRING_TYPES = (basestring,)
except NameError:
    STRING_TYPES = (str,)

try:
    import numpy as np
    HAS_NUMPY = True
except Exception:
    np = None
    HAS_NUMPY = False

import maya.cmds as cmds
import maya.OpenMayaUI as omui
import maya.api.OpenMaya as om2

try:
    from PySide6 import QtCore
    from PySide6.QtWidgets import *
    from PySide6.QtCore import *
    from PySide6.QtGui import *
    PYSIDE_VERSION = 6
except ImportError:
    from PySide2 import QtCore
    from PySide2.QtWidgets import *
    from PySide2.QtCore import *
    from PySide2.QtGui import *
    PYSIDE_VERSION = 2

try:
    from shiboken6 import wrapInstance
except ImportError:
    from shiboken2 import wrapInstance


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ROOT_GROUP      = "_INSTANCE_CLEANER"
MASTERS_GROUP   = "MASTERS"
INSTANCES_GROUP = "INSTANCES"
BACKUP_GROUP    = "BACKUPS"
CONVERTED_GROUP = "CONVERTED_GEO"

LAYER_MASTERS   = "DP_MASTERS"
LAYER_INSTANCES = "DP_INSTANCES"
LAYER_BACKUPS   = "DP_BACKUPS"
LAYER_CONVERTED = "DP_CONVERTED"

ATTR_IC_TYPE        = "ic_type"
ATTR_IC_GROUP       = "ic_group_id"
ATTR_IC_SOURCE      = "ic_source"
ATTR_IC_PROCESSED   = "ic_processed"
ATTR_IC_GROUP_NAME  = "ic_group_name"
ATTR_IC_BATCH       = "ic_batch_id"
ATTR_IC_ORIG_PARENT = "ic_original_parent"
ATTR_IC_ORIG_NAME   = "ic_original_name"
ATTR_IC_MATCH_TYPE  = "ic_match_type"
ATTR_IC_SCORE       = "ic_match_score"
ATTR_IC_ORIG_VIS     = "ic_original_visibility"
ATTR_IC_ORIG_LAYERS  = "ic_original_display_layers"
ATTR_IC_ORIG_MATRIX  = "ic_original_world_matrix"
ATTR_IC_STATUS      = "ic_status"
ATTR_IC_ORIG_SHADERS = "ic_original_shading_groups"

MATCH_SAFE      = "safe"
MATCH_FUZZY     = "fuzzy"
MATCH_PROCESSED = "processed"

ALIGN_ERROR_TOL_SAFE  = 0.006
ALIGN_ERROR_TOL_FUZZY = 0.030

FUZZY_CLUSTER_REPS = 3

ALIGN_VERIFY_TOL_DEFAULT        = 0.030
ORIENTATION_SEARCH_STEP_DEGREES = 15
ORIENTATION_SEARCH_MAX_POINTS   = 768
PCA_ICP_MAX_SAMPLE_POINTS       = 800
PCA_ICP_ITERATIONS              = 25


# Robust Similar method defaults, integrated from the standalone select_similar algorithm.
ROBUST_METHOD_NAME                = "Robust Similar (default)"
ROBUST_TOLERANCE_TOPOLOGY        = 0.01
ROBUST_TOLERANCE_VERTEX_COUNT    = 0.00
ROBUST_TOLERANCE_SHAPE_DEFAULT   = 0.015
ROBUST_USE_REAL_SIZE_FILTER      = True
ROBUST_REAL_SIZE_RATIO_MAX       = 1.20
ROBUST_REAL_SIZE_RATIO_MIN       = 1.0 / ROBUST_REAL_SIZE_RATIO_MAX
ROBUST_USE_VALENCE_TOPOLOGY_FILTER = True
ROBUST_MAX_VERTEX_SAMPLES        = 2500
ROBUST_MAX_EDGE_SAMPLES          = 2500
ROBUST_MAX_FACE_SAMPLES          = 2500
ROBUST_RADIAL_HIST_BINS          = 48
ROBUST_EDGE_LENGTH_HIST_BINS     = 48
ROBUST_FACE_AREA_HIST_BINS       = 48
ROBUST_RADIAL_HIST_MAX           = 3.0
ROBUST_EDGE_LENGTH_HIST_MAX      = 3.0
ROBUST_FACE_AREA_HIST_MAX        = 3.0
ROBUST_FACE_SIDE_HIST_MAX        = 12
ROBUST_VALENCE_HIST_MAX          = 16
ROBUST_EPSILON                   = 1e-10

GROUPING_CANCEL_CHECK_INTERVAL  = 256
GROUPING_PROGRESS_INTERVAL      = 512
DEEP_GEOMETRY_VERIFY_POINTS     = 128
DEEP_GEOMETRY_VERIFY_QUANTILES  = 96
DEEP_GEOMETRY_VERIFY_ROUND_TOL  = 0.0015


# ---------------------------------------------------------------------------
# Maya / Qt helpers
# ---------------------------------------------------------------------------
def maya_main_window():
    ptr = omui.MQtUtil.mainWindow()
    return wrapInstance(int(ptr), QWidget) if ptr else None


class UndoChunk(object):
    def __init__(self, name="InstanceCleanerOp"):
        self.name = name

    def __enter__(self):
        try:
            cmds.undoInfo(openChunk=True, chunkName=self.name)
        except Exception:
            pass
        return self

    def __exit__(self, *args):
        try:
            cmds.undoInfo(closeChunk=True)
        except Exception:
            pass
        return False


class ProcessCanceled(Exception):
    pass


class ICProgressDialog(QDialog):
    """Small modal progress window with one coherent cancel state.

    The callback returned by :meth:`callback` accepts both legacy
    ``(percent, message)`` and process ``(current, total, message)`` calls,
    plus keyword details used by newer scan/find/process code.
    """
    def __init__(self, title="Instance Cleaner Progress", parent=None):
        super(ICProgressDialog, self).__init__(parent or maya_main_window())
        self._canceled = False
        self._log_lines = []
        self.setWindowTitle(title)
        self.setModal(False)
        self.setMinimumWidth(720)
        self.resize(760, 340)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setStyleSheet(
            "QDialog { background:#161616; }"
            "QLabel { color:#bdbdbd; font-size:16px; }"
            "QProgressBar { background:#181818; color:#bdbdbd; border:1px solid #303030; border-radius:8px; text-align:center; min-height:28px; }"
            "QProgressBar::chunk { background:#7a1e2a; border-radius:7px; }"
            "QPushButton { background:#2b2b2b; color:#bdbdbd; border:1px solid #4a4a4a; border-radius:8px; min-height:36px; font-weight:700; }"
            "QPushButton:hover { border-color:#7a1e2a; }"
            "QPushButton:disabled { background:#202020; color:#5e5e5e; border-color:#303030; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        self.step_lbl = QLabel("Step: waiting")
        self.group_lbl = QLabel("Group: -")
        self.mesh_lbl = QLabel("Mesh: -")
        self.count_lbl = QLabel("0 / 0 (0%)")
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.log_lbl = QLabel("")
        self.log_lbl.setWordWrap(True)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.cancel)

        layout.addWidget(self.step_lbl)
        layout.addWidget(self.group_lbl)
        layout.addWidget(self.mesh_lbl)
        layout.addWidget(self.count_lbl)
        layout.addWidget(self.bar)
        layout.addWidget(self.log_lbl)
        layout.addWidget(self.cancel_btn)

    def cancel(self):
        self._canceled = True
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setText("Cancel requested...")
        self.step_lbl.setText("Step: cancel requested")

    def was_canceled(self):
        QApplication.processEvents()
        return bool(self._canceled)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.cancel()
            event.accept()
            return
        super(ICProgressDialog, self).keyPressEvent(event)

    def update_progress(self, current=None, total=None, percent=None, message="",
                        step=None, group=None, mesh=None, log=None):
        if total is None:
            total = 100
        total = max(1, int(total or 1))
        if current is None:
            current = int(percent or 0)
            if percent is not None and total != 100:
                current = int(float(percent) * float(total) / 100.0)
        current = max(0, min(int(current or 0), total))
        if percent is None:
            percent = int(float(current) / float(total) * 100.0)
        percent = max(0, min(100, int(percent)))

        text = str(message or "")
        if step is None and text:
            step = text
        self.step_lbl.setText("Step: {}".format(step or "working"))
        self.group_lbl.setText("Group: {}".format(_short(str(group)) if group else "-"))
        self.mesh_lbl.setText("Mesh: {}".format(_short(str(mesh)) if mesh else "-"))
        self.count_lbl.setText("{} / {} ({}%)".format(current, total, percent))
        self.bar.setValue(percent)

        entry = log or text
        if entry:
            entry = _short(str(entry))
            if not self._log_lines or self._log_lines[-1] != entry:
                self._log_lines.append(entry)
                self._log_lines = self._log_lines[-5:]
            self.log_lbl.setText("\n".join(self._log_lines))
        QApplication.processEvents()

    def callback(self):
        def _cb(*args, **kwargs):
            if len(args) == 2 and "percent" not in kwargs:
                kwargs["percent"], kwargs["message"] = args
            elif len(args) >= 3:
                kwargs["current"], kwargs["total"], kwargs["message"] = args[:3]
            self.update_progress(**kwargs)
        return _cb


# ---------------------------------------------------------------------------
# Basic utils
# ---------------------------------------------------------------------------
def _short(obj):
    return obj.split("|")[-1] if obj else obj


def _safe_name(name):
    out = "".join(c if c.isalnum() or c == "_" else "_" for c in name)
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_") or "mesh"


def _long(obj):
    if not obj:
        return obj
    try:
        found = cmds.ls(obj, long=True) or []
        return found[0] if found else obj
    except Exception:
        return obj


def _exists(obj):
    if not obj:
        return False
    try:
        return cmds.objExists(obj)
    except Exception:
        return False


def _dedupe_keep_order(items):
    """Return unique items while preserving order.

    Maya node strings are normalized to long paths only when they exist.
    Plain strings, labels and numeric indices stay untouched.
    """
    seen = set()
    out = []
    for item in items or []:
        if item is None:
            continue
        if isinstance(item, STRING_TYPES) and item == "":
            continue
        normalized = item
        try:
            if isinstance(item, STRING_TYPES) and _exists(item):
                normalized = _long(item)
        except Exception:
            normalized = item
        if normalized not in seen:
            seen.add(normalized)
            out.append(normalized)
    return out


def _is_under_ic_root(node_or_path):
    """Return True only for the top-level Instance Cleaner hierarchy."""
    if not node_or_path:
        return False
    path = str(node_or_path)
    root = "|{}".format(ROOT_GROUP)
    if path == root or path.startswith(root + "|"):
        return True
    try:
        long_path = _long(path)
        return long_path == root or str(long_path).startswith(root + "|")
    except Exception:
        return False


def _is_referenced(obj):
    try:
        return cmds.referenceQuery(obj, isNodeReferenced=True)
    except Exception:
        return False


def _get_dag_path(node_name):
    """Return an MDagPath for a DAG node, or raise a clean RuntimeError.

    Maya's selection list can contain non-DAG nodes when names are ambiguous
    or when a stale helper/display node is passed by mistake.  Calling
    getDagPath() on those nodes raises the noisy "item is not a DAG path"
    error, so centralize the validation here.
    """
    sel = om2.MSelectionList()
    sel.add(node_name)
    try:
        return sel.getDagPath(0)
    except Exception:
        try:
            dep = sel.getDependNode(0)
            if dep.hasFn(om2.MFn.kDagNode):
                return om2.MDagPath.getAPathTo(dep)
        except Exception:
            pass
    raise RuntimeError("not a DAG path: {}".format(node_name))


def _get_mesh_fn(transform_name):
    """Return (MFnMesh, MDagPath) for the first valid non-intermediate mesh shape.

    Maya transforms can contain multiple mesh shapes.  ``extendToShape()`` may
    choose an intermediate construction-history shape, so walk every child shape
    and return the first non-intermediate mesh instead.
    """
    try:
        if not transform_name or not _exists(transform_name):
            return None, None
        candidates = []
        if cmds.nodeType(transform_name) == "mesh":
            candidates = cmds.ls(transform_name, long=True) or []
        else:
            candidates = cmds.listRelatives(transform_name, shapes=True, fullPath=True, noIntermediate=True) or []
            if not candidates:
                candidates = cmds.listRelatives(transform_name, shapes=True, fullPath=True) or []
        for shape in candidates:
            try:
                if cmds.nodeType(shape) != "mesh":
                    continue
                dag = _get_dag_path(shape)
                if dag.apiType() != om2.MFn.kMesh:
                    continue
                dep = om2.MFnDependencyNode(dag.node())
                try:
                    if dep.findPlug("intermediateObject", False).asBool():
                        continue
                except Exception:
                    pass
                fn = om2.MFnMesh(dag)
                if fn.numVertices <= 0 or fn.numPolygons <= 0:
                    continue
                return fn, dag
            except Exception:
                continue
        return None, None
    except Exception:
        return None, None

def _has_mesh_shape(transform_name):
    fn, _ = _get_mesh_fn(transform_name)
    return fn is not None


def _get_world_matrix(node):
    try:
        return cmds.xform(node, q=True, ws=True, matrix=True)
    except Exception:
        return [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]


def _apply_world_matrix(node, matrix):
    try:
        cmds.xform(node, ws=True, matrix=matrix)
        return True
    except Exception as e:
        cmds.warning("[IC] Matrix apply failed on {}: {}".format(node, e))
        return False


def _world_bbox(node):
    try:
        bb = cmds.exactWorldBoundingBox(node, calculateExactly=True)
        center = ((bb[0]+bb[3])*0.5, (bb[1]+bb[4])*0.5, (bb[2]+bb[5])*0.5)
        size = (max(abs(bb[3]-bb[0]), 1e-8),
                max(abs(bb[4]-bb[1]), 1e-8),
                max(abs(bb[5]-bb[2]), 1e-8))
        return center, size
    except Exception:
        return (0., 0., 0.), (1., 1., 1.)


def _object_bbox_center(node):
    fn, _ = _get_mesh_fn(node)
    if not fn:
        return (0., 0., 0.)
    c = fn.boundingBox.center
    return (c.x, c.y, c.z)


# ---------------------------------------------------------------------------
# Vertex manipulation (preserves normals)
# ---------------------------------------------------------------------------
def _move_vertices_object_space(node, offset):
    fn, _ = _get_mesh_fn(node)
    if not fn:
        return
    pts = fn.getPoints(om2.MSpace.kObject)
    ov  = om2.MVector(offset[0], offset[1], offset[2])
    for i in range(len(pts)):
        pts[i] = om2.MPoint(om2.MVector(pts[i]) + ov)
    fn.setPoints(pts, om2.MSpace.kObject)
    try:
        fn.updateSurface()
    except Exception:
        pass


def _center_shape_on_transform(node):
    """Shift geometry so bbox center sits at origin without touching normals."""
    center = _object_bbox_center(node)
    if max(abs(center[0]), abs(center[1]), abs(center[2])) < 1e-5:
        return
    _move_vertices_object_space(node, (-center[0], -center[1], -center[2]))


# ---------------------------------------------------------------------------
# Scene traversal
# ---------------------------------------------------------------------------
def _iter_mesh_transforms(root=None, include_ic=False):
    """Return list of unique long-path transform nodes that have a mesh shape."""
    results = []
    seen    = set()

    def _process_dag(start_dag):
        it = om2.MItDag(om2.MItDag.kDepthFirst, om2.MFn.kTransform)
        if start_dag is not None:
            it.reset(start_dag)

        while not it.isDone():
            dag       = it.getPath()
            full_path = dag.fullPathName()

            if not include_ic and _is_under_ic_root(full_path):
                it.prune()
                it.next()
                continue

            if full_path not in seen:
                for i in range(dag.childCount()):
                    child = dag.child(i)
                    if child.apiType() != om2.MFn.kMesh:
                        continue
                    dep = om2.MFnDependencyNode(child)
                    try:
                        if dep.findPlug("intermediateObject", False).asBool():
                            continue
                    except Exception:
                        pass
                    seen.add(full_path)
                    results.append(full_path)
                    break

            it.next()

    try:
        if root:
            root_dag = _get_dag_path(root)
            _process_dag(root_dag)
        else:
            _process_dag(None)
    except Exception as e:
        # Ignore stale/non-DAG inputs quietly; callers often pass optional helper
        # nodes while rebuilding.  Real traversal errors are still useful.
        if "not a DAG path" not in str(e):
            cmds.warning("[IC] _iter_mesh_transforms error: {}".format(e))

    return results


def _get_selected_transforms():
    selection = cmds.ls(sl=True, long=True) or []
    out = []
    seen = set()
    for obj in selection:
        if "." in obj:
            obj = obj.split(".")[0]
        if not _exists(obj):
            continue
        if cmds.nodeType(obj) == "mesh":
            parents = cmds.listRelatives(obj, parent=True, fullPath=True) or []
            if parents:
                obj = parents[0]
        obj = _long(obj)
        if obj not in seen:
            seen.add(obj)
            out.append(obj)
    return out



def _get_ordered_selected_transforms():
    """Return selected transforms while preserving Maya selection order when available."""
    try:
        selection = cmds.ls(orderedSelection=True, long=True) or []
    except Exception:
        selection = []
    if not selection:
        try:
            selection = cmds.ls(os=True, long=True) or []
        except Exception:
            selection = []
    if not selection:
        try:
            selection = cmds.ls(sl=True, long=True) or []
        except Exception:
            selection = []

    out = []
    seen = set()
    for obj in selection:
        if "." in obj:
            obj = obj.split(".")[0]
        if not _exists(obj):
            continue
        if cmds.nodeType(obj) == "mesh":
            parents = cmds.listRelatives(obj, parent=True, fullPath=True) or []
            if parents:
                obj = parents[0]
        obj = _long(obj)
        if obj not in seen:
            seen.add(obj)
            out.append(obj)
    return out

def _collect_mesh_transforms_from_roots(roots, include_ic=False):
    roots = _dedupe_keep_order(roots)
    transforms = []
    for root in roots:
        if not _exists(root):
            continue
        if "." in root:
            root = root.split(".")[0]
        if cmds.nodeType(root) == "mesh":
            parents = cmds.listRelatives(root, parent=True, fullPath=True) or []
            if parents:
                root = parents[0]
        children = _iter_mesh_transforms(root, include_ic=include_ic)
        if children:
            transforms.extend(children)
        elif _has_mesh_shape(root):
            transforms.append(_long(root))
    return _dedupe_keep_order(transforms)


# ---------------------------------------------------------------------------
# Viewport helpers
# ---------------------------------------------------------------------------
def _model_panels():
    return cmds.getPanel(type="modelPanel") or []


def _active_model_panel():
    try:
        panel = cmds.getPanel(withFocus=True)
        if panel and cmds.getPanel(typeOf=panel) == "modelPanel":
            return panel
    except Exception:
        pass
    panels = _model_panels()
    return panels[0] if panels else None


def _isolate_state(panel):
    try:
        return bool(cmds.isolateSelect(panel, q=True, state=True))
    except Exception:
        return False


def _select_nodes(nodes):
    nodes = [n for n in _dedupe_keep_order(nodes) if _exists(n)]
    if nodes:
        cmds.select(nodes, r=True)
    return nodes


def _make_nodes_visible_for_selection(nodes, show_transform_visibility=False):
    """Make selected IC nodes visible enough to confirm the selection in viewport.

    Backups are intentionally hidden after PROCESS.  Selection tools still need
    to be readable, so this helper only reveals display layers by default and
    can also enable transform visibility for explicit backup/source selection.
    """
    nodes = [n for n in _dedupe_keep_order(nodes) if _exists(n)]
    changed = 0
    for node in nodes:
        try:
            for layer in (cmds.listConnections(node, type="displayLayer") or []):
                if layer != "defaultLayer" and _exists(layer + ".visibility"):
                    if not cmds.getAttr(layer + ".visibility"):
                        cmds.setAttr(layer + ".visibility", 1)
                        changed += 1
        except Exception:
            pass
        if show_transform_visibility:
            try:
                plug = node + ".visibility"
                if _exists(plug) and not cmds.getAttr(plug):
                    cmds.setAttr(plug, 1)
                    changed += 1
            except Exception:
                pass
    return changed


def _isolate_nodes(nodes, add=False, frame=True):
    nodes = _select_nodes(nodes)
    if not nodes:
        return 0
    panel = _active_model_panel()
    if not panel:
        return len(nodes)
    try:
        if _isolate_state(panel):
            if add:
                for n in nodes:
                    try:
                        cmds.isolateSelect(panel, addDagObject=n)
                    except Exception:
                        pass
            else:
                cmds.isolateSelect(panel, state=0)
                cmds.isolateSelect(panel, state=1)
                for n in nodes:
                    try:
                        cmds.isolateSelect(panel, addDagObject=n)
                    except Exception:
                        pass
        else:
            cmds.isolateSelect(panel, state=1)
            for n in nodes:
                try:
                    cmds.isolateSelect(panel, addDagObject=n)
                except Exception:
                    pass
    except Exception:
        pass
    if frame:
        try:
            cmds.viewFit(panel, all=False, animate=False)
        except Exception:
            try:
                cmds.viewFit(all=False)
            except Exception:
                pass
    return len(nodes)


def _frame_selected():
    panel = _active_model_panel()
    try:
        if panel:
            cmds.viewFit(panel, all=False, animate=False)
        else:
            cmds.viewFit(all=False)
    except Exception:
        pass


def _exit_isolate_all_panels():
    for panel in _model_panels():
        try:
            cmds.isolateSelect(panel, state=0)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Groups / layers / attributes
# ---------------------------------------------------------------------------
def _child_transform_by_name(parent, child_name):
    """Return the direct child transform matching child_name under parent."""
    if not parent or not _exists(parent):
        return None
    try:
        parent_long = _long(parent)
        children = cmds.listRelatives(parent_long, children=True, fullPath=True, type="transform") or []
        for child in children:
            if _short(child) == child_name:
                return _long(child)
    except Exception:
        pass
    return None


def _ensure_group(name, parent=None):
    """Create or return a transform group without stealing same-named user groups."""
    if parent and _exists(parent):
        parent_long = _long(parent)
        child = _child_transform_by_name(parent_long, name)
        if child:
            return child
        try:
            group = cmds.group(em=True, name=name, parent=parent_long)
        except Exception:
            group = cmds.group(em=True, name=name)
            try:
                group = cmds.parent(group, parent_long, absolute=True)[0]
            except Exception:
                pass
        group = _long(group)
    else:
        exact_root = "|{}".format(name) if "|" not in str(name) else str(name)
        if _exists(exact_root):
            group = _long(exact_root)
        else:
            group = cmds.group(em=True, name=name)
            group = _long(group)
    try:
        cmds.xform(group, ws=True, t=(0, 0, 0), ro=(0, 0, 0), s=(1, 1, 1))
    except Exception:
        pass
    return _long(group)


def _ic_root_path():
    root = "|{}".format(ROOT_GROUP)
    if _exists(root):
        return _long(root)
    return None


def _ic_group_path(group_name, ensure=False):
    """Return an Instance Cleaner child group path by short name."""
    if ensure:
        _ensure_ic_groups()
    root = _ic_root_path()
    if not root:
        return None
    return _child_transform_by_name(root, group_name)


def _ensure_ic_groups():
    root      = _ensure_group(ROOT_GROUP)
    masters   = _ensure_group(MASTERS_GROUP,   root)
    instances = _ensure_group(INSTANCES_GROUP, root)
    backups   = _ensure_group(BACKUP_GROUP,    root)
    converted = _ensure_group(CONVERTED_GROUP, root)
    return root, masters, instances, backups, converted


def _ensure_layer(layer_name, color_index=None):
    if not _exists(layer_name):
        cmds.createDisplayLayer(name=layer_name, empty=True)
    if color_index is not None:
        try:
            cmds.setAttr(layer_name + ".color", color_index)
        except Exception:
            pass
    return layer_name


def _add_to_layer(layer_name, nodes):
    if not _exists(layer_name):
        return
    nodes = [n for n in (cmds.ls(nodes, long=True) or []) if _exists(n)]
    if not nodes:
        return
    try:
        cmds.editDisplayLayerMembers(layer_name, *nodes, noRecurse=True)
    except Exception as e:
        cmds.warning("[IC] Add to layer {} failed: {}".format(layer_name, e))


def _remove_from_display_layers(nodes):
    for node in (cmds.ls(nodes, long=True) or []):
        try:
            for layer in (cmds.listConnections(node, type="displayLayer") or []):
                if layer != "defaultLayer":
                    try:
                        cmds.editDisplayLayerMembers(layer, node, remove=True)
                    except Exception:
                        pass
        except Exception:
            pass


def _unlock_layer_for(node):
    """Ensure the node's display layer is not locked (would block instancing)."""
    try:
        for layer in (cmds.listConnections(node, type="displayLayer") or []):
            if layer == "defaultLayer":
                continue
            try:
                if cmds.getAttr(layer + ".displayType") == 2:
                    cmds.setAttr(layer + ".displayType", 0)
            except Exception:
                pass
    except Exception:
        pass


def _unlock_transform_for_edit(node):
    if not _exists(node):
        return
    _unlock_layer_for(node)
    attrs = (
        "translateX", "translateY", "translateZ",
        "rotateX", "rotateY", "rotateZ",
        "scaleX", "scaleY", "scaleZ",
        "visibility", "inheritsTransform",
    )
    for attr in attrs:
        plug = node + "." + attr
        try:
            if cmds.objExists(plug) and cmds.getAttr(plug, lock=True):
                cmds.setAttr(plug, lock=False)
        except Exception:
            pass


def _parent_absolute_if_possible(node, parent):
    """Parent a node while preserving world transform, quietly handling no-ops.

    Maya returns None (and prints "skipped, already a child") when the node is
    already under the requested parent.  Older versions of this tool indexed that
    None return value, which produced repeated "NoneType is not subscriptable"
    warnings during rename/rebuild passes.
    """
    if not (_exists(node) and _exists(parent)):
        return _long(node)

    node_long = _long(node)
    parent_long = _long(parent)

    try:
        current_parent = cmds.listRelatives(node_long, parent=True, fullPath=True) or []
        if current_parent and _long(current_parent[0]) == parent_long:
            return node_long
    except Exception:
        pass

    _unlock_transform_for_edit(node_long)
    try:
        result = cmds.parent(node_long, parent_long, absolute=True)
        if result:
            return (cmds.ls(result[0], long=True) or [result[0]])[0]
        # Maya can legally return None for a no-op.  Keep the current long path.
        return _long(node_long)
    except Exception as e:
        msg = str(e)
        if "already a child" not in msg.lower():
            cmds.warning("[IC] Could not move {} under {}: {}".format(_short(node_long), _short(parent_long), e))
        return _long(node_long)


def _rename_node_safe(node, new_name):
    """Rename a Maya node and return its new long path without breaking the tool.

    Maya will auto-suffix if another sibling already uses the requested name.
    This wrapper keeps the rest of the script stable when nodes are locked,
    missing, or already named correctly.
    """
    if not _exists(node):
        return _long(node)
    clean = _safe_name(new_name)
    try:
        _unlock_transform_for_edit(node)
    except Exception:
        pass
    try:
        renamed = cmds.rename(node, clean)
        return (cmds.ls(renamed, long=True) or [renamed])[0]
    except Exception as e:
        cmds.warning("[IC] Rename failed for {} -> {}: {}".format(_short(node), clean, e))
        return _long(node)


def _sort_nodes_for_numbering(nodes):
    """Stable, readable node order for deterministic 001/002/003 suffixes."""
    return sorted(_dedupe_keep_order([n for n in (nodes or []) if _exists(n)]),
                  key=lambda n: (_short(n).lower(), _long(n).lower()))


def _ensure_ic_layers():
    lm = _ensure_layer(LAYER_MASTERS,   17)
    li = _ensure_layer(LAYER_INSTANCES, 14)
    lb = _ensure_layer(LAYER_BACKUPS,   21)
    lc = _ensure_layer(LAYER_CONVERTED, 18)
    try:
        cmds.setAttr(lm + ".visibility", 1)
        cmds.setAttr(li + ".visibility", 1)
        cmds.setAttr(lb + ".visibility", 0)
        cmds.setAttr(lc + ".visibility", 1)
    except Exception:
        pass
    return lm, li, lb, lc


def _add_ic_attr(node, attr_name, value, attr_type="string"):
    if not _exists(node):
        return
    if not cmds.attributeQuery(attr_name, node=node, exists=True):
        try:
            if attr_type == "string":
                cmds.addAttr(node, ln=attr_name, dt="string")
            elif attr_type == "int":
                cmds.addAttr(node, ln=attr_name, at="long")
            elif attr_type == "bool":
                cmds.addAttr(node, ln=attr_name, at="bool")
            elif attr_type == "float":
                cmds.addAttr(node, ln=attr_name, at="double")
        except Exception:
            pass
    try:
        if attr_type == "string":
            cmds.setAttr(node + "." + attr_name, str(value), type="string")
        elif attr_type == "bool":
            cmds.setAttr(node + "." + attr_name, bool(value))
        elif attr_type == "float":
            cmds.setAttr(node + "." + attr_name, float(value))
        else:
            cmds.setAttr(node + "." + attr_name, int(value))
    except Exception:
        pass


def _get_ic_attr(node, attr_name, default=None):
    if not _exists(node):
        return default
    if not cmds.attributeQuery(attr_name, node=node, exists=True):
        return default
    try:
        return cmds.getAttr(node + "." + attr_name)
    except Exception:
        return default


def _tag_node(node, ic_type, group_id, source="", group_name="", match_type="", score=0.0):
    _add_ic_attr(node, ATTR_IC_TYPE,       ic_type,    "string")
    _add_ic_attr(node, ATTR_IC_GROUP,      group_id,   "int")
    _add_ic_attr(node, ATTR_IC_SOURCE,     source,     "string")
    _add_ic_attr(node, ATTR_IC_GROUP_NAME, group_name, "string")
    _add_ic_attr(node, ATTR_IC_MATCH_TYPE, match_type, "string")
    _add_ic_attr(node, ATTR_IC_SCORE,      score,      "float")


def _clear_ic_attrs(node):
    for attr in (ATTR_IC_TYPE, ATTR_IC_GROUP, ATTR_IC_SOURCE, ATTR_IC_PROCESSED,
                 ATTR_IC_GROUP_NAME, ATTR_IC_BATCH, ATTR_IC_ORIG_PARENT,
                 ATTR_IC_ORIG_NAME, ATTR_IC_MATCH_TYPE, ATTR_IC_SCORE,
                 ATTR_IC_ORIG_VIS, ATTR_IC_ORIG_LAYERS, ATTR_IC_ORIG_MATRIX, ATTR_IC_STATUS,
                 ATTR_IC_ORIG_SHADERS):
        try:
            if _exists(node) and cmds.attributeQuery(attr, node=node, exists=True):
                cmds.deleteAttr(node + "." + attr)
        except Exception:
            pass


def _make_clean_group_name(reference_mesh, used_names):
    base = _safe_name(_short(reference_mesh))
    if not base.upper().endswith("_GRP"):
        base = base + "_GRP"
    candidate = base
    idx = 2
    while candidate in used_names:
        candidate = "{}_{:02d}".format(base, idx)
        idx += 1
    used_names.add(candidate)
    return candidate


def _unique_label(base, existing):
    base      = _safe_name(base)
    candidate = base
    idx = 2
    while candidate in existing:
        candidate = "{}_{:02d}".format(base, idx)
        idx += 1
    return candidate


# ---------------------------------------------------------------------------
# Signature / detection helpers
# ---------------------------------------------------------------------------
def _round_to(value, tolerance):
    if tolerance <= 0:
        return value
    return round(value / tolerance) * tolerance


def _hash_blob(*values):
    h = hashlib.md5()
    for v in values:
        h.update(str(v).encode("utf-8"))
        h.update(b"|")
    return h.hexdigest()


def _tuple_rounded(values, tol):
    return tuple(float(_round_to(v, tol)) for v in values)


def _sample_quantiles(values, count=24, tol=0.001):
    """Order-independent quantile sampling."""
    values = sorted([float(v) for v in values])
    if not values:
        return tuple([0.0] * count)
    if len(values) == 1:
        return tuple([float(_round_to(values[0], tol))] * count)
    out = []
    max_idx = len(values) - 1
    for i in range(count):
        t   = float(i) / float(max(1, count - 1))
        pos = t * max_idx
        lo  = int(math.floor(pos))
        hi  = int(math.ceil(pos))
        val = values[lo] if lo == hi else values[lo]*(1.-(pos-lo)) + values[hi]*(pos-lo)
        out.append(float(_round_to(val, tol)))
    return tuple(out)


def _histogram(values, bins=16, max_value=1.0):
    if not values:
        return tuple([0] * bins)
    out = [0] * bins
    max_value = max(max_value, 1e-8)
    for v in values:
        idx = int((float(v) / max_value) * float(bins - 1))
        out[max(0, min(bins-1, idx))] += 1
    total = float(max(1, len(values)))
    return tuple(float(_round_to(v / total, 0.0001)) for v in out)


def _bbox_size_from_fn(fn_mesh):
    try:
        bb = fn_mesh.boundingBox
        return (max(abs(bb.max.x - bb.min.x), 1e-8),
                max(abs(bb.max.y - bb.min.y), 1e-8),
                max(abs(bb.max.z - bb.min.z), 1e-8))
    except Exception:
        return (1., 1., 1.)


def _polygon_area(points):
    if len(points) < 3:
        return 0.
    origin = om2.MVector(points[0])
    area   = 0.
    for i in range(1, len(points) - 1):
        a = om2.MVector(points[i])   - origin
        b = om2.MVector(points[i+1]) - origin
        area += (a ^ b).length() * 0.5
    return area


def _point_cloud_descriptors(points, tol=0.001):
    """Rotation-invariant descriptors from point cloud."""
    if not points:
        z24 = tuple([0.]*24)
        return z24, z24, (1., 1., 1.)

    vecs   = [om2.MVector(p.x, p.y, p.z) for p in points]
    n      = len(vecs)
    center = om2.MVector(0., 0., 0.)
    for v in vecs:
        center += v
    center /= float(max(1, n))

    centered = [v - center for v in vecs]
    radii    = sorted([v.length() for v in centered])
    max_r    = max(max(radii), 1e-8)

    radial_norm  = [r / max_r for r in radii]
    radial_quant = _sample_quantiles(radial_norm, count=24, tol=tol)

    step  = max(1, n // 40)
    idxs  = list(range(0, n, step))[:40]
    sub   = [centered[i] for i in idxs]
    dists = sorted([(sub[i] - sub[j]).length() / max_r
                    for i in range(len(sub))
                    for j in range(i+1, len(sub))])
    dist_quant = _sample_quantiles(dists, count=24, tol=tol)

    xs = sorted([v.x for v in centered])
    ys = sorted([v.y for v in centered])
    zs = sorted([v.z for v in centered])

    spreads = sorted([
        max(xs[-1]-xs[0], 1e-8) if xs else 1e-8,
        max(ys[-1]-ys[0], 1e-8) if ys else 1e-8,
        max(zs[-1]-zs[0], 1e-8) if zs else 1e-8,
    ])
    max_sp = max(spreads)
    axis_ratio = _tuple_rounded([s/max_sp for s in spreads], tol)

    return dist_quant, radial_quant, axis_ratio


def _mesh_canonical_topology_hash(transform_name):
    """Strict order-independent topology fingerprint for safe/geometry modes."""
    fn, _ = _get_mesh_fn(transform_name)
    if fn is None:
        return ""
    try:
        vertex_count = fn.numVertices
        edge_count = fn.numEdges
        face_count = fn.numPolygons
        valences = [0] * vertex_count
        edge_pairs = []
        for eid in range(edge_count):
            v1, v2 = fn.getEdgeVertices(eid)
            if 0 <= v1 < vertex_count:
                valences[v1] += 1
            if 0 <= v2 < vertex_count:
                valences[v2] += 1
            edge_pairs.append((min(v1, v2), max(v1, v2)))
        face_degrees = []
        face_valence_rings = []
        edge_face_use = defaultdict(int)
        for fid in range(face_count):
            verts = list(fn.getPolygonVertices(fid))
            face_degrees.append(len(verts))
            face_valence_rings.append(tuple(sorted(valences[v] for v in verts)))
            for i, v1 in enumerate(verts):
                v2 = verts[(i + 1) % len(verts)]
                edge_face_use[(min(v1, v2), max(v1, v2))] += 1
        edge_valence_pairs = sorted((min(valences[a], valences[b]), max(valences[a], valences[b])) for a, b in edge_pairs)
        boundary_edges = sum(1 for use in edge_face_use.values() if use == 1)
        nonmanifold_edges = sum(1 for use in edge_face_use.values() if use > 2)
        return _hash_blob(
            vertex_count, edge_count, face_count,
            tuple(sorted(valences)),
            tuple(sorted(face_degrees)),
            tuple(edge_valence_pairs),
            tuple(sorted(face_valence_rings)),
            boundary_edges, nonmanifold_edges,
        )
    except Exception:
        return ""


def _mesh_strict_signature_hash(transform_name, strict_tol=0.001):
    sig = _compute_signature(transform_name, strict_tol=strict_tol)
    return sig.strict_hash if sig else ""


def _restore_display_layers(node, encoded_layers):
    if not encoded_layers:
        return
    layers = [l for l in str(encoded_layers).split(";") if l]
    for layer in layers:
        try:
            if not _exists(layer):
                cmds.createDisplayLayer(name=layer, empty=True)
            cmds.editDisplayLayerMembers(layer, node, noRecurse=True)
        except Exception:
            pass


def _display_layers_for(node):
    try:
        return [l for l in (cmds.listConnections(node, type="displayLayer") or []) if l != "defaultLayer"]
    except Exception:
        return []


def _is_instanced_mesh_transform(node):
    try:
        for sh in (cmds.listRelatives(node, shapes=True, fullPath=True, noIntermediate=True) or []):
            if cmds.nodeType(sh) != "mesh":
                continue
            parents = cmds.listRelatives(sh, allParents=True, fullPath=True) or []
            if len(parents) > 1:
                return True
    except Exception:
        pass
    return False


def _duplicate_independent_transform(node, name):
    """Duplicate a transform and verify the result owns non-instanced shapes."""
    dup = cmds.duplicate(node, rr=True, instanceLeaf=False, name=name)[0]
    if _is_instanced_mesh_transform(dup):
        # Second duplicate usually materializes unique shapes from an instanced DAG.
        dup2 = cmds.duplicate(dup, rr=True, instanceLeaf=False, name=name + "_unique")[0]
        try:
            cmds.delete(dup)
        except Exception:
            pass
        dup = dup2
    if _is_instanced_mesh_transform(dup):
        raise RuntimeError("duplicate is still sharing an instanced mesh shape")
    return dup


# ---------------------------------------------------------------------------
# Debug color shaders
# ---------------------------------------------------------------------------
def _hsv_to_rgb(h, s, v):
    h = float(h or 0.0) % 1.0
    s = max(0.0, min(1.0, float(s or 0.0)))
    v = max(0.0, min(1.0, float(v or 0.0)))
    if s <= 0.0:
        return (v, v, v)
    i = int(h * 6.0)
    f = h * 6.0 - i
    p = v * (1.0 - s)
    q = v * (1.0 - f * s)
    t = v * (1.0 - (1.0 - f) * s)
    i = i % 6
    if i == 0:
        return (v, t, p)
    if i == 1:
        return (q, v, p)
    if i == 2:
        return (p, v, t)
    if i == 3:
        return (p, q, v)
    if i == 4:
        return (t, p, v)
    return (v, p, q)


def _stable_color_from_key(key):
    """Deterministic debug color from a key.

    Kept as a fallback for one-off calls.  Group shader assignment now uses
    _stable_palette_color() so several groups visible at once are intentionally
    spaced apart instead of only relying on hash luck.
    """
    key = str(key or "InstanceCleaner")
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()
    hue_seed = int(digest[0:8], 16) / float(0xFFFFFFFF)
    sat_seed = int(digest[8:12], 16) / float(0xFFFF)
    val_seed = int(digest[12:16], 16) / float(0xFFFF)
    hue = (hue_seed + 0.61803398875) % 1.0
    sat = 0.56 + 0.22 * sat_seed
    val = 0.76 + 0.16 * val_seed
    return _hsv_to_rgb(hue, sat, val)


def _stable_palette_color(index, total, key):
    """Readable, deterministic color with group-to-group hue separation."""
    index = int(max(0, index or 0))
    total = int(max(1, total or 1))
    key = str(key or index)
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()
    jitter = (int(digest[0:4], 16) / float(0xFFFF) - 0.5) * 0.055
    sat_seed = int(digest[4:8], 16) / float(0xFFFF)
    val_seed = int(digest[8:12], 16) / float(0xFFFF)
    # Golden-ratio stepping gives strong separation even when group count changes.
    hue = (0.11 + index * 0.61803398875 + jitter) % 1.0
    sat = 0.60 + 0.18 * sat_seed
    val = 0.78 + 0.14 * val_seed
    return _hsv_to_rgb(hue, sat, val)


def _dim_color(rgb, factor=0.45):
    factor = max(0.0, min(1.0, float(factor or 0.45)))
    return tuple(max(0.0, min(1.0, float(c) * factor)) for c in rgb)


def _shader_name_for_group(display_name, key, backup=False):
    """Return a deterministic shader name that stays unique per group.

    The readable display name alone is not enough because two rebuilt/merged
    groups can temporarily share the same visible name.  The internal id hash
    prevents different groups from accidentally sharing/recoloring the same
    Lambert shading group.
    """
    base = _safe_name(display_name) or "GROUP"
    suffix = _hash_blob(str(key or base))[:8]
    name = "IC_COLOR_{}_{}".format(base, suffix)
    if backup:
        name += "_BKP"
    return name


def _ensure_lambert_shader(shader_name, color):
    shader_name = _safe_name(shader_name)
    if not shader_name:
        shader_name = "IC_COLOR_SHADER"
    if not _exists(shader_name):
        try:
            shader = cmds.shadingNode("lambert", asShader=True, name=shader_name)
        except Exception:
            shader = None
    else:
        shader = shader_name
    if not shader or not _exists(shader):
        return None

    sg_name = _safe_name(shader_name + "SG")
    if not _exists(sg_name):
        try:
            sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=sg_name)
        except Exception:
            sg = None
    else:
        sg = sg_name
    if not sg or not _exists(sg):
        return None

    try:
        if not cmds.isConnected(shader + ".outColor", sg + ".surfaceShader"):
            cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader", force=True)
    except Exception:
        pass
    try:
        cmds.setAttr(shader + ".color", float(color[0]), float(color[1]), float(color[2]), type="double3")
    except Exception:
        pass
    try:
        cmds.setAttr(shader + ".diffuse", 0.85)
    except Exception:
        pass
    return sg


def _mesh_shader_targets(nodes):
    """Return DAG targets for shader assignment.

    Assigning a shadingEngine directly to the mesh shape of an instanced object
    can recolor every DAG instance that shares that shape.  Assigning the
    transform DAG path lets Maya create/use the proper per-instance object-group
    membership when possible, while still falling back to shapes for raw mesh
    shape inputs.
    """
    targets = []
    for node in _dedupe_keep_order(nodes or []):
        if not _exists(node):
            continue
        try:
            if cmds.nodeType(node) == "mesh":
                parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
                targets.append(_long(parents[0]) if parents else _long(node))
                continue
            if _has_mesh_shape(node):
                targets.append(_long(node))
        except Exception:
            continue
    return _dedupe_keep_order(targets)


def _mesh_shapes_for_shader(nodes):
    # Kept for compatibility with any external calls, but shader assignment now
    # uses transform DAG paths through _mesh_shader_targets().
    shapes = []
    for node in _dedupe_keep_order(nodes or []):
        if not _exists(node):
            continue
        try:
            if cmds.nodeType(node) == "mesh":
                shapes.append(_long(node))
                continue
            for shape in (cmds.listRelatives(node, shapes=True, fullPath=True, noIntermediate=True) or []):
                if cmds.nodeType(shape) == "mesh":
                    shapes.append(shape)
        except Exception:
            continue
    return _dedupe_keep_order(shapes)


def _assign_shader_to_nodes(nodes, shading_group):
    if not shading_group or not _exists(shading_group):
        return 0
    targets = _mesh_shader_targets(nodes)
    if not targets:
        return 0

    # Fast path: large instance groups can be very slow if each DAG path is
    # assigned one by one. Batch assignment is safe when Maya accepts it, and we
    # still keep the guarded per-node fallback below for edge cases.
    try:
        cmds.sets(targets, edit=True, forceElement=shading_group)
        return len(targets)
    except Exception:
        pass

    assigned = 0
    for target in targets:
        try:
            cmds.sets(target, edit=True, forceElement=shading_group)
            assigned += 1
        except Exception:
            # Do NOT fall back to the shared mesh shape when this transform is an
            # instance. Shape-level assignment would recolor every DAG instance
            # using that shape, which is exactly the bug that made different
            # groups appear to share one material.
            if _is_instanced_mesh_transform(target):
                cmds.warning("[IC] Shader assign skipped on instanced DAG {} because per-instance assignment failed.".format(_short(target)))
                continue
            fallback_shapes = _mesh_shapes_for_shader([target])
            fallback_ok = False
            for shape in fallback_shapes:
                try:
                    cmds.sets(shape, edit=True, forceElement=shading_group)
                    fallback_ok = True
                except Exception:
                    pass
            if fallback_ok:
                assigned += 1
            else:
                cmds.warning("[IC] Shader assign failed on {}".format(_short(target)))
    return assigned


def _shading_groups_for_node(node):
    """Return renderable shading groups currently assigned to a transform/shape.

    This intentionally stores object-level material state. It is not a complete
    per-face material serializer, but it is safe and fast for restoring debug
    colors on most environment assets.
    """
    groups = []
    for target in _mesh_shader_targets([node]) + _mesh_shapes_for_shader([node]):
        if not target or not _exists(target):
            continue
        try:
            sets = cmds.listSets(object=target) or []
        except Exception:
            sets = []
        for sg in sets:
            try:
                if sg and cmds.nodeType(sg) == "shadingEngine" and sg not in groups:
                    groups.append(sg)
            except Exception:
                pass
    return groups


_DEFAULT_SHADING_GROUPS = set(("initialShadingGroup", "initialParticleSE"))


def _is_default_shading_group(sg):
    return _short(sg) in _DEFAULT_SHADING_GROUPS


def _material_label_for_shading_group(sg):
    """Return a readable material/shading group label for UI status text."""
    if not sg or not _exists(sg):
        return ""
    try:
        shader = cmds.listConnections(sg + ".surfaceShader", source=True, destination=False) or []
        if shader:
            return _short(shader[0])
    except Exception:
        pass
    return _short(sg)


def _preferred_master_shading_group(master):
    """Choose the shader that should be propagated from a master to instances.

    Maya often reports both initialShadingGroup and the real visible material on
    a master DAG path. The previous logic treated that as multi-material and did
    nothing. For the production button, one non-default SG plus initialShadingGroup
    means: use the non-default SG.
    """
    result = {
        "sg": None,
        "label": "",
        "all_count": 0,
        "non_default_count": 0,
        "mode": "none",
    }
    all_sgs = [sg for sg in _shading_groups_for_node(master) if sg and _exists(sg)]
    object_sgs = [sg for sg in _object_shading_groups_for_node(master) if sg and _exists(sg)]
    all_sgs = _dedupe_keep_order(all_sgs)
    object_sgs = _dedupe_keep_order(object_sgs)
    non_default = [sg for sg in all_sgs if not _is_default_shading_group(sg)]
    object_non_default = [sg for sg in object_sgs if not _is_default_shading_group(sg)]

    result["all_count"] = len(all_sgs)
    result["non_default_count"] = len(non_default)

    chosen = None
    if len(object_non_default) == 1:
        chosen = object_non_default[0]
        result["mode"] = "object_non_default"
    elif len(non_default) == 1:
        chosen = non_default[0]
        result["mode"] = "single_non_default"
    elif len(object_sgs) == 1:
        chosen = object_sgs[0]
        result["mode"] = "object_single"
    elif len(non_default) > 1:
        result["mode"] = "multi_non_default"
    elif all_sgs:
        chosen = all_sgs[0]
        result["mode"] = "default"

    if chosen and _exists(chosen):
        result["sg"] = chosen
        result["label"] = _material_label_for_shading_group(chosen)
    return result


def _object_shading_groups_for_node(node):
    """Return shading groups assigned directly to a transform DAG path.

    This is intentionally narrower than _shading_groups_for_node(): it ignores
    mesh-shape/component memberships so we can detect per-instance material
    overrides without destroying shared per-face assignments on the master shape.
    """
    groups = []
    for target in _mesh_shader_targets([node]):
        if not target or not _exists(target):
            continue
        try:
            sets = cmds.listSets(object=target) or []
        except Exception:
            sets = []
        for sg in sets:
            try:
                if sg and cmds.nodeType(sg) == "shadingEngine" and sg not in groups:
                    groups.append(sg)
            except Exception:
                pass
    return groups


def _remove_object_shader_assignments(nodes):
    """Remove only direct transform-level shader assignments.

    Shape/component shader memberships are left untouched. For Maya instances,
    this lets an instance fall back to the shared master shape's assignments,
    which preserves multi-material masters better than force-assigning one SG.
    """
    removed = 0
    for target in _mesh_shader_targets(nodes):
        if not target or not _exists(target):
            continue
        for sg in _object_shading_groups_for_node(target):
            try:
                cmds.sets(target, edit=True, remove=sg)
                removed += 1
            except Exception:
                pass
    return removed


def _save_original_shaders_for_nodes(nodes, overwrite=False):
    saved = 0
    for node in _mesh_shader_targets(nodes):
        if not _exists(node):
            continue
        if (not overwrite) and cmds.attributeQuery(ATTR_IC_ORIG_SHADERS, node=node, exists=True):
            continue
        sgs = _shading_groups_for_node(node)
        if not sgs:
            sgs = ["initialShadingGroup"] if _exists("initialShadingGroup") else []
        _add_ic_attr(node, ATTR_IC_ORIG_SHADERS, ";".join(sgs), "string")
        saved += 1
    return saved


def _restore_original_shaders_for_nodes(nodes, remove_attr=False):
    restored = 0
    missing = 0
    for node in _mesh_shader_targets(nodes):
        if not _exists(node):
            continue
        encoded = _get_ic_attr(node, ATTR_IC_ORIG_SHADERS, "")
        sgs = [sg for sg in str(encoded or "").split(";") if sg and _exists(sg)]
        if not sgs and _exists("initialShadingGroup"):
            sgs = ["initialShadingGroup"]
        if not sgs:
            missing += 1
            continue
        if _assign_shader_to_nodes([node], sgs[0]):
            restored += 1
            if remove_attr:
                try:
                    if cmds.attributeQuery(ATTR_IC_ORIG_SHADERS, node=node, exists=True):
                        cmds.deleteAttr(node + "." + ATTR_IC_ORIG_SHADERS)
                except Exception:
                    pass
        else:
            missing += 1
    return {"restored": restored, "missing": missing}


def _mesh_compatibility_warning(source_mesh, target_mesh):
    """Return a warning string when two masters look unsafe to merge."""
    if not (_exists(source_mesh) and _exists(target_mesh)):
        return "missing mesh"
    try:
        src_key = _mesh_count_key(source_mesh)
        tgt_key = _mesh_count_key(target_mesh)
        if src_key and tgt_key and src_key != tgt_key:
            return "different topology counts {} -> {}".format(src_key, tgt_key)
    except Exception:
        pass
    try:
        _src_center, src_size = _world_bbox(source_mesh)
        _tgt_center, tgt_size = _world_bbox(target_mesh)
        ratios = []
        for a, b in zip(src_size, tgt_size):
            ratios.append(float(max(a, b, 1e-8)) / float(max(min(a, b), 1e-8)))
        if max(ratios or [1.0]) > 1.18:
            return "different bbox ratio {:.2f}".format(max(ratios))
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# MeshSignature
# ---------------------------------------------------------------------------
class MeshSignature(object):
    __slots__ = (
        "transform", "vertex_count", "edge_count", "face_count",
        "bbox_size", "edge_quant", "edge_hist", "face_quant", "face_hist",
        "valence_hist", "poly_degree_hist", "distance_quant", "radial_quant",
        "axis_ratio", "canonical_hash", "strict_hash", "loose_hash",
    )

    def __init__(self):
        for s in self.__slots__:
            setattr(self, s, "" if s in ("transform","canonical_hash","strict_hash","loose_hash") else
                    (1.,1.,1.) if s == "bbox_size" else
                    0 if s in ("vertex_count","edge_count","face_count") else
                    tuple())


def _compute_signature(transform_name, strict_tol=0.001):
    fn, _ = _get_mesh_fn(transform_name)
    if fn is None:
        return None

    sig               = MeshSignature()
    sig.transform     = _long(transform_name)
    sig.vertex_count  = fn.numVertices
    sig.edge_count    = fn.numEdges
    sig.face_count    = fn.numPolygons
    sig.bbox_size     = _bbox_size_from_fn(fn)

    points   = fn.getPoints(om2.MSpace.kObject)
    valences = [0] * sig.vertex_count

    edge_lengths = []
    for eid in range(sig.edge_count):
        try:
            v1, v2 = fn.getEdgeVertices(eid)
            l = (om2.MVector(points[v1]) - om2.MVector(points[v2])).length()
            edge_lengths.append(l)
            if 0 <= v1 < len(valences): valences[v1] += 1
            if 0 <= v2 < len(valences): valences[v2] += 1
        except Exception:
            pass

    avg_edge = max(sum(edge_lengths)/max(1., float(len(edge_lengths))), 1e-8)
    edge_norm = [l/avg_edge for l in edge_lengths]

    face_areas  = []
    poly_degrees = []
    for fid in range(sig.face_count):
        try:
            verts = fn.getPolygonVertices(fid)
            poly_degrees.append(len(verts))
            face_areas.append(_polygon_area([points[v] for v in verts]))
        except Exception:
            pass

    avg_area  = max(sum(face_areas)/max(1., float(len(face_areas))), 1e-8)
    area_norm = [a/avg_area for a in face_areas]

    sig.edge_quant       = _sample_quantiles(edge_norm,    count=32, tol=strict_tol)
    sig.edge_hist        = _histogram(edge_norm,            bins=20,  max_value=3.)
    sig.face_quant       = _sample_quantiles(area_norm,    count=20, tol=strict_tol)
    sig.face_hist        = _histogram(area_norm,            bins=16,  max_value=4.)
    sig.valence_hist     = _histogram(valences,             bins=10,  max_value=10.)
    sig.poly_degree_hist = _histogram(poly_degrees,         bins=8,   max_value=8.)

    sig.distance_quant, sig.radial_quant, sig.axis_ratio = \
        _point_cloud_descriptors(points, tol=strict_tol)
    sig.canonical_hash = _mesh_canonical_topology_hash(transform_name)

    sig.strict_hash = _hash_blob(
        sig.canonical_hash,
        sig.vertex_count, sig.edge_count, sig.face_count,
        sig.edge_quant, sig.face_quant,
        sig.valence_hist, sig.poly_degree_hist,
        sig.distance_quant, sig.radial_quant,
    )

    loose_e = _sample_quantiles(edge_norm,          count=12, tol=0.02)
    loose_f = _sample_quantiles(area_norm,          count=8,  tol=0.02)
    loose_d = _sample_quantiles(sig.distance_quant, count=12, tol=0.02)
    loose_r = _sample_quantiles(sig.radial_quant,   count=12, tol=0.02)

    # Keep loose_hash independent from strict_hash.
    sig.loose_hash = _hash_blob(
        sig.vertex_count, sig.edge_count, sig.face_count,
        loose_e, loose_f,
        sig.poly_degree_hist,
        loose_d, loose_r,
    )

    return sig


def _compute_light_signature(transform_name):
    """Minimal signature for UVOptimizer-style scans (topology/geometry/exact)."""
    fn, _ = _get_mesh_fn(transform_name)
    if fn is None:
        return None

    sig = MeshSignature()
    sig.transform    = _long(transform_name)
    sig.vertex_count = fn.numVertices
    sig.edge_count   = fn.numEdges
    sig.face_count   = fn.numPolygons
    sig.bbox_size    = _bbox_size_from_fn(fn)

    # Compute both hashes explicitly. In light mode they share the same value,
    # but remain separate attributes.
    sig.canonical_hash = _mesh_canonical_topology_hash(transform_name)
    topo_hash = sig.canonical_hash or _hash_blob(sig.vertex_count, sig.edge_count, sig.face_count)
    sig.strict_hash = topo_hash
    sig.loose_hash  = topo_hash
    return sig


# ---------------------------------------------------------------------------
# UVOptimizer-style mesh comparison helpers
# ---------------------------------------------------------------------------
def _mesh_compare_hash(mesh_transform, ignore_scale=False):
    if not _exists(mesh_transform) or not _has_mesh_shape(mesh_transform):
        return None
    try:
        vtx_count  = int(cmds.polyEvaluate(mesh_transform, vertex=True) or 0)
        edge_count = int(cmds.polyEvaluate(mesh_transform, edge=True) or 0)
        face_count = int(cmds.polyEvaluate(mesh_transform, face=True) or 0)
        area       = float(cmds.polyEvaluate(mesh_transform, worldArea=True) or 0.0)
        bbox       = cmds.exactWorldBoundingBox(mesh_transform)
        volume     = float((bbox[3]-bbox[0]) * (bbox[4]-bbox[1]) * (bbox[5]-bbox[2]))
        if ignore_scale:
            width  = abs(float(bbox[3] - bbox[0]))
            height = abs(float(bbox[4] - bbox[1]))
            depth  = abs(float(bbox[5] - bbox[2]))
            bbox_size = max(width, height, depth, 1e-8)
            area   = area / (bbox_size * bbox_size) if abs(area) > 1e-12 else 0.0
            volume = volume / (bbox_size * bbox_size * bbox_size) if abs(volume) > 1e-12 else 0.0
        return (vtx_count, edge_count, face_count, area, volume)
    except Exception:
        return None

def _mesh_count_key(mesh_transform):
    """Cheap topology prefilter key used before expensive canonical comparisons."""
    fn, _ = _get_mesh_fn(mesh_transform)
    if fn is None:
        return None
    try:
        return (int(fn.numVertices), int(fn.numEdges), int(fn.numPolygons))
    except Exception:
        return None

def _compare_mesh_topology(mesh1, mesh2, ignore_scale=False, tolerance=0.01):
    """Compare canonical topology, not just vertex/edge/face counts."""
    h1 = _mesh_canonical_topology_hash(mesh1)
    h2 = _mesh_canonical_topology_hash(mesh2)
    return bool(h1 and h2 and h1 == h2)


def _sample_indices_even(count, max_points):
    count = int(count or 0)
    max_points = max(3, int(max_points or 3))
    if count <= max_points:
        return list(range(count))
    if max_points <= 1:
        return [0]
    step = float(count - 1) / float(max_points - 1)
    return [min(count - 1, int(round(i * step))) for i in range(max_points)]


def _normalized_points_distance_signature(points,
                                          max_points=DEEP_GEOMETRY_VERIFY_POINTS,
                                          quantiles=DEEP_GEOMETRY_VERIFY_QUANTILES,
                                          round_tol=DEEP_GEOMETRY_VERIFY_ROUND_TOL):
    """Rotation/translation/scale-invariant deep shape signature.

    It is intentionally used only after cheap topology + descriptor filters.
    This makes Geometry mode less likely to accept unrelated meshes that have
    the same vertex/edge/face counts and similar global histograms.
    """
    if not points:
        return tuple()
    try:
        raw = [(float(p[0]), float(p[1]), float(p[2])) for p in points]
    except Exception:
        try:
            raw = [(float(p.x), float(p.y), float(p.z)) for p in points]
        except Exception:
            return tuple()
    if len(raw) < 3:
        return tuple()

    cx = sum(p[0] for p in raw) / float(len(raw))
    cy = sum(p[1] for p in raw) / float(len(raw))
    cz = sum(p[2] for p in raw) / float(len(raw))
    centered = [(p[0] - cx, p[1] - cy, p[2] - cz) for p in raw]
    radii = [math.sqrt(x*x + y*y + z*z) for x, y, z in centered]
    scale = max(max(radii), 1e-8)

    normalized = [(centered[i][0] / scale,
                   centered[i][1] / scale,
                   centered[i][2] / scale,
                   radii[i] / scale) for i in range(len(centered))]

    # Select by radial order to be more stable when vertex order differs, while
    # pairwise distances keep the final signature rotation-invariant.
    normalized.sort(key=lambda p: (round(p[3], 6), round(p[0], 6), round(p[1], 6), round(p[2], 6)))
    idxs = _sample_indices_even(len(normalized), max_points)
    sample = [normalized[i] for i in idxs]

    dists = []
    for i in range(len(sample)):
        ax, ay, az, _ar = sample[i]
        for j in range(i + 1, len(sample)):
            bx, by, bz, _br = sample[j]
            dx = ax - bx
            dy = ay - by
            dz = az - bz
            dists.append(math.sqrt(dx*dx + dy*dy + dz*dz))
    return _sample_quantiles(dists, count=quantiles, tol=round_tol)


def _deep_geometry_signature(mesh_transform, cache=None):
    key = _long(mesh_transform)
    if cache is not None and key in cache:
        return cache[key]
    pts = _points_array(key, om2.MSpace.kObject)
    sig = _normalized_points_distance_signature(pts)
    if cache is not None:
        cache[key] = sig
    return sig


def _deep_geometry_match(mesh1, mesh2, tolerance=0.01, cache=None):
    a = _deep_geometry_signature(mesh1, cache=cache)
    b = _deep_geometry_signature(mesh2, cache=cache)
    if not a or not b:
        return False
    tol = max(float(tolerance), 0.0)
    # Bound the deep check so a very high UI tolerance does not make Geometry
    # mode accept broadly similar-but-different props.
    allowed = max(0.004, min(0.022, tol * 0.055))
    return _avg_abs_delta(a, b) <= allowed


def _compare_mesh_geometry(mesh1, mesh2, ignore_scale=False, tolerance=0.01):
    """Compare topology plus strict normalized geometry descriptors.

    Counts/area/volume alone are intentionally insufficient because many
    unrelated meshes can share those values. The final deep distance signature
    check reduces false positives without requiring identical vertex order.
    """
    if not _compare_mesh_topology(mesh1, mesh2, ignore_scale=ignore_scale, tolerance=tolerance):
        return False
    s1 = _compute_signature(mesh1, strict_tol=max(min(float(tolerance), 0.02), 0.0005))
    s2 = _compute_signature(mesh2, strict_tol=max(min(float(tolerance), 0.02), 0.0005))
    if not s1 or not s2:
        return False
    tol = max(float(tolerance), 0.0)
    if not (_avg_abs_delta(s1.edge_quant, s2.edge_quant) <= max(tol * 0.25, 0.003) and
            _avg_abs_delta(s1.face_quant, s2.face_quant) <= max(tol * 0.25, 0.003) and
            _avg_abs_delta(s1.distance_quant, s2.distance_quant) <= max(tol * 0.20, 0.004) and
            _avg_abs_delta(s1.radial_quant, s2.radial_quant) <= max(tol * 0.20, 0.004)):
        return False
    return _deep_geometry_match(mesh1, mesh2, tolerance=tolerance)




# ---------------------------------------------------------------------------
# Robust Similar method (from select_similar.py, adapted for Instance Cleaner)
# ---------------------------------------------------------------------------
def _rs_relative_diff(a, b):
    if a == b:
        return 0.0
    denom = float(max(abs(a), abs(b), 1))
    return abs(float(a) - float(b)) / denom


def _rs_bounded_relative_diff(a, b):
    if abs(a) < ROBUST_EPSILON and abs(b) < ROBUST_EPSILON:
        return 0.0
    denom = max(abs(a), abs(b), ROBUST_EPSILON)
    return min(abs(a - b) / denom, 1.0)


def _rs_sample_indices(count, max_samples):
    count = int(count or 0)
    if count <= 0:
        return []
    if max_samples is None or max_samples <= 0 or count <= max_samples:
        return list(range(count))
    if max_samples == 1:
        return [0]
    step = float(count - 1) / float(max_samples - 1)
    indices = [int(round(i * step)) for i in range(max_samples)]
    seen = set()
    out = []
    for idx in indices:
        idx = max(0, min(count - 1, int(idx)))
        if idx not in seen:
            seen.add(idx)
            out.append(idx)
    return out


def _rs_normalized_int_histogram(values, max_bin):
    hist = [0.0] * (int(max_bin) + 2)
    if not values:
        return tuple(hist)
    for value in values:
        idx = int(value)
        if idx < 0:
            idx = 0
        elif idx > max_bin:
            idx = max_bin + 1
        hist[idx] += 1.0
    inv = 1.0 / float(max(1, len(values)))
    return tuple(v * inv for v in hist)


def _rs_float_histogram(values, bins, min_value, max_value):
    bins = int(bins or 0)
    hist = [0.0] * bins
    if not values or bins <= 0:
        return tuple(hist)
    span = max(float(max_value) - float(min_value), ROBUST_EPSILON)
    inv_span = float(bins) / span
    valid_count = 0
    for value in values:
        if value is None:
            continue
        try:
            value = float(value)
        except Exception:
            continue
        if math.isnan(value) or math.isinf(value):
            continue
        if value <= min_value:
            idx = 0
        elif value >= max_value:
            idx = bins - 1
        else:
            idx = int((value - min_value) * inv_span)
            idx = max(0, min(bins - 1, idx))
        hist[idx] += 1.0
        valid_count += 1
    if valid_count > 0:
        inv = 1.0 / float(valid_count)
        hist = [v * inv for v in hist]
    return tuple(hist)


def _rs_hist_l1_distance(a, b):
    if not a or not b or len(a) != len(b):
        return 1.0
    return 0.5 * sum(abs(x - y) for x, y in zip(a, b))


def _rs_hist_cdf_distance(a, b):
    if not a or not b or len(a) != len(b):
        return 1.0
    ca = 0.0
    cb = 0.0
    total = 0.0
    for x, y in zip(a, b):
        ca += x
        cb += y
        total += abs(ca - cb)
    return total / float(len(a))


def _rs_polygon_area_from_points(vertex_ids, points):
    if not vertex_ids or len(vertex_ids) < 3:
        return 0.0
    p0 = points[vertex_ids[0]]
    area = 0.0
    for i in range(1, len(vertex_ids) - 1):
        p1 = points[vertex_ids[i]]
        p2 = points[vertex_ids[i + 1]]
        ax = p1.x - p0.x
        ay = p1.y - p0.y
        az = p1.z - p0.z
        bx = p2.x - p0.x
        by = p2.y - p0.y
        bz = p2.z - p0.z
        cx = ay * bz - az * by
        cy = az * bx - ax * bz
        cz = ax * by - ay * bx
        area += 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)
    return area


def _rs_symmetric_3x3_eigenvalues(a00, a01, a02, a11, a12, a22):
    p1 = a01 * a01 + a02 * a02 + a12 * a12
    if p1 < ROBUST_EPSILON:
        vals = [a00, a11, a22]
        vals.sort(reverse=True)
        return vals
    trace = a00 + a11 + a22
    q = trace / 3.0
    b00 = a00 - q
    b11 = a11 - q
    b22 = a22 - q
    p2 = b00 * b00 + b11 * b11 + b22 * b22 + 2.0 * p1
    p = math.sqrt(max(p2 / 6.0, ROBUST_EPSILON))
    c00 = b00 / p
    c01 = a01 / p
    c02 = a02 / p
    c11 = b11 / p
    c12 = a12 / p
    c22 = b22 / p
    det_c = (
        c00 * (c11 * c22 - c12 * c12)
        - c01 * (c01 * c22 - c12 * c02)
        + c02 * (c01 * c12 - c11 * c02)
    )
    r = max(-1.0, min(1.0, det_c / 2.0))
    phi = math.acos(r) / 3.0
    eig1 = q + 2.0 * p * math.cos(phi)
    eig3 = q + 2.0 * p * math.cos(phi + (2.0 * math.pi / 3.0))
    eig2 = 3.0 * q - eig1 - eig3
    vals = [eig1, eig2, eig3]
    vals.sort(reverse=True)
    return vals


def _rs_compute_centroid_and_size(points):
    count = len(points)
    if count <= 0:
        return None, 0.0
    sx = sy = sz = 0.0
    for p in points:
        sx += p.x
        sy += p.y
        sz += p.z
    inv_count = 1.0 / float(count)
    cx = sx * inv_count
    cy = sy * inv_count
    cz = sz * inv_count
    radius_sq_sum = 0.0
    for p in points:
        dx = p.x - cx
        dy = p.y - cy
        dz = p.z - cz
        radius_sq_sum += dx * dx + dy * dy + dz * dz
    size_rms = math.sqrt(radius_sq_sum * inv_count)
    return (cx, cy, cz), size_rms


def _rs_covariance_ratios(points, centroid, size_rms, sample_ids):
    if not sample_ids or size_rms < ROBUST_EPSILON:
        return (0.0, 0.0, 0.0)
    cx, cy, cz = centroid
    inv_size = 1.0 / size_rms
    xx = xy = xz = yy = yz = zz = 0.0
    count = 0.0
    for idx in sample_ids:
        p = points[idx]
        x = (p.x - cx) * inv_size
        y = (p.y - cy) * inv_size
        z = (p.z - cz) * inv_size
        xx += x * x
        xy += x * y
        xz += x * z
        yy += y * y
        yz += y * z
        zz += z * z
        count += 1.0
    if count < ROBUST_EPSILON:
        return (0.0, 0.0, 0.0)
    inv = 1.0 / count
    eig = _rs_symmetric_3x3_eigenvalues(xx * inv, xy * inv, xz * inv, yy * inv, yz * inv, zz * inv)
    total = sum(abs(v) for v in eig)
    if total < ROBUST_EPSILON:
        return (0.0, 0.0, 0.0)
    return tuple(max(v, 0.0) / total for v in eig)


def _rs_compute_world_size_rms(dag_path):
    try:
        fn = om2.MFnMesh(dag_path)
        points = fn.getPoints(om2.MSpace.kWorld)
        _centroid, size_rms = _rs_compute_centroid_and_size(points)
        return size_rms
    except Exception:
        try:
            fn = om2.MFnMesh(dag_path)
            points = fn.getPoints(om2.MSpace.kObject)
            _centroid, size_rms = _rs_compute_centroid_and_size(points)
            return size_rms
        except Exception:
            return 0.0


def _rs_real_size_compatible(ref_world_size, cand_world_size,
                             min_ratio=ROBUST_REAL_SIZE_RATIO_MIN,
                             max_ratio=ROBUST_REAL_SIZE_RATIO_MAX):
    if ref_world_size < ROBUST_EPSILON or cand_world_size < ROBUST_EPSILON:
        return True, 1.0
    ratio = float(cand_world_size) / float(ref_world_size)
    if ratio < min_ratio:
        return False, ratio
    if ratio > max_ratio:
        return False, ratio
    return True, ratio


def _rs_get_polygon_counts(fn):
    try:
        polygon_counts, _polygon_connects = fn.getVertices()
        return [int(v) for v in polygon_counts]
    except Exception:
        counts = []
        for i in range(fn.numPolygons):
            try:
                counts.append(int(fn.polygonVertexCount(i)))
            except Exception:
                try:
                    counts.append(len(fn.getPolygonVertices(i)))
                except Exception:
                    counts.append(0)
        return counts


def _rs_compute_topology_signature(dag_path):
    fn = om2.MFnMesh(dag_path)
    face_counts = _rs_get_polygon_counts(fn)
    return {
        "num_vertices": int(fn.numVertices),
        "num_edges": int(fn.numEdges),
        "num_faces": int(fn.numPolygons),
        "face_sides_hist": _rs_normalized_int_histogram(face_counts, ROBUST_FACE_SIDE_HIST_MAX),
        "valence_hist": None,
        "boundary_edge_ratio": None,
    }


def _rs_ensure_detailed_topology_signature(signature, dag_path):
    if signature.get("valence_hist") is not None:
        return
    fn = om2.MFnMesh(dag_path)
    num_vertices = int(fn.numVertices)
    num_edges = int(fn.numEdges)
    valences = [0] * num_vertices
    boundary_edges = 0
    try:
        edge_it = om2.MItMeshEdge(dag_path)
        while not edge_it.isDone():
            try:
                v0 = int(edge_it.vertexId(0))
                v1 = int(edge_it.vertexId(1))
                if 0 <= v0 < num_vertices:
                    valences[v0] += 1
                if 0 <= v1 < num_vertices:
                    valences[v1] += 1
                try:
                    if edge_it.onBoundary():
                        boundary_edges += 1
                except Exception:
                    pass
            except Exception:
                pass
            edge_it.next()
    except Exception:
        for edge_id in range(num_edges):
            try:
                v0, v1 = fn.getEdgeVertices(edge_id)
                if 0 <= v0 < num_vertices:
                    valences[v0] += 1
                if 0 <= v1 < num_vertices:
                    valences[v1] += 1
            except Exception:
                pass
    signature["valence_hist"] = _rs_normalized_int_histogram(valences, ROBUST_VALENCE_HIST_MAX)
    signature["boundary_edge_ratio"] = float(boundary_edges) / float(max(num_edges, 1))


def _rs_topology_basic_compatible(ref_topo, cand_topo,
                                  tolerance_topology=ROBUST_TOLERANCE_TOPOLOGY,
                                  tolerance_vertex_count=ROBUST_TOLERANCE_VERTEX_COUNT):
    if _rs_relative_diff(ref_topo["num_vertices"], cand_topo["num_vertices"]) > tolerance_vertex_count:
        return False
    if _rs_relative_diff(ref_topo["num_edges"], cand_topo["num_edges"]) > tolerance_topology:
        return False
    if _rs_relative_diff(ref_topo["num_faces"], cand_topo["num_faces"]) > tolerance_topology:
        return False
    face_sides_distance = _rs_hist_l1_distance(ref_topo["face_sides_hist"], cand_topo["face_sides_hist"])
    return face_sides_distance <= tolerance_topology


def _rs_topology_compatible(ref_topo, cand_topo, ref_dag, cand_dag,
                            tolerance_topology=ROBUST_TOLERANCE_TOPOLOGY,
                            tolerance_vertex_count=ROBUST_TOLERANCE_VERTEX_COUNT):
    if not _rs_topology_basic_compatible(ref_topo, cand_topo, tolerance_topology, tolerance_vertex_count):
        return False
    if not ROBUST_USE_VALENCE_TOPOLOGY_FILTER:
        return True
    _rs_ensure_detailed_topology_signature(ref_topo, ref_dag)
    _rs_ensure_detailed_topology_signature(cand_topo, cand_dag)
    valence_distance = _rs_hist_l1_distance(ref_topo["valence_hist"], cand_topo["valence_hist"])
    if valence_distance > tolerance_topology * 2.0:
        return False
    boundary_diff = abs(float(ref_topo.get("boundary_edge_ratio") or 0.0) -
                        float(cand_topo.get("boundary_edge_ratio") or 0.0))
    return boundary_diff <= tolerance_topology * 2.0


def _rs_get_edge_vertex_pairs(fn, dag_path, edge_ids):
    pairs = []
    try:
        for edge_id in edge_ids:
            v0, v1 = fn.getEdgeVertices(int(edge_id))
            pairs.append((int(v0), int(v1)))
        return pairs
    except Exception:
        pairs = []
        wanted = set(int(e) for e in edge_ids)
        try:
            edge_it = om2.MItMeshEdge(dag_path)
            while not edge_it.isDone():
                edge_id = int(edge_it.index())
                if edge_id in wanted:
                    pairs.append((int(edge_it.vertexId(0)), int(edge_it.vertexId(1))))
                edge_it.next()
        except Exception:
            pass
    return pairs


def _rs_compute_geometry_signature(dag_path):
    fn = om2.MFnMesh(dag_path)
    points = fn.getPoints(om2.MSpace.kObject)
    num_vertices = int(fn.numVertices)
    num_edges = int(fn.numEdges)
    num_faces = int(fn.numPolygons)
    centroid, size_rms = _rs_compute_centroid_and_size(points)
    if centroid is None or size_rms < ROBUST_EPSILON:
        return None
    cx, cy, cz = centroid
    inv_size = 1.0 / size_rms
    inv_size_sq = inv_size * inv_size

    vertex_ids = _rs_sample_indices(num_vertices, ROBUST_MAX_VERTEX_SAMPLES)
    radial_values = []
    for vid in vertex_ids:
        p = points[vid]
        dx = p.x - cx
        dy = p.y - cy
        dz = p.z - cz
        radial_values.append(math.sqrt(dx * dx + dy * dy + dz * dz) * inv_size)
    radial_hist = _rs_float_histogram(radial_values, ROBUST_RADIAL_HIST_BINS, 0.0, ROBUST_RADIAL_HIST_MAX)

    edge_ids = _rs_sample_indices(num_edges, ROBUST_MAX_EDGE_SAMPLES)
    edge_pairs = _rs_get_edge_vertex_pairs(fn, dag_path, edge_ids)
    edge_lengths = []
    for v0, v1 in edge_pairs:
        if v0 < 0 or v1 < 0 or v0 >= num_vertices or v1 >= num_vertices:
            continue
        p0 = points[v0]
        p1 = points[v1]
        dx = p1.x - p0.x
        dy = p1.y - p0.y
        dz = p1.z - p0.z
        edge_lengths.append(math.sqrt(dx * dx + dy * dy + dz * dz) * inv_size)
    edge_length_hist = _rs_float_histogram(edge_lengths, ROBUST_EDGE_LENGTH_HIST_BINS, 0.0, ROBUST_EDGE_LENGTH_HIST_MAX)

    face_ids = _rs_sample_indices(num_faces, ROBUST_MAX_FACE_SAMPLES)
    face_areas = []
    for face_id in face_ids:
        try:
            verts = fn.getPolygonVertices(int(face_id))
            area = _rs_polygon_area_from_points(verts, points)
            face_areas.append(area * inv_size_sq)
        except Exception:
            pass
    face_area_hist = _rs_float_histogram(face_areas, ROBUST_FACE_AREA_HIST_BINS, 0.0, ROBUST_FACE_AREA_HIST_MAX)
    estimated_surface_area = (sum(face_areas) * float(num_faces) / float(len(face_areas))) if face_areas else 0.0
    covariance_ratios = _rs_covariance_ratios(points, centroid, size_rms, vertex_ids)

    return {
        "radial_hist": radial_hist,
        "edge_length_hist": edge_length_hist,
        "face_area_hist": face_area_hist,
        "covariance_ratios": covariance_ratios,
        "estimated_surface_area": estimated_surface_area,
        "size_rms": size_rms,
    }


def _rs_shape_distance(ref_geo, cand_geo):
    radial_d = _rs_hist_cdf_distance(ref_geo["radial_hist"], cand_geo["radial_hist"])
    edge_d = _rs_hist_cdf_distance(ref_geo["edge_length_hist"], cand_geo["edge_length_hist"])
    area_d = _rs_hist_cdf_distance(ref_geo["face_area_hist"], cand_geo["face_area_hist"])
    cov_d = 0.5 * sum(abs(a - b) for a, b in zip(ref_geo["covariance_ratios"], cand_geo["covariance_ratios"]))
    surface_d = _rs_bounded_relative_diff(ref_geo["estimated_surface_area"], cand_geo["estimated_surface_area"])
    score = 0.35 * radial_d + 0.25 * edge_d + 0.20 * area_d + 0.12 * cov_d + 0.08 * surface_d
    return score, {
        "score": score,
        "radial": radial_d,
        "edge": edge_d,
        "area": area_d,
        "covariance": cov_d,
        "surface": surface_d,
    }


def _rs_mesh_data(transform_name, cache=None):
    key = _long(transform_name)
    if cache is not None and key in cache:
        return cache[key]
    fn, dag = _get_mesh_fn(key)
    if not fn or not dag:
        data = None
    else:
        try:
            topo = _rs_compute_topology_signature(dag)
            geo = _rs_compute_geometry_signature(dag)
            data = {
                "transform": key,
                "dag": dag,
                "topology": topo,
                "geometry": geo,
                "world_size_rms": _rs_compute_world_size_rms(dag),
                "count_key": (int(fn.numVertices), int(fn.numEdges), int(fn.numPolygons)),
            }
        except Exception:
            data = None
    if cache is not None:
        cache[key] = data
    return data


def _real_size_range_compatible(ref_transform, cand_transform, ignore_scale=False,
                                real_size_ratio_max=ROBUST_REAL_SIZE_RATIO_MAX,
                                data_cache=None):
    """Shared world-size gate for every scan method.

    Ignore scale ON bypasses the filter.  Ignore scale OFF uses the UI's single
    Scale range value as max, and automatically derives min as 1 / max.  This
    keeps Robust Similar, Exact, Geometry, Topology and Find Selected aligned.
    """
    if ignore_scale or (not ROBUST_USE_REAL_SIZE_FILTER):
        return True
    ref = _rs_mesh_data(ref_transform, cache=data_cache)
    cand = _rs_mesh_data(cand_transform, cache=data_cache)
    if not ref or not cand:
        return True
    ratio_max = max(1.0, float(real_size_ratio_max or ROBUST_REAL_SIZE_RATIO_MAX))
    ratio_min = 1.0 / ratio_max if ratio_max > ROBUST_EPSILON else ROBUST_REAL_SIZE_RATIO_MIN
    ok, _ratio = _rs_real_size_compatible(
        ref.get("world_size_rms", 0.0), cand.get("world_size_rms", 0.0),
        min_ratio=ratio_min, max_ratio=ratio_max)
    return bool(ok)


def _rs_compare_meshes(ref_transform, cand_transform,
                       tolerance_shape=ROBUST_TOLERANCE_SHAPE_DEFAULT,
                       ignore_scale=False,
                       data_cache=None,
                       tolerance_topology=ROBUST_TOLERANCE_TOPOLOGY,
                       tolerance_vertex_count=ROBUST_TOLERANCE_VERTEX_COUNT,
                       real_size_ratio_max=ROBUST_REAL_SIZE_RATIO_MAX):
    ref = _rs_mesh_data(ref_transform, cache=data_cache)
    cand = _rs_mesh_data(cand_transform, cache=data_cache)
    if not ref or not cand or ref.get("geometry") is None or cand.get("geometry") is None:
        return False, None, None
    if not _rs_topology_compatible(ref["topology"], cand["topology"], ref["dag"], cand["dag"],
                                   tolerance_topology, tolerance_vertex_count):
        return False, None, None
    if (not ignore_scale) and ROBUST_USE_REAL_SIZE_FILTER:
        ratio_max = max(1.0, float(real_size_ratio_max or ROBUST_REAL_SIZE_RATIO_MAX))
        ratio_min = 1.0 / ratio_max if ratio_max > ROBUST_EPSILON else ROBUST_REAL_SIZE_RATIO_MIN
        size_ok, size_ratio = _rs_real_size_compatible(
            ref["world_size_rms"], cand["world_size_rms"],
            min_ratio=ratio_min, max_ratio=ratio_max)
        if not size_ok:
            return False, None, {"size_ratio": size_ratio}
    else:
        size_ratio = 1.0
    score, details = _rs_shape_distance(ref["geometry"], cand["geometry"])
    if details is None:
        details = {}
    details["size_ratio"] = size_ratio
    return score <= max(float(tolerance_shape), 0.0), score, details


def find_groups_robust_similarity_style(signatures,
                                        tolerance_shape=ROBUST_TOLERANCE_SHAPE_DEFAULT,
                                        ignore_scale=False,
                                        real_size_ratio_max=ROBUST_REAL_SIZE_RATIO_MAX,
                                        progress_cb=None,
                                        cancel_cb=None):
    groups = {}
    uniques = []
    remaining = sorted(list(signatures or []), key=lambda s: (
        s.vertex_count, s.edge_count, s.face_count, _short(s.transform).lower()
    ))
    processed = set()
    data_cache = {}
    group_index = 0
    total = max(1, len(remaining))

    vertex_buckets = defaultdict(list)
    for sig in remaining:
        vertex_buckets[int(sig.vertex_count)].append(sig)

    cancel_stride = 128
    for source_index, source in enumerate(remaining):
        if source.transform in processed:
            continue
        if cancel_cb and cancel_cb():
            raise ProcessCanceled()
        if progress_cb:
            progress_cb(percent=int(float(source_index) / float(total) * 100.0),
                        message="Robust Similar grouping {}".format(_short(source.transform)),
                        step="Robust Similar grouping", group="Robust Similar",
                        mesh=source.transform, current=source_index, total=total)

        source_data = _rs_mesh_data(source.transform, cache=data_cache)
        if not source_data:
            processed.add(source.transform)
            uniques.append(source.transform)
            continue

        candidates = vertex_buckets.get(int(source.vertex_count), [])
        matches = [source.transform]
        processed.add(source.transform)
        worst_distance = 0.0

        for c_index, candidate in enumerate(candidates):
            if candidate.transform in processed:
                continue
            if c_index % cancel_stride == 0 and cancel_cb and cancel_cb():
                raise ProcessCanceled()
            candidate_data = _rs_mesh_data(candidate.transform, cache=data_cache)
            if not candidate_data:
                continue
            # Cheap exact count prefilter; the robust topology stage still does the detailed hist/valence checks.
            if source_data.get("count_key") != candidate_data.get("count_key"):
                continue
            ok, distance, _details = _rs_compare_meshes(
                source.transform, candidate.transform,
                tolerance_shape=tolerance_shape,
                ignore_scale=ignore_scale,
                data_cache=data_cache,
                real_size_ratio_max=real_size_ratio_max,
            )
            if ok:
                matches.append(candidate.transform)
                processed.add(candidate.transform)
                if distance is not None:
                    worst_distance = max(worst_distance, float(distance))

        if len(matches) > 1:
            # The robust distance itself is already normalized and tiny when the match is strong.
            # Display it as a readable confidence percentage in the group card.
            similarity = max(0.0, min(1.0, 1.0 - worst_distance))
            iid = "robust_{:03d}_{}".format(group_index, _hash_blob(matches, tolerance_shape, ignore_scale)[:10])
            groups[iid] = {"meshes": matches, "score": similarity}
            group_index += 1
        else:
            uniques.extend(matches)

    if progress_cb:
        progress_cb(percent=100, message="Robust Similar grouping complete", step="Grouping", current=total, total=total)
    return groups, _dedupe_keep_order(uniques)


def _points_array(transform_name, space=om2.MSpace.kObject):
    fn, dag = _get_mesh_fn(transform_name)
    if not fn:
        return None
    try:
        pts = fn.getPoints(space)
        return [(float(p.x), float(p.y), float(p.z)) for p in pts]
    except Exception:
        if space != om2.MSpace.kObject and dag:
            try:
                pts = fn.getPoints(om2.MSpace.kObject)
                mat = dag.inclusiveMatrix()
                return [((p*mat).x, (p*mat).y, (p*mat).z) for p in pts]
            except Exception:
                return None
        return None


def _normalize_point_list(points):
    if not points:
        return points, 1.0
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    zs = [p[2] for p in points]
    center = ((min(xs)+max(xs))*0.5, (min(ys)+max(ys))*0.5, (min(zs)+max(zs))*0.5)
    size = max(max(xs)-min(xs), max(ys)-min(ys), max(zs)-min(zs), 1e-8)
    return [((p[0]-center[0])/size, (p[1]-center[1])/size, (p[2]-center[2])/size) for p in points], size


def _point_list_rms(a, b):
    if not a or not b or len(a) != len(b):
        return None
    total = 0.0
    for p, q in zip(a, b):
        dx = p[0] - q[0]
        dy = p[1] - q[1]
        dz = p[2] - q[2]
        total += dx*dx + dy*dy + dz*dz
    return math.sqrt(total / float(max(1, len(a))))


def _compare_mesh_exact(mesh1, mesh2, ignore_scale=False, tolerance=0.001):
    """Exact ordered-vertex comparison after best-fit translate/rotate/scale.

    Translation and rotation are always normalized via SVD.  ``ignore_scale``
    controls whether uniform scale differences are also accepted.  This still
    requires identical topology and vertex order.
    """
    if not _compare_mesh_topology(mesh1, mesh2, ignore_scale=ignore_scale):
        return False
    p1 = _points_array(mesh1, om2.MSpace.kObject)
    p2 = _points_array(mesh2, om2.MSpace.kObject)
    if not p1 or not p2 or len(p1) != len(p2):
        return False
    tol = max(float(tolerance), 0.0)
    if not HAS_NUMPY:
        if ignore_scale:
            p1, _ = _normalize_point_list(p1)
            p2, _ = _normalize_point_list(p2)
        rms = _point_list_rms(p1, p2)
        return bool(rms is not None and rms <= tol)
    src = np.array(p1, dtype=np.float64)
    dst = np.array(p2, dtype=np.float64)
    if not ignore_scale:
        # Unit-scale comparison: remove translation/rotation only, and fail on scale deltas.
        src_c = src.mean(axis=0)
        dst_c = dst.mean(axis=0)
        a = src - src_c
        b = dst - dst_c
        h = a.T.dot(b)
        try:
            u, _, vt = np.linalg.svd(h)
        except np.linalg.LinAlgError:
            return False
        r = vt.T.dot(u.T)
        if np.linalg.det(r) < 0:
            vt[-1, :] *= -1
            r = vt.T.dot(u.T)
        predicted = a.dot(r) + dst_c
        diff = predicted - dst
        rms = math.sqrt(float(np.mean(np.sum(diff * diff, axis=1))))
    else:
        matrix, rms = _svd_align(src, dst)
        if rms is None:
            return False
    _, size = _normalize_point_list(p2)
    return (rms / max(size, 1e-8)) <= tol

def _uvoptimizer_compare_score(mesh1, mesh2, method="exact", tolerance=0.30, ignore_scale=True):
    method = (method or "signature").lower()
    if method == "topology":
        return 1.0 if _compare_mesh_topology(mesh1, mesh2, ignore_scale=ignore_scale, tolerance=tolerance) else 0.0
    if method == "geometry":
        return 1.0 if _compare_mesh_geometry(mesh1, mesh2, ignore_scale=ignore_scale, tolerance=tolerance) else 0.0
    if method == "exact":
        return 1.0 if _compare_mesh_exact(mesh1, mesh2, ignore_scale=ignore_scale, tolerance=tolerance) else 0.0
    return 0.0


def find_groups_uvoptimizer_style(signatures, method="exact", tolerance=0.30, ignore_scale=True,
                                    real_size_ratio_max=ROBUST_REAL_SIZE_RATIO_MAX,
                                    progress_cb=None, cancel_cb=None):
    """Cached, bucketed grouping for UVOptimizer-like methods.

    The original implementation compared each source against almost every other
    mesh. This version first buckets meshes by count/topology, then runs the
    expensive Geometry/Exact checks only inside plausible buckets.
    """
    groups    = {}
    uniques   = []
    remaining = sorted(list(signatures or []), key=lambda s: (
        s.vertex_count, s.edge_count, s.face_count, _short(s.transform).lower()
    ))
    processed   = set()
    group_index = 0
    total       = max(1, len(remaining))
    method      = (method or "exact").lower()

    full_signature_cache = {}
    topology_hash_cache  = {}
    points_cache         = {}
    deep_sig_cache       = {}
    size_data_cache      = {}
    sig_tol = max(min(float(tolerance), 0.02), 0.0005)

    def _count_key(sig):
        return (int(sig.vertex_count), int(sig.edge_count), int(sig.face_count))

    def _topology_hash(sig):
        cached = getattr(sig, "canonical_hash", "")
        if cached:
            return cached
        key = sig.transform
        if key not in topology_hash_cache:
            topology_hash_cache[key] = _mesh_canonical_topology_hash(key)
        return topology_hash_cache[key]

    def _bucket_key(sig):
        topo = _topology_hash(sig)
        if topo:
            return (_count_key(sig), topo)
        return (_count_key(sig), "NO_TOPO_HASH")

    def _topology_match(a, b):
        if _count_key(a) != _count_key(b):
            return False
        ha = _topology_hash(a)
        hb = _topology_hash(b)
        return bool(ha and hb and ha == hb)

    def _full_signature(sig):
        key = sig.transform
        if key not in full_signature_cache:
            full_signature_cache[key] = _compute_signature(key, strict_tol=sig_tol)
        return full_signature_cache[key]

    def _deep_match(a, b):
        return _deep_geometry_match(a.transform, b.transform, tolerance=tolerance, cache=deep_sig_cache)

    def _geometry_match(a, b):
        if not _topology_match(a, b):
            return False
        s1 = _full_signature(a)
        s2 = _full_signature(b)
        if not s1 or not s2:
            return False
        tol = max(float(tolerance), 0.0)
        if not (_avg_abs_delta(s1.edge_quant, s2.edge_quant) <= max(tol * 0.25, 0.003) and
                _avg_abs_delta(s1.face_quant, s2.face_quant) <= max(tol * 0.25, 0.003) and
                _avg_abs_delta(s1.distance_quant, s2.distance_quant) <= max(tol * 0.20, 0.004) and
                _avg_abs_delta(s1.radial_quant, s2.radial_quant) <= max(tol * 0.20, 0.004)):
            return False
        return _deep_match(a, b)

    def _points(sig):
        key = sig.transform
        if key not in points_cache:
            points_cache[key] = _points_array(key, om2.MSpace.kObject)
        return points_cache[key]

    def _exact_match(a, b):
        if not _topology_match(a, b):
            return False
        p1 = _points(a)
        p2 = _points(b)
        if not p1 or not p2 or len(p1) != len(p2):
            return False
        tol = max(float(tolerance), 0.0)
        if not HAS_NUMPY:
            pp1, pp2 = p1, p2
            if ignore_scale:
                pp1, _ = _normalize_point_list(pp1)
                pp2, _ = _normalize_point_list(pp2)
            rms = _point_list_rms(pp1, pp2)
            return bool(rms is not None and rms <= tol)
        src = np.array(p1, dtype=np.float64)
        dst = np.array(p2, dtype=np.float64)
        if not ignore_scale:
            src_c = src.mean(axis=0)
            dst_c = dst.mean(axis=0)
            aa = src - src_c
            bb = dst - dst_c
            h = aa.T.dot(bb)
            try:
                u, _, vt = np.linalg.svd(h)
            except np.linalg.LinAlgError:
                return False
            r = vt.T.dot(u.T)
            if np.linalg.det(r) < 0:
                vt[-1, :] *= -1
                r = vt.T.dot(u.T)
            predicted = aa.dot(r) + dst_c
            diff = predicted - dst
            rms = math.sqrt(float(np.mean(np.sum(diff * diff, axis=1))))
        else:
            _matrix, rms = _svd_align(src, dst)
            if rms is None:
                return False
        _, size = _normalize_point_list(p2)
        return (rms / max(size, 1e-8)) <= tol

    def _size_match(a, b):
        return _real_size_range_compatible(
            a.transform, b.transform, ignore_scale=ignore_scale,
            real_size_ratio_max=real_size_ratio_max, data_cache=size_data_cache)

    def _matches(a, b):
        if not _size_match(a, b):
            return False
        if method == "topology":
            return _topology_match(a, b)
        if method == "geometry":
            return _geometry_match(a, b)
        if method == "exact":
            return _exact_match(a, b)
        return False

    buckets = defaultdict(list)
    for sig in remaining:
        if cancel_cb and len(buckets) % GROUPING_CANCEL_CHECK_INTERVAL == 0 and cancel_cb():
            raise ProcessCanceled()
        buckets[_bucket_key(sig)].append(sig)

    bucket_items = sorted(buckets.items(), key=lambda item: (item[0][0], item[0][1]))
    bucket_total = max(1, len(bucket_items))

    for bucket_index, (_bkey, bucket) in enumerate(bucket_items):
        if cancel_cb and cancel_cb():
            raise ProcessCanceled()
        if progress_cb:
            pct = int(float(bucket_index) / float(bucket_total) * 100.0)
            progress_cb(percent=pct, message="Grouping bucket {} / {}".format(bucket_index + 1, bucket_total),
                        step="Grouping", group="UVOptimizer", mesh="{} mesh(es)".format(len(bucket)),
                        current=bucket_index, total=bucket_total)

        if len(bucket) <= 1:
            if bucket[0].transform not in processed:
                uniques.append(bucket[0].transform)
            continue

        # Topology mode only needs the topology bucket when scale is ignored.
        # When scale matters, keep the pairwise pass so Scale range can split same-topology meshes by world size.
        if method == "topology" and ignore_scale:
            meshes = [s.transform for s in bucket if s.transform not in processed]
            if len(meshes) > 1:
                for mesh in meshes:
                    processed.add(mesh)
                hash_part = _hash_blob(meshes, tolerance, ignore_scale)[:10]
                iid = "{method}_{idx:03d}_{hash}".format(method=method, idx=group_index, hash=hash_part)
                groups[iid] = {"meshes": meshes, "score": 1.0}
                group_index += 1
            elif meshes:
                uniques.extend(meshes)
            continue

        for source_index, source in enumerate(bucket):
            if source.transform in processed:
                continue
            if source_index % GROUPING_CANCEL_CHECK_INTERVAL == 0 and cancel_cb and cancel_cb():
                raise ProcessCanceled()

            matches = [source.transform]
            processed.add(source.transform)

            for candidate_index in range(source_index + 1, len(bucket)):
                candidate = bucket[candidate_index]
                if candidate.transform in processed:
                    continue
                if candidate_index % GROUPING_CANCEL_CHECK_INTERVAL == 0 and cancel_cb and cancel_cb():
                    raise ProcessCanceled()
                if progress_cb and candidate_index and candidate_index % GROUPING_PROGRESS_INTERVAL == 0:
                    pct = int(float(bucket_index) / float(bucket_total) * 100.0)
                    progress_cb(percent=max(0, min(99, pct)),
                                message="Grouping {} vs {}".format(_short(source.transform), _short(candidate.transform)),
                                step="Grouping", group="UVOptimizer", mesh=candidate.transform,
                                current=bucket_index, total=bucket_total)
                if _matches(source, candidate):
                    matches.append(candidate.transform)
                    processed.add(candidate.transform)

            if len(matches) > 1:
                hash_part = _hash_blob(matches, tolerance, ignore_scale)[:10]
                iid = "{method}_{idx:03d}_{hash}".format(method=method, idx=group_index, hash=hash_part)
                groups[iid] = {"meshes": matches, "score": 1.0}
                group_index += 1
            else:
                uniques.extend(matches)

    if progress_cb:
        progress_cb(percent=100, message="Grouping complete", step="Grouping", current=bucket_total, total=bucket_total)

    return groups, _dedupe_keep_order(uniques)


def _verify_instance_matches_original(instance, original, tolerance=ALIGN_VERIFY_TOL_DEFAULT):
    if not _exists(instance) or not _exists(original):
        return False, None
    inst_pts = _points_array(instance, om2.MSpace.kWorld)
    orig_pts = _points_array(original, om2.MSpace.kWorld)
    if not inst_pts or not orig_pts or len(inst_pts) != len(orig_pts):
        return False, None
    _, size = _normalize_point_list(orig_pts)
    rms = _point_list_rms(inst_pts, orig_pts)
    if rms is None:
        return False, None
    norm = rms / max(size, 1e-8)
    tol  = max(float(tolerance), 0.0)
    if norm <= tol:
        return True, norm

    sample_limit = max(16, int(ORIENTATION_SEARCH_MAX_POINTS))
    if len(inst_pts) > sample_limit:
        step       = float(len(inst_pts)) / float(sample_limit)
        idx        = [min(len(inst_pts) - 1, int(i * step)) for i in range(sample_limit)]
        inst_check = [inst_pts[i] for i in idx]
        orig_check = [orig_pts[i] for i in idx]
    else:
        inst_check = inst_pts
        orig_check = orig_pts

    ratio, nearest_err = _nearest_point_match_score(inst_check, orig_check, max(tol * max(size, 1e-8), 0.0001))
    if ratio >= 0.98:
        return True, nearest_err
    return False, norm


def _verify_instance_ordered_points(instance, original, tolerance=ALIGN_VERIFY_TOL_DEFAULT):
    """Strict ordered-vertex verification used before any fuzzy nearest check.

    The nearest-point verifier is useful for meshes with reordered vertices, but
    symmetric parts can also pass it when they are rotated 180 degrees.  This
    strict test keeps the normal path deterministic for duplicated meshes that
    still share vertex order.
    """
    if not _exists(instance) or not _exists(original):
        return False, None
    inst_pts = _points_array(instance, om2.MSpace.kWorld)
    orig_pts = _points_array(original, om2.MSpace.kWorld)
    if not inst_pts or not orig_pts or len(inst_pts) != len(orig_pts):
        return False, None
    _, size = _normalize_point_list(orig_pts)
    rms = _point_list_rms(inst_pts, orig_pts)
    if rms is None:
        return False, None
    norm = rms / max(size, 1e-8)
    return norm <= max(float(tolerance), 0.0), norm


def _nearest_point_match_score(src_points, dst_points, world_tolerance):
    if not src_points or not dst_points:
        return 0.0, None
    tol = max(float(world_tolerance), 1e-8)
    inv = 1.0 / tol
    buckets = defaultdict(list)
    for p in dst_points:
        key = (int(math.floor(p[0] * inv)), int(math.floor(p[1] * inv)), int(math.floor(p[2] * inv)))
        buckets[key].append(p)

    matched  = 0
    total_sq = 0.0
    for p in src_points:
        bx = int(math.floor(p[0] * inv))
        by = int(math.floor(p[1] * inv))
        bz = int(math.floor(p[2] * inv))
        best_sq = None
        for ox in (-1, 0, 1):
            for oy in (-1, 0, 1):
                for oz in (-1, 0, 1):
                    for q in buckets.get((bx + ox, by + oy, bz + oz), []):
                        dx = p[0] - q[0]
                        dy = p[1] - q[1]
                        dz = p[2] - q[2]
                        d2 = dx*dx + dy*dy + dz*dz
                        if best_sq is None or d2 < best_sq:
                            best_sq = d2
        if best_sq is not None and best_sq <= tol * tol:
            matched += 1
            total_sq += best_sq
        else:
            total_sq += tol * tol

    ratio = float(matched) / float(max(1, len(src_points)))
    rms   = math.sqrt(total_sq / float(max(1, len(src_points))))
    return ratio, rms / tol


def _matrix_flat_to_np(matrix):
    if not HAS_NUMPY or matrix is None or len(matrix) != 16:
        return None
    return np.array(matrix, dtype=np.float64).reshape((4, 4))


def _matrix_np_to_flat(matrix):
    return [float(v) for v in np.asarray(matrix, dtype=np.float64).reshape((16,))]


def _translation_matrix_np(offset):
    m = np.identity(4, dtype=np.float64)
    # Maya row-vector convention: translation lives on row 3 (index [3, 0:3]).
    m[3, 0] = float(offset[0])
    m[3, 1] = float(offset[1])
    m[3, 2] = float(offset[2])
    return m


def _rotation_matrix_np(axis, degrees):
    """Build a 4x4 rotation matrix using Maya row-vector convention."""
    rad = math.radians(float(degrees))
    c   = math.cos(rad)
    s   = math.sin(rad)
    m   = np.identity(4, dtype=np.float64)
    if axis == 0:   # X
        m[1, 1], m[1, 2] =  c, s
        m[2, 1], m[2, 2] = -s, c
    elif axis == 1: # Y
        m[0, 0], m[0, 2] =  c, -s
        m[2, 0], m[2, 2] =  s,  c
    else:           # Z
        m[0, 0], m[0, 1] =  c, s
        m[1, 0], m[1, 1] = -s, c
    return m


def _transform_points_with_matrix(points, matrix_np):
    if not HAS_NUMPY or matrix_np is None or not points:
        return []
    arr = np.array([[p[0], p[1], p[2], 1.0] for p in points], dtype=np.float64)
    out = arr.dot(matrix_np)
    return [(float(p[0]), float(p[1]), float(p[2])) for p in out[:, :3]]


def _candidate_rotation_matrices(base_matrix, pivot):
    """Generate full 0-360 degree candidate matrices around the backup center."""
    base = _matrix_flat_to_np(base_matrix)
    if base is None:
        return []
    pivot    = (float(pivot[0]), float(pivot[1]), float(pivot[2]))
    to_pivot = _translation_matrix_np((-pivot[0], -pivot[1], -pivot[2]))
    fr_pivot = _translation_matrix_np(pivot)

    candidates = [base]
    # Robust dedupe using rounded matrix keys.
    seen = set()
    seen.add(tuple(round(v, 6) for v in _matrix_np_to_flat(base)))

    def _add(rot):
        candidate = base.dot(to_pivot).dot(rot).dot(fr_pivot)
        key = tuple(round(v, 6) for v in _matrix_np_to_flat(candidate))
        if key not in seen:
            seen.add(key)
            candidates.append(candidate)

    step = max(1, int(ORIENTATION_SEARCH_STEP_DEGREES))
    for ax in (0, 1, 2):
        for deg in range(step, 360, step):
            _add(_rotation_matrix_np(ax, deg))

    # Right-angle combinations.
    for rx in (0, 90, 180, 270):
        for ry in (0, 90, 180, 270):
            for rz in (0, 90, 180, 270):
                # Skip the identity rotation explicitly.
                if rx == 0 and ry == 0 and rz == 0:
                    continue
                rot = (_rotation_matrix_np(0, rx)
                       .dot(_rotation_matrix_np(1, ry))
                       .dot(_rotation_matrix_np(2, rz)))
                _add(rot)

    return candidates


def _refine_instance_orientation_to_original(instance, original, tolerance=ALIGN_VERIFY_TOL_DEFAULT):
    if not HAS_NUMPY or not _exists(instance) or not _exists(original):
        return False, 0.0, None

    mfn, _ = _get_mesh_fn(instance)
    if not mfn:
        return False, 0.0, None
    master_obj_pts = [(p.x, p.y, p.z) for p in mfn.getPoints(om2.MSpace.kObject)]
    orig_pts       = _points_array(original, om2.MSpace.kWorld)
    if not master_obj_pts or not orig_pts or len(master_obj_pts) != len(orig_pts):
        return False, 0.0, None

    max_points = max(16, int(ORIENTATION_SEARCH_MAX_POINTS))
    if len(master_obj_pts) > max_points:
        step       = float(len(master_obj_pts)) / float(max_points)
        sample_idx = [min(len(master_obj_pts) - 1, int(i * step)) for i in range(max_points)]
        src_sample = [master_obj_pts[i] for i in sample_idx]
        dst_sample = [orig_pts[i]       for i in sample_idx]
    else:
        src_sample = master_obj_pts
        dst_sample = orig_pts

    _, size      = _normalize_point_list(orig_pts)
    world_tol    = max(float(tolerance) * max(size, 1e-8), 0.0001)
    pivot, _     = _world_bbox(original)
    curr_matrix  = _get_world_matrix(instance)

    best_matrix = None
    best_ratio  = -1.0
    best_rms    = None

    for candidate in _candidate_rotation_matrices(curr_matrix, pivot):
        pred  = _transform_points_with_matrix(src_sample, candidate)
        ratio, norm_rms = _nearest_point_match_score(pred, dst_sample, world_tol)
        if (ratio > best_ratio or
                (abs(ratio - best_ratio) < 1e-9 and
                 (best_rms is None or (norm_rms is not None and norm_rms < best_rms)))):
            best_ratio  = ratio
            best_rms    = norm_rms
            best_matrix = candidate
        if best_ratio >= 0.999:
            break

    if best_matrix is None:
        return False, 0.0, None
    _apply_world_matrix(instance, _matrix_np_to_flat(best_matrix))
    return best_ratio >= 0.98, best_ratio, best_rms


# ---------------------------------------------------------------------------
# PCA + ICP backup alignment
# ---------------------------------------------------------------------------
def _sample_np_points(points, max_points=PCA_ICP_MAX_SAMPLE_POINTS):
    if not HAS_NUMPY or points is None:
        return None
    if len(points) <= max_points:
        return points.copy()
    indices = np.linspace(0, len(points) - 1, max_points).astype(int)
    return points[indices].copy()


def _world_vertices_np(transform_name):
    fn, _ = _get_mesh_fn(transform_name)
    if not fn:
        return None
    try:
        points = fn.getPoints(om2.MSpace.kWorld)
        return np.array([[p.x, p.y, p.z] for p in points], dtype=np.float64)
    except Exception:
        return None


def _nearest_neighbor_bruteforce_np(source, target):
    nearest   = []
    distances = []
    chunk_size = 200
    for i in range(0, len(source), chunk_size):
        src_chunk = source[i:i + chunk_size]
        diff      = src_chunk[:, None, :] - target[None, :, :]
        dist_sq   = np.sum(diff * diff, axis=2)
        idx       = np.argmin(dist_sq, axis=1)
        nearest.append(target[idx])
        distances.append(np.sqrt(np.min(dist_sq, axis=1)))
    return np.vstack(nearest), np.concatenate(distances)


def _best_fit_transform_np(source, target):
    """Kabsch best-fit rotation + translation."""
    source_center   = np.mean(source, axis=0)
    target_center   = np.mean(target, axis=0)
    source_centered = source - source_center
    target_centered = target - target_center
    h = source_centered.T.dot(target_centered)
    try:
        u, _, vt = np.linalg.svd(h)
    except np.linalg.LinAlgError:
        return None, None
    r = vt.T.dot(u.T)
    if np.linalg.det(r) < 0:
        vt[-1, :] *= -1
        r = vt.T.dot(u.T)
    t = target_center - r.dot(source_center)
    return r, t


def _apply_np_transform(points, rotation, translation):
    return rotation.dot(points.T).T + translation


def _compute_pca_axes_np(points):
    center  = np.mean(points, axis=0)
    centered = points - center
    cov = np.cov(centered.T)
    try:
        values, vectors = np.linalg.eigh(cov)
    except np.linalg.LinAlgError:
        return None, None
    order   = np.argsort(values)[::-1]
    vectors = vectors[:, order]
    if np.linalg.det(vectors) < 0:
        vectors[:, -1] *= -1
    return center, vectors


def _generate_orientation_candidates_np(a_axes, b_axes):
    candidates = []
    for perm in itertools.permutations([0, 1, 2]):
        permuted_a = a_axes[:, perm]
        for sign in itertools.product([-1, 1], repeat=3):
            signed_a = permuted_a * np.array(sign)
            if np.linalg.det(signed_a) < 0:
                continue
            r = b_axes.dot(signed_a.T)
            if np.linalg.det(r) > 0:
                candidates.append(r)
    return candidates


def _score_alignment_np(source, target):
    _, distances = _nearest_neighbor_bruteforce_np(source, target)
    return float(np.mean(distances))


def _pca_icp_score_is_usable(score, target_mesh, tolerance=ALIGN_VERIFY_TOL_DEFAULT):
    """Return True when the sampled PCA+ICP score is close enough to trust.

    The final PCA+ICP score is an average world-space nearest-neighbor
    distance on sampled points.  It is intentionally used as a fallback trust
    signal because exact ordered-vertex verification can reject valid matches
    when duplicated meshes have different vertex order, symmetric topology, or
    sparse sampling differences.
    """
    if score is None:
        return False
    try:
        score = float(score)
        if not np.isfinite(score):
            return False
        _, size = _world_bbox(target_mesh)
        ref_size = max(float(size[0]), float(size[1]), float(size[2]), 1e-8)
        return (score / ref_size) <= max(float(tolerance) * 2.0, 0.001)
    except Exception:
        return False


def _row_vector_delta_matrix_np(rotation_col, translation_col):
    """Build a Maya row-vector 4x4 delta matrix from Kabsch output.

    Maya stores transform matrices as row-major values with translation in the
    fourth row. Kabsch returns a column-vector rotation, so transpose it before
    writing it into the Maya matrix layout.
    """
    matrix = np.identity(4, dtype=np.float64)
    # Convert column-vector rotation to Maya row-vector layout.
    matrix[:3, :3] = rotation_col.T
    # Translation lives on row 3 in Maya row-vector convention.
    matrix[3, 0] = float(translation_col[0])
    matrix[3, 1] = float(translation_col[1])
    matrix[3, 2] = float(translation_col[2])
    return matrix


def _pca_icp_align_instance_to_backup(instance, backup,
                                       iterations=PCA_ICP_ITERATIONS,
                                       max_points=PCA_ICP_MAX_SAMPLE_POINTS):
    if not HAS_NUMPY or not _exists(instance) or not _exists(backup):
        return False, None
    points_a = _world_vertices_np(instance)
    points_b = _world_vertices_np(backup)
    if points_a is None or points_b is None:
        return False, None

    sample_a = _sample_np_points(points_a, max_points=max_points)
    sample_b = _sample_np_points(points_b, max_points=max_points)
    if sample_a is None or sample_b is None or len(sample_a) < 3 or len(sample_b) < 3:
        return False, None

    center_a, axes_a = _compute_pca_axes_np(sample_a)
    center_b, axes_b = _compute_pca_axes_np(sample_b)
    if center_a is None or center_b is None:
        return False, None

    best_score = None
    best_r     = None
    best_t     = None
    for r in _generate_orientation_candidates_np(axes_a, axes_b):
        t           = center_b - r.dot(center_a)
        transformed = _apply_np_transform(sample_a, r, t)
        score       = _score_alignment_np(transformed, sample_b)
        if best_score is None or score < best_score:
            best_score = score
            best_r     = r
            best_t     = t

    if best_r is None or best_t is None:
        return False, None

    current_points = _apply_np_transform(sample_a, best_r, best_t)
    total_r        = best_r.copy()
    total_t        = best_t.copy()

    for _ in range(max(0, int(iterations))):
        nearest_points, _ = _nearest_neighbor_bruteforce_np(current_points, sample_b)
        delta_r, delta_t  = _best_fit_transform_np(current_points, nearest_points)
        if delta_r is None or delta_t is None:
            break
        current_points = _apply_np_transform(current_points, delta_r, delta_t)
        total_r = delta_r.dot(total_r)
        total_t = delta_r.dot(total_t) + delta_t

    final_score    = _score_alignment_np(current_points, sample_b)
    current_matrix = _matrix_flat_to_np(_get_world_matrix(instance))
    if current_matrix is None:
        return False, final_score

    # Build the delta matrix with Maya row-vector convention.
    delta_matrix = _row_vector_delta_matrix_np(total_r, total_t)
    _apply_world_matrix(instance, _matrix_np_to_flat(current_matrix.dot(delta_matrix)))
    return True, final_score


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def _avg_abs_delta(a, b):
    n = min(len(a), len(b))
    if n <= 0:
        return 1.
    return sum(abs(float(a[i])-float(b[i])) for i in range(n)) / float(n)


def _score_from_delta(delta, tol):
    tol = max(float(tol), 1e-8)
    return max(0., min(1., 1. - delta/tol))


def _count_sim(a, b, abs_tol, pct_tol=0.0):
    diff = abs(int(a) - int(b))
    if diff == 0:
        return 1.
    allowed = max(float(abs_tol), max(int(a), int(b))*float(pct_tol), 1.)
    return max(0., min(1., 1. - float(diff)/(allowed*1.35)))


def _safe_score(a, b):
    """Exact topology match required."""
    if not a or not b:
        return 0.
    if a.vertex_count != b.vertex_count: return 0.
    if a.edge_count   != b.edge_count:   return 0.
    if a.face_count   != b.face_count:   return 0.

    return (
        _score_from_delta(_avg_abs_delta(a.edge_quant,       b.edge_quant),       0.006) * 0.25 +
        _score_from_delta(_avg_abs_delta(a.face_quant,       b.face_quant),       0.006) * 0.25 +
        _score_from_delta(_avg_abs_delta(a.valence_hist,     b.valence_hist),     0.015) * 0.20 +
        _score_from_delta(_avg_abs_delta(a.poly_degree_hist, b.poly_degree_hist), 0.015) * 0.15 +
        _score_from_delta(_avg_abs_delta(a.distance_quant,   b.distance_quant),   0.012) * 0.10 +
        _score_from_delta(_avg_abs_delta(a.radial_quant,     b.radial_quant),     0.012) * 0.05
    )


def _fuzzy_score(a, b, vertex_tol=5, size_tol=0.04):
    if not a or not b:
        return 0.

    simple = (min(a.face_count, b.face_count) <= 6 or
              min(a.vertex_count, b.vertex_count) <= 10)

    vtol = max(0, int(vertex_tol))
    etol = max(vtol*2, 2)
    ftol = max(vtol, 1)

    if vtol <= 0:
        if a.vertex_count != b.vertex_count: return 0.
        if a.edge_count   != b.edge_count:   return 0.
        if a.face_count   != b.face_count:   return 0.
        v_s = e_s = f_s = 1.
    else:
        v_s = _count_sim(a.vertex_count, b.vertex_count, vtol)
        e_s = _count_sim(a.edge_count,   b.edge_count,   etol)
        f_s = _count_sim(a.face_count,   b.face_count,   ftol)

    if v_s <= 0. or e_s <= 0. or f_s <= 0.:
        return 0.

    st = max(size_tol, 0.02)

    edge_s    = _score_from_delta(_avg_abs_delta(a.edge_quant,       b.edge_quant),       max(st*0.65, 0.025))
    face_s    = _score_from_delta(_avg_abs_delta(a.face_quant,       b.face_quant),       max(st*0.65, 0.025))
    valence_s = _score_from_delta(_avg_abs_delta(a.valence_hist,     b.valence_hist),     0.08)
    poly_s    = _score_from_delta(_avg_abs_delta(a.poly_degree_hist, b.poly_degree_hist), 0.04)
    dist_s    = _score_from_delta(_avg_abs_delta(a.distance_quant,   b.distance_quant),   max(st*0.55, 0.022))
    radial_s  = _score_from_delta(_avg_abs_delta(a.radial_quant,     b.radial_quant),     max(st*0.55, 0.022))

    count_score = v_s*0.45 + e_s*0.35 + f_s*0.20
    topo_score  = valence_s*0.55 + poly_s*0.45
    shape_score = dist_s*0.55 + radial_s*0.45
    prop_score  = edge_s*0.60 + face_s*0.40

    if shape_score  < 0.60: return 0.
    if prop_score   < 0.65: return 0.
    if topo_score   < 0.85: return 0.

    if simple:
        if count_score < 0.999: return 0.
        if topo_score  < 0.98:  return 0.
        if shape_score < 0.90:  return 0.
        if prop_score  < 0.90:  return 0.

    score = (count_score*0.45 + topo_score*0.35 + prop_score*0.15 + shape_score*0.05)
    if simple:
        score = min(score, 0.91)

    return max(0., min(1., score))


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------
def find_groups(signatures,
                detect_method="signature",
                compare_tolerance=0.30,
                ignore_scale=True,
                real_size_ratio_max=ROBUST_REAL_SIZE_RATIO_MAX,
                fuzzy_enabled=True,
                fuzzy_vertex_tol=0,
                fuzzy_size_tol=0.04,
                fuzzy_score_min=0.92,
                progress_cb=None,
                cancel_cb=None):
    """Returns (groups_safe, groups_fuzzy, uniques). Raises ProcessCanceled via cancel_cb."""
    method = (detect_method or "signature").lower()
    if method in ("robust", "robust_shape", "similar", "robust_similar"):
        groups_safe, uniques = find_groups_robust_similarity_style(
            signatures, tolerance_shape=compare_tolerance,
            ignore_scale=ignore_scale, real_size_ratio_max=real_size_ratio_max,
            progress_cb=progress_cb, cancel_cb=cancel_cb
        )
        return groups_safe, {}, uniques
    if method in ("topology", "geometry", "exact"):
        groups_safe, uniques = find_groups_uvoptimizer_style(
            signatures, method=method, tolerance=compare_tolerance,
            ignore_scale=ignore_scale, real_size_ratio_max=real_size_ratio_max,
            progress_cb=progress_cb, cancel_cb=cancel_cb
        )
        return groups_safe, {}, uniques

    groups_safe  = {}
    groups_fuzzy = {}
    uniques      = []
    consumed     = set()
    size_data_cache = {}

    strict_buckets = defaultdict(list)
    for sig in signatures:
        if cancel_cb and cancel_cb():
            raise ProcessCanceled()
        strict_buckets[sig.strict_hash].append(sig)

    for sh, bucket in strict_buckets.items():
        if cancel_cb and cancel_cb():
            raise ProcessCanceled()
        if len(bucket) <= 1:
            continue
        ref        = bucket[0]
        safe_group = [ref]
        for sig in bucket[1:]:
            if not _real_size_range_compatible(
                    ref.transform, sig.transform,
                    ignore_scale=ignore_scale,
                    real_size_ratio_max=real_size_ratio_max,
                    data_cache=size_data_cache):
                continue
            if _safe_score(ref, sig) >= 0.985:
                safe_group.append(sig)
        if len(safe_group) > 1:
            iid = "safe_{}".format(sh)
            groups_safe[iid] = {"meshes": [s.transform for s in safe_group], "score": 1.0}
            for sig in safe_group:
                consumed.add(sig.transform)

    remaining = [s for s in signatures if s.transform not in consumed]

    if not fuzzy_enabled:
        uniques.extend([s.transform for s in remaining])
        return groups_safe, groups_fuzzy, _dedupe_keep_order(uniques)

    remaining = sorted(remaining, key=lambda s: (
        s.vertex_count, s.edge_count, s.face_count, _short(s.transform).lower()
    ))

    clusters = []
    total_remaining = max(1, len(remaining))

    for sig_index, sig in enumerate(remaining):
        if cancel_cb and cancel_cb():
            raise ProcessCanceled()
        if progress_cb:
            pct = int(float(sig_index) / float(total_remaining) * 100.)
            progress_cb(percent=pct, message="Fuzzy grouping {}".format(_short(sig.transform)),
                        step="Fuzzy grouping", group="Fuzzy", mesh=sig.transform,
                        current=sig_index, total=total_remaining)

        best_cluster = None
        best_score   = 0.

        for cluster_index, cluster in enumerate(clusters):
            if cluster_index % 64 == 0 and cancel_cb and cancel_cb():
                raise ProcessCanceled()
            compatible_scores = [
                _fuzzy_score(sig, rep, vertex_tol=fuzzy_vertex_tol, size_tol=fuzzy_size_tol)
                for rep in cluster["reps"]
                if _real_size_range_compatible(
                    rep.transform, sig.transform,
                    ignore_scale=ignore_scale,
                    real_size_ratio_max=real_size_ratio_max,
                    data_cache=size_data_cache)
            ]
            if not compatible_scores:
                continue
            cluster_best = max(compatible_scores)
            if cluster_best > best_score:
                best_score   = cluster_best
                best_cluster = cluster

        if best_cluster is not None and best_score >= fuzzy_score_min:
            best_cluster["items"].append(sig)
            best_cluster["scores"].append(best_score)
            if len(best_cluster["reps"]) < FUZZY_CLUSTER_REPS:
                best_cluster["reps"].append(sig)
        else:
            clusters.append({"reps": [sig], "items": [sig], "scores": [1.]})

    fuzzy_index = 0
    for cluster in clusters:
        if len(cluster["items"]) <= 1:
            uniques.extend([s.transform for s in cluster["items"]])
            continue
        meshes = [s.transform for s in cluster["items"]]
        score  = min(cluster["scores"]) if cluster["scores"] else fuzzy_score_min
        iid    = "fuzzy_{:03d}_{}".format(fuzzy_index, _hash_blob(meshes, score)[:10])
        groups_fuzzy[iid] = {"meshes": meshes, "score": float(score)}
        fuzzy_index += 1

    return groups_safe, groups_fuzzy, _dedupe_keep_order(uniques)


# ---------------------------------------------------------------------------
# SVD-based alignment
# ---------------------------------------------------------------------------
def _svd_align(master_pts_obj, target_pts_world, allow_reflection=False):
    """
    Compute a 4x4 world matrix (flat list, row-major Maya convention) mapping
    master_pts_obj (Nx3) onto target_pts_world (Nx3) via SVD.
    Returns (matrix_16, rms_error) or (None, None).
    """
    if not HAS_NUMPY or master_pts_obj is None or target_pts_world is None:
        return None, None

    src = master_pts_obj
    dst = target_pts_world

    if src.shape[0] < 3 or src.shape != dst.shape:
        return None, None

    src_c = src.mean(axis=0)
    dst_c = dst.mean(axis=0)
    A     = src - src_c
    B     = dst - dst_c

    src_scale = math.sqrt(float(np.mean(np.sum(A*A, axis=1))))
    dst_scale = math.sqrt(float(np.mean(np.sum(B*B, axis=1))))
    if src_scale < 1e-8:
        return None, None
    scale = dst_scale / src_scale

    A_n = A / src_scale
    B_n = B / dst_scale

    H = A_n.T.dot(B_n)
    try:
        U, _, Vt = np.linalg.svd(H)
    except np.linalg.LinAlgError:
        return None, None

    R = Vt.T.dot(U.T)
    if np.linalg.det(R) < 0 and not allow_reflection:
        Vt[-1, :] *= -1
        R = Vt.T.dot(U.T)

    RS = R * scale                    # 3x3 rotation + scale.
    t  = dst_c - src_c.dot(RS)       # translation row-vector

    # 4x4 Maya row-vector matrix.
    matrix = [
        float(RS[0,0]), float(RS[0,1]), float(RS[0,2]), 0.,
        float(RS[1,0]), float(RS[1,1]), float(RS[1,2]), 0.,
        float(RS[2,0]), float(RS[2,1]), float(RS[2,2]), 0.,
        float(t[0]),    float(t[1]),    float(t[2]),    1.,
    ]

    predicted = src.dot(RS) + t
    diff      = predicted - dst
    rms       = math.sqrt(float(np.mean(np.sum(diff*diff, axis=1))))

    return matrix, rms


def _compute_alignment(master_transform, original_transform):
    if not HAS_NUMPY:
        return None, None

    mfn, _    = _get_mesh_fn(master_transform)
    tfn, tdag = _get_mesh_fn(original_transform)
    if not mfn or not tfn:
        return None, None
    if mfn.numVertices != tfn.numVertices:
        return None, None

    src_pts  = mfn.getPoints(om2.MSpace.kObject)
    tgt_pts  = tfn.getPoints(om2.MSpace.kObject)
    tgt_wmat = tdag.inclusiveMatrix()

    src = np.array([[p.x, p.y, p.z] for p in src_pts], dtype=np.float64)
    dst = np.array([[(p*tgt_wmat).x, (p*tgt_wmat).y, (p*tgt_wmat).z]
                    for p in tgt_pts], dtype=np.float64)

    matrix, rms = _svd_align(src, dst, allow_reflection=True)
    if matrix is None:
        return None, None

    try:
        _, tgt_size = _world_bbox(original_transform)
        ref_size    = max(tgt_size[0], tgt_size[1], tgt_size[2], 1e-8)
        norm_err    = rms / ref_size
    except Exception:
        norm_err = None

    return matrix, norm_err


def _fallback_align(instance, original):
    _apply_world_matrix(instance, _get_world_matrix(original))
    oc, _ = _world_bbox(original)
    ic, _ = _world_bbox(instance)
    try:
        pos = cmds.xform(instance, q=True, ws=True, t=True)
        cmds.xform(instance, ws=True, t=(
            pos[0]+oc[0]-ic[0],
            pos[1]+oc[1]-ic[1],
            pos[2]+oc[2]-ic[2],
        ))
    except Exception:
        pass


def _bbox_fit_align(instance, original):
    _fallback_align(instance, original)
    try:
        _, os  = _world_bbox(original)
        _, is_ = _world_bbox(instance)
        ratio  = tuple(max(0.001, min(1000., os[i]/max(is_[i], 1e-8))) for i in range(3))
        sx = cmds.getAttr(instance+".scaleX")
        sy = cmds.getAttr(instance+".scaleY")
        sz = cmds.getAttr(instance+".scaleZ")
        cmds.setAttr(instance+".scaleX", sx*ratio[0])
        cmds.setAttr(instance+".scaleY", sy*ratio[1])
        cmds.setAttr(instance+".scaleZ", sz*ratio[2])
        oc, _ = _world_bbox(original)
        ic, _ = _world_bbox(instance)
        pos   = cmds.xform(instance, q=True, ws=True, t=True)
        cmds.xform(instance, ws=True, t=(
            pos[0]+oc[0]-ic[0], pos[1]+oc[1]-ic[1], pos[2]+oc[2]-ic[2]
        ))
    except Exception:
        pass


def _align_instance_to_original(instance, master_transform, original_transform,
                                match_type=MATCH_SAFE,
                                use_pca_icp_alignment=True):
    """Place an instance with the standalone PCA-candidates + ICP algorithm only.

    This intentionally does not run the old ordered-vertex verification, nearest
    cloud verifier, SVD, bounding-box, or manual orientation-search fallbacks.
    The replacement placement is exactly the simple mesh-A-to-mesh-B pass: sample
    world vertices, choose the best PCA orientation candidate, refine with
    brute-force nearest-neighbor ICP, then apply the final row-vector Maya delta
    matrix to the new instance.
    """
    del master_transform, match_type, use_pca_icp_alignment

    if not (_exists(instance) and _exists(original_transform)):
        return False, None, False, None

    pca_icp_ok, pca_icp_err = _pca_icp_align_instance_to_backup(instance, original_transform)
    return pca_icp_ok, pca_icp_err, pca_icp_ok, pca_icp_err


# ---------------------------------------------------------------------------
# Master manager
# ---------------------------------------------------------------------------
class MasterManager(object):
    def __init__(self):
        self.masters = {}

    def find_existing_master(self, internal_id):
        root = _ic_group_path(MASTERS_GROUP)
        if not root:
            return None
        for mesh in _iter_mesh_transforms(root, include_ic=True):
            if (_get_ic_attr(mesh, ATTR_IC_TYPE, "") == "master" and
                    _get_ic_attr(mesh, ATTR_IC_SOURCE, "") == internal_id):
                return mesh
        return None

    def create_master(self, internal_id, display_name, reference_mesh,
                      group_id, match_type, score,
                      spacing=10., index=0, batch_id=None):
        if not _exists(reference_mesh):
            cmds.warning("[IC] create_master: reference_mesh gone: {}".format(reference_mesh))
            return None

        _, masters_group, _, _, _ = _ensure_ic_groups()
        layer_masters, _, _, _    = _ensure_ic_layers()

        existing = self.find_existing_master(internal_id)
        if existing and _exists(existing):
            self.masters[internal_id] = existing
            _add_to_layer(layer_masters, [existing])
            return existing

        master_name = "MASTER_{}".format(display_name)
        try:
            dup = cmds.duplicate(reference_mesh, rr=True)[0]
            dup = cmds.rename(dup, master_name)
            dup = cmds.parent(dup, masters_group, absolute=True)[0]
        except Exception as e:
            cmds.warning("[IC] create_master duplicate failed: {}".format(e))
            return None

        _center_shape_on_transform(dup)

        try:
            cmds.xform(dup, ws=True, t=(index*spacing, 0, 0), ro=(0,0,0))
            cmds.setAttr(dup+".scaleX", 1)
            cmds.setAttr(dup+".scaleY", 1)
            cmds.setAttr(dup+".scaleZ", 1)
            cmds.setAttr(dup+".visibility", 1)
        except Exception:
            pass

        dup = cmds.ls(dup, long=True)[0]
        _tag_node(dup, "master", group_id, internal_id, display_name, match_type, score)
        _add_ic_attr(dup, ATTR_IC_PROCESSED, True, "bool")
        if batch_id is not None:
            _add_ic_attr(dup, ATTR_IC_BATCH, batch_id, "int")

        _add_to_layer(layer_masters, [dup])
        self.masters[internal_id] = dup
        return dup

    def replace_with_instances(self, internal_id, display_name, group_meshes,
                                group_id, match_type, score,
                                keep_hidden_backups=True,
                                delete_originals=False,
                                use_pca_icp_alignment=True,
                                batch_id=None,
                                progress_cb=None,
                                cancel_cb=None,
                                progress_state=None):
        if internal_id not in self.masters:
            return [], [], []

        master_path = self.masters[internal_id]
        if not _exists(master_path):
            cmds.warning("[IC] Master gone: {}".format(master_path))
            return [], [], []

        _, _, instances_root, backups_root, _ = _ensure_ic_groups()
        lm, li, lb, _                          = _ensure_ic_layers()

        inst_grp   = _ensure_group("{}_INSTANCES".format(display_name), instances_root)
        backup_grp = _ensure_group("{}_BACKUPS".format(display_name),   backups_root)

        instances_created = []
        backups_created   = []
        originals_visible = []

        _unlock_layer_for(master_path)

        for idx, mesh in enumerate(group_meshes):
            if cancel_cb and cancel_cb():
                raise ProcessCanceled()

            if not _exists(mesh):
                continue

            full_mesh = cmds.ls(mesh, long=True)[0]

            if _is_under_ic_root(full_mesh):
                continue
            if _get_ic_attr(full_mesh, ATTR_IC_PROCESSED, False):
                continue
            if _is_referenced(full_mesh):
                cmds.warning("[IC] Skipping referenced: {}".format(full_mesh))
                continue

            try:
                _unlock_transform_for_edit(master_path)
                _unlock_transform_for_edit(full_mesh)
                inst = cmds.instance(master_path)[0]
                inst = cmds.rename(inst, "{}_INST_{:03d}".format(display_name, idx))
                inst = _parent_absolute_if_possible(inst, inst_grp)
            except Exception as e:
                cmds.warning("[IC] Instance creation failed for {}: {}".format(full_mesh, e))
                if progress_state is not None:
                    progress_state["current"] = progress_state.get("current", 0) + 1
                    if progress_cb:
                        progress_cb(current=progress_state["current"], total=progress_state.get("total", 1),
                                    message="Instance creation failed", step="Process",
                                    group=display_name, mesh=full_mesh)
                continue

            ok = False
            verify_err = None
            pca_icp_ok = False
            pca_icp_err = None

            try:
                ok, verify_err, pca_icp_ok, pca_icp_err = _align_instance_to_original(
                    inst, master_path, full_mesh,
                    match_type=match_type,
                    use_pca_icp_alignment=use_pca_icp_alignment)
                if not ok:
                    cmds.warning("[IC] PCA+ICP alignment did not converge for {} (score: {}).".format(
                        _short(full_mesh), "n/a" if verify_err is None else "{:.5f}".format(verify_err)))
                elif pca_icp_ok:
                    _add_ic_attr(inst, ATTR_IC_STATUS, "pca_icp_alignment", "string")
            except Exception as e:
                cmds.warning("[IC] PCA+ICP alignment failed for {}: {}".format(full_mesh, e))

            if not ok:
                cmds.warning("[IC] Keeping best-effort instance for {} even though verification failed.".format(_short(full_mesh)))
                _add_ic_attr(inst, ATTR_IC_STATUS, "best_effort_alignment", "string")
                if progress_state is not None:
                    progress_state["alignment_skipped"] = progress_state.get("alignment_skipped", 0) + 1

            try:
                inst = cmds.ls(inst, long=True)[0]
                cmds.setAttr(inst+".visibility", 1)
            except Exception:
                pass

            _tag_node(inst, "instance", group_id, internal_id, display_name, match_type, score)
            _add_ic_attr(inst, ATTR_IC_PROCESSED, True, "bool")
            if batch_id is not None:
                _add_ic_attr(inst, ATTR_IC_BATCH, batch_id, "int")

            _add_to_layer(li, [inst])
            instances_created.append(inst)

            try:
                if not _exists(full_mesh):
                    pass
                elif delete_originals:
                    cmds.delete(full_mesh)
                elif keep_hidden_backups:
                    orig_parent = (cmds.listRelatives(full_mesh, parent=True, fullPath=True) or [""])[0]
                    orig_name   = _short(full_mesh)
                    orig_vis    = bool(cmds.getAttr(full_mesh + ".visibility"))
                    orig_layers = ";".join(_display_layers_for(full_mesh))
                    orig_matrix = ",".join(str(v) for v in _get_world_matrix(full_mesh))
                    bkp = _parent_absolute_if_possible(full_mesh, backup_grp)
                    try:
                        bkp = cmds.rename(bkp, "{}_BACKUP_{:03d}".format(display_name, idx))
                    except Exception as e:
                        cmds.warning("[IC] Backup rename failed for {}: {}".format(_short(bkp), e))
                    bkp = cmds.ls(bkp, long=True)[0]
                    try:
                        cmds.setAttr(bkp+".visibility", 0)
                    except Exception:
                        pass
                    _tag_node(bkp, "backup", group_id, internal_id, display_name, match_type, score)
                    _add_ic_attr(bkp, ATTR_IC_PROCESSED,   True,        "bool")
                    _add_ic_attr(bkp, ATTR_IC_ORIG_PARENT, orig_parent, "string")
                    _add_ic_attr(bkp, ATTR_IC_ORIG_NAME,   orig_name,   "string")
                    _add_ic_attr(bkp, ATTR_IC_ORIG_VIS,    int(orig_vis), "int")
                    _add_ic_attr(bkp, ATTR_IC_ORIG_LAYERS, orig_layers, "string")
                    _add_ic_attr(bkp, ATTR_IC_ORIG_MATRIX, orig_matrix, "string")
                    if batch_id is not None:
                        _add_ic_attr(bkp, ATTR_IC_BATCH, batch_id, "int")
                    _add_to_layer(lb, [bkp])
                    backups_created.append(bkp)
                else:
                    try:
                        cmds.setAttr(full_mesh+".visibility", 1)
                    except Exception:
                        pass
                    _tag_node(full_mesh, "original_visible", group_id, internal_id, display_name, match_type, score)
                    _add_ic_attr(full_mesh, ATTR_IC_PROCESSED, True, "bool")
                    if batch_id is not None:
                        _add_ic_attr(full_mesh, ATTR_IC_BATCH, batch_id, "int")
                    _add_to_layer(lb, [full_mesh])
                    originals_visible.append(full_mesh)
            except ProcessCanceled:
                raise
            except Exception as e:
                cmds.warning("[IC] Cleanup failed for {}: {}".format(full_mesh, e))
                try:
                    if _exists(full_mesh):
                        _unlock_transform_for_edit(full_mesh)
                        cmds.setAttr(full_mesh + ".visibility", 0)
                        _tag_node(full_mesh, "backup", group_id, internal_id, display_name, match_type, score)
                        _add_ic_attr(full_mesh, ATTR_IC_PROCESSED, True, "bool")
                        if batch_id is not None:
                            _add_ic_attr(full_mesh, ATTR_IC_BATCH, batch_id, "int")
                        backups_created.append(full_mesh)
                except Exception as hide_error:
                    cmds.warning("[IC] Could not hide original {}: {}".format(_short(full_mesh), hide_error))

            if progress_state is not None:
                progress_state["current"] = progress_state.get("current", 0) + 1
                if progress_cb:
                    progress_cb(current=progress_state["current"], total=progress_state.get("total", 1),
                                message="Processing {}".format(_short(full_mesh)),
                                step="Process", group=display_name, mesh=full_mesh,
                                log="Instanced {}".format(_short(full_mesh)))

        _add_to_layer(lb, backups_created)
        _add_to_layer(lm, [master_path])

        try:
            cmds.setAttr(lm+".visibility", 1)
            cmds.setAttr(li+".visibility", 1)
            cmds.setAttr(lb+".visibility", 0)
        except Exception:
            pass

        return instances_created, backups_created, originals_visible


# ---------------------------------------------------------------------------
# Core InstanceCleaner
# ---------------------------------------------------------------------------
class InstanceCleaner(object):
    def __init__(self):
        self.master_manager          = MasterManager()
        self.signatures              = []
        self.signature_by_transform  = {}
        self.groups_safe             = {}
        self.groups_fuzzy            = {}
        self.uniques                 = []
        self.validated_groups        = {}
        self.last_process_batch      = None
        self._batch_counter          = 0
        self._manual_group_counter   = 0

    def _all_ic_meshes(self):
        root = _ic_root_path()
        if not root:
            return []
        return _iter_mesh_transforms(root, include_ic=True)

    def _existing_display_names_by_internal_id(self):
        data = {}
        for mesh in self._all_ic_meshes():
            iid  = _get_ic_attr(mesh, ATTR_IC_SOURCE, "")
            name = _get_ic_attr(mesh, ATTR_IC_GROUP_NAME, "")
            if iid and name:
                data[iid] = name
        return data

    def _append_processed_groups(self):
        root_path = _ic_group_path(INSTANCES_GROUP)
        if not root_path:
            return
        root = [root_path]
        buckets       = defaultdict(list)
        display_names = {}
        match_types   = {}
        scores        = {}

        for mesh in _iter_mesh_transforms(root[0], include_ic=True):
            if _get_ic_attr(mesh, ATTR_IC_TYPE, "") != "instance":
                continue
            iid  = _get_ic_attr(mesh, ATTR_IC_SOURCE, "")
            name = _get_ic_attr(mesh, ATTR_IC_GROUP_NAME, "") or "Processed_GRP"
            mt   = _get_ic_attr(mesh, ATTR_IC_MATCH_TYPE, MATCH_PROCESSED)
            sc   = _get_ic_attr(mesh, ATTR_IC_SCORE, 0.) or 0.
            if not iid:
                continue
            buckets[iid].append(mesh)
            display_names[iid] = name
            match_types[iid]   = mt
            scores[iid]        = sc

        for iid, meshes in buckets.items():
            label = iid + "_DONE"
            if label in self.validated_groups:
                continue
            self.validated_groups[label] = {
                "meshes":            meshes,
                "type":              MATCH_PROCESSED,
                "accepted":          False,
                "group_id":          -1,
                "processed":         True,
                "internal_id":       iid,
                "display_name":      display_names.get(iid, "Processed_GRP"),
                "score":             float(scores.get(iid, 0.)),
                "source_match_type": match_types.get(iid, MATCH_PROCESSED),
            }

    def _renumber_groups(self):
        gid = 0
        for info in self.validated_groups.values():
            if info.get("processed"):
                info["group_id"] = -1
            else:
                info["group_id"] = gid
                gid += 1

    def rebuild_groups_from_scene(self):
        """Rebuild the in-memory Group List from an existing Instance Cleaner scene.

        This is useful after reopening a Maya scene: the UI memory is empty, but
        processed masters/instances/backups still exist under _INSTANCE_CLEANER
        and carry Instance Cleaner attributes.  The method reads those tags and
        recreates processed group cards without running a full geometry scan.
        """
        self.signatures             = []
        self.signature_by_transform = {}
        self.groups_safe            = {}
        self.groups_fuzzy           = {}
        self.uniques                = []
        self.validated_groups       = {}
        try:
            self.master_manager.masters = {}
        except Exception:
            pass

        stats = {
            "groups": 0,
            "masters": 0,
            "instances": 0,
            "backups": 0,
            "originals": 0,
            "converted": 0,
        }

        if not _ic_root_path():
            return stats

        buckets = {}

        def _bucket(internal_id):
            if internal_id not in buckets:
                buckets[internal_id] = {
                    "nodes_by_type": defaultdict(list),
                    "display_name": "",
                    "match_type": MATCH_PROCESSED,
                    "score": 0.0,
                    "group_id": -1,
                    "batch": None,
                }
            return buckets[internal_id]

        # Scan the full scene, not only _INSTANCE_CLEANER.  This also supports
        # the optional mode where originals were kept visible in their original
        # hierarchy but tagged as original_visible.
        for node in _iter_mesh_transforms(None, include_ic=True):
            if not _exists(node):
                continue
            node = _long(node)
            ic_type = _get_ic_attr(node, ATTR_IC_TYPE, "")
            if ic_type not in ("master", "instance", "backup", "original_visible", "converted_geo"):
                continue
            internal_id = _get_ic_attr(node, ATTR_IC_SOURCE, "")
            if not internal_id:
                continue

            data = _bucket(internal_id)
            data["nodes_by_type"][ic_type].append(node)

            display_name = _get_ic_attr(node, ATTR_IC_GROUP_NAME, "")
            if display_name and not data["display_name"]:
                data["display_name"] = display_name

            match_type = _get_ic_attr(node, ATTR_IC_MATCH_TYPE, "")
            if match_type and data["match_type"] == MATCH_PROCESSED:
                data["match_type"] = match_type

            try:
                score = float(_get_ic_attr(node, ATTR_IC_SCORE, data["score"]) or 0.0)
                data["score"] = max(float(data.get("score", 0.0) or 0.0), score)
            except Exception:
                pass

            try:
                group_id = int(_get_ic_attr(node, ATTR_IC_GROUP, data["group_id"]))
                if data["group_id"] < 0:
                    data["group_id"] = group_id
            except Exception:
                pass

            try:
                batch = _get_ic_attr(node, ATTR_IC_BATCH, None)
                if batch is not None:
                    batch = int(batch)
                    if data["batch"] is None or batch > data["batch"]:
                        data["batch"] = batch
            except Exception:
                pass

        used_labels = set()
        ordered = sorted(buckets.items(), key=lambda item: (
            item[1].get("display_name", "").lower(), str(item[0]).lower()))

        for internal_id, data in ordered:
            by_type = data["nodes_by_type"]
            masters   = _dedupe_keep_order(by_type.get("master", []))
            instances = _dedupe_keep_order(by_type.get("instance", []))
            backups   = _dedupe_keep_order(by_type.get("backup", []))
            originals = _dedupe_keep_order(by_type.get("original_visible", []))
            converted = _dedupe_keep_order(by_type.get("converted_geo", []))

            primary_meshes = instances or backups or originals or converted or masters
            if not primary_meshes:
                continue

            display_name = data.get("display_name") or _safe_name(str(internal_id))
            match_type   = data.get("match_type") or MATCH_PROCESSED
            score        = float(data.get("score", 0.0) or 0.0)

            base_label = str(internal_id) + "_DONE"
            label = base_label
            suffix = 2
            while label in used_labels:
                label = "{}_{}".format(base_label, suffix)
                suffix += 1
            used_labels.add(label)

            info = {
                "meshes":            primary_meshes,
                "type":              MATCH_PROCESSED,
                "accepted":          False,
                "group_id":          -1,
                "processed":         True,
                "internal_id":       internal_id,
                "display_name":      display_name,
                "score":             score,
                "source_match_type": match_type,
            }
            if data.get("batch") is not None:
                info["processed_batch"] = data.get("batch")

            self.validated_groups[label] = info

            if masters:
                try:
                    self.master_manager.masters[internal_id] = masters[0]
                except Exception:
                    pass

            stats["groups"]    += 1
            stats["masters"]   += len(masters)
            stats["instances"] += len(instances)
            stats["backups"]   += len(backups)
            stats["originals"] += len(originals)
            stats["converted"] += len(converted)

        self._renumber_groups()
        return stats

    # -- Public API --

    def scan(self, root=None, roots=None, selection_only=False,
             strict_tol=0.001,
             detect_method="signature", compare_tolerance=0.30, ignore_scale=True,
             real_size_ratio_max=ROBUST_REAL_SIZE_RATIO_MAX,
             fuzzy_enabled=True, fuzzy_vertex_tol=0,
             fuzzy_size_tol=0.04, fuzzy_score_min=0.92,
             min_copies=2, progress_cb=None, cancel_cb=None):
        """
        progress_cb accepts legacy (percent, message) or keyword details; cancel_cb aborts cleanly.
        """
        if roots is None and root:
            roots = [root]

        if roots is not None:
            transforms = _collect_mesh_transforms_from_roots(roots)
        elif selection_only:
            selected_roots = _get_selected_transforms()
            if not selected_roots:
                cmds.warning("[IC] scan(selection_only=True) requires roots or a current Maya selection.")
                transforms = []
            else:
                transforms = _collect_mesh_transforms_from_roots(selected_roots)
        else:
            transforms = _iter_mesh_transforms(None)

        transforms = _dedupe_keep_order(transforms)
        transforms = [t for t in transforms
                      if not _get_ic_attr(t, ATTR_IC_PROCESSED, False)]

        self.signatures             = []
        self.signature_by_transform = {}

        method = (detect_method or "signature").lower()
        uvoptimizer_mode = method in ("topology", "geometry", "exact", "robust", "robust_shape", "similar", "robust_similar")

        total = len(transforms)
        for i, tf in enumerate(transforms):
            if cancel_cb and cancel_cb():
                raise ProcessCanceled()
            if progress_cb:
                progress_cb(percent=int(i * 60. / max(1, total)),
                            message="Scanning {}".format(_short(tf)),
                            step="Scene scan" if not selection_only else "Selection scan",
                            group="Scene" if not selection_only else "Selection",
                            mesh=tf, current=i, total=max(1, total))
            sig = (_compute_light_signature(tf) if uvoptimizer_mode
                   else _compute_signature(tf, strict_tol=strict_tol))
            if sig:
                self.signatures.append(sig)
                self.signature_by_transform[sig.transform] = sig

        # Map grouping progress into the scan progress range.
        def grouping_progress(*args, **kwargs):
            if progress_cb:
                percent = kwargs.pop("percent", args[0] if args else 0)
                message = kwargs.pop("message", args[1] if len(args) > 1 else "Grouping meshes")
                mapped = 60 + int(float(percent) * 35. / 100.)
                progress_cb(percent=min(95, mapped), message=message, **kwargs)

        if progress_cb:
            progress_cb(percent=60, message="Grouping meshes...", step="Grouping", current=0, total=max(1, total))

        self.groups_safe, self.groups_fuzzy, self.uniques = find_groups(
            self.signatures,
            detect_method=detect_method,
            compare_tolerance=compare_tolerance,
            ignore_scale=ignore_scale,
            real_size_ratio_max=real_size_ratio_max,
            fuzzy_enabled=fuzzy_enabled,
            fuzzy_vertex_tol=fuzzy_vertex_tol,
            fuzzy_size_tol=fuzzy_size_tol,
            fuzzy_score_min=fuzzy_score_min,
            progress_cb=grouping_progress,
            cancel_cb=cancel_cb,
        )

        if cancel_cb and cancel_cb():
            raise ProcessCanceled()
        if progress_cb:
            progress_cb(percent=95, message="Building group list...", step="Build group list", current=total, total=max(1, total))

        self.validated_groups = {}
        existing_names = self._existing_display_names_by_internal_id()
        used_names     = set(existing_names.values())
        gid = 0

        for iid, data in self.groups_safe.items():
            meshes = data["meshes"]
            if len(meshes) < min_copies:
                self.uniques.extend(meshes)
                continue
            dname = existing_names.get(iid) or _make_clean_group_name(meshes[0], used_names)
            self.validated_groups[iid] = {
                "meshes":            meshes,
                "type":              MATCH_SAFE,
                "accepted":          True,
                "group_id":          gid,
                "processed":         False,
                "internal_id":       iid,
                "display_name":      dname,
                "score":             float(data.get("score", 1.)),
                "source_match_type": MATCH_SAFE,
            }
            gid += 1

        for iid, data in self.groups_fuzzy.items():
            meshes = data["meshes"]
            if len(meshes) < min_copies:
                self.uniques.extend(meshes)
                continue
            dname = existing_names.get(iid) or _make_clean_group_name(meshes[0], used_names)
            self.validated_groups[iid] = {
                "meshes":            meshes,
                "type":              MATCH_FUZZY,
                "accepted":          None,
                "group_id":          gid,
                "processed":         False,
                "internal_id":       iid,
                "display_name":      dname,
                "score":             float(data.get("score", fuzzy_score_min)),
                "source_match_type": MATCH_FUZZY,
            }
            gid += 1

        self.uniques = _dedupe_keep_order(self.uniques)
        self._append_processed_groups()
        return len(self.validated_groups)


    def find_fast_group_for_source(self, source_mesh, method="geometry", tolerance=0.01, ignore_scale=True,
                                   real_size_ratio_max=ROBUST_REAL_SIZE_RATIO_MAX,
                                   min_copies=2, progress_cb=None, cancel_cb=None):
        """Fast selected-source lookup without rebuilding all scene groups.

        Compares one selected source mesh against every eligible scene mesh in
        O(n), then stores a single temporary accepted group when enough copies
        are found.
        """
        for label in list(self.validated_groups.keys()):
            if str(label).startswith("fast_selected_"):
                del self.validated_groups[label]

        if cancel_cb and cancel_cb():
            raise ProcessCanceled()
        source_candidates = _collect_mesh_transforms_from_roots([source_mesh])
        source = next((m for m in source_candidates if _exists(m) and _has_mesh_shape(m)), None)
        if not source:
            self._renumber_groups()
            return None, []
        source = _long(source)

        method = (method or "geometry").lower()
        if method == "signature":
            method = "geometry"

        source_count_key = _mesh_count_key(source)
        robust_data_cache = {}

        def _matches(candidate):
            # Most scene meshes can be rejected with O(1) data. Robust Similar keeps its
            # own topology + real-size + shape checks because it is stricter than the
            # older count prefilter in some scenes.
            if method in ("robust", "robust_shape", "similar", "robust_similar"):
                ok, _distance, _details = _rs_compare_meshes(
                    source, candidate,
                    tolerance_shape=tolerance,
                    ignore_scale=ignore_scale,
                    data_cache=robust_data_cache,
                    real_size_ratio_max=real_size_ratio_max,
                )
                return ok
            if not _real_size_range_compatible(
                    source, candidate, ignore_scale=ignore_scale,
                    real_size_ratio_max=real_size_ratio_max, data_cache=robust_data_cache):
                return False
            if source_count_key and _mesh_count_key(candidate) != source_count_key:
                return False
            if method == "topology":
                return _compare_mesh_topology(source, candidate, ignore_scale=ignore_scale, tolerance=tolerance)
            if method == "exact":
                return _compare_mesh_exact(source, candidate, ignore_scale=ignore_scale, tolerance=tolerance)
            return _compare_mesh_geometry(source, candidate, ignore_scale=ignore_scale, tolerance=tolerance)

        scene_meshes = _dedupe_keep_order(_iter_mesh_transforms(None))
        matches = []
        total = len(scene_meshes)
        for i, mesh in enumerate(scene_meshes):
            if cancel_cb and cancel_cb():
                raise ProcessCanceled()
            if progress_cb:
                pct = int(float(i) / float(max(1, total)) * 100.0)
                progress_cb(percent=pct, message="Fast-find {}".format(_short(mesh)),
                            step="Find selected fast group", group=_short(source),
                            mesh=mesh, current=i, total=max(1, total))
            if not mesh or not _exists(mesh):
                continue
            mesh = _long(mesh)
            if _is_under_ic_root(mesh):
                continue
            if bool(_get_ic_attr(mesh, ATTR_IC_PROCESSED, False)):
                continue
            if _matches(mesh):
                matches.append(mesh)
            if total > 500 and (i + 1) % 50 == 0:
                try:
                    QApplication.processEvents()
                except Exception:
                    pass

        matches = _dedupe_keep_order(matches)
        if progress_cb:
            progress_cb(percent=100, message="Fast-find complete: {} match(es)".format(len(matches)),
                        step="Find selected fast group", group=_short(source),
                        mesh=source, current=total, total=max(1, total),
                        log="{} match(es) found".format(len(matches)))
        if len(matches) < int(min_copies):
            self._renumber_groups()
            return None, matches

        hash_part = _hash_blob(source, matches, method, tolerance, ignore_scale)[:10]
        label = "fast_selected_" + hash_part
        used_names = set(g.get("display_name", "") for g in self.validated_groups.values())
        dname = _make_clean_group_name(source, used_names)
        self.validated_groups[label] = {
            "meshes":            matches,
            "type":              MATCH_SAFE,
            "accepted":          True,
            "group_id":          0,
            "processed":         False,
            "internal_id":       label,
            "display_name":      dname,
            "score":             1.0,
            "source_match_type": MATCH_SAFE,
        }
        self._renumber_groups()
        return label, matches

    def accept_group(self, label):
        if label in self.validated_groups and not self.validated_groups[label].get("processed"):
            self.validated_groups[label]["accepted"] = True

    def reject_group(self, label):
        if label in self.validated_groups:
            self.validated_groups[label]["accepted"] = False

    def select_group(self, label):
        meshes = self.get_nodes_for_label(label, target="source")
        return _select_nodes(meshes)

    def resolve_group_label(self, label):
        """Resolve a possibly stale UI label to the current validated group label."""
        if label in self.validated_groups:
            return label
        wanted = str(label or "")
        if not wanted:
            return None
        for current_label, info in self.validated_groups.items():
            if str(info.get("internal_id", "")) == wanted:
                return current_label
            if str(info.get("display_name", "")) == wanted:
                return current_label
        return None

    def _find_by_type(self, label, ic_type):
        resolved_label = self.resolve_group_label(label)
        if resolved_label:
            label = resolved_label
        internal_id = self.validated_groups.get(label, {}).get("internal_id", label)
        roots = []
        if ic_type == "master":
            root = _ic_group_path(MASTERS_GROUP)
            roots = [root] if root else []
        elif ic_type == "instance":
            root = _ic_group_path(INSTANCES_GROUP)
            roots = [root] if root else []
        elif ic_type == "backup":
            root = _ic_group_path(BACKUP_GROUP)
            roots = [root] if root else []
        elif ic_type == "converted_geo":
            root = _ic_group_path(CONVERTED_GROUP)
            roots = [root] if root else []
        elif ic_type == "original_visible":
            roots = [None]
        found = []
        for r in roots:
            iterator = (_iter_mesh_transforms(None, include_ic=True)
                        if r is None else _iter_mesh_transforms(r, include_ic=True))
            for mesh in iterator:
                if (_get_ic_attr(mesh, ATTR_IC_TYPE,   "") == ic_type and
                        _get_ic_attr(mesh, ATTR_IC_SOURCE, "") == internal_id):
                    found.append(mesh)
        return _dedupe_keep_order(found)


    def _label_for_internal_id(self, internal_id):
        if not internal_id:
            return None
        for label, info in self.validated_groups.items():
            if info.get("internal_id") == internal_id:
                return label
        return None

    def _delete_empty_ic_subgroups(self):
        """Remove empty helper groups left under INSTANCES/BACKUPS after remastering."""
        for root_name in (INSTANCES_GROUP, BACKUP_GROUP, CONVERTED_GROUP):
            root = _ic_group_path(root_name)
            if not root:
                continue
            try:
                groups = cmds.listRelatives(root, allDescendents=True, fullPath=True, type="transform") or []
            except Exception:
                groups = []
            groups = sorted(groups, key=lambda n: n.count("|"), reverse=True)
            for grp in groups:
                if not _exists(grp) or _has_mesh_shape(grp):
                    continue
                try:
                    children = cmds.listRelatives(grp, children=True, fullPath=True) or []
                except Exception:
                    children = []
                if not children:
                    try:
                        cmds.delete(grp)
                    except Exception:
                        pass

    def replace_master_groups_with_target(self, source_labels, target_label, delete_source_masters=True):
        """Re-instance processed source group(s) onto another processed master group.

        Typical use: select one or more old masters/instances, then select the
        target master last.  The old instances are recreated from the target
        master, backups/originals are re-tagged to the target group, old masters
        are deleted, and the in-memory group list is rebuilt from scene tags.
        """
        result = {
            "sources": 0,
            "replaced_instances": 0,
            "retagged_nodes": 0,
            "deleted_masters": 0,
            "skipped": 0,
            "target_label": target_label,
            "shape_warnings": 0,
            "error": "",
        }

        if target_label not in self.validated_groups:
            result["error"] = "Target group not found."
            return result

        target_info = self.validated_groups.get(target_label, {})
        if not target_info.get("processed"):
            result["error"] = "Target group must already be processed and have a master."
            return result

        source_labels = [l for l in _dedupe_keep_order(source_labels or [])
                         if l in self.validated_groups and l != target_label]
        if not source_labels:
            result["error"] = "No source group selected."
            return result

        target_masters = self._find_by_type(target_label, "master")
        if not target_masters:
            result["error"] = "Target master not found in scene."
            return result

        target_master = target_masters[0]
        target_iid    = target_info.get("internal_id", target_label)
        target_name   = target_info.get("display_name", target_label)
        target_gid    = int(target_info.get("group_id", -1) or -1)
        target_match  = target_info.get("source_match_type", target_info.get("type", MATCH_PROCESSED))
        target_score  = float(target_info.get("score", 0.0) or 0.0)

        _, _, instances_root, backups_root, _ = _ensure_ic_groups()
        _lm, li, lb, _lc = _ensure_ic_layers()
        target_inst_grp = _ensure_group("{}_INSTANCES".format(target_name), instances_root)
        target_bkp_grp  = _ensure_group("{}_BACKUPS".format(target_name), backups_root)

        with UndoChunk("InstanceCleanerReplaceMasterGroups"):
            _unlock_transform_for_edit(target_master)
            for source_label in source_labels:
                info = self.validated_groups.get(source_label, {})
                if not info or not info.get("processed"):
                    result["skipped"] += 1
                    continue

                source_iid = info.get("internal_id", source_label)
                if not source_iid or source_iid == target_iid:
                    result["skipped"] += 1
                    continue

                result["sources"] += 1
                source_instances = self._find_by_type(source_label, "instance")
                source_masters   = self._find_by_type(source_label, "master")

                if source_masters:
                    warn = _mesh_compatibility_warning(source_masters[0], target_master)
                    if warn:
                        result["shape_warnings"] += 1
                        cmds.warning("[IC] MERGE MASTERS safety warning: {} -> {} : {}".format(
                            _short(source_masters[0]), _short(target_master), warn))

                for old_inst in list(source_instances):
                    if not _exists(old_inst):
                        continue
                    try:
                        old_inst = cmds.ls(old_inst, long=True)[0]
                        _unlock_transform_for_edit(old_inst)
                        old_name = _short(old_inst)
                        old_mat  = _get_world_matrix(old_inst)
                        try:
                            old_vis = bool(cmds.getAttr(old_inst + ".visibility"))
                        except Exception:
                            old_vis = True
                        old_batch  = _get_ic_attr(old_inst, ATTR_IC_BATCH, None)
                        old_status = _get_ic_attr(old_inst, ATTR_IC_STATUS, "")

                        new_inst = cmds.instance(target_master)[0]
                        new_inst = _parent_absolute_if_possible(new_inst, target_inst_grp)
                        _unlock_transform_for_edit(new_inst)
                        _apply_world_matrix(new_inst, old_mat)
                        try:
                            cmds.setAttr(new_inst + ".visibility", old_vis)
                        except Exception:
                            pass

                        _tag_node(new_inst, "instance", target_gid, target_iid, target_name, target_match, target_score)
                        _add_ic_attr(new_inst, ATTR_IC_PROCESSED, True, "bool")
                        if old_batch is not None:
                            try:
                                _add_ic_attr(new_inst, ATTR_IC_BATCH, int(old_batch), "int")
                            except Exception:
                                pass
                        if old_status:
                            _add_ic_attr(new_inst, ATTR_IC_STATUS, old_status, "string")
                        _add_to_layer(li, [new_inst])

                        try:
                            cmds.delete(old_inst)
                        except Exception:
                            pass
                        try:
                            new_inst = cmds.rename(new_inst, old_name)
                        except Exception:
                            pass
                        result["replaced_instances"] += 1
                    except Exception as e:
                        result["skipped"] += 1
                        cmds.warning("[IC] Replace instance failed for {}: {}".format(_short(old_inst), e))

                # Move/retag backups to the target backup group.  Visible originals
                # and converted geometry keep their current hierarchy, but become
                # part of the target group for future rebuilds / selection lookup.
                for ic_type in ("backup", "original_visible", "converted_geo"):
                    for node in list(self._find_by_type(source_label, ic_type)):
                        if not _exists(node):
                            continue
                        try:
                            node = cmds.ls(node, long=True)[0]
                            old_batch = _get_ic_attr(node, ATTR_IC_BATCH, None)
                            if ic_type == "backup":
                                node = _parent_absolute_if_possible(node, target_bkp_grp)
                                _add_to_layer(lb, [node])
                            _tag_node(node, ic_type, target_gid, target_iid, target_name, target_match, target_score)
                            _add_ic_attr(node, ATTR_IC_PROCESSED, True, "bool")
                            if old_batch is not None:
                                try:
                                    _add_ic_attr(node, ATTR_IC_BATCH, int(old_batch), "int")
                                except Exception:
                                    pass
                            result["retagged_nodes"] += 1
                        except Exception as e:
                            result["skipped"] += 1
                            cmds.warning("[IC] Retag failed for {}: {}".format(_short(node), e))

                if delete_source_masters:
                    for master in list(source_masters):
                        if not _exists(master):
                            continue
                        if _long(master) == _long(target_master):
                            continue
                        try:
                            cmds.delete(master)
                            result["deleted_masters"] += 1
                        except Exception as e:
                            result["skipped"] += 1
                            cmds.warning("[IC] Delete source master failed for {}: {}".format(_short(master), e))

                try:
                    self.master_manager.masters.pop(source_iid, None)
                except Exception:
                    pass

            try:
                self.master_manager.masters[target_iid] = _long(target_master)
            except Exception:
                pass
            self._delete_empty_ic_subgroups()

        # Rebuild from scene tags so duplicate processed cards collapse into the
        # target internal id and viewport-selection lookup stays correct.
        self.rebuild_groups_from_scene()
        result["target_label"] = self._label_for_internal_id(target_iid) or target_label
        return result

    def select_master(self, label):
        found = self._find_by_type(label, "master")
        if found: cmds.select(found, r=True)
        return found

    def select_instances(self, label):
        found = self._find_by_type(label, "instance")
        if found: cmds.select(found, r=True)
        return found

    def select_master_and_instances(self, label):
        found = []
        found.extend(self._find_by_type(label, "master") or [])
        found.extend(self._find_by_type(label, "instance") or [])
        found = _dedupe_keep_order(found)
        if found:
            cmds.select(found, r=True)
        return found

    def select_backups(self, label):
        found = self._find_by_type(label, "backup")
        if found: cmds.select(found, r=True)
        return found

    def _nodes_for_shader_labels(self, labels=None, include_backups=True, include_converted=True):
        if labels is None:
            labels = list(self.validated_groups.keys())
        labels = [l for l in _dedupe_keep_order(labels or []) if l in self.validated_groups]
        nodes = []
        for label in labels:
            info = self.validated_groups.get(label, {})
            if info.get("processed"):
                nodes.extend(self._find_by_type(label, "master") or [])
                nodes.extend(self._find_by_type(label, "instance") or [])
                if include_converted:
                    nodes.extend(self._find_by_type(label, "converted_geo") or [])
                if include_backups:
                    nodes.extend(self._find_by_type(label, "backup") or [])
                    nodes.extend(self._find_by_type(label, "original_visible") or [])
            else:
                nodes.extend([m for m in info.get("meshes", []) if _exists(m)])
        return _dedupe_keep_order(nodes)

    def save_current_shaders(self, labels=None, include_backups=True, overwrite=False):
        nodes = self._nodes_for_shader_labels(labels=labels, include_backups=include_backups, include_converted=True)
        with UndoChunk("InstanceCleanerSaveShaders"):
            saved = _save_original_shaders_for_nodes(nodes, overwrite=overwrite)
        return {"saved": saved, "nodes": len(nodes)}

    def restore_saved_shaders(self, labels=None, include_backups=True, remove_saved_attrs=False):
        nodes = self._nodes_for_shader_labels(labels=labels, include_backups=include_backups, include_converted=True)
        with UndoChunk("InstanceCleanerRestoreShaders"):
            stats = _restore_original_shaders_for_nodes(nodes, remove_attr=remove_saved_attrs)
        stats["nodes"] = len(nodes)
        return stats

    def clean_debug_shaders(self):
        """Delete unused IC_COLOR debug materials/shading groups only."""
        stats = {"deleted": 0, "skipped_used": 0}
        candidates = []
        try:
            candidates.extend(cmds.ls("IC_COLOR_*", materials=True) or [])
        except Exception:
            pass
        try:
            candidates.extend(cmds.ls("IC_COLOR_*SG", type="shadingEngine") or [])
        except Exception:
            pass
        candidates = _dedupe_keep_order(candidates)
        # Delete unused shading groups first, then now-orphaned materials.
        ordered = sorted(candidates, key=lambda n: 0 if cmds.nodeType(n) == "shadingEngine" else 1)
        with UndoChunk("InstanceCleanerCleanDebugShaders"):
            for node in ordered:
                if not _exists(node):
                    continue
                try:
                    if cmds.nodeType(node) == "shadingEngine":
                        members = cmds.sets(node, q=True) or []
                        if members:
                            stats["skipped_used"] += 1
                            continue
                    cmds.delete(node)
                    stats["deleted"] += 1
                except Exception:
                    stats["skipped_used"] += 1
        return stats

    def validate_repair_scene(self, repair=True):
        """Validate IC hierarchy/tags and repair safe structural issues."""
        stats = {
            "groups": 0, "masters": 0, "instances": 0, "backups": 0,
            "missing_master": 0, "extra_masters": 0, "reparented": 0,
            "empty_helpers_deleted": 0, "warnings": 0,
        }
        if repair:
            _ensure_ic_groups()
            _ensure_ic_layers()
        self.rebuild_groups_from_scene()
        _root, masters_root, instances_root, backups_root, converted_root = _ensure_ic_groups()
        for label, info in list(self.validated_groups.items()):
            if not info.get("processed"):
                continue
            stats["groups"] += 1
            name = _safe_name(info.get("display_name", label)) or "GROUP"
            masters = self._find_by_type(label, "master")
            instances = self._find_by_type(label, "instance")
            backups = self._find_by_type(label, "backup")
            converted = self._find_by_type(label, "converted_geo")
            stats["masters"] += len(masters)
            stats["instances"] += len(instances)
            stats["backups"] += len(backups)
            if not masters:
                stats["missing_master"] += 1
                stats["warnings"] += 1
            if len(masters) > 1:
                stats["extra_masters"] += len(masters) - 1
                stats["warnings"] += len(masters) - 1
            if repair:
                inst_grp = _ensure_group("{}_INSTANCES".format(name), instances_root)
                bkp_grp = _ensure_group("{}_BACKUPS".format(name), backups_root)
                geo_grp = _ensure_group("{}_GEO".format(name), converted_root)
                for node, parent in [(m, masters_root) for m in masters] + [(i, inst_grp) for i in instances] + [(b, bkp_grp) for b in backups] + [(g, geo_grp) for g in converted]:
                    if not _exists(node):
                        continue
                    before = _long(node)
                    after = _parent_absolute_if_possible(node, parent)
                    if before != after:
                        stats["reparented"] += 1
                _add_to_layer(LAYER_MASTERS, masters)
                _add_to_layer(LAYER_INSTANCES, instances)
                _add_to_layer(LAYER_BACKUPS, backups)
                _add_to_layer(LAYER_CONVERTED, converted)
        if repair:
            before_helpers = 0
            try:
                for root_name in (INSTANCES_GROUP, BACKUP_GROUP, CONVERTED_GROUP):
                    root = _ic_group_path(root_name)
                    if root:
                        before_helpers += len(cmds.listRelatives(root, allDescendents=True, fullPath=True, type="transform") or [])
            except Exception:
                pass
            self._delete_empty_ic_subgroups()
            after_helpers = 0
            try:
                for root_name in (INSTANCES_GROUP, BACKUP_GROUP, CONVERTED_GROUP):
                    root = _ic_group_path(root_name)
                    if root:
                        after_helpers += len(cmds.listRelatives(root, allDescendents=True, fullPath=True, type="transform") or [])
            except Exception:
                pass
            stats["empty_helpers_deleted"] = max(0, before_helpers - after_helpers)
            self.rebuild_groups_from_scene()
        return stats

    def assign_color_shaders(self, labels=None, include_backups=False):
        """Assign deterministic per-group Lambert colors to Instance Cleaner groups.

        The original material assignment is saved once on every affected transform
        before debug colors are applied, so RESTORE SAVED SHADERS can bring the
        scene back to its previous look.
        """
        if labels is None:
            labels = list(self.validated_groups.keys())
        labels = [l for l in _dedupe_keep_order(labels or []) if l in self.validated_groups]
        stats = {"groups": 0, "nodes": 0, "materials": 0, "backups": 0, "saved_shaders": 0}
        if not labels:
            return stats

        all_labels = sorted(list(self.validated_groups.keys()), key=lambda l: (self.validated_groups.get(l, {}).get("display_name", l).lower(), l.lower()))
        index_by_label = dict((l, i) for i, l in enumerate(all_labels))

        with UndoChunk("InstanceCleanerAssignColorShaders"):
            for label in labels:
                info = self.validated_groups.get(label, {})
                if not info:
                    continue
                internal_key = info.get("internal_id") or info.get("display_name") or label
                display_name = info.get("display_name", label)
                color_key = "{}|{}|{}|{}".format(label, internal_key, display_name, info.get("group_id", -1))
                color_index = index_by_label.get(label, len(index_by_label))
                base_color = _stable_palette_color(color_index, max(1, len(all_labels)), color_key)
                sg = _ensure_lambert_shader(_shader_name_for_group(display_name, color_key, backup=False), base_color)
                if not sg:
                    continue

                nodes = []
                if info.get("processed"):
                    nodes.extend(self._find_by_type(label, "master") or [])
                    nodes.extend(self._find_by_type(label, "instance") or [])
                    nodes.extend(self._find_by_type(label, "converted_geo") or [])
                else:
                    nodes.extend([m for m in info.get("meshes", []) if _exists(m)])

                stats["saved_shaders"] += _save_original_shaders_for_nodes(nodes, overwrite=False)
                assigned = _assign_shader_to_nodes(nodes, sg)
                if assigned:
                    stats["groups"] += 1
                    stats["nodes"] += assigned
                    stats["materials"] += 1

                if include_backups:
                    backup_nodes = []
                    backup_nodes.extend(self._find_by_type(label, "backup") or [])
                    backup_nodes.extend(self._find_by_type(label, "original_visible") or [])
                    if backup_nodes:
                        stats["saved_shaders"] += _save_original_shaders_for_nodes(backup_nodes, overwrite=False)
                        bkp_sg = _ensure_lambert_shader(_shader_name_for_group(display_name, color_key, backup=True), _dim_color(base_color, 0.42))
                        bkp_assigned = _assign_shader_to_nodes(backup_nodes, bkp_sg)
                        if bkp_assigned:
                            stats["nodes"] += bkp_assigned
                            stats["backups"] += bkp_assigned
                            stats["materials"] += 1
        return stats

    def _labels_from_selected_masters(self, selected_nodes=None):
        """Resolve processed group labels from selected IC masters or instances."""
        if selected_nodes is None:
            selected_nodes = _get_selected_transforms()
        selected_nodes = selected_nodes or []

        # Rebuild once if the UI was opened after the scene already contained an
        # _INSTANCE_CLEANER hierarchy. This keeps the button useful in reopened
        # production scenes without forcing a geometry scan.
        if not self.validated_groups and _ic_root_path():
            try:
                self.rebuild_groups_from_scene()
            except Exception:
                pass

        labels = []
        masters = []
        candidates = []
        for node in selected_nodes:
            found = _collect_mesh_transforms_from_roots([node], include_ic=True)
            if found:
                candidates.extend(found)
            elif _exists(node):
                candidates.append(_long(node))

        for node in _dedupe_keep_order(candidates):
            try:
                ic_type = _get_ic_attr(node, ATTR_IC_TYPE, "")
                if ic_type == "master":
                    masters.append(_long(node))
                internal_id = _get_ic_attr(node, ATTR_IC_SOURCE, "")
                label = self._label_for_internal_id(internal_id) if internal_id else None
                if not label:
                    label = self.find_group_for_mesh(node, allow_compute=False)
                if label and self.validated_groups.get(label, {}).get("processed"):
                    labels.append(label)
            except Exception:
                pass

        if not labels and _ic_root_path():
            try:
                self.rebuild_groups_from_scene()
                for node in _dedupe_keep_order(candidates):
                    internal_id = _get_ic_attr(node, ATTR_IC_SOURCE, "")
                    label = self._label_for_internal_id(internal_id) if internal_id else None
                    if label and self.validated_groups.get(label, {}).get("processed"):
                        labels.append(label)
            except Exception:
                pass

        return _dedupe_keep_order(labels), _dedupe_keep_order(masters)

    def assign_master_shaders_to_instances(self, selected_nodes=None, labels=None):
        """Apply each group's master material to all of its IC instances.

        The selection can be a master, an instance, or a group containing IC
        meshes. If the master reports both initialShadingGroup and one real
        material, the real non-default material is used. True multi-material
        masters are kept safe: direct instance overrides are removed so the
        shared master shape assignments remain visible.
        """
        selected_masters = []
        if labels is None:
            labels, selected_masters = self._labels_from_selected_masters(selected_nodes=selected_nodes)
        else:
            labels = [l for l in _dedupe_keep_order(labels or []) if l in self.validated_groups]

        stats = {
            "groups": 0,
            "masters": len(selected_masters),
            "instances": 0,
            "assigned": 0,
            "cleared_overrides": 0,
            "missing_master": 0,
            "missing_instances": 0,
            "missing_shader": 0,
            "multi_material_groups": 0,
            "used_shaders": [],
        }
        if not labels:
            return stats

        with UndoChunk("InstanceCleanerMasterShadersToInstances"):
            for label in labels:
                if label not in self.validated_groups:
                    continue
                masters = self._find_by_type(label, "master")
                instances = self._find_by_type(label, "instance")
                if not masters:
                    stats["missing_master"] += 1
                    continue
                if not instances:
                    stats["missing_instances"] += 1
                    continue

                master = masters[0]
                preferred = _preferred_master_shading_group(master)
                sg = preferred.get("sg")

                stats["instances"] += len(instances)
                if sg:
                    stats["cleared_overrides"] += _remove_object_shader_assignments(instances)
                    assigned = _assign_shader_to_nodes(instances, sg)
                    stats["assigned"] += assigned
                    if assigned:
                        stats["groups"] += 1
                        label_name = preferred.get("label") or _short(sg)
                        if label_name and label_name not in stats["used_shaders"]:
                            stats["used_shaders"].append(label_name)
                    else:
                        stats["missing_shader"] += 1
                elif preferred.get("mode") == "multi_non_default":
                    removed = _remove_object_shader_assignments(instances)
                    stats["cleared_overrides"] += removed
                    stats["assigned"] += len(instances)
                    stats["groups"] += 1
                    stats["multi_material_groups"] += 1
                elif _exists("initialShadingGroup"):
                    assigned = _assign_shader_to_nodes(instances, "initialShadingGroup")
                    stats["assigned"] += assigned
                    if assigned:
                        stats["groups"] += 1
                        if "initialShadingGroup" not in stats["used_shaders"]:
                            stats["used_shaders"].append("initialShadingGroup")
                    else:
                        stats["missing_shader"] += 1
                else:
                    stats["missing_shader"] += 1

        try:
            cmds.refresh(force=True)
        except Exception:
            pass
        return stats

    def rename_processed_group_nodes(self, labels=None, include_backups=True, include_converted=True):
        """Cleanly rename processed Instance Cleaner nodes for selected groups.

        Naming convention:
          - master:    MASTER_<GROUP>
          - instances: <GROUP>_INST_001, <GROUP>_INST_002, ...
          - backups:   <GROUP>_BKP_001,  <GROUP>_BKP_002,  ...
          - converted: <GROUP>_GEO_001,  <GROUP>_GEO_002,  ...

        The method keeps Instance Cleaner tags intact and deliberately does not
        rename visible original source meshes outside the IC hierarchy, so a
        cancel/restore workflow can still bring them back safely.
        """
        if labels is None:
            labels = list(self.validated_groups.keys())
        labels = [l for l in _dedupe_keep_order(labels or []) if l in self.validated_groups]
        stats = {
            "groups": 0,
            "masters": 0,
            "instances": 0,
            "backups": 0,
            "converted": 0,
            "helpers": 0,
            "retagged": 0,
            "skipped": 0,
        }
        if not labels:
            return stats

        _root, _masters_root, instances_root, backups_root, converted_root = _ensure_ic_groups()
        lm, li, lb, lc = _ensure_ic_layers()

        with UndoChunk("InstanceCleanerRenameProcessedNodes"):
            for label in labels:
                info = self.validated_groups.get(label, {})
                if not info or not info.get("processed"):
                    stats["skipped"] += 1
                    continue

                internal_id = info.get("internal_id", label)
                display_name = _safe_name(info.get("display_name", label))
                if not display_name:
                    display_name = _safe_name(label)
                info["display_name"] = display_name

                group_id = int(info.get("group_id", -1) or -1)
                match_type = info.get("source_match_type", info.get("type", MATCH_PROCESSED))
                score = float(info.get("score", 0.0) or 0.0)

                masters = _sort_nodes_for_numbering(self._find_by_type(label, "master"))
                instances = _sort_nodes_for_numbering(self._find_by_type(label, "instance"))
                backups = _sort_nodes_for_numbering(self._find_by_type(label, "backup")) if include_backups else []
                converted = _sort_nodes_for_numbering(self._find_by_type(label, "converted_geo")) if include_converted else []
                originals = _sort_nodes_for_numbering(self._find_by_type(label, "original_visible"))

                if not (masters or instances or backups or converted or originals):
                    stats["skipped"] += 1
                    continue

                inst_grp = _ensure_group("{}_INSTANCES".format(display_name), instances_root)
                bkp_grp = _ensure_group("{}_BACKUPS".format(display_name), backups_root)
                stats["helpers"] += 2

                renamed_masters = []
                for idx, node in enumerate(masters, 1):
                    base = "MASTER_{}".format(display_name) if len(masters) == 1 else "MASTER_{}_{:03d}".format(display_name, idx)
                    node = _rename_node_safe(node, base)
                    _tag_node(node, "master", group_id, internal_id, display_name, match_type, score)
                    _add_ic_attr(node, ATTR_IC_PROCESSED, True, "bool")
                    _add_to_layer(lm, [node])
                    renamed_masters.append(node)
                    stats["masters"] += 1
                    stats["retagged"] += 1

                renamed_instances = []
                for idx, node in enumerate(instances, 1):
                    node = _parent_absolute_if_possible(node, inst_grp)
                    node = _rename_node_safe(node, "{}_INST_{:03d}".format(display_name, idx))
                    _tag_node(node, "instance", group_id, internal_id, display_name, match_type, score)
                    _add_ic_attr(node, ATTR_IC_PROCESSED, True, "bool")
                    _add_to_layer(li, [node])
                    renamed_instances.append(node)
                    stats["instances"] += 1
                    stats["retagged"] += 1

                renamed_backups = []
                for idx, node in enumerate(backups, 1):
                    node = _parent_absolute_if_possible(node, bkp_grp)
                    node = _rename_node_safe(node, "{}_BKP_{:03d}".format(display_name, idx))
                    _tag_node(node, "backup", group_id, internal_id, display_name, match_type, score)
                    _add_ic_attr(node, ATTR_IC_PROCESSED, True, "bool")
                    _add_to_layer(lb, [node])
                    renamed_backups.append(node)
                    stats["backups"] += 1
                    stats["retagged"] += 1

                renamed_converted = []
                for idx, node in enumerate(converted, 1):
                    node = _parent_absolute_if_possible(node, converted_root)
                    node = _rename_node_safe(node, "{}_GEO_{:03d}".format(display_name, idx))
                    _tag_node(node, "converted_geo", group_id, internal_id, display_name, match_type, score)
                    _add_ic_attr(node, ATTR_IC_PROCESSED, True, "bool")
                    _add_to_layer(lc, [node])
                    renamed_converted.append(node)
                    stats["converted"] += 1
                    stats["retagged"] += 1

                # Keep tags coherent on visible originals, but do not rename those user-scene nodes.
                for node in originals:
                    _tag_node(node, "original_visible", group_id, internal_id, display_name, match_type, score)
                    _add_ic_attr(node, ATTR_IC_PROCESSED, True, "bool")
                    stats["retagged"] += 1

                primary_meshes = renamed_instances or renamed_backups or originals or renamed_converted or renamed_masters
                info["meshes"] = _dedupe_keep_order(primary_meshes)
                if renamed_masters:
                    try:
                        self.master_manager.masters[internal_id] = renamed_masters[0]
                    except Exception:
                        pass
                stats["groups"] += 1

            self._delete_empty_ic_subgroups()

        return stats


    def select_all_masters(self):
        masters = []
        root = _ic_group_path(MASTERS_GROUP)
        if root:
            for mesh in _iter_mesh_transforms(root, include_ic=True):
                if _get_ic_attr(mesh, ATTR_IC_TYPE, "") == "master":
                    masters.append(mesh)
        if masters:
            cmds.select(masters, r=True)
        return len(masters)

    def get_nodes_for_label(self, label, target="source"):
        resolved_label = self.resolve_group_label(label)
        if resolved_label:
            label = resolved_label
        elif label not in self.validated_groups:
            return []
        if target == "master":
            return self._find_by_type(label, "master")
        if target == "instances":
            return self._find_by_type(label, "instance")
        if target == "backups":
            return self._find_by_type(label, "backup")

        info = self.validated_groups.get(label, {})
        source_nodes = [m for m in info.get("meshes", []) if _exists(m)]
        if source_nodes:
            return _dedupe_keep_order(source_nodes)

        # After PROCESS, originals are moved/renamed into the hidden backup area,
        # so the old source paths in the group are no longer valid. Make the SRC
        # button useful by falling back to those tagged backups (or visible
        # originals when processing used that mode).
        if info.get("processed"):
            backups = self._find_by_type(label, "backup")
            if backups:
                return backups
            originals = self._find_by_type(label, "original_visible")
            if originals:
                return originals
        return []

    def find_labels_for_nodes(self, nodes, allow_compute=False):
        labels = []
        for node in _collect_mesh_transforms_from_roots(nodes, include_ic=True):
            label = self.find_group_for_mesh(node, allow_compute=allow_compute)
            if label and label not in labels:
                labels.append(label)
        return labels

    def merge_groups(self, labels, primary_label=None):
        labels = [l for l in labels
                  if l in self.validated_groups and not self.validated_groups[l].get("processed")]
        labels = list(dict.fromkeys(labels))
        if len(labels) < 2:
            return {"merged": 0, "target": primary_label, "meshes": 0}
        if primary_label not in labels:
            primary_label = labels[0]

        target       = self.validated_groups[primary_label]
        all_meshes   = []
        score        = float(target.get("score", 1.) or 1.)
        has_fuzzy    = target.get("type") == MATCH_FUZZY
        # Start empty; the target group is included by the loop below.
        acc_values   = []
        merged_count = 0

        for label in labels:
            info = self.validated_groups.get(label)
            if not info:
                continue
            all_meshes.extend(info.get("meshes", []))
            score = min(score, float(info.get("score", 1.) or 1.))
            if info.get("type") == MATCH_FUZZY:
                has_fuzzy = True
            acc_values.append(info.get("accepted"))

        target["meshes"]            = _dedupe_keep_order([m for m in all_meshes if _exists(m)])
        target["type"]              = MATCH_FUZZY if has_fuzzy else MATCH_SAFE
        target["source_match_type"] = target["type"]
        target["score"]             = score
        target["accepted"]          = True if True in acc_values else None
        target["internal_id"]       = target.get("internal_id", primary_label)

        for label in labels:
            if label != primary_label and label in self.validated_groups:
                del self.validated_groups[label]
                merged_count += 1

        self._renumber_groups()
        return {"merged": merged_count, "target": primary_label, "meshes": len(target["meshes"])}

    def split_selected_from_group(self, label, selected_nodes):
        if label not in self.validated_groups:
            return {"split": 0, "new_label": None}
        info = self.validated_groups[label]
        if info.get("processed"):
            return {"split": 0, "new_label": None}

        sel_meshes   = _dedupe_keep_order([m for m in
                         _collect_mesh_transforms_from_roots(selected_nodes) if _exists(m)])
        group_meshes = _dedupe_keep_order([m for m in info.get("meshes", []) if _exists(m)])
        group_set    = set(group_meshes)
        split_meshes = [m for m in sel_meshes if m in group_set]

        if not split_meshes or len(split_meshes) >= len(group_meshes):
            return {"split": 0, "new_label": None}

        info["meshes"] = [m for m in group_meshes if m not in set(split_meshes)]
        self._manual_group_counter += 1
        new_iid   = "manual_split_{:03d}_{}".format(self._manual_group_counter,
                                                     _hash_blob(split_meshes)[:10])
        new_label = _unique_label(new_iid, self.validated_groups)
        used_names = set(g.get("display_name","") for g in self.validated_groups.values())
        dname     = _unique_label(info.get("display_name","Group")+"_SPLIT", used_names)

        self.validated_groups[new_label] = {
            "meshes":            split_meshes,
            "type":              MATCH_FUZZY,
            "accepted":          True if info.get("accepted") is True else None,
            "group_id":          0,
            "processed":         False,
            "internal_id":       new_label,
            "display_name":      dname,
            "score":             float(info.get("score", 0.85) or 0.85),
            "source_match_type": MATCH_FUZZY,
        }
        self._renumber_groups()
        return {"split": len(split_meshes), "new_label": new_label}

    def add_selected_to_group(self, label, selected_nodes):
        if label not in self.validated_groups:
            return {"added": 0, "total": 0, "removed_from_other": 0}
        info = self.validated_groups[label]
        if info.get("processed"):
            return {"added": 0, "total": len(info.get("meshes", [])), "removed_from_other": 0}

        selected_meshes = _dedupe_keep_order([m for m in
                          _collect_mesh_transforms_from_roots(selected_nodes)
                          if _exists(m) and not _is_under_ic_root(m)])
        if not selected_meshes:
            return {"added": 0, "total": len(info.get("meshes", [])), "removed_from_other": 0}

        current = _dedupe_keep_order([m for m in info.get("meshes", []) if _exists(m)])
        current_set = set(current)
        to_add = [m for m in selected_meshes if m not in current_set]
        removed_from_other = 0

        # Keep one source mesh in one editable group only; otherwise processing can
        # instance the same original twice.
        add_set = set(to_add)
        for other_label, other in list(self.validated_groups.items()):
            if other_label == label or other.get("processed"):
                continue
            before = _dedupe_keep_order([m for m in other.get("meshes", []) if _exists(m)])
            after = [m for m in before if m not in add_set]
            if len(after) != len(before):
                other["meshes"] = after
                removed_from_other += len(before) - len(after)

        info["meshes"] = _dedupe_keep_order(current + to_add)
        if to_add:
            info["type"] = MATCH_FUZZY
            info["source_match_type"] = MATCH_FUZZY
            # Manual additions usually mean "this is the group I want". Keep the
            # group processable instead of silently moving it back to a pending
            # review state, unless the user explicitly rejected it before.
            if info.get("accepted") is not False:
                info["accepted"] = True
            info["score"] = min(float(info.get("score", 0.85) or 0.85), 0.85)
        self._renumber_groups()
        return {"added": len(to_add), "total": len(info["meshes"]), "removed_from_other": removed_from_other}

    def remove_selected_from_group(self, label, selected_nodes):
        if label not in self.validated_groups:
            return {"removed": 0, "total": 0, "kept_minimum": False}
        info = self.validated_groups[label]
        if info.get("processed"):
            return {"removed": 0, "total": len(info.get("meshes", [])), "kept_minimum": False}

        selected_meshes = set(_dedupe_keep_order([m for m in
                              _collect_mesh_transforms_from_roots(selected_nodes) if _exists(m)]))
        current = _dedupe_keep_order([m for m in info.get("meshes", []) if _exists(m)])
        if not selected_meshes or not current:
            return {"removed": 0, "total": len(current), "kept_minimum": False}

        remaining = [m for m in current if m not in selected_meshes]
        removed = len(current) - len(remaining)
        kept_minimum = False
        if removed and len(remaining) < 2:
            # A one-mesh group is not useful for instancing. Keep enough meshes so
            # the group stays processable and visible instead of silently breaking it.
            needed = 2 - len(remaining)
            restore = [m for m in current if m in selected_meshes][:needed]
            remaining = _dedupe_keep_order(remaining + restore)
            removed = len(current) - len(remaining)
            kept_minimum = True

        if removed:
            info["meshes"] = remaining
            info["accepted"] = None if info.get("accepted") is True else info.get("accepted")
        self._renumber_groups()
        return {"removed": removed, "total": len(info.get("meshes", [])), "kept_minimum": kept_minimum}

    def keep_only_groups_for_nodes(self, nodes):
        seed_meshes = _dedupe_keep_order([m for m in
                         _collect_mesh_transforms_from_roots(nodes) if _exists(m)])
        labels = []
        for mesh in seed_meshes:
            label = self.find_group_for_mesh(mesh, allow_compute=True)
            if label and label not in labels:
                labels.append(label)

        keep = set(labels)
        for label in list(self.validated_groups.keys()):
            if label not in keep:
                del self.validated_groups[label]
        self._renumber_groups()
        return labels

    def set_preferred_master_from_selection(self, label, selected_nodes):
        if label not in self.validated_groups:
            return None
        info = self.validated_groups[label]
        if info.get("processed"):
            return None

        selected_meshes = _dedupe_keep_order([m for m in
                          _collect_mesh_transforms_from_roots(selected_nodes) if _exists(m)])
        group_meshes = _dedupe_keep_order([m for m in info.get("meshes", []) if _exists(m)])
        group_set    = set(group_meshes)
        preferred    = next((m for m in selected_meshes if m in group_set), None)
        if not preferred:
            return None

        info["meshes"] = [preferred] + [m for m in group_meshes if m != preferred]
        return preferred

    def organize_masters(self, spacing=10.):
        root = _ic_group_path(MASTERS_GROUP)
        if not root:
            cmds.warning("[IC] No master group.")
            return {"organized": 0}
        masters = [m for m in _iter_mesh_transforms(root, include_ic=True)
                   if _get_ic_attr(m, ATTR_IC_TYPE, "") == "master"]
        if not masters:
            return {"organized": 0}

        masters = sorted(masters, key=lambda x: _short(x).lower())
        n       = len(masters)
        cols    = max(1, int(math.ceil(math.sqrt(n))))
        sizes   = []
        for m in masters:
            _, s = _world_bbox(m)
            sizes.append(s)

        col_widths = [0.]*cols
        row_depths = []
        for i, s in enumerate(sizes):
            r = i // cols
            c = i % cols
            while len(row_depths) <= r:
                row_depths.append(0.)
            col_widths[c] = max(col_widths[c], s[0])
            row_depths[r] = max(row_depths[r], s[2])

        x_centers = [0.]
        for c in range(1, cols):
            x_centers.append(x_centers[-1] + col_widths[c-1]*0.5 + col_widths[c]*0.5 + spacing)
        z_centers = [0.]
        for r in range(1, len(row_depths)):
            z_centers.append(z_centers[-1] + row_depths[r-1]*0.5 + row_depths[r]*0.5 + spacing)

        tx = x_centers[-1] if x_centers else 0.
        tz = z_centers[-1] if z_centers else 0.

        with UndoChunk("InstanceCleanerOrganizeMasters"):
            for i, m in enumerate(masters):
                r   = i // cols
                c   = i % cols
                tgt = (x_centers[c]-tx*0.5, sizes[i][1]*0.5, -(z_centers[r]-tz*0.5))
                cc, _ = _world_bbox(m)
                try:
                    cmds.move(tgt[0]-cc[0], tgt[1]-cc[1], tgt[2]-cc[2], m, r=True, ws=True)
                except Exception as e:
                    cmds.warning("[IC] organize_masters move failed {}: {}".format(m, e))

        return {"organized": n}

    def exit_isolate(self):
        _exit_isolate_all_panels()

    def _next_batch_id(self):
        latest = self.find_latest_batch_id()
        self._batch_counter = max(self._batch_counter, latest or 0)
        self._batch_counter += 1
        return self._batch_counter

    def find_latest_batch_id(self):
        latest = None
        for node in self._all_ic_meshes():
            v = _get_ic_attr(node, ATTR_IC_BATCH, None)
            if v is None:
                continue
            try:
                v = int(v)
            except Exception:
                continue
            if latest is None or v > latest:
                latest = v
        return latest

    def find_group_for_mesh(self, mesh, allow_compute=False):
        if not mesh or not _exists(mesh):
            return None
        mesh = _long(mesh)

        iid = _get_ic_attr(mesh, ATTR_IC_SOURCE, "")
        if iid:
            for label, info in self.validated_groups.items():
                if info.get("internal_id") == iid:
                    return label

        for label, info in self.validated_groups.items():
            if mesh in [_long(m) for m in info.get("meshes", []) if _exists(m)]:
                return label

        sel_sig = self.signature_by_transform.get(mesh)
        if sel_sig is None:
            if not allow_compute:
                return None
            try:
                sel_sig = _compute_signature(mesh, strict_tol=0.001)
            except Exception:
                return None
            if sel_sig is not None:
                self.signature_by_transform[mesh] = sel_sig
        if not sel_sig:
            return None

        best_label = None
        best_score = 0.

        for label, info in self.validated_groups.items():
            meshes = info.get("meshes", [])
            if not meshes:
                continue
            rep = meshes[0]
            if not _exists(rep):
                continue
            rep_long = _long(rep)
            rep_sig = self.signature_by_transform.get(rep_long)
            if rep_sig is None:
                if not allow_compute:
                    continue
                try:
                    rep_sig = _compute_signature(rep_long, strict_tol=0.001)
                except Exception:
                    rep_sig = None
                if rep_sig is not None:
                    self.signature_by_transform[rep_long] = rep_sig
            if rep_sig is None:
                continue

            if sel_sig.strict_hash == rep_sig.strict_hash:
                return label

            sc = _fuzzy_score(sel_sig, rep_sig, vertex_tol=0, size_tol=0.04)
            if sc > best_score:
                best_score = sc
                best_label = label

        return best_label if best_label and best_score >= 0.92 else None

    def create_masters_and_replace(self, master_spacing=10.,
                                   keep_hidden_backups=True,
                                   delete_originals=False,
                                   use_pca_icp_alignment=True,
                                   progress_cb=None,
                                   cancel_cb=None,
                                   labels=None):
        requested_labels = None if labels is None else [l for l in labels if l in self.validated_groups]
        if requested_labels is None:
            accepted = {
                label: info
                for label, info in self.validated_groups.items()
                if info["accepted"] is True and not info.get("processed")
            }
            process_scope = "accepted groups"
        else:
            accepted = {
                label: self.validated_groups[label]
                for label in requested_labels
                if not self.validated_groups[label].get("processed")
            }
            process_scope = "requested group(s)"

        if not accepted:
            cmds.warning("[IC] No unprocessed {} to process.".format(process_scope))
            return {}

        if delete_originals:
            cmds.warning("[IC] delete_originals=True permanently deletes source meshes; cancel_last_process cannot restore them. Prefer keep_hidden_backups=True for rollback-safe processing.")

        batch_id                = self._next_batch_id()
        self.last_process_batch = batch_id

        stats = {
            "masters_created":   0,
            "instances_created": 0,
            "backups_created":   0,
            "originals_visible": 0,
            "groups_skipped":    0,
            "canceled":          False,
            "rollback":          None,
            "alignment_skipped": 0,
            "processed_labels": [],
            "process_scope": process_scope,
        }

        total_meshes = sum(
            len([m for m in info["meshes"] if _exists(m)])
            for info in accepted.values()
        )
        progress_state = {"current": 0, "total": max(1, total_meshes)}

        try:
            with UndoChunk("InstanceCleanerProcess"):
                _ensure_ic_groups()
                _ensure_ic_layers()
                process_index = 0

                for label, info in accepted.items():
                    if cancel_cb and cancel_cb():
                        raise ProcessCanceled()

                    meshes = [m for m in info["meshes"]
                              if _exists(m) and not _get_ic_attr(m, ATTR_IC_PROCESSED, False)]

                    if not meshes:
                        stats["groups_skipped"] += 1
                        continue

                    internal_id    = info["internal_id"]
                    display_name   = info["display_name"]
                    group_id       = info["group_id"]
                    match_type     = info.get("type", MATCH_SAFE)
                    score          = float(info.get("score", 1.))
                    reference_mesh = next((m for m in meshes if _exists(m)), None)
                    if reference_mesh is None:
                        stats["groups_skipped"] += 1
                        continue

                    existed = self.master_manager.find_existing_master(internal_id)

                    if progress_cb:
                        progress_cb(current=progress_state["current"], total=progress_state["total"],
                                    message="Creating master {}".format(display_name),
                                    step="Create master", group=display_name, mesh=reference_mesh)

                    master = self.master_manager.create_master(
                        internal_id, display_name, reference_mesh,
                        group_id, match_type, score,
                        spacing=master_spacing, index=process_index,
                        batch_id=None if existed else batch_id,
                    )

                    if master is None:
                        stats["groups_skipped"] += 1
                        continue

                    if not existed:
                        stats["masters_created"] += 1

                    instances, backups, originals = self.master_manager.replace_with_instances(
                        internal_id, display_name, meshes,
                        group_id, match_type, score,
                        keep_hidden_backups=keep_hidden_backups,
                        delete_originals=delete_originals,
                        use_pca_icp_alignment=use_pca_icp_alignment,
                        batch_id=batch_id,
                        progress_cb=progress_cb,
                        cancel_cb=cancel_cb,
                        progress_state=progress_state,
                    )

                    stats["instances_created"]  += len(instances)
                    stats["backups_created"]    += len(backups)
                    stats["originals_visible"]  += len(originals)
                    stats["alignment_skipped"] += progress_state.get("alignment_skipped", 0)
                    progress_state["alignment_skipped"] = 0
                    if instances or backups or originals:
                        info["processed"] = True
                        info["accepted"] = False
                        info["processed_batch"] = batch_id
                        if str(label).startswith("fast_selected_"):
                            info["temporary_fast_group"] = False
                        stats["processed_labels"].append(label)
                    else:
                        stats["groups_skipped"] += 1
                    process_index += 1

        except ProcessCanceled:
            stats["canceled"] = True
            try:
                stats["rollback"] = self.cancel_last_process(batch_id=batch_id)
            except Exception as e:
                cmds.warning("[IC] Rollback failed: {}".format(e))

        return stats

    def cancel_last_process(self, batch_id=None):
        if batch_id is None:
            batch_id = self.last_process_batch
        if batch_id is None:
            batch_id = self.find_latest_batch_id()
        if batch_id is None:
            cmds.warning("[IC] No batch to cancel.")
            return {"restored": 0, "deleted_instances": 0, "deleted_masters": 0}

        restored          = []
        restored_by_source = defaultdict(list)
        deleted_instances = 0
        deleted_masters   = 0

        with UndoChunk("InstanceCleanerCancelProcess"):
            for node in list(self._all_ic_meshes()):
                if not _exists(node):
                    continue
                try:
                    node_bid = int(_get_ic_attr(node, ATTR_IC_BATCH, -1) or -1)
                except Exception:
                    continue
                if node_bid != int(batch_id):
                    continue
                node_type = _get_ic_attr(node, ATTR_IC_TYPE, "")
                if node_type == "instance":
                    try:
                        cmds.delete(node)
                        deleted_instances += 1
                    except Exception:
                        pass
                elif node_type == "original_visible":
                    try:
                        _remove_from_display_layers([node])
                        _clear_ic_attrs(node)
                    except Exception:
                        pass

            root = _ic_group_path(BACKUP_GROUP)
            if root:
                backups = _iter_mesh_transforms(root, include_ic=True)
                for bkp in backups:
                    if not _exists(bkp):
                        continue
                    try:
                        node_bid = int(_get_ic_attr(bkp, ATTR_IC_BATCH, -1) or -1)
                    except Exception:
                        continue
                    if node_bid != int(batch_id):
                        continue
                    if _get_ic_attr(bkp, ATTR_IC_TYPE, "") != "backup":
                        continue

                    source_id   = _get_ic_attr(bkp, ATTR_IC_SOURCE, "")
                    orig_parent = _get_ic_attr(bkp, ATTR_IC_ORIG_PARENT, "")
                    orig_name   = _get_ic_attr(bkp, ATTR_IC_ORIG_NAME, _short(bkp))
                    orig_vis    = _get_ic_attr(bkp, ATTR_IC_ORIG_VIS, 1)
                    orig_layers = _get_ic_attr(bkp, ATTR_IC_ORIG_LAYERS, "")
                    orig_matrix = _get_ic_attr(bkp, ATTR_IC_ORIG_MATRIX, "")

                    _remove_from_display_layers([bkp])

                    try:
                        if orig_parent and _exists(orig_parent):
                            bkp = cmds.parent(bkp, orig_parent, absolute=True)[0]
                        else:
                            bkp = cmds.parent(bkp, world=True)[0]
                    except Exception:
                        pass

                    try:
                        bkp = cmds.rename(bkp, orig_name)
                    except Exception:
                        pass

                    bkp = cmds.ls(bkp, long=True)[0]
                    try:
                        if orig_matrix:
                            vals = [float(v) for v in str(orig_matrix).split(",") if v != ""]
                            if len(vals) == 16:
                                _apply_world_matrix(bkp, vals)
                    except Exception:
                        pass
                    try:
                        cmds.setAttr(bkp+".visibility", bool(int(orig_vis)))
                    except Exception:
                        pass
                    _restore_display_layers(bkp, orig_layers)
                    _clear_ic_attrs(bkp)
                    restored.append(bkp)
                    if source_id:
                        restored_by_source[source_id].append(bkp)

            root = _ic_group_path(MASTERS_GROUP)
            if root:
                masters = _iter_mesh_transforms(root, include_ic=True)
                for mst in masters:
                    if not _exists(mst):
                        continue
                    try:
                        node_bid = int(_get_ic_attr(mst, ATTR_IC_BATCH, -1) or -1)
                    except Exception:
                        continue
                    if node_bid != int(batch_id):
                        continue
                    if _get_ic_attr(mst, ATTR_IC_TYPE, "") != "master":
                        continue
                    try:
                        cmds.delete(mst)
                        deleted_masters += 1
                    except Exception:
                        pass

            try:
                if restored:
                    cmds.select(restored, r=True)
            except Exception:
                pass

        if restored_by_source:
            for _label, _info in self.validated_groups.items():
                if _info.get("processed_batch") != batch_id:
                    continue
                _internal_id = _info.get("internal_id", _label)
                _restored = restored_by_source.get(_internal_id, [])
                if _restored:
                    _info["meshes"] = _dedupe_keep_order(_restored)
                _info["processed"] = False
                _info["accepted"] = True
                _info.pop("processed_batch", None)

        if self.last_process_batch == batch_id:
            self.last_process_batch = None

        return {"restored": len(restored),
                "deleted_instances": deleted_instances,
                "deleted_masters": deleted_masters}

    def _convert_instance_nodes_to_geometry(self, instances, select_converted=False, chunk_name="InstanceCleanerConvertInstances"):
        if not _ic_group_path(INSTANCES_GROUP):
            cmds.warning("[IC] No instance group.")
            return {"converted": 0, "requested": 0}

        _, _, _, _, conv_root = _ensure_ic_groups()
        _, li, _, lc          = _ensure_ic_layers()

        instances = _dedupe_keep_order([
            n for n in (instances or [])
            if _exists(n) and _get_ic_attr(_long(n), ATTR_IC_TYPE, "") == "instance"
        ])
        if not instances:
            cmds.warning("[IC] No matching instance(s) to convert.")
            return {"converted": 0, "requested": 0}

        converted = []
        with UndoChunk(chunk_name):
            for idx, inst in enumerate(instances):
                if not _exists(inst):
                    continue
                try:
                    inst     = cmds.ls(inst, long=True)[0]
                    mat      = _get_world_matrix(inst)
                    dname    = _get_ic_attr(inst, ATTR_IC_GROUP_NAME, "Converted")
                    mt       = _get_ic_attr(inst, ATTR_IC_MATCH_TYPE, "")
                    sc       = float(_get_ic_attr(inst, ATTR_IC_SCORE, 0.) or 0.)
                    gid      = int(_get_ic_attr(inst, ATTR_IC_GROUP, 0) or 0)
                    src      = _get_ic_attr(inst, ATTR_IC_SOURCE, "")
                    new_name = "GEO_{:03d}_{}".format(idx, _safe_name(dname))
                    geo      = _duplicate_independent_transform(inst, new_name)
                    geo      = cmds.parent(geo, conv_root, absolute=True)[0]
                    _apply_world_matrix(geo, mat)
                    geo      = cmds.ls(geo, long=True)[0]
                    _tag_node(geo, "converted_geo", gid, src, dname, mt, sc)
                    _add_ic_attr(geo, ATTR_IC_PROCESSED, True, "bool")
                    _add_to_layer(lc, [geo])
                    converted.append(geo)
                    cmds.delete(inst)
                except Exception as e:
                    cmds.warning("[IC] Convert failed for {}: {}".format(inst, e))

        try:
            cmds.setAttr(lc+".visibility", 1)
            cmds.setAttr(li+".visibility", 1)
        except Exception:
            pass

        if select_converted and converted:
            try:
                cmds.select(converted, r=True)
            except Exception:
                pass

        return {"converted": len(converted), "requested": len(instances)}

    def convert_instances_to_geometry(self):
        inst_root = _ic_group_path(INSTANCES_GROUP)
        if not inst_root:
            cmds.warning("[IC] No instance group.")
            return {"converted": 0, "requested": 0}

        instances = [n for n in _iter_mesh_transforms(inst_root, include_ic=True)
                     if _get_ic_attr(n, ATTR_IC_TYPE, "") == "instance"]
        return self._convert_instance_nodes_to_geometry(
            instances, select_converted=False, chunk_name="InstanceCleanerConvertAllInstances")

    def convert_selected_instances_to_geometry(self, selected_nodes=None):
        selected_nodes = selected_nodes or _get_selected_transforms()
        if not selected_nodes:
            cmds.warning("[IC] Select one or more Instance Cleaner instance(s) first.")
            return {"converted": 0, "requested": 0}

        candidates = _collect_mesh_transforms_from_roots(selected_nodes, include_ic=True)
        instances = [n for n in candidates
                     if _exists(n) and _get_ic_attr(n, ATTR_IC_TYPE, "") == "instance"]
        return self._convert_instance_nodes_to_geometry(
            instances, select_converted=True, chunk_name="InstanceCleanerConvertSelectedInstances")

    def get_report(self):
        accepted  = [l for l,i in self.validated_groups.items() if i["accepted"] is True]
        processed = [l for l,i in self.validated_groups.items() if i.get("processed")]
        fuzzy     = [l for l,i in self.validated_groups.items()
                     if i.get("type") == MATCH_FUZZY and not i.get("processed")]
        safe      = [l for l,i in self.validated_groups.items()
                     if i.get("type") == MATCH_SAFE and not i.get("processed")]
        rejected  = [l for l,i in self.validated_groups.items()
                     if i["accepted"] is False and not i.get("processed")]
        return {
            "total_scanned":    len(self.signatures),
            "safe_groups":      len(safe),
            "fuzzy_groups":     len(fuzzy),
            "unique_meshes":    len(self.uniques),
            "accepted_groups":  len(accepted),
            "processed_groups": len(processed),
            "rejected_groups":  len(rejected),
            "total_groups":     len(self.validated_groups),
        }


# ---------------------------------------------------------------------------
# UI widgets
# ---------------------------------------------------------------------------
class ColorBtn(QPushButton):
    def __init__(self, text="", tip="", bg="#2d2d2d", fg="#a0a0a0", w=None, h=24, parent=None):
        super(ColorBtn, self).__init__(text, parent)
        self._base_w = w
        self._base_h = int(h or 24)
        self._base_bg = bg or "#2d2d2d"
        self._base_fg = fg or "#a0a0a0"
        self._ui_scale = 1.0
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tip)
        self.setSizePolicy(QSizePolicy.MinimumExpanding if w else QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._apply_scaled_metrics()

    def _s(self, value):
        return max(1, int(round(float(value) * float(self._ui_scale))))

    def set_ui_scale(self, scale):
        self._ui_scale = float(scale or 1.0)
        self._apply_scaled_metrics()

    def _apply_scaled_metrics(self):
        h = self._s(self._base_h)
        self.setMinimumHeight(h)
        self.setMaximumHeight(h)
        if self._base_w:
            self.setMinimumWidth(self._s(self._base_w))
        else:
            self.setMinimumWidth(self._s(42))

        label = (self.text() or "").upper()
        base_bg = self._base_bg
        base_fg = self._base_fg
        if base_bg.lower() in ("#2b2b2b", "#30282a"):
            base_bg = "#2b2b2b"
            base_fg = "#bdbdbd"
        elif base_bg.lower() in ("#bdbdbd", "#c7c7c7"):
            base_fg = "#bdbdbd"
        border = "#4a4a4a"
        # Keep most tools neutral grey. Only approve/process actions are green;
        # destructive/cancel actions are red.
        positive_labels = (
            "OK", "OK + NEXT", "SAFE", "SAFE OK",
            "ACCEPT", "ACCEPT ALL", "ACCEPT + NEXT", "PROCESS",
        )
        destructive_labels = (
            "NO", "NO + NEXT", "REJECT", "REJECT ALL",
            "REJECT + NEXT", "STOP", "CANCEL", "CANCEL PROCESS",
        )
        if label in positive_labels or label.startswith("ACCEPT") or label.startswith("OK"):
            base_bg = "#1f4a32"
            base_fg, border = "#c7d6cc", "#3d8a5d"
        elif (label in destructive_labels or label.startswith("REJECT") or
              label.startswith("CANCEL") or label.startswith("STOP")):
            base_bg = "#4b0e19"
            base_fg, border = "#c9b8bb", "#a3293d"
        elif "FUZZ" in label:
            base_bg = "#4b321d"
            base_fg, border = "#d1c0aa", "#9a6532"

        hover = QColor(base_bg).lighter(136).name()
        pressed = QColor(base_bg).darker(122).name()
        self.setStyleSheet(
            "QPushButton {{ background:{bg}; color:{fg}; border:1px solid {border};"
            " border-radius:{radius}px; font-family:Segoe UI, Arial, sans-serif;"
            " font-weight:700; font-size:{font}px; padding:{pv}px {ph}px; }}"
            "QPushButton:hover {{ background:{hover}; border-color:{hover_border}; color:#d0d0d0; }}"
            "QPushButton:pressed {{ background:{pressed}; padding-top:{press_pad}px; }}"
            "QPushButton:disabled {{ background:#202020; color:#5e5e5e; border-color:#303030; }}"
            .format(bg=base_bg, fg=base_fg, border=border,
                    hover=hover, hover_border=QColor(border).lighter(130).name(), pressed=pressed,
                    radius=self._s(4), font=self._s(8), pv=self._s(1), ph=self._s(4), press_pad=self._s(2))
        )
        self.updateGeometry()


class SectionLabel(QLabel):
    def __init__(self, text, parent=None):
        super(SectionLabel, self).__init__(text, parent)
        self._ui_scale = 1.0
        self._apply_scaled_metrics()

    def _s(self, value):
        return max(1, int(round(float(value) * float(self._ui_scale))))

    def set_ui_scale(self, scale):
        self._ui_scale = float(scale or 1.0)
        self._apply_scaled_metrics()

    def _apply_scaled_metrics(self):
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(self._s(20))
        self.setMaximumHeight(self._s(20))

        self.setStyleSheet(
            "color:#c3c3c3; font-family:Segoe UI, Arial, sans-serif; font-size:{font}px; "
            "font-weight:800; letter-spacing:.9px; padding:{pt}px {px}px {pb}px {px}px; "
            "border-left:{bar}px solid #7a1e2a; background:#1b1b1b; border-radius:{radius}px;"
            .format(
                font=self._s(8),
                pt=self._s(3),
                pb=self._s(2),
                px=self._s(6),
                bar=self._s(3),
                radius=self._s(4)
            )
        )
        self.updateGeometry()


class ParamSlider(QWidget):
    if PYSIDE_VERSION == 6:
        valueChanged = Signal(float)
    else:
        valueChanged = QtCore.Signal(float)

    def __init__(self, label, min_val, max_val, default, decimals=3, label_width=90, parent=None):
        super(ParamSlider, self).__init__(parent)
        self._mult = 10 ** decimals
        self._base_label_width = label_width
        self._ui_scale = 1.0

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0,0,0,0)
        self._layout.setSpacing(4)

        self._label = QLabel(label)
        self._label.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)

        self._slider = QSlider(Qt.Horizontal)
        self._slider.setRange(int(min_val*self._mult), int(max_val*self._mult))
        self._slider.setValue(int(default*self._mult))
        self._slider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self._spin = QDoubleSpinBox()
        self._spin.setRange(min_val, max_val)
        self._spin.setDecimals(decimals)
        self._spin.setValue(default)
        self._spin.setMinimumWidth(64)
        self._spin.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        self._spin.setButtonSymbols(QAbstractSpinBox.NoButtons)

        self._layout.addWidget(self._label)
        self._layout.addWidget(self._slider, 1)
        self._layout.addWidget(self._spin)

        self._slider.valueChanged.connect(self._on_slider)
        self._spin.valueChanged.connect(self._on_spin)
        self.set_ui_scale(1.0)

    def _s(self, value):
        return max(1, int(round(float(value) * float(self._ui_scale))))

    def set_ui_scale(self, scale):
        self._ui_scale = float(scale or 1.0)
        self._layout.setSpacing(self._s(4))
        self._label.setMinimumWidth(self._s(self._base_label_width))
        self._label.setStyleSheet("color:#9a9a9a; font-size:{}px;".format(self._s(9)))
        self._spin.setMinimumWidth(self._s(60))
        self._spin.setMinimumHeight(self._s(22))
        self._spin.setMaximumHeight(self._s(27))
        self._slider.setMinimumHeight(self._s(18))
        self._slider.setMaximumHeight(self._s(26))
        self.setMinimumHeight(self._s(24))
        self.setMaximumHeight(self._s(29))
        self.updateGeometry()

    def _on_slider(self, v):
        rv = v / float(self._mult)
        self._spin.blockSignals(True)
        self._spin.setValue(rv)
        self._spin.blockSignals(False)
        self.valueChanged.emit(rv)

    def _on_spin(self, v):
        self._slider.blockSignals(True)
        self._slider.setValue(int(v*self._mult))
        self._slider.blockSignals(False)
        self.valueChanged.emit(v)

    def value(self):
        return self._spin.value()


class GroupItem(QWidget):
    if PYSIDE_VERSION == 6:
        accept_clicked    = Signal(str)
        reject_clicked    = Signal(str)
        select_clicked    = Signal(str)
        master_clicked    = Signal(str)
        instances_clicked = Signal(str)
        backups_clicked   = Signal(str)
        all_clicked       = Signal(str)
        checked_changed   = Signal(str, bool)
        part_checked_changed = Signal(str, str, bool)
    else:
        accept_clicked    = QtCore.Signal(str)
        reject_clicked    = QtCore.Signal(str)
        select_clicked    = QtCore.Signal(str)
        master_clicked    = QtCore.Signal(str)
        instances_clicked = QtCore.Signal(str)
        backups_clicked   = QtCore.Signal(str)
        all_clicked       = QtCore.Signal(str)
        checked_changed   = QtCore.Signal(str, bool)
        part_checked_changed = QtCore.Signal(str, str, bool)

    def __init__(self, label, info, parent=None, ui_scale=1.0):
        super(GroupItem, self).__init__(parent)
        self.setObjectName("GroupItemCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.label = label
        self.info  = info
        self._highlighted = False
        self._ui_scale = float(ui_scale or 1.0)
        self._build()
        self.set_ui_scale(self._ui_scale)
        self.refresh()

    def _build(self):
        layout = QVBoxLayout(self)
        self._card_layout = layout
        layout.setContentsMargins(8,7,8,7)
        layout.setSpacing(5)

        header = QHBoxLayout()
        self._header_layout = header
        self.group_check = QCheckBox("")
        self.group_check.setToolTip("Check multiple group cards, then use MERGE SEL GROUPS.")
        self.group_check.stateChanged.connect(
            lambda _v: self.checked_changed.emit(self.label, self.group_check.isChecked()))
        self.badge       = QLabel("")
        self.badge.setMinimumSize(68, 20)
        self.badge.setAlignment(Qt.AlignCenter)
        self.name_label  = QLabel(self.info.get("display_name", self.label))
        self.name_label.setStyleSheet("color:#c9c9c9; font-size:10px; font-weight:bold;")
        self.name_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.count_label = QLabel("{} copies".format(len(self.info["meshes"])))
        self.count_label.setStyleSheet("color:#bdbdbd; font-size:9px;")
        self.score_label = QLabel("")
        self.score_label.setStyleSheet("color:#9f9f9f; font-size:9px;")
        self.score_label.setMinimumWidth(48)
        header.addWidget(self.group_check)
        header.addWidget(self.badge)
        header.addWidget(self.name_label)
        header.addStretch()
        header.addWidget(self.score_label)
        header.addWidget(self.count_label)

        actions = QHBoxLayout()
        self._actions_layout = actions
        actions.setSpacing(3)
        self.src_btn       = ColorBtn("SRC", "Select source meshes", "#2b2b2b","#bdbdbd", 46,21)
        self.master_btn    = ColorBtn("MST", "Select master",        "#2b2b2b","#bdbdbd", 46,21)
        self.instances_btn = ColorBtn("INS", "Select instances",     "#2b2b2b","#bdbdbd", 46,21)
        self.all_btn       = ColorBtn("ALL", "Select master + instances", "#2b2b2b","#bdbdbd", 46,21)
        self.backups_btn   = ColorBtn("BKP", "Select backups",       "#2b2b2b","#bdbdbd", 46,21)

        self.acc_btn = ColorBtn("OK",  "Accept group", "#30282a","#c7c7c7", 30,21)
        self.rej_btn = ColorBtn("NO",  "Reject group", "#4b0e19","#c9b8bb", 30,21)

        self.src_btn.clicked.connect(lambda: self.select_clicked.emit(self.label))
        self.master_btn.clicked.connect(lambda: self.master_clicked.emit(self.label))
        self.instances_btn.clicked.connect(lambda: self.instances_clicked.emit(self.label))
        self.all_btn.clicked.connect(lambda: self.all_clicked.emit(self.label))
        self.backups_btn.clicked.connect(lambda: self.backups_clicked.emit(self.label))
        self.acc_btn.clicked.connect(lambda: self.accept_clicked.emit(self.label))
        self.rej_btn.clicked.connect(lambda: self.reject_clicked.emit(self.label))

        actions.addWidget(self.src_btn)
        actions.addWidget(self.master_btn)
        actions.addWidget(self.instances_btn)
        actions.addWidget(self.all_btn)
        actions.addWidget(self.backups_btn)
        actions.addStretch()
        actions.addWidget(self.acc_btn)
        actions.addWidget(self.rej_btn)

        self.mark_row = QHBoxLayout()
        self._mark_layout = self.mark_row
        self.mark_row.setSpacing(5)
        self.mark_label = QLabel("SEL:")
        self.mark_label.setToolTip("Tick parts on several cards, then click SELECT MARKED.")
        self.mark_checks = {}
        for part, text, tip in (
                ("source", "SRC", "Mark source/backups from this group for multi-selection"),
                ("master", "MST", "Mark master from this group for multi-selection"),
                ("instances", "INS", "Mark instances from this group for multi-selection"),
                ("backups", "BKP", "Mark backups from this group for multi-selection")):
            cb = QCheckBox(text)
            cb.setToolTip(tip)
            cb.stateChanged.connect(
                lambda _v, p=part, c=cb: self.part_checked_changed.emit(self.label, p, c.isChecked()))
            self.mark_checks[part] = cb
            self.mark_row.addWidget(cb)
        self.mark_row.addStretch()

        self.status_lbl = QLabel("")
        layout.addLayout(header)
        layout.addLayout(actions)
        layout.addLayout(self.mark_row)
        layout.addWidget(self.status_lbl)


    def _s(self, value):
        return max(1, int(round(float(value) * float(self._ui_scale))))

    def set_ui_scale(self, scale):
        self._ui_scale = float(scale or 1.0)
        if hasattr(self, "_card_layout"):
            self._card_layout.setContentsMargins(self._s(8), self._s(7), self._s(8), self._s(7))
            self._card_layout.setSpacing(self._s(5))
        if hasattr(self, "_header_layout"):
            self._header_layout.setSpacing(self._s(4))
        if hasattr(self, "_actions_layout"):
            self._actions_layout.setSpacing(self._s(3))
        self.badge.setMinimumSize(self._s(68), self._s(20))
        self.score_label.setMinimumWidth(self._s(48))
        self.name_label.setStyleSheet("color:#c9c9c9; font-size:{}px; font-weight:bold;".format(self._s(10)))
        self.count_label.setStyleSheet("color:#bdbdbd; font-size:{}px;".format(self._s(9)))
        self.score_label.setStyleSheet("color:#9f9f9f; font-size:{}px;".format(self._s(9)))
        if hasattr(self, "mark_label"):
            self.mark_label.setStyleSheet("color:#8f8f8f; font-size:{}px; font-weight:bold;".format(self._s(8)))
        if hasattr(self, "_mark_layout"):
            self._mark_layout.setSpacing(self._s(5))
        for cb in getattr(self, "mark_checks", {}).values():
            cb.setStyleSheet("color:#9f9f9f; font-size:{}px;".format(self._s(8)))
        for btn in (self.src_btn, self.master_btn, self.instances_btn, self.all_btn, self.backups_btn, self.acc_btn, self.rej_btn):
            btn.set_ui_scale(self._ui_scale)
        self.updateGeometry()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.select_clicked.emit(self.label)
        super(GroupItem, self).mousePressEvent(event)

    def _set_badge(self, text, bg, fg="#c9c9c9"):
        self.badge.setText(text)
        self.badge.setStyleSheet(
            "background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {}, stop:1 {}); color:{}; "
            "font-size:{}px; font-weight:900; border:1px solid {}; border-radius:{}px;"
            .format(QColor(bg).lighter(120).name(), bg, fg, self._s(8), QColor(bg).lighter(145).name(), self._s(6))
        )

    def set_highlighted(self, state):
        self._highlighted = bool(state)
        self.refresh()

    def set_checked(self, state):
        self.group_check.blockSignals(True)
        self.group_check.setChecked(bool(state))
        self.group_check.blockSignals(False)

    def is_checked(self):
        return self.group_check.isChecked()

    def set_part_checked(self, part, state):
        cb = getattr(self, "mark_checks", {}).get(part)
        if not cb:
            return
        cb.blockSignals(True)
        cb.setChecked(bool(state))
        cb.blockSignals(False)

    def set_parts_checked(self, parts):
        parts = set(parts or [])
        for part in getattr(self, "mark_checks", {}):
            self.set_part_checked(part, part in parts)

    def checked_parts(self):
        return set(part for part, cb in getattr(self, "mark_checks", {}).items() if cb.isChecked())

    def refresh(self):
        accepted  = self.info["accepted"]
        processed = self.info.get("processed")
        gtype     = self.info.get("type")
        score     = float(self.info.get("score", 0.) or 0.)

        self.name_label.setText(self.info.get("display_name", self.label))
        self.count_label.setText("{} copies".format(len(self.info.get("meshes", []))))
        self.score_label.setText("{:03d}%".format(int(score*100.)))

        self.master_btn.setEnabled(bool(processed))
        self.instances_btn.setEnabled(bool(processed))
        self.all_btn.setEnabled(bool(processed))
        self.backups_btn.setEnabled(bool(processed))
        if hasattr(self, "mark_checks"):
            if "source" in self.mark_checks:
                self.mark_checks["source"].setEnabled(True)
            for part in ("master", "instances", "backups"):
                if part in self.mark_checks:
                    self.mark_checks[part].setEnabled(bool(processed))

        self.acc_btn.setVisible(not bool(processed))
        self.rej_btn.setVisible(not bool(processed))

        if processed:
            self._set_badge("DONE", "#2b5870")
            self.status_lbl.setText("Processed - SRC falls back to backups")
            bg, border, color = "#1d2830", "#4d90b8", "#b6d5ea"
        elif accepted is False:
            self._set_badge("REJECT", "#6d1728")
            self.status_lbl.setText("Rejected")
            bg, border, color = "#2b181d", "#8b2035", "#ffb6c4"
        elif accepted is True and gtype == MATCH_SAFE:
            self._set_badge("SAFE OK", "#2f6543")
            self.status_lbl.setText("Safe match / accepted")
            bg, border, color = "#1c2922", "#4e9b67", "#b8d8c2"
        elif accepted is True and gtype == MATCH_FUZZY:
            self._set_badge("FUZ OK", "#7b4a1f")
            self.status_lbl.setText("Fuzzy match / accepted")
            bg, border, color = "#30251d", "#a96d30", "#d6c1a5"
        elif gtype == MATCH_SAFE:
            self._set_badge("SAFE", "#2f6543")
            self.status_lbl.setText("Safe match")
            bg, border, color = "#1c2922", "#4e9b67", "#b8d8c2"
        elif gtype == MATCH_FUZZY:
            self._set_badge("FUZZY", "#82451c")
            self.status_lbl.setText("Similar shape - review")
            bg, border, color = "#30211b", "#a45d2d", "#d6b28d"
        else:
            self._set_badge("WAIT", "#3a3a3a")
            self.status_lbl.setText("Waiting")
            bg, border, color = "#242424", "#555555", "#aaaaaa"

        if self._highlighted:
            current_status = self.status_lbl.text()
            if current_status and not current_status.startswith("CURRENT"):
                self.status_lbl.setText("CURRENT - " + current_status)
            bw = 3
            border = "#ff4058"
            bg_a = QColor(bg).lighter(128).name()
            bg_b = QColor(bg).lighter(112).name()
        else:
            bw = 1
            bg_a = QColor(bg).lighter(112).name()
            bg_b = bg
        self.status_lbl.setStyleSheet("color:{}; font-size:{}px;".format(color, self._s(8)))
        self.setStyleSheet(
            "#GroupItemCard {{ background:qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {}, stop:1 {}); "
            "border:{}px solid {}; border-radius:{}px; }}"
            .format(bg_a, bg_b, bw, border, self._s(8))
        )


# ---------------------------------------------------------------------------
# Main UI
# ---------------------------------------------------------------------------
class InstanceCleanerUI(QDialog):
    # Screen-aware UI scale. Full-HD monitors open smaller by default, while
    # larger/high-DPI screens can still use 100%+ from the UI Scale combo.
    BASE_UI_SCALE = 1.0

    def _default_user_ui_percent_for_screen(self):
        # User requested: always open at 100% by default.
        # The window itself is still clamped to the available screen and scrollable,
        # so Full-HD remains usable without shrinking the whole UI.
        return 100

    def __init__(self, parent=maya_main_window()):
        super(InstanceCleanerUI, self).__init__(parent)

        self.cleaner              = InstanceCleaner()
        self.group_items          = {}
        self.visible_group_order  = []
        self.current_group_label  = None
        self._highlighted_label   = None
        self.checked_group_labels = set()
        self.marked_group_parts   = defaultdict(set)
        self._last_selection_key  = ""
        self._is_processing       = False
        self._cancel_requested    = False
        self._compact_state       = None
        self._find_selected_filter = None
        self._default_ui_percent = self._default_user_ui_percent_for_screen()
        self.ui_scale = self.BASE_UI_SCALE * (float(self._default_ui_percent) / 100.0)
        self._user_resized = False
        self._programmatic_resize = False

        self.setWindowTitle("Instance Cleaner")
        self.setMinimumSize(self._s(350), self._s(300))
        self.resize(self._s(400), self._s(430))
        self.setWindowFlags(Qt.Window | Qt.WindowCloseButtonHint | Qt.WindowMinMaxButtonsHint)
        self.setSizeGripEnabled(True)

        self._build_ui()
        self._apply_stylesheet()
        self._apply_scaled_layout_metrics()
        self._start_selection_watcher()
        self._update_window_compactness(0, force=True, allow_resize=True)
        self._update_stat_chips()
        # Run one more autofit after the widget is shown and Qt has computed final size hints.
        try:
            QTimer.singleShot(0, self._autofit_current_layout)
        except Exception:
            pass
        # Width is controlled by compact/expanded mode; do not hard-lock it.
    def _s(self, value):
        return max(1, int(round(float(value) * float(getattr(self, "ui_scale", 1.0)))))

    def set_ui_scale(self, scale):
        user_scale = float(scale or 1.0)
        self.ui_scale = self.BASE_UI_SCALE * user_scale
        self._apply_stylesheet()
        self._apply_scaled_layout_metrics()
        total = len(self.cleaner.validated_groups) if hasattr(self, "cleaner") else 0
        self._update_window_compactness(total, force=True, allow_resize=True)
        try:
            QTimer.singleShot(0, self._autofit_current_layout)
        except Exception:
            pass

    def _apply_scaled_layout_metrics(self):
        for layout in getattr(self, "_scaled_layouts", []):
            spacing = int(layout.property("baseSpacing") or 4)
            margins = layout.property("baseMargins")
            layout.setSpacing(self._s(spacing))
            if margins:
                layout.setContentsMargins(self._s(margins[0]), self._s(margins[1]), self._s(margins[2]), self._s(margins[3]))

        for widget in self.findChildren(QWidget):
            if hasattr(widget, "set_ui_scale") and widget is not self:
                widget.set_ui_scale(self.ui_scale)

        label_width = self._s(74)
        for label in getattr(self, "_row_labels", []):
            label.setMinimumWidth(label_width)
            label.setMaximumWidth(self._s(118))
        for label in getattr(self, "_right_row_labels", []):
            label.setMinimumWidth(self._s(32))
            label.setMaximumWidth(self._s(60))

        for widget in getattr(self, "_field_widgets", []):
            widget.setMinimumHeight(self._s(22))
            widget.setMaximumHeight(self._s(27))
            widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        for widget, base_width in getattr(self, "_fixed_width_widgets", []):
            try:
                widget.setMinimumWidth(self._s(base_width))
                widget.setMaximumWidth(self._s(base_width))
                widget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            except Exception:
                pass
        for label, color, size in getattr(self, "_small_labels", []):
            label.setStyleSheet("color:{}; font-size:{}px;".format(color, self._s(size)))
        if hasattr(self, "progress_bar"):
            self.progress_bar.setMinimumHeight(self._s(12))
            self.progress_bar.setMaximumHeight(self._s(16))
        if hasattr(self, "left_scroll"):
            # Keep the left column usable on Full-HD screens; content scrolls vertically when needed.
            self.left_scroll.setMinimumWidth(self._s(260))
            self.left_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        if hasattr(self, "right_col"):
            self.right_col.setMinimumWidth(self._s(250) if self.right_col.isVisible() else 0)
            self.right_col.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        if hasattr(self, "groups_scroll"):
            self.groups_scroll.setMinimumHeight(self._s(140))
            self.groups_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        if hasattr(self, "groups_layout"):
            self.groups_layout.setContentsMargins(self._s(6), self._s(6), self._s(6), self._s(6))
            self.groups_layout.setSpacing(self._s(6))
        if hasattr(self, "header_status_label"):
            self.header_status_label.setMinimumWidth(self._s(104))
            self.header_status_label.setStyleSheet(
                "background:#202020; color:#bdbdbd; border:1px solid #3a3a3a; "
                "border-radius:{}px; font-size:{}px; font-weight:900; padding:{}px {}px;"
                .format(self._s(10), self._s(9), self._s(4), self._s(8)))
        if hasattr(self, "groups_count_label"):
            self.groups_count_label.setMinimumHeight(self._s(20))
            self.groups_count_label.setMaximumHeight(self._s(26))
        for chip in (getattr(self, "stat_groups_label", None), getattr(self, "stat_safe_label", None), getattr(self, "stat_fuzzy_label", None)):
            if chip:
                chip.setMinimumWidth(self._s(70))
        self.updateGeometry()

    def _track_layout(self, layout, spacing=4, margins=(0,0,0,0)):
        layout.setProperty("baseSpacing", spacing)
        layout.setProperty("baseMargins", margins)
        if not hasattr(self, "_scaled_layouts"):
            self._scaled_layouts = []
        self._scaled_layouts.append(layout)
        layout.setSpacing(self._s(spacing))
        layout.setContentsMargins(self._s(margins[0]), self._s(margins[1]), self._s(margins[2]), self._s(margins[3]))
        return layout

    def _section_panel(self):
        panel = QFrame()
        panel.setObjectName("SectionPanel")
        panel.setAttribute(Qt.WA_StyledBackground, True)
        panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = self._track_layout(QVBoxLayout(panel), spacing=3, margins=(5,5,5,5))
        return panel, layout

    def _apply_stylesheet(self):
        self.setStyleSheet("""
            QDialog {{ background-color:#161616; font-family:Segoe UI, Arial, sans-serif; }}

            QWidget#HeaderPanel {{
                background:#1d1d1d; border:1px solid #303030; border-radius:{panel_radius}px;
            }}

            QLabel#TitleLabel {{ color:#c9c9c9; font-size:{title_font}px; font-weight:800; letter-spacing:.2px; }}
            QLabel#SubtitleLabel {{ color:#8f8f8f; font-size:{small_font}px; }}

            QLabel#StatChip {{ background:#242424; color:#bdbdbd; border:1px solid #3a3a3a;
                               border-radius:{chip_radius}px; font-size:{small_font}px; font-weight:800; padding:{chip_vpad}px {chip_hpad}px; }}
            QLabel#StatChip[role=accent] {{ background:#28191d; color:#c7c7c7; border-color:#7a1e2a; }}
            QLabel#StatChip[role=warn] {{ background:#2d2020; color:#c7b0b0; border-color:#6d2430; }}

            QLabel {{ color:#b8b8b8; font-size:{label_font}px; }}

            QLineEdit {{ background:#222222; color:#c7c7c7; border:1px solid #363636;
                        border-radius:{radius}px; padding:{field_vpad}px {field_hpad}px; font-size:{field_font}px; selection-background-color:#555555; }}
            QLineEdit:focus {{ border-color:#7a1e2a; background:#292222; }}

            QCheckBox {{ color:#b8b8b8; font-size:{field_font}px; spacing:{spacing}px; }}
            QCheckBox::indicator {{ width:{indicator}px; height:{indicator}px; }}

            QWidget#LeftPanel, QWidget#RightPanel {{ background:#1b1b1b; border:1px solid #2f2f2f; border-radius:{panel_radius}px; }}

            QFrame#SectionPanel {{
                background:#1b1b1b; border:1px solid #303030; border-radius:{panel_radius}px;
            }}

            QScrollArea {{ border:none; background:transparent; }}
            QScrollBar:vertical {{ background:#181818; width:{scroll_w}px; border-radius:{scroll_r}px; margin:0; }}
            QScrollBar::handle:vertical {{ background:#404040; border-radius:{scroll_r}px; min-height:{scroll_min}px; }}
            QScrollBar::handle:vertical:hover {{ background:#555555; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}

            QSlider::groove:horizontal {{ height:{slider_h}px; background:#303030; border-radius:{slider_r}px; }}
            QSlider::handle:horizontal {{ background:#a91e2b; border:1px solid #c74755; width:{handle_w}px; margin:-{handle_m}px 0; border-radius:{handle_r}px; }}
            QSlider::handle:horizontal:hover {{ background:#bf2635; border-color:#d65b67; }}
            QSlider::sub-page:horizontal {{ background:#74121e; border-radius:{slider_r}px; }}

            QSpinBox, QDoubleSpinBox {{ background:#222222; color:#c7c7c7; border:1px solid #363636;
                                       border-radius:{radius}px; padding:{spin_pad}px; font-size:{field_font}px; }}
            QSpinBox:focus, QDoubleSpinBox:focus {{ border-color:#7a1e2a; background:#292222; }}

            QComboBox {{ background:#222222; color:#c7c7c7; border:1px solid #363636;
                        border-radius:{radius}px; padding:{field_vpad}px {field_hpad}px; font-size:{field_font}px; }}
            QComboBox:hover {{ border-color:#7a1e2a; }}
            QComboBox QAbstractItemView {{ background:#222222; color:#c7c7c7; border:1px solid #363636; selection-background-color:#5a1621; }}

            QProgressBar {{ background:#181818; border:1px solid #303030; border-radius:{radius}px;
                           text-align:center; color:#bdbdbd; font-size:{small_font}px; font-weight:700; }}
            QProgressBar::chunk {{ background:#7a1e2a; border-radius:{chunk_r}px; }}
        """.format(
            title_font=self._s(14), label_font=self._s(9), field_font=self._s(9), small_font=self._s(8),
            radius=self._s(4), panel_radius=self._s(6), field_vpad=self._s(3), field_hpad=self._s(7),
            spin_pad=self._s(2), spacing=self._s(4), indicator=self._s(13), scroll_w=self._s(9),
            scroll_r=self._s(4), scroll_min=self._s(32), slider_h=self._s(4),
            slider_r=self._s(2), handle_w=self._s(11), handle_m=self._s(4),
            handle_r=self._s(6), chunk_r=self._s(3),
            chip_radius=self._s(8), chip_vpad=self._s(3), chip_hpad=self._s(7)))


    def _build_ui(self):
        self._scaled_layouts = []
        self._row_labels = []
        self._right_row_labels = []
        self._field_widgets = []
        self._small_labels = []
        self._fixed_width_widgets = []

        root = self._track_layout(QVBoxLayout(self), spacing=5, margins=(6,6,6,6))


        body = self._track_layout(QHBoxLayout(), spacing=8, margins=(0,0,0,0))
        root.addLayout(body, 1)

        left_content = QWidget()
        left_content.setObjectName("LeftPanel")
        left_content.setAttribute(Qt.WA_StyledBackground, True)
        left = self._track_layout(QVBoxLayout(left_content), spacing=3, margins=(5,5,5,5))
        left_content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self.left_scroll = QScrollArea()
        self.left_scroll.setWidgetResizable(True)
        self.left_scroll.setFrameShape(QFrame.NoFrame)
        self.left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.left_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.left_scroll.setWidget(left_content)
        self.left_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        right_col = QWidget()
        right_col.setObjectName("RightPanel")
        right_col.setAttribute(Qt.WA_StyledBackground, True)
        # Width is controlled by _update_window_compactness().

        right_col.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right = self._track_layout(QVBoxLayout(right_col), spacing=3, margins=(5,5,5,5))

        body.addWidget(self.left_scroll, 1)
        body.addWidget(right_col, 1)
        self.left_col = left_content
        self.right_col = right_col

        # --- SCAN ---
        scan_panel, scan = self._section_panel()
        scan.addWidget(SectionLabel("SCAN"))

        self.scan_mode_combo = QComboBox()
        self.scan_mode_combo.addItems(["Scene", "Selected Mesh(es)"])
        self.scan_mode_combo.setCurrentIndex(0)
        self.scan_mode_combo.setToolTip(
            "Scene scans everything. Selected Mesh(es) scans only the selection. "
            "Use FIND SELECTED IN GROUPS to locate the current selection in the scanned groups.")

        self.ui_scale_combo = QComboBox()
        self.ui_scale_combo.addItems(["100%", "90%", "85%", "80%", "75%", "70%", "60%", "110%", "125%", "150%"] )
        self.ui_scale_combo.setCurrentText("{}%".format(int(getattr(self, "_default_ui_percent", 100))))
        self.ui_scale_combo.setToolTip("UI Scale - default is 100%. Change only when you need a smaller/larger tool window.")
        self.ui_scale_combo.currentIndexChanged.connect(
            lambda _idx: self.set_ui_scale(float(self.ui_scale_combo.currentText().rstrip('%')) / 100.0))

        source_ui_row = self._track_layout(QHBoxLayout(), spacing=5, margins=(0,0,0,0))
        source_lbl = QLabel("Source")
        source_lbl.setMinimumWidth(self._s(74))
        source_lbl.setMaximumWidth(self._s(118))
        ui_lbl = QLabel("UI Scale")
        ui_lbl.setMinimumWidth(self._s(48))
        ui_lbl.setMaximumWidth(self._s(62))
        ui_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._row_labels.append(source_lbl)
        self._field_widgets.append(self.scan_mode_combo)
        self._field_widgets.append(self.ui_scale_combo)
        self._fixed_width_widgets.append((self.ui_scale_combo, 72))
        source_ui_row.addWidget(source_lbl)
        source_ui_row.addWidget(self.scan_mode_combo, 1)
        source_ui_row.addSpacing(self._s(6))
        source_ui_row.addWidget(ui_lbl, 0)
        source_ui_row.addWidget(self.ui_scale_combo, 0)
        scan.addLayout(source_ui_row)

        self.strict_tol_slider = ParamSlider("Strict tol", 0.0001, 0.02, 0.001, 4, 90)
        scan.addWidget(self.strict_tol_slider)

        self.detect_method_combo = QComboBox()
        self.detect_method_combo.addItems([
            "Robust Similar (default)",
            "Exact (UVOptimizer)",
            "Geometry (UVOptimizer)",
            "Topology (UVOptimizer)",
            "Signature + Fuzzy (current)",
        ])
        self.detect_method_combo.setCurrentIndex(0)
        self.detect_method_combo.setToolTip(
            "Robust Similar is the default integrated from the new selection algorithm: topology/valence filters, "
            "world-size filtering when Ignore scale is off, and normalized radial/edge/face/covariance shape distance. "
            "Exact/Geometry/Topology keep the older UVOptimizer-style methods.")
        method_row = self._track_layout(QHBoxLayout(), spacing=5, margins=(0,0,0,0))
        method_lbl = QLabel("Method")
        method_lbl.setMinimumWidth(self._s(74))
        method_lbl.setMaximumWidth(self._s(118))
        self._row_labels.append(method_lbl)
        self._field_widgets.append(self.detect_method_combo)
        self.fuzzy_enabled_cb = QCheckBox("Fuzzy")
        self.fuzzy_enabled_cb.setChecked(True)
        self.fuzzy_enabled_cb.setToolTip("Enable fuzzy detection / secondary similar-shape grouping pass.")
        method_row.addWidget(method_lbl)
        method_row.addWidget(self.detect_method_combo, 1)
        method_row.addWidget(self.fuzzy_enabled_cb, 0)
        scan.addLayout(method_row)

        self.ignore_scale_cb = QCheckBox("Ignore scale")
        self.ignore_scale_cb.setChecked(False)
        self.ignore_scale_cb.setToolTip("When checked, the Scale range filter is ignored. When off, Scale range drives both min and max size ratios.")

        self.scale_ratio_slider = ParamSlider("Scale range", 1.00, 2.00, ROBUST_REAL_SIZE_RATIO_MAX, 2, 74)
        self.scale_ratio_slider.setToolTip(
            "Real-size filter used when Ignore scale is OFF. One slider drives both limits: "
            "max = this value, min = 1 / this value. Default 1.20 means approx 0.83x to 1.20x.")
        self.scale_ratio_info_label = QLabel("")
        self.scale_ratio_info_label.setToolTip("Effective accepted real-size range: min to max.")
        self._small_labels.append((self.scale_ratio_info_label, "#8b8285", 8))
        scale_ratio_row = self._track_layout(QHBoxLayout(), spacing=5, margins=(0,0,0,0))
        scale_ratio_row.addWidget(self.scale_ratio_slider, 1)
        scale_ratio_row.addWidget(self.scale_ratio_info_label, 0)
        scale_ratio_row.addWidget(self.ignore_scale_cb, 0)
        scan.addLayout(scale_ratio_row)
        self.scale_ratio_slider.valueChanged.connect(lambda _v: self._update_scale_ratio_label())
        self.ignore_scale_cb.toggled.connect(lambda checked: self._set_scale_ratio_enabled(not checked))
        self._set_scale_ratio_enabled(not self.ignore_scale_cb.isChecked())
        self._update_scale_ratio_label()

        self.compare_tolerance_spin = QDoubleSpinBox()
        self.compare_tolerance_spin.setRange(0.0001, 1.0)
        self.compare_tolerance_spin.setDecimals(4)
        self.compare_tolerance_spin.setSingleStep(0.001)
        self.compare_tolerance_spin.setValue(ROBUST_TOLERANCE_SHAPE_DEFAULT)
        self.compare_tolerance_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)

        self.fuzzy_vertex_spin = QSpinBox()
        self.fuzzy_vertex_spin.setRange(0, 50)
        self.fuzzy_vertex_spin.setValue(0)
        self.fuzzy_vertex_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)

        tol_vert_row = self._track_layout(QHBoxLayout(), spacing=4, margins=(0,0,0,0))
        tol_lbl = QLabel("Tolerance")
        tol_lbl.setMinimumWidth(self._s(74))
        tol_lbl.setMaximumWidth(self._s(118))
        vert_lbl = QLabel("Vert +/-")
        vert_lbl.setMinimumWidth(self._s(48))
        vert_lbl.setMaximumWidth(self._s(70))
        self._row_labels.append(tol_lbl)
        self._right_row_labels.append(vert_lbl)
        self._field_widgets.append(self.compare_tolerance_spin)
        self._field_widgets.append(self.fuzzy_vertex_spin)
        tol_vert_row.addWidget(tol_lbl)
        tol_vert_row.addWidget(self.compare_tolerance_spin, 1)
        tol_vert_row.addWidget(vert_lbl)
        tol_vert_row.addWidget(self.fuzzy_vertex_spin, 1)
        scan.addLayout(tol_vert_row)

        self.fuzzy_size_slider  = ParamSlider("Shape tol",  0.005, 0.30, ROBUST_TOLERANCE_SHAPE_DEFAULT, 3, 90)
        self.fuzzy_score_slider = ParamSlider("Min score",  0.50, 0.99, 0.94, 2, 90)
        scan.addWidget(self.fuzzy_size_slider)
        scan.addWidget(self.fuzzy_score_slider)

        self.min_copies_spin = QSpinBox()
        self.min_copies_spin.setRange(2, 999)
        self.min_copies_spin.setValue(2)
        self.min_copies_spin.setKeyboardTracking(False)
        self.min_copies_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.min_copies_spin.setToolTip("Minimum number of copies needed to create a group. You can type a custom value directly.")

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setMinimumHeight(self._s(12))
        self.progress_bar.setMaximumHeight(self._s(16))

        min_copies_row = self._track_layout(QHBoxLayout(), spacing=4, margins=(0,0,0,0))
        min_copies_lbl = QLabel("Min copies")
        min_copies_lbl.setMinimumWidth(self._s(74))
        min_copies_lbl.setMaximumWidth(self._s(118))
        self._row_labels.append(min_copies_lbl)
        self._fixed_width_widgets.append((self.min_copies_spin, 34))

        self.min_copies_minus_btn = ColorBtn("-", "Decrease Min copies", "#2b2b2b", "#bdbdbd", w=24, h=20)
        self.min_copies_plus_btn  = ColorBtn("+", "Increase Min copies", "#2b2b2b", "#bdbdbd", w=24, h=20)
        self._fixed_width_widgets.append((self.min_copies_minus_btn, 24))
        self._fixed_width_widgets.append((self.min_copies_plus_btn, 24))
        self.min_copies_minus_btn.clicked.connect(lambda: self.min_copies_spin.setValue(max(self.min_copies_spin.minimum(), self.min_copies_spin.value() - 1)))
        self.min_copies_plus_btn.clicked.connect(lambda: self.min_copies_spin.setValue(min(self.min_copies_spin.maximum(), self.min_copies_spin.value() + 1)))

        min_copies_row.addWidget(min_copies_lbl)
        min_copies_row.addWidget(self.min_copies_minus_btn, 0)
        min_copies_row.addWidget(self.min_copies_spin, 0)
        min_copies_row.addWidget(self.min_copies_plus_btn, 0)
        min_copies_row.addWidget(self.progress_bar, 1)
        scan.addLayout(min_copies_row)

        self.status_label = QLabel("Ready")
        self._small_labels.append((self.status_label, "#8b8285", 9))
        self.status_label.setStyleSheet("color:#8b8285; font-size:{}px;".format(self._s(9)))
        scan.addWidget(self.status_label)

        scan_btn = ColorBtn("REFRESH SCENE", "Force Source to Scene and run a full scan", "#2b2b2b","#bdbdbd", h=24)
        scan_current_btn = ColorBtn("REFRESH MODE", "Scan using the current Source combo (Scene or Selected Mesh(es))", "#2b2b2b","#bdbdbd", h=24)
        find_btn = ColorBtn("FIND SELECTED", "Fast-find meshes matching the current selection", "#2b2b2b","#bdbdbd", h=24)
        show_all_btn = ColorBtn("SHOW ALL", "Clear selection-find filter and show all scanned groups", "#2b2b2b","#bdbdbd", h=24)
        self._connect_button(scan_btn, "Refresh scene", self.do_refresh_scene)
        self._connect_button(scan_current_btn, "Refresh current mode", self.do_refresh_current)
        self._connect_button(find_btn, "Find selected", self.do_find_selected)
        self._connect_button(show_all_btn, "Show all groups", self.do_show_all_groups)
        scan.addLayout(self._button_grid([scan_btn, scan_current_btn, find_btn, show_all_btn], columns=4))
        left.addWidget(scan_panel)

        # --- GROUPS ---
        groups_panel, groups = self._section_panel()
        groups.addWidget(SectionLabel("GROUPS"))

        # Compact action grid. Use readable icon + text labels,
        # not ambiguous icon-only buttons.
        group_btn_h = 20
        self.rebuild_scene_groups_groups_btn = ColorBtn(
            "REBUILD",
            "Rebuild the Group List from the existing _INSTANCE_CLEANER hierarchy without rescanning geometry.",
            "#2b2b2b", "#bdbdbd", h=group_btn_h)
        acc_safe_btn = ColorBtn("SAFE", "OK SAFE - Accept only safe groups", "#2b2b2b", "#bdbdbd", h=group_btn_h)
        acc_all_btn  = ColorBtn("ACCEPT",  "Accept safe and fuzzy groups.", "#30282a", "#c7c7c7", h=group_btn_h)
        rej_all_btn  = ColorBtn("REJECT",  "Reject all current groups.", "#4b0e19", "#c9b8bb", h=group_btn_h)

        merge_btn = ColorBtn("GROUP", "Merge checked groups, or groups resolved from the current viewport selection.", "#2b2b2b", "#bdbdbd", h=group_btn_h)
        split_btn = ColorBtn("SPLIT", "Split the selected mesh(es) out of the current group.", "#2b2b2b", "#bdbdbd", h=group_btn_h)
        add_sel_btn = ColorBtn("+ ADD", "Add selected viewport mesh(es) to the current group.", "#2b2b2b", "#bdbdbd", h=group_btn_h)
        rem_sel_btn = ColorBtn("- REM", "Remove selected viewport mesh(es) from the current group.", "#2b2b2b", "#bdbdbd", h=group_btn_h)

        sel_mst_btn = ColorBtn("SEL MST", "SELECT MASTERS - Select all masters", "#2b2b2b", "#bdbdbd", h=group_btn_h)
        org_mst_btn = ColorBtn("ORG", "ORGANIZE - Organize masters", "#2b2b2b", "#bdbdbd", h=group_btn_h)
        set_mst_btn = ColorBtn("SET MST", "SET MASTER - Use selected mesh as the master/reference for its group", "#2b2b2b", "#bdbdbd", h=group_btn_h)
        merge_masters_btn = ColorBtn(
            "MASTER",
            "Processed groups only. Select old master(s)/instances first, target master last.",
            "#2b2b2b", "#bdbdbd", h=group_btn_h)

        rename_nodes_btn = ColorBtn(
            "RENAME",
            "Rename processed masters, instances, backups and converted geo.",
            "#2b2b2b", "#bdbdbd", h=group_btn_h)
        validate_repair_btn = ColorBtn(
            "CHECK",
            "Validate and repair the IC hierarchy, layers and group tags.",
            "#2b2b2b", "#bdbdbd", h=group_btn_h)

        self._connect_button(self.rebuild_scene_groups_groups_btn, "Rebuild group list from scene", self.do_rebuild_group_list_from_scene)
        self._connect_button(acc_safe_btn, "Accept safe", self.do_accept_safe)
        self._connect_button(acc_all_btn, "Accept all", self.do_accept_all)
        self._connect_button(rej_all_btn, "Reject all", self.do_reject_all)
        self._connect_button(merge_btn, "Merge selected groups", self.do_merge_selected_groups)
        self._connect_button(split_btn, "Split selected out", self.do_split_selected_from_group)
        self._connect_button(add_sel_btn, "Add selected to group", self.do_add_selected_to_group)
        self._connect_button(rem_sel_btn, "Remove selected from group", self.do_remove_selected_from_group)
        self._connect_button(sel_mst_btn, "Select all masters", self.do_select_all_masters)
        self._connect_button(org_mst_btn, "Organize masters", self.do_organize_masters)
        self._connect_button(set_mst_btn, "Set selected as master", self.do_set_selected_as_master)
        self._connect_button(merge_masters_btn, "Merge masters", self.do_merge_masters_to_target)
        self._connect_button(rename_nodes_btn, "Rename IC nodes", self.do_rename_processed_nodes)
        self._connect_button(validate_repair_btn, "Validate repair scene", self.do_validate_repair_scene)

        # Keep the GROUPS area readable: actions are separated by workflow,
        # instead of one big dense wall of buttons.
        def _group_subtitle(text):
            lbl = QLabel(text)
            lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            lbl.setMinimumHeight(self._s(11))
            lbl.setMaximumHeight(self._s(13))
            lbl.setStyleSheet(
                "color:#797979; font-size:{}px; font-weight:800; letter-spacing:.8px; "
                "padding:{}px 0 0 2px; border-top:1px solid #2b2b2b;"
                .format(self._s(7), self._s(2))
            )
            return lbl

        groups.addLayout(self._button_grid([
            self.rebuild_scene_groups_groups_btn, acc_safe_btn, acc_all_btn, rej_all_btn,
        ], columns=4))

        groups.addWidget(_group_subtitle("EDIT GROUP"))
        groups.addLayout(self._button_grid([
            merge_btn, split_btn, add_sel_btn, rem_sel_btn,
        ], columns=4))

        groups.addWidget(_group_subtitle("MASTER"))
        groups.addLayout(self._button_grid([
            sel_mst_btn, org_mst_btn, set_mst_btn, merge_masters_btn,
        ], columns=4))

        groups.addWidget(_group_subtitle("TOOLS"))
        groups.addLayout(self._button_grid([
            rename_nodes_btn, validate_repair_btn,
        ], columns=4))
        left.addWidget(groups_panel)

        # --- PROCESS ---
        process_panel, process = self._section_panel()
        process.addWidget(SectionLabel("PROCESS"))

        process_top_row = self._track_layout(QHBoxLayout(), spacing=6, margins=(0,0,0,0))
        spacing_lbl = QLabel("Spacing")
        spacing_lbl.setToolTip("Distance used when laying out processed masters/instances.")
        self._row_labels.append(spacing_lbl)
        self.master_spacing_spin = QDoubleSpinBox()
        self.master_spacing_spin.setRange(0, 5000)
        self.master_spacing_spin.setValue(20)
        self.master_spacing_spin.setDecimals(0)
        self.master_spacing_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.master_spacing_spin.setToolTip("Distance used when laying out processed masters/instances.")
        self._field_widgets.append(self.master_spacing_spin)
        self._fixed_width_widgets.append((self.master_spacing_spin, 58))
        self.rename_on_process_cb = QCheckBox("Rename after PROCESS")
        self.rename_on_process_cb.setChecked(True)
        self.rename_on_process_cb.setToolTip("After PROCESS, normalize names: MASTER_<group>, <group>_INST_001, <group>_BKP_001, etc.")
        process_top_row.addWidget(spacing_lbl)
        process_top_row.addWidget(self.master_spacing_spin)
        process_top_row.addSpacing(self._s(6))
        process_top_row.addWidget(self.rename_on_process_cb, 1)
        process.addLayout(process_top_row)

        process_checks = self._track_layout(QHBoxLayout(), spacing=6, margins=(0,0,0,0))
        self.assign_shader_on_process_cb = QCheckBox("Color after PROCESS")
        self.assign_shader_on_process_cb.setToolTip("After processing, assign deterministic colored Lambert shaders per group to masters + instances.")
        self.shader_tools_toggle = QCheckBox("Show shader tools")
        self.shader_tools_toggle.setToolTip("Show debug color and shader maintenance tools.")
        process_checks.addWidget(self.assign_shader_on_process_cb)
        process_checks.addWidget(self.shader_tools_toggle)
        process_checks.addStretch(1)
        process.addLayout(process_checks)

        self.shader_tools_widget = QWidget()
        shader_tools_layout = self._track_layout(QVBoxLayout(self.shader_tools_widget), spacing=4, margins=(0,0,0,0))

        shader_opts = self._track_layout(QHBoxLayout(), spacing=4, margins=(0,0,0,0))
        self.assign_shader_backups_cb = QCheckBox("Shade BKP")
        self.assign_shader_backups_cb.setToolTip("Also assign a darker debug shader to backups/originals.")
        self.assign_shaders_now_btn = ColorBtn(
            "ASSIGN COLORS NOW",
            "Assign deterministic colored Lambert shaders to checked/current/visible groups.",
            "#2b2b2b", "#bdbdbd", h=22)
        self.master_shader_to_instances_btn = ColorBtn(
            "MASTER MAT > INSTANCES",
            "Select IC master(s) or instance(s), then apply each group master material to all instances.",
            "#2b2b2b", "#bdbdbd", h=22)
        self._connect_button(self.assign_shaders_now_btn, "Assign color shaders", self.do_assign_color_shaders)
        self._connect_button(self.master_shader_to_instances_btn, "Master shaders to instances", self.do_assign_master_shaders_to_instances)
        shader_opts.addWidget(self.assign_shader_backups_cb)
        shader_opts.addWidget(self.assign_shaders_now_btn, 1)
        shader_opts.addWidget(self.master_shader_to_instances_btn, 1)
        shader_tools_layout.addLayout(shader_opts)

        shader_restore_opts = self._track_layout(QHBoxLayout(), spacing=4, margins=(0,0,0,0))
        save_shaders_btn = ColorBtn("SAVE", "Save current shaders for checked/current/visible groups before debug coloring.", "#2b2b2b", "#bdbdbd", h=22)
        restore_shaders_btn = ColorBtn("RESTORE", "Restore shaders saved before IC debug color assignment.", "#2b2b2b", "#bdbdbd", h=22)
        clean_shaders_btn = ColorBtn("CLEAN IC", "Delete unused IC_COLOR debug shader nodes. Restore first if they are still assigned.", "#2b2b2b", "#bdbdbd", h=22)
        self._connect_button(save_shaders_btn, "Save current shaders", self.do_save_current_shaders)
        self._connect_button(restore_shaders_btn, "Restore saved shaders", self.do_restore_saved_shaders)
        self._connect_button(clean_shaders_btn, "Clean debug shaders", self.do_clean_debug_shaders)
        shader_restore_opts.addWidget(save_shaders_btn)
        shader_restore_opts.addWidget(restore_shaders_btn)
        shader_restore_opts.addWidget(clean_shaders_btn)
        shader_tools_layout.addLayout(shader_restore_opts)
        self.shader_tools_widget.setVisible(False)
        self.shader_tools_toggle.toggled.connect(self.shader_tools_widget.setVisible)
        self.shader_tools_toggle.toggled.connect(lambda _checked: QTimer.singleShot(0, self._autofit_current_layout))
        process.addWidget(self.shader_tools_widget)

        self.process_btn = ColorBtn(
            "PROCESS",
            "Process all accepted groups. If no group is accepted, processes the current editable group.",
            "#1f4a32", "#c7d6cc", h=24)
        self.convert_btn = ColorBtn(
            "TO GEO",
            "Convert instances to geometry. Left click: all instances. Right click: selected instances only.",
            "#2b2b2b", "#bdbdbd", h=24)
        self._connect_button(self.process_btn, "Process", self.do_process)
        self._connect_button(self.convert_btn, "Convert instances to geo", self.do_convert_instances)
        self.convert_btn.setContextMenuPolicy(Qt.CustomContextMenu)
        self.convert_btn.customContextMenuRequested.connect(
            lambda pos: self._show_convert_context_menu(self.convert_btn, pos))
        proc_row = self._track_layout(QHBoxLayout(), spacing=4, margins=(0,0,0,0))
        self.cancel_btn       = ColorBtn("CANCEL", "Restore before latest batch / cancel the latest process batch", "#581522", "#c9b8bb", h=24)
        self.stop_process_btn = ColorBtn("STOP",   "Stop current operation safely", "#4b0e19", "#c9b8bb", h=24)
        self.stop_process_btn.setEnabled(False)
        self._connect_button(self.cancel_btn, "Cancel process", self.do_cancel_process)
        self._connect_button(self.stop_process_btn, "Stop process", self.do_stop_process)
        proc_row.addWidget(self.process_btn, 2)
        proc_row.addWidget(self.convert_btn, 1)
        proc_row.addWidget(self.cancel_btn, 1)
        proc_row.addWidget(self.stop_process_btn, 1)
        process.addLayout(proc_row)
        left.addWidget(process_panel)
        # Keep compact mode tightly fitted to the content. Do not add a bottom stretch here,
        # otherwise the left panel expands and creates a large empty area under PROCESS.
        # --- RIGHT: group list ---
        right.addWidget(SectionLabel("GROUP LIST"))


        self.groups_count_label = QLabel()
        self.groups_count_label.setTextFormat(Qt.RichText)
        self._small_labels.append((self.groups_count_label, "#a9a0a3", 9))
        self.groups_count_label.setStyleSheet("color:#a9a0a3; font-size:{}px;".format(self._s(9)))
        self.groups_count_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.groups_count_label.setFixedHeight(self._s(24))
        self._set_groups_count_summary(0, 0, {})
        right.addWidget(self.groups_count_label)

        rev_row = self._track_layout(QHBoxLayout(), spacing=4, margins=(0,0,0,0))
        self.prev_btn       = ColorBtn("PREV", "Previous group", "#26292d", "#b8bec6", 64, 22)
        self.review_src_btn = ColorBtn("ISO + FRAME", "Isolate + frame current group", "#26292d", "#b8bec6", h=22)
        self.next_btn       = ColorBtn("NEXT", "Next group", "#26292d", "#b8bec6", 64, 22)
        self.exit_iso_btn   = ColorBtn("EXIT ISO", "Exit isolate all panels", "#303030", "#bdbdbd", 76, 22)
        self._connect_button(self.prev_btn, "Previous group", lambda: self._navigate_review(-1))
        self._connect_button(self.next_btn, "Next group", lambda: self._navigate_review(1))
        self._connect_button(self.review_src_btn, "Isolate current source", self.do_isolate_current_source)
        self._connect_button(self.exit_iso_btn, "Exit isolate", self.do_exit_isolate)
        rev_row.addWidget(self.prev_btn)
        rev_row.addWidget(self.review_src_btn, 1)
        rev_row.addWidget(self.next_btn)
        rev_row.addWidget(self.exit_iso_btn)
        right.addLayout(rev_row)

        tools_row = self._track_layout(QHBoxLayout(), spacing=4, margins=(0,0,0,0))
        self.show_vp_selection_btn = ColorBtn(
            "FIND VP SEL",
            "Find the selected source/master/instance/backup in the scanned groups, scroll to its card and highlight it. Shortcut: F.",
            "#2b2b2b", "#bdbdbd", h=22)
        self.select_vp_group_btn = ColorBtn(
            "SEL VP GROUP",
            "From the current viewport selection, select all masters and instances from every matching group. Backups are ignored.",
            "#2b2b2b", "#bdbdbd", h=22)
        self.select_marked_parts_btn = ColorBtn(
            "SELECT MARKED",
            "Select all SRC/MST/INS/BKP parts ticked on multiple group cards.",
            "#2b2b2b", "#bdbdbd", h=22)
        self.clear_marked_parts_btn = ColorBtn(
            "CLEAR",
            "Clear all per-card SRC/MST/INS/BKP selection marks.",
            "#2b2b2b", "#bdbdbd", h=22)
        self._connect_button(self.show_vp_selection_btn, "Show viewport selection in group list", self.do_frame_selected_group)
        self._connect_button(self.select_vp_group_btn, "Select viewport groups", self.do_select_viewport_group_master_instances)
        self._connect_button(self.select_marked_parts_btn, "Select marked group parts", self.do_select_marked_group_parts)
        self._connect_button(self.clear_marked_parts_btn, "Clear marked group parts", self.do_clear_marked_group_parts)
        tools_row.addWidget(self.show_vp_selection_btn, 2)
        tools_row.addWidget(self.select_vp_group_btn, 2)
        tools_row.addWidget(self.select_marked_parts_btn, 2)
        tools_row.addWidget(self.clear_marked_parts_btn, 1)
        right.addLayout(tools_row)

        rev_row2 = self._track_layout(QHBoxLayout(), spacing=4, margins=(0,0,0,0))
        self.accept_next_btn = ColorBtn("ACCEPT + NEXT", "Accept then next", "#1f4a32","#c7d6cc", h=22)
        self.reject_next_btn = ColorBtn("REJECT + NEXT", "Reject then next", "#4b0e19","#c9b8bb", h=22)
        self._connect_button(self.accept_next_btn, "Accept and next", self.do_accept_current_and_next)
        self._connect_button(self.reject_next_btn, "Reject and next", self.do_reject_current_and_next)
        rev_row2.addWidget(self.accept_next_btn)
        rev_row2.addWidget(self.reject_next_btn)
        right.addLayout(rev_row2)

        filter_sort_row = self._track_layout(QHBoxLayout(), spacing=4, margins=(0,0,0,0))
        fl = QLabel("Filter"); self._right_row_labels.append(fl); fl.setMinimumWidth(self._s(34))
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All","Safe","Fuzzy","Accepted","Rejected","Processed"])
        self.filter_combo.currentIndexChanged.connect(lambda _idx: self._run_ui_action("Filter groups", self.refresh_group_list))
        sl = QLabel("Sort"); self._right_row_labels.append(sl); sl.setMinimumWidth(self._s(28))
        self.sort_combo = QComboBox()
        self.sort_combo.addItems([
            "Copies high","Copies low","Score high","Score low",
            "Name A-Z","Name Z-A","Type","Accepted first","Fuzzy first",
        ])
        self.sort_combo.currentIndexChanged.connect(lambda _idx: self._run_ui_action("Sort groups", self.refresh_group_list))
        filter_sort_row.addWidget(fl)
        filter_sort_row.addWidget(self.filter_combo, 1)
        filter_sort_row.addSpacing(self._s(2))
        filter_sort_row.addWidget(sl)
        filter_sort_row.addWidget(self.sort_combo, 1)
        right.addLayout(filter_sort_row)

        search_row = self._track_layout(QHBoxLayout(), spacing=4, margins=(0,0,0,0))
        sel = QLabel("Search"); self._right_row_labels.append(sel); sel.setMinimumWidth(self._s(46))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("name...")
        self.search_edit.textChanged.connect(lambda _text: self._run_ui_action("Search groups", self.refresh_group_list))
        search_row.addWidget(sel); search_row.addWidget(self.search_edit)
        right.addLayout(search_row)

        self.groups_scroll = QScrollArea()
        self.groups_scroll.setWidgetResizable(True)
        self.groups_scroll.setFrameShape(QFrame.NoFrame)
        self.groups_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.groups_scroll.setStyleSheet("""
            QScrollArea { background-color:#151515; border:1px solid #2f2f2f; border-radius:8px; }
            QScrollArea > QWidget > QWidget { background-color:#151515; }
        """)
        self.groups_scroll.viewport().setStyleSheet("background-color:#151515;")

        self.groups_container = QWidget()
        self.groups_container.setStyleSheet("background-color:#151515;")
        self.groups_layout = QVBoxLayout(self.groups_container)
        self.groups_layout.setContentsMargins(self._s(6), self._s(6), self._s(6), self._s(6))
        self.groups_layout.setSpacing(self._s(6))

        self.groups_empty = QLabel("No groups found.\nRun REFRESH SCENE or lower Min copies.")
        self.groups_empty.setAlignment(Qt.AlignCenter)
        self._small_labels.append((self.groups_empty, "#606060", 10))
        self.groups_empty.setStyleSheet("color:#606060; font-size:{}px;".format(self._s(10)))
        self.groups_layout.addWidget(self.groups_empty)
        self.groups_layout.addStretch()

        self.groups_scroll.setWidget(self.groups_container)
        right.addWidget(self.groups_scroll, 1)

    def _make_stat_chip(self, text, role=""):
        chip = QLabel(text)
        chip.setObjectName("StatChip")
        chip.setProperty("role", role)
        chip.setAlignment(Qt.AlignCenter)
        chip.setMinimumWidth(self._s(70))
        return chip

    def _set_groups_count_summary(self, visible_count, total_count, report=None):
        report = dict(report or {})
        safe = int(report.get("safe_groups", 0) or 0)
        fuzzy = int(report.get("fuzzy_groups", 0) or 0)
        accepted = int(report.get("accepted_groups", 0) or 0)
        done = int(report.get("processed_groups", 0) or 0)
        unique = int(report.get("unique_meshes", 0) or 0)
        html = (
            '<span style="color:#a6a6a6;">Vis</span> <b>{visible}</b>/<b>{total}</b>'
            ' &nbsp; <span style="color:#78be8c;">Safe</span> <b>{safe}</b>'
            ' &nbsp; <span style="color:#d29a63;">Fuzzy</span> <b>{fuzzy}</b>'
            ' &nbsp; <span style="color:#7ebf90;">Accept</span> <b>{accepted}</b>'
            ' &nbsp; <span style="color:#79abd3;">Done</span> <b>{done}</b>'
            ' &nbsp; <span style="color:#c4c4c4;">Unique</span> <b>{unique}</b>'
        ).format(visible=int(visible_count or 0), total=int(total_count or 0), safe=safe, fuzzy=fuzzy, accepted=accepted, done=done, unique=unique)
        if hasattr(self, "groups_count_label") and self.groups_count_label:
            self.groups_count_label.setText(html)
            self.groups_count_label.setToolTip(
                "Visible {}/{} | Safe {} | Fuzzy {} | Accepted {} | Done {} | Unique {}".format(
                    int(visible_count or 0), int(total_count or 0), safe, fuzzy, accepted, done, unique))

    def _update_stat_chips(self):
        if not hasattr(self, "stat_groups_label"):
            return
        report = self.cleaner.get_report()
        self.stat_groups_label.setText("Groups {}".format(len(self.cleaner.validated_groups)))
        self.stat_safe_label.setText("Safe {}".format(report.get("safe_groups", 0)))
        self.stat_fuzzy_label.setText("Fuzzy {}".format(report.get("fuzzy_groups", 0)))
        for chip in (self.stat_groups_label, self.stat_safe_label, self.stat_fuzzy_label):
            chip.style().unpolish(chip)
            chip.style().polish(chip)

    def _connect_button(self, button, action_name, callback):
        button.clicked.connect(lambda _checked=False: self._run_ui_action(action_name, callback))

    def _show_convert_context_menu(self, button, pos):
        menu = QMenu(self)
        selected_action = menu.addAction("Convert selected instances to geo")
        all_action = menu.addAction("Convert all instances to geo")
        global_pos = button.mapToGlobal(pos)
        if hasattr(menu, "exec_"):
            chosen = menu.exec_(global_pos)
        else:
            chosen = menu.exec(global_pos)
        if chosen == selected_action:
            self._run_ui_action("Convert selected instances to geo", self.do_convert_selected_instances)
        elif chosen == all_action:
            self._run_ui_action("Convert instances to geo", self.do_convert_instances)

    def _set_header_state(self, text="READY", tone="ready"):
        if not hasattr(self, "header_status_label"):
            return
        palettes = {
            "ready": ("#202020", "#bdbdbd", "#3a3a3a"),
            "busy":  ("#2a1c1f", "#c9c9c9", "#7a1e2a"),
            "warn":  ("#302020", "#c7b7b9", "#7a1e2a"),
            "error": ("#4b0e19", "#c9b8bb", "#a3293d"),
        }
        bg, fg, border = palettes.get(tone, palettes["ready"])
        self.header_status_label.setText(str(text or "READY").upper())
        self.header_status_label.setStyleSheet(
            "background:{}; color:{}; border:1px solid {}; border-radius:{}px; "
            "font-size:{}px; font-weight:800; padding:{}px {}px;"
            .format(bg, fg, border, self._s(10), self._s(9), self._s(4), self._s(8)))

    def _run_ui_action(self, action_name, callback, *args, **kwargs):
        if self._is_processing and action_name != "Stop process":
            self.status_label.setText("{} ignored: processing is running".format(action_name))
            self._set_header_state("BUSY", "warn")
            return None
        try:
            self.status_label.setText("{}...".format(action_name))
            self._set_header_state("WORKING", "busy")
            QApplication.processEvents()
            result = callback(*args, **kwargs)
            if not self._is_processing:
                self._set_header_state("READY", "ready")
            return result
        except Exception as e:
            message = "{} failed: {}".format(action_name, e)
            self.status_label.setText(message)
            self._set_header_state("ERROR", "error")
            self.progress_bar.setValue(0)
            try:
                cmds.warning("[IC] {}\n{}".format(message, traceback.format_exc()))
            except Exception:
                print("[IC] {}\n{}".format(message, traceback.format_exc()))
            return None

    def _button_grid(self, buttons, columns=3):
        grid = self._track_layout(QGridLayout(), spacing=3, margins=(0,0,0,0))
        columns = max(1, int(columns or 1))
        for col in range(columns):
            grid.setColumnStretch(col, 1)
        for index, button in enumerate(buttons):
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            grid.addWidget(button, index // columns, index % columns)
        return grid

    def _row(self, label_text, widget, label_width=74):
        row = self._track_layout(QHBoxLayout(), spacing=4, margins=(0,0,0,0))
        lbl = QLabel(label_text)
        lbl.setMinimumWidth(self._s(label_width))
        lbl.setMaximumWidth(self._s(118))
        lbl.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._row_labels.append(lbl)
        self._field_widgets.append(widget)
        row.addWidget(lbl)
        row.addWidget(widget, 1)
        return row

    # -- Selection watcher --

    def _start_selection_watcher(self):
        self.selection_timer = QTimer(self)
        self.selection_timer.setInterval(400)
        self.selection_timer.timeout.connect(self._on_selection_timer)
        self.selection_timer.start()

    def _stop_selection_watcher(self):
        try:
            if hasattr(self, "selection_timer") and self.selection_timer:
                self.selection_timer.stop()
        except Exception:
            pass

    def _on_selection_timer(self):
        if self._is_processing:
            return
        try:
            sel = cmds.ls(sl=True, long=True) or []
        except Exception:
            return
        key = "|".join(sel)
        if key == self._last_selection_key:
            return
        self._last_selection_key = key
        label = self._find_group_from_selection()
        if label:
            self._highlight_group_item(label, frame=False, select=False)

    def _find_group_from_selection(self):
        selected = _get_selected_transforms()
        if not selected:
            return None
        labels = self.cleaner.find_labels_for_nodes(selected, allow_compute=False)
        return labels[0] if labels else None

    def _on_group_checked_changed(self, label, checked):
        if checked:
            self.checked_group_labels.add(label)
        else:
            self.checked_group_labels.discard(label)
        self.status_label.setText("{} checked group(s)".format(len(self.checked_group_labels)))

    def _on_group_part_checked_changed(self, label, part, checked):
        if checked:
            self.marked_group_parts[label].add(part)
        else:
            if label in self.marked_group_parts:
                self.marked_group_parts[label].discard(part)
                if not self.marked_group_parts[label]:
                    self.marked_group_parts.pop(label, None)
        marked = sum(len(v) for v in self.marked_group_parts.values())
        self.status_label.setText("{} marked part(s) for multi-select".format(marked))

    def do_clear_marked_group_parts(self):
        self.marked_group_parts.clear()
        for item in self.group_items.values():
            item.set_parts_checked(set())
        self.status_label.setText("Cleared marked group parts")

    def do_select_marked_group_parts(self):
        if not self.marked_group_parts:
            self.status_label.setText("No marked parts. Tick SRC/MST/INS/BKP on group cards first.")
            return

        ordered_labels = list(self.visible_group_order)
        for label in list(self.marked_group_parts.keys()):
            if label not in ordered_labels:
                ordered_labels.append(label)

        nodes = []
        reveal_nodes = []
        detail_count = 0
        stale_labels = []

        for label in ordered_labels:
            parts = set(self.marked_group_parts.get(label, set()))
            if not parts:
                continue

            resolved_label = self.cleaner.resolve_group_label(label)
            if not resolved_label:
                stale_labels.append(label)
                continue

            info = self.cleaner.validated_groups.get(resolved_label, {})
            for part in ("source", "master", "instances", "backups"):
                if part not in parts:
                    continue
                detail_count += 1
                target = "backups" if part == "backups" else part
                part_nodes = self.cleaner.get_nodes_for_label(resolved_label, target=target)
                nodes.extend(part_nodes)
                if part == "backups" or (part == "source" and bool(info.get("processed"))):
                    reveal_nodes.extend(part_nodes)

        for label in stale_labels:
            self.marked_group_parts.pop(label, None)

        nodes = _dedupe_keep_order([n for n in nodes if _exists(n)])
        reveal_nodes = _dedupe_keep_order([n for n in reveal_nodes if _exists(n)])

        if not nodes:
            self.status_label.setText("Marked parts found no valid scene nodes.")
            self.refresh_group_list()
            return

        with UndoChunk("InstanceCleanerSelectMarked"):
            if reveal_nodes:
                _make_nodes_visible_for_selection(reveal_nodes, show_transform_visibility=True)
            _select_nodes(nodes)

        _frame_selected()
        stale_text = " | cleared {} stale mark(s)".format(len(stale_labels)) if stale_labels else ""
        reveal_text = " | revealed {} hidden source/backup node(s)".format(len(reveal_nodes)) if reveal_nodes else ""
        self.status_label.setText(
            "Selected {} node(s) from {} marked part(s){}{}".format(
                len(nodes), detail_count, reveal_text, stale_text))

    # -- Highlight / nav --

    def _highlight_group_item(self, label, frame=False, select=False):
        if self._highlighted_label and self._highlighted_label in self.group_items:
            self.group_items[self._highlighted_label].set_highlighted(False)
        self._highlighted_label  = label
        self.current_group_label = label
        if label in self.group_items:
            self.group_items[label].set_highlighted(True)
            if frame:
                self.groups_scroll.ensureWidgetVisible(self.group_items[label])
            if select:
                self.cleaner.select_group(label)
            info = self.cleaner.validated_groups.get(label, {})
            self.status_label.setText("Selected: {}".format(info.get("display_name", label)))

    def _fast_refresh_after_state_change(self, label=None):
        if label and label in self.group_items:
            self.group_items[label].refresh()
        report = self.cleaner.get_report()
        self._update_stat_chips()
        self._set_groups_count_summary(
            len(self.visible_group_order),
            len(self.cleaner.validated_groups),
            report,
        )

    # -- Actions --

    def do_frame_selected_group(self):
        """Reveal the group-card that owns the current Maya viewport selection.

        This is intentionally non-destructive: it does not change the Maya
        selection. It only clears UI filters when needed, scrolls the Group List
        to the matching card, and highlights it. It works with original source
        meshes, processed masters, instances and backups because processed nodes
        carry the Instance Cleaner source id attribute.
        """
        selected = _get_selected_transforms()
        if not selected:
            self.status_label.setText("VP SEL -> GROUP LIST: select a source, master, instance or backup mesh first.")
            return

        labels = self.cleaner.find_labels_for_nodes(selected, allow_compute=True)
        label = labels[0] if labels else None
        if not label or label not in self.cleaner.validated_groups:
            self.status_label.setText("VP SEL -> GROUP LIST: selection not found in current scanned groups.")
            return

        info = self.cleaner.validated_groups.get(label, {})
        display_name = info.get("display_name", label)
        filter_changed = False

        # FIND SELECTED can temporarily display only one group. If the viewport
        # selection belongs to another group, clear that temporary filter so the
        # matching card can actually appear in the list.
        if self._find_selected_filter is not None and label not in self._find_selected_filter:
            self._find_selected_filter = None
            filter_changed = True

        filter_text = self.filter_combo.currentText()
        if not self._passes_filter(info, filter_text):
            self.filter_combo.blockSignals(True)
            self.filter_combo.setCurrentText("All")
            self.filter_combo.blockSignals(False)
            filter_changed = True

        search_text = self.search_edit.text().strip().lower()
        display_l = str(display_name).lower()
        label_l = str(label).lower()
        if search_text and search_text not in display_l and search_text not in label_l:
            self.search_edit.blockSignals(True)
            self.search_edit.clear()
            self.search_edit.blockSignals(False)
            filter_changed = True

        if filter_changed or label not in self.group_items:
            self.refresh_group_list()

        if label in self.group_items:
            self._highlight_group_item(label, frame=True, select=False)
            suffix = ""
            if len(labels) > 1:
                suffix = " | {} groups matched, showing first".format(len(labels))
            self.status_label.setText("VP selection -> group list: {}{}".format(display_name, suffix))
        else:
            self.status_label.setText("VP SEL -> GROUP LIST: group found but still hidden by current UI state.")

    def do_select_viewport_group_master_instances(self):
        """Select master + instances for every group touched by the viewport selection.

        This is different from SELECT MARKED: it uses the current Maya viewport
        selection as the input. Selecting one instance or one master is enough to
        select every instance plus the master for that group. If several objects
        from different groups are selected, all matching groups are expanded at
        once. Backups are used only to identify the group and are never selected.
        """
        selected = _get_selected_transforms()
        if not selected:
            self.status_label.setText("SEL VP GROUP: select a master, instance, source or backup mesh first.")
            return

        labels = self.cleaner.find_labels_for_nodes(selected, allow_compute=True)
        if not labels:
            self.status_label.setText("SEL VP GROUP: selection not found in the current group list.")
            return

        resolved_labels = []
        output_nodes = []
        source_fallback_count = 0

        for label in labels:
            resolved = self.cleaner.resolve_group_label(label)
            if not resolved or resolved not in self.cleaner.validated_groups:
                continue
            if resolved in resolved_labels:
                continue
            resolved_labels.append(resolved)

            group_nodes = []
            group_nodes.extend(self.cleaner.get_nodes_for_label(resolved, target="master") or [])
            group_nodes.extend(self.cleaner.get_nodes_for_label(resolved, target="instances") or [])
            group_nodes = _dedupe_keep_order([n for n in group_nodes if _exists(n)])

            # Before PROCESS there is no master/instance yet. In that case only,
            # fall back to the source meshes so the command is still useful.
            info = self.cleaner.validated_groups.get(resolved, {})
            if not group_nodes and not bool(info.get("processed")):
                group_nodes = self.cleaner.get_nodes_for_label(resolved, target="source") or []
                source_fallback_count += 1

            output_nodes.extend(group_nodes)

        output_nodes = _dedupe_keep_order([n for n in output_nodes if _exists(n)])
        if not output_nodes:
            self.status_label.setText("SEL VP GROUP: no master/instance nodes found. Backups are ignored.")
            return

        with UndoChunk("InstanceCleanerSelectViewportGroups"):
            panel = _active_model_panel()
            if panel and _isolate_state(panel):
                _isolate_nodes(output_nodes, add=True, frame=False)
            else:
                _select_nodes(output_nodes)

        _frame_selected()
        if resolved_labels:
            self._highlight_group_item(resolved_labels[0], frame=True, select=False)

        fallback_text = " | {} unprocessed group(s) used source fallback".format(source_fallback_count) if source_fallback_count else ""
        self.status_label.setText(
            "SEL VP GROUP: selected {} master/instance node(s) from {} group(s){}".format(
                len(output_nodes), len(resolved_labels), fallback_text))

    def do_rebuild_group_list_from_scene(self):
        """Reload processed group cards from the current Maya scene hierarchy."""
        stats = self.cleaner.rebuild_groups_from_scene()
        self._find_selected_filter = None
        self.checked_group_labels = set()
        self.current_group_label = None
        self._highlighted_label = None

        try:
            self.filter_combo.blockSignals(True)
            self.filter_combo.setCurrentText("All")
            self.filter_combo.blockSignals(False)
            self.search_edit.blockSignals(True)
            self.search_edit.clear()
            self.search_edit.blockSignals(False)
        except Exception:
            pass

        self.refresh_group_list()
        if stats.get("groups", 0):
            self.status_label.setText(
                "Rebuilt Group List from scene: {groups} group(s), {masters} master(s), {instances} instance(s), {backups} backup(s).".format(**stats))
        else:
            self.status_label.setText(
                "No Instance Cleaner groups found in scene. Expected _INSTANCE_CLEANER with tagged masters/instances/backups.")
        return stats

    def _get_detect_method(self):
        txt = self.detect_method_combo.currentText().lower()
        if txt.startswith("robust"):   return "robust_shape"
        if txt.startswith("topology"): return "topology"
        if txt.startswith("geometry"): return "geometry"
        if txt.startswith("exact"):    return "exact"
        return "signature"

    def _get_match_tolerance(self):
        # Robust Similar uses the Shape tol slider from the integrated algorithm.
        # Older UVOptimizer-style methods keep using the generic Tolerance spinbox.
        method = self._get_detect_method()
        if method in ("robust", "robust_shape", "similar", "robust_similar"):
            return self.fuzzy_size_slider.value()
        return self.compare_tolerance_spin.value()

    def _get_scale_ratio_max(self):
        try:
            return max(1.0, float(self.scale_ratio_slider.value()))
        except Exception:
            return ROBUST_REAL_SIZE_RATIO_MAX

    def _get_scale_ratio_min(self):
        try:
            ratio_max = self._get_scale_ratio_max()
            return 1.0 / ratio_max if ratio_max > ROBUST_EPSILON else ROBUST_REAL_SIZE_RATIO_MIN
        except Exception:
            return ROBUST_REAL_SIZE_RATIO_MIN

    def _update_scale_ratio_label(self):
        if not hasattr(self, "scale_ratio_info_label"):
            return
        ratio_min = self._get_scale_ratio_min()
        ratio_max = self._get_scale_ratio_max()
        self.scale_ratio_info_label.setText("{:.2f}x-{:.2f}x".format(ratio_min, ratio_max))
        self.scale_ratio_info_label.setToolTip(
            "Scale filter currently accepts candidates between {:.2f}x and {:.2f}x of the reference size."
            .format(ratio_min, ratio_max))

    def _set_scale_ratio_enabled(self, enabled):
        enabled = bool(enabled)
        if hasattr(self, "scale_ratio_slider"):
            self.scale_ratio_slider.setEnabled(enabled)
        if hasattr(self, "scale_ratio_info_label"):
            self.scale_ratio_info_label.setEnabled(enabled)
        self._update_scale_ratio_label()

    def do_refresh_scene(self):
        # Deliberately forces a full-scene scan; use REFRESH CURRENT MODE to respect the Source combo.
        self.scan_mode_combo.setCurrentText("Scene")
        return self.do_scan()

    def do_refresh_current(self):
        return self.do_scan()

    def _make_progress(self, title):
        dlg = ICProgressDialog(title, self)
        dlg.show()
        QApplication.processEvents()
        return dlg, dlg.callback(), dlg.was_canceled

    def do_scan(self):
        previous_filter = self._find_selected_filter
        self._find_selected_filter = None
        roots          = None
        selection_only = False
        mode           = self.scan_mode_combo.currentText()

        if mode == "Selected Mesh(es)":
            selected_roots = _get_selected_transforms()
            if not selected_roots:
                self.progress_bar.setValue(0)
                self._find_selected_filter = previous_filter
                self.status_label.setText("Selection scan canceled: select at least one mesh. Existing scan kept.")
                return
            roots          = selected_roots
            selection_only = True

        dlg, progress_cb, cancel_cb = self._make_progress("Instance Cleaner - {} scan".format(mode))

        progress_tick = {"i": 0}

        def local_progress(*args, **kwargs):
            progress_tick["i"] += 1
            progress_cb(*args, **kwargs)
            percent = kwargs.get("percent", args[0] if len(args) == 2 else None)
            if percent is None and "current" in kwargs:
                percent = int(float(kwargs.get("current", 0)) / float(max(1, kwargs.get("total", 1))) * 100.0)
            self.progress_bar.setValue(max(0, min(100, int(percent or 0))))
            self.status_label.setText(_short(str(kwargs.get("message", args[1] if len(args) > 1 else "Scanning"))))
            if progress_tick["i"] % 8 == 0:
                QApplication.processEvents()

        self.progress_bar.setValue(0)
        previous_state = (
            list(self.cleaner.signatures),
            dict(self.cleaner.signature_by_transform),
            dict(self.cleaner.groups_safe),
            dict(self.cleaner.groups_fuzzy),
            list(self.cleaner.uniques),
            dict((k, dict(v)) for k, v in self.cleaner.validated_groups.items()),
        )
        try:
            count = self.cleaner.scan(
                roots=roots, selection_only=selection_only,
                strict_tol=self.strict_tol_slider.value(),
                detect_method=self._get_detect_method(),
                compare_tolerance=self._get_match_tolerance(),
                ignore_scale=self.ignore_scale_cb.isChecked(),
                real_size_ratio_max=self._get_scale_ratio_max(),
                fuzzy_enabled=self.fuzzy_enabled_cb.isChecked(),
                fuzzy_vertex_tol=self.fuzzy_vertex_spin.value(),
                fuzzy_size_tol=self.fuzzy_size_slider.value(),
                fuzzy_score_min=self.fuzzy_score_slider.value(),
                min_copies=self.min_copies_spin.value(),
                progress_cb=local_progress,
                cancel_cb=cancel_cb,
            )
        except ProcessCanceled:
            (self.cleaner.signatures, self.cleaner.signature_by_transform,
             self.cleaner.groups_safe, self.cleaner.groups_fuzzy,
             self.cleaner.uniques, self.cleaner.validated_groups) = previous_state
            self.progress_bar.setValue(0)
            self.status_label.setText("{} scan canceled. Previous scan restored.".format(mode))
            cmds.warning("[IC] {} scan canceled by user; previous scan restored.".format(mode))
            self.refresh_group_list()
            dlg.close()
            return
        finally:
            dlg.close()

        self.progress_bar.setValue(100)
        report = self.cleaner.get_report()
        scope = "Scene scan" if not selection_only else "Selection scan"
        self.status_label.setText(
            "{} complete: {} groups | {} safe | {} fuzzy | {} unique".format(
                scope, count, report["safe_groups"], report["fuzzy_groups"], report["unique_meshes"]))
        self.refresh_group_list()

    def do_find_selected(self):
        selected_roots = _get_selected_transforms()
        if not selected_roots:
            self.status_label.setText("Find selected: select at least one mesh. Existing scan kept.")
            return

        source = selected_roots[0]
        method = self._get_detect_method()
        if method == "signature":
            method = "geometry"

        dlg, progress_cb, cancel_cb = self._make_progress("Instance Cleaner - Find Selected")
        try:
            label, matches = self.cleaner.find_fast_group_for_source(
                source,
                method=method,
                tolerance=self._get_match_tolerance(),
                ignore_scale=self.ignore_scale_cb.isChecked(),
                real_size_ratio_max=self._get_scale_ratio_max(),
                min_copies=self.min_copies_spin.value(),
                progress_cb=progress_cb,
                cancel_cb=cancel_cb,
            )
        except ProcessCanceled:
            dlg.close()
            self.status_label.setText("Find selected canceled. Existing scan kept.")
            cmds.warning("[IC] Find selected canceled by user.")
            return
        finally:
            dlg.close()

        if label is None:
            self._find_selected_filter = set()
            self.current_group_label = None
            self.refresh_group_list()
            self.status_label.setText(
                "Find selected: no group found for {} | {} candidate match(es), need min {}.".format(
                    _short(source), len(matches), self.min_copies_spin.value()))
            cmds.warning("[IC] Find selected found no processable group for {}. Existing scan kept.".format(_short(source)))
            return

        self._find_selected_filter = set([label])
        self.current_group_label = label
        self.filter_combo.setCurrentText("All")
        self.search_edit.clear()
        self.refresh_group_list()
        nodes = self.cleaner.select_group(label)
        _isolate_nodes(nodes, add=False, frame=True)
        self._highlight_group_item(label, frame=True, select=False)
        info = self.cleaner.validated_groups.get(label, {})
        self.status_label.setText(
            "Find selected: {} match(es) in current group '{}' | selected in Maya.".format(
                len(nodes), info.get("display_name", label)))

    def do_show_all_groups(self):
        self._find_selected_filter = None
        self.refresh_group_list()
        self.status_label.setText("Showing all scanned groups. Current group: {}".format(
            self.cleaner.validated_groups.get(self.current_group_label, {}).get("display_name", "none")))

    def _passes_filter(self, info, filter_text):
        if filter_text == "Safe"      and info["type"] != MATCH_SAFE:   return False
        if filter_text == "Fuzzy"     and info["type"] != MATCH_FUZZY:  return False
        if filter_text == "Accepted"  and info["accepted"] is not True:  return False
        if filter_text == "Rejected"  and info["accepted"] is not False: return False
        if filter_text == "Processed" and not info.get("processed"):     return False
        return True

    def _sort_items(self, items):
        st = self.sort_combo.currentText()
        key_map = {
            "Copies high":    lambda x: (-len(x[1].get("meshes",[])), x[1].get("display_name","").lower()),
            "Copies low":     lambda x: ( len(x[1].get("meshes",[])), x[1].get("display_name","").lower()),
            "Score high":     lambda x: (-float(x[1].get("score",0.) or 0.), x[1].get("display_name","").lower()),
            "Score low":      lambda x: ( float(x[1].get("score",0.) or 0.), x[1].get("display_name","").lower()),
            "Name A-Z":       lambda x: x[1].get("display_name","").lower(),
            "Name Z-A":       lambda x: x[1].get("display_name","").lower(),
            "Type":           lambda x: (x[1].get("type",""), x[1].get("display_name","").lower()),
            "Accepted first": lambda x: (x[1].get("accepted") is not True, x[1].get("display_name","").lower()),
            "Fuzzy first":    lambda x: (x[1].get("type") != MATCH_FUZZY, x[1].get("display_name","").lower()),
        }
        rev = st == "Name Z-A"
        return sorted(items, key=key_map.get(st, lambda x: x[0]), reverse=rev)

    def refresh_group_list(self):
        for i in range(self.groups_layout.count()-1, -1, -1):
            w = self.groups_layout.itemAt(i).widget()
            if isinstance(w, GroupItem):
                self.groups_layout.takeAt(i)
                w.deleteLater()

        self.group_items         = {}
        self.visible_group_order = []

        filter_text = self.filter_combo.currentText()
        search_text = self.search_edit.text().strip().lower()
        all_items   = list(self.cleaner.validated_groups.items())

        filtered = []
        for label, info in all_items:
            if self._find_selected_filter is not None and label not in self._find_selected_filter:
                continue
            if not self._passes_filter(info, filter_text):
                continue
            dname = info.get("display_name", label).lower()
            if search_text and search_text not in dname and search_text not in label.lower():
                continue
            filtered.append((label, info))

        filtered  = self._sort_items(filtered)
        has_items = False

        for i, (label, info) in enumerate(filtered):
            has_items = True
            w = GroupItem(label, info, ui_scale=self.ui_scale)
            w.accept_clicked.connect(lambda lbl, self=self: self._run_ui_action("Accept group", self.on_accept_group, lbl))
            w.reject_clicked.connect(lambda lbl, self=self: self._run_ui_action("Reject group", self.on_reject_group, lbl))
            w.select_clicked.connect(lambda lbl, self=self: self._run_ui_action("Select group", self.on_select_group, lbl))
            w.master_clicked.connect(lambda lbl, self=self: self._run_ui_action("Select master", self.on_select_master, lbl))
            w.instances_clicked.connect(lambda lbl, self=self: self._run_ui_action("Select instances", self.on_select_instances, lbl))
            w.all_clicked.connect(lambda lbl, self=self: self._run_ui_action("Select master + instances", self.on_select_master_and_instances, lbl))
            w.backups_clicked.connect(lambda lbl, self=self: self._run_ui_action("Select backups", self.on_select_backups, lbl))
            w.checked_changed.connect(lambda lbl, checked, self=self: self._run_ui_action("Check group", self._on_group_checked_changed, lbl, checked))
            w.part_checked_changed.connect(lambda lbl, part, checked, self=self: self._run_ui_action("Mark group part", self._on_group_part_checked_changed, lbl, part, checked))
            w.set_checked(label in self.checked_group_labels)
            w.set_parts_checked(self.marked_group_parts.get(label, set()))
            self.groups_layout.insertWidget(i, w)
            self.group_items[label]  = w
            self.visible_group_order.append(label)
            if label == self._highlighted_label:
                w.set_highlighted(True)

        if self._find_selected_filter is not None:
            self.groups_empty.setText("No groups found for the current selection.\nTry another mesh or use SHOW ALL GROUPS.")
        else:
            self.groups_empty.setText("No global groups found.\nTry REFRESH SCENE, Signature + Fuzzy, or lower Min copies.")

        self.groups_empty.setVisible(not has_items)

        self.groups_scroll.setMaximumHeight(16777215)
        self.groups_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        report = self.cleaner.get_report()
        self._update_stat_chips()
        self._set_groups_count_summary(len(filtered), len(all_items), report)
        self._update_window_compactness(len(all_items))

    def _available_screen_geometry(self):
        try:
            screen = QApplication.screenAt(self.frameGeometry().center())
            if screen:
                return screen.availableGeometry()
        except Exception:
            pass
        try:
            screen = QApplication.primaryScreen()
            if screen:
                return screen.availableGeometry()
        except Exception:
            pass
        try:
            return QApplication.desktop().availableGeometry(self)
        except Exception:
            return None

    def _target_window_size_for_layout(self, compact):
        try:
            if self.layout():
                self.layout().activate()
            if hasattr(self, "left_col") and self.left_col.layout():
                self.left_col.layout().activate()
            if hasattr(self, "right_col") and self.right_col.layout():
                self.right_col.layout().activate()
        except Exception:
            pass

        if compact:
            left_hint = self.left_col.sizeHint() if hasattr(self, "left_col") else QSize(self._s(360), self._s(560))
            target_w = max(self._s(390), left_hint.width() + self._s(28))
            # Fit to content height, then clamp to the screen so the left column scrolls
            # instead of opening taller than a Full-HD viewport.
            target_h = left_hint.height() + self._s(36)
            min_w, min_h = self._s(340), min(self._s(360), target_h)
        else:
            left_hint = self.left_col.sizeHint() if hasattr(self, "left_col") else QSize(self._s(340), self._s(520))
            right_hint = self.right_col.sizeHint() if hasattr(self, "right_col") else QSize(self._s(280), self._s(440))
            target_w = max(self._s(680), left_hint.width() + right_hint.width() + self._s(42))
            target_h = max(max(left_hint.height(), right_hint.height()) + self._s(54), self._s(460))
            min_w, min_h = self._s(620), self._s(390)

        geom = self._available_screen_geometry()
        if geom:
            screen_w = int(geom.width())
            screen_h = int(geom.height())
            if screen_h <= 1100:
                max_w = int(screen_w * 0.90)
                max_h = int(screen_h * 0.84)
            else:
                max_w = int(screen_w * 0.94)
                max_h = int(screen_h * 0.90)
            target_w = min(target_w, max_w)
            target_h = min(target_h, max_h)

        target_w = max(target_w, min_w)
        target_h = max(target_h, min_h)
        return target_w, target_h, min_w, min_h

    def _resize_window_programmatically(self, width, height):
        self._programmatic_resize = True
        try:
            self.resize(int(width), int(height))
        finally:
            self._programmatic_resize = False

    def _autofit_current_layout(self):
        total = len(self.cleaner.validated_groups) if hasattr(self, "cleaner") else 0
        self._update_window_compactness(total, force=True, allow_resize=True)

    def _update_window_compactness(self, total_count, force=False, allow_resize=False):
        compact = total_count == 0
        state_changed = self._compact_state != compact
        if not force and not state_changed:
            return
        self._compact_state = compact

        self.right_col.setVisible(not compact)
        if compact:
            self.right_col.setMinimumWidth(0)
            if hasattr(self, "left_scroll"):
                self.left_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
                self.left_scroll.setMaximumHeight(16777215)
        else:
            self.right_col.setMinimumWidth(self._s(250))
            if hasattr(self, "left_scroll"):
                self.left_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
                self.left_scroll.setMaximumHeight(16777215)

        target_w, target_h, min_w, min_h = self._target_window_size_for_layout(compact)
        self.setMinimumSize(min_w, min_h)

        if allow_resize or (force and not self._user_resized):
            self._resize_window_programmatically(target_w, target_h)
        self.updateGeometry()

    def resizeEvent(self, event):
        if (getattr(self, "_compact_state", None) is not None and
                not getattr(self, "_programmatic_resize", False)):
            self._user_resized = True
        super(InstanceCleanerUI, self).resizeEvent(event)

    def on_accept_group(self, label):
        self.cleaner.accept_group(label)
        if label in self.group_items:
            self.group_items[label].refresh()
        self._highlight_group_item(label)
        self._fast_refresh_after_state_change(label)

    def on_reject_group(self, label):
        self.cleaner.reject_group(label)
        if label in self.group_items:
            self.group_items[label].refresh()
        self._highlight_group_item(label)
        self._fast_refresh_after_state_change(label)

    def _select_and_maybe_isolate(self, label, target="source", add=True):
        resolved_label = self.cleaner.resolve_group_label(label) or label
        nodes = self.cleaner.get_nodes_for_label(resolved_label, target=target)
        if not nodes:
            self.status_label.setText("No {} for group".format(target))
            return []
        info = self.cleaner.validated_groups.get(resolved_label, {})
        reveal_hidden = (target == "backups") or (target == "source" and bool(info.get("processed")))
        if reveal_hidden:
            _make_nodes_visible_for_selection(nodes, show_transform_visibility=True)
        panel = _active_model_panel()
        if add and panel and _isolate_state(panel):
            _isolate_nodes(nodes, add=True, frame=False)
        else:
            _select_nodes(nodes)
        return nodes

    def on_select_group(self, label):
        nodes = self._select_and_maybe_isolate(label, "source", add=True)
        self._highlight_group_item(label)
        if nodes:
            self.status_label.setText("Source: {} meshes".format(len(nodes)))

    def on_select_master(self, label):
        nodes = self.cleaner.select_master(label)
        self._highlight_group_item(label)
        if nodes:
            _frame_selected()

    def on_select_instances(self, label):
        nodes = self.cleaner.select_instances(label)
        self._highlight_group_item(label)
        if nodes:
            _frame_selected()

    def on_select_master_and_instances(self, label):
        nodes = self.cleaner.select_master_and_instances(label)
        self._highlight_group_item(label)
        if nodes:
            _frame_selected()
            self.status_label.setText("Selected master + instances: {} nodes".format(len(nodes)))
        else:
            self.status_label.setText("No master/instances for group")

    def on_select_backups(self, label):
        nodes = self._select_and_maybe_isolate(label, "backups", add=True)
        self._highlight_group_item(self.cleaner.resolve_group_label(label) or label)
        if nodes:
            _frame_selected()
            self.status_label.setText("Backups: {} node(s) selected and revealed".format(len(nodes)))

    def do_accept_safe(self):
        for label, info in self.cleaner.validated_groups.items():
            if not info.get("processed") and info.get("type") == MATCH_SAFE:
                self.cleaner.accept_group(label)
        self.refresh_group_list()

    def do_accept_all(self):
        for label, info in self.cleaner.validated_groups.items():
            if not info.get("processed"):
                self.cleaner.accept_group(label)
        self.refresh_group_list()

    def do_reject_all(self):
        for label in self.cleaner.validated_groups:
            self.cleaner.reject_group(label)
        self.refresh_group_list()

    def do_merge_selected_groups(self):
        selected = _get_selected_transforms()
        labels   = self.cleaner.find_labels_for_nodes(selected, allow_compute=True)
        checked  = [l for l in self.visible_group_order
                    if l in self.checked_group_labels and l in self.cleaner.validated_groups]
        for label in checked:
            if label not in labels:
                labels.append(label)
        if self.current_group_label and self.current_group_label in self.cleaner.validated_groups:
            if self.current_group_label not in labels:
                labels.insert(0, self.current_group_label)
        if len(labels) < 2:
            self.status_label.setText("Merge: select meshes from 2 groups or check 2+ group cards.")
            return
        primary = self.current_group_label if self.current_group_label in labels else labels[0]
        stats   = self.cleaner.merge_groups(labels, primary_label=primary)
        self.checked_group_labels.difference_update(labels)
        self.refresh_group_list()
        tgt = stats.get("target")
        if tgt:
            self._highlight_group_item(tgt, frame=True)
        self.status_label.setText("Merged {} groups | {} meshes".format(
            stats.get("merged",0), stats.get("meshes",0)))

    def do_split_selected_from_group(self):
        selected = _get_selected_transforms()
        selected_label = self._find_group_from_selection() if selected else None
        label = selected_label or self.current_group_label
        if not label:
            self.status_label.setText("Split: select source meshes from a group or highlight a group first.")
            return
        stats = self.cleaner.split_selected_from_group(label, selected)
        if not stats.get("split"):
            self.status_label.setText("Split: select only part of one unprocessed group.")
            return
        nl = stats.get("new_label")
        if self._find_selected_filter is not None and nl:
            self._find_selected_filter.update([label, nl])
        self.refresh_group_list()
        if nl:
            self._highlight_group_item(nl, frame=True, select=True)
        self.status_label.setText("Split {} meshes into new group".format(stats.get("split",0)))

    def _editable_current_label(self, action):
        label = self.current_group_label or self._find_group_from_selection()
        if not label or label not in self.cleaner.validated_groups:
            self.status_label.setText("{}: highlight a group first.".format(action))
            return None
        if self.cleaner.validated_groups[label].get("processed"):
            self.status_label.setText("{}: processed groups cannot be edited.".format(action))
            return None
        return label

    def do_add_selected_to_group(self):
        label = self._editable_current_label("Add selected")
        if not label:
            return
        selected = _get_selected_transforms()
        if not selected:
            self.status_label.setText("Add selected: select mesh(es) in the viewport first.")
            return
        stats = self.cleaner.add_selected_to_group(label, selected)
        self.refresh_group_list()
        self._highlight_group_item(label, frame=True, select=True)
        self.status_label.setText(
            "Added {} mesh(es) | group total {} | removed from other groups {}".format(
                stats.get("added", 0), stats.get("total", 0), stats.get("removed_from_other", 0)))

    def do_remove_selected_from_group(self):
        label = self._editable_current_label("Remove selected")
        if not label:
            return
        selected = _get_selected_transforms()
        if not selected:
            self.status_label.setText("Remove selected: select mesh(es) in the viewport first.")
            return
        stats = self.cleaner.remove_selected_from_group(label, selected)
        self.refresh_group_list()
        self._highlight_group_item(label, frame=True, select=True)
        suffix = " | kept minimum 2 meshes" if stats.get("kept_minimum") else ""
        self.status_label.setText(
            "Removed {} mesh(es) | group total {}{}".format(
                stats.get("removed", 0), stats.get("total", 0), suffix))

    def do_set_selected_as_master(self):
        selected = _get_selected_transforms()
        label    = self._find_group_from_selection() or self.current_group_label
        if not label:
            self.status_label.setText("Set master: select a mesh from a scanned group.")
            return
        preferred = self.cleaner.set_preferred_master_from_selection(label, selected)
        if not preferred:
            self.status_label.setText("Set master: selected mesh must be inside an unprocessed group.")
            return
        self.refresh_group_list()
        self._highlight_group_item(label, frame=True, select=False)
        self.status_label.setText("Master reference set to {}".format(_short(preferred)))


    def _labels_from_ordered_viewport_selection(self):
        labels = []
        for node in _get_ordered_selected_transforms():
            candidates = _collect_mesh_transforms_from_roots([node], include_ic=True)
            if not candidates and _exists(node):
                candidates = [node]
            for mesh in candidates:
                label = self.cleaner.find_group_for_mesh(mesh, allow_compute=True)
                if label and label not in labels:
                    labels.append(label)
        return labels

    def do_merge_masters_to_target(self):
        """Merge processed duplicate master groups into one target master group."""
        ordered_labels = self._labels_from_ordered_viewport_selection()
        checked = [l for l in self.visible_group_order
                   if l in self.checked_group_labels and l in self.cleaner.validated_groups]

        if len(ordered_labels) >= 2:
            # Maya-style workflow: select old master(s)/instances first, target master last.
            target = ordered_labels[-1]
            sources = ordered_labels[:-1] + [l for l in checked if l != target]
        else:
            # Alternative workflow: highlight the target card, then check/select source groups.
            target = self.current_group_label if self.current_group_label in self.cleaner.validated_groups else None
            if not target and ordered_labels:
                target = ordered_labels[-1]
            sources = checked + [l for l in ordered_labels if l != target]

        sources = [l for l in _dedupe_keep_order(sources) if l and l != target]

        if not target or not sources:
            self.status_label.setText(
                "Merge masters: select old master(s)/instances first, then the target master last; or highlight target and check source group cards.")
            return

        bad = [l for l in [target] + sources
               if not self.cleaner.validated_groups.get(l, {}).get("processed")]
        if bad:
            self.status_label.setText("Merge masters: only processed/DONE groups can be merged this way.")
            return

        stats = self.cleaner.replace_master_groups_with_target(sources, target, delete_source_masters=True)
        if stats.get("error"):
            self.status_label.setText("Merge masters: {}".format(stats.get("error")))
            return

        # After remastering, old instance names are no longer meaningful. Normalize
        # the target group immediately so all instances/backups are numbered together.
        target_after_merge = stats.get("target_label") or target
        rename_stats = self.cleaner.rename_processed_group_nodes(labels=[target_after_merge], include_backups=True, include_converted=True)

        self.checked_group_labels.difference_update(sources)
        self._find_selected_filter = None
        self.current_group_label = target_after_merge
        self._highlighted_label = self.current_group_label
        self.refresh_group_list()
        if self.current_group_label in self.cleaner.validated_groups:
            self._highlight_group_item(self.current_group_label, frame=True, select=False)

        warn_txt = " | SHAPE WARNING {}".format(stats.get("shape_warnings", 0)) if stats.get("shape_warnings", 0) else ""
        self.status_label.setText(
            "Merged {} source group(s) into target | replaced {} instance(s) | retagged {} backup/source node(s) | deleted {} old master(s) | renamed {} node(s){}".format(
                stats.get("sources", 0), stats.get("replaced_instances", 0),
                stats.get("retagged_nodes", 0), stats.get("deleted_masters", 0),
                rename_stats.get("masters", 0) + rename_stats.get("instances", 0) + rename_stats.get("backups", 0) + rename_stats.get("converted", 0), warn_txt))

    def do_select_all_masters(self):
        n = self.cleaner.select_all_masters()
        self.status_label.setText("Selected {} masters".format(n))

    def do_organize_masters(self):
        stats = self.cleaner.organize_masters(spacing=10.)
        self.status_label.setText("Organized {} masters".format(stats.get("organized",0)))

    def do_exit_isolate(self):
        _exit_isolate_all_panels()
        self.status_label.setText("Exited isolate")

    def do_isolate_current_source(self):
        if not self.current_group_label:
            if self.visible_group_order:
                self.current_group_label = self.visible_group_order[0]
            else:
                self.status_label.setText("No group to isolate")
                return
        self._isolate_label_source(self.current_group_label, frame_list=True)

    def _isolate_label_source(self, label, frame_list=True):
        nodes = self.cleaner.get_nodes_for_label(label, target="source")
        if not nodes:
            self.status_label.setText("No source for group")
            return
        _isolate_nodes(nodes, add=False, frame=True)
        self._highlight_group_item(label, frame=frame_list)
        info = self.cleaner.validated_groups.get(label, {})
        self.status_label.setText("Review: {} | {} meshes".format(
            info.get("display_name", label), len(nodes)))

    def _navigate_review(self, direction):
        if not self.visible_group_order:
            return
        if self.current_group_label not in self.visible_group_order:
            idx = 0 if direction >= 0 else len(self.visible_group_order)-1
        else:
            idx = self.visible_group_order.index(self.current_group_label)
            idx = max(0, min(len(self.visible_group_order)-1, idx+direction))
        self._isolate_label_source(self.visible_group_order[idx], frame_list=True)

    def do_accept_current_and_next(self):
        if self.current_group_label:
            self.cleaner.accept_group(self.current_group_label)
            if self.current_group_label in self.group_items:
                self.group_items[self.current_group_label].refresh()
            self._fast_refresh_after_state_change(self.current_group_label)
        self._navigate_review(1)

    def do_reject_current_and_next(self):
        if self.current_group_label:
            self.cleaner.reject_group(self.current_group_label)
            if self.current_group_label in self.group_items:
                self.group_items[self.current_group_label].refresh()
            self._fast_refresh_after_state_change(self.current_group_label)
        self._navigate_review(1)

    def do_stop_process(self):
        if not self._is_processing:
            return
        self._cancel_requested = True
        self.status_label.setText("Stopping after current operation...")
        QApplication.processEvents()

    def _set_processing_ui(self, state):
        self._is_processing = bool(state)
        self.process_btn.setEnabled(not state)
        self.cancel_btn.setEnabled(not state)
        self.stop_process_btn.setEnabled(state)

    def _labels_for_current_process_context(self, force_current=False):
        if force_current:
            label = self.current_group_label
            if self._find_selected_filter:
                label = next(iter(self._find_selected_filter))
            return [label] if label in self.cleaner.validated_groups else []
        if self._find_selected_filter is not None:
            labels = [l for l in self._find_selected_filter if l in self.cleaner.validated_groups]
            return labels
        return None

    def _rename_target_labels(self):
        checked = [l for l in self.visible_group_order
                   if l in self.checked_group_labels and l in self.cleaner.validated_groups]
        if checked:
            return checked
        if self.current_group_label and self.current_group_label in self.cleaner.validated_groups:
            return [self.current_group_label]
        visible = [l for l in self.visible_group_order if l in self.cleaner.validated_groups]
        if visible:
            return visible
        return list(self.cleaner.validated_groups.keys())

    def do_rename_processed_nodes(self, labels=None):
        if labels is None:
            labels = self._rename_target_labels()
        labels = [l for l in (labels or []) if l in self.cleaner.validated_groups]
        if not labels:
            self.status_label.setText("Rename nodes: no group available.")
            return {}
        stats = self.cleaner.rename_processed_group_nodes(labels=labels, include_backups=True, include_converted=True)
        self.refresh_group_list()
        target = None
        if self.current_group_label in self.cleaner.validated_groups:
            target = self.current_group_label
        elif labels:
            target = labels[0]
        if target in self.cleaner.validated_groups:
            self._highlight_group_item(target, frame=True, select=False)
        self.status_label.setText(
            "Renamed IC nodes: {groups} group(s) | MST {masters} | INS {instances} | BKP {backups} | GEO {converted} | skipped {skipped}".format(**stats))
        return stats

    def _shader_target_labels(self):
        checked = [l for l in self.visible_group_order if l in self.checked_group_labels and l in self.cleaner.validated_groups]
        if checked:
            return checked
        if self.current_group_label in self.cleaner.validated_groups:
            return [self.current_group_label]
        visible = [l for l in self.visible_group_order if l in self.cleaner.validated_groups]
        if visible:
            return visible
        return list(self.cleaner.validated_groups.keys())

    def do_assign_color_shaders(self, labels=None):
        if labels is None:
            labels = self._shader_target_labels()
        labels = [l for l in (labels or []) if l in self.cleaner.validated_groups]
        if not labels:
            self.status_label.setText("Assign shaders: no group available.")
            return {}
        include_backups = bool(self.assign_shader_backups_cb.isChecked()) if hasattr(self, "assign_shader_backups_cb") else False
        stats = self.cleaner.assign_color_shaders(labels=labels, include_backups=include_backups)
        self.status_label.setText(
            "Assigned color shaders: {} group(s), {} node(s), saved {} shader state(s){}".format(
                stats.get("groups", 0), stats.get("nodes", 0), stats.get("saved_shaders", 0),
                " incl. {} backup(s)".format(stats.get("backups", 0)) if include_backups else ""))
        return stats

    def do_assign_master_shaders_to_instances(self):
        selected = _get_selected_transforms()
        if not selected:
            self.status_label.setText("Master material > instances: select one or more IC masters or instances in the viewport first.")
            return {}
        stats = self.cleaner.assign_master_shaders_to_instances(selected_nodes=selected)
        if not stats.get("groups"):
            self.status_label.setText(
                "Master material > instances: no valid selected IC master/instance group found. "
                "Select a processed master or instance under _INSTANCE_CLEANER."
            )
            return stats
        self.status_label.setText(
            "Master material > instances: {groups} group(s), {assigned}/{instances} instance(s), cleared {cleared_overrides} override(s){multi}{shader}.".format(
                multi=" | multi-material fallback" if stats.get("multi_material_groups") else "",
                shader=" | shader: " + ", ".join(stats.get("used_shaders", [])[:3]) if stats.get("used_shaders") else "",
                **stats))
        return stats

    def do_save_current_shaders(self):
        labels = self._shader_target_labels()
        include_backups = bool(self.assign_shader_backups_cb.isChecked()) if hasattr(self, "assign_shader_backups_cb") else True
        stats = self.cleaner.save_current_shaders(labels=labels, include_backups=include_backups, overwrite=False)
        self.status_label.setText("Saved shaders on {} / {} node(s).".format(stats.get("saved", 0), stats.get("nodes", 0)))
        return stats

    def do_restore_saved_shaders(self):
        labels = self._shader_target_labels()
        include_backups = bool(self.assign_shader_backups_cb.isChecked()) if hasattr(self, "assign_shader_backups_cb") else True
        stats = self.cleaner.restore_saved_shaders(labels=labels, include_backups=include_backups, remove_saved_attrs=False)
        self.status_label.setText("Restored shaders on {} node(s), missing {}.".format(stats.get("restored", 0), stats.get("missing", 0)))
        return stats

    def do_clean_debug_shaders(self):
        stats = self.cleaner.clean_debug_shaders()
        self.status_label.setText("Clean shaders: deleted {}, skipped used {}.".format(stats.get("deleted", 0), stats.get("skipped_used", 0)))
        return stats

    def do_validate_repair_scene(self):
        stats = self.cleaner.validate_repair_scene(repair=True)
        self.refresh_group_list()
        self.status_label.setText(
            "Validate/repair: {groups} group(s), MST {masters}, INS {instances}, BKP {backups}, missing MST {missing_master}, extra MST {extra_masters}, reparented {reparented}".format(**stats))
        return stats

    def do_process_current(self):
        return self.do_process(force_current=True)

    def do_process(self, force_current=False):
        accepted_now = [
            l for l, info in self.cleaner.validated_groups.items()
            if info.get("accepted") is True and not info.get("processed")
        ]

        labels = None
        contextual = False

        if force_current:
            labels = self._labels_for_current_process_context(force_current=True)
            contextual = True
            if not labels:
                self.status_label.setText("Process current: no current group to process.")
                return
        elif accepted_now:
            # Regular PROCESS is intentionally global: after SPLIT / ADD SEL + ACCEPT ALL,
            # every accepted unprocessed group is processed, including manual split groups.
            labels = None
            contextual = False
            if self._find_selected_filter is not None:
                self.status_label.setText("Process: processing all accepted groups, not only the filtered view.")
        else:
            cur = self.current_group_label
            cur_info = self.cleaner.validated_groups.get(cur, {}) if cur else {}
            if cur_info and not cur_info.get("processed") and cur_info.get("accepted") is not False:
                labels = [cur]
                contextual = True
            else:
                self.status_label.setText("Process: no accepted group. Accept groups first or highlight one editable group.")
                return

        self._cancel_requested = False
        self._set_processing_ui(True)
        title = "Instance Cleaner - Process {}".format("current/filtered group" if contextual else "accepted groups")
        dlg, progress_cb, cancel_cb = self._make_progress(title)

        def combined_cancel_cb():
            QApplication.processEvents()
            return self._cancel_requested or cancel_cb()

        try:
            stats = self.cleaner.create_masters_and_replace(
                master_spacing=self.master_spacing_spin.value(),
                keep_hidden_backups=True,
                delete_originals=False,
                use_pca_icp_alignment=True,
                progress_cb=progress_cb,
                cancel_cb=combined_cancel_cb,
                labels=labels,
            )
        except Exception as e:
            cmds.warning("[IC] Process exception: {}\n{}".format(e, traceback.format_exc()))
            stats = {}
        finally:
            dlg.close()
            self._set_processing_ui(False)

        if not stats:
            self.progress_bar.setValue(0)
            self.status_label.setText("Process: no matching unprocessed group in the requested scope.")
            return

        processed_labels = stats.get("processed_labels", [])
        if stats.get("canceled"):
            rb = stats.get("rollback") or {}
            self.progress_bar.setValue(0)
            self.status_label.setText(
                "Process stopped | restored {} | del inst {} | del masters {}".format(
                    rb.get("restored",0), rb.get("deleted_instances",0), rb.get("deleted_masters",0)))
        else:
            self.progress_bar.setValue(100)
            self.status_label.setText(
                "Process complete ({}): masters {} | instances {} | backups {} | skipped {}".format(
                    stats.get("process_scope", "scope"), stats["masters_created"], stats["instances_created"],
                    stats["backups_created"], stats["groups_skipped"]))

        post_process_labels = processed_labels or (labels or [])
        if not post_process_labels and accepted_now:
            post_process_labels = accepted_now

        if (not stats.get("canceled")) and hasattr(self, "rename_on_process_cb") and self.rename_on_process_cb.isChecked():
            rename_stats = self.cleaner.rename_processed_group_nodes(labels=post_process_labels, include_backups=True, include_converted=True)
            try:
                self.status_label.setText(self.status_label.text() + " | renamed {} node(s)".format(
                    rename_stats.get("masters", 0) + rename_stats.get("instances", 0) + rename_stats.get("backups", 0) + rename_stats.get("converted", 0)))
            except Exception:
                pass

        if (not stats.get("canceled")) and hasattr(self, "assign_shader_on_process_cb") and self.assign_shader_on_process_cb.isChecked():
            include_backups = bool(self.assign_shader_backups_cb.isChecked()) if hasattr(self, "assign_shader_backups_cb") else False
            shader_stats = self.cleaner.assign_color_shaders(labels=post_process_labels, include_backups=include_backups)
            try:
                self.status_label.setText(self.status_label.text() + " | color shaders {} node(s), saved {}".format(shader_stats.get("nodes", 0), shader_stats.get("saved_shaders", 0)))
            except Exception:
                pass

        # Refresh only UI state; do not trigger a global scene scan, especially after find-selected.
        self.refresh_group_list()
        keep_label = processed_labels[0] if processed_labels else (labels[0] if labels else self.current_group_label)
        if keep_label in self.cleaner.validated_groups:
            if self._find_selected_filter is not None:
                self._find_selected_filter = set([keep_label])
                self.refresh_group_list()
            self._highlight_group_item(keep_label, frame=True, select=False)

    def do_cancel_process(self):
        stats = self.cleaner.cancel_last_process()
        self.status_label.setText(
            "Cancel latest process complete | restored {} | del inst {} | del masters {}. Use REFRESH CURRENT MODE if you need a rescan.".format(
                stats.get("restored",0), stats.get("deleted_instances",0),
                stats.get("deleted_masters",0)))
        self.refresh_group_list()

    def do_convert_instances(self):
        stats = self.cleaner.convert_instances_to_geometry()
        self.status_label.setText(
            "Converted {} instances to geo. Use REFRESH CURRENT MODE if you need a rescan.".format(stats.get("converted",0)))
        self.refresh_group_list()

    def do_convert_selected_instances(self):
        stats = self.cleaner.convert_selected_instances_to_geometry(_get_selected_transforms())
        self.status_label.setText(
            "Converted {} selected instance(s) to geo. Use REFRESH CURRENT MODE if you need a rescan.".format(stats.get("converted",0)))
        self.refresh_group_list()

    def keyPressEvent(self, event):
        focus = QApplication.focusWidget()

        if event.key() == Qt.Key_Escape and self._is_processing:
            self.do_stop_process()
            event.accept()
            return

        if isinstance(focus, (QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox)):
            super(InstanceCleanerUI, self).keyPressEvent(event)
            return

        k = event.key()
        if k in (Qt.Key_Right, Qt.Key_Down, Qt.Key_S):
            self._navigate_review(1);  event.accept(); return
        if k in (Qt.Key_Left, Qt.Key_Up, Qt.Key_W):
            self._navigate_review(-1); event.accept(); return
        if k in (Qt.Key_Return, Qt.Key_Enter):
            self.do_isolate_current_source(); event.accept(); return
        if k == Qt.Key_A:
            self.do_accept_current_and_next(); event.accept(); return
        if k == Qt.Key_R:
            self.do_reject_current_and_next(); event.accept(); return
        if k == Qt.Key_F:
            self.do_frame_selected_group();    event.accept(); return

        super(InstanceCleanerUI, self).keyPressEvent(event)

    def closeEvent(self, event):
        self._stop_selection_watcher()
        try:
            self.cleaner.exit_isolate()
        except Exception:
            pass
        super(InstanceCleanerUI, self).closeEvent(event)


# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------
_instance_cleaner_ui = None

def launch():
    global _instance_cleaner_ui
    try:
        _instance_cleaner_ui.close()
        _instance_cleaner_ui.deleteLater()
    except Exception:
        pass
    _instance_cleaner_ui = InstanceCleanerUI()
    _instance_cleaner_ui.show()
    return _instance_cleaner_ui


if __name__ == "__main__":
    launch()
