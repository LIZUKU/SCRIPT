# -*- coding: utf-8 -*-
import math
import time
import maya.cmds as cmds
import maya.mel as mel
import maya.api.OpenMaya as om

try:
    from PySide2 import QtWidgets, QtCore, QtGui
    from shiboken2 import wrapInstance
    PYSIDE_VERSION = 2
except ImportError:
    from PySide6 import QtWidgets, QtCore, QtGui
    from shiboken6 import wrapInstance
    PYSIDE_VERSION = 6

import maya.OpenMayaUI as omui


# ============================================================
# CONSTANTS
# ============================================================
WINDOW_OBJECT_NAME = "ProCurve"
WINDOW_TITLE = "PR Curve Tools v2.9"

SEGMENTS_MIN = 0
SEGMENTS_MAX = 20
RADIUS_MIN = 0.0
RADIUS_DEFAULT = 0.3
RADIUS_SOFT_MAX = 3.0
RADIUS_MAX_SPINBOX = 99999

SWEEP_PREVIEW_SHADER = "PR_SweepPreview_Mat"
SWEEP_PREVIEW_SG = "PR_SweepPreview_SG"
SWEEP_PREVIEW_TRANSPARENCY = 0.7
SWEEP_UI_SPECS = {
    "profilePolySides": {"label": "Sides", "min": 3, "max": 64, "default": 16, "decimals": 0},
    "scaleProfileX": {"label": "Scale X", "min": 0.01, "max": 5.0, "default": 1.0, "decimals": 3},
    "rotateProfile": {"label": "Rotate", "min": -180.0, "max": 180.0, "default": 0.0, "decimals": 3},
    "twist": {"label": "Twist", "min": -360.0, "max": 360.0, "default": 0.0, "decimals": 3},
    "taper": {"label": "Taper", "min": -5.0, "max": 5.0, "default": 1.0, "decimals": 3},
    "interpolationPrecision": {"label": "Precision", "min": 1.0, "max": 100.0, "default": 75.0, "decimals": 3},
    "interpolationMode": {"label": "Interp Mode", "min": 0, "max": 3, "default": 0, "decimals": 0},
    "interpolationOptimize": {"label": "Optimize", "min": 0, "max": 1, "default": 0, "decimals": 0},
}
SWEEP_DEFAULT_SETTINGS = {
    attr: spec["default"] for attr, spec in SWEEP_UI_SPECS.items()
}

AUTO_CURVE_LENGTH_DEFAULT = 15.0


# ============================================================
# MAYA VERSION DETECTION
# ============================================================
def _get_maya_version():
    try:
        return int(cmds.about(version=True)[:4])
    except Exception:
        return 2024


MAYA_VERSION = _get_maya_version()

LIVE_CHAMFER_TIMER_MS = 140 if MAYA_VERSION <= 2022 else 80
DRAG_MAX_FPS = 15.0 if MAYA_VERSION <= 2022 else 30.0


# ============================================================
# SESSION STATE
# ============================================================
_chamfer_backup = None
_chamfer_active = False
_chamfer_curves = []

_chamfer_drag_ctx = "prChamferDragCtx"
_chamfer_prev_ctx = None
_chamfer_drag_anchor = None
_chamfer_drag_start_radius = 0.3
_chamfer_drag_start_segments = 3
_chamfer_drag_job = None
_last_drag_update_time = 0.0

_sweep_preview_meshes = []
_sweep_preview_active = False
_sweep_preview_curves = []
_sweep_refresh_in_progress = False
_sweep_preview_settings = dict(SWEEP_DEFAULT_SETTINGS)

_sweep_original_select_mode = None
_sweep_original_select_type = {}
_sweep_original_wos = {}
_sweep_wos_changed_panels = set()
_sweep_original_cv_size = None
_sweep_original_ep_size = None

_always_on_top_enabled = True
_always_on_top_draw_job = None
SWEEP_MODE_ITEMS = ["Precision", "Start to End", "EP to EP", "Distance"]


# ============================================================
# HELPERS
# ============================================================
def get_maya_main_window():
    try:
        ptr = omui.MQtUtil.mainWindow()
        if ptr is not None:
            return wrapInstance(int(ptr), QtWidgets.QWidget)
    except Exception:
        pass
    return None


def _delete_existing_pr_curve_tools_windows():
    try:
        app = QtWidgets.QApplication.instance()
        if not app:
            return
        for w in app.allWidgets():
            try:
                if w.objectName() == WINDOW_OBJECT_NAME:
                    try:
                        w.setParent(None)
                    except Exception:
                        pass
                    try:
                        w.close()
                    except Exception:
                        pass
                    try:
                        w.deleteLater()
                    except Exception:
                        pass
            except Exception:
                pass
        try:
            app.processEvents()
        except Exception:
            pass
    except Exception:
        pass


def vector_length(vec):
    return math.sqrt(sum(v ** 2 for v in vec))


def normalize_vector(vec):
    length = vector_length(vec)
    if length > 1e-5:
        return [v / length for v in vec]
    return [0.0, 1.0, 0.0]


def _dist3(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def _safe_obj_exists(obj):
    if not obj:
        return False
    try:
        return cmds.objExists(obj)
    except Exception:
        return False


def _safe_delete(obj):
    if not obj:
        return
    try:
        if cmds.objExists(obj):
            cmds.delete(obj)
    except Exception:
        pass


def _get_vertex_normal_world(vertex_component):
    try:
        sel = om.MSelectionList()
        sel.add(vertex_component)
        dag_path, component = sel.getComponent(0)
        if dag_path.apiType() != om.MFn.kMesh:
            dag_path.extendToShape()
        mesh_fn = om.MFnMesh(dag_path)
        vert_iter = om.MItMeshVertex(dag_path, component)
        if vert_iter.isDone():
            return [0.0, 1.0, 0.0]
        normal = vert_iter.getNormal(om.MSpace.kWorld)
        vec = [normal.x, normal.y, normal.z]
        return normalize_vector(vec)
    except Exception as e:
        cmds.warning("[PR] Failed to get vertex normal for {}: {}".format(vertex_component, e))
        return [0.0, 1.0, 0.0]


def get_curve_transform(obj):
    if not _safe_obj_exists(obj):
        return None
    try:
        node_type = cmds.nodeType(obj)
        if node_type == "transform":
            shapes = cmds.listRelatives(obj, s=True, type="nurbsCurve", fullPath=True) or []
            if shapes:
                return obj
        elif node_type == "nurbsCurve":
            parents = cmds.listRelatives(obj, parent=True, fullPath=True) or []
            if parents:
                return parents[0]
    except Exception:
        pass
    return None


def add_to_isolate(objects):
    if not objects:
        return
    if isinstance(objects, str):
        objects = [objects]
    try:
        panels = cmds.getPanel(type="modelPanel") or []
        for panel in panels:
            try:
                if cmds.isolateSelect(panel, q=True, state=True):
                    for obj in objects:
                        if _safe_obj_exists(obj):
                            cmds.isolateSelect(panel, addDagObject=obj)
            except Exception:
                pass
    except Exception:
        pass


def _get_isolate_active_panels():
    active = []
    try:
        panels = cmds.getPanel(type="modelPanel") or []
        for p in panels:
            try:
                if cmds.isolateSelect(p, q=True, state=True):
                    active.append(p)
            except Exception:
                pass
    except Exception:
        pass
    return active


def _isolate_off_temporarily(fn):
    panels = _get_isolate_active_panels()
    if not panels:
        return fn()
    before = set(cmds.ls(dag=True, long=True, type="transform") or [])
    for p in panels:
        try:
            cmds.isolateSelect(p, state=False)
        except Exception:
            pass
    result = None
    try:
        result = fn()
    finally:
        after = set(cmds.ls(dag=True, long=True, type="transform") or [])
        created = list(after - before)
        for p in panels:
            try:
                cmds.isolateSelect(p, state=True)
            except Exception:
                pass
        if created:
            add_to_isolate(created)
    return result


def _get_curve_data(crv):
    if not _safe_obj_exists(crv):
        return None
    try:
        shapes = cmds.listRelatives(crv, s=True, type="nurbsCurve", fullPath=True) or []
        if not shapes:
            return None
        shp = shapes[0]
        degree = cmds.getAttr(shp + ".degree")
        form = cmds.getAttr(shp + ".form")
        cvs = cmds.ls(crv + ".cv[*]", fl=True) or []
        positions = [list(cmds.pointPosition(c, w=True)) for c in cvs]
        cyclic = form in (1, 2)
        cleaned = positions[:]
        if cyclic and len(cleaned) > 2 and _dist3(cleaned[0], cleaned[-1]) < 1e-6:
            cleaned = cleaned[:-1]
        return shp, degree, form, positions, cyclic, cleaned
    except Exception:
        return None


# ============================================================
# ALWAYS ON TOP
# ============================================================
def _set_curve_always_on_top(crv, value=None):
    if not crv:
        return
    state = _always_on_top_enabled if value is None else bool(value)
    try:
        shapes = cmds.listRelatives(crv, s=True, type="nurbsCurve", fullPath=True) or []
        for shp in shapes:
            try:
                if cmds.attributeQuery("alwaysDrawOnTop", node=shp, exists=True):
                    cmds.setAttr(shp + ".alwaysDrawOnTop", 1 if state else 0)
            except Exception:
                pass
    except Exception:
        pass


def _apply_always_on_top_to_all_curves(state):
    try:
        all_curves = cmds.ls(type="nurbsCurve", long=True) or []
        for shp in all_curves:
            try:
                if cmds.attributeQuery("alwaysDrawOnTop", node=shp, exists=True):
                    cmds.setAttr(shp + ".alwaysDrawOnTop", 1 if state else 0)
            except Exception:
                pass
    except Exception:
        pass


def set_always_on_top_enabled(state):
    global _always_on_top_enabled
    _always_on_top_enabled = bool(state)
    _apply_always_on_top_to_all_curves(_always_on_top_enabled)
    print("[PR] Always On Top: {}".format("ON" if _always_on_top_enabled else "OFF"))


def _start_draw_on_top_job():
    global _always_on_top_draw_job
    _stop_draw_on_top_job()
    if not _always_on_top_enabled:
        return
    _known_curves = set(cmds.ls(type="nurbsCurve", long=True) or [])

    def _on_scene_changed():
        if not _always_on_top_enabled:
            return
        try:
            current = set(cmds.ls(type="nurbsCurve", long=True) or [])
            new_shapes = current - _known_curves
            for shp in new_shapes:
                try:
                    if cmds.attributeQuery("alwaysDrawOnTop", node=shp, exists=True):
                        cmds.setAttr(shp + ".alwaysDrawOnTop", 1)
                    _known_curves.add(shp)
                except Exception:
                    pass
            for shp in list(_known_curves):
                try:
                    if cmds.objExists(shp):
                        if cmds.attributeQuery("alwaysDrawOnTop", node=shp, exists=True):
                            val = cmds.getAttr(shp + ".alwaysDrawOnTop")
                            if val != 1:
                                cmds.setAttr(shp + ".alwaysDrawOnTop", 1)
                except Exception:
                    pass
        except Exception:
            pass

    try:
        _always_on_top_draw_job = cmds.scriptJob(
            event=["SelectionChanged", _on_scene_changed],
            protected=False
        )
    except Exception:
        pass


def _stop_draw_on_top_job():
    global _always_on_top_draw_job
    if _always_on_top_draw_job:
        try:
            if cmds.scriptJob(exists=_always_on_top_draw_job):
                cmds.scriptJob(kill=_always_on_top_draw_job, force=True)
        except Exception:
            pass
    _always_on_top_draw_job = None


# ============================================================
# CV SIZE
# ============================================================
def _query_cv_ep_size():
    cv = None
    ep = None
    try:
        cv = int(mel.eval("displayPref -q -cvSize;"))
    except Exception:
        pass
    try:
        ep = int(mel.eval("displayPref -q -epSize;"))
    except Exception:
        pass
    return cv, ep


def _set_cv_ep_size(size):
    ok = False
    try:
        mel.eval("displayPref -cvSize {};".format(int(size)))
        ok = True
    except Exception:
        pass
    try:
        mel.eval("displayPref -epSize {};".format(int(size)))
        ok = True
    except Exception:
        pass
    if ok:
        try:
            cmds.refresh(f=True)
        except Exception:
            pass
    return ok


def _set_cv_size_temp(size=10):
    global _sweep_original_cv_size, _sweep_original_ep_size
    try:
        if _sweep_original_cv_size is None or _sweep_original_ep_size is None:
            cv, ep = _query_cv_ep_size()
            _sweep_original_cv_size = cv
            _sweep_original_ep_size = ep
        _set_cv_ep_size(size)
    except Exception:
        pass


def _restore_cv_size():
    global _sweep_original_cv_size, _sweep_original_ep_size
    try:
        if _sweep_original_cv_size is not None:
            _set_cv_ep_size(_sweep_original_cv_size)
    except Exception:
        pass
    _sweep_original_cv_size = None
    _sweep_original_ep_size = None


# ============================================================
# SWEEP SETTINGS
# ============================================================
def _find_sweep_creator_from_mesh(mesh_transform):
    try:
        shapes = cmds.listRelatives(mesh_transform, s=True, type="mesh", fullPath=True) or []
        hist = cmds.listHistory(shapes[0], pruneDagObjects=True) or []
        for n in hist:
            try:
                if cmds.nodeType(n) == "sweepMeshCreator":
                    return n
            except Exception:
                pass
    except Exception:
        pass
    return None


def _capture_sweep_settings_from_current_preview():
    global _sweep_preview_settings
    _sweep_preview_settings = dict(SWEEP_DEFAULT_SETTINGS)
    for mesh in (_sweep_preview_meshes or []):
        if not _safe_obj_exists(mesh):
            continue
        sc = _find_sweep_creator_from_mesh(mesh)
        if not sc:
            continue
        try:
            attrs = cmds.listAttr(sc, k=True) or []
        except Exception:
            attrs = []
        captured = {}
        for a in attrs:
            plug = sc + "." + a
            try:
                if a in ("message",):
                    continue
                if not cmds.getAttr(plug, se=True):
                    continue
                if cmds.listConnections(plug, s=True, d=False):
                    continue
                captured[a] = cmds.getAttr(plug)
            except Exception:
                pass
        if captured:
            merged = dict(SWEEP_DEFAULT_SETTINGS)
            merged.update(captured)
            _sweep_preview_settings = merged
            return
    _sweep_preview_settings = dict(SWEEP_DEFAULT_SETTINGS)


def _apply_sweep_settings_to_meshes(meshes):
    if not _sweep_preview_settings:
        return
    for mesh in meshes or []:
        if not _safe_obj_exists(mesh):
            continue
        sc = _find_sweep_creator_from_mesh(mesh)
        if not sc:
            continue
        for a, val in _sweep_preview_settings.items():
            plug = sc + "." + a
            try:
                if not cmds.objExists(plug):
                    continue
                if not cmds.getAttr(plug, se=True):
                    continue
                if cmds.listConnections(plug, s=True, d=False):
                    continue
                if isinstance(val, (list, tuple)) and len(val) == 1 and isinstance(val[0], (list, tuple)):
                    val = val[0]
                if isinstance(val, (int, float, bool)):
                    cmds.setAttr(plug, val)
                elif isinstance(val, (list, tuple)):
                    if len(val) == 3:
                        cmds.setAttr(plug, val[0], val[1], val[2])
                    elif len(val) == 2:
                        cmds.setAttr(plug, val[0], val[1])
                    elif len(val) == 1:
                        cmds.setAttr(plug, val[0])
            except Exception:
                pass


def _set_sweep_attr_on_creator(sc, attr, val):
    plug = sc + "." + attr
    if not cmds.objExists(plug):
        return False
    try:
        if not cmds.getAttr(plug, se=True):
            return False
        if cmds.listConnections(plug, s=True, d=False):
            return False
        decimals = SWEEP_UI_SPECS.get(attr, {}).get("decimals", 3)
        if decimals == 0:
            cmds.setAttr(plug, int(round(float(val))))
        else:
            cmds.setAttr(plug, float(val))
        return True
    except Exception:
        return False


def set_sweep_preview_setting(attr, val):
    global _sweep_preview_settings
    spec = SWEEP_UI_SPECS.get(attr)
    if not spec:
        return
    min_val = spec["min"]
    max_val = spec["max"]
    decimals = spec.get("decimals", 3)
    clamped = max(min_val, min(max_val, float(val)))
    final_val = int(round(clamped)) if decimals == 0 else float(clamped)
    _sweep_preview_settings[attr] = final_val

    if not _sweep_preview_active:
        return
    for mesh in (_sweep_preview_meshes or []):
        if not _safe_obj_exists(mesh):
            continue
        sc = _find_sweep_creator_from_mesh(mesh)
        if not sc:
            continue
        _set_sweep_attr_on_creator(sc, attr, final_val)
    try:
        cmds.refresh(f=True)
    except Exception:
        pass


# ============================================================
# SWEEP PREVIEW HELPERS
# ============================================================
def _set_wireframe_on_shaded_preview(enabled):
    global _sweep_original_wos, _sweep_wos_changed_panels
    panels = cmds.getPanel(type="modelPanel") or []
    if not panels:
        return
    if enabled:
        _sweep_original_wos = {}
        _sweep_wos_changed_panels = set()
        for p in panels:
            try:
                cur = cmds.modelEditor(p, q=True, wireframeOnShaded=True)
                _sweep_original_wos[p] = cur
                if not cur:
                    cmds.modelEditor(p, e=True, wireframeOnShaded=True)
                    _sweep_wos_changed_panels.add(p)
            except Exception:
                pass
    else:
        for p in list(_sweep_wos_changed_panels):
            try:
                if cmds.modelEditor(p, q=True, exists=True):
                    cmds.modelEditor(p, e=True, wireframeOnShaded=_sweep_original_wos.get(p, False))
            except Exception:
                pass
        _sweep_original_wos = {}
        _sweep_wos_changed_panels = set()


def _set_surface_pick_mask(enabled):
    global _sweep_original_select_mode, _sweep_original_select_type
    try:
        if not enabled:
            try:
                _sweep_original_select_mode = cmds.selectMode(q=True, object=True)
            except Exception:
                _sweep_original_select_mode = None
            _sweep_original_select_type = {
                "polymesh": cmds.selectType(q=True, polymesh=True),
                "nurbsSurface": cmds.selectType(q=True, nurbsSurface=True),
                "subdiv": cmds.selectType(q=True, subdiv=True),
                "nurbsCurve": cmds.selectType(q=True, nurbsCurve=True),
            }
            try:
                cmds.selectMode(object=True)
            except Exception:
                pass
            try:
                mel.eval('setObjectPickMask "Surface" false;')
            except Exception:
                pass
            try:
                mel.eval('setObjectPickMask "Curve" true;')
            except Exception:
                pass
            try:
                cmds.selectType(polymesh=False, nurbsSurface=False, subdiv=False, nurbsCurve=True)
            except Exception:
                pass
        else:
            try:
                mel.eval('setObjectPickMask "Surface" true;')
            except Exception:
                pass
            if _sweep_original_select_type:
                try:
                    cmds.selectType(
                        polymesh=_sweep_original_select_type.get("polymesh", True),
                        nurbsSurface=_sweep_original_select_type.get("nurbsSurface", True),
                        subdiv=_sweep_original_select_type.get("subdiv", True),
                        nurbsCurve=_sweep_original_select_type.get("nurbsCurve", True),
                    )
                except Exception:
                    pass
            if _sweep_original_select_mode is not None:
                try:
                    cmds.selectMode(object=_sweep_original_select_mode)
                except Exception:
                    pass
            _sweep_original_select_type = {}
            _sweep_original_select_mode = None
    except Exception as e:
        cmds.warning("[PR] Failed to set pick mask: {}".format(e))


def _ensure_sweep_preview_shader():
    import colorsys
    try:
        if not cmds.objExists(SWEEP_PREVIEW_SHADER):
            cmds.shadingNode("blinn", asShader=True, name=SWEEP_PREVIEW_SHADER)
        h_deg, s, v = 360.0, 0.538, 0.300
        h = (h_deg % 360.0) / 360.0
        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        cmds.setAttr(SWEEP_PREVIEW_SHADER + ".color", r, g, b, type="double3")
        cmds.setAttr(
            SWEEP_PREVIEW_SHADER + ".transparency",
            SWEEP_PREVIEW_TRANSPARENCY, SWEEP_PREVIEW_TRANSPARENCY, SWEEP_PREVIEW_TRANSPARENCY,
            type="double3"
        )
        if not cmds.objExists(SWEEP_PREVIEW_SG):
            cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=SWEEP_PREVIEW_SG)
        if not cmds.isConnected(SWEEP_PREVIEW_SHADER + ".outColor", SWEEP_PREVIEW_SG + ".surfaceShader"):
            cmds.connectAttr(SWEEP_PREVIEW_SHADER + ".outColor", SWEEP_PREVIEW_SG + ".surfaceShader", f=True)
        return SWEEP_PREVIEW_SG
    except Exception as e:
        cmds.warning("[PR] Failed to create preview shader: {}".format(e))
        return "initialShadingGroup"


