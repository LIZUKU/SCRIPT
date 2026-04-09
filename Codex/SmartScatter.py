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

import math
import random
import traceback

import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.OpenMayaUI as omui

try:
    from PySide6 import QtCore, QtWidgets
    from shiboken6 import wrapInstance
except ImportError:
    from PySide2 import QtCore, QtWidgets
    from shiboken2 import wrapInstance


WINDOW_NAME = "SmartScatterTool2026"
WINDOW_TITLE = "Smart Scatter Tool"
PREVIEW_GROUP = "SmartScatter_preview_GRP"
RESULT_GROUP = "SmartScatter_result_GRP"
ACCENT = "#e05a5a"
BG = "#2d2d2d"
PANEL = "#353535"
FIELD = "#252525"
TEXT = "#c8c8c8"


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
    return [m(i, j) for i in range(4) for j in range(4)]


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
                try:
                    tris_pts, _tris_verts = fn_mesh.getTriangles()
                except Exception:
                    tris_pts = []
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

    def sample(self, count):
        out = []
        if not self.entries or self.total_area <= 1e-10:
            return out

        import bisect
        cumulative = [e[0] for e in self.entries]
        for _ in range(count):
            r = self.random.uniform(0.0, self.total_area)
            idx = bisect.bisect_left(cumulative, r)
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

    def sample(self, count):
        out = []
        if not self.edges or self.total_length <= 1e-10:
            return out

        import bisect
        cumulative = [e[0] for e in self.edges]
        for _ in range(count):
            r = self.random.uniform(0.0, self.total_length)
            idx = bisect.bisect_left(cumulative, r)
            _cum, mesh, edge_id, p0, p1, fn_mesh = self.edges[min(idx, len(self.edges) - 1)]
            t = self.random.random()
            pos = p0 + ((p1 - p0) * t)
            tangent = (p1 - p0).normal()

            normal = om.MVector(0.0, 1.0, 0.0)
            try:
                connected = fn_mesh.getEdgeVertices(edge_id)
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

    def clear_preview(self):
        for node in list(self.preview_nodes):
            safe_delete(node)
        self.preview_nodes = []
        safe_delete(self.preview_group)
        self.preview_group = None

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

        # Local position jitter in world space
        pos_jitter = om.MVector(
            rng.uniform(-settings["rand_pos_x"], settings["rand_pos_x"]),
            rng.uniform(-settings["rand_pos_y"], settings["rand_pos_y"]),
            rng.uniform(-settings["rand_pos_z"], settings["rand_pos_z"]),
        )
        pos += pos_jitter

        base_offset = om.MVector(settings["offset_x"], settings["offset_y"], settings["offset_z"])
        pos += base_offset

        base_m = build_matrix_from_axes(
            x_axis=(x_axis.x, x_axis.y, x_axis.z),
            y_axis=(y_axis.x, y_axis.y, y_axis.z),
            z_axis=(z_axis.x, z_axis.y, z_axis.z),
            pos=(pos.x, pos.y, pos.z)
        )

        rx = settings["base_rot_x"] + rng.uniform(-settings["rand_rot_x"], settings["rand_rot_x"])
        ry = settings["base_rot_y"] + rng.uniform(-settings["rand_rot_y"], settings["rand_rot_y"])
        rz = settings["base_rot_z"] + rng.uniform(-settings["rand_rot_z"], settings["rand_rot_z"])

        rot_m = compose_random_rotation(rx, ry, rz)
        final_m = rot_m * base_m
        return final_m, pos

    def _passes_spacing(self, pos, accepted_positions, min_dist):
        if min_dist <= 0.0:
            return True
        min_sq = min_dist * min_dist
        for p in accepted_positions:
            d = pos - p
            if (d.x * d.x + d.y * d.y + d.z * d.z) < min_sq:
                return False
        return True

    def preview(self, settings):
        if not self.source_objects:
            raise RuntimeError("No source objects set.")
        if not self.target_data:
            raise RuntimeError("No target set.")

        self.clear_preview()
        sampler = self._build_sampler(settings)
        count = max(1, int(settings.get("count", 100)))
        source_mode = settings.get("source_pick_mode", "random")
        use_instances = settings.get("use_instances", True)
        rng = random.Random(int(settings.get("seed", 1)))

        source_radius = 0.0
        if self.source_objects:
            source_radius = max(estimate_source_radius(obj) for obj in self.source_objects)

        spacing_mul = max(0.0, float(settings.get("spacing_multiplier", 0.0)))
        min_dist = source_radius * spacing_mul
        max_tries = max(count * 30, 200)

        samples = []
        accepted_positions = []
        tries = 0
        while len(samples) < count and tries < max_tries:
            tries += 1
            batch = sampler.sample(1)
            if not batch:
                break
            sample = batch[0]
            if self._passes_spacing(sample.position, accepted_positions, min_dist):
                samples.append(sample)
                accepted_positions.append(om.MVector(sample.position))

        if not samples:
            raise RuntimeError("No valid scatter points found.")

        if not cmds.objExists(PREVIEW_GROUP):
            self.preview_group = cmds.group(em=True, n=PREVIEW_GROUP)
        else:
            self.preview_group = PREVIEW_GROUP

        for i, sample in enumerate(samples):
            if source_mode == "cycle":
                src = self.source_objects[i % len(self.source_objects)]
            else:
                src = rng.choice(self.source_objects)

            if use_instances:
                node = cmds.instance(src, n="smartScatter_preview_{:04d}".format(i + 1))[0]
            else:
                node = cmds.duplicate(src, rr=True, n="smartScatter_preview_{:04d}".format(i + 1))[0]

            mat, pos = self._build_transform_from_sample(sample, settings, rng)
            cmds.xform(node, ws=True, matrix=matrix_to_list(mat))

            base_scale = float(settings.get("scale", 1.0))
            uni_rand = float(settings.get("rand_uniform_scale", 0.0))
            rand_xyz = settings.get("rand_non_uniform", False)

            if rand_xyz:
                sx = max(0.001, base_scale + rng.uniform(-settings["rand_scale_x"], settings["rand_scale_x"]))
                sy = max(0.001, base_scale + rng.uniform(-settings["rand_scale_y"], settings["rand_scale_y"]))
                sz = max(0.001, base_scale + rng.uniform(-settings["rand_scale_z"], settings["rand_scale_z"]))
            else:
                s = max(0.001, base_scale + rng.uniform(-uni_rand, uni_rand))
                sx = sy = sz = s

            cmds.scale(sx, sy, sz, node, absolute=True, objectSpace=True)
            try:
                cmds.parent(node, self.preview_group)
            except Exception:
                pass
            self.preview_nodes.append(node)

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

    def __init__(self, parent=maya_main_window()):
        super(ScatterUI, self).__init__(parent)
        self.setObjectName(WINDOW_NAME)
        self.setWindowTitle(WINDOW_TITLE)
        self.setMinimumWidth(440)
        self.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.WindowCloseButtonHint)

        self.engine = SmartScatterEngine()
        self._build_ui()
        self._apply_style()

    # ------------------------------
    # UI Construction
    # ------------------------------
    def _build_ui(self):
        main = QtWidgets.QVBoxLayout(self)
        main.setContentsMargins(10, 10, 10, 10)
        main.setSpacing(6)

        self.status = QtWidgets.QLabel("Ready")
        self.status.setObjectName("statusLabel")
        self.status.setAlignment(QtCore.Qt.AlignCenter)
        main.addWidget(self.status)

        # Sources
        main.addWidget(self._section_label("SOURCES"))
        src_row = QtWidgets.QHBoxLayout()
        self.btn_set_sources = QtWidgets.QPushButton("Set Sources From Selection")
        self.btn_set_sources.clicked.connect(self.on_set_sources)
        src_row.addWidget(self.btn_set_sources)
        main.addLayout(src_row)

        self.sources_label = QtWidgets.QLabel("Sources: -")
        main.addWidget(self.sources_label)

        # Target
        main.addWidget(self._section_label("TARGET"))
        target_row = QtWidgets.QHBoxLayout()
        self.btn_set_target = QtWidgets.QPushButton("Set Target From Selection")
        self.btn_set_target.clicked.connect(self.on_set_target)
        target_row.addWidget(self.btn_set_target)
        main.addLayout(target_row)

        self.target_label = QtWidgets.QLabel("Target: -")
        main.addWidget(self.target_label)

        # Core
        main.addWidget(self._section_label("DISTRIBUTION"))
        self.count_spin = self._add_spin(main, "Count", 1, 50000, 100)
        self.seed_spin = self._add_spin(main, "Seed", 1, 999999, 1)
        self.spacing_mul_spin = self._add_dspin(main, "Spacing Mult", 0.0, 10.0, 0.01, 0.0)

        self.source_pick_combo = QtWidgets.QComboBox()
        self.source_pick_combo.addItems(["random", "cycle"])
        self._add_widget_row(main, "Source Pick", self.source_pick_combo)

        self.align_combo = QtWidgets.QComboBox()
        self.align_combo.addItems(["normal", "tangent", "world"])
        self._add_widget_row(main, "Align Mode", self.align_combo)

        self.curve_mode_combo = QtWidgets.QComboBox()
        self.curve_mode_combo.addItems(["count", "even"])
        self._add_widget_row(main, "Curve Mode", self.curve_mode_combo)

        self.world_up_combo = QtWidgets.QComboBox()
        self.world_up_combo.addItems(["y", "x", "z"])
        self._add_widget_row(main, "World Up", self.world_up_combo)

        # Offsets / rotation
        main.addWidget(self._section_label("OFFSET POSITION"))
        self.offset_x = self._add_dspin(main, "Offset X", -100000, 100000, 0.01, 0.0)
        self.offset_y = self._add_dspin(main, "Offset Y", -100000, 100000, 0.01, 0.0)
        self.offset_z = self._add_dspin(main, "Offset Z", -100000, 100000, 0.01, 0.0)

        main.addWidget(self._section_label("RANDOM POSITION"))
        self.rand_pos_x = self._add_dspin(main, "Rand Pos X", 0.0, 100000, 0.01, 0.0)
        self.rand_pos_y = self._add_dspin(main, "Rand Pos Y", 0.0, 100000, 0.01, 0.0)
        self.rand_pos_z = self._add_dspin(main, "Rand Pos Z", 0.0, 100000, 0.01, 0.0)

        main.addWidget(self._section_label("BASE ROTATION"))
        self.base_rot_x = self._add_dspin(main, "Base Rot X", -360.0, 360.0, 0.1, 0.0)
        self.base_rot_y = self._add_dspin(main, "Base Rot Y", -360.0, 360.0, 0.1, 0.0)
        self.base_rot_z = self._add_dspin(main, "Base Rot Z", -360.0, 360.0, 0.1, 0.0)

        main.addWidget(self._section_label("RANDOM ROTATION"))
        self.rand_rot_x = self._add_dspin(main, "Rand Rot X", 0.0, 360.0, 0.1, 0.0)
        self.rand_rot_y = self._add_dspin(main, "Rand Rot Y", 0.0, 360.0, 0.1, 0.0)
        self.rand_rot_z = self._add_dspin(main, "Rand Rot Z", 0.0, 360.0, 0.1, 0.0)

        # Scale
        main.addWidget(self._section_label("SCALE"))
        self.scale_spin = self._add_dspin(main, "Base Scale", 0.001, 1000.0, 0.01, 1.0)
        self.rand_uni_scale = self._add_dspin(main, "Rand Uniform", 0.0, 1000.0, 0.01, 0.0)
        self.rand_scale_x = self._add_dspin(main, "Rand Scale X", 0.0, 1000.0, 0.01, 0.0)
        self.rand_scale_y = self._add_dspin(main, "Rand Scale Y", 0.0, 1000.0, 0.01, 0.0)
        self.rand_scale_z = self._add_dspin(main, "Rand Scale Z", 0.0, 1000.0, 0.01, 0.0)

        # Options
        main.addWidget(self._section_label("OPTIONS"))
        self.chk_instance = QtWidgets.QCheckBox("Use Instances")
        self.chk_instance.setChecked(True)
        main.addWidget(self.chk_instance)

        self.chk_group = QtWidgets.QCheckBox("Group Result On Bake")
        self.chk_group.setChecked(True)
        main.addWidget(self.chk_group)

        self.chk_keep_upright = QtWidgets.QCheckBox("Keep Upright")
        self.chk_keep_upright.setChecked(False)
        main.addWidget(self.chk_keep_upright)

        self.chk_rand_non_uniform = QtWidgets.QCheckBox("Use XYZ Random Scale")
        self.chk_rand_non_uniform.setChecked(False)
        main.addWidget(self.chk_rand_non_uniform)

        # Buttons
        main.addSpacing(4)
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
        main.addLayout(buttons)

        self.btn_close = QtWidgets.QPushButton("Close")
        self.btn_close.clicked.connect(self.close)
        main.addWidget(self.btn_close)

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
            QSpinBox, QDoubleSpinBox, QComboBox {
                background-color: %s;
                border: 1px solid #444444;
                border-radius: 3px;
                min-height: 22px;
                padding-left: 4px;
            }
            QCheckBox {
                padding: 2px;
            }
        """ % (BG, TEXT, PANEL, ACCENT, ACCENT, ACCENT, FIELD))

    def _section_label(self, text):
        lbl = QtWidgets.QLabel(text)
        lbl.setObjectName("sectionLabel")
        return lbl

    def _add_widget_row(self, parent_layout, label, widget):
        row = QtWidgets.QHBoxLayout()
        lbl = QtWidgets.QLabel(label)
        lbl.setFixedWidth(110)
        row.addWidget(lbl)
        row.addWidget(widget)
        parent_layout.addLayout(row)
        return widget

    def _add_spin(self, parent_layout, label, mn, mx, dv):
        w = QtWidgets.QSpinBox()
        w.setRange(mn, mx)
        w.setValue(dv)
        self._add_widget_row(parent_layout, label, w)
        return w

    def _add_dspin(self, parent_layout, label, mn, mx, step, dv):
        w = QtWidgets.QDoubleSpinBox()
        w.setDecimals(3)
        w.setSingleStep(step)
        w.setRange(mn, mx)
        w.setValue(dv)
        self._add_widget_row(parent_layout, label, w)
        return w

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
            "source_pick_mode": self.source_pick_combo.currentText(),
            "align_mode": self.align_combo.currentText(),
            "curve_mode": self.curve_mode_combo.currentText(),
            "world_up_axis": self.world_up_combo.currentText(),
            "offset_x": self.offset_x.value(),
            "offset_y": self.offset_y.value(),
            "offset_z": self.offset_z.value(),
            "rand_pos_x": self.rand_pos_x.value(),
            "rand_pos_y": self.rand_pos_y.value(),
            "rand_pos_z": self.rand_pos_z.value(),
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
            "rand_non_uniform": self.chk_rand_non_uniform.isChecked(),
            "use_instances": self.chk_instance.isChecked(),
            "keep_upright": self.chk_keep_upright.isChecked(),
        }

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
            self._refresh_source_label()
            self.status.setText("Sources set: {}".format(len(self.engine.source_objects)))
        except Exception as exc:
            cmds.warning(str(exc))
            self.status.setText("Source error")

    def on_set_target(self):
        try:
            self.engine.set_target_from_selection()
            self._refresh_target_label()
            self.status.setText("Target set: {}".format(self.engine.target_data["type"]))
        except Exception as exc:
            cmds.warning(str(exc))
            self.status.setText("Target error")

    def on_preview(self):
        try:
            res = self.engine.preview(self._settings())
            self.status.setText("Preview: {} objects".format(len(res)))
            cmds.select(clear=True)
        except Exception as exc:
            cmds.warning("Smart Scatter Preview failed: {}".format(exc))
            traceback.print_exc()
            self.status.setText("Preview failed")

    def on_bake(self):
        try:
            result = self.engine.bake(group_result=self.chk_group.isChecked())
            self.status.setText("Baked")
            return result
        except Exception as exc:
            cmds.warning("Bake failed: {}".format(exc))
            self.status.setText("Bake failed")

    def on_clear(self):
        self.engine.clear_preview()
        self.status.setText("Preview cleared")

    def closeEvent(self, event):
        try:
            self.engine.clear_preview()
        except Exception:
            pass
        super(ScatterUI, self).closeEvent(event)

    @classmethod
    def show_ui(cls):
        if cls._instance is not None:
            try:
                cls._instance.close()
                cls._instance.deleteLater()
            except Exception:
                pass
            cls._instance = None

        if cmds.window(WINDOW_NAME, exists=True):
            cmds.deleteUI(WINDOW_NAME)

        cls._instance = ScatterUI()
        cls._instance.show()
        return cls._instance


# ============================================================
# ENTRY
# ============================================================
def show_ui():
    return ScatterUI.show_ui()


if __name__ == "__main__":
    show_ui()
