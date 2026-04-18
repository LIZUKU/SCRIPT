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
CIRCLE_SECTIONS_DEFAULT = 32
CIRCLE_SECTIONS_PRESETS = (8, 16, 32, 64, 128)


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
MIRROR_ADV_DEFAULTS = {
    "distance_offset": 0.0,
    "reverse": False,
    "keep_original": False,
    "hide_original_if_kept": True,
    "consolidate_seam": True,
    "seam_tol": 0.0001,
    "auto_close": True,
}


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


def _mc_vec_add(a, b):
    return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]


def _mc_vec_sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _mc_vec_mul(a, s):
    return [a[0] * s, a[1] * s, a[2] * s]


def _mc_vec_dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _mc_vec_len(a):
    return math.sqrt(max(_mc_vec_dot(a, a), 1e-16))


def _mc_vec_norm(a):
    l = _mc_vec_len(a)
    if l < 1e-8:
        return [1.0, 0.0, 0.0]
    return [a[0] / l, a[1] / l, a[2] / l]


def _mc_vec_lerp(a, b, t):
    return [
        a[0] + (b[0] - a[0]) * t,
        a[1] + (b[1] - a[1]) * t,
        a[2] + (b[2] - a[2]) * t,
    ]


def _mc_distance(a, b):
    return _mc_vec_len(_mc_vec_sub(a, b))


def _mc_signed_distance_to_plane(point, plane_point, plane_normal):
    return _mc_vec_dot(_mc_vec_sub(point, plane_point), plane_normal)


def _mc_mirror_point_across_plane(point, plane_point, plane_normal):
    d = _mc_signed_distance_to_plane(point, plane_point, plane_normal)
    return _mc_vec_sub(point, _mc_vec_mul(plane_normal, 2.0 * d))


def _mc_dedupe_consecutive(points, tol=1e-5):
    if not points:
        return []
    out = [points[0]]
    for p in points[1:]:
        if _mc_distance(p, out[-1]) > tol:
            out.append(p)
    return out


def _mc_get_curve_cvs_world(curve_transform):
    shapes = cmds.listRelatives(curve_transform, s=True, type="nurbsCurve", fullPath=True) or []
    if not shapes:
        return None, None, None
    shape = shapes[0]
    cvs = cmds.ls(curve_transform + ".cv[*]", fl=True) or []
    points = [cmds.pointPosition(cv, world=True) for cv in cvs]
    degree = cmds.getAttr(shape + ".degree")
    form = cmds.getAttr(shape + ".form")
    return [[p[0], p[1], p[2]] for p in points], degree, form


def _mc_clip_cvs_against_plane(points, plane_point, plane_normal, keep_positive=True, eps=1e-8):
    if len(points) < 2:
        return points[:]

    def inside(sd):
        return sd >= -eps if keep_positive else sd <= eps

    out = []
    prev = points[0]
    prev_sd = _mc_signed_distance_to_plane(prev, plane_point, plane_normal)
    prev_in = inside(prev_sd)

    if prev_in:
        out.append(prev)

    for curr in points[1:]:
        curr_sd = _mc_signed_distance_to_plane(curr, plane_point, plane_normal)
        curr_in = inside(curr_sd)

        crossed = (prev_sd > eps and curr_sd < -eps) or (prev_sd < -eps and curr_sd > eps)
        if crossed:
            denom = prev_sd - curr_sd
            if abs(denom) > eps:
                t = prev_sd / denom
                inter = _mc_vec_lerp(prev, curr, t)
                if not out or _mc_distance(out[-1], inter) > 1e-7:
                    out.append(inter)
        elif abs(curr_sd) <= eps:
            if not out or _mc_distance(out[-1], curr) > 1e-7:
                out.append(curr)

        if curr_in and (not out or _mc_distance(out[-1], curr) > 1e-7):
            out.append(curr)

        prev = curr
        prev_sd = curr_sd
        prev_in = curr_in

    return _mc_dedupe_consecutive(out, tol=1e-7)


def _mc_build_mirrored_profile(points_kept, plane_point, plane_normal, seam_tol=1e-4, consolidate_seam=True):
    if len(points_kept) < 2:
        return points_kept[:]
    mirrored = [_mc_mirror_point_across_plane(p, plane_point, plane_normal) for p in points_kept]
    mirrored.reverse()
    result = points_kept[:]
    if mirrored and consolidate_seam and _mc_distance(result[-1], mirrored[0]) <= seam_tol:
        mirrored = mirrored[1:]
    result.extend(mirrored)
    return _mc_dedupe_consecutive(result, tol=seam_tol)


def _mc_insert_center_plane_points(points, plane_point, plane_normal, eps=1e-8, tol=1e-6):
    if len(points) < 2:
        return points[:]

    out = [points[0]]
    for i in range(1, len(points)):
        prev = out[-1]
        curr = points[i]
        prev_sd = _mc_signed_distance_to_plane(prev, plane_point, plane_normal)
        curr_sd = _mc_signed_distance_to_plane(curr, plane_point, plane_normal)

        crossed = (prev_sd > eps and curr_sd < -eps) or (prev_sd < -eps and curr_sd > eps)
        on_prev = abs(prev_sd) <= eps
        on_curr = abs(curr_sd) <= eps

        if crossed:
            denom = prev_sd - curr_sd
            if abs(denom) > eps:
                t = prev_sd / denom
                inter = _mc_vec_lerp(prev, curr, t)
                if _mc_distance(out[-1], inter) > tol:
                    out.append(inter)
        elif not on_prev and on_curr:
            if _mc_distance(out[-1], curr) > tol:
                out.append(curr)
            continue
        elif on_prev and not on_curr:
            pass

        if _mc_distance(out[-1], curr) > tol:
            out.append(curr)
    return _mc_dedupe_consecutive(out, tol=tol)


def _mc_curve_parent_short_name(curve_transform):
    return curve_transform.split("|")[-1].split(":")[-1]


def _mirrorclip_curve(
    curve_transform,
    origin,
    direction,
    distance_offset=0.0,
    reverse=False,
    keep_original=False,
    hide_original_if_kept=True,
    consolidate_seam=True,
    seam_tol=0.0001,
    auto_close=True,
):
    direction = _mc_vec_norm(direction)
    plane_point = _mc_vec_add(origin, _mc_vec_mul(direction, distance_offset))

    cv_points, curve_degree, curve_form = _mc_get_curve_cvs_world(curve_transform)
    if not cv_points:
        raise RuntimeError("Courbe invalide.")

    keep_positive = not reverse
    kept = _mc_clip_cvs_against_plane(
        cv_points,
        plane_point,
        direction,
        keep_positive=keep_positive,
    )
    if len(kept) < 2:
        raise RuntimeError("La partie gardée après clip est vide ou trop petite.")

    final_points = _mc_build_mirrored_profile(
        kept,
        plane_point,
        direction,
        seam_tol=seam_tol,
        consolidate_seam=consolidate_seam,
    )
    final_points = _mc_insert_center_plane_points(
        final_points,
        plane_point,
        direction,
        tol=seam_tol,
    )

    short_name = _mc_curve_parent_short_name(curve_transform)
    new_name = "{}_mirror".format(short_name)
    new_degree = min(max(1, int(curve_degree)), max(1, len(final_points) - 1))
    if curve_form in (1, 2):
        new_degree = min(new_degree, 3)

    new_curve = cmds.curve(p=final_points, d=new_degree, name=new_name)
    if auto_close:
        try:
            cmds.closeCurve(new_curve, ch=False, ps=True, rpo=True)
        except Exception:
            pass
    _set_curve_always_on_top(new_curve)
    add_to_isolate(new_curve)

    if keep_original:
        if hide_original_if_kept and _safe_obj_exists(curve_transform):
            try:
                cmds.hide(curve_transform)
            except Exception:
                pass
    else:
        _safe_delete(curve_transform)

    return new_curve


def _mirror_pivot_from_mode(curves, axis, mode):
    axis_key = str(axis).lower().lstrip('-')
    axis_map = {'x': 0, 'y': 1, 'z': 2}
    axis_idx = axis_map.get(axis_key, 0)
    if mode == 'world':
        return 0.0
    if mode == 'object':
        bbox = cmds.exactWorldBoundingBox(curves[0])
        return (bbox[axis_idx] + bbox[axis_idx + 3]) * 0.5
    if mode == 'boundingBox':
        bbox = cmds.exactWorldBoundingBox(curves)
        return (bbox[axis_idx] + bbox[axis_idx + 3]) * 0.5
    if mode == 'grid':
        bbox = cmds.exactWorldBoundingBox(curves)
        center = (bbox[axis_idx] + bbox[axis_idx + 3]) * 0.5
        grid_size = cmds.grid(q=True, spacing=True)
        return round(center / grid_size) * grid_size if grid_size else 0.0
    return 0.0


def mirror_curve(axis='auto', mode='world', merge_threshold=0.001, advanced=None, quiet=False):
    sel = cmds.ls(sl=True, long=True) or []
    curves = list(set(filter(None, [get_curve_transform(s) for s in sel])))
    if not curves:
        cmds.warning("[PR] Select curves to mirror.")
        return

    if axis == 'auto':
        axis = _detect_best_mirror_axis(curves)
    axis = axis.lower() if axis else 'x'
    if axis not in ('x', 'y', 'z', '-x', '-y', '-z'):
        axis = 'x'

    opts = dict(MIRROR_ADV_DEFAULTS)
    if isinstance(advanced, dict):
        opts.update(advanced)

    result_curves = []
    axis_vec = {'x': [1.0, 0.0, 0.0], 'y': [0.0, 1.0, 0.0], 'z': [0.0, 0.0, 1.0], '-x': [-1.0, 0.0, 0.0], '-y': [0.0, -1.0, 0.0], '-z': [0.0, 0.0, -1.0]}[axis]
    pivot = _mirror_pivot_from_mode(curves, axis, mode)
    origin = [0.0, 0.0, 0.0]
    origin[{'x': 0, 'y': 1, 'z': 2}[axis.lstrip('-')]] = float(pivot)

    for crv in curves:
        try:
            new_crv = _mirrorclip_curve(
                curve_transform=crv,
                origin=origin,
                direction=axis_vec,
                distance_offset=float(opts.get("distance_offset", 0.0)),
                reverse=bool(opts.get("reverse", False)),
                keep_original=bool(opts.get("keep_original", False)),
                hide_original_if_kept=bool(opts.get("hide_original_if_kept", True)),
                consolidate_seam=bool(opts.get("consolidate_seam", True)),
                seam_tol=max(0.0, float(opts.get("seam_tol", 0.0001))),
                auto_close=bool(opts.get("auto_close", True)),
            )
            result_curves.append(new_crv)
        except Exception as e:
            cmds.warning("[PR] Mirror failed for {}: {}".format(crv, e))

    if result_curves:
        cmds.select(result_curves, r=True)
        if not quiet:
            print("[PR] {} curve(s) mirror-clipped along {}.".format(len(result_curves), axis.upper()))
            cmds.inViewMessage(
                amg="<hl>Mirror</hl> done along <hl>{}</hl>".format(axis.upper().replace("-", "−")),
                pos="topCenter",
                fade=True
            )


# ============================================================
# EDGE TO CURVE
# ============================================================
def _split_edges_by_connectivity(edges):
    by_mesh = {}
    for edge in edges:
        mesh = edge.split(".e[", 1)[0]
        by_mesh.setdefault(mesh, []).append(edge)

    groups = []
    for mesh, mesh_edges in by_mesh.items():
        edge_to_verts = {}
        vert_to_edges = {}

        for edge in mesh_edges:
            verts = cmds.polyListComponentConversion(edge, fromEdge=True, toVertex=True) or []
            verts = cmds.ls(verts, fl=True) or []
            if len(verts) < 2:
                continue
            edge_to_verts[edge] = set(verts)
            for v in verts:
                vert_to_edges.setdefault(v, set()).add(edge)

        remaining = set(edge_to_verts.keys())
        while remaining:
            seed = next(iter(remaining))
            stack = [seed]
            chunk = []
            while stack:
                current = stack.pop()
                if current not in remaining:
                    continue
                remaining.remove(current)
                chunk.append(current)
                for v in edge_to_verts.get(current, ()):
                    for neighbor in vert_to_edges.get(v, ()):
                        if neighbor in remaining:
                            stack.append(neighbor)
            if chunk:
                groups.append(chunk)

    return groups