def sweep_refresh_preview():
    global _sweep_preview_meshes, _sweep_preview_active, _sweep_preview_curves, _sweep_refresh_in_progress
    if not _sweep_preview_active or _sweep_refresh_in_progress:
        return
    curves = [c for c in (_sweep_preview_curves or []) if _safe_obj_exists(c)]
    if not curves:
        return
    _sweep_refresh_in_progress = True
    try:
        _capture_sweep_settings_from_current_preview()
        for m in list(_sweep_preview_meshes):
            _safe_delete(m)
        _sweep_preview_meshes = []
        before_mesh_shapes = set(cmds.ls(type="mesh", long=True) or [])

        def _do():
            cmds.select(curves, r=True)
            try:
                cmds.sweepMeshFromCurve(oneNodePerCurve=False)
            except Exception:
                mel.eval("sweepMeshFromCurve;")

        _isolate_off_temporarily(_do)
        after_mesh_shapes = set(cmds.ls(type="mesh", long=True) or [])
        new_mesh_shapes = list(after_mesh_shapes - before_mesh_shapes)
        meshes = []
        for shp in new_mesh_shapes:
            parents = cmds.listRelatives(shp, parent=True, fullPath=True) or []
            if parents:
                meshes.append(parents[0])
        meshes = list(dict.fromkeys(meshes))
        if not meshes:
            return
        add_to_isolate(meshes)
        _apply_sweep_settings_to_meshes(meshes)
        preview_sg = _ensure_sweep_preview_shader()
        for mesh in meshes:
            try:
                cmds.sets(mesh, e=True, forceElement=preview_sg)
            except Exception:
                pass
        _sweep_preview_meshes = meshes
        _set_surface_pick_mask(False)
        cmds.select(curves, r=True)
        cmds.refresh(f=True)
    finally:
        _sweep_refresh_in_progress = False


# ============================================================
# REBUILD CURVE
# ============================================================
def rebuild_curve_keep_name(old_name, positions, degree, form):
    global _sweep_preview_curves, _sweep_preview_active
    old_short = old_name.split("|")[-1].split(":")[-1]
    if _safe_obj_exists(old_name):
        _safe_delete(old_name)
    try:
        if form in (1, 2):
            pts = positions[:]
            if len(pts) > 2 and _dist3(pts[0], pts[-1]) < 1e-6:
                pts = pts[:-1]
            if len(pts) < 2:
                return None
            pts = pts + [pts[0]]
            new_curve = cmds.curve(d=degree, p=pts, per=True, k=list(range(len(pts) + degree - 1)))
        else:
            if len(positions) < 2:
                return None
            new_curve = cmds.curve(d=degree, p=positions)
        new_curve = cmds.rename(new_curve, old_short)
        add_to_isolate(new_curve)
        _set_curve_always_on_top(new_curve)
        try:
            if _sweep_preview_active and _sweep_preview_curves:
                updated = []
                replaced = False
                for c in _sweep_preview_curves:
                    c_short = c.split("|")[-1].split(":")[-1]
                    if c == old_name or c_short == old_short:
                        updated.append(new_curve)
                        replaced = True
                    else:
                        updated.append(c)
                if replaced:
                    _sweep_preview_curves = updated
                    cmds.evalDeferred(sweep_refresh_preview)
        except Exception:
            pass
        return new_curve
    except Exception as e:
        cmds.warning("[PR] Failed to rebuild curve: {}".format(e))
        return None


# ============================================================
# AUTO CREATE CURVE FROM VERTEX
# ============================================================
def auto_create_curve_from_vertex(length=AUTO_CURVE_LENGTH_DEFAULT):
    sel = cmds.ls(sl=True, fl=True) or []
    verts = [s for s in sel if ".vtx[" in s]
    if not verts:
        cmds.warning("[PR] Auto Curve : selectionne au moins un vertex.")
        return None
    created = []
    for vert in verts:
        try:
            pos = cmds.pointPosition(vert, w=True)
            start = list(pos)
            normal_dir = _get_vertex_normal_world(vert)
            end = [
                start[0] + normal_dir[0] * length,
                start[1] + normal_dir[1] * length,
                start[2] + normal_dir[2] * length
            ]
            crv = cmds.curve(d=1, p=[start, end], name="pr_auto_curve#")
            cmds.xform(crv, centerPivots=True)
            add_to_isolate(crv)
            _set_curve_always_on_top(crv)
            created.append(crv)
        except Exception as e:
            cmds.warning("[PR] Auto Curve failed for {}: {}".format(vert, e))
    if created:
        cmds.select(created, r=True)
        cmds.inViewMessage(
            amg="<hl>Auto Curve</hl> created from {} vertex(es)".format(len(created)),
            pos="topCenter", fade=True
        )
        print("[PR] Auto Curve: {} curve(s) from {} vertex(es)".format(len(created), len(verts)))
    return created


# ============================================================
# SNAP GRID + DRAW CURVE
# ============================================================
def toggle_snap_grid(enabled):
    try:
        cmds.snapMode(grid=bool(enabled))
        status = "ON" if enabled else "OFF"
        print("[PR] Snap Grid: {}".format(status))
    except Exception as e:
        cmds.warning("[PR] Toggle snap failed: {}".format(e))


def draw_curve(degree=1, snap_grid=False):
    ctx = "pr_curveEPCtx"
    try:
        cmds.snapMode(grid=bool(snap_grid))
        if not cmds.contextInfo(ctx, exists=True):
            cmds.curveEPCtx(ctx)
        cmds.setToolTo(ctx)
        degree = int(max(1, min(3, degree)))
        try:
            cmds.curveEPCtx(ctx, e=True, degree=degree)
        except Exception:
            pass
        cmds.snapMode(grid=bool(snap_grid))
        _start_draw_on_top_job()

        def _on_tool_exit():
            _stop_draw_on_top_job()
            if _always_on_top_enabled:
                try:
                    sel = cmds.ls(sl=True, long=True) or []
                    for s in sel:
                        crv = get_curve_transform(s)
                        if crv:
                            _set_curve_always_on_top(crv)
                except Exception:
                    pass

        cmds.scriptJob(event=["ToolChanged", _on_tool_exit], runOnce=True)
    except Exception as e:
        cmds.warning("[PR] Draw curve failed: {}".format(e))


def stop_draw_tool():
    _stop_draw_on_top_job()
    try:
        mel.eval("setToolTo selectSuperContext")
    except Exception:
        pass


