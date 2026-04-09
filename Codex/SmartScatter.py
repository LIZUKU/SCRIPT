# -*- coding: utf-8 -*-
"""
Smart Scatter Tool for Maya 2026
--------------------------------
Features
- Scatter on:
    * Mesh object (surface)
    * Face selection
    * Edge selection
    * Vertex selection
    * Curve
- Live preview
- Instances or duplicates
- Random seed
- Random position / rotation / scale
- Align to normals / tangents
- Density / count
- Simple collision-like spacing using min distance rejection
- Multiple source objects support
- Dynamic slider UI with compact +/- steppers
- Optional slope-based scale modulation
- Keep upright option
- World up axis choice
- Preview / Bake / Clear

Tested logic target: Maya Python 2026
Qt: PySide2 or PySide6

Usage
-----
1. Select scatter source object(s), click "Set Sources"
2. Select target mesh / components / curve, click "Set Target"
3. Adjust settings
4. Click "Preview"
5. Click "Bake" when satisfied

Notes
-----
- Preview objects are recreated on each preview.
- Spacing is approximate and based on world-space rejection.
- For very dense scatters, preview can become slower.
- This is a strong production base intended to be expanded.
"""

from __future__ import annotations

import json
import math
import random
import time
import traceback

import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.OpenMayaUI as omui

try:
    from PySide6 import QtCore, QtWidgets
    from shiboken6 import wrapInstance, isValid
except ImportError:
    from PySide2 import QtCore, QtWidgets
    from shiboken2 import wrapInstance, isValid


WINDOW_NAME = "SmartScatterTool2026"
WINDOW_TITLE = "Smart Scatter Tool"
PREVIEW_GROUP = "SmartScatter_preview_GRP"
RESULT_GROUP = "SmartScatter_result_GRP"
ACCENT = "#e05a5a"
BG = "#2d2d2d"
PANEL = "#353535"
FIELD = "#252525"
TEXT = "#c8c8c8"
PRESET_VERSION = 1
DEFAULT_PREVIEW_CAP = 2000
DEFAULT_SAMPLE_BATCH_SIZE = 64


# ============================================================
# MAYA / QT HELPERS
# ============================================================
def maya_main_window():
    ptr = omui.MQtUtil.mainWindow()
    if ptr is None:
        return None
    return wrapInstance(int(ptr), QtWidgets.QWidget)


def safe_delete(node):
    try:
        if node and cmds.objExists(node):
            cmds.delete(node)
    except Exception:
        pass


def obj_exists(node):
    try:
        return bool(node) and cmds.objExists(node)
    except Exception:
        return False


def clamp(v, vmin, vmax):
    return max(vmin, min(vmax, v))


def lerp(a, b, t):
    return a + (b - a) * t


# ============================================================
# VECTOR HELPERS
# ============================================================
def v_add(a, b):
    return om.MVector(a[0] + b[0], a[1] + b[1], a[2] + b[2])


def v_sub(a, b):
    return om.MVector(a[0] - b[0], a[1] - b[1], a[2] - b[2])


def v_mul(v, s):
    return om.MVector(v[0] * s, v[1] * s, v[2] * s)


def v_len(v):
    return math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z)


def v_norm(v):
    l = v.length()
    if l < 1e-8:
        return om.MVector(0.0, 1.0, 0.0)
    return v / l


def pick_non_parallel_up(nrm, preferred=(0.0, 1.0, 0.0)):
    up = om.MVector(*preferred)
    if abs(nrm.normal() * up.normal()) > 0.98:
        up = om.MVector(1.0, 0.0, 0.0)
        if abs(nrm.normal() * up.normal()) > 0.98:
            up = om.MVector(0.0, 0.0, 1.0)
    return up


def build_matrix_from_axes(x_axis=None, y_axis=None, z_axis=None, pos=(0.0, 0.0, 0.0)):
    x = om.MVector(*(x_axis or (1.0, 0.0, 0.0))).normal()
    y = om.MVector(*(y_axis or (0.0, 1.0, 0.0))).normal()
    z = om.MVector(*(z_axis or (0.0, 0.0, 1.0))).normal()
    p = om.MPoint(*pos)

    return om.MMatrix([
        x.x, x.y, x.z, 0.0,
        y.x, y.y, y.z, 0.0,
        z.x, z.y, z.z, 0.0,
        p.x, p.y, p.z, 1.0,
    ])


def matrix_to_list(m):
    return [m[i] for i in range(16)]


def matrix_axes(m):
    return (
        om.MVector(m[0], m[1], m[2]),
        om.MVector(m[4], m[5], m[6]),
        om.MVector(m[8], m[9], m[10]),
    )


def rotation_matrix_x(angle_deg):
    a = math.radians(angle_deg)
    c = math.cos(a)
    s = math.sin(a)
    return om.MMatrix([
        1, 0, 0, 0,
        0, c, s, 0,
        0, -s, c, 0,
        0, 0, 0, 1,
    ])


def rotation_matrix_y(angle_deg):
    a = math.radians(angle_deg)
    c = math.cos(a)
    s = math.sin(a)
    return om.MMatrix([
        c, 0, -s, 0,
        0, 1, 0, 0,
        s, 0, c, 0,
        0, 0, 0, 1,
    ])


def rotation_matrix_z(angle_deg):
    a = math.radians(angle_deg)
    c = math.cos(a)
    s = math.sin(a)
    return om.MMatrix([
        c, s, 0, 0,
        -s, c, 0, 0,
        0, 0, 1, 0,
        0, 0, 0, 1,
    ])


def compose_random_rotation(rx, ry, rz):
    return rotation_matrix_x(rx) * rotation_matrix_y(ry) * rotation_matrix_z(rz)


# ============================================================
# DAG / API HELPERS
# ============================================================
def get_dag_path(node):
    sel = om.MSelectionList()
    sel.add(node)
    return sel.getDagPath(0)


def get_shape(node, shape_type=None):
    if not obj_exists(node):
        return None
    shapes = cmds.listRelatives(node, s=True, ni=True, f=True) or []
    if not shape_type:
        return shapes[0] if shapes else None
    for shp in shapes:
        if cmds.nodeType(shp) == shape_type:
            return shp
    return None


def is_mesh_transform(node):
    return bool(get_shape(node, "mesh"))


def is_curve_transform(node):
    return bool(get_shape(node, "nurbsCurve"))


def world_bbox_size(node):
    try:
        bb = cmds.exactWorldBoundingBox(node)
        return (
            abs(bb[3] - bb[0]),
            abs(bb[4] - bb[1]),
            abs(bb[5] - bb[2]),
        )
    except Exception:
        return (1.0, 1.0, 1.0)


def estimate_source_radius(node):
    sx, sy, sz = world_bbox_size(node)
    return max(0.001, max(sx, sy, sz) * 0.5)


def source_local_bbox(node):
    shapes = cmds.listRelatives(node, s=True, ni=True, f=True) or []
    mins = []
    maxs = []
    for shp in shapes:
        try:
            bb_min = cmds.getAttr("{}.boundingBoxMin".format(shp))[0]
            bb_max = cmds.getAttr("{}.boundingBoxMax".format(shp))[0]
            mins.append(bb_min)
            maxs.append(bb_max)
        except Exception:
            continue

    if not mins or not maxs:
        return (-0.5, -0.5, -0.5), (0.5, 0.5, 0.5)

    bb_min = (
        min(v[0] for v in mins),
        min(v[1] for v in mins),
        min(v[2] for v in mins),
    )
    bb_max = (
        max(v[0] for v in maxs),
        max(v[1] for v in maxs),
        max(v[2] for v in maxs),
    )
    return bb_min, bb_max


class SpatialHash(object):
    def __init__(self, cell_size=1.0, use_2d=False):
        self.cell_size = max(0.001, float(cell_size))
        self.use_2d = bool(use_2d)
        self.cells = {}

    def _key(self, pos):
        if self.use_2d:
            return (
                int(math.floor(pos.x / self.cell_size)),
                int(math.floor(pos.z / self.cell_size)),
            )
        return (
            int(math.floor(pos.x / self.cell_size)),
            int(math.floor(pos.y / self.cell_size)),
            int(math.floor(pos.z / self.cell_size)),
        )

    def add(self, pos, radius):
        key = self._key(pos)
        self.cells.setdefault(key, []).append((om.MVector(pos), float(radius)))

    def nearby(self, pos):
        key = self._key(pos)
        if self.use_2d:
            kx, kz = key
            for dx in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for item in self.cells.get((kx + dx, kz + dz), []):
                        yield item
        else:
            kx, ky, kz = key
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        for item in self.cells.get((kx + dx, ky + dy, kz + dz), []):
                            yield item


# ============================================================
# SAMPLING
# ============================================================
class ScatterSample(object):
    def __init__(self, position, normal=None, tangent=None, meta=None):
        self.position = om.MVector(*position)
        self.normal = om.MVector(*(normal or (0.0, 1.0, 0.0)))
        self.tangent = om.MVector(*(tangent or (1.0, 0.0, 0.0)))
        self.meta = meta or {}


class TargetSampler(object):
    def __init__(self, seed=1):
        self.random = random.Random(seed)

    def sample(self, count):
        return []