def edge_to_curve():
    sel = cmds.ls(sl=True, fl=True, long=True) or []
    edges = list(dict.fromkeys([c for c in sel if ".e[" in c]))
    if not edges:
        cmds.warning("[PR] Select edges.")
        return

    edge_groups = _split_edges_by_connectivity(edges)
    if not edge_groups:
        cmds.warning("[PR] No valid edges found.")
        return

    created_curves = []
    try:
        for group in edge_groups:
            cmds.select(group, r=True)
            res = cmds.polyToCurve(form=2, degree=1, conformToSmoothMeshPreview=False)
            if not res:
                continue
            curve = res[0]
            created_curves.append(curve)
            add_to_isolate(curve)
            _set_curve_always_on_top(curve)

        if created_curves:
            cmds.select(created_curves, r=True)
            print("[PR] {} curve(s) created from {} edge group(s).".format(len(created_curves), len(edge_groups)))
        else:
            cmds.warning("[PR] polyToCurve failed on selected edges.")
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
def create_primitive_circle(sections=CIRCLE_SECTIONS_DEFAULT):
    try:
        sections = int(max(3, sections))
        crv = cmds.circle(c=(0, 0, 0), nr=(0, 1, 0), sw=360, r=1, d=1, ut=0, tol=0.01, s=sections, ch=1)[0]
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


def _create_curve_segments_from_points(base_curve, segments_points, degree=1):
    created = []
    base_short = base_curve.split("|")[-1].split(":")[-1]
    for pts in segments_points:
        if len(pts) < 2:
            continue
        try:
            d = min(max(1, int(degree)), len(pts) - 1)
            new_crv = cmds.curve(d=d, p=pts, name="{}_part#".format(base_short))
            _set_curve_always_on_top(new_crv)
            add_to_isolate(new_crv)
            created.append(new_crv)
        except Exception as e:
            cmds.warning("[PR] Failed to build curve segment: {}".format(e))
    return created


def _collect_selected_curve_point_indices(selection):
    """Collect curve indices from selected CVs/EPs."""
    by_curve = {}
    for comp in selection:
        crv = None
        idx = None
        try:
            if ".cv[" in comp:
                crv = get_curve_transform(comp.split(".cv[")[0]) or comp.split(".cv[")[0]
                idx = int(comp.split("[")[1].split("]")[0])
            elif ".ep[" in comp:
                crv = get_curve_transform(comp.split(".ep[")[0]) or comp.split(".ep[")[0]
                ep_pos = cmds.pointPosition(comp, w=True)
                cv_names = cmds.ls(crv + ".cv[*]", fl=True) or []
                if not cv_names:
                    continue
                cv_positions = [cmds.pointPosition(cv_name, w=True) for cv_name in cv_names]
                idx = min(
                    range(len(cv_positions)),
                    key=lambda i: _dist3(cv_positions[i], ep_pos)
                )
            if crv is not None and idx is not None:
                by_curve.setdefault(crv, set()).add(int(idx))
        except Exception:
            continue
    return by_curve


def split_curve_at_selected_cvs():
    sel = cmds.ls(sl=True, fl=True) or []
    point_components = [c for c in sel if ".cv[" in c or ".ep[" in c]
    if not point_components:
        cmds.warning("[PR] Split CV: select one or more CVs/EPs.")
        return

    by_curve = _collect_selected_curve_point_indices(point_components)
    if not by_curve:
        cmds.warning("[PR] Split CV: no valid curve points found.")
        return

    all_created = []
    for crv, idx_set in by_curve.items():
        try:
            data = _get_curve_data(crv)
            if not data:
                continue
            _shp, degree, _form, _positions_raw, cyclic, cleaned_positions = data
            if cyclic:
                points = [list(p) for p in cleaned_positions]
                count = len(points)
                if count < 3:
                    continue
                split_indices = sorted({i % count for i in idx_set})
                if not split_indices:
                    continue

                segments_pts = []
                if len(split_indices) == 1:
                    start = split_indices[0]
                    rotated = points[start:] + points[:start]
                    if len(rotated) >= 2:
                        segments_pts = [rotated]
                else:
                    wrapped = split_indices + [split_indices[0] + count]
                    for a, b in zip(wrapped[:-1], wrapped[1:]):
                        seg = [points[k % count] for k in range(a, b + 1)]
                        if len(seg) >= 2:
                            segments_pts.append(seg)
            else:
                cv_names = cmds.ls(crv + ".cv[*]", fl=True) or []
                points = [list(cmds.pointPosition(c, w=True)) for c in cv_names]
                count = len(points)
                if count < 2:
                    continue
                split_indices = sorted(i for i in idx_set if 0 < i < count - 1)
                if not split_indices:
                    cmds.warning("[PR] Split CV: ignored end CV selection on {}.".format(crv))
                    continue
                start_indices = [0] + split_indices
                end_indices = split_indices + [count - 1]
                segments_pts = [points[s:e + 1] for s, e in zip(start_indices, end_indices)]

            created = _create_curve_segments_from_points(crv, segments_pts, degree=degree)
            if created:
                _safe_delete(crv)
                all_created.extend(created)
        except Exception as e:
            cmds.warning("[PR] Split CV failed on {}: {}".format(crv, e))

    if all_created:
        cmds.select(all_created, r=True)
        print("[PR] Split CV: {} curve(s) created.".format(len(all_created)))


def delete_selected_cvs_open():
    sel = cmds.ls(sl=True, fl=True) or []
    point_components = [c for c in sel if ".cv[" in c or ".ep[" in c]
    if not point_components:
        cmds.warning("[PR] Delete CV Open: select one or more CVs/EPs.")
        return

    by_curve = _collect_selected_curve_point_indices(point_components)
    if not by_curve:
        cmds.warning("[PR] Delete CV Open: no valid curve points found.")
        return

    all_created = []
    for crv, idx_set in by_curve.items():
        try:
            data = _get_curve_data(crv)
            if not data:
                continue
            _shp, degree, _form, _positions_raw, _cyclic, _positions = data
            cv_names = cmds.ls(crv + ".cv[*]", fl=True) or []
            positions_full = [list(cmds.pointPosition(c, w=True)) for c in cv_names]
            if len(positions_full) < 2:
                continue

            segments_pts = []
            run = []
            for i, p in enumerate(positions_full):
                if i in idx_set:
                    if len(run) >= 2:
                        segments_pts.append(run)
                    run = []
                else:
                    run.append(p)
            if len(run) >= 2:
                segments_pts.append(run)

            created = _create_curve_segments_from_points(crv, segments_pts, degree=degree)
            if created:
                _safe_delete(crv)
                all_created.extend(created)
        except Exception as e:
            cmds.warning("[PR] Delete CV Open failed on {}: {}".format(crv, e))

    if all_created:
        cmds.select(all_created, r=True)
        print("[PR] Delete CV Open: {} curve(s) created.".format(len(all_created)))


# ============================================================
# SLOT TOOL
# ============================================================
_SLOT_TOOL = {
    "context_name": "qdSlotToolContext",
    "source_curves": [],
    "preview_nodes": [],
    "width": 1.0,
    "start_width": 1.0,
    "anchor_x": 0.0,
    "press_button": 1,
    "axis_mode": "auto",   # auto / x / y / z
    "active": False,
    "miter_limit": 4.0,
    "smooth_amount": 0.0,
    "start_smooth_amount": 0.0,
    "hard_angle_deg": 170.0,
}


def _info(msg):
    om.MGlobal.displayInfo("[SlotTool] {}".format(msg))


def _warn(msg):
    om.MGlobal.displayWarning("[SlotTool] {}".format(msg))


def _obj_exists(node):
    return bool(node) and cmds.objExists(node)


def _get_curve_shape(node):
    if not _obj_exists(node):
        return None
    if cmds.nodeType(node) == "nurbsCurve":
        return node
    shapes = cmds.listRelatives(node, s=True, ni=True, f=True) or []
    for s in shapes:
        if cmds.nodeType(s) == "nurbsCurve":
            return s
    return None


def _get_curve_fn(node):
    shape = _get_curve_shape(node)
    if not shape:
        raise RuntimeError("Selection invalide : pas de nurbsCurve.")
    sel = om.MSelectionList()
    sel.add(shape)
    try:
        dag = sel.getDagPath(0)
    except TypeError:
        dag = om.MDagPath()
        sel.getDagPath(0, dag)
    return om.MFnNurbsCurve(dag), shape


def _point_to_tuple(p):
    return (p.x, p.y, p.z)


def _delete_nodes(nodes):
    for n in nodes or []:
        if _obj_exists(n):
            try:
                cmds.delete(n)
            except Exception:
                pass


def _clamp(v, vmin, vmax):
    return max(vmin, min(vmax, v))


def _read_modifiers():
    mods = cmds.getModifiers()
    return bool(mods & 1), bool(mods & 4), bool(mods & 8)


def _cycle_axis_mode(current_mode):
    order = ["auto", "x", "y", "z"]
    try:
        idx = order.index(current_mode)
    except ValueError:
        idx = 0
    return order[(idx + 1) % len(order)]


def _length2d(v):
    return math.sqrt(v[0] * v[0] + v[1] * v[1])


def _normalize2d(v):
    l = _length2d(v)
    if l < 1e-12:
        return (0.0, 0.0)
    return (v[0] / l, v[1] / l)


def _distance2d(a, b):
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return math.sqrt(dx * dx + dy * dy)


def _perp2d(v):
    return (-v[1], v[0])


def _dot2d(a, b):
    return a[0] * b[0] + a[1] * b[1]


def _cross2d(a, b):
    return a[0] * b[1] - a[1] * b[0]


def _signed_area_2d(points):
    n = len(points)
    if n < 3:
        return 0.0
    area = 0.0
    for i in range(n):
        a = points[i]
        b = points[(i + 1) % n]
        area += (a[0] * b[1]) - (b[0] * a[1])
    return area * 0.5


def _line_intersection_2d(p1, d1, p2, d2):
    denom = _cross2d(d1, d2)
    if abs(denom) < 1e-10:
        return None
    diff = (p2[0] - p1[0], p2[1] - p1[1])
    t = _cross2d(diff, d2) / denom
    return (p1[0] + d1[0] * t, p1[1] + d1[1] * t)


def _segment_intersection_2d(a1, a2, b1, b2):
    da = (a2[0] - a1[0], a2[1] - a1[1])
    db = (b2[0] - b1[0], b2[1] - b1[1])
    denom = _cross2d(da, db)
    if abs(denom) < 1e-10:
        return None
    diff = (b1[0] - a1[0], b1[1] - a1[1])
    t = _cross2d(diff, db) / denom
    u = _cross2d(diff, da) / denom
    if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
        return (a1[0] + da[0] * t, a1[1] + da[1] * t)
    return None


def _point_line_distance_2d(p, a, b):
    ab = (b[0] - a[0], b[1] - a[1])
    ap = (p[0] - a[0], p[1] - a[1])
    ab_len = _length2d(ab)
    if ab_len < 1e-12:
        return _distance2d(p, a)
    return abs(_cross2d(ab, ap)) / ab_len


def _cleanup_2d_points(points, eps=1e-5, closed=False):
    if not points:
        return []
    out = [points[0]]
    for p in points[1:]:
        if _distance2d(p, out[-1]) > eps:
            out.append(p)
    if closed and len(out) > 2 and _distance2d(out[0], out[-1]) < eps:
        out.pop(-1)
    return out