# ============================================================
# CLOSE CURVES
# ============================================================
def close_selected_curves():
    sel = cmds.ls(sl=True, long=True) or []
    curves = list(set(filter(None, [get_curve_transform(s) for s in sel])))
    if not curves:
        cmds.warning("[PR] Select curves to close.")
        return
    count = 0
    for crv in curves:
        try:
            shapes = cmds.listRelatives(crv, s=True, type="nurbsCurve", fullPath=True) or []
            for shp in shapes:
                if cmds.getAttr(shp + ".form") == 0:
                    try:
                        cmds.closeCurve(crv, ch=False, ps=True, rpo=True)
                        _set_curve_always_on_top(crv)
                        count += 1
                    except Exception:
                        pass
                    break
        except Exception:
            pass
    print("[PR] {} curve(s) closed.".format(count))


# ============================================================
# SMART MIRROR
# ============================================================
def _detect_best_mirror_axis(curves):
    try:
        bbox = cmds.exactWorldBoundingBox(curves)
        dx = abs(bbox[3] - bbox[0])
        dy = abs(bbox[4] - bbox[1])
        dz = abs(bbox[5] - bbox[2])
        dims = {'x': dx, 'y': dy, 'z': dz}
        best_axis = max(dims, key=dims.get)
        print("[PR] Auto-detected mirror axis: {} (dim: {:.2f})".format(best_axis.upper(), dims[best_axis]))
        return best_axis
    except Exception:
        return 'x'


def _reverse_points(points):
    pts = [list(p) for p in points]
    pts.reverse()
    return pts


def _best_open_curve_join(orig_pts, mir_pts):
    """
    Trouve la meilleure combinaison de raccord entre la courbe d'origine
    et sa version miroir.

    Cas testés :
      - orig_end   -> mir_start
      - orig_end   -> mir_end
      - orig_start -> mir_start
      - orig_start -> mir_end
    """
    candidates = []

    d1 = _dist3(orig_pts[-1], mir_pts[0])
    candidates.append(("end_start", d1, orig_pts[:], mir_pts[:]))

    d2 = _dist3(orig_pts[-1], mir_pts[-1])
    candidates.append(("end_end", d2, orig_pts[:], _reverse_points(mir_pts)))

    d3 = _dist3(orig_pts[0], mir_pts[0])
    candidates.append(("start_start", d3, _reverse_points(orig_pts), mir_pts[:]))

    d4 = _dist3(orig_pts[0], mir_pts[-1])
    candidates.append(("start_end", d4, _reverse_points(orig_pts), _reverse_points(mir_pts)))

    candidates.sort(key=lambda x: x[1])
    return candidates[0]


def mirror_curve(axis='auto', mode='world', merge_threshold=0.001):
    """
    Mirror intelligent d'une curve.

    - Pour les courbes ouvertes :
      crée une vraie continuité en testant les 4 combinaisons d'extrémités
      puis fusionne en une seule curve.

    - Pour les courbes fermées :
      remplace simplement par la version miroir.
    """
    sel = cmds.ls(sl=True, long=True) or []
    curves = list(set(filter(None, [get_curve_transform(s) for s in sel])))
    if not curves:
        cmds.warning("[PR] Select curves to mirror.")
        return

    if axis == 'auto':
        axis = _detect_best_mirror_axis(curves)

    axis_map = {'x': 0, 'y': 1, 'z': 2}
    axis_idx = axis_map.get(axis.lower(), 0)

    if mode == 'world':
        pivot = 0.0
    elif mode == 'object':
        bbox = cmds.exactWorldBoundingBox(curves[0])
        pivot = (bbox[axis_idx] + bbox[axis_idx + 3]) / 2.0
    elif mode == 'boundingBox':
        bbox = cmds.exactWorldBoundingBox(curves)
        pivot = (bbox[axis_idx] + bbox[axis_idx + 3]) / 2.0
    elif mode == 'grid':
        bbox = cmds.exactWorldBoundingBox(curves)
        center = (bbox[axis_idx] + bbox[axis_idx + 3]) / 2.0
        grid_size = cmds.grid(q=True, spacing=True)
        pivot = round(center / grid_size) * grid_size if grid_size else 0.0
    else:
        pivot = 0.0

    result_curves = []

    for crv in curves:
        try:
            data = _get_curve_data(crv)
            if not data:
                continue

            shp, degree, form, positions, cyclic, cleaned = data

            mirrored_positions = []
            for pos in positions:
                new_pos = list(pos)
                new_pos[axis_idx] = 2.0 * pivot - pos[axis_idx]
                mirrored_positions.append(new_pos)

            orig_short = crv.split("|")[-1].split(":")[-1]

            if cyclic or form in (1, 2):
                _safe_delete(crv)
                new_crv = rebuild_curve_keep_name(orig_short, mirrored_positions, degree, form)
                if new_crv:
                    result_curves.append(new_crv)
                continue

            if len(positions) < 2 or len(mirrored_positions) < 2:
                continue

            join_mode, best_dist, ordered_orig, ordered_mir = _best_open_curve_join(
                positions, mirrored_positions
            )

            if best_dist <= merge_threshold:
                combined = ordered_orig + ordered_mir[1:]
            else:
                combined = ordered_orig + ordered_mir

            _safe_delete(crv)

            new_degree = max(1, min(int(degree), 3))
            new_crv = cmds.curve(d=new_degree, p=combined, name=orig_short)

            add_to_isolate(new_crv)
            _set_curve_always_on_top(new_crv)
            result_curves.append(new_crv)

        except Exception as e:
            cmds.warning("[PR] Mirror failed for {}: {}".format(crv, e))

    if result_curves:
        cmds.select(result_curves, r=True)
        print("[PR] {} curve(s) mirrored + merged along {}.".format(len(result_curves), axis.upper()))
        cmds.inViewMessage(
            amg="<hl>Mirror</hl> done along <hl>{}</hl>".format(axis.upper()),
            pos="topCenter",
            fade=True
        )


# ============================================================
# EDGE TO CURVE
# ============================================================
def edge_to_curve():
    sel = cmds.ls(sl=True, fl=True) or []
    edges = [c for c in sel if ".e[" in c]
    if not edges:
        cmds.warning("[PR] Select edges.")
        return
    cmds.select(edges, r=True)
    try:
        res = cmds.polyToCurve(form=2, degree=1, conformToSmoothMeshPreview=False)
        if res:
            add_to_isolate(res[0])
            _set_curve_always_on_top(res[0])
        cmds.select(res, r=True)
    except Exception as e:
        cmds.warning("[PR] polyToCurve failed: {}".format(e))


# ============================================================
# SPLIT & DETACH WITH CURVE
# ============================================================
def _do_split_with_curve(detach=True):
    sel = cmds.ls(sl=True, long=True) or []
    curves = []
    meshes = []
    for obj in sel:
        xf = get_curve_transform(obj)
        if xf:
            curves.append(xf)
            continue
        node_type = cmds.nodeType(obj)
        if node_type == "transform":
            if cmds.listRelatives(obj, s=True, type="mesh", fullPath=True):
                meshes.append(obj)
        elif node_type == "mesh":
            parents = cmds.listRelatives(obj, parent=True, fullPath=True) or []
            if parents:
                meshes.append(parents[0])

    curves = list(dict.fromkeys(curves))
    meshes = list(dict.fromkeys(meshes))
    label = "Split & Detach" if detach else "Split Only"

    if not curves:
        cmds.warning("[PR] {} : selectionne au moins une curve.".format(label))
        return
    if not meshes:
        cmds.warning("[PR] {} : selectionne au moins un mesh.".format(label))
        return

    all_to_hide = []

    for mesh in meshes:
        mesh_short = mesh.split("|")[-1]
        for crv in curves:
            crv_short = crv.split("|")[-1]
            try:
                curves_before = set(cmds.ls(type="nurbsCurve") or [])
                proj_result = cmds.polyProjectCurve(
                    crv_short, mesh_short,
                    ch=True, pointsOnEdges=False,
                    curveSamples=50, automatic=True, tolerance=0.001
                )
                proj_crv_transform = proj_result[0] if proj_result else None
                if not proj_crv_transform:
                    continue

                proj_shape = None
                shapes = cmds.listRelatives(proj_crv_transform, s=True, fullPath=True) or []
                if shapes:
                    proj_shape = shapes[0].split("|")[-1]

                if not proj_shape:
                    curves_after = set(cmds.ls(type="nurbsCurve") or [])
                    new_shapes = list(curves_after - curves_before)
                    if new_shapes:
                        proj_shape = new_shapes[0]

                if not proj_shape:
                    continue

                split_node = cmds.polySplit(
                    mesh_short,
                    projectedCurve=proj_shape,
                    detachEdges=detach,
                    ch=True
                )
                print("[PR] {} OK : {} sur {}".format(label, crv_short, mesh_short))
                all_to_hide.append(crv)
                if _safe_obj_exists(proj_crv_transform):
                    all_to_hide.append(proj_crv_transform)

            except Exception as e:
                cmds.warning("[PR] {} failed {} / {}: {}".format(label, crv_short, mesh_short, e))

    _hide_in_pr_group(list(dict.fromkeys(all_to_hide)))
    cmds.inViewMessage(amg="<hl>{}</hl> done".format(label), pos="topCenter", fade=True)


def split_detach_with_curve():
    _do_split_with_curve(detach=True)


def split_only_with_curve():
    _do_split_with_curve(detach=False)


def _hide_in_pr_group(nodes):
    if not nodes:
        return
    try:
        grp_name = "_PR_curves_hidden"
        if not cmds.objExists(grp_name):
            cmds.group(em=True, name=grp_name)
        for node in nodes:
            if not _safe_obj_exists(node):
                continue
            try:
                current_parent = (cmds.listRelatives(node, parent=True) or [None])[0]
                if current_parent != grp_name:
                    cmds.parent(node, grp_name)
            except Exception:
                pass
        cmds.setAttr(grp_name + ".visibility", 0)
    except Exception as e:
        cmds.warning("[PR] Hide in group failed: {}".format(e))


# ============================================================
# MERGE CURVE CVs
# ============================================================
_merge_cv_backup = {}
_merge_cv_active = False
_merge_cv_was_cv_mode = False


def _get_curves_for_merge():
    sel = cmds.ls(sl=True, long=True, flatten=True) or []
    curves = set()
    for s in sel:
        if ".cv[" in s:
            crv = s.split(".cv[")[0]
            xf = get_curve_transform(crv)
            if xf:
                curves.add(xf)
            continue
        xf = get_curve_transform(s)
        if xf:
            curves.add(xf)
    return list(curves)


def _is_in_cv_mode():
    try:
        sel = cmds.ls(sl=True, flatten=True) or []
        return any(".cv[" in s for s in sel)
    except Exception:
        return False


def merge_curve_cvs(threshold=0.0, live_update=False):
    global _merge_cv_backup, _merge_cv_active, _merge_cv_was_cv_mode

    was_cv = _is_in_cv_mode()

    if not live_update or not _merge_cv_backup:
        curves = _get_curves_for_merge()
        if not curves:
            cmds.warning("[PR] Merge CVs : selectionne une ou plusieurs curves.")
            return
        _merge_cv_backup = {}
        _merge_cv_was_cv_mode = was_cv
        for crv in curves:
            data = _get_curve_data(crv)
            if data:
                shp, degree, form, positions, cyclic, cleaned = data
                _merge_cv_backup[crv] = {
                    "degree": degree, "form": form,
                    "positions": [list(p) for p in positions],
                    "cyclic": cyclic,
                }
        _merge_cv_active = True

    if not _merge_cv_backup:
        return

    for crv, saved in _merge_cv_backup.items():
        if not _safe_obj_exists(crv):
            continue
        positions = [list(p) for p in saved["positions"]]
        degree = saved["degree"]
        form = saved["form"]

        if threshold <= 0.0:
            rebuild_curve_keep_name(crv, positions, degree, form)
            continue

        merged = [positions[0]]
        for i in range(1, len(positions)):
            if _dist3(positions[i], merged[-1]) <= threshold:
                merged[-1] = [(merged[-1][j] + positions[i][j]) / 2.0 for j in range(3)]
            else:
                merged.append(positions[i])

        min_pts = degree + 1
        if len(merged) < min_pts:
            merged = positions[:min_pts]

        rebuild_curve_keep_name(crv, merged, degree, form)

    cmds.refresh(f=True)

    if _merge_cv_was_cv_mode:
        try:
            for crv in _merge_cv_backup:
                if _safe_obj_exists(crv):
                    cmds.select(crv, r=True)
            mel.eval("SelectCurveCV;")
        except Exception:
            pass


def reset_merge_cv():
    global _merge_cv_backup, _merge_cv_active, _merge_cv_was_cv_mode
    _merge_cv_backup = {}
    _merge_cv_active = False
    _merge_cv_was_cv_mode = False


# ============================================================
# ATTACH CURVES
# ============================================================
def _curve_endpoints_world(crv):
    data = _get_curve_data(crv)
    if not data:
        return False, None, None
    shp, degree, form, positions, cyclic, cleaned = data
    if cyclic or form in (1, 2):
        return True, None, None
    pts = positions
    if not pts or len(pts) < 2:
        return False, None, None
    return False, pts[0], pts[-1]


def _maybe_reverse_curve_for_attach(prev_end_point, next_curve):
    is_closed, sp, ep = _curve_endpoints_world(next_curve)
    if is_closed or sp is None or ep is None or prev_end_point is None:
        return False
    d_start = _dist3(prev_end_point, sp)
    d_end = _dist3(prev_end_point, ep)
    if d_end < d_start:
        try:
            cmds.reverseCurve(next_curve, ch=False, rpo=True)
            return True
        except Exception:
            return False
    return False