class MeshSurfaceSampler(TargetSampler):
    def __init__(self, mesh_transform, seed=1):
        super(MeshSurfaceSampler, self).__init__(seed=seed)
        self.mesh_transform = mesh_transform
        self.mesh_shape = get_shape(mesh_transform, "mesh")
        self.dag = get_dag_path(self.mesh_shape)
        self.fn_mesh = om.MFnMesh(self.dag)
        self.points = self.fn_mesh.getPoints(om.MSpace.kWorld)
        self.tri_ids, self.tri_verts = self.fn_mesh.getTriangles()
        self.tris = []
        self.areas = []
        self.total_area = 0.0
        self._build_triangle_cache()

    def _build_triangle_cache(self):
        cursor = 0
        for poly_id, tri_count in enumerate(self.tri_ids):
            for local_tri in range(tri_count):
                ids = self.tri_verts[cursor:cursor + 3]
                cursor += 3
                p0 = om.MVector(self.points[ids[0]])
                p1 = om.MVector(self.points[ids[1]])
                p2 = om.MVector(self.points[ids[2]])
                area = ((p1 - p0) ^ (p2 - p0)).length() * 0.5
                if area < 1e-10:
                    continue
                self.tris.append((poly_id, ids[0], ids[1], ids[2], p0, p1, p2))
                self.total_area += area
                self.areas.append(self.total_area)

    def _random_triangle(self):
        if not self.tris or self.total_area <= 1e-10:
            return None
        r = self.random.uniform(0.0, self.total_area)
        import bisect
        idx = bisect.bisect_left(self.areas, r)
        return self.tris[min(idx, len(self.tris) - 1)]

    def _random_point_in_triangle(self, p0, p1, p2):
        r1 = math.sqrt(self.random.random())
        r2 = self.random.random()
        a = 1.0 - r1
        b = r1 * (1.0 - r2)
        c = r1 * r2
        return (p0 * a) + (p1 * b) + (p2 * c)

    def sample(self, count):
        out = []
        if not self.tris:
            return out

        for _ in range(count):
            tri = self._random_triangle()
            if not tri:
                continue
            poly_id, _i0, _i1, _i2, p0, p1, p2 = tri
            pos = self._random_point_in_triangle(p0, p1, p2)
            nrm = om.MVector(self.fn_mesh.getPolygonNormal(poly_id, om.MSpace.kWorld))
            tangent = (p1 - p0).normal()
            out.append(ScatterSample(
                position=(pos.x, pos.y, pos.z),
                normal=(nrm.x, nrm.y, nrm.z),
                tangent=(tangent.x, tangent.y, tangent.z),
                meta={"polyId": poly_id}
            ))
        return out


class FaceSampler(TargetSampler):
    def __init__(self, face_components, seed=1):
        super(FaceSampler, self).__init__(seed=seed)
        self.face_components = cmds.filterExpand(face_components, sm=34) or []
        self.entries = []
        self.cumulative = []
        self.total_area = 0.0
        self._build_cache()

    def _build_cache(self):
        by_mesh = {}
        for comp in self.face_components:
            mesh = comp.split(".f[")[0]
            by_mesh.setdefault(mesh, []).append(comp)

        for mesh, comps in by_mesh.items():
            shape = get_shape(mesh, "mesh") if is_mesh_transform(mesh) else mesh
            dag = get_dag_path(shape)
            fn_mesh = om.MFnMesh(dag)
            points = fn_mesh.getPoints(om.MSpace.kWorld)

            for comp in comps:
                idx = int(comp.split(".f[")[-1].rstrip("]"))
                poly_verts = fn_mesh.getPolygonVertices(idx)
                if len(poly_verts) < 3:
                    continue
                p0 = om.MVector(points[poly_verts[0]])
                for i in range(1, len(poly_verts) - 1):
                    p1 = om.MVector(points[poly_verts[i]])
                    p2 = om.MVector(points[poly_verts[i + 1]])
                    area = ((p1 - p0) ^ (p2 - p0)).length() * 0.5
                    if area < 1e-10:
                        continue
                    self.total_area += area
                    self.entries.append((self.total_area, mesh, idx, p0, p1, p2, fn_mesh))
        self.cumulative = [e[0] for e in self.entries]

    def sample(self, count):
        out = []
        if not self.entries or self.total_area <= 1e-10:
            return out

        import bisect
        for _ in range(count):
            r = self.random.uniform(0.0, self.total_area)
            idx = bisect.bisect_left(self.cumulative, r)
            _cum, mesh, face_id, p0, p1, p2, fn_mesh = self.entries[min(idx, len(self.entries) - 1)]

            rr1 = math.sqrt(self.random.random())
            rr2 = self.random.random()
            a = 1.0 - rr1
            b = rr1 * (1.0 - rr2)
            c = rr1 * rr2
            pos = (p0 * a) + (p1 * b) + (p2 * c)
            nrm = om.MVector(fn_mesh.getPolygonNormal(face_id, om.MSpace.kWorld))
            tangent = (p1 - p0).normal()

            out.append(ScatterSample(
                position=(pos.x, pos.y, pos.z),
                normal=(nrm.x, nrm.y, nrm.z),
                tangent=(tangent.x, tangent.y, tangent.z),
                meta={"mesh": mesh, "faceId": face_id}
            ))
        return out


class EdgeSampler(TargetSampler):
    def __init__(self, edge_components, seed=1):
        super(EdgeSampler, self).__init__(seed=seed)
        self.edge_components = cmds.filterExpand(edge_components, sm=32) or []
        self.edges = []
        self.cumulative = []
        self.total_length = 0.0
        self._build_cache()

    def _build_cache(self):
        for comp in self.edge_components:
            mesh = comp.split(".e[")[0]
            shape = get_shape(mesh, "mesh") if is_mesh_transform(mesh) else mesh
            dag = get_dag_path(shape)
            fn_mesh = om.MFnMesh(dag)
            points = fn_mesh.getPoints(om.MSpace.kWorld)
            edge_id = int(comp.split(".e[")[-1].rstrip("]"))
            try:
                it = om.MItMeshEdge(dag)
                it.setIndex(edge_id)
                p0 = om.MVector(it.point(0, om.MSpace.kWorld))
                p1 = om.MVector(it.point(1, om.MSpace.kWorld))
                length = (p1 - p0).length()
                if length < 1e-10:
                    continue
                self.total_length += length
                self.edges.append((self.total_length, mesh, edge_id, p0, p1, fn_mesh))
            except Exception:
                continue
        self.cumulative = [e[0] for e in self.edges]

    def sample(self, count):
        out = []
        if not self.edges or self.total_length <= 1e-10:
            return out

        import bisect
        for _ in range(count):
            r = self.random.uniform(0.0, self.total_length)
            idx = bisect.bisect_left(self.cumulative, r)
            _cum, mesh, edge_id, p0, p1, fn_mesh = self.edges[min(idx, len(self.edges) - 1)]
            t = self.random.random()
            pos = p0 + ((p1 - p0) * t)
            tangent = (p1 - p0).normal()

            normal = om.MVector(0.0, 1.0, 0.0)
            try:
                poly_ids = fn_mesh.getConnectedFaces(edge_id)
                if poly_ids:
                    acc = om.MVector()
                    for pid in poly_ids:
                        acc += om.MVector(fn_mesh.getPolygonNormal(pid, om.MSpace.kWorld))
                    if acc.length() > 1e-8:
                        normal = acc.normal()
            except Exception:
                pass

            out.append(ScatterSample(
                position=(pos.x, pos.y, pos.z),
                normal=(normal.x, normal.y, normal.z),
                tangent=(tangent.x, tangent.y, tangent.z),
                meta={"mesh": mesh, "edgeId": edge_id}
            ))
        return out


class VertexSampler(TargetSampler):
    def __init__(self, vertex_components, seed=1):
        super(VertexSampler, self).__init__(seed=seed)
        self.vertex_components = cmds.filterExpand(vertex_components, sm=31) or []
        self.verts = []
        self._build_cache()

    def _build_cache(self):
        for comp in self.vertex_components:
            mesh = comp.split(".vtx[")[0]
            shape = get_shape(mesh, "mesh") if is_mesh_transform(mesh) else mesh
            dag = get_dag_path(shape)
            fn_mesh = om.MFnMesh(dag)
            idx = int(comp.split(".vtx[")[-1].rstrip("]"))
            try:
                pos = fn_mesh.getPoint(idx, om.MSpace.kWorld)
                normals = fn_mesh.getVertexNormals(False, om.MSpace.kWorld)
                normal = om.MVector(normals[idx]) if idx < len(normals) else om.MVector(0.0, 1.0, 0.0)
                tangent = pick_non_parallel_up(normal) ^ normal
                if tangent.length() < 1e-8:
                    tangent = om.MVector(1.0, 0.0, 0.0)
                self.verts.append((mesh, idx, pos, normal, tangent))
            except Exception:
                continue

    def sample(self, count):
        out = []
        if not self.verts:
            return out

        if count <= len(self.verts):
            picks = self.random.sample(self.verts, count)
        else:
            picks = [self.random.choice(self.verts) for _ in range(count)]

        for mesh, idx, pos, normal, tangent in picks:
            out.append(ScatterSample(
                position=(pos.x, pos.y, pos.z),
                normal=(normal.x, normal.y, normal.z),
                tangent=(tangent.x, tangent.y, tangent.z),
                meta={"mesh": mesh, "vertexId": idx}
            ))
        return out