def _remove_collinear_points(points, closed=False, tolerance=1e-4):
    pts = points[:]
    if len(pts) < 3:
        return pts
    changed = True
    while changed:
        changed = False
        n = len(pts)
        if n < 3:
            break
        new_pts = []
        if closed:
            for i in range(n):
                prev_p = pts[(i - 1) % n]
                p = pts[i]
                next_p = pts[(i + 1) % n]
                if _distance2d(prev_p, p) < tolerance or _distance2d(p, next_p) < tolerance:
                    changed = True
                    continue
                if _point_line_distance_2d(p, prev_p, next_p) < tolerance:
                    changed = True
                    continue
                new_pts.append(p)
            pts = _cleanup_2d_points(new_pts, eps=tolerance, closed=True)
        else:
            new_pts.append(pts[0])
            for i in range(1, n - 1):
                prev_p, p, next_p = pts[i - 1], pts[i], pts[i + 1]
                if _distance2d(prev_p, p) < tolerance or _distance2d(p, next_p) < tolerance:
                    changed = True
                    continue
                if _point_line_distance_2d(p, prev_p, next_p) < tolerance:
                    changed = True
                    continue
                new_pts.append(p)
            new_pts.append(pts[-1])
            pts = _cleanup_2d_points(new_pts, eps=tolerance, closed=False)
    return pts


def _append_unique(out, pts, eps=1e-5):
    if not pts:
        return
    if not out:
        out.extend(pts)
        return
    for p in pts:
        if _distance2d(out[-1], p) > eps:
            out.append(p)


def _corner_angle_deg(d_prev, d_next):
    return math.degrees(math.acos(_clamp(_dot2d(d_prev, d_next), -1.0, 1.0)))


def _is_hard_corner(d_prev, d_next, hard_angle_deg):
    return _corner_angle_deg(d_prev, d_next) < hard_angle_deg


def _build_arc_with_sweep(center, radius, a0, sweep, steps):
    cx, cy = center
    pts = []
    steps = max(2, int(steps))
    for i in range(steps + 1):
        t = float(i) / float(steps)
        ang = a0 + sweep * t
        pts.append((cx + math.cos(ang) * radius, cy + math.sin(ang) * radius))
    return pts