def attach_selected_curves(delete_originals=True, method="connect", keep_multiple_knots=False,
                           auto_reverse=True, clean_attach=False):
    sel = cmds.ls(sl=True, long=True) or []
    curves_raw = [get_curve_transform(s) for s in sel]
    curves_raw = [c for c in curves_raw if c]
    seen = set()
    curves = []
    for c in curves_raw:
        if c not in seen:
            curves.append(c)
            seen.add(c)

    if len(curves) < 2:
        cmds.warning("[PR] Select at least 2 curves to Attach.")
        return None

    work_curves = curves
    if not delete_originals:
        dupes = []
        for c in curves:
            try:
                d = cmds.duplicate(c, rr=True)[0]
                dupes.append(d)
            except Exception:
                pass
        if len(dupes) < 2:
            return None
        work_curves = dupes

    if auto_reverse:
        is_closed, sp, ep = _curve_endpoints_world(work_curves[0])
        prev_end = ep if (not is_closed) else None
        for i in range(1, len(work_curves)):
            crv = work_curves[i]
            _maybe_reverse_curve_for_attach(prev_end, crv)
            is_closed_i, sp_i, ep_i = _curve_endpoints_world(crv)
            prev_end = ep_i if (not is_closed_i) else prev_end

    if clean_attach:
        for crv in work_curves:
            try:
                data = _get_curve_data(crv)
                if not data:
                    continue
                shp, degree, form, positions, cyclic, cleaned = data
                if cyclic or len(positions) < 2:
                    continue
                simple_positions = [positions[0], positions[-1]]
                rebuild_curve_keep_name(crv, simple_positions, degree=1, form=0)
            except Exception:
                pass

    shapes = []
    for c in work_curves:
        shps = cmds.listRelatives(c, s=True, type="nurbsCurve", fullPath=True) or []
        if shps:
            shapes.append(shps[0])

    if len(shapes) < 2:
        return None

    method_idx = 0 if method.lower() == "connect" else 1
    replace_original = bool(delete_originals)

    try:
        res = cmds.attachCurve(
            shapes, ch=False, rpo=replace_original,
            method=method_idx, kmk=bool(keep_multiple_knots)
        )
        new_transform = None
        if isinstance(res, (list, tuple)) and res:
            if cmds.nodeType(res[0]) == "transform":
                new_transform = res[0]
            else:
                parents = cmds.listRelatives(res[0], parent=True, fullPath=True) or []
                new_transform = parents[0] if parents else None

        if not new_transform or not cmds.objExists(new_transform):
            new_sel = cmds.ls(sl=True, long=True) or []
            for s in new_sel:
                t = get_curve_transform(s)
                if t:
                    new_transform = t
                    break

        if new_transform:
            add_to_isolate(new_transform)
            _set_curve_always_on_top(new_transform)
            cmds.select(new_transform, r=True)
            msg = "<hl>Curves Attached</hl>"
            if clean_attach:
                msg += " (Clean Mode)"
            cmds.inViewMessage(amg=msg, pos="topCenter", fade=True)
            return new_transform

        return None
    except Exception as e:
        cmds.warning("[PR] Attach failed: {}".format(e))
        return None


# ============================================================
# PRIMITIVES
# ============================================================
def create_primitive_circle():
    try:
        crv = cmds.circle(c=(0, 0, 0), nr=(0, 1, 0), sw=360, r=1, d=3, ut=0, tol=0.01, s=8, ch=1)[0]
        _set_curve_always_on_top(crv)
        cmds.select(crv, r=True)
        try:
            mel.eval("objectMoveCommand;")
        except Exception:
            cmds.setToolTo("moveSuperContext")
    except Exception as e:
        cmds.warning("[PR] Create circle failed: {}".format(e))


def create_primitive_square():
    try:
        points = [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1), (0, 0, 0)]
        sq = cmds.curve(degree=1, p=points, name="square_curve")
        cmds.closeCurve(sq, ch=False, ps=1, rpo=True, bb=0.5, bki=False, p=0.1)
        _set_curve_always_on_top(sq)
        cmds.xform(sq, centerPivots=True)
        cmds.select(sq, r=True)
        try:
            mel.eval("objectMoveCommand;")
        except Exception:
            cmds.setToolTo("moveSuperContext")
    except Exception as e:
        cmds.warning("[PR] Create square failed: {}".format(e))


# ============================================================
# EXTRUDE CV
# ============================================================
def _activate_move_tool_with_snap():
    try:
        mel.eval("setToolTo $gMove;")
        cmds.setToolTo("moveSuperContext")
        cmds.refresh(cv=True)
    except Exception:
        try:
            cmds.setToolTo("Move")
        except Exception:
            pass


def extrude_cv_along_curve(length=0.0):
    sel = cmds.ls(sl=True, fl=True) or []
    cvs = [c for c in sel if ".cv[" in c]
    if not cvs:
        cmds.warning("[PR] Select a CV (start or end).")
        return

    new_cvs = []
    for cv in cvs:
        try:
            crv = cv.split(".cv[")[0]
            idx = int(cv.split("[")[1].split("]")[0])
            data = _get_curve_data(crv)
            if not data:
                continue
            shp, degree, form, positions, cyclic, cleaned = data
            total = len(positions)
            if idx != 0 and idx != total - 1:
                continue

            if idx == 0:
                direction = normalize_vector([positions[0][i] - positions[1][i] for i in range(3)])
                new_p = [positions[0][i] + direction[i] * length for i in range(3)]
                positions.insert(0, new_p)
                new_idx = 0
            else:
                direction = normalize_vector([positions[-1][i] - positions[-2][i] for i in range(3)])
                new_p = [positions[-1][i] + direction[i] * length for i in range(3)]
                positions.append(new_p)
                new_idx = len(positions) - 1

            new_crv = rebuild_curve_keep_name(crv, positions, degree, form)
            if new_crv:
                new_cvs.append("{}.cv[{}]".format(new_crv, new_idx))
        except Exception as e:
            cmds.warning("[PR] Extrude failed: {}".format(e))

    if new_cvs:
        cmds.select(new_cvs, r=True)
        cmds.evalDeferred(_activate_move_tool_with_snap)


# ============================================================
# CHAMFER
# ============================================================
def reset_chamfer():
    global _chamfer_backup, _chamfer_active, _chamfer_curves
    _chamfer_backup = None
    _chamfer_active = False
    _chamfer_curves = []


def is_chamfer_curve_selected():
    global _chamfer_curves
    if not _chamfer_curves:
        return False
    try:
        sel = cmds.ls(sl=True, fl=True) or []
        for s in sel:
            crv = s.split(".")[0] if "." in s else s
            crv = get_curve_transform(crv)
            if crv and crv in _chamfer_curves:
                return True
    except Exception:
        pass
    return False


def _chamfer_build_points(prev_p, curr_p, next_p, segments, radius):
    vec_prev = normalize_vector([prev_p[i] - curr_p[i] for i in range(3)])
    vec_next = normalize_vector([next_p[i] - curr_p[i] for i in range(3)])
    dist_prev = vector_length([prev_p[i] - curr_p[i] for i in range(3)])
    dist_next = vector_length([next_p[i] - curr_p[i] for i in range(3)])
    max_rad = min(dist_prev, dist_next) * 0.95
    actual_rad = min(radius, max_rad) if radius > max_rad else radius
    actual_rad = max(0.0, actual_rad)
    start_p = [curr_p[i] + vec_prev[i] * actual_rad for i in range(3)]
    end_p = [curr_p[i] + vec_next[i] * actual_rad for i in range(3)]
    new_pts = [start_p]
    for s in range(1, segments + 1):
        t = s / float(segments + 1)
        inv = 1.0 - t
        bez = [inv * inv * start_p[j] + 2 * inv * t * curr_p[j] + t * t * end_p[j] for j in range(3)]
        new_pts.append(bez)
    new_pts.append(end_p)
    return new_pts


def chamfer_cv(segments=3, radius=0.3, live_update=False):
    global _chamfer_backup, _chamfer_active, _chamfer_curves
    segments = int(max(0, segments))
    radius = float(max(0.0, radius))

    if not live_update:
        sel = cmds.ls(sl=True, fl=True) or []
        cvs = [c for c in sel if ".cv[" in c]
        if not cvs:
            cmds.warning("[PR] Select CVs for chamfer.")
            return

        by_curve = {}
        for cv in cvs:
            crv = cv.split(".cv[")[0]
            idx = int(cv.split("[")[1].split("]")[0])
            by_curve.setdefault(crv, []).append(idx)

        backup = []
        curves_list = []
        for crv, indices in by_curve.items():
            crv = get_curve_transform(crv) or crv
            data = _get_curve_data(crv)
            if not data:
                continue
            shp, degree, form, positions, cyclic, cleaned = data
            backup.append({
                "name": crv, "degree": degree, "form": form,
                "positions": [list(p) for p in positions],
                "indices": sorted(set(indices)),
            })
            curves_list.append(crv)

        if not backup:
            return

        _chamfer_backup = backup
        _chamfer_active = True
        _chamfer_curves = curves_list
    else:
        if not _chamfer_backup:
            return

    new_selection = []

    for data in _chamfer_backup:
        crv = data["name"]
        indices = data["indices"]
        positions = [list(p) for p in data["positions"]]
        degree = data["degree"]
        form = data["form"]

        cur_data = _get_curve_data(crv)
        if not cur_data:
            continue
        shp, degree, form, _positions_raw, cyclic, cleaned = cur_data

        work_positions = positions[:]
        if cyclic and len(work_positions) > 2 and _dist3(work_positions[0], work_positions[-1]) < 1e-6:
            work_positions = work_positions[:-1]

        if len(work_positions) < 3:
            continue

        offset = 0
        for orig_idx in indices:
            idx = orig_idx + offset
            if idx < 0 or idx >= len(work_positions):
                continue

            if cyclic:
                prev_i = (idx - 1) % len(work_positions)
                next_i = (idx + 1) % len(work_positions)
            else:
                prev_i = idx - 1
                next_i = idx + 1

            if not cyclic and (prev_i < 0 or next_i >= len(work_positions)):
                if idx == 0 and len(work_positions) >= 2:
                    curr_p = work_positions[0]
                    next_p = work_positions[1]
                    prev_p = [curr_p[i] + (curr_p[i] - next_p[i]) for i in range(3)]
                    new_pts = _chamfer_build_points(prev_p, curr_p, next_p, segments, radius)
                elif idx == len(work_positions) - 1 and len(work_positions) >= 2:
                    curr_p = work_positions[-1]
                    prev_p = work_positions[-2]
                    next_p = [curr_p[i] + (curr_p[i] - prev_p[i]) for i in range(3)]
                    new_pts = _chamfer_build_points(prev_p, curr_p, next_p, segments, radius)
                else:
                    continue
            else:
                prev_p = work_positions[prev_i]
                curr_p = work_positions[idx]
                next_p = work_positions[next_i]
                new_pts = _chamfer_build_points(prev_p, curr_p, next_p, segments, radius)

            work_positions = work_positions[:idx] + new_pts + work_positions[idx + 1:]
            for i in range(len(new_pts)):
                new_selection.append("{}.cv[{}]".format(crv, idx + i))
            offset += len(new_pts) - 1

        rebuild_curve_keep_name(crv, work_positions, degree, form)

    if new_selection:
        cmds.select(new_selection, r=True)


def remove_chamfer():
    sel = cmds.ls(sl=True, fl=True) or []
    cvs = [c for c in sel if ".cv[" in c]
    if not cvs:
        cmds.warning("[PR] Select the chamfer CVs.")
        return

    reset_chamfer()

    by_curve = {}
    for cv in cvs:
        crv = cv.split(".cv[")[0]
        idx = int(cv.split("[")[1].split("]")[0])
        by_curve.setdefault(crv, set()).add(idx)

    for crv, idx_set in by_curve.items():
        try:
            crv = get_curve_transform(crv) or crv
            data = _get_curve_data(crv)
            if not data:
                continue
            shp, degree, form, positions_raw, cyclic, positions = data
            positions_full = [list(cmds.pointPosition(c, w=True)) for c in (cmds.ls(crv + ".cv[*]", fl=True) or [])]
            if not positions_full:
                continue

            new_positions = []
            i = 0
            while i < len(positions_full):
                if i in idx_set:
                    start = i
                    while i + 1 < len(positions_full) and (i + 1) in idx_set:
                        i += 1
                    end = i
                    if start == 0 or end == len(positions_full) - 1:
                        for j in range(start, end + 1):
                            new_positions.append(positions_full[j])
                    else:
                        p_prev = positions_full[start - 1]
                        p_next = positions_full[end + 1]
                        corner = [(p_prev[j] + p_next[j]) / 2 for j in range(3)]
                        new_positions.append(corner)
                    i += 1
                else:
                    new_positions.append(positions_full[i])
                    i += 1

            rebuild_curve_keep_name(crv, new_positions, degree, form)
        except Exception as e:
            cmds.warning("[PR] Remove chamfer failed: {}".format(e))


# ============================================================
# EDIT / INSERT CV
# ============================================================
def edit_curve_cvs():
    sel = cmds.ls(sl=True) or []
    curves = list(set(filter(None, [get_curve_transform(s) for s in sel])))
    if not curves:
        cmds.warning("[PR] Select a curve.")
        return
    all_cvs = []
    for crv in curves:
        cvs = cmds.ls(crv + ".cv[*]", fl=True) or []
        all_cvs.extend(cvs)
    if all_cvs:
        cmds.select(all_cvs, r=True)
        cmds.setToolTo("Move")