class CurveSampler(TargetSampler):
    def __init__(self, curve_transform, seed=1, mode="count"):
        super(CurveSampler, self).__init__(seed=seed)
        self.curve_transform = curve_transform
        self.mode = mode

    def sample(self, count):
        out = []
        if not obj_exists(self.curve_transform):
            return out

        count = max(1, int(count))
        for i in range(count):
            if self.mode == "even" and count > 1:
                u = float(i) / float(count - 1)
            else:
                u = self.random.random()

            try:
                pos = cmds.pointOnCurve(self.curve_transform, pr=u, p=True, top=True)
                tangent = cmds.pointOnCurve(self.curve_transform, pr=u, nt=True, top=True)
            except Exception:
                continue

            t = om.MVector(*tangent).normal()
            up = om.MVector(0.0, 1.0, 0.0)
            if abs(t * up) > 0.98:
                up = om.MVector(1.0, 0.0, 0.0)
            side = (t ^ up).normal()
            normal = (side ^ t).normal()

            out.append(ScatterSample(
                position=pos,
                normal=(normal.x, normal.y, normal.z),
                tangent=(t.x, t.y, t.z),
                meta={"u": u}
            ))
        return out


# ============================================================
# SCATTER ENGINE
# ============================================================
class SmartScatterEngine(object):
    def __init__(self):
        self.preview_nodes = []
        self.preview_group = None
        self.source_objects = []
        self.target_data = None
        self.last_result_group = None
        self.last_preview_stats = {}
        self._distribution_cache = []
        self._distribution_signature = None

    def _parse_source_weights(self, settings):
        raw = str(settings.get("source_weights", "") or "").strip()
        if not raw:
            return [1.0] * len(self.source_objects)

        tokens = [t.strip() for t in raw.replace(";", ",").split(",") if t.strip()]
        values = []
        for i in range(len(self.source_objects)):
            try:
                values.append(max(0.0, float(tokens[i])))
            except Exception:
                values.append(1.0)
        if sum(values) <= 1e-8:
            return [1.0] * len(self.source_objects)
        return values

    def _pick_source(self, source_mode, rng, index, source_weights):
        if source_mode == "cycle":
            return self.source_objects[index % len(self.source_objects)]
        if source_mode == "weighted":
            return rng.choices(self.source_objects, weights=source_weights, k=1)[0]
        return rng.choice(self.source_objects)

    def _slope_angle_deg(self, normal, world_up):
        dot = clamp(v_norm(normal) * v_norm(world_up), -1.0, 1.0)
        return math.degrees(math.acos(dot))

    def _passes_slope_filter(self, sample, settings, world_up):
        min_slope = float(settings.get("slope_min_deg", 0.0))
        max_slope = float(settings.get("slope_max_deg", 180.0))
        if max_slope < min_slope:
            min_slope, max_slope = max_slope, min_slope
        slope = self._slope_angle_deg(sample.normal, world_up)
        return min_slope <= slope <= max_slope

    def _slope_scale_factor(self, sample, settings, world_up):
        if not settings.get("enable_slope_scale", False):
            return 1.0
        slope = self._slope_angle_deg(sample.normal, world_up)
        t = clamp(slope / 180.0, 0.0, 1.0)
        smin = float(settings.get("slope_scale_min", 1.0))
        smax = float(settings.get("slope_scale_max", 1.0))
        return max(0.001, lerp(smin, smax, t))

    def clear_preview(self):
        for node in list(self.preview_nodes):
            safe_delete(node)
        self.preview_nodes = []
        safe_delete(self.preview_group)
        self.preview_group = None
        self._distribution_cache = []
        self._distribution_signature = None

    def set_sources(self, sources):
        self.source_objects = []
        for obj in sources:
            if obj_exists(obj) and cmds.nodeType(obj) == "transform":
                shapes = cmds.listRelatives(obj, s=True, ni=True, f=True) or []
                if shapes:
                    self.source_objects.append(obj)

    def set_target_from_selection(self):
        sel = cmds.ls(sl=True, fl=True, long=True) or []
        if not sel:
            raise RuntimeError("Nothing selected for target.")

        first = sel[0]

        face_sel = cmds.filterExpand(sel, sm=34) or []
        edge_sel = cmds.filterExpand(sel, sm=32) or []
        vert_sel = cmds.filterExpand(sel, sm=31) or []

        if face_sel:
            self.target_data = {"type": "faces", "items": face_sel}
            return self.target_data
        if edge_sel:
            self.target_data = {"type": "edges", "items": edge_sel}
            return self.target_data
        if vert_sel:
            self.target_data = {"type": "verts", "items": vert_sel}
            return self.target_data

        transforms = cmds.ls(sl=True, long=True, transforms=True) or []
        if not transforms:
            raise RuntimeError("Select a mesh, curve, or components.")

        node = transforms[0]
        if is_curve_transform(node):
            self.target_data = {"type": "curve", "items": [node]}
            return self.target_data
        if is_mesh_transform(node):
            self.target_data = {"type": "mesh", "items": [node]}
            return self.target_data

        raise RuntimeError("Unsupported target selection.")

    def _build_sampler(self, settings):
        seed = int(settings.get("seed", 1))
        target_type = self.target_data["type"]
        items = self.target_data["items"]

        if target_type == "mesh":
            return MeshSurfaceSampler(items[0], seed=seed)
        if target_type == "faces":
            return FaceSampler(items, seed=seed)
        if target_type == "edges":
            return EdgeSampler(items, seed=seed)
        if target_type == "verts":
            return VertexSampler(items, seed=seed)
        if target_type == "curve":
            return CurveSampler(items[0], seed=seed, mode=settings.get("curve_mode", "count"))
        raise RuntimeError("Invalid target type: {}".format(target_type))

    def _build_transform_from_sample(self, sample, settings, rng):
        align_mode = settings.get("align_mode", "normal")
        keep_upright = settings.get("keep_upright", False)
        world_up_axis = settings.get("world_up_axis", "y")

        if world_up_axis == "x":
            world_up = om.MVector(1.0, 0.0, 0.0)
        elif world_up_axis == "z":
            world_up = om.MVector(0.0, 0.0, 1.0)
        else:
            world_up = om.MVector(0.0, 1.0, 0.0)

        normal = v_norm(sample.normal)
        tangent = v_norm(sample.tangent)

        if align_mode == "world":
            y_axis = world_up
            x_axis = om.MVector(1.0, 0.0, 0.0)
            z_axis = x_axis ^ y_axis
            if z_axis.length() < 1e-8:
                z_axis = om.MVector(0.0, 0.0, 1.0)
            z_axis.normalize()
            x_axis = y_axis ^ z_axis
            x_axis.normalize()
        elif align_mode == "tangent":
            x_axis = tangent
            if keep_upright:
                y_axis = world_up
                z_axis = (x_axis ^ y_axis)
                if z_axis.length() < 1e-8:
                    y_axis = pick_non_parallel_up(x_axis)
                    z_axis = (x_axis ^ y_axis)
                z_axis.normalize()
                y_axis = (z_axis ^ x_axis).normal()
            else:
                y_axis = pick_non_parallel_up(x_axis, preferred=(0.0, 1.0, 0.0))
                z_axis = (x_axis ^ y_axis).normal()
                y_axis = (z_axis ^ x_axis).normal()
        else:
            y_axis = normal
            if keep_upright:
                fwd = tangent if tangent.length() > 1e-8 else om.MVector(1.0, 0.0, 0.0)
                x_axis = (world_up ^ y_axis)
                if x_axis.length() < 1e-8:
                    x_axis = (fwd ^ y_axis)
                if x_axis.length() < 1e-8:
                    x_axis = pick_non_parallel_up(y_axis)
                x_axis.normalize()
                z_axis = (x_axis ^ y_axis).normal()
                x_axis = (y_axis ^ z_axis).normal()
            else:
                x_axis = tangent if tangent.length() > 1e-8 else (pick_non_parallel_up(y_axis) ^ y_axis)
                if x_axis.length() < 1e-8:
                    x_axis = om.MVector(1.0, 0.0, 0.0)
                x_axis.normalize()
                z_axis = (x_axis ^ y_axis).normal()
                x_axis = (y_axis ^ z_axis).normal()

        pos = om.MVector(sample.position)

        # World-space offset/jitter
        pos_jitter = om.MVector(
            rng.uniform(-settings["rand_pos_x"], settings["rand_pos_x"]),
            rng.uniform(-settings["rand_pos_y"], settings["rand_pos_y"]),
            rng.uniform(-settings["rand_pos_z"], settings["rand_pos_z"]),
        )
        pos += pos_jitter

        base_offset = om.MVector(settings["offset_x"], settings["offset_y"], settings["offset_z"])
        pos += base_offset

        # Local-space offset/jitter along the oriented basis
        local_offset = v_mul(x_axis, settings["local_offset_x"]) + v_mul(y_axis, settings["local_offset_y"]) + v_mul(z_axis, settings["local_offset_z"])
        local_jitter = (
            v_mul(x_axis, rng.uniform(-settings["rand_local_x"], settings["rand_local_x"]))
            + v_mul(y_axis, rng.uniform(-settings["rand_local_y"], settings["rand_local_y"]))
            + v_mul(z_axis, rng.uniform(-settings["rand_local_z"], settings["rand_local_z"]))
        )
        pos += local_offset + local_jitter

        base_m = build_matrix_from_axes(
            x_axis=(x_axis.x, x_axis.y, x_axis.z),
            y_axis=(y_axis.x, y_axis.y, y_axis.z),
            z_axis=(z_axis.x, z_axis.y, z_axis.z),
            pos=(pos.x, pos.y, pos.z)
        )

        yaw_only = settings.get("yaw_only", False)
        rx = settings["base_rot_x"] + (0.0 if yaw_only else rng.uniform(-settings["rand_rot_x"], settings["rand_rot_x"]))
        ry = settings["base_rot_y"] + rng.uniform(-settings["rand_rot_y"], settings["rand_rot_y"])
        rz = settings["base_rot_z"] + (0.0 if yaw_only else rng.uniform(-settings["rand_rot_z"], settings["rand_rot_z"]))

        rot_m = compose_random_rotation(rx, ry, rz)
        final_m = rot_m * base_m
        return final_m, pos, world_up

    def _passes_spacing(self, pos, src_radius, accepted_positions, min_dist, spacing_mode="sphere_radius"):
        if min_dist <= 0.0:
            return True
        for p, other_radius in accepted_positions:
            pair_min_dist = max(min_dist, src_radius + other_radius)
            d = pos - p
            if spacing_mode == "footprint_2d":
                dist_sq = (d.x * d.x) + (d.z * d.z)
            else:
                dist_sq = (d.x * d.x) + (d.y * d.y) + (d.z * d.z)
            if dist_sq < (pair_min_dist * pair_min_dist):
                return False
        return True

    def _compute_min_dist(self, src_radius, spacing_mul, overlap_mode, overlap_softness):
        if overlap_mode == "ignore":
            return 0.0
        if overlap_mode == "soft":
            softness = clamp(overlap_softness, 0.0, 1.0)
            return src_radius * spacing_mul * (1.0 - softness)
        return src_radius * spacing_mul

    def _source_contact_local(self, src, settings):
        mode = settings.get("contact_mode", "bbox_bottom")
        axis_name = settings.get("contact_axis", "y")
        axis_idx = {"x": 0, "y": 1, "z": 2}.get(axis_name, 1)
        if mode == "pivot":
            return om.MVector(0.0, 0.0, 0.0)
        if mode in ("custom", "custom_local"):
            return om.MVector(
                float(settings.get("contact_custom_x", 0.0)),
                float(settings.get("contact_custom_y", 0.0)),
                float(settings.get("contact_custom_z", 0.0)),
            )
        if mode == "bbox_center":
            bb_min, bb_max = source_local_bbox(src)
            return om.MVector(
                (bb_min[0] + bb_max[0]) * 0.5,
                (bb_min[1] + bb_max[1]) * 0.5,
                (bb_min[2] + bb_max[2]) * 0.5,
            )
        if mode == "bbox_bottom":
            bb_min, bb_max = source_local_bbox(src)
            center = [
                (bb_min[0] + bb_max[0]) * 0.5,
                (bb_min[1] + bb_max[1]) * 0.5,
                (bb_min[2] + bb_max[2]) * 0.5,
            ]
            center[axis_idx] = bb_min[axis_idx]
            return om.MVector(center[0], center[1], center[2])
        return om.MVector(0.0, 0.0, 0.0)

    def _distribution_signature_from_settings(self, settings):
        target_key = tuple(self.target_data["items"]) if self.target_data else ()
        source_key = tuple(self.source_objects)
        return (
            int(settings.get("count", 100)),
            int(settings.get("seed", 1)),
            float(settings.get("spacing_multiplier", 0.0)),
            settings.get("spacing_mode", "sphere_radius"),
            float(settings.get("custom_radius", 0.5)),
            settings.get("overlap_mode", "strict"),
            float(settings.get("overlap_softness", 0.35)),
            settings.get("source_pick_mode", "random"),
            str(settings.get("source_weights", "")),
            settings.get("curve_mode", "count"),
            target_key,
            source_key,
        )

    def preview(self, settings, update_mode="full"):
        if not self.source_objects:
            raise RuntimeError("No source objects set.")
        if not self.target_data:
            raise RuntimeError("No target set.")

        t0 = time.time()
        requested_count = max(1, int(settings.get("count", 100)))
        preview_cap = max(1, int(settings.get("preview_cap", DEFAULT_PREVIEW_CAP)))
        count = min(requested_count, preview_cap)
        source_mode = settings.get("source_pick_mode", "random")
        use_instances = settings.get("use_instances", True)
        rng = random.Random(int(settings.get("seed", 1)))
        desired_signature = self._distribution_signature_from_settings(settings)
        can_transform_only = False

        self.clear_preview()
        sampler = self._build_sampler(settings)

        source_weights = self._parse_source_weights(settings)
        source_radii = {obj: estimate_source_radius(obj) for obj in self.source_objects}
        source_contact_local = {obj: self._source_contact_local(obj, settings) for obj in self.source_objects}
        spacing_mul = max(0.0, float(settings.get("spacing_multiplier", 0.0)))
        overlap_mode = settings.get("overlap_mode", "strict")
        spacing_mode = settings.get("spacing_mode", "sphere_radius")
        custom_radius = max(0.001, float(settings.get("custom_radius", 0.5)))
        overlap_softness = float(settings.get("overlap_softness", 0.35))
        max_tries = max(count * 30, 200)
        accepted_positions = []
        use_2d_hash = spacing_mode == "footprint_2d"
        spacing_hint = max(0.01, spacing_mul * (custom_radius if spacing_mode == "custom_radius" else (sum(source_radii.values()) / max(1, len(source_radii)))))
        spacing_hash = SpatialHash(cell_size=spacing_hint, use_2d=use_2d_hash)
        tries = 0
        created = 0
        rejected_slope = 0
        rejected_spacing = 0

        if not cmds.objExists(PREVIEW_GROUP):
            self.preview_group = cmds.group(em=True, n=PREVIEW_GROUP)
        else:
            self.preview_group = PREVIEW_GROUP

        records = []
        sample_batch = []
        sample_batch_size = max(1, int(settings.get("sample_batch_size", DEFAULT_SAMPLE_BATCH_SIZE)))

        refresh_suspended = False
        try:
            cmds.refresh(suspend=True)
            refresh_suspended = True
        except Exception:
            refresh_suspended = False

        try:
            while created < count and tries < max_tries:
                if not sample_batch:
                    wanted = min(sample_batch_size, max_tries - tries)
                    if wanted <= 0:
                        break
                    sample_batch = sampler.sample(wanted)
                    if not sample_batch:
                        break

                tries += 1
                sample = sample_batch.pop()
                src = self._pick_source(source_mode, rng, created, source_weights)
                rec_seed = rng.randint(0, 10 ** 9)

                src_radius = custom_radius if spacing_mode == "custom_radius" else source_radii.get(src, 0.001)
                dyn_min_dist = self._compute_min_dist(src_radius, spacing_mul, overlap_mode, overlap_softness)

                if source_mode == "cycle":
                    preview_idx = created
                else:
                    preview_idx = len(self.preview_nodes)

                trng = random.Random(rec_seed)
                mat, pos, world_up = self._build_transform_from_sample(sample, settings, trng)
                if not self._passes_slope_filter(sample, settings, world_up):
                    rejected_slope += 1
                    continue
                nearby_positions = spacing_hash.nearby(pos) if spacing_mode != "none" else ()
                if spacing_mode != "none" and not self._passes_spacing(pos, src_radius, nearby_positions, dyn_min_dist, spacing_mode=spacing_mode):
                    rejected_spacing += 1
                    continue

                base_scale = float(settings.get("scale", 1.0))
                uni_rand = float(settings.get("rand_uniform_scale", 0.0))
                rand_xyz = settings.get("rand_non_uniform", False)
                slope_scale = self._slope_scale_factor(sample, settings, world_up)

                if rand_xyz:
                    sx = max(0.001, base_scale + trng.uniform(-settings["rand_scale_x"], settings["rand_scale_x"]))
                    sy = max(0.001, base_scale + trng.uniform(-settings["rand_scale_y"], settings["rand_scale_y"]))
                    sz = max(0.001, base_scale + trng.uniform(-settings["rand_scale_z"], settings["rand_scale_z"]))
                else:
                    s = max(0.001, base_scale + trng.uniform(-uni_rand, uni_rand))
                    sx = sy = sz = s
                sx *= slope_scale
                sy *= slope_scale
                sz *= slope_scale

                normal_push = float(settings.get("offset_along_normal", 0.0))
                if abs(normal_push) > 1e-8:
                    pos += v_norm(sample.normal) * normal_push

                contact_mode = settings.get("contact_mode", "bbox_bottom")
                if contact_mode == "custom_world":
                    pos -= om.MVector(
                        float(settings.get("contact_custom_x", 0.0)),
                        float(settings.get("contact_custom_y", 0.0)),
                        float(settings.get("contact_custom_z", 0.0)),
                    )
                else:
                    contact_local = source_contact_local.get(src, om.MVector())
                    if contact_local.length() > 1e-8:
                        ax, ay, az = matrix_axes(mat)
                        contact_world = (ax * (contact_local.x * sx)) + (ay * (contact_local.y * sy)) + (az * (contact_local.z * sz))
                        pos -= contact_world

                if use_instances:
                    node = cmds.instance(src, n="smartScatter_preview_{:04d}".format(preview_idx + 1))[0]
                else:
                    node = cmds.duplicate(src, rr=True, n="smartScatter_preview_{:04d}".format(preview_idx + 1))[0]
                mat_list = matrix_to_list(mat)
                mat_list[12] = pos.x
                mat_list[13] = pos.y
                mat_list[14] = pos.z
                cmds.xform(node, ws=True, matrix=mat_list)

                cmds.scale(sx, sy, sz, node, absolute=True, objectSpace=True)
                try:
                    cmds.parent(node, self.preview_group)
                except Exception:
                    pass
                self.preview_nodes.append(node)
                records.append({
                    "position": (sample.position.x, sample.position.y, sample.position.z),
                    "normal": (sample.normal.x, sample.normal.y, sample.normal.z),
                    "tangent": (sample.tangent.x, sample.tangent.y, sample.tangent.z),
                    "meta": sample.meta,
                    "src": src,
                    "rand_seed": rec_seed,
                })
                accepted_positions.append((om.MVector(pos), src_radius))
                spacing_hash.add(pos, src_radius)
                created += 1
        finally:
            if refresh_suspended:
                try:
                    cmds.refresh(suspend=False)
                    cmds.refresh(force=True)
                except Exception:
                    pass

        if not self.preview_nodes:
            raise RuntimeError("No valid scatter points found.")

        self.last_preview_stats = {
            "requested": requested_count,
            "created": created,
            "tries": tries,
            "rejected_slope": rejected_slope,
            "rejected_spacing": rejected_spacing,
            "mode": "instances" if use_instances else "duplicates",
            "update_mode": "transform" if can_transform_only else "full",
            "preview_cap": preview_cap,
            "capped": requested_count > count,
            "seconds": time.time() - t0,
        }
        self._distribution_cache = records
        self._distribution_signature = desired_signature
        return self.preview_nodes

    def bake(self, group_result=True):
        if not self.preview_nodes:
            raise RuntimeError("No preview to bake.")

        final_nodes = []
        for i, node in enumerate(list(self.preview_nodes)):
            if not obj_exists(node):
                continue
            try:
                new_name = cmds.rename(node, "smartScatter_{:04d}".format(i + 1))
            except Exception:
                new_name = node
            final_nodes.append(new_name)

        self.preview_nodes = []
        self.preview_group = None

        result = final_nodes
        if group_result and final_nodes:
            if cmds.objExists(RESULT_GROUP):
                idx = 1
                grp_name = RESULT_GROUP
                while cmds.objExists(grp_name):
                    idx += 1
                    grp_name = "{}_{}".format(RESULT_GROUP, idx)
            else:
                grp_name = RESULT_GROUP
            result = cmds.group(final_nodes, n=grp_name)
            self.last_result_group = result
            cmds.select(result, r=True)
        else:
            cmds.select(final_nodes, r=True)
        return result


