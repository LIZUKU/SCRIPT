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
import time
import traceback
from collections import defaultdict

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
        self.setMinimumWidth(460)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

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
    seen = set()
    out = []
    for item in items or []:
        if not item:
            continue
        item = _long(item)
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _is_referenced(obj):
    try:
        return cmds.referenceQuery(obj, isNodeReferenced=True)
    except Exception:
        return False


def _get_dag_path(node_name):
    sel = om2.MSelectionList()
    sel.add(node_name)
    return sel.getDagPath(0)


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
    """Shift geometry so bbox center sits at origin; then fix normals."""
    center = _object_bbox_center(node)
    if max(abs(center[0]), abs(center[1]), abs(center[2])) < 1e-5:
        return
    _move_vertices_object_space(node, (-center[0], -center[1], -center[2]))
    try:
        shapes = cmds.listRelatives(node, shapes=True, fullPath=True) or []
        for sh in shapes:
            cmds.polySoftEdge(sh, a=180, ch=False)
    except Exception:
        pass


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

            if not include_ic and full_path.startswith("|" + ROOT_GROUP):
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
def _ensure_group(name, parent=None):
    if _exists(name):
        group = cmds.ls(name, long=True)[0]
    else:
        group = cmds.group(em=True, name=name)
        group = cmds.ls(group, long=True)[0]
        try:
            cmds.xform(group, ws=True, t=(0,0,0), ro=(0,0,0), s=(1,1,1))
        except Exception:
            pass
    if parent and _exists(parent):
        parent_long    = cmds.ls(parent, long=True)[0]
        current_parent = cmds.listRelatives(group, parent=True, fullPath=True) or []
        if not current_parent or current_parent[0] != parent_long:
            try:
                group = cmds.parent(group, parent_long, absolute=True)[0]
                group = cmds.ls(group, long=True)[0]
            except Exception:
                pass
    return group


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
    if not (_exists(node) and _exists(parent)):
        return _long(node)
    _unlock_transform_for_edit(node)
    try:
        return cmds.ls(cmds.parent(node, parent, absolute=True)[0], long=True)[0]
    except Exception as e:
        cmds.warning("[IC] Could not move {} under {}: {}".format(_short(node), _short(parent), e))
        return _long(node)


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
                 ATTR_IC_ORIG_VIS, ATTR_IC_ORIG_LAYERS, ATTR_IC_ORIG_MATRIX, ATTR_IC_STATUS):
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

    # FIX : loose_hash est distinct de strict_hash
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

    # FIX : strict_hash et loose_hash calculés séparément (même valeur ici
    # car le mode léger n'a pas d'autres données, mais ce sont bien deux
    # attributs distincts qui ne se référencent pas l'un l'autre).
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


def _compare_mesh_topology(mesh1, mesh2, ignore_scale=False, tolerance=0.01):
    """Compare canonical topology, not just vertex/edge/face counts."""
    h1 = _mesh_canonical_topology_hash(mesh1)
    h2 = _mesh_canonical_topology_hash(mesh2)
    return bool(h1 and h2 and h1 == h2)


def _compare_mesh_geometry(mesh1, mesh2, ignore_scale=False, tolerance=0.01):
    """Compare topology plus strict normalized geometric descriptors.

    Counts/area/volume alone are intentionally insufficient because many
    unrelated meshes can share those values.
    """
    if not _compare_mesh_topology(mesh1, mesh2, ignore_scale=ignore_scale, tolerance=tolerance):
        return False
    s1 = _compute_signature(mesh1, strict_tol=max(min(float(tolerance), 0.02), 0.0005))
    s2 = _compute_signature(mesh2, strict_tol=max(min(float(tolerance), 0.02), 0.0005))
    if not s1 or not s2:
        return False
    tol = max(float(tolerance), 0.0)
    return (_avg_abs_delta(s1.edge_quant, s2.edge_quant) <= max(tol * 0.25, 0.003) and
            _avg_abs_delta(s1.face_quant, s2.face_quant) <= max(tol * 0.25, 0.003) and
            _avg_abs_delta(s1.distance_quant, s2.distance_quant) <= max(tol * 0.20, 0.004) and
            _avg_abs_delta(s1.radial_quant, s2.radial_quant) <= max(tol * 0.20, 0.004))


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
                                    progress_cb=None, cancel_cb=None):
    """Pairwise all-scene grouping using UVOptimizer-like methods/options."""
    groups    = {}
    uniques   = []
    remaining = sorted(list(signatures or []), key=lambda s: (
        s.vertex_count, s.edge_count, s.face_count, _short(s.transform).lower()
    ))
    processed   = set()
    group_index = 0
    total       = max(1, len(remaining))

    for source_index, source in enumerate(remaining):
        if cancel_cb and cancel_cb():
            raise ProcessCanceled()
        if progress_cb:
            pct = int(float(source_index) / float(total) * 100.)
            progress_cb(percent=pct, message="Grouping {}".format(_short(source.transform)),
                        step="Grouping", group="UVOptimizer", mesh=source.transform,
                        current=source_index, total=total)

        if source.transform in processed:
            continue

        matches = [source.transform]
        processed.add(source.transform)

        for candidate in remaining:
            if cancel_cb and cancel_cb():
                raise ProcessCanceled()
            if candidate.transform in processed:
                continue
            if _uvoptimizer_compare_score(source.transform, candidate.transform, method, tolerance, ignore_scale) >= 1.0:
                matches.append(candidate.transform)
                processed.add(candidate.transform)

        if len(matches) > 1:
            # FIX : construction de l'ID du groupe — une seule passe .format()
            hash_part = _hash_blob(matches, tolerance, ignore_scale)[:10]
            iid = "{method}_{idx:03d}_{hash}".format(
                method=method, idx=group_index, hash=hash_part
            )
            groups[iid] = {"meshes": matches, "score": 1.0}
            group_index += 1
        else:
            uniques.extend(matches)

    if progress_cb:
        progress_cb(percent=100, message="Grouping complete", step="Grouping", current=total, total=total)

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
    # Convention row-vector Maya : translation en ligne 3 (index [3, 0:3])
    m[3, 0] = float(offset[0])
    m[3, 1] = float(offset[1])
    m[3, 2] = float(offset[2])
    return m