def _choose_arc_by_outward(center, start_pt, end_pt, outward_dir, steps=8):
    cx, cy = center
    sv = (start_pt[0] - cx, start_pt[1] - cy)
    ev = (end_pt[0] - cx, end_pt[1] - cy)
    radius = _length2d(sv)
    if radius < 1e-10:
        return [start_pt, end_pt]
    a0 = math.atan2(sv[1], sv[0])
    a1 = math.atan2(ev[1], ev[0])
    ccw = a1 - a0
    while ccw <= 0.0:
        ccw += math.pi * 2.0
    cw = a1 - a0
    while cw >= 0.0:
        cw -= math.pi * 2.0
    arc_ccw = _build_arc_with_sweep(center, radius, a0, ccw, steps)
    arc_cw = _build_arc_with_sweep(center, radius, a0, cw, steps)
    mid_ccw = arc_ccw[len(arc_ccw) // 2]
    mid_cw = arc_cw[len(arc_cw) // 2]
    score_ccw = _dot2d(_normalize2d((mid_ccw[0] - cx, mid_ccw[1] - cy)), outward_dir)
    score_cw = _dot2d(_normalize2d((mid_cw[0] - cx, mid_cw[1] - cy)), outward_dir)
    return arc_ccw if score_ccw >= score_cw else arc_cw


def _arc_2d(center, start_pt, end_pt, outward_dir, steps=16):
    return _choose_arc_by_outward(center, start_pt, end_pt, outward_dir, steps=steps)


def _world_to_plane2d(p, axis):
    if axis == "x":
        return (p.y, p.z)
    if axis == "y":
        return (p.x, p.z)
    return (p.x, p.y)


def _plane2d_to_world(p2, axis, base_point):
    if axis == "x":
        return om.MPoint(base_point.x, p2[0], p2[1])
    if axis == "y":
        return om.MPoint(p2[0], base_point.y, p2[1])
    return om.MPoint(p2[0], p2[1], base_point.z)


def _sample_curve_world(fn_curve, count=120):
    total_len = fn_curve.length()
    is_closed = fn_curve.form in (om.MFnNurbsCurve.kClosed, om.MFnNurbsCurve.kPeriodic)
    samples = count if is_closed else (count + 1)
    denom = float(count) if count > 0 else 1.0
    pts3 = []
    for i in range(samples):
        param = fn_curve.findParamFromLength(total_len * (float(i) / denom))
        pts3.append(fn_curve.getPointAtParam(param, om.MSpace.kWorld))
    return pts3, is_closed


def _projection_score(points3d, axis, is_closed):
    pts2 = _cleanup_2d_points([_world_to_plane2d(p, axis) for p in points3d], eps=1e-5, closed=is_closed)
    if len(pts2) < 2:
        return -1e20
    xs, ys = [p[0] for p in pts2], [p[1] for p in pts2]
    width, height = max(xs) - min(xs), max(ys) - min(ys)
    if is_closed:
        return abs(_signed_area_2d(pts2)) + (width * height * 0.01)
    span = sum(_distance2d(pts2[i], pts2[i + 1]) for i in range(len(pts2) - 1))
    return span + max(width, height) * 0.01


def _auto_best_axis(curve_node):
    fn_curve, _ = _get_curve_fn(curve_node)
    points3d, is_closed = _sample_curve_world(fn_curve, count=90)
    best_axis, best_score = "z", -1e20
    for axis in ("x", "y", "z"):
        score = _projection_score(points3d, axis, is_closed)
        if score > best_score:
            best_score, best_axis = score, axis
    return best_axis


def _resolve_axis_for_curve(curve_node, axis_mode):
    return _auto_best_axis(curve_node) if axis_mode == "auto" else axis_mode


def _extract_curve_points_2d(fn_curve, axis):
    is_closed = fn_curve.form in (om.MFnNurbsCurve.kClosed, om.MFnNurbsCurve.kPeriodic)
    degree, num_cvs = fn_curve.degree, fn_curve.numCVs
    pts3, pts2 = [], []
    use_cvs = (degree == 1) or (num_cvs <= 12)
    if use_cvs:
        for p in fn_curve.cvPositions(om.MSpace.kWorld):
            pts3.append(p)
            pts2.append(_world_to_plane2d(p, axis))
        pts2 = _cleanup_2d_points(pts2, eps=1e-5, closed=is_closed)
        if len(pts2) > 3:
            pts2 = _remove_collinear_points(pts2, closed=is_closed, tolerance=1e-5)
        if pts3:
            return pts3, pts2, is_closed
    sample_count = 220 if not is_closed else 180
    total_len = fn_curve.length()
    samples = sample_count if is_closed else (sample_count + 1)
    denom = float(sample_count) if sample_count > 0 else 1.0
    for i in range(samples):
        param = fn_curve.findParamFromLength(total_len * (float(i) / denom))
        p = fn_curve.getPointAtParam(param, om.MSpace.kWorld)
        pts3.append(p)
        pts2.append(_world_to_plane2d(p, axis))
    pts2 = _cleanup_2d_points(pts2, eps=1e-5, closed=is_closed)
    if len(pts2) > 3:
        pts2 = _remove_collinear_points(pts2, closed=is_closed, tolerance=1e-4)
    return pts3, pts2, is_closed


def _join_intersection(p, d_prev, d_next, n_prev, n_next, radius, miter_limit):
    prev_offset = (p[0] + n_prev[0] * radius, p[1] + n_prev[1] * radius)
    next_offset = (p[0] + n_next[0] * radius, p[1] + n_next[1] * radius)
    inter = _line_intersection_2d(prev_offset, d_prev, next_offset, d_next)
    if inter is None:
        return None
    if _distance2d(inter, p) > abs(radius) * max(1.0, miter_limit):
        return None
    return inter


def _offset_is_outside_for_closed_polygon(points2d, radius):
    area = _signed_area_2d(points2d)
    return radius < 0.0 if area > 0.0 else radius > 0.0


def _corner_is_convex_original(d_prev, d_next, polygon_area):
    turn = _cross2d(d_prev, d_next)
    return turn > 0.0 if polygon_area > 0.0 else turn < 0.0


def _build_open_side(points2d, radius, miter_limit=4.0, round_amount=0.0, hard_angle_deg=170.0, arc_steps=20):
    n = len(points2d)
    if n < 2:
        return points2d[:]
    dirs, norms = [], []
    for i in range(n - 1):
        a, b = points2d[i], points2d[i + 1]
        d = _normalize2d((b[0] - a[0], b[1] - a[1]))
        if d == (0.0, 0.0):
            d = (1.0, 0.0)
        dirs.append(d)
        norms.append(_perp2d(d))
    out = [(points2d[0][0] + norms[0][0] * radius, points2d[0][1] + norms[0][1] * radius)]
    for i in range(1, n - 1):
        p = points2d[i]
        d_prev, d_next = dirs[i - 1], dirs[i]
        n_prev, n_next = norms[i - 1], norms[i]
        prev_pt = (p[0] + n_prev[0] * radius, p[1] + n_prev[1] * radius)
        next_pt = (p[0] + n_next[0] * radius, p[1] + n_next[1] * radius)
        is_outer_side = (_cross2d(d_prev, d_next) * radius) > 1e-10
        if is_outer_side and round_amount > 0.0 and _is_hard_corner(d_prev, d_next, hard_angle_deg):
            outward_dir = _normalize2d(((n_prev[0] + n_next[0]) * radius, (n_prev[1] + n_next[1]) * radius))
            if outward_dir == (0.0, 0.0):
                outward_dir = _normalize2d((n_next[0] * radius, n_next[1] * radius))
            pts = _choose_arc_by_outward(p, prev_pt, next_pt, outward_dir, steps=max(4, int(4 + round_amount * arc_steps)))
            _append_unique(out, pts[1:])
        elif is_outer_side:
            inter = _join_intersection(p, d_prev, d_next, n_prev, n_next, radius, miter_limit)
            _append_unique(out, [inter] if inter is not None else [prev_pt, next_pt])
        else:
            _append_unique(out, [prev_pt, next_pt])
    _append_unique(out, [(points2d[-1][0] + norms[-1][0] * radius, points2d[-1][1] + norms[-1][1] * radius)])
    out = _cleanup_2d_points(out, eps=1e-5, closed=False)
    if len(out) > 2:
        out = _remove_collinear_points(out, closed=False, tolerance=1e-4)
    return out


def _build_closed_offset(points2d, radius, miter_limit=4.0, round_amount=0.0, hard_angle_deg=170.0, arc_steps=20):
    n = len(points2d)
    if n < 3:
        return points2d[:]
    polygon_area = _signed_area_2d(points2d)
    is_outside_side = _offset_is_outside_for_closed_polygon(points2d, radius)
    out = []
    for i in range(n):
        p_prev, p, p_next = points2d[(i - 1) % n], points2d[i], points2d[(i + 1) % n]
        d_prev = _normalize2d((p[0] - p_prev[0], p[1] - p_prev[1]))
        d_next = _normalize2d((p_next[0] - p[0], p_next[1] - p[1]))
        if d_prev == (0.0, 0.0):
            d_prev = d_next
        if d_next == (0.0, 0.0):
            d_next = d_prev
        n_prev, n_next = _perp2d(d_prev), _perp2d(d_next)
        prev_pt = (p[0] + n_prev[0] * radius, p[1] + n_prev[1] * radius)
        next_pt = (p[0] + n_next[0] * radius, p[1] + n_next[1] * radius)
        original_convex = _corner_is_convex_original(d_prev, d_next, polygon_area)
        is_outer_corner = (original_convex and is_outside_side) or ((not original_convex) and (not is_outside_side))
        if is_outer_corner and round_amount > 0.0 and _is_hard_corner(d_prev, d_next, hard_angle_deg):
            outward_dir = _normalize2d(((n_prev[0] + n_next[0]) * radius, (n_prev[1] + n_next[1]) * radius))
            if outward_dir == (0.0, 0.0):
                outward_dir = _normalize2d((n_next[0] * radius, n_next[1] * radius))
            pts = _choose_arc_by_outward(p, prev_pt, next_pt, outward_dir, steps=max(4, int(4 + round_amount * arc_steps)))
            if not out:
                out.extend(pts)
            else:
                _append_unique(out, pts[1:])
        elif is_outer_corner:
            inter = _join_intersection(p, d_prev, d_next, n_prev, n_next, radius, miter_limit)
            if not out:
                out.extend([inter] if inter is not None else [prev_pt, next_pt])
            else:
                _append_unique(out, [inter] if inter is not None else [prev_pt, next_pt])
        else:
            if not out:
                out.extend([prev_pt, next_pt])
            else:
                _append_unique(out, [prev_pt, next_pt])
    out = _cleanup_2d_points(out, eps=1e-5, closed=True)
    if len(out) > 3:
        out = _remove_collinear_points(out, closed=True, tolerance=1e-4)
    return out


def _light_prune_self_intersections_closed(points2d):
    if len(points2d) < 4:
        return points2d[:]
    pts = points2d[:]
    for _ in range(6):
        changed = False
        n = len(pts)
        if n < 4:
            break
        for i in range(n):
            a1, a2 = pts[i], pts[(i + 1) % n]
            for j in range(i + 2, n):
                if (j + 1) % n == i or j == i:
                    continue
                b1, b2 = pts[j], pts[(j + 1) % n]
                hit = _segment_intersection_2d(a1, a2, b1, b2)
                if hit is not None:
                    new_pts = []
                    for k in range(n):
                        if k == i or k == (j + 1) % n:
                            new_pts.append(hit)
                        elif i < j:
                            if not (i < k <= j):
                                new_pts.append(pts[k])
                        else:
                            new_pts.append(pts[k])
                    pts = _cleanup_2d_points(new_pts, eps=1e-5, closed=True)
                    changed = True
                    break
            if changed:
                break
        if not changed:
            break
    return pts


def _create_curve_from_2d(points2d, axis, base_point, close=True, name="slot_crv"):
    pts2d = _cleanup_2d_points(points2d, eps=1e-5, closed=close)
    if len(pts2d) > 3:
        pts2d = _remove_collinear_points(pts2d, closed=close, tolerance=1e-4)
    pts3d = [_plane2d_to_world(p, axis, base_point) for p in pts2d]
    crv = cmds.curve(p=[_point_to_tuple(p) for p in pts3d], d=1)
    if close:
        crv = cmds.closeCurve(crv, ch=False, ps=False, rpo=True)[0]
    try:
        crv = cmds.rename(crv, name)
    except Exception:
        pass
    return crv


def build_result_for_curve(curve_node, width=1.0, axis_mode="auto", miter_limit=4.0, smooth_amount=0.0, hard_angle_deg=170.0):
    fn_curve, _shape = _get_curve_fn(curve_node)
    axis = _resolve_axis_for_curve(curve_node, axis_mode)
    radius = max(0.0001, width * 0.5)
    round_amount = _clamp(smooth_amount, 0.0, 1.0)
    pts3, pts2, is_closed = _extract_curve_points_2d(fn_curve, axis)
    if len(pts2) < 2:
        raise RuntimeError("Pas assez de points.")
    base_point = pts3[0]
    arc_steps = int(round(10 + round_amount * 28.0))
    if not is_closed:
        side_plus = _build_open_side(pts2, radius, miter_limit, round_amount, hard_angle_deg, arc_steps)
        side_minus = _build_open_side(pts2, -radius, miter_limit, round_amount, hard_angle_deg, arc_steps)
        start_dir = _normalize2d((pts2[1][0] - pts2[0][0], pts2[1][1] - pts2[0][1]))
        end_dir = _normalize2d((pts2[-1][0] - pts2[-2][0], pts2[-1][1] - pts2[-2][1]))
        start_cap = _arc_2d(pts2[0], side_minus[0], side_plus[0], (-start_dir[0], -start_dir[1]), steps=max(10, arc_steps))
        end_cap = _arc_2d(pts2[-1], side_plus[-1], side_minus[-1], end_dir, steps=max(10, arc_steps))
        final2d = side_plus + end_cap[1:-1] + list(reversed(side_minus)) + start_cap[1:-1]
        final2d = _cleanup_2d_points(final2d, eps=1e-5, closed=True)
        if len(final2d) > 3:
            final2d = _remove_collinear_points(final2d, closed=True, tolerance=1e-4)
        final2d = _light_prune_self_intersections_closed(final2d)
        final2d = _cleanup_2d_points(final2d, eps=1e-5, closed=True)
        return [_create_curve_from_2d(final2d, axis, base_point, close=True, name=curve_node.split("|")[-1] + "_slotPreview")]
    outer2d = _light_prune_self_intersections_closed(_cleanup_2d_points(_build_closed_offset(pts2, radius, miter_limit, round_amount, hard_angle_deg, arc_steps), eps=1e-5, closed=True))
    inner2d = _light_prune_self_intersections_closed(_cleanup_2d_points(_build_closed_offset(pts2, -radius, miter_limit, round_amount, hard_angle_deg, arc_steps), eps=1e-5, closed=True))
    outer_name, inner_name = curve_node.split("|")[-1] + "_outerPreview", curve_node.split("|")[-1] + "_innerPreview"
    outer_crv = _create_curve_from_2d(outer2d, axis, base_point, close=True, name=outer_name)
    inner_crv = _create_curve_from_2d(inner2d, axis, base_point, close=True, name=inner_name)
    return [cmds.group([outer_crv, inner_crv], n=curve_node.split("|")[-1] + "_bandPreview_grp")]


def _colorize_curve_or_group(node, rgb=(1.0, 0.75, 0.15)):
    if not _obj_exists(node):
        return
    shapes = []
    node_type = cmds.nodeType(node)
    if node_type == "transform":
        shapes.extend(cmds.listRelatives(node, ad=True, s=True, f=True) or [])
    elif node_type == "nurbsCurve":
        shapes.append(node)
    for s in shapes:
        if cmds.nodeType(s) == "nurbsCurve":
            try:
                cmds.setAttr(s + ".overrideEnabled", 1)
                cmds.setAttr(s + ".overrideRGBColors", 1)
                cmds.setAttr(s + ".overrideColorRGB", rgb[0], rgb[1], rgb[2])
            except Exception:
                pass


def _delete_preview():
    _delete_nodes(_SLOT_TOOL.get("preview_nodes", []))
    _SLOT_TOOL["preview_nodes"] = []


def create_or_update_slot_preview():
    _delete_preview()
    created = []
    for src in _SLOT_TOOL["source_curves"]:
        if not _obj_exists(src):
            continue
        try:
            created.extend(build_result_for_curve(src, width=_SLOT_TOOL["width"], axis_mode=_SLOT_TOOL["axis_mode"],
                                                  miter_limit=_SLOT_TOOL["miter_limit"], smooth_amount=_SLOT_TOOL["smooth_amount"],
                                                  hard_angle_deg=_SLOT_TOOL["hard_angle_deg"]))
        except Exception as e:
            _warn("{} : {}".format(src.split("|")[-1], e))
    for n in created:
        _colorize_curve_or_group(n)
    _SLOT_TOOL["preview_nodes"] = created
    axis_txt = _SLOT_TOOL["axis_mode"].upper() if _SLOT_TOOL["axis_mode"] != "auto" else "AUTO"
    cmds.inViewMessage(amg='Width: <hl>{:.3f}</hl> | Axis: <hl>{}</hl> | CornerRound: <hl>{:.2f}</hl> | HardAngle: <hl>{:.1f}</hl>'.format(
        _SLOT_TOOL["width"], axis_txt, _SLOT_TOOL["smooth_amount"], _SLOT_TOOL["hard_angle_deg"]
    ), pos='botCenter', fade=True)
    cmds.refresh(cv=True)


def finalize_slot_tool():
    previews, finals = _SLOT_TOOL.get("preview_nodes", []), []
    for node in previews:
        if not _obj_exists(node):
            continue
        base = node.replace("_slotPreview", "_slot").replace("_outerPreview", "_outer").replace("_innerPreview", "_inner").replace("_bandPreview_grp", "_band")
        try:
            node = cmds.rename(node, base)
        except Exception:
            pass
        finals.append(node)
    _SLOT_TOOL["preview_nodes"] = []
    _SLOT_TOOL["active"] = False
    if finals:
        cmds.select(finals, r=True)


def cancel_slot_tool():
    _delete_preview()
    _SLOT_TOOL["active"] = False


def slot_tool_press():
    ctx = _SLOT_TOOL["context_name"]
    anchor = cmds.draggerContext(ctx, q=True, anchorPoint=True)
    _SLOT_TOOL["anchor_x"] = anchor[0]
    _SLOT_TOOL["start_width"] = _SLOT_TOOL["width"]
    _SLOT_TOOL["start_smooth_amount"] = _SLOT_TOOL["smooth_amount"]
    _SLOT_TOOL["press_button"] = cmds.draggerContext(ctx, q=True, button=True)
    has_shift, _has_ctrl, _has_alt = _read_modifiers()
    if has_shift:
        _SLOT_TOOL["axis_mode"] = _cycle_axis_mode(_SLOT_TOOL["axis_mode"])
    create_or_update_slot_preview()


def slot_tool_drag():
    ctx = _SLOT_TOOL["context_name"]
    drag = cmds.draggerContext(ctx, q=True, dragPoint=True)
    dx = drag[0] - _SLOT_TOOL["anchor_x"]
    button = _SLOT_TOOL.get("press_button", 1)
    sensitivity = 0.01
    _has_shift, has_ctrl, _has_alt = _read_modifiers()
    if has_ctrl:
        _SLOT_TOOL["width"] = _SLOT_TOOL["start_width"]
        delta = dx * sensitivity
        new_smooth = _SLOT_TOOL["start_smooth_amount"] + (delta if button != 3 else -delta)
        _SLOT_TOOL["smooth_amount"] = _clamp(new_smooth, 0.0, 1.0)
        create_or_update_slot_preview()
        return
    delta = dx * sensitivity
    new_width = _SLOT_TOOL["start_width"] + (delta if button != 3 else -delta)
    _SLOT_TOOL["width"] = max(0.001, new_width)
    create_or_update_slot_preview()


def slot_tool_release():
    create_or_update_slot_preview()


def slot_tool_finalize():
    try:
        finalize_slot_tool()
    except Exception as e:
        _warn("Finalize error : {}".format(e))


def start_slot_tool(initial_width=1.0, miter_limit=4.0, initial_smooth=0.0, hard_angle_deg=170.0):
    sel = cmds.ls(sl=True, l=True) or []
    if not sel:
        _warn("Selectionne une ou plusieurs courbes NURBS.")
        return
    curves = [obj for obj in sel if _get_curve_shape(obj)]
    if not curves:
        _warn("Aucune courbe NURBS valide dans la selection.")
        return
    _SLOT_TOOL.update({
        "source_curves": curves,
        "preview_nodes": [],
        "width": max(0.001, initial_width),
        "start_width": max(0.001, initial_width),
        "axis_mode": "auto",
        "active": True,
        "miter_limit": max(1.0, miter_limit),
        "smooth_amount": _clamp(initial_smooth, 0.0, 1.0),
        "start_smooth_amount": _clamp(initial_smooth, 0.0, 1.0),
        "hard_angle_deg": _clamp(hard_angle_deg, 45.0, 179.9),
    })
    _delete_preview()
    create_or_update_slot_preview()
    ctx = _SLOT_TOOL["context_name"]
    if cmds.draggerContext(ctx, exists=True):
        cmds.deleteUI(ctx)
    cmds.draggerContext(
        ctx,
        pressCommand='slot_tool_press()',
        dragCommand='slot_tool_drag()',
        releaseCommand='slot_tool_release()',
        finalize='slot_tool_finalize()',
        cursor='crossHair',
        undoMode='step',
        space='screen'
    )
    cmds.setToolTo(ctx)
    _info("Actif : drag gauche=+, drag droit=-, Shift=cycle axe, Ctrl+drag=corner round local, Q=valide.")


def launch_slot_tool_from_ui():
    start_slot_tool(initial_width=20.0, miter_limit=4.0, initial_smooth=0.0, hard_angle_deg=175.0)


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
        try:
            dp = sl.getDagPath(0)
        except TypeError:
            dp = om.MDagPath()
            sl.getDagPath(0, dp)
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


def _prepare_curves_for_ring_fill(curves, spans=32):
    """Convertit les curves non-lineaires en degree 1 avant le ring fill."""
    prepared = []
    linearized_count = 0
    for c in curves or []:
        if not _safe_obj_exists(c):
            continue
        try:
            shapes = cmds.listRelatives(c, shapes=True, type="nurbsCurve", fullPath=True) or []
            if not shapes:
                continue
            degree = int(cmds.getAttr(shapes[0] + ".degree"))
            if degree != 1:
                cmds.rebuildCurve(
                    c,
                    ch=1, rpo=1, rt=0, end=1, kr=0, kcp=0, kep=1, kt=1,
                    s=int(max(3, spans)), d=1, tol=0.01
                )
                linearized_count += 1
            prepared.append(c)
        except Exception as e:
            cmds.warning("[PR] Rebuild curve failed for {}: {}".format(c, e))
    if linearized_count:
        print("[PR] Ring Fill: {} curve(s) linearized (degree 1).".format(linearized_count))
    return prepared


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

    curves = _prepare_curves_for_ring_fill(curves, spans=CIRCLE_SECTIONS_DEFAULT)
    if not curves:
        cmds.warning("[PR] Ring Fill : aucune curve valide apres conversion.")
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
# ============================================================
# BOOLEAN NURBS CURVES
# ============================================================
BOOLEAN_LOFT_OFFSET = 1.0


def boolean_get_cvs_world(curve_transform):
    shapes = cmds.listRelatives(curve_transform, shapes=True, type="nurbsCurve") or []
    if not shapes:
        return []
    shape = shapes[0]
    num_cvs = cmds.getAttr(shape + ".degree") + cmds.getAttr(shape + ".spans")
    pts = []
    for i in range(num_cvs):
        pos = cmds.xform("{}.cv[{}]".format(curve_transform, i), query=True, worldSpace=True, translation=True)
        pts.append((pos[0], pos[1], pos[2]))
    return pts


def boolean_compute_normal_vector(pts):
    n = len(pts)
    nx = ny = nz = 0.0
    for i in range(n):
        cur = pts[i]
        nxt = pts[(i + 1) % n]
        nx += (cur[1] - nxt[1]) * (cur[2] + nxt[2])
        ny += (cur[2] - nxt[2]) * (cur[0] + nxt[0])
        nz += (cur[0] - nxt[0]) * (cur[1] + nxt[1])
    length = (nx * nx + ny * ny + nz * nz) ** 0.5
    if length < 1e-10:
        return (0.0, 1.0, 0.0)
    return (nx / length, ny / length, nz / length)


def boolean_detect_normal_vector(curve_transform):
    pts = boolean_get_cvs_world(curve_transform)
    if len(pts) < 3:
        return (0.0, 1.0, 0.0)
    return boolean_compute_normal_vector(pts)


def boolean_check_is_nurbs_curve(transforms):
    for t in transforms:
        sh = cmds.listRelatives(t, shapes=True, type="nurbsCurve") or []
        if not sh:
            raise RuntimeError("'{}' n'est pas une NURBS curve.".format(t))


def boolean_loft_curve(curve_transform, normal_vec):
    nx, ny, nz = normal_vec
    dx, dy, dz = nx * BOOLEAN_LOFT_OFFSET, ny * BOOLEAN_LOFT_OFFSET, nz * BOOLEAN_LOFT_OFFSET

    dup = cmds.duplicate(curve_transform, returnRootsOnly=True)[0]
    cmds.move(dx, dy, dz, dup, relative=True)

    lofted = cmds.loft(
        curve_transform,
        dup,
        ch=False,
        u=True,
        c=False,
        ar=True,
        d=3,
        ss=1,
        rn=False,
        po=False,
        rsn=True,
    )[0]

    cmds.delete(dup)
    return lofted


def boolean_do_nurbs_boolean(surf_a, surf_b, op_int):
    return cmds.nurbsBoolean(surf_a, surf_b, ch=False, nsf=1, op=op_int)


def boolean_collect_bool_surfaces(result_nodes, loft_a, loft_b):
    exclude = {loft_a, loft_b}
    surfaces = []

    for node in result_nodes:
        if not cmds.objExists(node):
            continue
        if cmds.nodeType(node) == "transform":
            sh = cmds.listRelatives(node, shapes=True, type="nurbsSurface") or []
            if sh and node not in exclude:
                surfaces.append(node)
        elif cmds.nodeType(node) == "nurbsSurface":
            par = (cmds.listRelatives(node, parent=True) or [None])[0]
            if par and par not in exclude:
                surfaces.append(par)

    if not surfaces:
        for t in cmds.ls("nurbsBooleanSurface*", type="transform") or []:
            if t in exclude:
                continue
            sh = cmds.listRelatives(t, shapes=True, type="nurbsSurface") or []
            if sh:
                surfaces.append(t)

    return list(dict.fromkeys(surfaces))


def boolean_nurbs_to_poly(surface):
    return cmds.nurbsToPoly(
        surface,
        mnd=1,
        ch=False,
        f=2,
        pt=1,
        pc=200,
        chr=0.1,
        ft=0.01,
        mel=0.001,
        d=0.1,
        ut=1,
        un=1,
        vt=1,
        vn=1,
        uch=False,
        ucr=False,
        cht=0.2,
        es=False,
        ntr=False,
        mrt=False,
        uss=True,
    )[0]


def boolean_get_origin_border_edges(mesh, normal_vec, curve_a_pts):
    nx, ny, nz = normal_vec

    def proj(pt):
        return pt[0] * nx + pt[1] * ny + pt[2] * nz

    origin_proj = sum(proj(p) for p in curve_a_pts) / len(curve_a_pts)

    cmds.select(mesh)
    cmds.polySelectConstraint(mode=3, type=0x8000, where=1)
    border_edges = cmds.ls(selection=True, flatten=True) or []
    cmds.polySelectConstraint(mode=0)
    cmds.select(clear=True)

    if not border_edges:
        return []

    edge_proj = {}
    for edge in border_edges:
        info = cmds.polyInfo(edge, edgeToVertex=True)
        if not info:
            continue
        tokens = info[0].split()
        vals = []
        for tok in tokens[2:]:
            try:
                vi = int(tok)
                pos = cmds.xform("{}.vtx[{}]".format(mesh, vi), q=True, ws=True, t=True)
                vals.append(proj(pos))
            except Exception:
                pass
        if vals:
            edge_proj[edge] = sum(vals) / len(vals)

    if not edge_proj:
        return border_edges

    closest_val = min(edge_proj.values(), key=lambda v: abs(v - origin_proj))
    tol = BOOLEAN_LOFT_OFFSET * 0.4
    result = [e for e, v in edge_proj.items() if abs(v - closest_val) < tol]
    return result if result else border_edges


def run_boolean_curve(operation_str="union"):
    cmds.undoInfo(openChunk=True, chunkName="boolean_nurbs_{}".format(operation_str))
    try:
        _run_boolean_curve_internal(operation_str)
    finally:
        cmds.undoInfo(closeChunk=True)


def _run_boolean_curve_internal(operation_str):
    sel = cmds.ls(selection=True, type="transform")
    if len(sel) != 2:
        cmds.confirmDialog(title="Boolean NURBS", message="Selectionnez exactement 2 NURBS curves.", button=["OK"])
        return

    curve_a, curve_b = sel[0], sel[1]

    try:
        boolean_check_is_nurbs_curve([curve_a, curve_b])
    except RuntimeError as e:
        cmds.confirmDialog(title="Boolean NURBS - Erreur", message=str(e), button=["OK"])
        return

    pts_a = boolean_get_cvs_world(curve_a)
    normal_vec = boolean_detect_normal_vector(curve_a)
    op_int = {"union": 0, "difference": 1, "intersection": 2}[operation_str]
    temp_nodes = []

    try:
        loft_a = boolean_loft_curve(curve_a, normal_vec)
        loft_b = boolean_loft_curve(curve_b, normal_vec)
        temp_nodes += [loft_a, loft_b]

        bool_result = boolean_do_nurbs_boolean(loft_a, loft_b, op_int)
        bool_surfs = boolean_collect_bool_surfaces(bool_result, loft_a, loft_b)

        if not bool_surfs:
            cmds.confirmDialog(
                title="Boolean NURBS",
                message="Le boolean n'a produit aucune surface.\nVerifiez que les courbes se chevauchent.",
                button=["OK"],
            )
            cmds.delete([n for n in temp_nodes if cmds.objExists(n)])
            return

        for s in bool_surfs:
            par = (cmds.listRelatives(s, parent=True) or [None])[0]
            if par:
                temp_nodes.append(par)
            temp_nodes.append(s)

        meshes = []
        for surf in bool_surfs:
            m = boolean_nurbs_to_poly(surf)
            meshes.append(m)
            temp_nodes.append(m)

        combined = cmds.polyUnite(meshes, ch=False, mergeUVSets=True)[0] if len(meshes) > 1 else meshes[0]
        temp_nodes.append(combined)
        cmds.polyMergeVertex(combined, d=0.001, am=True, ch=False)

        origin_edges = boolean_get_origin_border_edges(combined, normal_vec, pts_a)
        if not origin_edges:
            cmds.confirmDialog(
                title="Boolean NURBS",
                message="Impossible de trouver le bord de la courbe resultante.",
                button=["OK"],
            )
            return

        cmds.select(origin_edges)
        curve_nodes = cmds.polyToCurve(form=2, degree=1, conformToSmoothMeshPreview=True)
        final_curve = cmds.rename(curve_nodes[0], "boolean_{}_#".format(operation_str))
        cmds.xform(final_curve, centerPivots=True)

        cmds.hide(curve_a)
        cmds.hide(curve_b)

        to_del = [n for n in temp_nodes if cmds.objExists(n) and n != final_curve]
        if to_del:
            cmds.delete(to_del)

        for pattern in ["loftedSurface*", "nurbsBooleanSurface*"]:
            for node in cmds.ls(pattern, type="transform") or []:
                if cmds.objExists(node) and node != final_curve:
                    cmds.delete(node)

        cmds.select(final_curve)
    except Exception as e:
        for n in temp_nodes:
            if cmds.objExists(n):
                try:
                    cmds.delete(n)
                except Exception:
                    pass
        cmds.confirmDialog(title="Boolean NURBS - Erreur", message=str(e), button=["OK"])


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


class MirrorAdvancedDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, state=None):
        super(MirrorAdvancedDialog, self).__init__(parent)
        self.setWindowTitle("Mirror Options")
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(320)
        self._preview_sources = list(set(filter(None, [get_curve_transform(s) for s in (cmds.ls(sl=True, long=True) or [])])))
        self._preview_curves = []
        self._preview_update_busy = False
        self._state = dict(MIRROR_ADV_DEFAULTS)
        if isinstance(state, dict):
            self._state.update(state)
        self._build_ui()
        self._load_state()
        self._refresh_preview()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        self.axis_combo = QtWidgets.QComboBox()
        self.axis_combo.addItems(["Auto", "X", "Y", "Z", "-X", "-Y", "-Z"])

        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(["World Center (0)", "Object Center", "Bounding Box", "Grid Snap"])

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignLeft)
        form.addRow("Axis", self.axis_combo)
        form.addRow("Pivot Mode", self.mode_combo)
        layout.addLayout(form)

        self.advanced_btn = QtWidgets.QToolButton()
        self.advanced_btn.setText("Advanced")
        self.advanced_btn.setCheckable(True)
        self.advanced_btn.setChecked(False)
        self.advanced_btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        layout.addWidget(self.advanced_btn)

        self.advanced_widget = QtWidgets.QWidget()
        adv_form = QtWidgets.QFormLayout(self.advanced_widget)
        adv_form.setLabelAlignment(QtCore.Qt.AlignLeft)
        adv_form.setContentsMargins(6, 0, 0, 0)

        self.distance_spin = QtWidgets.QDoubleSpinBox()
        self.distance_spin.setDecimals(6)
        self.distance_spin.setRange(-999999.0, 999999.0)
        self.distance_spin.setSingleStep(0.01)
        self.distance_spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        adv_form.addRow("Distance", self.distance_spin)

        self.reverse_combo = QtWidgets.QComboBox()
        self.reverse_combo.addItems(["Forward", "Reverse"])
        adv_form.addRow("Direction", self.reverse_combo)

        self.keep_original_cb = QtWidgets.QCheckBox("Keep Original")
        adv_form.addRow("", self.keep_original_cb)

        self.hide_if_keep_cb = QtWidgets.QCheckBox("Hide Original if Kept")
        adv_form.addRow("", self.hide_if_keep_cb)

        self.consolidate_cb = QtWidgets.QCheckBox("Consolidate Seam")
        adv_form.addRow("", self.consolidate_cb)

        self.auto_close_cb = QtWidgets.QCheckBox("Auto Close Result")
        adv_form.addRow("", self.auto_close_cb)

        self.seam_tol_spin = QtWidgets.QDoubleSpinBox()
        self.seam_tol_spin.setDecimals(6)
        self.seam_tol_spin.setRange(0.0, 100.0)
        self.seam_tol_spin.setSingleStep(0.0001)
        self.seam_tol_spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        adv_form.addRow("Seam Tolerance", self.seam_tol_spin)

        layout.addWidget(self.advanced_widget)
        self.advanced_widget.setVisible(False)

        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        layout.addWidget(btns)

        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        self.advanced_btn.toggled.connect(self._on_advanced_toggled)
        self.keep_original_cb.toggled.connect(self.hide_if_keep_cb.setEnabled)
        self.axis_combo.currentIndexChanged.connect(self._refresh_preview)
        self.mode_combo.currentIndexChanged.connect(self._refresh_preview)
        self.distance_spin.valueChanged.connect(self._refresh_preview)
        self.reverse_combo.currentIndexChanged.connect(self._refresh_preview)
        self.keep_original_cb.toggled.connect(self._refresh_preview)
        self.hide_if_keep_cb.toggled.connect(self._refresh_preview)
        self.consolidate_cb.toggled.connect(self._refresh_preview)
        self.auto_close_cb.toggled.connect(self._refresh_preview)
        self.seam_tol_spin.valueChanged.connect(self._refresh_preview)

    def _load_state(self):
        axis = str(self._state.get("axis", "auto")).lower()
        axis_map = {"auto": 0, "x": 1, "y": 2, "z": 3, "-x": 4, "-y": 5, "-z": 6}
        self.axis_combo.setCurrentIndex(axis_map.get(axis, 0))

        mode = str(self._state.get("mode", "world"))
        mode_map = {"world": 0, "object": 1, "boundingBox": 2, "grid": 3}
        self.mode_combo.setCurrentIndex(mode_map.get(mode, 0))

        self.distance_spin.setValue(float(self._state.get("distance_offset", 0.0)))
        self.reverse_combo.setCurrentIndex(1 if bool(self._state.get("reverse", False)) else 0)
        self.keep_original_cb.setChecked(bool(self._state.get("keep_original", False)))
        self.hide_if_keep_cb.setChecked(bool(self._state.get("hide_original_if_kept", True)))
        self.hide_if_keep_cb.setEnabled(self.keep_original_cb.isChecked())
        self.consolidate_cb.setChecked(bool(self._state.get("consolidate_seam", True)))
        self.auto_close_cb.setChecked(bool(self._state.get("auto_close", True)))
        self.seam_tol_spin.setValue(float(self._state.get("seam_tol", 0.0001)))

    def settings(self):
        axis_items = ["auto", "x", "y", "z", "-x", "-y", "-z"]
        mode_items = ["world", "object", "boundingBox", "grid"]
        return {
            "axis": axis_items[self.axis_combo.currentIndex()],
            "mode": mode_items[self.mode_combo.currentIndex()],
            "distance_offset": float(self.distance_spin.value()),
            "reverse": self.reverse_combo.currentIndex() == 1,
            "keep_original": self.keep_original_cb.isChecked(),
            "hide_original_if_kept": self.hide_if_keep_cb.isChecked(),
            "consolidate_seam": self.consolidate_cb.isChecked(),
            "auto_close": self.auto_close_cb.isChecked(),
            "seam_tol": float(self.seam_tol_spin.value()),
        }

    def _on_advanced_toggled(self, checked):
        self.advanced_widget.setVisible(checked)
        self.adjustSize()

    def _delete_preview_curves(self):
        if not self._preview_curves:
            return
        to_delete = [c for c in self._preview_curves if _safe_obj_exists(c)]
        self._preview_curves = []
        if to_delete:
            try:
                cmds.delete(to_delete)
            except Exception:
                pass

    def _refresh_preview(self, *_):
        if self._preview_update_busy:
            return
        if not self._preview_sources:
            return
        existing_sources = [c for c in self._preview_sources if _safe_obj_exists(c)]
        if not existing_sources:
            return

        self._preview_update_busy = True
        try:
            self._delete_preview_curves()
            cmds.select(existing_sources, r=True)
            preview_opts = self.settings()
            preview_adv = {
                "distance_offset": preview_opts.get("distance_offset", 0.0),
                "reverse": preview_opts.get("reverse", False),
                "keep_original": True,
                "hide_original_if_kept": False,
                "consolidate_seam": preview_opts.get("consolidate_seam", True),
                "auto_close": preview_opts.get("auto_close", True),
                "seam_tol": preview_opts.get("seam_tol", 0.0001),
            }
            mirror_curve(
                preview_opts.get("axis", "auto"),
                preview_opts.get("mode", "world"),
                advanced=preview_adv,
                quiet=True
            )
            self._preview_curves = list(set(filter(None, [get_curve_transform(s) for s in (cmds.ls(sl=True, long=True) or [])])))
        except Exception as e:
            cmds.warning("[PR] Live mirror preview failed: {}".format(e))
        finally:
            self._preview_update_busy = False

    def accept(self):
        self._delete_preview_curves()
        existing_sources = [c for c in self._preview_sources if _safe_obj_exists(c)]
        if existing_sources:
            cmds.select(existing_sources, r=True)
        super(MirrorAdvancedDialog, self).accept()

    def reject(self):
        self._delete_preview_curves()
        existing_sources = [c for c in self._preview_sources if _safe_obj_exists(c)]
        if existing_sources:
            cmds.select(existing_sources, r=True)
        super(MirrorAdvancedDialog, self).reject()


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
    MIRROR_SLIDER_MIN_MAX = {
        "distance_offset": (-10.0, 10.0),
        "seam_tol": (0.0, 0.1),
    }

    def __init__(self, parent=get_maya_main_window()):
        super(PRCurveToolsUI, self).__init__(parent)

        self._last_mirror_axis = 'auto'
        self._last_mirror_mode = 'world'
        self._mirror_adv_state = dict(MIRROR_ADV_DEFAULTS)
        self._mirror_adv_state.update({"axis": "auto", "mode": "world"})
        self._attach_auto_reverse = True
        self._attach_clean_mode = False
        self._auto_curve_length = AUTO_CURVE_LENGTH_DEFAULT
        self._sweep_controls = {}
        self._circle_sections_default = int(CIRCLE_SECTIONS_DEFAULT)
        self._mirror_preview_sources = []
        self._mirror_preview_curves = []
        self._mirror_preview_busy = False

        self.setObjectName(WINDOW_OBJECT_NAME)
        self.setWindowTitle(WINDOW_TITLE)
        self.setMinimumWidth(310)
        self.setSizeGripEnabled(True)
        self.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.WindowCloseButtonHint)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)

        self._job = None
        self._rebuild_timer = QtCore.QTimer()
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(LIVE_CHAMFER_TIMER_MS)
        self._rebuild_timer.timeout.connect(self._do_live_chamfer)

        self._build_ui()
        self._apply_global_style()
        self.adjustSize()

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
        self.degree_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.degree_slider.setRange(1, 3)
        self.degree_slider.setValue(1)
        self.degree_slider.setSingleStep(1)
        self.degree_slider.setPageStep(1)
        self.degree_slider.setFixedWidth(58)
        self.degree_slider.setTickPosition(QtWidgets.QSlider.NoTicks)
        row.addWidget(self.degree_slider)
        self.degree_value_lbl = QtWidgets.QLabel("1")
        self.degree_value_lbl.setFixedWidth(10)
        row.addWidget(self.degree_value_lbl)
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
        self.circle_btn = PRColorBtn("Circle", tip="Left-click: default 32 | Right-click: presets", bg="#1a2a3a", fg=self.C_DRAW)
        self.circle_btn.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.circle_btn.customContextMenuRequested.connect(self._show_circle_menu)
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

        layout.addWidget(SectionLabel("  BOOLEAN NURBS", self.C_CONVERT))
        row_boolean = QtWidgets.QHBoxLayout()
        row_boolean.setSpacing(4)
        self.bool_union_btn = PRColorBtn("Union", bg="#1d3557", fg="#87b8ff")
        self.bool_diff_btn = PRColorBtn("Difference", bg="#5a2a1a", fg="#ff9a7a")
        self.bool_inter_btn = PRColorBtn("Intersection", bg="#1f4a2b", fg="#86d39a")
        row_boolean.addWidget(self.bool_union_btn)
        row_boolean.addWidget(self.bool_diff_btn)
        row_boolean.addWidget(self.bool_inter_btn)
        layout.addLayout(row_boolean)

        layout.addWidget(SectionLabel("  MIRROR", self.C_MIRROR))
        self._add_mirror_controls(layout)

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

        row_cv_cut = QtWidgets.QHBoxLayout()
        row_cv_cut.setSpacing(4)
        self.split_cv_btn = PRColorBtn("Split CV", bg="#2a1e0a", fg=self.C_CV)
        self.delete_open_cv_btn = PRColorBtn("Delete Open", bg="#2a1e0a", fg=self.C_CV)
        self.slot_btn = PRColorBtn("Slots", bg="#2a1e0a", fg=self.C_CV, tip="Build slot from selected curve(s)")
        row_cv_cut.addWidget(self.split_cv_btn)
        row_cv_cut.addWidget(self.delete_open_cv_btn)
        row_cv_cut.addWidget(self.slot_btn)
        layout.addLayout(row_cv_cut)

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

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
        self.fill_btn = PRColorBtn("Ring Fill",
                                   tip="Left-click: Hide curves | Right-click: Delete curves",
                                   bg="#0f2a2a", fg=self.C_MESH)
        self.fill_btn.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.fill_btn.customContextMenuRequested.connect(self._show_ring_fill_menu)
        self.sweep_btn = PRColorBtn("Sweep Mesh", bg="#0f2a2a", fg=self.C_MESH)
        self.bake_btn = PRColorBtn("Bake", bg="#0f3010", fg="#50ff50", w=56)
        self.bake_btn.setEnabled(False)
        row.addWidget(self.fill_btn, 1)
        row.addWidget(self.sweep_btn, 1)
        row.addWidget(self.bake_btn, 1)
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
        self.draw_btn.clicked.connect(lambda: draw_curve(self.degree_slider.value(), self.snap_chk.isChecked()))
        self.degree_slider.valueChanged.connect(lambda v: self.degree_value_lbl.setText(str(int(v))))
        self.stop_btn.clicked.connect(stop_draw_tool)
        self.close_btn.clicked.connect(close_selected_curves)
        self.circle_btn.clicked.connect(lambda: create_primitive_circle(self._circle_sections_default))
        self.square_btn.clicked.connect(create_primitive_square)
        self.auto_curve_btn.clicked.connect(lambda: auto_create_curve_from_vertex(self.auto_curve_len_spin.value()))

        self.edge_btn.clicked.connect(edge_to_curve)
        self.attach_btn.clicked.connect(lambda: self._do_attach(True, "connect"))
        self.bool_union_btn.clicked.connect(lambda: run_boolean_curve("union"))
        self.bool_diff_btn.clicked.connect(lambda: run_boolean_curve("difference"))
        self.bool_inter_btn.clicked.connect(lambda: run_boolean_curve("intersection"))
        self.mirror_auto_btn.clicked.connect(lambda: self._run_mirror_with_axis("auto"))
        self.mirror_x_btn.clicked.connect(lambda: self._run_mirror_with_axis("x"))
        self.mirror_y_btn.clicked.connect(lambda: self._run_mirror_with_axis("y"))
        self.mirror_z_btn.clicked.connect(lambda: self._run_mirror_with_axis("z"))
        self.mirror_negative_cb.toggled.connect(self._on_mirror_control_changed)
        self.mirror_adv_toggle.toggled.connect(self._on_mirror_advanced_toggled)
        self.mirror_keep_original_cb.toggled.connect(self.mirror_hide_if_keep_cb.setEnabled)
        self.mirror_apply_btn.clicked.connect(self._apply_mirror_from_controls)
        self.mirror_mode_combo.currentIndexChanged.connect(self._on_mirror_control_changed)
        self.mirror_axis_auto_btn.clicked.connect(lambda: self._set_mirror_axis_state("auto", refresh=True))
        self.mirror_axis_x_btn.clicked.connect(lambda: self._set_mirror_axis_state("x", refresh=True))
        self.mirror_axis_y_btn.clicked.connect(lambda: self._set_mirror_axis_state("y", refresh=True))
        self.mirror_axis_z_btn.clicked.connect(lambda: self._set_mirror_axis_state("z", refresh=True))
        self.mirror_axis_nx_btn.clicked.connect(lambda: self._set_mirror_axis_state("-x", refresh=True))
        self.mirror_axis_ny_btn.clicked.connect(lambda: self._set_mirror_axis_state("-y", refresh=True))
        self.mirror_axis_nz_btn.clicked.connect(lambda: self._set_mirror_axis_state("-z", refresh=True))
        self.mirror_distance_spin.valueChanged.connect(self._on_mirror_control_changed)
        self.mirror_seam_spin.valueChanged.connect(self._on_mirror_control_changed)
        self.mirror_reverse_cb.toggled.connect(self._on_mirror_control_changed)
        self.mirror_keep_original_cb.toggled.connect(self._on_mirror_control_changed)
        self.mirror_hide_if_keep_cb.toggled.connect(self._on_mirror_control_changed)
        self.mirror_consolidate_cb.toggled.connect(self._on_mirror_control_changed)
        self.mirror_auto_close_cb.toggled.connect(self._on_mirror_control_changed)
        self.mirror_live_preview_btn.toggled.connect(self._on_mirror_live_toggled)

        self.extrude_btn.clicked.connect(lambda: extrude_cv_along_curve(0))
        self.edit_btn.clicked.connect(edit_curve_cvs)
        self.insert_btn.clicked.connect(insert_cv)
        self.split_cv_btn.clicked.connect(split_curve_at_selected_cvs)
        self.delete_open_cv_btn.clicked.connect(delete_selected_cvs_open)
        self.slot_btn.clicked.connect(self._do_slot_tool)

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
        self._sync_mirror_controls_from_state()
        self._update_bake_button()

    def _do_slot_tool(self):
        try:
            launch_slot_tool_from_ui()
        except Exception as e:
            cmds.warning("[PR] Slot tool failed: {}".format(e))

    def _add_mirror_controls(self, parent_layout):
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
        self.mirror_auto_btn = PRColorBtn("Smart Mirror", tip="Auto axis", bg="#2a1a3a", fg=self.C_MIRROR)
        self.mirror_x_btn = PRColorBtn("X", tip="Mirror X", bg="#2a1a3a", fg=self.C_MIRROR, w=34)
        self.mirror_y_btn = PRColorBtn("Y", tip="Mirror Y", bg="#2a1a3a", fg=self.C_MIRROR, w=34)
        self.mirror_z_btn = PRColorBtn("Z", tip="Mirror Z", bg="#2a1a3a", fg=self.C_MIRROR, w=34)
        self.mirror_negative_cb = QtWidgets.QCheckBox("Neg")
        self.mirror_negative_cb.setToolTip("Use negative axis for quick mirror buttons (X/Y/Z -> -X/-Y/-Z).")
        self.mirror_negative_cb.setMinimumWidth(46)
        row.addWidget(self.mirror_auto_btn)
        row.addWidget(self.mirror_negative_cb)
        row.addWidget(self.mirror_x_btn)
        row.addWidget(self.mirror_y_btn)
        row.addWidget(self.mirror_z_btn)
        parent_layout.addLayout(row)

        self.mirror_adv_toggle = QtWidgets.QToolButton()
        self.mirror_adv_toggle.setText("Advanced")
        self.mirror_adv_toggle.setCheckable(True)
        self.mirror_adv_toggle.setChecked(False)
        self.mirror_adv_toggle.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        parent_layout.addWidget(self.mirror_adv_toggle)

        self.mirror_adv_widget = QtWidgets.QWidget()
        adv_layout = QtWidgets.QVBoxLayout(self.mirror_adv_widget)
        adv_layout.setContentsMargins(6, 0, 0, 0)
        adv_layout.setSpacing(4)

        mode_row = QtWidgets.QHBoxLayout()
        mode_row.setSpacing(4)
        mode_row.addWidget(QtWidgets.QLabel("Pivot"))
        self.mirror_mode_combo = QtWidgets.QComboBox()
        self.mirror_mode_combo.addItems(["World Center (0)", "Object Center", "Bounding Box", "Grid Snap"])
        mode_row.addWidget(self.mirror_mode_combo)
        adv_layout.addLayout(mode_row)

        axis_row = QtWidgets.QHBoxLayout()
        axis_row.setSpacing(4)
        axis_row.addWidget(QtWidgets.QLabel("Axis"))
        self.mirror_axis_auto_btn = PRColorBtn("Auto", tip="Smart axis", bg="#2a1a3a", fg=self.C_MIRROR, w=42)
        self.mirror_axis_x_btn = PRColorBtn("X", tip="Mirror X", bg="#2a1a3a", fg=self.C_MIRROR, w=26)
        self.mirror_axis_y_btn = PRColorBtn("Y", tip="Mirror Y", bg="#2a1a3a", fg=self.C_MIRROR, w=26)
        self.mirror_axis_z_btn = PRColorBtn("Z", tip="Mirror Z", bg="#2a1a3a", fg=self.C_MIRROR, w=26)
        self.mirror_axis_nx_btn = PRColorBtn("-X", tip="Mirror -X", bg="#2a1a3a", fg=self.C_MIRROR, w=30)
        self.mirror_axis_ny_btn = PRColorBtn("-Y", tip="Mirror -Y", bg="#2a1a3a", fg=self.C_MIRROR, w=30)
        self.mirror_axis_nz_btn = PRColorBtn("-Z", tip="Mirror -Z", bg="#2a1a3a", fg=self.C_MIRROR, w=30)
        axis_row.addWidget(self.mirror_axis_auto_btn)
        axis_row.addWidget(self.mirror_axis_x_btn)
        axis_row.addWidget(self.mirror_axis_y_btn)
        axis_row.addWidget(self.mirror_axis_z_btn)
        axis_row.addWidget(self.mirror_axis_nx_btn)
        axis_row.addWidget(self.mirror_axis_ny_btn)
        axis_row.addWidget(self.mirror_axis_nz_btn)
        axis_row.addStretch()
        adv_layout.addLayout(axis_row)

        self.mirror_distance_slider, self.mirror_distance_spin, self.mirror_distance_reset = self._add_mirror_param_slider(
            adv_layout, "Distance", "distance_offset", decimals=4
        )
        self.mirror_seam_slider, self.mirror_seam_spin, self.mirror_seam_reset = self._add_mirror_param_slider(
            adv_layout, "Seam Tol.", "seam_tol", decimals=6
        )

        self.mirror_reverse_cb = QtWidgets.QCheckBox("Reverse")
        self.mirror_keep_original_cb = QtWidgets.QCheckBox("Keep Original")
        self.mirror_hide_if_keep_cb = QtWidgets.QCheckBox("Hide Original if Kept")
        self.mirror_consolidate_cb = QtWidgets.QCheckBox("Consolidate Seam")
        self.mirror_auto_close_cb = QtWidgets.QCheckBox("Auto Close Result")
        adv_layout.addWidget(self.mirror_reverse_cb)
        adv_layout.addWidget(self.mirror_keep_original_cb)
        adv_layout.addWidget(self.mirror_hide_if_keep_cb)
        adv_layout.addWidget(self.mirror_consolidate_cb)
        adv_layout.addWidget(self.mirror_auto_close_cb)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(4)
        self.mirror_live_preview_btn = PRColorBtn("Start Live", bg="#2a2a2a", fg="#909090", w=82)
        self.mirror_live_preview_btn.setCheckable(True)
        self.mirror_live_preview_btn.setChecked(False)
        self.mirror_apply_btn = PRColorBtn("OK", bg="#3a103a", fg=self.C_MIRROR, w=42)
        btn_row.addWidget(self.mirror_live_preview_btn)
        btn_row.addStretch()
        btn_row.addWidget(self.mirror_apply_btn)
        adv_layout.addLayout(btn_row)

        parent_layout.addWidget(self.mirror_adv_widget)
        self.mirror_adv_widget.setVisible(False)

        self.mirror_distance_reset.clicked.connect(
            lambda: self.mirror_distance_spin.setValue(float(MIRROR_ADV_DEFAULTS.get("distance_offset", 0.0)))
        )
        self.mirror_seam_reset.clicked.connect(
            lambda: self.mirror_seam_spin.setValue(float(MIRROR_ADV_DEFAULTS.get("seam_tol", 0.0001)))
        )

    def _add_mirror_param_slider(self, parent_layout, label, key, decimals=3):
        min_val, max_val = self.MIRROR_SLIDER_MIN_MAX.get(key, (0.0, 1.0))
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
        lbl = QtWidgets.QLabel(label)
        lbl.setFixedWidth(62)
        row.addWidget(lbl)

        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        slider.setMinimum(0)
        slider.setMaximum(1000)
        row.addWidget(slider)

        spinbox = QtWidgets.QDoubleSpinBox()
        spinbox.setDecimals(decimals)
        spinbox.setRange(min_val, max_val)
        spinbox.setSingleStep(0.0001 if key == "seam_tol" else 0.01)
        spinbox.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        spinbox.setKeyboardTracking(False)
        spinbox.setFixedWidth(65)
        row.addWidget(spinbox)

        reset_btn = PRColorBtn("Reset", bg="#2a2a2a", fg="#707070", w=46)
        row.addWidget(reset_btn)
        parent_layout.addLayout(row)

        def _slider_to_spin(v):
            ratio = v / 1000.0
            real_val = min_val + ratio * (max_val - min_val)
            spinbox.blockSignals(True)
            spinbox.setValue(real_val)
            spinbox.blockSignals(False)
            self._on_mirror_control_changed()

        def _spin_to_slider(v):
            clamped = max(min_val, min(max_val, float(v)))
            ratio = (clamped - min_val) / (max_val - min_val) if max_val > min_val else 0.0
            slider.blockSignals(True)
            slider.setValue(int(ratio * 1000))
            slider.blockSignals(False)

        slider.valueChanged.connect(_slider_to_spin)
        spinbox.valueChanged.connect(_spin_to_slider)
        spinbox.setValue(float(MIRROR_ADV_DEFAULTS.get(key, min_val)))
        return slider, spinbox, reset_btn

    def _on_mirror_advanced_toggled(self, checked):
        self.mirror_adv_widget.setVisible(checked)
        if checked and self.mirror_live_preview_btn.isChecked():
            self._refresh_mirror_live_preview()
        if not checked:
            self._delete_mirror_preview_curves()
        self.adjustSize()

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

    def _show_circle_menu(self, pos):
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(self._menu_style())

        for sections in CIRCLE_SECTIONS_PRESETS:
            action = menu.addAction("{} sections".format(int(sections)))
            if int(sections) == int(self._circle_sections_default):
                f = action.font()
                f.setBold(True)
                action.setFont(f)
                action.setText("{} sections (Default)".format(int(sections)))

            def _create_with_preset(_checked=False, s=int(sections)):
                self._circle_sections_default = int(s)
                create_primitive_circle(self._circle_sections_default)

            action.triggered.connect(_create_with_preset)

        menu.exec_(self.circle_btn.mapToGlobal(pos))

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

        menu.addAction("Smart Auto-Detect").triggered.connect(lambda: self._run_mirror_preset('auto', 'world'))
        menu.addAction("Mirror Options...").triggered.connect(self._show_mirror_advanced_dialog)
        menu.addSeparator()

        axis_menu = menu.addMenu("Manual Axis")
        axis_menu.setStyleSheet(self._menu_style())
        axis_menu.addAction("X").triggered.connect(lambda: self._run_mirror_preset('x', 'world'))
        axis_menu.addAction("Y").triggered.connect(lambda: self._run_mirror_preset('y', 'world'))
        axis_menu.addAction("Z").triggered.connect(lambda: self._run_mirror_preset('z', 'world'))
        axis_menu.addSeparator()
        axis_menu.addAction("-X").triggered.connect(lambda: self._run_mirror_preset('-x', 'world'))
        axis_menu.addAction("-Y").triggered.connect(lambda: self._run_mirror_preset('-y', 'world'))
        axis_menu.addAction("-Z").triggered.connect(lambda: self._run_mirror_preset('-z', 'world'))

        menu.addSeparator()
        mode_menu = menu.addMenu("Pivot Mode")
        mode_menu.setStyleSheet(self._menu_style())
        mode_menu.addAction("World Center (0)").triggered.connect(lambda: self._last_mirror_with_mode('world'))
        mode_menu.addAction("Object Center").triggered.connect(lambda: self._last_mirror_with_mode('object'))
        mode_menu.addAction("Bounding Box").triggered.connect(lambda: self._last_mirror_with_mode('boundingBox'))
        mode_menu.addAction("Grid Snap").triggered.connect(lambda: self._last_mirror_with_mode('grid'))

        menu.addSeparator()
        menu.addAction("Mirror X (World)").triggered.connect(lambda: self._run_mirror_preset('x', 'world'))
        menu.addAction("Mirror X (BBox)").triggered.connect(lambda: self._run_mirror_preset('x', 'boundingBox'))
        menu.addAction("Mirror -X (World)").triggered.connect(lambda: self._run_mirror_preset('-x', 'world'))
        menu.addAction("Mirror -X (BBox)").triggered.connect(lambda: self._run_mirror_preset('-x', 'boundingBox'))
        menu.addAction("Mirror Z (World)").triggered.connect(lambda: self._run_mirror_preset('z', 'world'))
        menu.addAction("Mirror Z (BBox)").triggered.connect(lambda: self._run_mirror_preset('z', 'boundingBox'))

        menu.exec_(self.mirror_auto_btn.mapToGlobal(pos))

    def _last_mirror_with_mode(self, mode):
        axis = self._last_mirror_axis if self._last_mirror_axis != 'auto' else 'x'
        self._last_mirror_mode = mode
        settings = self._collect_mirror_settings()
        settings["mode"] = mode
        self._mirror_adv_state.update(settings)
        mirror_curve(axis, mode, advanced={
            "distance_offset": settings.get("distance_offset", 0.0),
            "reverse": settings.get("reverse", False),
            "keep_original": settings.get("keep_original", False),
            "hide_original_if_kept": settings.get("hide_original_if_kept", True),
            "consolidate_seam": settings.get("consolidate_seam", True),
            "auto_close": settings.get("auto_close", True),
            "seam_tol": settings.get("seam_tol", 0.0001),
        })

    def _run_mirror_preset(self, axis, mode):
        self._last_mirror_axis = axis
        self._last_mirror_mode = mode
        self._set_mirror_axis_state(axis, refresh=False)
        settings = self._collect_mirror_settings()
        settings["axis"] = axis
        settings["mode"] = mode
        self._mirror_adv_state.update(settings)
        mirror_curve(axis, mode, advanced={
            "distance_offset": settings.get("distance_offset", 0.0),
            "reverse": settings.get("reverse", False),
            "keep_original": settings.get("keep_original", False),
            "hide_original_if_kept": settings.get("hide_original_if_kept", True),
            "consolidate_seam": settings.get("consolidate_seam", True),
            "auto_close": settings.get("auto_close", True),
            "seam_tol": settings.get("seam_tol", 0.0001),
        })

    def _run_mirror_with_axis(self, axis):
        axis = self._axis_with_quick_negative(axis)
        self._set_mirror_axis_state(axis, refresh=False)
        settings = self._collect_mirror_settings()
        settings["axis"] = axis
        self._mirror_adv_state.update(settings)
        self._last_mirror_axis = axis
        self._last_mirror_mode = settings.get("mode", "world")

        adv = {
            "distance_offset": settings.get("distance_offset", 0.0),
            "reverse": settings.get("reverse", False),
            "keep_original": settings.get("keep_original", False),
            "hide_original_if_kept": settings.get("hide_original_if_kept", True),
            "consolidate_seam": settings.get("consolidate_seam", True),
            "auto_close": settings.get("auto_close", True),
            "seam_tol": settings.get("seam_tol", 0.0001),
        }
        mirror_curve(axis, settings.get("mode", "world"), advanced=adv)

    def _axis_with_quick_negative(self, axis):
        axis = str(axis).lower()
        if getattr(self, "mirror_negative_cb", None) and self.mirror_negative_cb.isChecked():
            if axis in ("x", "y", "z"):
                return "-{}".format(axis)
        return axis

    def _show_mirror_advanced_dialog(self):
        dlg = MirrorAdvancedDialog(self, state=self._mirror_adv_state)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return

        settings = dlg.settings()
        self._mirror_adv_state.update(settings)
        self._last_mirror_axis = settings.get("axis", "auto")
        self._last_mirror_mode = settings.get("mode", "world")

        adv = {
            "distance_offset": settings.get("distance_offset", 0.0),
            "reverse": settings.get("reverse", False),
            "keep_original": settings.get("keep_original", False),
            "hide_original_if_kept": settings.get("hide_original_if_kept", True),
            "consolidate_seam": settings.get("consolidate_seam", True),
            "auto_close": settings.get("auto_close", True),
            "seam_tol": settings.get("seam_tol", 0.0001),
        }
        mirror_curve(settings.get("axis", "auto"), settings.get("mode", "world"), advanced=adv)

    def _sync_mirror_controls_from_state(self):
        mode_map = {"world": 0, "object": 1, "boundingBox": 2, "grid": 3}
        self.mirror_mode_combo.setCurrentIndex(mode_map.get(self._mirror_adv_state.get("mode", "world"), 0))
        self._set_mirror_axis_state(self._mirror_adv_state.get("axis", "auto"), refresh=False)
        self.mirror_distance_spin.setValue(float(self._mirror_adv_state.get("distance_offset", 0.0)))
        self.mirror_seam_spin.setValue(float(self._mirror_adv_state.get("seam_tol", 0.0001)))
        self.mirror_reverse_cb.setChecked(bool(self._mirror_adv_state.get("reverse", False)))
        self.mirror_keep_original_cb.setChecked(bool(self._mirror_adv_state.get("keep_original", False)))
        self.mirror_hide_if_keep_cb.setChecked(bool(self._mirror_adv_state.get("hide_original_if_kept", True)))
        self.mirror_hide_if_keep_cb.setEnabled(self.mirror_keep_original_cb.isChecked())
        self.mirror_consolidate_cb.setChecked(bool(self._mirror_adv_state.get("consolidate_seam", True)))
        self.mirror_auto_close_cb.setChecked(bool(self._mirror_adv_state.get("auto_close", True)))

    def _collect_mirror_settings(self):
        mode_items = ["world", "object", "boundingBox", "grid"]
        mode_idx = max(0, min(len(mode_items) - 1, self.mirror_mode_combo.currentIndex()))
        return {
            "axis": self._mirror_axis_from_ui(),
            "mode": mode_items[mode_idx],
            "distance_offset": float(self.mirror_distance_spin.value()),
            "seam_tol": float(self.mirror_seam_spin.value()),
            "reverse": self.mirror_reverse_cb.isChecked(),
            "keep_original": self.mirror_keep_original_cb.isChecked(),
            "hide_original_if_kept": self.mirror_hide_if_keep_cb.isChecked(),
            "consolidate_seam": self.mirror_consolidate_cb.isChecked(),
            "auto_close": self.mirror_auto_close_cb.isChecked(),
        }

    def _mirror_axis_from_ui(self):
        if self.mirror_axis_x_btn.isChecked():
            return "x"
        if self.mirror_axis_y_btn.isChecked():
            return "y"
        if self.mirror_axis_z_btn.isChecked():
            return "z"
        if self.mirror_axis_nx_btn.isChecked():
            return "-x"
        if self.mirror_axis_ny_btn.isChecked():
            return "-y"
        if self.mirror_axis_nz_btn.isChecked():
            return "-z"
        return "auto"

    def _set_mirror_axis_state(self, axis, refresh=False):
        axis = str(axis).lower()
        if axis not in ("auto", "x", "y", "z", "-x", "-y", "-z"):
            axis = "auto"
        axis_buttons = {
            "auto": self.mirror_axis_auto_btn,
            "x": self.mirror_axis_x_btn,
            "y": self.mirror_axis_y_btn,
            "z": self.mirror_axis_z_btn,
            "-x": self.mirror_axis_nx_btn,
            "-y": self.mirror_axis_ny_btn,
            "-z": self.mirror_axis_nz_btn,
        }
        for key, btn in axis_buttons.items():
            btn.setCheckable(True)
            btn.setChecked(key == axis)
        self._last_mirror_axis = axis
        self._mirror_adv_state["axis"] = axis
        if refresh:
            self._on_mirror_control_changed()

    def _delete_mirror_preview_curves(self):
        if not self._mirror_preview_curves:
            return
        to_delete = [c for c in self._mirror_preview_curves if _safe_obj_exists(c)]
        self._mirror_preview_curves = []
        if to_delete:
            try:
                cmds.delete(to_delete)
            except Exception:
                pass

    def _on_mirror_control_changed(self, *_):
        if not hasattr(self, "mirror_live_preview_btn"):
            return
        if self.mirror_live_preview_btn.isChecked() and self.mirror_adv_toggle.isChecked():
            self._refresh_mirror_live_preview()

    def _on_mirror_live_toggled(self, checked):
        self.mirror_live_preview_btn.setText("Stop Live" if checked else "Start Live")
        if checked:
            sel_curves = list(set(filter(None, [get_curve_transform(s) for s in (cmds.ls(sl=True, long=True) or [])])))
            if sel_curves:
                self._mirror_preview_sources = sel_curves
            self._refresh_mirror_live_preview()
        else:
            self._delete_mirror_preview_curves()

    def _refresh_mirror_live_preview(self):
        if self._mirror_preview_busy:
            return
        if not self.mirror_adv_toggle.isChecked():
            return
        if not self._mirror_preview_sources:
            sel_curves = list(set(filter(None, [get_curve_transform(s) for s in (cmds.ls(sl=True, long=True) or [])])))
            if sel_curves:
                self._mirror_preview_sources = sel_curves
        sources = [c for c in self._mirror_preview_sources if _safe_obj_exists(c)]
        if not sources:
            return

        self._mirror_preview_busy = True
        try:
            self._delete_mirror_preview_curves()
            cmds.select(sources, r=True)
            settings = self._collect_mirror_settings()
            axis = settings.get("axis", "auto")
            mirror_curve(axis, settings.get("mode", "world"), advanced={
                "distance_offset": settings.get("distance_offset", 0.0),
                "reverse": settings.get("reverse", False),
                "keep_original": True,
                "hide_original_if_kept": False,
                "consolidate_seam": settings.get("consolidate_seam", True),
                "auto_close": settings.get("auto_close", True),
                "seam_tol": settings.get("seam_tol", 0.0001),
            }, quiet=True)
            selected_after = list(set(filter(None, [get_curve_transform(s) for s in (cmds.ls(sl=True, long=True) or [])])))
            source_set = set(sources)
            self._mirror_preview_curves = [c for c in selected_after if c not in source_set]
            cmds.select(sources, r=True)
        except Exception as e:
            cmds.warning("[PR] Live mirror preview failed: {}".format(e))
        finally:
            self._mirror_preview_busy = False

    def _apply_mirror_from_controls(self):
        self._delete_mirror_preview_curves()
        sources = [c for c in self._mirror_preview_sources if _safe_obj_exists(c)]
        if sources:
            cmds.select(sources, r=True)
        axis = self._collect_mirror_settings().get("axis", "auto")
        self._run_mirror_with_axis(axis)

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
            was_visible = self.sweep_settings_widget.isVisible()
            self.sweep_settings_widget.setVisible(is_active)
            if was_visible != is_active:
                self._fit_to_content_height()

    def _fit_to_content_height(self):
        try:
            self.layout().activate()
        except Exception:
            pass
        try:
            self.adjustSize()
            self.setFixedHeight(self.sizeHint().height())
        except Exception:
            pass

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
        self._delete_mirror_preview_curves()

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