def insert_cv():
    sel = cmds.ls(sl=True, fl=True) or []
    cvs = [c for c in sel if ".cv[" in c]
    if len(cvs) != 2:
        cmds.warning("[PR] Select exactly 2 CVs.")
        return
    crv1 = cvs[0].split(".cv[")[0]
    crv2 = cvs[1].split(".cv[")[0]
    if crv1 != crv2:
        cmds.warning("[PR] CVs must be on the same curve.")
        return
    idx1 = int(cvs[0].split("[")[1].split("]")[0])
    idx2 = int(cvs[1].split("[")[1].split("]")[0])
    if idx1 > idx2:
        idx1, idx2 = idx2, idx1

    try:
        data = _get_curve_data(crv1)
        if not data:
            return
        shp, degree, form, positions_raw, cyclic, positions = data
        all_cvs = cmds.ls(crv1 + ".cv[*]", fl=True) or []
        positions_full = [cmds.pointPosition(c, w=True) for c in all_cvs]
        p1, p2 = positions_full[idx1], positions_full[idx2]
        new_p = [(p1[i] + p2[i]) / 2 for i in range(3)]
        positions_full.insert(idx1 + 1, new_p)
        new_crv = rebuild_curve_keep_name(crv1, positions_full, degree, form)
        if new_crv:
            cmds.select("{}.cv[{}]".format(new_crv, idx1 + 1), r=True)
            cmds.evalDeferred(_activate_move_tool_with_snap)
    except Exception as e:
        cmds.warning("[PR] Insert failed: {}".format(e))


# ============================================================
# SWEEP MESH
# ============================================================
def sweep_mesh_preview():
    global _sweep_preview_meshes, _sweep_preview_active, _sweep_preview_curves, _sweep_preview_settings
    if _sweep_preview_active:
        sweep_cancel()
    try:
        if not cmds.pluginInfo("sweep", q=True, loaded=True):
            cmds.loadPlugin("sweep")
    except Exception:
        cmds.warning("[PR] Plugin 'sweep' not found.")
        return

    sel = cmds.ls(sl=True, long=True) or []
    curves = list(set(filter(None, [get_curve_transform(s) for s in sel])))
    if not curves:
        cmds.warning("[PR] Select a curve.")
        return

    _sweep_preview_curves = curves[:]
    if not _sweep_preview_settings:
        _sweep_preview_settings = dict(SWEEP_DEFAULT_SETTINGS)
    _set_cv_size_temp(10)
    _set_wireframe_on_shaded_preview(True)

    def _do():
        cmds.select(curves, r=True)
        try:
            return cmds.sweepMeshFromCurve(oneNodePerCurve=False)
        except Exception:
            mel.eval("sweepMeshFromCurve;")
            return None

    before_mesh_shapes = set(cmds.ls(type="mesh", long=True) or [])
    try:
        _isolate_off_temporarily(_do)
        after_mesh_shapes = set(cmds.ls(type="mesh", long=True) or [])
        new_mesh_shapes = list(after_mesh_shapes - before_mesh_shapes)
        meshes = []
        for shp in new_mesh_shapes:
            parents = cmds.listRelatives(shp, parent=True, fullPath=True) or []
            if parents:
                meshes.append(parents[0])
        meshes = list(dict.fromkeys(meshes))
        if not meshes:
            _set_surface_pick_mask(True)
            _set_wireframe_on_shaded_preview(False)
            _restore_cv_size()
            return

        add_to_isolate(meshes)
        _sweep_preview_meshes = meshes
        _apply_sweep_settings_to_meshes(meshes)
        _capture_sweep_settings_from_current_preview()
        preview_sg = _ensure_sweep_preview_shader()
        for mesh in meshes:
            try:
                cmds.sets(mesh, e=True, forceElement=preview_sg)
            except Exception:
                pass

        _sweep_preview_active = True
        _set_surface_pick_mask(False)
        cmds.select(curves, r=True)
        cmds.inViewMessage(
            amg="<hl>Sweep Preview</hl> : Edit curves, then <hl>Bake</hl> or <hl>Cancel</hl>",
            pos="topCenter", fade=True
        )
        return meshes
    except Exception as e:
        cmds.warning("[PR] Sweep failed: {}".format(e))
        _set_surface_pick_mask(True)
        _set_wireframe_on_shaded_preview(False)
        _restore_cv_size()
        return None


def sweep_bake():
    global _sweep_preview_meshes, _sweep_preview_active, _sweep_preview_curves, _sweep_preview_settings
    if not _sweep_preview_active or not _sweep_preview_meshes:
        cmds.warning("[PR] No sweep preview to bake.")
        return
    try:
        for mesh in _sweep_preview_meshes:
            if _safe_obj_exists(mesh):
                try:
                    cmds.sets(mesh, e=True, forceElement="initialShadingGroup")
                except Exception:
                    pass
        _set_surface_pick_mask(True)
        try:
            cmds.selectMode(object=True)
        except Exception:
            pass
        _set_wireframe_on_shaded_preview(False)
        _restore_cv_size()
        valid_meshes = [m for m in _sweep_preview_meshes if _safe_obj_exists(m)]
        if valid_meshes:
            cmds.select(valid_meshes, r=True)
        cmds.inViewMessage(amg="<hl>Sweep Baked</hl>", pos="topCenter", fade=True)
    except Exception as e:
        cmds.warning("[PR] Bake failed: {}".format(e))
    finally:
        _sweep_preview_meshes = []
        _sweep_preview_curves = []
        _sweep_preview_active = False
        for node in (SWEEP_PREVIEW_SG, SWEEP_PREVIEW_SHADER):
            try:
                if cmds.objExists(node):
                    cmds.delete(node)
            except Exception:
                pass


def sweep_cancel():
    global _sweep_preview_meshes, _sweep_preview_active, _sweep_preview_curves, _sweep_preview_settings
    if not _sweep_preview_active:
        return
    try:
        _set_surface_pick_mask(True)
        _set_wireframe_on_shaded_preview(False)
        _restore_cv_size()
        for mesh in _sweep_preview_meshes:
            _safe_delete(mesh)
    except Exception as e:
        cmds.warning("[PR] Cancel failed: {}".format(e))
    finally:
        _sweep_preview_meshes = []
        _sweep_preview_curves = []
        _sweep_preview_active = False


def is_sweep_preview_active():
    return _sweep_preview_active


# ============================================================
# RING FILL
# ============================================================
def get_curve_area(curve):
    bb = cmds.exactWorldBoundingBox(curve)
    dx = bb[3] - bb[0]
    dy = bb[4] - bb[1]
    dz = bb[5] - bb[2]
    return max(dx * dy, dx * dz, dy * dz)


def delete_useless_verts(mesh):
    """Supprime les vertices inutiles (colineaires sur un seul face)."""
    try:
        sl = om.MSelectionList()
        sl.add(mesh)
        dp = sl.getDagPath(0)
        it = om.MItMeshVertex(dp)
        to_delete = []
        while not it.isDone():
            if it.numConnectedFaces() == 1 and it.numConnectedEdges() == 2:
                idx = it.index()
                connected = it.getConnectedVertices()
                if len(connected) == 2:
                    pos = cmds.xform("{}.vtx[{}]".format(mesh, idx), q=True, ws=True, t=True)
                    pos0 = cmds.xform("{}.vtx[{}]".format(mesh, connected[0]), q=True, ws=True, t=True)
                    pos1 = cmds.xform("{}.vtx[{}]".format(mesh, connected[1]), q=True, ws=True, t=True)
                    v1 = [pos[i] - pos0[i] for i in range(3)]
                    v2 = [pos1[i] - pos[i] for i in range(3)]
                    l1 = math.sqrt(sum(x ** 2 for x in v1))
                    l2 = math.sqrt(sum(x ** 2 for x in v2))
                    if l1 > 0 and l2 > 0:
                        v1n = [x / l1 for x in v1]
                        v2n = [x / l2 for x in v2]
                        d = sum(a * b for a, b in zip(v1n, v2n))
                        if abs(d) > 0.999:
                            to_delete.append("{}.vtx[{}]".format(mesh, idx))
            it.next()
        if to_delete:
            cmds.select(to_delete)
            mel.eval('doDelete;')
    except Exception as e:
        cmds.warning("[PR] delete_useless_verts failed: {}".format(e))


def _get_selected_curves_for_ring_fill():
    """Retourne les transforms de curves sélectionnées, même si on sélectionne shapes/CVs."""
    sel = cmds.ls(sl=True, long=True, flatten=True) or []
    curves = []

    for s in sel:
        xf = get_curve_transform(s)
        if xf and xf not in curves:
            curves.append(xf)

    return curves


def _get_transform_from_node(node):
    if not node or not cmds.objExists(node):
        return None
    try:
        if cmds.nodeType(node) == "transform":
            return node
        parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
        return parents[0] if parents else None
    except Exception:
        return None


def _find_surface_transform(nodes):
    for n in nodes or []:
        if not cmds.objExists(n):
            continue
        try:
            if cmds.nodeType(n) == "transform":
                shapes = cmds.listRelatives(n, s=True, fullPath=True) or []
                for s in shapes:
                    nt = cmds.nodeType(s)
                    if nt == "nurbsSurface":
                        return n
            else:
                if cmds.nodeType(n) == "nurbsSurface":
                    tr = _get_transform_from_node(n)
                    if tr:
                        return tr
        except Exception:
            pass
    return None


def _find_mesh_transform_from_nodes(nodes):
    for n in nodes or []:
        if not cmds.objExists(n):
            continue
        try:
            if cmds.nodeType(n) == "transform":
                shapes = cmds.listRelatives(n, s=True, type="mesh", fullPath=True) or []
                if shapes:
                    return n
            elif cmds.nodeType(n) == "mesh":
                tr = _get_transform_from_node(n)
                if tr:
                    return tr
        except Exception:
            pass
    return None


def curve_to_bevel_plus():
    """
    Ring Fill robuste pour Maya récents :
    - ferme les curves si besoin
    - crée une surface plane via planarSrf
    - convertit en polygon via nurbsToPoly

    NOTE :
    On garde le nom de fonction pour ne pas casser le reste du script,
    mais on n'utilise plus bevelPlus car trop instable ici.
    """
    curves = _get_selected_curves_for_ring_fill()
    if not curves:
        cmds.warning("[PR] Ring Fill : aucune curve sélectionnée.")
        return None

    closed_curves = []

    for c in curves:
        try:
            shapes = cmds.listRelatives(c, shapes=True, type="nurbsCurve", fullPath=True) or []
            if not shapes:
                continue

            form = cmds.getAttr(shapes[0] + ".form")

            if form == 0:
                try:
                    res = cmds.closeCurve(c, ch=False, ps=True, rpo=True)
                    if isinstance(res, (list, tuple)) and res:
                        c = res[0]
                except Exception as e:
                    cmds.warning("[PR] closeCurve failed for {}: {}".format(c, e))

            closed_curves.append(c)

        except Exception as e:
            cmds.warning("[PR] Failed reading curve {}: {}".format(c, e))

    if not closed_curves:
        cmds.warning("[PR] Ring Fill : aucune curve fermée valide.")
        return None

    curves_sorted = sorted(closed_curves, key=get_curve_area, reverse=True)

    before_surfaces = set(cmds.ls(type="nurbsSurface", long=True) or [])
    before_meshes = set(cmds.ls(type="mesh", long=True) or [])

    surf_transform = None
    poly_transform = None

    try:
        cmds.select(curves_sorted, r=True)

        surf_res = cmds.planarSrf(curves_sorted, ch=False, tol=0.01)
        if not surf_res:
            cmds.warning("[PR] planarSrf returned nothing.")
            return None

        surf_transform = _find_surface_transform(surf_res)

        if not surf_transform:
            after_surfaces = set(cmds.ls(type="nurbsSurface", long=True) or [])
            new_surfs = list(after_surfaces - before_surfaces)
            for s in new_surfs:
                tr = _get_transform_from_node(s)
                if tr:
                    surf_transform = tr
                    break

        if not surf_transform or not cmds.objExists(surf_transform):
            cmds.warning("[PR] Impossible de récupérer la surface générée.")
            return None

        poly_res = cmds.nurbsToPoly(
            surf_transform,
            mnd=1,
            ch=False,
            f=2,
            pt=1,
            pc=200,
            chr=0.9,
            ft=0.01,
            mel=0.001,
            d=0.1,
            ut=1,
            un=1,
            vt=1,
            vn=1,
            uch=0,
            ucr=0,
            cht=0.2,
            es=0,
            ntr=0,
            mrt=0,
            uss=1
        )

        poly_transform = _find_mesh_transform_from_nodes(poly_res)

        if not poly_transform:
            after_meshes = set(cmds.ls(type="mesh", long=True) or [])
            new_meshes = list(after_meshes - before_meshes)
            for m in new_meshes:
                tr = _get_transform_from_node(m)
                if tr:
                    poly_transform = tr
                    break

        if not poly_transform or not cmds.objExists(poly_transform):
            cmds.warning("[PR] nurbsToPoly n'a pas produit de mesh valide.")
            return None

        try:
            cmds.delete(poly_transform, ch=True)
        except Exception:
            pass

        try:
            cmds.polyMergeVertex(poly_transform, d=0.0001, am=1, ch=False)
        except Exception:
            pass

        try:
            cmds.polyNormal(poly_transform, normalMode=2, userNormalMode=0, ch=False)
        except Exception:
            pass

        try:
            delete_useless_verts(poly_transform)
        except Exception:
            pass

        try:
            if surf_transform and cmds.objExists(surf_transform):
                cmds.delete(surf_transform)
        except Exception:
            pass

        cmds.select(poly_transform, r=True)
        cmds.xform(poly_transform, cpc=True)

        print("[PR] Ring Fill OK : {}".format(poly_transform))
        return poly_transform

    except Exception as e:
        cmds.warning("[PR] Ring Fill failed: {}".format(e))

        try:
            if surf_transform and cmds.objExists(surf_transform):
                cmds.delete(surf_transform)
        except Exception:
            pass

        return None