def _rotation_matrix_np(axis, degrees):
    """Matrice de rotation 4x4 en convention row-vector (Maya)."""
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
    """Generate full 0-360° candidate matrices around the backup center."""
    base = _matrix_flat_to_np(base_matrix)
    if base is None:
        return []
    pivot    = (float(pivot[0]), float(pivot[1]), float(pivot[2]))
    to_pivot = _translation_matrix_np((-pivot[0], -pivot[1], -pivot[2]))
    fr_pivot = _translation_matrix_np(pivot)

    candidates = [base]
    # FIX : déduplication robuste via clé arrondie
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

    # Combinaisons d'angles droits
    for rx in (0, 90, 180, 270):
        for ry in (0, 90, 180, 270):
            for rz in (0, 90, 180, 270):
                # FIX : skip explicite de la rotation identité (rx=ry=rz=0)
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
    """
    FIX : construit la matrice delta 4x4 en convention row-vector Maya.

    Maya stocke les matrices sous forme row-major :
        | R00  R01  R02  0 |
        | R10  R11  R12  0 |
        | R20  R21  R22  0 |
        | Tx   Ty   Tz   1 |

    rotation_col est une matrice 3x3 en convention colonne (Kabsch).
    On la transpose pour obtenir la convention ligne, puis on place la
    translation en ligne 3.
    """
    matrix = np.identity(4, dtype=np.float64)
    # Rotation : transposée de la colonne-convention → ligne-convention
    matrix[:3, :3] = rotation_col.T
    # Translation en ligne 3 (convention row-vector)
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

    # FIX : delta_matrix construit avec la convention row-vector correcte
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
                fuzzy_enabled=True,
                fuzzy_vertex_tol=0,
                fuzzy_size_tol=0.04,
                fuzzy_score_min=0.92,
                progress_cb=None,
                cancel_cb=None):
    """Returns (groups_safe, groups_fuzzy, uniques). Raises ProcessCanceled via cancel_cb."""
    method = (detect_method or "signature").lower()
    if method in ("topology", "geometry", "exact"):
        groups_safe, uniques = find_groups_uvoptimizer_style(
            signatures, method=method, tolerance=compare_tolerance,
            ignore_scale=ignore_scale, progress_cb=progress_cb, cancel_cb=cancel_cb
        )
        return groups_safe, {}, uniques

    groups_safe  = {}
    groups_fuzzy = {}
    uniques      = []
    consumed     = set()

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

        for cluster in clusters:
            if cancel_cb and cancel_cb():
                raise ProcessCanceled()
            cluster_best = max(
                _fuzzy_score(sig, rep, vertex_tol=fuzzy_vertex_tol, size_tol=fuzzy_size_tol)
                for rep in cluster["reps"]
            )
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

    RS = R * scale                    # 3x3 rotation+scale (colonne-convention)
    t  = dst_c - src_c.dot(RS)       # translation row-vector

    # FIX : matrice 4x4 en convention row-vector Maya
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
    """Align an instance to the still-visible original/backup.

    Prefer the original transform + bbox-center offset first.  Because masters
    are recentered for clean storage, this is the closest equivalent to a
    frozen-transform placement and it preserves signed scales / mirrored parent
    transforms that SVD rotation-only alignment can misread as a 180° flip.
    PCA+ICP is intentionally kept as a late fallback: on repeated or symmetric
    meshes its nearest-neighbor score can look good even when the part is spun
    the wrong way around.
    """
    if not (_exists(instance) and _exists(master_transform) and _exists(original_transform)):
        return False, None, False, None

    best_ok = False
    best_err = None
    pca_icp_ok = False
    pca_icp_err = None

    # 1) Deterministic placement: original matrix, then compensate for the
    # master geometry that was centered on its transform.
    _fallback_align(instance, original_transform)
    best_ok, best_err = _verify_instance_ordered_points(
        instance, original_transform, ALIGN_VERIFY_TOL_DEFAULT)
    if best_ok:
        return True, best_err, pca_icp_ok, pca_icp_err

    # If vertex order changed but the visual point cloud matches, keep this
    # safer transform-derived orientation instead of jumping straight to ICP.
    cloud_ok, cloud_err = _verify_instance_matches_original(
        instance, original_transform, ALIGN_VERIFY_TOL_DEFAULT)
    if cloud_ok:
        return True, cloud_err, pca_icp_ok, pca_icp_err

    # 2) Ordered SVD.  This can recover non-centered or edited pivots and now
    # accepts reflection matrices so mirrored duplicates do not get forced into
    # an incorrect pure rotation.
    mat, err = _compute_alignment(master_transform, original_transform)
    if mat:
        tol = ALIGN_ERROR_TOL_SAFE if match_type == MATCH_SAFE else ALIGN_ERROR_TOL_FUZZY
        if err is None or err <= tol:
            _apply_world_matrix(instance, mat)
            best_ok, best_err = _verify_instance_ordered_points(
                instance, original_transform, ALIGN_VERIFY_TOL_DEFAULT)
            if best_ok:
                return True, best_err, pca_icp_ok, pca_icp_err
            cloud_ok, cloud_err = _verify_instance_matches_original(
                instance, original_transform, ALIGN_VERIFY_TOL_DEFAULT)
            if cloud_ok:
                return True, cloud_err, pca_icp_ok, pca_icp_err

    # 3) Fuzzy bbox fit for non-safe matches.
    if match_type != MATCH_SAFE:
        _bbox_fit_align(instance, original_transform)
        cloud_ok, cloud_err = _verify_instance_matches_original(
            instance, original_transform, ALIGN_VERIFY_TOL_DEFAULT)
        if cloud_ok:
            return True, cloud_err, pca_icp_ok, pca_icp_err

    # 4) Last-resort PCA+ICP only.  This is useful when vertex order differs,
    # but no longer overrides the transform-derived orientation above.
    if use_pca_icp_alignment:
        pca_icp_ok, pca_icp_err = _pca_icp_align_instance_to_backup(instance, original_transform)
        if pca_icp_ok:
            best_ok, best_err = _verify_instance_ordered_points(
                instance, original_transform, ALIGN_VERIFY_TOL_DEFAULT)
            if best_ok:
                return True, best_err, pca_icp_ok, pca_icp_err
            cloud_ok, cloud_err = _verify_instance_matches_original(
                instance, original_transform, ALIGN_VERIFY_TOL_DEFAULT)
            if cloud_ok or _pca_icp_score_is_usable(pca_icp_err, original_transform):
                return True, cloud_err if cloud_err is not None else pca_icp_err, pca_icp_ok, pca_icp_err

    return False, best_err, pca_icp_ok, pca_icp_err