# ============================================================
# UI
# ============================================================
class ScatterUI(QtWidgets.QDialog):
    _instance = None

    def __init__(self, parent=None):
        if parent is None:
            parent = maya_main_window()
        super(ScatterUI, self).__init__(parent)
        self.setObjectName(WINDOW_NAME)
        self.setWindowTitle(WINDOW_TITLE)
        self.setMinimumWidth(560)
        self.setMinimumHeight(760)
        self.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.WindowCloseButtonHint)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)

        self.engine = SmartScatterEngine()
        self._last_settings_snapshot = None
        self._build_ui()
        self._apply_style()

    # ------------------------------
    # UI Construction
    # ------------------------------
    def _build_ui(self):
        main = QtWidgets.QVBoxLayout(self)
        main.setContentsMargins(10, 10, 10, 10)
        main.setSpacing(8)

        self.status = QtWidgets.QLabel("Ready")
        self.status.setObjectName("statusLabel")
        self.status.setAlignment(QtCore.Qt.AlignCenter)
        main.addWidget(self.status)

        self.tabs = QtWidgets.QTabWidget()
        main.addWidget(self.tabs, 1)

        tab_setup_page, tab_setup = self._create_tab_layout()
        tab_transform_page, tab_transform = self._create_tab_layout()
        tab_scale_page, tab_scale = self._create_tab_layout()
        tab_workflow_page, tab_workflow = self._create_tab_layout()

        # Setup tab ---------------------------------------------------------
        setup_sources_grp, setup_sources = self._section_box("Sources")
        src_row = QtWidgets.QHBoxLayout()
        self.btn_set_sources = QtWidgets.QPushButton("Set Sources From Selection")
        self.btn_set_sources.clicked.connect(self.on_set_sources)
        src_row.addWidget(self.btn_set_sources)
        setup_sources.addLayout(src_row)

        self.sources_label = QtWidgets.QLabel("Sources: -")
        setup_sources.addWidget(self.sources_label)
        tab_setup.addWidget(setup_sources_grp)

        setup_target_grp, setup_target = self._section_box("Target")
        target_row = QtWidgets.QHBoxLayout()
        self.btn_set_target = QtWidgets.QPushButton("Set Target From Selection")
        self.btn_set_target.clicked.connect(self.on_set_target)
        target_row.addWidget(self.btn_set_target)
        setup_target.addLayout(target_row)

        self.target_label = QtWidgets.QLabel("Target: -")
        setup_target.addWidget(self.target_label)
        tab_setup.addWidget(setup_target_grp)

        setup_distribution_grp, setup_distribution = self._section_box("Distribution")
        self.count_spin = self._add_spin(setup_distribution, "Count", 1, 50000, 100)
        self.seed_spin = self._add_spin(setup_distribution, "Seed", 1, 999999, 1)
        self.spacing_mul_spin = self._add_dspin_slider(setup_distribution, "Spacing Mult", 0.0, 10.0, 0.01, 0.25)
        self.spacing_mode_combo = QtWidgets.QComboBox()
        self.spacing_mode_combo.addItems(["sphere_radius", "footprint_2d", "custom_radius", "none"])
        self._add_widget_row(setup_distribution, "Spacing Mode", self.spacing_mode_combo)
        self.custom_radius_spin = self._add_dspin_slider(setup_distribution, "Custom Radius", 0.001, 10000.0, 0.01, 0.5)

        self.source_pick_combo = QtWidgets.QComboBox()
        self.source_pick_combo.addItems(["random", "cycle", "weighted"])
        self._add_widget_row(setup_distribution, "Source Pick", self.source_pick_combo)
        self.source_weights_edit = QtWidgets.QLineEdit()
        self.source_weights_edit.setPlaceholderText("ex: 60,25,15")
        self._add_widget_row(setup_distribution, "Source Weights", self.source_weights_edit)

        self.align_combo = QtWidgets.QComboBox()
        self.align_combo.addItems(["normal", "tangent", "world"])
        self._add_widget_row(setup_distribution, "Align Mode", self.align_combo)

        self.curve_mode_combo = QtWidgets.QComboBox()
        self.curve_mode_combo.addItems(["count", "even"])
        self._add_widget_row(setup_distribution, "Curve Mode", self.curve_mode_combo)

        self.world_up_combo = QtWidgets.QComboBox()
        self.world_up_combo.addItems(["y", "x", "z"])
        self._add_widget_row(setup_distribution, "World Up", self.world_up_combo)

        self.overlap_combo = QtWidgets.QComboBox()
        self.overlap_combo.addItems(["strict", "soft", "ignore"])
        self._add_widget_row(setup_distribution, "Overlap", self.overlap_combo)
        self.overlap_softness = self._add_dspin_slider(setup_distribution, "Overlap Softness", 0.0, 1.0, 0.01, 0.35)
        self.slope_min = self._add_dspin_slider(setup_distribution, "Min Slope°", 0.0, 180.0, 0.1, 0.0)
        self.slope_max = self._add_dspin_slider(setup_distribution, "Max Slope°", 0.0, 180.0, 0.1, 180.0)
        self.contact_mode_combo = QtWidgets.QComboBox()
        self.contact_mode_combo.addItems(["bbox_bottom", "pivot", "bbox_center", "custom_local", "custom_world"])
        self._add_widget_row(setup_distribution, "Contact Mode", self.contact_mode_combo)
        self.contact_axis_combo = QtWidgets.QComboBox()
        self.contact_axis_combo.addItems(["y", "z", "x"])
        self._add_widget_row(setup_distribution, "Contact Axis", self.contact_axis_combo)
        tab_setup.addWidget(setup_distribution_grp)

        # Transform tab -----------------------------------------------------
        transform_offset_grp, transform_offset = self._section_box("Offset Position")
        self.offset_x = self._add_dspin(transform_offset, "Offset X", -100000, 100000, 0.01, 0.0)
        self.offset_y = self._add_dspin(transform_offset, "Offset Y", -100000, 100000, 0.01, 0.0)
        self.offset_z = self._add_dspin(transform_offset, "Offset Z", -100000, 100000, 0.01, 0.0)
        self.local_offset_x = self._add_dspin(transform_offset, "Local Offset X", -100000, 100000, 0.01, 0.0)
        self.local_offset_y = self._add_dspin(transform_offset, "Local Offset Y", -100000, 100000, 0.01, 0.0)
        self.local_offset_z = self._add_dspin(transform_offset, "Local Offset Z", -100000, 100000, 0.01, 0.0)
        self.normal_offset = self._add_dspin(transform_offset, "Offset Along Normal", -100000, 100000, 0.01, 0.0)
        self.contact_custom_x = self._add_dspin(transform_offset, "Contact Custom X", -100000, 100000, 0.01, 0.0)
        self.contact_custom_y = self._add_dspin(transform_offset, "Contact Custom Y", -100000, 100000, 0.01, 0.0)
        self.contact_custom_z = self._add_dspin(transform_offset, "Contact Custom Z", -100000, 100000, 0.01, 0.0)
        tab_transform.addWidget(transform_offset_grp)

        transform_random_pos_grp, transform_random_pos = self._section_box("Random Position")
        self.rand_pos_x = self._add_dspin_slider(transform_random_pos, "Rand Pos X", 0.0, 100000, 0.01, 0.0)
        self.rand_pos_y = self._add_dspin_slider(transform_random_pos, "Rand Pos Y", 0.0, 100000, 0.01, 0.0)
        self.rand_pos_z = self._add_dspin_slider(transform_random_pos, "Rand Pos Z", 0.0, 100000, 0.01, 0.0)
        self.rand_local_x = self._add_dspin_slider(transform_random_pos, "Rand Local X", 0.0, 100000, 0.01, 0.0)
        self.rand_local_y = self._add_dspin_slider(transform_random_pos, "Rand Local Y", 0.0, 100000, 0.01, 0.0)
        self.rand_local_z = self._add_dspin_slider(transform_random_pos, "Rand Local Z", 0.0, 100000, 0.01, 0.0)
        tab_transform.addWidget(transform_random_pos_grp)

        transform_base_rot_grp, transform_base_rot = self._section_box("Base Rotation")
        self.base_rot_x = self._add_dspin(transform_base_rot, "Base Rot X", -360.0, 360.0, 0.1, 0.0)
        self.base_rot_y = self._add_dspin(transform_base_rot, "Base Rot Y", -360.0, 360.0, 0.1, 0.0)
        self.base_rot_z = self._add_dspin(transform_base_rot, "Base Rot Z", -360.0, 360.0, 0.1, 0.0)
        tab_transform.addWidget(transform_base_rot_grp)

        transform_random_rot_grp, transform_random_rot = self._section_box("Random Rotation")
        self.rand_rot_x = self._add_dspin_slider(transform_random_rot, "Rand Rot X", 0.0, 360.0, 0.1, 0.0)
        self.rand_rot_y = self._add_dspin_slider(transform_random_rot, "Rand Rot Y", 0.0, 360.0, 0.1, 0.0)
        self.rand_rot_z = self._add_dspin_slider(transform_random_rot, "Rand Rot Z", 0.0, 360.0, 0.1, 0.0)
        tab_transform.addWidget(transform_random_rot_grp)

        # Scale & Rules tab -------------------------------------------------
        scale_box_grp, scale_box = self._section_box("Scale")
        self.scale_spin = self._add_dspin_slider(scale_box, "Base Scale", 0.001, 1000.0, 0.01, 1.0)
        self.rand_uni_scale = self._add_dspin_slider(scale_box, "Rand Uniform", 0.0, 1000.0, 0.01, 0.1)
        self.rand_scale_x = self._add_dspin_slider(scale_box, "Rand Scale X", 0.0, 1000.0, 0.01, 0.0)
        self.rand_scale_y = self._add_dspin_slider(scale_box, "Rand Scale Y", 0.0, 1000.0, 0.01, 0.0)
        self.rand_scale_z = self._add_dspin_slider(scale_box, "Rand Scale Z", 0.0, 1000.0, 0.01, 0.0)
        self.slope_scale_min = self._add_dspin_slider(scale_box, "Slope Scale Min", 0.001, 10.0, 0.01, 1.0)
        self.slope_scale_max = self._add_dspin_slider(scale_box, "Slope Scale Max", 0.001, 10.0, 0.01, 1.0)
        tab_scale.addWidget(scale_box_grp)

        options_box_grp, options_box = self._section_box("Options")
        self.chk_instance = QtWidgets.QCheckBox("Use Instances")
        self.chk_instance.setChecked(True)
        options_box.addWidget(self.chk_instance)

        self.chk_group = QtWidgets.QCheckBox("Group Result On Bake")
        self.chk_group.setChecked(True)
        options_box.addWidget(self.chk_group)

        self.chk_keep_upright = QtWidgets.QCheckBox("Keep Upright")
        self.chk_keep_upright.setChecked(True)
        options_box.addWidget(self.chk_keep_upright)
        self.chk_yaw_only = QtWidgets.QCheckBox("Yaw Only Random Rotation")
        self.chk_yaw_only.setChecked(True)
        options_box.addWidget(self.chk_yaw_only)

        self.chk_rand_non_uniform = QtWidgets.QCheckBox("Use XYZ Random Scale")
        self.chk_rand_non_uniform.setChecked(False)
        options_box.addWidget(self.chk_rand_non_uniform)
        self.chk_slope_scale = QtWidgets.QCheckBox("Scale By Slope")
        self.chk_slope_scale.setChecked(False)
        options_box.addWidget(self.chk_slope_scale)
        tab_scale.addWidget(options_box_grp)

        # Workflow tab ------------------------------------------------------
        workflow_preview_grp, workflow_preview = self._section_box("Preview")
        self.chk_live_preview = QtWidgets.QCheckBox("Live Preview")
        self.chk_live_preview.setChecked(False)
        workflow_preview.addWidget(self.chk_live_preview)
        self.live_debounce_ms = self._add_spin(workflow_preview, "Live Debounce ms", 50, 5000, 500)

        buttons = QtWidgets.QHBoxLayout()
        self.btn_preview = QtWidgets.QPushButton("Preview")
        self.btn_preview.clicked.connect(self.on_preview)
        buttons.addWidget(self.btn_preview)

        self.btn_bake = QtWidgets.QPushButton("Bake")
        self.btn_bake.clicked.connect(self.on_bake)
        buttons.addWidget(self.btn_bake)

        self.btn_clear = QtWidgets.QPushButton("Clear Preview")
        self.btn_clear.clicked.connect(self.on_clear)
        buttons.addWidget(self.btn_clear)
        workflow_preview.addLayout(buttons)

        preset_row = QtWidgets.QHBoxLayout()
        self.btn_save_preset = QtWidgets.QPushButton("Save Preset")
        self.btn_save_preset.clicked.connect(self.on_save_preset)
        preset_row.addWidget(self.btn_save_preset)
        self.btn_load_preset = QtWidgets.QPushButton("Load Preset")
        self.btn_load_preset.clicked.connect(self.on_load_preset)
        preset_row.addWidget(self.btn_load_preset)
        workflow_preview.addLayout(preset_row)
        tab_workflow.addWidget(workflow_preview_grp)

        tab_workflow.addStretch(1)

        self.btn_close = QtWidgets.QPushButton("Close")
        self.btn_close.clicked.connect(self.close)
        main.addWidget(self.btn_close)

        self.tabs.addTab(tab_setup_page, "Setup")
        self.tabs.addTab(tab_transform_page, "Transform")
        self.tabs.addTab(tab_scale_page, "Scale / Rules")
        self.tabs.addTab(tab_workflow_page, "Preview / Bake")

        self._live_timer = QtCore.QTimer(self)
        self._live_timer.setSingleShot(True)
        self._live_timer.timeout.connect(self.on_preview)
        self._connect_live_preview_controls()

    def _create_tab_layout(self):
        page = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(page)
        lay.setContentsMargins(8, 10, 8, 8)
        lay.setSpacing(8)
        return page, lay

    def _section_box(self, title):
        grp = QtWidgets.QGroupBox(title)
        lay = QtWidgets.QVBoxLayout(grp)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        return grp, lay

    def _apply_style(self):
        self.setStyleSheet("""
            QWidget {
                background-color: %s;
                color: %s;
                font-size: 11px;
            }
            QDialog {
                border: 1px solid #444444;
            }
            QPushButton {
                background-color: %s;
                border: 1px solid #4b4b4b;
                border-radius: 4px;
                padding: 6px;
                min-height: 24px;
            }
            QPushButton:hover {
                border: 1px solid %s;
            }
            QLabel#sectionLabel {
                color: %s;
                font-weight: bold;
                padding-top: 4px;
                border-top: 1px solid #444444;
            }
            QLabel#statusLabel {
                color: %s;
                font-weight: bold;
                padding: 4px;
            }
            QTabWidget::pane {
                border: 1px solid #4a4a4a;
                border-radius: 4px;
                top: -1px;
            }
            QTabBar::tab {
                background: #313131;
                border: 1px solid #474747;
                border-bottom-color: #474747;
                padding: 6px 10px;
                min-width: 90px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background: #3c3c3c;
                border-bottom-color: #3c3c3c;
            }
            QGroupBox {
                border: 1px solid #444444;
                margin-top: 8px;
                padding-top: 8px;
                border-radius: 4px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px;
                color: %s;
                font-weight: bold;
            }
            QSpinBox, QDoubleSpinBox, QComboBox {
                background-color: %s;
                border: 1px solid #444444;
                border-radius: 3px;
                min-height: 22px;
                padding-left: 4px;
            }
            QLineEdit {
                background-color: %s;
                border: 1px solid #444444;
                border-radius: 3px;
                min-height: 22px;
                padding-left: 4px;
            }
            QSlider::groove:horizontal {
                border: 1px solid #4b4b4b;
                background: #202020;
                height: 8px;
                border-radius: 4px;
            }
            QSlider::handle:horizontal {
                background: %s;
                border: 1px solid #111111;
                width: 12px;
                margin: -4px 0;
                border-radius: 6px;
            }
            QCheckBox {
                padding: 2px;
            }
            QToolButton {
                background-color: #3a3a3a;
                border: 1px solid #4b4b4b;
                border-radius: 2px;
                font-size: 9px;
                padding: 0px;
            }
            QToolButton:hover {
                border: 1px solid %s;
            }
        """ % (BG, TEXT, PANEL, ACCENT, ACCENT, ACCENT, ACCENT, FIELD, FIELD, ACCENT, ACCENT))

    def _section_label(self, text):
        lbl = QtWidgets.QLabel(text)
        lbl.setObjectName("sectionLabel")
        return lbl

    def _add_widget_row(self, parent_layout, label, widget):
        row = QtWidgets.QHBoxLayout()
        lbl = QtWidgets.QLabel(label)
        lbl.setFixedWidth(130)
        row.addWidget(lbl)
        row.addWidget(widget)
        parent_layout.addLayout(row)
        return widget

    def _add_spin(self, parent_layout, label, mn, mx, dv):
        row = QtWidgets.QHBoxLayout()
        lbl = QtWidgets.QLabel(label)
        lbl.setFixedWidth(130)
        row.addWidget(lbl)

        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        slider.setMinimumWidth(180)
        row.addWidget(slider, 1)

        w = QtWidgets.QSpinBox()
        w.setRange(mn, mx)
        w.setValue(dv)
        row.addWidget(w)
        parent_layout.addLayout(row)

        self._bind_dynamic_slider(slider, w, mn, mx, step=1.0)
        return w

    def _add_dspin(self, parent_layout, label, mn, mx, step, dv):
        return self._add_dspin_slider(parent_layout, label, mn, mx, step, dv)

    def _add_dspin_slider(self, parent_layout, label, mn, mx, step, dv):
        row = QtWidgets.QHBoxLayout()
        lbl = QtWidgets.QLabel(label)
        lbl.setFixedWidth(130)
        row.addWidget(lbl)

        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        slider.setMinimumWidth(180)
        row.addWidget(slider, 1)

        spin = QtWidgets.QDoubleSpinBox()
        spin.setDecimals(3)
        spin.setSingleStep(step)
        spin.setRange(mn, mx)
        spin.setValue(dv)
        row.addWidget(spin)
        parent_layout.addLayout(row)

        self._bind_dynamic_slider(slider, spin, mn, mx, step)
        return spin

    def _build_micro_stepper(self, spin_widget):
        controls = QtWidgets.QVBoxLayout()
        controls.setSpacing(0)
        controls.setContentsMargins(1, 0, 0, 0)

        btn_plus = QtWidgets.QToolButton()
        btn_plus.setText("+")
        btn_plus.setAutoRaise(True)
        btn_plus.setFixedSize(16, 11)

        btn_minus = QtWidgets.QToolButton()
        btn_minus.setText("-")
        btn_minus.setAutoRaise(True)
        btn_minus.setFixedSize(16, 11)

        btn_plus.clicked.connect(spin_widget.stepUp)
        btn_minus.clicked.connect(spin_widget.stepDown)

        controls.addWidget(btn_plus)
        controls.addWidget(btn_minus)
        return controls

    def _bind_dynamic_slider(self, slider, spin_widget, mn, mx, step):
        scale = max(1, int(round(1.0 / max(step, 1e-6))))
        slider_window = 500
        full_min = int(round(mn * scale))
        full_max = int(round(mx * scale))

        def _set_window(center_value):
            center = int(round(center_value * scale))
            if full_max - full_min <= slider_window:
                lo, hi = full_min, full_max
            else:
                half = slider_window // 2
                lo = max(full_min, center - half)
                hi = min(full_max, center + half)
                width = hi - lo
                if width < slider_window:
                    if lo == full_min:
                        hi = min(full_max, lo + slider_window)
                    elif hi == full_max:
                        lo = max(full_min, hi - slider_window)
            slider.blockSignals(True)
            slider.setRange(lo, hi)
            slider.setValue(clamp(center, lo, hi))
            slider.blockSignals(False)

        def _on_spin_changed(val):
            _set_window(val)

        def _on_slider_changed(val):
            spin_widget.blockSignals(True)
            spin_widget.setValue(float(val) / float(scale))
            spin_widget.blockSignals(False)
            spin_widget.valueChanged.emit(spin_widget.value())

        _set_window(spin_widget.value())
        spin_widget.valueChanged.connect(_on_spin_changed)
        slider.valueChanged.connect(_on_slider_changed)

    def _connect_live_preview_controls(self):
        widgets = [
            self.count_spin, self.seed_spin, self.spacing_mul_spin,
            self.spacing_mode_combo, self.custom_radius_spin,
            self.source_pick_combo, self.source_weights_edit, self.align_combo, self.curve_mode_combo,
            self.world_up_combo, self.overlap_combo, self.overlap_softness, self.slope_min, self.slope_max, self.contact_mode_combo, self.contact_axis_combo,
            self.offset_x, self.offset_y, self.offset_z,
            self.local_offset_x, self.local_offset_y, self.local_offset_z, self.normal_offset,
            self.contact_custom_x, self.contact_custom_y, self.contact_custom_z,
            self.rand_pos_x, self.rand_pos_y, self.rand_pos_z,
            self.rand_local_x, self.rand_local_y, self.rand_local_z,
            self.base_rot_x, self.base_rot_y, self.base_rot_z,
            self.rand_rot_x, self.rand_rot_y, self.rand_rot_z,
            self.scale_spin, self.rand_uni_scale, self.rand_scale_x, self.rand_scale_y, self.rand_scale_z,
            self.slope_scale_min, self.slope_scale_max,
            self.chk_instance, self.chk_keep_upright, self.chk_yaw_only, self.chk_rand_non_uniform, self.chk_slope_scale,
        ]
        for w in widgets:
            if hasattr(w, "valueChanged"):
                w.valueChanged.connect(self.on_live_param_changed)
            elif hasattr(w, "currentTextChanged"):
                w.currentTextChanged.connect(self.on_live_param_changed)
            elif hasattr(w, "textChanged"):
                w.textChanged.connect(self.on_live_param_changed)
            elif hasattr(w, "toggled"):
                w.toggled.connect(self.on_live_param_changed)
        self.chk_live_preview.toggled.connect(self.on_live_param_changed)
        self.live_debounce_ms.valueChanged.connect(self.on_live_param_changed)

    # ------------------------------
    # State display
    # ------------------------------
    def _refresh_source_label(self):
        if not self.engine.source_objects:
            self.sources_label.setText("Sources: -")
            return
        short = [s.split("|")[-1] for s in self.engine.source_objects[:5]]
        more = "" if len(self.engine.source_objects) <= 5 else " ... (+{})".format(len(self.engine.source_objects) - 5)
        self.sources_label.setText("Sources [{}]: {}{}".format(len(self.engine.source_objects), ", ".join(short), more))

    def _refresh_target_label(self):
        data = self.engine.target_data
        if not data:
            self.target_label.setText("Target: -")
            return
        if data["type"] in ("mesh", "curve"):
            name = data["items"][0].split("|")[-1]
            self.target_label.setText("Target: {} -> {}".format(data["type"], name))
        else:
            self.target_label.setText("Target: {} [{}]".format(data["type"], len(data["items"])))

    def _settings(self):
        return {
            "count": self.count_spin.value(),
            "seed": self.seed_spin.value(),
            "spacing_multiplier": self.spacing_mul_spin.value(),
            "spacing_mode": self.spacing_mode_combo.currentText(),
            "custom_radius": self.custom_radius_spin.value(),
            "source_pick_mode": self.source_pick_combo.currentText(),
            "source_weights": self.source_weights_edit.text(),
            "align_mode": self.align_combo.currentText(),
            "curve_mode": self.curve_mode_combo.currentText(),
            "world_up_axis": self.world_up_combo.currentText(),
            "overlap_mode": self.overlap_combo.currentText(),
            "overlap_softness": self.overlap_softness.value(),
            "slope_min_deg": self.slope_min.value(),
            "slope_max_deg": self.slope_max.value(),
            "contact_mode": self.contact_mode_combo.currentText(),
            "contact_axis": self.contact_axis_combo.currentText(),
            "offset_x": self.offset_x.value(),
            "offset_y": self.offset_y.value(),
            "offset_z": self.offset_z.value(),
            "local_offset_x": self.local_offset_x.value(),
            "local_offset_y": self.local_offset_y.value(),
            "local_offset_z": self.local_offset_z.value(),
            "offset_along_normal": self.normal_offset.value(),
            "contact_custom_x": self.contact_custom_x.value(),
            "contact_custom_y": self.contact_custom_y.value(),
            "contact_custom_z": self.contact_custom_z.value(),
            "rand_pos_x": self.rand_pos_x.value(),
            "rand_pos_y": self.rand_pos_y.value(),
            "rand_pos_z": self.rand_pos_z.value(),
            "rand_local_x": self.rand_local_x.value(),
            "rand_local_y": self.rand_local_y.value(),
            "rand_local_z": self.rand_local_z.value(),
            "base_rot_x": self.base_rot_x.value(),
            "base_rot_y": self.base_rot_y.value(),
            "base_rot_z": self.base_rot_z.value(),
            "rand_rot_x": self.rand_rot_x.value(),
            "rand_rot_y": self.rand_rot_y.value(),
            "rand_rot_z": self.rand_rot_z.value(),
            "scale": self.scale_spin.value(),
            "rand_uniform_scale": self.rand_uni_scale.value(),
            "rand_scale_x": self.rand_scale_x.value(),
            "rand_scale_y": self.rand_scale_y.value(),
            "rand_scale_z": self.rand_scale_z.value(),
            "slope_scale_min": self.slope_scale_min.value(),
            "slope_scale_max": self.slope_scale_max.value(),
            "rand_non_uniform": self.chk_rand_non_uniform.isChecked(),
            "enable_slope_scale": self.chk_slope_scale.isChecked(),
            "use_instances": self.chk_instance.isChecked(),
            "keep_upright": self.chk_keep_upright.isChecked(),
            "yaw_only": self.chk_yaw_only.isChecked(),
        }

    def _apply_settings(self, data):
        self.count_spin.setValue(int(data.get("count", self.count_spin.value())))
        self.seed_spin.setValue(int(data.get("seed", self.seed_spin.value())))
        self.spacing_mul_spin.setValue(float(data.get("spacing_multiplier", self.spacing_mul_spin.value())))
        self.spacing_mode_combo.setCurrentText(str(data.get("spacing_mode", self.spacing_mode_combo.currentText())))
        self.custom_radius_spin.setValue(float(data.get("custom_radius", self.custom_radius_spin.value())))
        self.source_pick_combo.setCurrentText(str(data.get("source_pick_mode", self.source_pick_combo.currentText())))
        self.source_weights_edit.setText(str(data.get("source_weights", self.source_weights_edit.text())))
        self.align_combo.setCurrentText(str(data.get("align_mode", self.align_combo.currentText())))
        self.curve_mode_combo.setCurrentText(str(data.get("curve_mode", self.curve_mode_combo.currentText())))
        self.world_up_combo.setCurrentText(str(data.get("world_up_axis", self.world_up_combo.currentText())))
        self.overlap_combo.setCurrentText(str(data.get("overlap_mode", self.overlap_combo.currentText())))
        self.overlap_softness.setValue(float(data.get("overlap_softness", self.overlap_softness.value())))
        self.slope_min.setValue(float(data.get("slope_min_deg", self.slope_min.value())))
        self.slope_max.setValue(float(data.get("slope_max_deg", self.slope_max.value())))
        self.contact_mode_combo.setCurrentText(str(data.get("contact_mode", self.contact_mode_combo.currentText())))
        self.contact_axis_combo.setCurrentText(str(data.get("contact_axis", self.contact_axis_combo.currentText())))

        for key in (
            "offset_x", "offset_y", "offset_z",
            "local_offset_x", "local_offset_y", "local_offset_z",
            "offset_along_normal", "contact_custom_x", "contact_custom_y", "contact_custom_z",
            "rand_pos_x", "rand_pos_y", "rand_pos_z",
            "rand_local_x", "rand_local_y", "rand_local_z",
            "base_rot_x", "base_rot_y", "base_rot_z",
            "rand_rot_x", "rand_rot_y", "rand_rot_z",
            "scale", "rand_uniform_scale", "rand_scale_x", "rand_scale_y", "rand_scale_z",
            "slope_scale_min", "slope_scale_max",
        ):
            widget = {
                "offset_x": self.offset_x, "offset_y": self.offset_y, "offset_z": self.offset_z,
                "local_offset_x": self.local_offset_x, "local_offset_y": self.local_offset_y, "local_offset_z": self.local_offset_z,
                "offset_along_normal": self.normal_offset,
                "contact_custom_x": self.contact_custom_x, "contact_custom_y": self.contact_custom_y, "contact_custom_z": self.contact_custom_z,
                "rand_pos_x": self.rand_pos_x, "rand_pos_y": self.rand_pos_y, "rand_pos_z": self.rand_pos_z,
                "rand_local_x": self.rand_local_x, "rand_local_y": self.rand_local_y, "rand_local_z": self.rand_local_z,
                "base_rot_x": self.base_rot_x, "base_rot_y": self.base_rot_y, "base_rot_z": self.base_rot_z,
                "rand_rot_x": self.rand_rot_x, "rand_rot_y": self.rand_rot_y, "rand_rot_z": self.rand_rot_z,
                "scale": self.scale_spin, "rand_uniform_scale": self.rand_uni_scale,
                "rand_scale_x": self.rand_scale_x, "rand_scale_y": self.rand_scale_y, "rand_scale_z": self.rand_scale_z,
                "slope_scale_min": self.slope_scale_min, "slope_scale_max": self.slope_scale_max,
            }[key]
            widget.setValue(float(data.get(key, widget.value())))

        self.chk_rand_non_uniform.setChecked(bool(data.get("rand_non_uniform", self.chk_rand_non_uniform.isChecked())))
        self.chk_slope_scale.setChecked(bool(data.get("enable_slope_scale", self.chk_slope_scale.isChecked())))
        self.chk_instance.setChecked(bool(data.get("use_instances", self.chk_instance.isChecked())))
        self.chk_keep_upright.setChecked(bool(data.get("keep_upright", self.chk_keep_upright.isChecked())))
        self.chk_yaw_only.setChecked(bool(data.get("yaw_only", self.chk_yaw_only.isChecked())))

    # ------------------------------
    # Callbacks
    # ------------------------------
    def on_set_sources(self):
        try:
            selection = cmds.ls(sl=True, long=True, transforms=True) or []
            if not selection:
                raise RuntimeError("Select one or more source transforms first.")
            self.engine.set_sources(selection)
            if not self.engine.source_objects:
                raise RuntimeError("No valid source transforms found.")
            self._last_settings_snapshot = None
            self._refresh_source_label()
            self.status.setText("Sources set: {}".format(len(self.engine.source_objects)))
        except Exception as exc:
            cmds.warning(str(exc))
            self.status.setText("Source error")
        self.on_live_param_changed()

    def on_set_target(self):
        try:
            self.engine.set_target_from_selection()
            self._last_settings_snapshot = None
            self._refresh_target_label()
            self.status.setText("Target set: {}".format(self.engine.target_data["type"]))
        except Exception as exc:
            cmds.warning(str(exc))
            self.status.setText("Target error")
        self.on_live_param_changed()

    def on_live_param_changed(self, *_args):
        if not self.chk_live_preview.isChecked():
            return
        if not self.engine.source_objects or not self.engine.target_data:
            return
        self._live_timer.start(self.live_debounce_ms.value())

    def on_preview(self):
        cmds.undoInfo(openChunk=True, chunkName="SmartScatterPreview")
        try:
            self._live_timer.stop()
            settings = self._settings()
            update_mode = self._choose_update_mode(settings)
            res = self.engine.preview(settings, update_mode=update_mode)
            self._last_settings_snapshot = settings
            st = self.engine.last_preview_stats or {}
            self.status.setText(
                "Preview: {}/{} | spacing rej:{} | slope rej:{} | tries:{} | mode:{} | update:{} | {:.2f}s".format(
                    st.get("created", len(res)),
                    st.get("requested", len(res)),
                    st.get("rejected_spacing", 0),
                    st.get("rejected_slope", 0),
                    st.get("tries", 0),
                    st.get("mode", "-"),
                    st.get("update_mode", update_mode),
                    float(st.get("seconds", 0.0)),
                )
            )
            cmds.select(clear=True)
        except Exception as exc:
            cmds.warning("Smart Scatter Preview failed: {}".format(exc))
            traceback.print_exc()
            self.status.setText("Preview failed")
        finally:
            cmds.undoInfo(closeChunk=True)

    def on_bake(self):
        cmds.undoInfo(openChunk=True, chunkName="SmartScatterBake")
        try:
            result = self.engine.bake(group_result=self.chk_group.isChecked())
            self.status.setText("Baked")
            return result
        except Exception as exc:
            cmds.warning("Bake failed: {}".format(exc))
            self.status.setText("Bake failed")
        finally:
            cmds.undoInfo(closeChunk=True)

    def on_clear(self):
        self._live_timer.stop()
        self.engine.clear_preview()
        self._last_settings_snapshot = None
        self.status.setText("Preview cleared")

    def _choose_update_mode(self, settings):
        _ = settings
        return "full"

    def on_save_preset(self):
        try:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                "Save Smart Scatter Preset",
                "",
                "Smart Scatter Preset (*.json)"
            )
            if not path:
                return
            data = {"version": PRESET_VERSION, "settings": self._settings()}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=True)
            self.status.setText("Preset saved")
        except Exception as exc:
            cmds.warning("Save preset failed: {}".format(exc))
            self.status.setText("Preset save failed")

    def on_load_preset(self):
        try:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self,
                "Load Smart Scatter Preset",
                "",
                "Smart Scatter Preset (*.json)"
            )
            if not path:
                return
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            settings = data.get("settings", data)
            self._apply_settings(settings)
            self.status.setText("Preset loaded")
            self.on_live_param_changed()
        except Exception as exc:
            cmds.warning("Load preset failed: {}".format(exc))
            self.status.setText("Preset load failed")

    def closeEvent(self, event):
        try:
            self.engine.clear_preview()
        except Exception:
            pass
        type(self)._instance = None
        super(ScatterUI, self).closeEvent(event)

    @classmethod
    def show_ui(cls):
        if cls._instance is not None:
            try:
                if isValid(cls._instance):
                    cls._instance.show()
                    cls._instance.raise_()
                    cls._instance.activateWindow()
                    return cls._instance
            except Exception:
                pass
            cls._instance = None

        cls._instance = ScatterUI(parent=maya_main_window())
        cls._instance.show()
        cls._instance.raise_()
        cls._instance.activateWindow()
        return cls._instance


# ============================================================
# ENTRY
# ============================================================
def show_ui():
    return ScatterUI.show_ui()


if __name__ == "__main__":
    show_ui()