def ring_fill(delete_curves=False):
    """
    Cree un mesh plat (Ring Fill) depuis les curves selectionnees.
    delete_curves=True : supprime les curves apres creation.
    delete_curves=False : cache les curves dans le groupe _PR_curves_hidden.
    """
    curves = _get_selected_curves_for_ring_fill()
    if not curves:
        cmds.warning("[PR] Ring Fill : selectionne au moins une curve fermee.")
        return

    curves_to_handle = list(curves)

    bevel_mesh = curve_to_bevel_plus()

    if bevel_mesh and curves_to_handle:
        existing = [c for c in curves_to_handle if cmds.objExists(c)]
        if existing:
            if delete_curves:
                try:
                    cmds.delete(existing)
                    print("[PR] Ring Fill : {} curve(s) supprimee(s).".format(len(existing)))
                except Exception as e:
                    cmds.warning("[PR] Delete curves failed: {}".format(e))
            else:
                _hide_in_pr_group(existing)
                print("[PR] Ring Fill : {} curve(s) cachee(s).".format(len(existing)))

    if bevel_mesh:
        cmds.inViewMessage(
            amg="<hl>Ring Fill</hl> done",
            pos="topCenter", fade=True
        )


# ============================================================
# VIEWPORT DRAG TOOL (CHAMFER)
# ============================================================
def _pr_get_ui():
    try:
        for w in QtWidgets.QApplication.allWidgets():
            if w.objectName() == WINDOW_OBJECT_NAME:
                return w
    except Exception:
        pass
    return None


def _pr_set_ui_values(radius, segments):
    ui = _pr_get_ui()
    if not ui:
        return
    radius = float(max(0.0, radius))
    segments = int(max(0, segments))
    try:
        ui.seg_slider.blockSignals(True)
        ui.rad_slider.blockSignals(True)
        ui.seg_spin.blockSignals(True)
        ui.rad_spin.blockSignals(True)

        seg_ratio = min(1.0, segments / float(SEGMENTS_MAX))
        ui.seg_slider.setValue(int(seg_ratio * 1000))
        ui.seg_spin.setValue(segments)

        rad_ratio = min(1.0, radius / RADIUS_SOFT_MAX)
        ui.rad_slider.setValue(int(rad_ratio * 1000))
        ui.rad_spin.setValue(radius)

        ui.seg_spin.blockSignals(False)
        ui.rad_spin.blockSignals(False)
        ui.seg_slider.blockSignals(False)
        ui.rad_slider.blockSignals(False)

        if _chamfer_active and is_chamfer_curve_selected():
            chamfer_cv(segments, radius, True)
        cmds.refresh(f=True)
    except Exception:
        pass


def _stop_chamfer_drag_tool():
    global _chamfer_prev_ctx, _chamfer_drag_anchor, _chamfer_drag_job, _last_drag_update_time
    _chamfer_drag_anchor = None
    _last_drag_update_time = 0.0
    if _chamfer_prev_ctx:
        try:
            if cmds.contextInfo(_chamfer_prev_ctx, exists=True):
                cmds.setToolTo(_chamfer_prev_ctx)
        except Exception:
            pass
    _chamfer_prev_ctx = None
    if _chamfer_drag_job:
        try:
            if cmds.scriptJob(exists=_chamfer_drag_job):
                cmds.scriptJob(kill=_chamfer_drag_job, force=True)
        except Exception:
            pass
    _chamfer_drag_job = None


def _on_tool_changed_kill_if_not_ours():
    try:
        if cmds.currentCtx() != _chamfer_drag_ctx:
            _stop_chamfer_drag_tool()
    except Exception:
        pass


def _pr_drag_press():
    global _chamfer_drag_anchor, _chamfer_drag_start_radius, _chamfer_drag_start_segments, _last_drag_update_time
    try:
        _chamfer_drag_anchor = cmds.draggerContext(_chamfer_drag_ctx, q=True, anchorPoint=True)
        _last_drag_update_time = 0.0
        ui = _pr_get_ui()
        if ui:
            _chamfer_drag_start_radius = ui.rad_spin.value()
            _chamfer_drag_start_segments = int(ui.seg_spin.value())
    except Exception:
        pass


def _pr_drag_move():
    global _chamfer_drag_anchor, _chamfer_drag_start_radius, _chamfer_drag_start_segments
    global _last_drag_update_time
    try:
        if not _chamfer_drag_anchor:
            _chamfer_drag_anchor = cmds.draggerContext(_chamfer_drag_ctx, q=True, anchorPoint=True)
            if not _chamfer_drag_anchor:
                return
        now = time.time()
        if (now - _last_drag_update_time) < (1.0 / float(DRAG_MAX_FPS)):
            return
        _last_drag_update_time = now
        drag_pt = cmds.draggerContext(_chamfer_drag_ctx, q=True, dragPoint=True)
        dx = (drag_pt[0] - _chamfer_drag_anchor[0])
        mods = cmds.draggerContext(_chamfer_drag_ctx, q=True, modifier=True)
        if mods == "shift":
            seg_delta = int(round(dx / 5.0))
            new_segments = max(0, _chamfer_drag_start_segments + seg_delta)
            _pr_set_ui_values(_chamfer_drag_start_radius, new_segments)
        else:
            rad_delta = dx * 0.01
            new_radius = max(0.0, _chamfer_drag_start_radius + rad_delta)
            _pr_set_ui_values(new_radius, _chamfer_drag_start_segments)
    except Exception:
        pass


def _pr_drag_release():
    global _chamfer_drag_anchor
    _chamfer_drag_anchor = None


def _pr_ensure_drag_ctx():
    try:
        if cmds.contextInfo(_chamfer_drag_ctx, exists=True):
            try:
                cmds.deleteUI(_chamfer_drag_ctx)
            except Exception:
                pass
        cmds.draggerContext(
            _chamfer_drag_ctx,
            pressCommand=_pr_drag_press,
            dragCommand=_pr_drag_move,
            releaseCommand=_pr_drag_release,
            cursor="hand"
        )
    except Exception:
        pass


def start_chamfer_viewport_drag(auto=False):
    global _chamfer_prev_ctx, _chamfer_drag_anchor
    global _chamfer_drag_start_radius, _chamfer_drag_start_segments
    global _chamfer_drag_job, _last_drag_update_time

    if not _chamfer_active or not is_chamfer_curve_selected():
        if not auto:
            cmds.warning("[PR] Apply chamfer first.")
        return

    _pr_ensure_drag_ctx()
    _chamfer_prev_ctx = cmds.currentCtx()
    _chamfer_drag_anchor = None
    _last_drag_update_time = 0.0

    ui = _pr_get_ui()
    if ui:
        _chamfer_drag_start_radius = float(ui.rad_spin.value())
        _chamfer_drag_start_segments = int(ui.seg_spin.value())

    cmds.setToolTo(_chamfer_drag_ctx)

    if _chamfer_drag_job:
        try:
            if cmds.scriptJob(exists=_chamfer_drag_job):
                cmds.scriptJob(kill=_chamfer_drag_job, force=True)
        except Exception:
            pass

    _chamfer_drag_job = cmds.scriptJob(
        event=["ToolChanged", _on_tool_changed_kill_if_not_ours],
        protected=True
    )
    cmds.inViewMessage(
        amg="<hl>Chamfer Drag</hl> : drag = radius | <hl>Shift</hl> = segments",
        pos="topCenter", fade=True
    )


# ============================================================
# UI - COLOR BUTTON
# ============================================================
class PRColorBtn(QtWidgets.QPushButton):
    def __init__(self, text="", tip="", bg="#2a2a2a", fg="#909090", w=None, h=26, parent=None):
        super(PRColorBtn, self).__init__(text, parent)
        if w:
            self.setFixedWidth(w)
        self.setFixedHeight(h)
        self.setToolTip(tip)
        self._bg = bg
        self._fg = fg
        self._update_style()

    def _update_style(self):
        lighter = QtGui.QColor(self._bg).lighter(130).name()
        self.setStyleSheet("""
            QPushButton {{
                background-color: {bg}; color: {fg};
                border: 1px solid {border}; border-radius: 3px;
                font-size: 11px; font-weight: bold; padding: 3px 8px;
            }}
            QPushButton:hover {{ background-color: {hover}; border-color: {fg}; }}
            QPushButton:pressed {{ background-color: #111; }}
            QPushButton:disabled {{ background-color: #1a1a1a; color: #333; border-color: #222; }}
        """.format(
            bg=self._bg, fg=self._fg,
            border=QtGui.QColor(self._bg).lighter(150).name(),
            hover=lighter
        ))


# ============================================================
# UI - SECTION LABEL
# ============================================================
class SectionLabel(QtWidgets.QLabel):
    def __init__(self, text, color="#cc4444", parent=None):
        super(SectionLabel, self).__init__(text, parent)
        self.setStyleSheet("""
            QLabel {{
                color: {c}; font-size: 9px; font-weight: bold;
                padding: 5px 0 2px 0;
                border-bottom: 1px solid {border};
                margin-top: 4px;
            }}
        """.format(c=color, border=QtGui.QColor(color).darker(150).name()))