# ---------------------------------------------------------------------------
# Master manager
# ---------------------------------------------------------------------------
class MasterManager(object):
    def __init__(self):
        self.masters = {}

    def find_existing_master(self, internal_id):
        if not _exists(MASTERS_GROUP):
            return None
        root = cmds.ls(MASTERS_GROUP, long=True)
        if not root:
            return None
        for mesh in _iter_mesh_transforms(root[0], include_ic=True):
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

            if full_mesh.startswith("|" + ROOT_GROUP):
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
                    cmds.warning("[IC] Alignment verify failed for {} (err: {}).".format(
                        _short(full_mesh), "n/a" if verify_err is None else "{:.5f}".format(verify_err)))
                elif pca_icp_ok and _pca_icp_score_is_usable(pca_icp_err, full_mesh):
                    _add_ic_attr(inst, ATTR_IC_STATUS, "trusted_pca_icp_alignment", "string")
            except Exception as e:
                cmds.warning("[IC] Alignment failed for {}: {}".format(full_mesh, e))
                try:
                    _fallback_align(inst, full_mesh)
                    ok, verify_err = _verify_instance_matches_original(inst, full_mesh, ALIGN_VERIFY_TOL_DEFAULT)
                    if not ok:
                        cmds.warning("[IC] Fallback verify failed for {} (err: {}).".format(
                            _short(full_mesh), "n/a" if verify_err is None else "{:.5f}".format(verify_err)))
                except Exception:
                    pass

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
        if not _exists(ROOT_GROUP):
            return []
        root = cmds.ls(ROOT_GROUP, long=True)
        if not root:
            return []
        return _iter_mesh_transforms(root[0], include_ic=True)

    def _existing_display_names_by_internal_id(self):
        data = {}
        for mesh in self._all_ic_meshes():
            iid  = _get_ic_attr(mesh, ATTR_IC_SOURCE, "")
            name = _get_ic_attr(mesh, ATTR_IC_GROUP_NAME, "")
            if iid and name:
                data[iid] = name
        return data

    def _append_processed_groups(self):
        if not _exists(INSTANCES_GROUP):
            return
        root = cmds.ls(INSTANCES_GROUP, long=True)
        if not root:
            return
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

    # -- Public API --

    def scan(self, root=None, roots=None, selection_only=False,
             strict_tol=0.001,
             detect_method="signature", compare_tolerance=0.30, ignore_scale=True,
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
        uvoptimizer_mode = method in ("topology", "geometry", "exact")

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

        # FIX : grouping_progress adapté pour n'émettre que 2 arguments
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

        def _matches(candidate):
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
            if mesh.startswith("|" + ROOT_GROUP):
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
        if label not in self.validated_groups:
            return []
        meshes = [m for m in self.validated_groups[label]["meshes"] if _exists(m)]
        return _select_nodes(meshes)

    def _find_by_type(self, label, ic_type):
        internal_id = self.validated_groups.get(label, {}).get("internal_id", label)
        roots = []
        if ic_type == "master"   and _exists(MASTERS_GROUP):
            roots = cmds.ls(MASTERS_GROUP,   long=True) or []
        elif ic_type == "instance" and _exists(INSTANCES_GROUP):
            roots = cmds.ls(INSTANCES_GROUP, long=True) or []
        elif ic_type == "backup"   and _exists(BACKUP_GROUP):
            roots = cmds.ls(BACKUP_GROUP,    long=True) or []
        found = []
        for r in roots:
            for mesh in _iter_mesh_transforms(r, include_ic=True):
                if (_get_ic_attr(mesh, ATTR_IC_TYPE,   "") == ic_type and
                        _get_ic_attr(mesh, ATTR_IC_SOURCE, "") == internal_id):
                    found.append(mesh)
        return _dedupe_keep_order(found)

    def select_master(self, label):
        found = self._find_by_type(label, "master")
        if found: cmds.select(found, r=True)
        return found

    def select_instances(self, label):
        found = self._find_by_type(label, "instance")
        if found: cmds.select(found, r=True)
        return found

    def select_backups(self, label):
        found = self._find_by_type(label, "backup")
        if found: cmds.select(found, r=True)
        return found

    def select_all_masters(self):
        masters = []
        if _exists(MASTERS_GROUP):
            root = cmds.ls(MASTERS_GROUP, long=True)[0]
            for mesh in _iter_mesh_transforms(root, include_ic=True):
                if _get_ic_attr(mesh, ATTR_IC_TYPE, "") == "master":
                    masters.append(mesh)
        if masters: cmds.select(masters, r=True)
        return len(masters)

    def get_nodes_for_label(self, label, target="source"):
        if target == "master":
            return self._find_by_type(label, "master")
        if target == "instances":
            return self._find_by_type(label, "instance")
        if target == "backups":
            return self._find_by_type(label, "backup")
        return [m for m in self.validated_groups.get(label, {}).get("meshes", []) if _exists(m)]

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
        # FIX : acc_values initialisé vide ; la cible est incluse via la boucle
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
            "accepted":          None,
            "group_id":          0,
            "processed":         False,
            "internal_id":       new_label,
            "display_name":      dname,
            "score":             float(info.get("score", 0.85) or 0.85),
            "source_match_type": MATCH_FUZZY,
        }
        self._renumber_groups()
        return {"split": len(split_meshes), "new_label": new_label}

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
        if not _exists(MASTERS_GROUP):
            cmds.warning("[IC] No master group.")
            return {"organized": 0}
        root    = cmds.ls(MASTERS_GROUP, long=True)[0]
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

            if _exists(BACKUP_GROUP):
                root    = cmds.ls(BACKUP_GROUP, long=True)[0]
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

            if _exists(MASTERS_GROUP):
                root    = cmds.ls(MASTERS_GROUP, long=True)[0]
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

        if self.last_process_batch == batch_id:
            self.last_process_batch = None

        return {"restored": len(restored),
                "deleted_instances": deleted_instances,
                "deleted_masters": deleted_masters}

    def convert_instances_to_geometry(self):
        if not _exists(INSTANCES_GROUP):
            cmds.warning("[IC] No instance group.")
            return {"converted": 0}

        _, _, inst_root, _, conv_root = _ensure_ic_groups()
        _, li, _, lc                   = _ensure_ic_layers()

        instances = [n for n in _iter_mesh_transforms(inst_root, include_ic=True)
                     if _get_ic_attr(n, ATTR_IC_TYPE, "") == "instance"]
        if not instances:
            cmds.warning("[IC] No instances to convert.")
            return {"converted": 0}

        converted = []
        with UndoChunk("InstanceCleanerConvertInstances"):
            for idx, inst in enumerate(instances):
                if not _exists(inst):
                    continue
                try:
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

        return {"converted": len(converted)}

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
    def __init__(self, text="", tip="", bg="#2d2d2d", fg="#a0a0a0", w=None, h=28, parent=None):
        super(ColorBtn, self).__init__(text, parent)
        if w:
            self.setFixedSize(w, h)
        else:
            self.setFixedHeight(h)
        self.setToolTip(tip)
        hover = QColor(bg).lighter(130).name()
        self.setStyleSheet(
            "QPushButton {{ background:{bg}; color:{fg}; border:1px solid #222;"
            " border-radius:3px; font-weight:bold; font-size:9px; padding:1px 4px; }}"
            "QPushButton:hover {{ background:{hover}; border-color:#444; }}"
            "QPushButton:pressed {{ background:#1a1a1a; }}"
            "QPushButton:disabled {{ background:#242424; color:#555; border-color:#222; }}"
            .format(bg=bg, fg=fg, hover=hover)
        )