# ============================================================
# MAIN UI
# ============================================================
class PRCurveToolsUI(QtWidgets.QDialog):
    _instance = None

    C_DRAW = "#4a9eff"
    C_CONVERT = "#50c878"
    C_MIRROR = "#c878c8"
    C_CV = "#ffaa40"
    C_CHAMFER = "#cc4444"
    C_MESH = "#40c8c8"

    def __init__(self, parent=get_maya_main_window()):
        super(PRCurveToolsUI, self).__init__(parent)

        self._last_mirror_axis = 'auto'
        self._attach_auto_reverse = True
        self._attach_clean_mode = False
        self._auto_curve_length = AUTO_CURVE_LENGTH_DEFAULT
        self._sweep_controls = {}

        self.setObjectName(WINDOW_OBJECT_NAME)
        self.setWindowTitle(WINDOW_TITLE)
        self.setFixedWidth(310)
        self.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.WindowCloseButtonHint)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)

        self._job = None
        self._rebuild_timer = QtCore.QTimer()
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(LIVE_CHAMFER_TIMER_MS)
        self._rebuild_timer.timeout.connect(self._do_live_chamfer)

        self._build_ui()
        self._apply_global_style()

        try:
            self._job = cmds.scriptJob(event=["SelectionChanged", self._on_sel_changed], parent=WINDOW_OBJECT_NAME)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # BUILD UI
    # ------------------------------------------------------------------
    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)

        layout.addWidget(SectionLabel("  DRAW", self.C_DRAW))

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(5)
        row.addWidget(QtWidgets.QLabel("Degree:"))
        self.degree_spin = QtWidgets.QSpinBox()
        self.degree_spin.setRange(1, 3)
        self.degree_spin.setValue(1)
        self.degree_spin.setFixedWidth(44)
        self.degree_spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        row.addWidget(self.degree_spin)
        self.snap_chk = QtWidgets.QCheckBox("Snap Grid")
        row.addWidget(self.snap_chk)
        row.addStretch()
        layout.addLayout(row)

        row_aot = QtWidgets.QHBoxLayout()
        self.on_top_chk = QtWidgets.QCheckBox("Curves Always On Top")
        self.on_top_chk.setChecked(True)
        row_aot.addWidget(self.on_top_chk)
        row_aot.addStretch()
        layout.addLayout(row_aot)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
        self.circle_btn = PRColorBtn("Circle", bg="#1a2a3a", fg=self.C_DRAW)
        self.square_btn = PRColorBtn("Square", bg="#1a2a3a", fg=self.C_DRAW)
        row.addWidget(self.circle_btn)
        row.addWidget(self.square_btn)
        layout.addLayout(row)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
        self.draw_btn = PRColorBtn("Draw", bg="#1a3060", fg=self.C_DRAW)
        self.stop_btn = PRColorBtn("Stop", bg="#2a2a2a", fg="#707070")
        self.close_btn = PRColorBtn("Close", bg="#1a2a3a", fg=self.C_DRAW)
        row.addWidget(self.draw_btn)
        row.addWidget(self.stop_btn)
        row.addWidget(self.close_btn)
        layout.addLayout(row)

        row_auto = QtWidgets.QHBoxLayout()
        row_auto.setSpacing(4)
        self.auto_curve_btn = PRColorBtn(
            "Auto Curve from Vtx",
            tip="Selectionne un vertex puis cree une courbe selon la normale du vertex",
            bg="#1a3060",
            fg=self.C_DRAW
        )
        self.auto_curve_len_spin = QtWidgets.QDoubleSpinBox()
        self.auto_curve_len_spin.setRange(0.01, 9999)
        self.auto_curve_len_spin.setValue(AUTO_CURVE_LENGTH_DEFAULT)
        self.auto_curve_len_spin.setDecimals(2)
        self.auto_curve_len_spin.setFixedWidth(56)
        self.auto_curve_len_spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.auto_curve_len_spin.setToolTip("Longueur de la courbe")
        row_auto.addWidget(self.auto_curve_btn)
        row_auto.addWidget(self.auto_curve_len_spin)
        layout.addLayout(row_auto)

        layout.addWidget(SectionLabel("  CONVERT", self.C_CONVERT))

        self.edge_btn = PRColorBtn("Edge to Curve", bg="#1a3020", fg=self.C_CONVERT)
        layout.addWidget(self.edge_btn)

        self.attach_btn = PRColorBtn("Attach Curves", tip="Left-click: Connect+Delete | Right-click: Options",
                                     bg="#1a3020", fg=self.C_CONVERT)
        self.attach_btn.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.attach_btn.customContextMenuRequested.connect(self._show_attach_menu)
        layout.addWidget(self.attach_btn)

        layout.addWidget(SectionLabel("  MIRROR", self.C_MIRROR))

        self.mirror_btn = PRColorBtn("Smart Mirror",
                                     tip="Left-click: Auto-detect axis | Right-click: Manual",
                                     bg="#2a1a3a", fg=self.C_MIRROR)
        self.mirror_btn.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.mirror_btn.customContextMenuRequested.connect(self._show_mirror_menu)
        layout.addWidget(self.mirror_btn)

        layout.addWidget(SectionLabel("  CV TOOLS", self.C_CV))

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
        self.extrude_btn = PRColorBtn("Extrude", bg="#2a1e0a", fg=self.C_CV)
        self.edit_btn = PRColorBtn("Edit All", bg="#2a1e0a", fg=self.C_CV)
        self.insert_btn = PRColorBtn("Insert", bg="#2a1e0a", fg=self.C_CV)
        row.addWidget(self.extrude_btn)
        row.addWidget(self.edit_btn)
        row.addWidget(self.insert_btn)
        layout.addLayout(row)

        self.merge_cv_slider, self.merge_cv_spin = self._add_merge_slider(
            layout, "Merge CVs", 0.0, 10.0, 0.0, self.C_CV
        )
        row_merge = QtWidgets.QHBoxLayout()
        row_merge.setSpacing(4)
        self.merge_cv_btn = PRColorBtn("Apply Merge", bg="#2a1e0a", fg=self.C_CV)
        self.reset_merge_cv_btn = PRColorBtn("Reset", bg="#2a2a2a", fg="#707070", w=56)
        row_merge.addWidget(self.merge_cv_btn)
        row_merge.addWidget(self.reset_merge_cv_btn)
        layout.addLayout(row_merge)

        layout.addWidget(SectionLabel("  CHAMFER", self.C_CHAMFER))

        self.seg_slider, self.seg_spin = self._add_slider(
            layout, "Segments", SEGMENTS_MIN, SEGMENTS_MAX, 3, 0, self.C_CHAMFER
        )
        self._add_radius_row(layout)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
        self.chamfer_btn = PRColorBtn("Apply", bg="#3a1010", fg=self.C_CHAMFER)
        self.unchamfer_btn = PRColorBtn("Remove", bg="#2a2a2a", fg="#707070")
        row.addWidget(self.chamfer_btn)
        row.addWidget(self.unchamfer_btn)
        layout.addLayout(row)

        layout.addWidget(SectionLabel("  MESH", self.C_MESH))

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
        self.split_detach_btn = PRColorBtn("Split & Detach",
                                           tip="Curve + Mesh > Project + Split + Detach",
                                           bg="#0f2a2a", fg=self.C_MESH)
        self.split_only_btn = PRColorBtn("Split Only",
                                         tip="Curve + Mesh > Project + Split",
                                         bg="#0f2a2a", fg=self.C_MESH)
        row.addWidget(self.split_detach_btn)
        row.addWidget(self.split_only_btn)
        layout.addLayout(row)

        self.fill_btn = PRColorBtn("Ring Fill",
                                   tip="Left-click: Hide curves | Right-click: Delete curves",
                                   bg="#0f2a2a", fg=self.C_MESH)
        self.fill_btn.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.fill_btn.customContextMenuRequested.connect(self._show_ring_fill_menu)
        layout.addWidget(self.fill_btn)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
        self.sweep_btn = PRColorBtn("Sweep Mesh", bg="#0f2a2a", fg=self.C_MESH)
        self.bake_btn = PRColorBtn("Bake", bg="#0f3010", fg="#50ff50", w=56)
        self.bake_btn.setEnabled(False)
        row.addWidget(self.sweep_btn)
        row.addWidget(self.bake_btn)
        layout.addLayout(row)
        self.sweep_settings_widget = QtWidgets.QWidget()
        self.sweep_settings_layout = QtWidgets.QVBoxLayout(self.sweep_settings_widget)
        self.sweep_settings_layout.setContentsMargins(0, 0, 0, 0)
        self.sweep_settings_layout.setSpacing(4)
        self._add_sweep_settings_rows(self.sweep_settings_layout)
        layout.addWidget(self.sweep_settings_widget)
        self.sweep_settings_widget.setVisible(False)

        layout.addStretch()
        self._connect_signals()

    # ------------------------------------------------------------------
    # RADIUS ROW
    # ------------------------------------------------------------------
    def _add_radius_row(self, parent_layout):
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)

        lbl = QtWidgets.QLabel("Radius")
        lbl.setFixedWidth(52)
        row.addWidget(lbl)

        self.rad_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.rad_slider.setMinimum(0)
        self.rad_slider.setMaximum(1000)
        row.addWidget(self.rad_slider)

        self.rad_spin = QtWidgets.QDoubleSpinBox()
        self.rad_spin.setDecimals(3)
        self.rad_spin.setMinimum(0.0)
        self.rad_spin.setMaximum(RADIUS_MAX_SPINBOX)
        self.rad_spin.setKeyboardTracking(False)
        self.rad_spin.setFixedWidth(62)
        self.rad_spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.rad_spin.setValue(RADIUS_DEFAULT)
        row.addWidget(self.rad_spin)

        self.rad_minus_btn = QtWidgets.QPushButton("-")
        self.rad_minus_btn.setFixedSize(22, 22)
        self.rad_minus_btn.setToolTip("Divise le radius par 2")
        self.rad_plus_btn = QtWidgets.QPushButton("+")
        self.rad_plus_btn.setFixedSize(22, 22)
        self.rad_plus_btn.setToolTip("Multiplie le radius par 2")
        row.addWidget(self.rad_minus_btn)
        row.addWidget(self.rad_plus_btn)

        parent_layout.addLayout(row)

        def _slider_to_spin(val):
            ratio = val / 1000.0
            real_val = RADIUS_MIN + ratio * (RADIUS_SOFT_MAX - RADIUS_MIN)
            self.rad_spin.blockSignals(True)
            self.rad_spin.setValue(real_val)
            self.rad_spin.blockSignals(False)
            self._on_chamfer_changed()

        def _spin_to_slider(val):
            clamped = max(RADIUS_MIN, min(RADIUS_SOFT_MAX, float(val)))
            ratio = (clamped - RADIUS_MIN) / (RADIUS_SOFT_MAX - RADIUS_MIN)
            self.rad_slider.blockSignals(True)
            self.rad_slider.setValue(int(ratio * 1000))
            self.rad_slider.blockSignals(False)
            self._on_chamfer_changed()

        def _rad_minus():
            cur = float(self.rad_spin.value())
            if cur > 0.001:
                new_val = max(0.001, cur / 2.0)
                self.rad_spin.setValue(new_val)
                _spin_to_slider(new_val)

        def _rad_plus():
            cur = float(self.rad_spin.value())
            new_val = cur * 2.0 if cur > 0.0 else 0.1
            self.rad_spin.setValue(new_val)
            _spin_to_slider(new_val)

        self.rad_slider.valueChanged.connect(_slider_to_spin)
        self.rad_spin.valueChanged.connect(_spin_to_slider)
        self.rad_minus_btn.clicked.connect(_rad_minus)
        self.rad_plus_btn.clicked.connect(_rad_plus)

        ratio = (RADIUS_DEFAULT - RADIUS_MIN) / (RADIUS_SOFT_MAX - RADIUS_MIN)
        self.rad_slider.setValue(int(ratio * 1000))

    # ------------------------------------------------------------------
    # GENERIC SLIDER
    # ------------------------------------------------------------------
    def _add_slider(self, parent_layout, label, min_val, max_val, default, decimals, accent_color=None):
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(5)

        lbl = QtWidgets.QLabel(label)
        lbl.setFixedWidth(52)
        row.addWidget(lbl)

        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        slider.setMinimum(0)
        slider.setMaximum(1000)
        row.addWidget(slider)

        if decimals > 0:
            spinbox = QtWidgets.QDoubleSpinBox()
            spinbox.setDecimals(decimals)
            spinbox.setMinimum(min_val)
            spinbox.setMaximum(RADIUS_MAX_SPINBOX)
            spinbox.setKeyboardTracking(False)
        else:
            spinbox = QtWidgets.QSpinBox()
            spinbox.setMinimum(int(min_val))
            spinbox.setMaximum(RADIUS_MAX_SPINBOX)
            spinbox.setKeyboardTracking(False)

        spinbox.setFixedWidth(55)
        spinbox.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        spinbox.setValue(default if decimals > 0 else int(default))
        row.addWidget(spinbox)

        def update_spinbox(val):
            ratio = val / 1000.0
            real_val = min_val + ratio * (max_val - min_val)
            spinbox.blockSignals(True)
            spinbox.setValue(real_val if decimals > 0 else int(real_val))
            spinbox.blockSignals(False)
            self._on_chamfer_changed()

        def update_slider(val):
            clamped = max(min_val, min(max_val, val))
            ratio = (clamped - min_val) / (max_val - min_val)
            slider.blockSignals(True)
            slider.setValue(int(ratio * 1000))
            slider.blockSignals(False)
            self._on_chamfer_changed()

        slider.valueChanged.connect(update_spinbox)
        spinbox.valueChanged.connect(update_slider)

        ratio = (default - min_val) / (max_val - min_val)
        slider.setValue(int(ratio * 1000))
        parent_layout.addLayout(row)
        return slider, spinbox

    def _add_merge_slider(self, parent_layout, label, min_val, max_val, default, accent_color=None):
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)

        lbl = QtWidgets.QLabel(label + ":")
        lbl.setFixedWidth(62)
        row.addWidget(lbl)

        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        slider.setMinimum(0)
        slider.setMaximum(1000)
        row.addWidget(slider)

        spinbox = QtWidgets.QDoubleSpinBox()
        spinbox.setDecimals(3)
        spinbox.setMinimum(min_val)
        spinbox.setMaximum(RADIUS_MAX_SPINBOX)
        spinbox.setKeyboardTracking(False)
        spinbox.setFixedWidth(55)
        spinbox.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        spinbox.setValue(default)
        row.addWidget(spinbox)

        def update_spinbox_from_slider(val):
            ratio = val / 1000.0
            real_val = min_val + ratio * (max_val - min_val)
            spinbox.blockSignals(True)
            spinbox.setValue(real_val)
            spinbox.blockSignals(False)
            self._on_merge_cv_slider_live(real_val)

        def update_slider_from_spinbox(val):
            clamped = max(min_val, min(max_val, float(val)))
            ratio = (clamped - min_val) / (max_val - min_val)
            slider.blockSignals(True)
            slider.setValue(int(ratio * 1000))
            slider.blockSignals(False)
            self._on_merge_cv_slider_live(float(val))

        slider.sliderMoved.connect(update_spinbox_from_slider)
        spinbox.valueChanged.connect(update_slider_from_spinbox)
        slider.setValue(0)
        parent_layout.addLayout(row)
        return slider, spinbox

    def _add_sweep_settings_rows(self, parent_layout):
        for attr, spec in SWEEP_UI_SPECS.items():
            if attr == "interpolationMode":
                self._sweep_controls[attr] = self._add_sweep_mode_combo(parent_layout)
            else:
                slider, spinbox = self._add_sweep_slider(
                    parent_layout,
                    spec["label"],
                    spec["min"],
                    spec["max"],
                    spec["default"],
                    spec["decimals"],
                    attr
                )
                self._sweep_controls[attr] = (slider, spinbox)

    def _add_sweep_mode_combo(self, parent_layout):
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(5)

        lbl = QtWidgets.QLabel("Interp Mode")
        lbl.setFixedWidth(62)
        row.addWidget(lbl)

        combo = QtWidgets.QComboBox()
        combo.addItems(SWEEP_MODE_ITEMS)
        combo.setCurrentIndex(int(SWEEP_UI_SPECS["interpolationMode"]["default"]))
        combo.currentIndexChanged.connect(lambda idx: set_sweep_preview_setting("interpolationMode", int(idx)))
        row.addWidget(combo)
        row.addStretch()
        parent_layout.addLayout(row)
        return ("combo", combo)

    def _add_sweep_slider(self, parent_layout, label, min_val, max_val, default, decimals, attr):
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(5)

        lbl = QtWidgets.QLabel(label)
        lbl.setFixedWidth(62)
        row.addWidget(lbl)

        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        slider.setMinimum(0)
        slider.setMaximum(1000)
        row.addWidget(slider)

        if decimals > 0:
            spinbox = QtWidgets.QDoubleSpinBox()
            spinbox.setDecimals(decimals)
            spinbox.setMinimum(min_val)
            spinbox.setMaximum(max_val)
            spinbox.setKeyboardTracking(False)
        else:
            spinbox = QtWidgets.QSpinBox()
            spinbox.setMinimum(int(min_val))
            spinbox.setMaximum(int(max_val))
            spinbox.setKeyboardTracking(False)

        spinbox.setFixedWidth(62)
        spinbox.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        spinbox.setValue(default if decimals > 0 else int(default))
        row.addWidget(spinbox)

        def update_spinbox(val):
            ratio = val / 1000.0
            real_val = min_val + ratio * (max_val - min_val)
            final_val = real_val if decimals > 0 else int(round(real_val))
            spinbox.blockSignals(True)
            spinbox.setValue(final_val)
            spinbox.blockSignals(False)
            set_sweep_preview_setting(attr, final_val)

        def update_slider(val):
            clamped = max(min_val, min(max_val, float(val)))
            ratio = (clamped - min_val) / (max_val - min_val)
            slider.blockSignals(True)
            slider.setValue(int(ratio * 1000))
            slider.blockSignals(False)
            set_sweep_preview_setting(attr, clamped)

        slider.valueChanged.connect(update_spinbox)
        spinbox.valueChanged.connect(update_slider)

        ratio = (default - min_val) / (max_val - min_val)
        slider.setValue(int(ratio * 1000))
        parent_layout.addLayout(row)
        return slider, spinbox

    # ------------------------------------------------------------------
    # GLOBAL STYLE
    # ------------------------------------------------------------------
    def _apply_global_style(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #1a1a1a;
                border: 1px solid #2a2a2a;
                border-radius: 4px;
            }
            QLabel {
                color: #808080;
                font-size: 11px;
            }
            QPushButton {
                background-color: #242424;
                color: #808080;
                border: 1px solid #333;
                border-radius: 3px;
                font-size: 11px;
                padding: 4px 6px;
            }
            QPushButton:hover {
                background-color: #2e2e2e;
                border-color: #444;
                color: #a0a0a0;
            }
            QPushButton:pressed { background-color: #111; }
            QPushButton:disabled { background-color: #1a1a1a; color: #333; border-color: #222; }
            QSlider::groove:horizontal {
                height: 3px;
                background: #111;
                border-radius: 1px;
            }
            QSlider::handle:horizontal {
                background: #555;
                width: 10px;
                margin: -4px 0;
                border-radius: 5px;
            }
            QSlider::handle:horizontal:hover { background: #888; }
            QSlider::sub-page:horizontal { background: #333; border-radius: 1px; }
            QSpinBox, QDoubleSpinBox {
                background-color: #1e1e1e;
                color: #909090;
                border: 1px solid #2e2e2e;
                border-radius: 2px;
                padding: 2px;
                font-size: 11px;
            }
            QComboBox {
                background-color: #1e1e1e;
                color: #909090;
                border: 1px solid #2e2e2e;
                border-radius: 2px;
                padding: 2px 4px;
                font-size: 11px;
                min-height: 18px;
            }
            QComboBox::drop-down { border: none; width: 16px; }
            QComboBox QAbstractItemView {
                background-color: #1e1e1e;
                color: #b0b0b0;
                selection-background-color: #333;
                border: 1px solid #2e2e2e;
            }
            QCheckBox { color: #707070; font-size: 11px; spacing: 6px; }
            QCheckBox::indicator {
                width: 13px; height: 13px;
                border-radius: 2px;
                border: 1px solid #333;
                background: #1e1e1e;
            }
            QCheckBox::indicator:checked { background: #334; border-color: #4a6a9f; }
            QCheckBox::indicator:hover { border-color: #555; }
        """)

    # ------------------------------------------------------------------
    # SIGNALS
    # ------------------------------------------------------------------
    def _connect_signals(self):
        self.draw_btn.clicked.connect(lambda: draw_curve(self.degree_spin.value(), self.snap_chk.isChecked()))
        self.stop_btn.clicked.connect(stop_draw_tool)
        self.close_btn.clicked.connect(close_selected_curves)
        self.circle_btn.clicked.connect(create_primitive_circle)
        self.square_btn.clicked.connect(create_primitive_square)
        self.auto_curve_btn.clicked.connect(lambda: auto_create_curve_from_vertex(self.auto_curve_len_spin.value()))

        self.edge_btn.clicked.connect(edge_to_curve)
        self.attach_btn.clicked.connect(lambda: self._do_attach(True, "connect"))
        self.mirror_btn.clicked.connect(lambda: mirror_curve('auto', 'world'))

        self.extrude_btn.clicked.connect(lambda: extrude_cv_along_curve(0))
        self.edit_btn.clicked.connect(edit_curve_cvs)
        self.insert_btn.clicked.connect(insert_cv)

        self.merge_cv_spin.valueChanged.connect(lambda v: None)
        self.merge_cv_btn.clicked.connect(self._do_apply_merge_cv)
        self.reset_merge_cv_btn.clicked.connect(self._do_reset_merge_cv)

        self.chamfer_btn.clicked.connect(self._do_chamfer)
        self.unchamfer_btn.clicked.connect(self._do_unchamfer)

        self.split_detach_btn.clicked.connect(split_detach_with_curve)
        self.split_only_btn.clicked.connect(split_only_with_curve)
        self.fill_btn.clicked.connect(lambda: ring_fill(delete_curves=False))
        self.sweep_btn.clicked.connect(self._do_sweep)
        self.bake_btn.clicked.connect(self._do_bake)

        self.snap_chk.toggled.connect(self._on_snap_toggled)
        self.on_top_chk.toggled.connect(self._on_always_on_top_toggled)
        self._sync_sweep_ui_from_settings()
        self._update_bake_button()

    # ------------------------------------------------------------------
    # CONTEXT MENUS
    # ------------------------------------------------------------------
    def _menu_style(self):
        return """
            QMenu { background-color: #252525; border: 1px solid #3a3a3a; padding: 4px; }
            QMenu::item { color: #aaaaaa; padding: 6px 20px; font-size: 11px; }
            QMenu::item:selected { background-color: #333; color: #ffffff; }
            QMenu::separator { height: 1px; background: #333; margin: 3px 0; }
        """

    def _show_ring_fill_menu(self, pos):
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(self._menu_style())
        menu.addAction("Delete Curves").triggered.connect(lambda: ring_fill(delete_curves=True))
        menu.exec_(self.fill_btn.mapToGlobal(pos))

    def _show_attach_menu(self, pos):
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(self._menu_style())

        auto_act = menu.addAction("Auto Reverse")
        auto_act.setCheckable(True)
        auto_act.setChecked(bool(self._attach_auto_reverse))
        auto_act.toggled.connect(lambda v: setattr(self, '_attach_auto_reverse', bool(v)))

        clean_act = menu.addAction("Clean Mode (No Middle Points)")
        clean_act.setCheckable(True)
        clean_act.setChecked(bool(self._attach_clean_mode))
        clean_act.toggled.connect(lambda v: setattr(self, '_attach_clean_mode', bool(v)))

        menu.addSeparator()
        menu.addAction("Connect - Delete Originals").triggered.connect(lambda: self._do_attach(True, "connect"))
        menu.addAction("Connect - Keep Originals").triggered.connect(lambda: self._do_attach(False, "connect"))
        menu.addSeparator()
        menu.addAction("Blend - Delete Originals").triggered.connect(lambda: self._do_attach(True, "blend"))
        menu.addAction("Blend - Keep Originals").triggered.connect(lambda: self._do_attach(False, "blend"))
        menu.exec_(self.attach_btn.mapToGlobal(pos))

    def _do_attach(self, delete, method):
        attach_selected_curves(
            delete_originals=delete, method=method,
            keep_multiple_knots=False,
            auto_reverse=self._attach_auto_reverse,
            clean_attach=self._attach_clean_mode
        )

    def _show_mirror_menu(self, pos):
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(self._menu_style())

        menu.addAction("Smart Auto-Detect").triggered.connect(lambda: mirror_curve('auto', 'world'))
        menu.addSeparator()

        axis_menu = menu.addMenu("Manual Axis")
        axis_menu.setStyleSheet(self._menu_style())
        axis_menu.addAction("X").triggered.connect(lambda: mirror_curve('x', 'world'))
        axis_menu.addAction("Y").triggered.connect(lambda: mirror_curve('y', 'world'))
        axis_menu.addAction("Z").triggered.connect(lambda: mirror_curve('z', 'world'))

        menu.addSeparator()
        mode_menu = menu.addMenu("Pivot Mode")
        mode_menu.setStyleSheet(self._menu_style())
        mode_menu.addAction("World Center (0)").triggered.connect(lambda: self._last_mirror_with_mode('world'))
        mode_menu.addAction("Object Center").triggered.connect(lambda: self._last_mirror_with_mode('object'))
        mode_menu.addAction("Bounding Box").triggered.connect(lambda: self._last_mirror_with_mode('boundingBox'))
        mode_menu.addAction("Grid Snap").triggered.connect(lambda: self._last_mirror_with_mode('grid'))

        menu.addSeparator()
        menu.addAction("Mirror X (World)").triggered.connect(lambda: mirror_curve('x', 'world'))
        menu.addAction("Mirror X (BBox)").triggered.connect(lambda: mirror_curve('x', 'boundingBox'))
        menu.addAction("Mirror Z (World)").triggered.connect(lambda: mirror_curve('z', 'world'))
        menu.addAction("Mirror Z (BBox)").triggered.connect(lambda: mirror_curve('z', 'boundingBox'))

        menu.exec_(self.mirror_btn.mapToGlobal(pos))

    def _last_mirror_with_mode(self, mode):
        axis = self._last_mirror_axis if self._last_mirror_axis != 'auto' else 'x'
        mirror_curve(axis, mode)

    # ------------------------------------------------------------------
    # CALLBACKS
    # ------------------------------------------------------------------
    def _on_snap_toggled(self, checked):
        toggle_snap_grid(checked)

    def _on_always_on_top_toggled(self, checked):
        set_always_on_top_enabled(checked)

    def _on_sel_changed(self):
        global _chamfer_active
        if _chamfer_active and not is_chamfer_curve_selected():
            reset_chamfer()
        self._update_bake_button()

    def _sync_sweep_ui_from_settings(self):
        for attr, control in self._sweep_controls.items():
            spec = SWEEP_UI_SPECS.get(attr, {})
            cur_val = _sweep_preview_settings.get(attr, spec.get("default", 0))
            if isinstance(control, tuple) and control and control[0] == "combo":
                combo = control[1]
                idx = int(max(0, min(len(SWEEP_MODE_ITEMS) - 1, int(round(float(cur_val))))))
                combo.blockSignals(True)
                combo.setCurrentIndex(idx)
                combo.blockSignals(False)
            else:
                decimals = spec.get("decimals", 3)
                slider, spinbox = control
                min_val = spec.get("min", 0.0)
                max_val = spec.get("max", 1.0)
                try:
                    ratio = (float(cur_val) - min_val) / (max_val - min_val)
                except Exception:
                    ratio = 0.0
                ratio = max(0.0, min(1.0, ratio))
                spinbox.blockSignals(True)
                slider.blockSignals(True)
                if decimals == 0:
                    spinbox.setValue(int(round(float(cur_val))))
                else:
                    spinbox.setValue(float(cur_val))
                slider.setValue(int(ratio * 1000))
                spinbox.blockSignals(False)
                slider.blockSignals(False)

    def _update_bake_button(self):
        is_active = is_sweep_preview_active()
        self.bake_btn.setEnabled(is_active)
        if hasattr(self, "sweep_settings_widget"):
            self.sweep_settings_widget.setVisible(is_active)

    def _on_chamfer_changed(self):
        if _chamfer_active and is_chamfer_curve_selected():
            self._rebuild_timer.start()

    def _do_live_chamfer(self):
        if _chamfer_active and is_chamfer_curve_selected():
            try:
                segments = int(self.seg_spin.value())
                radius = float(self.rad_spin.value())
                chamfer_cv(segments, radius, True)
            except Exception:
                pass

    def _do_chamfer(self):
        reset_chamfer()
        try:
            segments = int(self.seg_spin.value())
            radius = float(self.rad_spin.value())
            chamfer_cv(segments, radius, False)
            start_chamfer_viewport_drag(auto=True)
        except Exception as e:
            cmds.warning("[PR] Chamfer failed: {}".format(e))

    def _do_unchamfer(self):
        remove_chamfer()

    def _on_merge_cv_changed(self):
        pass

    def _on_merge_cv_slider_live(self, threshold):
        global _merge_cv_active, _merge_cv_backup

        current_curves = set(_get_curves_for_merge())
        if not current_curves:
            return

        if set(_merge_cv_backup.keys()) != current_curves:
            _merge_cv_backup = {}
            _merge_cv_active = False

        if not _merge_cv_active or not _merge_cv_backup:
            for crv in current_curves:
                data = _get_curve_data(crv)
                if data:
                    shp, degree, form, positions, cyclic, cleaned = data
                    _merge_cv_backup[crv] = {
                        "degree": degree, "form": form,
                        "positions": [list(p) for p in positions],
                        "cyclic": cyclic,
                    }
            _merge_cv_active = True

        try:
            merge_curve_cvs(float(threshold), live_update=True)
        except Exception:
            pass

    def _do_apply_merge_cv(self):
        try:
            threshold = float(self.merge_cv_spin.value())
            merge_curve_cvs(threshold, live_update=False)
            reset_merge_cv()
            cmds.inViewMessage(amg="<hl>Merge CVs</hl> applied", pos="topCenter", fade=True)
        except Exception as e:
            cmds.warning("[PR] Merge CVs failed: {}".format(e))

    def _do_reset_merge_cv(self):
        self.merge_cv_spin.blockSignals(True)
        self.merge_cv_slider.blockSignals(True)
        self.merge_cv_spin.setValue(0.0)
        self.merge_cv_slider.setValue(0)
        self.merge_cv_spin.blockSignals(False)
        self.merge_cv_slider.blockSignals(False)
        if _merge_cv_active:
            merge_curve_cvs(0.0, live_update=True)
        reset_merge_cv()

    def _do_sweep(self):
        sweep_mesh_preview()
        for attr, spec in SWEEP_UI_SPECS.items():
            set_sweep_preview_setting(attr, _sweep_preview_settings.get(attr, spec["default"]))
        self._sync_sweep_ui_from_settings()
        self._update_bake_button()

    def _do_bake(self):
        sweep_bake()
        self._update_bake_button()

    def closeEvent(self, event):
        self._rebuild_timer.stop()

        if is_sweep_preview_active():
            sweep_cancel()

        if self._job:
            try:
                if cmds.scriptJob(exists=self._job):
                    cmds.scriptJob(kill=self._job, force=True)
            except Exception:
                pass
            self._job = None

        _stop_chamfer_drag_tool()

        try:
            if PRCurveToolsUI._instance is self:
                PRCurveToolsUI._instance = None
        except Exception:
            pass

        try:
            QtWidgets.QDialog.closeEvent(self, event)
        except Exception:
            pass

    @classmethod
    def show_ui(cls):
        try:
            app = QtWidgets.QApplication.instance()
            for w in (app.allWidgets() if app else []):
                try:
                    if w.objectName() == WINDOW_OBJECT_NAME:
                        if isinstance(w, cls):
                            cls._instance = w
                            w.show()
                            w.raise_()
                            w.activateWindow()
                            return w
                        else:
                            try:
                                w.setParent(None)
                            except Exception:
                                pass
                            try:
                                w.close()
                            except Exception:
                                pass
                            try:
                                w.deleteLater()
                            except Exception:
                                pass
                except Exception:
                    pass
        except Exception:
            pass

        cls._instance = cls()
        cls._instance.show()
        cls._instance.raise_()
        cls._instance.activateWindow()
        return cls._instance


# ============================================================
# ENTRY POINT
# ============================================================
def show_pr_curve_tools():
    _delete_existing_pr_curve_tools_windows()
    return PRCurveToolsUI.show_ui()


if __name__ == "__main__":
    show_pr_curve_tools()