class SectionLabel(QLabel):
    def __init__(self, text, parent=None):
        super(SectionLabel, self).__init__(text, parent)
        self.setStyleSheet(
            "color:#555; font-size:9px; font-weight:bold;"
            " padding:4px 0 2px 0; border-bottom:1px solid #2a2a2a;"
        )


class ParamSlider(QWidget):
    if PYSIDE_VERSION == 6:
        valueChanged = Signal(float)
    else:
        valueChanged = QtCore.Signal(float)

    def __init__(self, label, min_val, max_val, default, decimals=3, label_width=90, parent=None):
        super(ParamSlider, self).__init__(parent)
        self._mult = 10 ** decimals

        row = QHBoxLayout(self)
        row.setContentsMargins(0,0,0,0)
        row.setSpacing(4)

        lbl = QLabel(label)
        lbl.setFixedWidth(label_width)
        lbl.setStyleSheet("color:#707070; font-size:10px;")

        self._slider = QSlider(Qt.Horizontal)
        self._slider.setRange(int(min_val*self._mult), int(max_val*self._mult))
        self._slider.setValue(int(default*self._mult))

        self._spin = QDoubleSpinBox()
        self._spin.setRange(min_val, max_val)
        self._spin.setDecimals(decimals)
        self._spin.setValue(default)
        self._spin.setFixedWidth(64)
        self._spin.setButtonSymbols(QAbstractSpinBox.NoButtons)

        row.addWidget(lbl)
        row.addWidget(self._slider)
        row.addWidget(self._spin)

        self._slider.valueChanged.connect(self._on_slider)
        self._spin.valueChanged.connect(self._on_spin)

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
        checked_changed   = Signal(str, bool)
    else:
        accept_clicked    = QtCore.Signal(str)
        reject_clicked    = QtCore.Signal(str)
        select_clicked    = QtCore.Signal(str)
        master_clicked    = QtCore.Signal(str)
        instances_clicked = QtCore.Signal(str)
        backups_clicked   = QtCore.Signal(str)
        checked_changed   = QtCore.Signal(str, bool)

    def __init__(self, label, info, parent=None):
        super(GroupItem, self).__init__(parent)
        self.setObjectName("GroupItemCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.label = label
        self.info  = info
        self._highlighted = False
        self._build()
        self.refresh()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6,5,6,5)
        layout.setSpacing(4)

        header = QHBoxLayout()
        self.group_check = QCheckBox("")
        self.group_check.setToolTip("Check multiple group cards, then use MERGE SEL GROUPS.")
        self.group_check.stateChanged.connect(
            lambda _v: self.checked_changed.emit(self.label, self.group_check.isChecked()))
        self.badge       = QLabel("")
        self.badge.setFixedSize(60, 18)
        self.badge.setAlignment(Qt.AlignCenter)
        self.name_label  = QLabel(self.info.get("display_name", self.label))
        self.name_label.setStyleSheet("color:#e0e0e0; font-size:10px; font-weight:bold;")
        self.count_label = QLabel("{} copies".format(len(self.info["meshes"])))
        self.count_label.setStyleSheet("color:#d0d0d0; font-size:9px;")
        self.score_label = QLabel("")
        self.score_label.setStyleSheet("color:#aaaaaa; font-size:9px;")
        self.score_label.setFixedWidth(48)
        header.addWidget(self.group_check)
        header.addWidget(self.badge)
        header.addWidget(self.name_label)
        header.addStretch()
        header.addWidget(self.score_label)
        header.addWidget(self.count_label)

        actions = QHBoxLayout()
        actions.setSpacing(3)
        self.src_btn       = ColorBtn("SRC", "Select source meshes", "#252525","#909090", 46,21)
        self.master_btn    = ColorBtn("MST", "Select master",        "#253525","#90d090", 46,21)
        self.instances_btn = ColorBtn("INS", "Select instances",     "#252535","#9090d0", 46,21)
        self.backups_btn   = ColorBtn("BKP", "Select backups",       "#352525","#d09090", 46,21)

        # FIX : acc_btn et rej_btn créés inconditionnellement (toujours
        # accessibles via self), puis cachés si le groupe est déjà processed.
        self.acc_btn = ColorBtn("OK",  "Accept group", "#1a3a1a","#60d060", 30,21)
        self.rej_btn = ColorBtn("NO",  "Reject group", "#3a1a1a","#d06060", 30,21)

        self.src_btn.clicked.connect(lambda: self.select_clicked.emit(self.label))
        self.master_btn.clicked.connect(lambda: self.master_clicked.emit(self.label))
        self.instances_btn.clicked.connect(lambda: self.instances_clicked.emit(self.label))
        self.backups_btn.clicked.connect(lambda: self.backups_clicked.emit(self.label))
        self.acc_btn.clicked.connect(lambda: self.accept_clicked.emit(self.label))
        self.rej_btn.clicked.connect(lambda: self.reject_clicked.emit(self.label))

        actions.addWidget(self.src_btn)
        actions.addWidget(self.master_btn)
        actions.addWidget(self.instances_btn)
        actions.addWidget(self.backups_btn)
        actions.addStretch()
        actions.addWidget(self.acc_btn)
        actions.addWidget(self.rej_btn)

        self.status_lbl = QLabel("")
        layout.addLayout(header)
        layout.addLayout(actions)
        layout.addWidget(self.status_lbl)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.select_clicked.emit(self.label)
        super(GroupItem, self).mousePressEvent(event)

    def _set_badge(self, text, bg, fg="#ffffff"):
        self.badge.setText(text)
        self.badge.setStyleSheet(
            "background:{}; color:{}; font-size:8px; font-weight:bold; border-radius:2px;".format(bg, fg)
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
        self.backups_btn.setEnabled(bool(processed))

        # FIX : masquer les boutons accept/reject sur les groupes déjà traités
        self.acc_btn.setVisible(not bool(processed))
        self.rej_btn.setVisible(not bool(processed))

        if processed:
            self._set_badge("DONE", "#2a6f9e")
            self.status_lbl.setText("Processed")
            bg, border, color = "#102638", "#2a6f9e", "#80c0ff"
        elif accepted is False:
            self._set_badge("REJECT", "#7a2424")
            self.status_lbl.setText("Rejected")
            bg, border, color = "#321515", "#7a2424", "#ff8080"
        elif accepted is True and gtype == MATCH_SAFE:
            self._set_badge("SAFE OK", "#1e7a35")
            self.status_lbl.setText("Safe match / accepted")
            bg, border, color = "#102c16", "#1e7a35", "#70ff90"
        elif accepted is True and gtype == MATCH_FUZZY:
            self._set_badge("FUZ OK", "#8a6a00")
            self.status_lbl.setText("Fuzzy match / accepted")
            bg, border, color = "#332800", "#8a6a00", "#ffd060"
        elif gtype == MATCH_SAFE:
            self._set_badge("SAFE", "#1e7a35")
            self.status_lbl.setText("Safe match")
            bg, border, color = "#102c16", "#1e7a35", "#70ff90"
        elif gtype == MATCH_FUZZY:
            self._set_badge("FUZZY", "#a05a00")
            self.status_lbl.setText("Similar shape — review")
            bg, border, color = "#3a2106", "#a05a00", "#ffb060"
        else:
            self._set_badge("WAIT", "#555555")
            self.status_lbl.setText("Waiting")
            bg, border, color = "#202020", "#444444", "#aaaaaa"

        bw     = 2 if self._highlighted else 1
        border = "#d8c85a" if self._highlighted else border
        self.status_lbl.setStyleSheet("color:{}; font-size:8px;".format(color))
        self.setStyleSheet(
            "#GroupItemCard {{ background:{}; border:{}px solid {}; border-radius:4px; }}".format(bg, bw, border)
        )


# ---------------------------------------------------------------------------
# Main UI
# ---------------------------------------------------------------------------
class InstanceCleanerUI(QDialog):
    def __init__(self, parent=maya_main_window()):
        super(InstanceCleanerUI, self).__init__(parent)

        self.cleaner              = InstanceCleaner()
        self.group_items          = {}
        self.visible_group_order  = []
        self.current_group_label  = None
        self._highlighted_label   = None
        self.checked_group_labels = set()
        self._last_selection_key  = ""
        self._is_processing       = False
        self._cancel_requested    = False
        self._compact_state       = None
        self._find_selected_filter = None

        self.setWindowTitle("Instance Cleaner")
        self.setMinimumSize(430, 560)
        self.resize(460, 620)
        self.setWindowFlags(Qt.Window | Qt.WindowCloseButtonHint)

        self._build_ui()
        self._apply_stylesheet()
        self._start_selection_watcher()
        self._update_window_compactness(0, force=True)

    def _apply_stylesheet(self):
        self.setStyleSheet("""
            QDialog { background-color:#1e1e1e; }
            QLabel { color:#707070; font-size:10px; }
            QLineEdit { background:#252525; color:#a0a0a0; border:1px solid #303030;
                        border-radius:3px; padding:4px 8px; font-size:11px; }
            QCheckBox { color:#888888; font-size:11px; }
            QScrollArea { border:none; background:transparent; }
            QScrollBar:vertical { background:#141414; width:14px; border-radius:6px; }
            QScrollBar::handle:vertical { background:#555; border-radius:6px; min-height:34px; }
            QScrollBar::handle:vertical:hover { background:#777; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
            QSlider::groove:horizontal { height:4px; background:#2a2a2a; border-radius:2px; }
            QSlider::handle:horizontal { background:#d32f2f; width:12px; margin:-4px 0; border-radius:6px; }
            QSlider::sub-page:horizontal { background:#d32f2f; border-radius:2px; }
            QSpinBox, QDoubleSpinBox { background:#252525; color:#a0a0a0; border:1px solid #303030;
                                       border-radius:3px; padding:2px; font-size:11px; }
            QComboBox { background:#252525; color:#a0a0a0; border:1px solid #303030;
                        border-radius:3px; padding:4px 8px; font-size:11px; }
            QProgressBar { background:#1a1a1a; border:1px solid #303030; border-radius:3px;
                           text-align:center; color:#707070; font-size:9px; }
            QProgressBar::chunk { background:#d32f2f; border-radius:2px; }
        """)

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(8,8,8,8)
        root.setSpacing(10)

        left_col = QWidget()
        left     = QVBoxLayout(left_col)
        left.setContentsMargins(0,0,0,0)
        left.setSpacing(6)

        right_col = QWidget()
        right_col.setMinimumWidth(520)
        right = QVBoxLayout(right_col)
        right.setContentsMargins(0,0,0,0)
        right.setSpacing(6)

        root.addWidget(left_col, 3)
        root.addWidget(right_col, 2)
        self.left_col  = left_col
        self.right_col = right_col

        # --- SCAN ---
        left.addWidget(SectionLabel("SCAN"))

        self.scan_mode_combo = QComboBox()
        self.scan_mode_combo.addItems(["Scene", "Selected Mesh(es)"])
        self.scan_mode_combo.setCurrentIndex(0)
        self.scan_mode_combo.setToolTip(
            "Scene scans everything. Selected Mesh(es) scans only the selection. "
            "Use FIND SELECTED IN GROUPS to locate the current selection in the scanned groups.")
        left.addLayout(self._row("Source", self.scan_mode_combo))

        self.strict_tol_slider = ParamSlider("Strict tol", 0.0001, 0.02, 0.001, 4, 90)
        left.addWidget(self.strict_tol_slider)

        self.detect_method_combo = QComboBox()
        self.detect_method_combo.addItems([
            "Exact (UVOptimizer)",
            "Geometry (UVOptimizer)",
            "Topology (UVOptimizer)",
            "Signature + Fuzzy (current)",
        ])
        self.detect_method_combo.setCurrentIndex(3)
        self.detect_method_combo.setToolTip(
            "Exact requires identical topology and vertex order, then normalizes translation/rotation "
            "and optionally uniform scale via Ignore scale. Geometry uses strict descriptors, not just counts/area/volume. "
            "Signature + Fuzzy is the default for robust instance review.")
        left.addLayout(self._row("Method", self.detect_method_combo))

        self.ignore_scale_cb = QCheckBox("Ignore scale")
        self.ignore_scale_cb.setChecked(True)
        left.addWidget(self.ignore_scale_cb)

        self.compare_tolerance_spin = QDoubleSpinBox()
        self.compare_tolerance_spin.setRange(0.0001, 1.0)
        self.compare_tolerance_spin.setDecimals(4)
        self.compare_tolerance_spin.setSingleStep(0.001)
        self.compare_tolerance_spin.setValue(0.3000)
        self.compare_tolerance_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        left.addLayout(self._row("Tolerance", self.compare_tolerance_spin))

        self.fuzzy_enabled_cb = QCheckBox("Enable fuzzy detection")
        self.fuzzy_enabled_cb.setChecked(True)
        left.addWidget(self.fuzzy_enabled_cb)

        self.fuzzy_vertex_spin = QSpinBox()
        self.fuzzy_vertex_spin.setRange(0, 50)
        self.fuzzy_vertex_spin.setValue(0)
        self.fuzzy_vertex_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        left.addLayout(self._row("Vert +/-", self.fuzzy_vertex_spin))

        self.fuzzy_size_slider  = ParamSlider("Shape tol",  0.01, 0.30, 0.04, 3, 90)
        self.fuzzy_score_slider = ParamSlider("Min score",  0.50, 0.99, 0.94, 2, 90)
        left.addWidget(self.fuzzy_size_slider)
        left.addWidget(self.fuzzy_score_slider)

        self.min_copies_spin = QSpinBox()
        self.min_copies_spin.setRange(2, 999)
        self.min_copies_spin.setValue(2)
        self.min_copies_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        left.addLayout(self._row("Min copies", self.min_copies_spin))

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(16)
        left.addWidget(self.progress_bar)

        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("color:#505050; font-size:9px;")
        left.addWidget(self.status_label)

        scan_btn = ColorBtn("REFRESH SCENE", "Force Source to Scene and run a full scan", "#1a2a3a","#60a0d0", h=32)
        self._connect_button(scan_btn, "Refresh scene", self.do_refresh_scene)
        left.addWidget(scan_btn)

        scan_current_btn = ColorBtn("REFRESH CURRENT MODE", "Scan using the current Source combo (Scene or Selected Mesh(es))", "#1a2f3a","#70b0d0", h=28)
        self._connect_button(scan_current_btn, "Refresh current mode", self.do_refresh_current)
        left.addWidget(scan_current_btn)

        find_btn = ColorBtn("FIND SELECTED IN GROUPS", "Fast-find meshes matching the current selection", "#2a243a","#c0a0ff", h=28)
        self._connect_button(find_btn, "Find selected", self.do_find_selected)
        left.addWidget(find_btn)

        show_all_btn = ColorBtn("SHOW ALL GROUPS", "Clear selection-find filter and show all scanned groups", "#24302a","#90d0a0", h=26)
        self._connect_button(show_all_btn, "Show all groups", self.do_show_all_groups)
        left.addWidget(show_all_btn)

        # --- GROUPS ---
        left.addWidget(SectionLabel("GROUPS"))

        bulk = QHBoxLayout()
        acc_safe_btn = ColorBtn("ACCEPT SAFE", "Accept only safe groups", "#1a3a1a","#60d060", h=24)
        acc_all_btn  = ColorBtn("ACCEPT ALL",  "Accept safe + fuzzy",     "#3a3510","#e0d060", h=24)
        rej_all_btn  = ColorBtn("REJECT ALL",  "",                         "#3a1a1a","#d06060", h=24)
        self._connect_button(acc_safe_btn, "Accept safe", self.do_accept_safe)
        self._connect_button(acc_all_btn, "Accept all", self.do_accept_all)
        self._connect_button(rej_all_btn, "Reject all", self.do_reject_all)
        bulk.addWidget(acc_safe_btn)
        bulk.addWidget(acc_all_btn)
        bulk.addWidget(rej_all_btn)
        left.addLayout(bulk)

        manual_row = QHBoxLayout()
        merge_btn = ColorBtn("MERGE SEL GROUPS", "Merge groups from selection", "#2f2b12","#e0d070", h=24)
        split_btn = ColorBtn("SPLIT SEL OUT",    "Split selected out of group",  "#2a223a","#c0a0ff", h=24)
        self._connect_button(merge_btn, "Merge selected groups", self.do_merge_selected_groups)
        self._connect_button(split_btn, "Split selected out", self.do_split_selected_from_group)
        manual_row.addWidget(merge_btn)
        manual_row.addWidget(split_btn)
        left.addLayout(manual_row)

        master_row = QHBoxLayout()
        sel_mst_btn = ColorBtn("SELECT ALL MASTERS", "", "#253525","#90d090", h=24)
        org_mst_btn = ColorBtn("ORGANIZE MASTERS",   "", "#2a2a3a","#a0c0ff", h=24)
        self._connect_button(sel_mst_btn, "Select all masters", self.do_select_all_masters)
        self._connect_button(org_mst_btn, "Organize masters", self.do_organize_masters)
        master_row.addWidget(sel_mst_btn)
        master_row.addWidget(org_mst_btn)
        left.addLayout(master_row)

        set_mst_btn = ColorBtn(
            "SET SEL MASTER",
            "Use selected mesh as the master/reference for its group",
            "#203a2a","#80e0a0", h=24)
        self._connect_button(set_mst_btn, "Set selected as master", self.do_set_selected_as_master)
        left.addWidget(set_mst_btn)

        # --- PROCESS ---
        left.addWidget(SectionLabel("PROCESS"))

        self.master_spacing_spin = QDoubleSpinBox()
        self.master_spacing_spin.setRange(0, 5000)
        self.master_spacing_spin.setValue(20)
        self.master_spacing_spin.setDecimals(0)
        self.master_spacing_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        left.addLayout(self._row("Spacing", self.master_spacing_spin))

        self.pca_icp_align_cb = QCheckBox("PCA+ICP match to backup")
        self.pca_icp_align_cb.setChecked(True)
        self.pca_icp_align_cb.setToolTip(
            "Default: use the PCA candidate + ICP algorithm to align each new "
            "instance to the still-visible original/backup before moving it to BACKUPS.")
        left.addWidget(self.pca_icp_align_cb)

        proc_row = QHBoxLayout()
        self.process_btn = ColorBtn(
            "PROCESS",
            "One process action: processes the find-selected/current context when active, otherwise accepted groups",
            "#1e3a1a", "#80e060", h=34)
        self.cancel_btn       = ColorBtn("CANCEL PROCESS", "Restore before latest batch", "#3a2a1a", "#e0a060", h=34)
        self.stop_process_btn = ColorBtn("STOP",           "Stop current operation safely", "#4a1515", "#ff7070", h=34)
        self.stop_process_btn.setEnabled(False)
        self._connect_button(self.process_btn, "Process", self.do_process)
        self._connect_button(self.cancel_btn, "Cancel process", self.do_cancel_process)
        self._connect_button(self.stop_process_btn, "Stop process", self.do_stop_process)
        proc_row.addWidget(self.process_btn, 2)
        proc_row.addWidget(self.cancel_btn, 1)
        proc_row.addWidget(self.stop_process_btn, 1)
        left.addLayout(proc_row)

        conv_btn = ColorBtn("CONVERT INSTANCES TO GEO", "", "#3a1e3a","#e080e0", h=34)
        self._connect_button(conv_btn, "Convert instances to geo", self.do_convert_instances)
        left.addWidget(conv_btn)
        left.addStretch()

        # --- RIGHT: group list ---
        right.addWidget(SectionLabel("GROUP LIST / FAST REVIEW"))

        self.groups_count_label = QLabel(
            "Visible 0 / 0 | Safe 0 | Fuzzy 0 | Accepted 0 | Done 0 | Unique 0")
        self.groups_count_label.setStyleSheet("color:#707070; font-size:9px;")
        right.addWidget(self.groups_count_label)

        rev_row = QHBoxLayout()
        self.prev_btn       = ColorBtn("◀", "Previous group",               "#222a35","#a0c0ff", 36, 26)
        self.review_src_btn = ColorBtn("SRC ISOLATE + FRAME", "Isolate + frame current group", "#1f2c3a","#80c0ff", h=26)
        self.next_btn       = ColorBtn("▶", "Next group",                   "#222a35","#a0c0ff", 36, 26)
        self._connect_button(self.prev_btn, "Previous group", lambda: self._navigate_review(-1))
        self._connect_button(self.next_btn, "Next group", lambda: self._navigate_review(1))
        self._connect_button(self.review_src_btn, "Isolate current source", self.do_isolate_current_source)
        rev_row.addWidget(self.prev_btn)
        rev_row.addWidget(self.review_src_btn)
        rev_row.addWidget(self.next_btn)
        right.addLayout(rev_row)

        rev_row2 = QHBoxLayout()
        self.accept_next_btn = ColorBtn("OK + NEXT", "Accept then next",          "#1a3a1a","#70e070", h=26)
        self.reject_next_btn = ColorBtn("NO + NEXT", "Reject then next",          "#3a1a1a","#e07070", h=26)
        self.exit_iso_btn    = ColorBtn("EXIT ISO",  "Exit isolate all panels",   "#303030","#b0b0b0", h=26)
        self._connect_button(self.accept_next_btn, "Accept and next", self.do_accept_current_and_next)
        self._connect_button(self.reject_next_btn, "Reject and next", self.do_reject_current_and_next)
        self._connect_button(self.exit_iso_btn, "Exit isolate", self.do_exit_isolate)
        rev_row2.addWidget(self.accept_next_btn)
        rev_row2.addWidget(self.reject_next_btn)
        rev_row2.addWidget(self.exit_iso_btn)
        right.addLayout(rev_row2)

        filter_row = QHBoxLayout()
        fl = QLabel("Filter"); fl.setFixedWidth(50)
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All","Safe","Fuzzy","Accepted","Rejected","Processed"])
        self.filter_combo.currentIndexChanged.connect(lambda _idx: self._run_ui_action("Filter groups", self.refresh_group_list))
        filter_row.addWidget(fl); filter_row.addWidget(self.filter_combo)
        right.addLayout(filter_row)

        sort_row = QHBoxLayout()
        sl = QLabel("Sort"); sl.setFixedWidth(50)
        self.sort_combo = QComboBox()
        self.sort_combo.addItems([
            "Copies high","Copies low","Score high","Score low",
            "Name A-Z","Name Z-A","Type","Accepted first","Fuzzy first",
        ])
        self.sort_combo.currentIndexChanged.connect(lambda _idx: self._run_ui_action("Sort groups", self.refresh_group_list))
        sort_row.addWidget(sl); sort_row.addWidget(self.sort_combo)
        right.addLayout(sort_row)

        search_row = QHBoxLayout()
        sel = QLabel("Search"); sel.setFixedWidth(50)
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
            QScrollArea { background-color:#1e1e1e; border:none; }
            QScrollArea > QWidget > QWidget { background-color:#1e1e1e; }
        """)
        self.groups_scroll.viewport().setStyleSheet("background-color:#1e1e1e;")

        self.groups_container = QWidget()
        self.groups_container.setStyleSheet("background-color:#1e1e1e;")
        self.groups_layout = QVBoxLayout(self.groups_container)
        self.groups_layout.setContentsMargins(0,0,0,0)
        self.groups_layout.setSpacing(4)

        self.groups_empty = QLabel("No groups found.\nTry REFRESH SCENE, Signature + Fuzzy, or lower Min copies.")
        self.groups_empty.setAlignment(Qt.AlignCenter)
        self.groups_empty.setStyleSheet("color:#606060; font-size:10px;")
        self.groups_layout.addWidget(self.groups_empty)
        self.groups_layout.addStretch()

        self.groups_scroll.setWidget(self.groups_container)
        right.addWidget(self.groups_scroll)

    def _connect_button(self, button, action_name, callback):
        button.clicked.connect(lambda _checked=False: self._run_ui_action(action_name, callback))

    def _run_ui_action(self, action_name, callback, *args, **kwargs):
        if self._is_processing and action_name != "Stop process":
            self.status_label.setText("{} ignored: processing is running".format(action_name))
            return None
        try:
            self.status_label.setText("{}...".format(action_name))
            QApplication.processEvents()
            return callback(*args, **kwargs)
        except Exception as e:
            message = "{} failed: {}".format(action_name, e)
            self.status_label.setText(message)
            self.progress_bar.setValue(0)
            try:
                cmds.warning("[IC] {}\n{}".format(message, traceback.format_exc()))
            except Exception:
                print("[IC] {}\n{}".format(message, traceback.format_exc()))
            return None

    def _row(self, label_text, widget, label_width=90):
        row = QHBoxLayout()
        row.setSpacing(4)
        lbl = QLabel(label_text)
        lbl.setFixedWidth(label_width)
        row.addWidget(lbl)
        row.addWidget(widget)
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
        self.groups_count_label.setText(
            "Visible {} / {} | Safe {} | Fuzzy {} | Accepted {} | Done {} | Unique {}".format(
                len(self.visible_group_order), len(self.cleaner.validated_groups),
                report.get("safe_groups",0),  report.get("fuzzy_groups",0),
                report.get("accepted_groups",0), report.get("processed_groups",0),
                report.get("unique_meshes",0),
            )
        )

    # -- Actions --

    def do_frame_selected_group(self):
        selected = _get_selected_transforms()
        labels = self.cleaner.find_labels_for_nodes(selected, allow_compute=True) if selected else []
        label = labels[0] if labels else None
        if not label:
            self.status_label.setText("Selection not found in current scan")
            return
        if label not in self.group_items:
            self.filter_combo.setCurrentText("All")
            self.search_edit.clear()
            self.refresh_group_list()
        if label in self.group_items:
            self._highlight_group_item(label, frame=True, select=False)
        else:
            self.status_label.setText("Group found but hidden by current filter")

    def _get_detect_method(self):
        txt = self.detect_method_combo.currentText().lower()
        if txt.startswith("topology"):  return "topology"
        if txt.startswith("geometry"):  return "geometry"
        if txt.startswith("exact"):     return "exact"
        return "signature"

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

        def local_progress(*args, **kwargs):
            progress_cb(*args, **kwargs)
            percent = kwargs.get("percent", args[0] if len(args) == 2 else None)
            if percent is None and "current" in kwargs:
                percent = int(float(kwargs.get("current", 0)) / float(max(1, kwargs.get("total", 1))) * 100.0)
            self.progress_bar.setValue(max(0, min(100, int(percent or 0))))
            self.status_label.setText(_short(str(kwargs.get("message", args[1] if len(args) > 1 else "Scanning"))))
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
                compare_tolerance=self.compare_tolerance_spin.value(),
                ignore_scale=self.ignore_scale_cb.isChecked(),
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
                tolerance=self.compare_tolerance_spin.value(),
                ignore_scale=self.ignore_scale_cb.isChecked(),
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
            w = GroupItem(label, info)
            w.accept_clicked.connect(lambda lbl, self=self: self._run_ui_action("Accept group", self.on_accept_group, lbl))
            w.reject_clicked.connect(lambda lbl, self=self: self._run_ui_action("Reject group", self.on_reject_group, lbl))
            w.select_clicked.connect(lambda lbl, self=self: self._run_ui_action("Select group", self.on_select_group, lbl))
            w.master_clicked.connect(lambda lbl, self=self: self._run_ui_action("Select master", self.on_select_master, lbl))
            w.instances_clicked.connect(lambda lbl, self=self: self._run_ui_action("Select instances", self.on_select_instances, lbl))
            w.backups_clicked.connect(lambda lbl, self=self: self._run_ui_action("Select backups", self.on_select_backups, lbl))
            w.checked_changed.connect(lambda lbl, checked, self=self: self._run_ui_action("Check group", self._on_group_checked_changed, lbl, checked))
            w.set_checked(label in self.checked_group_labels)
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

        report = self.cleaner.get_report()
        self.groups_count_label.setText(
            "Visible {} / {} | Safe {} | Fuzzy {} | Accepted {} | Done {} | Unique {}".format(
                len(filtered), len(all_items),
                report.get("safe_groups",0),   report.get("fuzzy_groups",0),
                report.get("accepted_groups",0), report.get("processed_groups",0),
                report.get("unique_meshes",0),
            )
        )
        self._update_window_compactness(len(all_items))

    def _update_window_compactness(self, total_count, force=False):
        compact = total_count == 0
        if not force and self._compact_state == compact:
            return
        self._compact_state = compact

        self.right_col.setVisible(not compact)
        if compact:
            self.right_col.setMinimumWidth(0)
            self.setMinimumSize(430, 560)
            self.resize(460, 620)
        else:
            self.right_col.setMinimumWidth(520)
            self.setMinimumSize(980, 560)
            if force or self.width() < 980:
                self.resize(1120, 620)

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
        nodes = self.cleaner.get_nodes_for_label(label, target=target)
        if not nodes:
            self.status_label.setText("No {} for group".format(target))
            return []
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

    def on_select_backups(self, label):
        nodes = self.cleaner.select_backups(label)
        self._highlight_group_item(label)
        if nodes:
            _frame_selected()

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
        label = self.current_group_label or self._find_group_from_selection()
        if not label:
            self.status_label.setText("Split: highlight a group first.")
            return
        stats = self.cleaner.split_selected_from_group(label, _get_selected_transforms())
        if not stats.get("split"):
            self.status_label.setText("Split: select part of the group source meshes.")
            return
        self.refresh_group_list()
        nl = stats.get("new_label")
        if nl:
            self._highlight_group_item(nl, frame=True, select=True)
        self.status_label.setText("Split {} meshes into new group".format(stats.get("split",0)))

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

    def do_process_current(self):
        return self.do_process(force_current=True)

    def do_process(self, force_current=False):
        labels = self._labels_for_current_process_context(force_current=force_current)
        contextual = labels is not None
        if contextual and not labels:
            self.status_label.setText("Process current: no current/filtered group to process.")
            return

        if contextual:
            accepted_other = [l for l, info in self.cleaner.validated_groups.items()
                              if info.get("accepted") is True and not info.get("processed") and l not in labels]
            if accepted_other and not force_current:
                reply = QMessageBox.question(
                    self, "Process filtered group?",
                    "Find-selected context is active. Process only the filtered/current group now?\n\n"
                    "Choose No to process all accepted groups instead ({} other accepted group(s)).".format(len(accepted_other)),
                    QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                    QMessageBox.Yes)
                if reply == QMessageBox.Cancel:
                    self.status_label.setText("Process canceled before start.")
                    return
                if reply == QMessageBox.No:
                    labels = None
                    contextual = False
        elif self.current_group_label and force_current:
            labels = [self.current_group_label]
            contextual = True

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
                use_pca_icp_alignment=self.pca_icp_align_cb.isChecked(),
                progress_cb=progress_cb,
                cancel_cb=combined_cancel_cb,
                labels=labels,
            )
        except Exception as e:
            cmds.warning("[IC] Process exception: {}".format(e))
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
