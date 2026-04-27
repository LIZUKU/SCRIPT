# -*- coding: utf-8 -*-
from __future__ import print_function

import hashlib
import math
import struct
from collections import defaultdict

try:
    import numpy as np
except Exception:
    np = None

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


ROOT_GROUP = "_INSTANCE_CLEANER"
MASTERS_GROUP = "MASTERS"
INSTANCES_GROUP = "INSTANCES"
BACKUP_GROUP = "BACKUPS"
CONVERTED_GROUP = "CONVERTED_GEO"

LAYER_MASTERS = "DP_MASTERS"
LAYER_INSTANCES = "DP_INSTANCES"
LAYER_BACKUPS = "DP_BACKUPS"
LAYER_CONVERTED = "DP_CONVERTED"

ATTR_IC_TYPE = "ic_type"
ATTR_IC_GROUP = "ic_group_id"
ATTR_IC_SOURCE = "ic_source"
ATTR_IC_PROCESSED = "ic_processed"
ATTR_IC_GROUP_NAME = "ic_group_name"

MATCH_EXACT = "exact"
MATCH_SIMILAR = "similar"
MATCH_PROCESSED = "processed"


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


def _short(obj):
    return obj.split("|")[-1] if obj else obj


def _safe_name(name):
    out = "".join(c if c.isalnum() or c == "_" else "_" for c in name)
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_") or "mesh"


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
    try:
        dag = _get_dag_path(transform_name)
        if dag.apiType() == om2.MFn.kMesh:
            dag.pop()
        dag.extendToShape()
        if dag.apiType() == om2.MFn.kMesh:
            return om2.MFnMesh(dag), dag
    except Exception:
        pass
    return None, None


def _get_world_matrix(node):
    try:
        return cmds.xform(node, q=True, ws=True, matrix=True)
    except Exception:
        try:
            dag = _get_dag_path(node)
            matrix = dag.inclusiveMatrix()
            return [matrix(i, j) for i in range(4) for j in range(4)]
        except Exception:
            return [
                1.0, 0.0, 0.0, 0.0,
                0.0, 1.0, 0.0, 0.0,
                0.0, 0.0, 1.0, 0.0,
                0.0, 0.0, 0.0, 1.0,
            ]


def _apply_world_matrix(node, matrix):
    try:
        cmds.xform(node, ws=True, matrix=matrix)
        return True
    except Exception as error:
        cmds.warning("[IC] Matrix failed on {}: {}".format(node, error))
        return False


def _world_bbox(node):
    try:
        bb = cmds.exactWorldBoundingBox(node, calculateExactly=True)
        center = ((bb[0] + bb[3]) * 0.5, (bb[1] + bb[4]) * 0.5, (bb[2] + bb[5]) * 0.5)
        size = (max(abs(bb[3] - bb[0]), 1e-8), max(abs(bb[4] - bb[1]), 1e-8), max(abs(bb[5] - bb[2]), 1e-8))
        return center, size
    except Exception:
        return (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)


def _object_bbox_center(node):
    fn_mesh, _ = _get_mesh_fn(node)
    if not fn_mesh:
        return (0.0, 0.0, 0.0)
    center = fn_mesh.boundingBox.center
    return (center.x, center.y, center.z)


def _move_vertices_object_space(node, offset):
    fn_mesh, _ = _get_mesh_fn(node)
    if not fn_mesh:
        return

    points = fn_mesh.getPoints(om2.MSpace.kObject)
    offset_vec = om2.MVector(offset[0], offset[1], offset[2])

    for i in range(len(points)):
        points[i] = om2.MPoint(om2.MVector(points[i]) + offset_vec)

    fn_mesh.setPoints(points, om2.MSpace.kObject)

    try:
        fn_mesh.updateSurface()
    except Exception:
        pass


def _center_shape_on_transform(node):
    center = _object_bbox_center(node)
    _move_vertices_object_space(node, (-center[0], -center[1], -center[2]))


def _iter_mesh_transforms(root=None, include_ic=False):
    results = []

    if root:
        try:
            root_dag = _get_dag_path(root)
            it = om2.MItDag(om2.MItDag.kDepthFirst, om2.MFn.kTransform)
            it.reset(root_dag)
        except Exception:
            return results
    else:
        it = om2.MItDag(om2.MItDag.kDepthFirst, om2.MFn.kTransform)

    while not it.isDone():
        dag = it.getPath()
        full_path = dag.fullPathName()

        if not include_ic and full_path.startswith("|" + ROOT_GROUP):
            it.next()
            continue

        for i in range(dag.childCount()):
            child = dag.child(i)
            if child.apiType() != om2.MFn.kMesh:
                continue

            fn_node = om2.MFnDependencyNode(child)
            try:
                if fn_node.findPlug("intermediateObject", False).asBool():
                    continue
            except Exception:
                pass

            results.append(full_path)
            break

        it.next()

    seen = set()
    out = []
    for item in results:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _get_selected_transforms():
    selection = cmds.ls(sl=True, long=True) or []
    out = []
    seen = set()

    for obj in selection:
        if "." in obj:
            obj = obj.split(".")[0]
        if not cmds.objExists(obj):
            continue
        if cmds.nodeType(obj) == "mesh":
            parents = cmds.listRelatives(obj, parent=True, fullPath=True) or []
            if parents:
                obj = parents[0]
        if obj not in seen:
            seen.add(obj)
            out.append(obj)
    return out


def _ensure_group(name, parent=None):
    if cmds.objExists(name):
        group = cmds.ls(name, long=True)[0]
    else:
        group = cmds.group(em=True, name=name)
        group = cmds.ls(group, long=True)[0]
        try:
            cmds.xform(group, ws=True, t=(0, 0, 0), ro=(0, 0, 0), s=(1, 1, 1))
        except Exception:
            pass

    if parent and cmds.objExists(parent):
        parent_long = cmds.ls(parent, long=True)[0]
        current_parent = cmds.listRelatives(group, parent=True, fullPath=True) or []
        if not current_parent or current_parent[0] != parent_long:
            try:
                group = cmds.parent(group, parent_long, absolute=True)[0]
                group = cmds.ls(group, long=True)[0]
            except Exception:
                pass
    return group


def _ensure_ic_groups():
    root = _ensure_group(ROOT_GROUP)
    masters = _ensure_group(MASTERS_GROUP, root)
    instances = _ensure_group(INSTANCES_GROUP, root)
    backups = _ensure_group(BACKUP_GROUP, root)
    converted = _ensure_group(CONVERTED_GROUP, root)
    return root, masters, instances, backups, converted


def _ensure_layer(layer_name, color_index=None):
    if not cmds.objExists(layer_name):
        cmds.createDisplayLayer(name=layer_name, empty=True)
    if color_index is not None:
        try:
            cmds.setAttr(layer_name + ".color", color_index)
        except Exception:
            pass
    return layer_name


def _add_to_layer(layer_name, nodes):
    nodes = cmds.ls(nodes, long=True) or []
    nodes = [node for node in nodes if cmds.objExists(node)]
    if not nodes:
        return
    try:
        cmds.editDisplayLayerMembers(layer_name, *nodes, noRecurse=True)
    except Exception as error:
        cmds.warning("[IC] Add to layer failed {}: {}".format(layer_name, error))


def _ensure_ic_layers():
    layer_masters = _ensure_layer(LAYER_MASTERS, 17)
    layer_instances = _ensure_layer(LAYER_INSTANCES, 14)
    layer_backups = _ensure_layer(LAYER_BACKUPS, 21)
    layer_converted = _ensure_layer(LAYER_CONVERTED, 18)

    try:
        cmds.setAttr(layer_masters + ".visibility", 1)
        cmds.setAttr(layer_instances + ".visibility", 1)
        cmds.setAttr(layer_backups + ".visibility", 0)
        cmds.setAttr(layer_converted + ".visibility", 1)
    except Exception:
        pass

    return layer_masters, layer_instances, layer_backups, layer_converted


def _add_ic_attr(node, attr_name, value, attr_type="string"):
    if not cmds.objExists(node):
        return

    if not cmds.attributeQuery(attr_name, node=node, exists=True):
        try:
            if attr_type == "string":
                cmds.addAttr(node, ln=attr_name, dt="string")
            elif attr_type == "int":
                cmds.addAttr(node, ln=attr_name, at="long")
            elif attr_type == "bool":
                cmds.addAttr(node, ln=attr_name, at="bool")
        except Exception:
            pass

    try:
        if attr_type == "string":
            cmds.setAttr(node + "." + attr_name, value, type="string")
        elif attr_type == "bool":
            cmds.setAttr(node + "." + attr_name, bool(value))
        else:
            cmds.setAttr(node + "." + attr_name, int(value))
    except Exception:
        pass


def _get_ic_attr(node, attr_name, default=None):
    if not cmds.objExists(node):
        return default
    if not cmds.attributeQuery(attr_name, node=node, exists=True):
        return default
    try:
        return cmds.getAttr(node + "." + attr_name)
    except Exception:
        return default


def _tag_node(node, ic_type, group_id, source="", group_name=""):
    _add_ic_attr(node, ATTR_IC_TYPE, ic_type, "string")
    _add_ic_attr(node, ATTR_IC_GROUP, group_id, "int")
    _add_ic_attr(node, ATTR_IC_SOURCE, source, "string")
    _add_ic_attr(node, ATTR_IC_GROUP_NAME, group_name, "string")


class MeshSignature(object):
    __slots__ = ("transform", "vertex_count", "face_count", "edge_count", "shape_hash")

    def __init__(self):
        self.transform = ""
        self.vertex_count = 0
        self.face_count = 0
        self.edge_count = 0
        self.shape_hash = ""


def _round_to(value, tolerance):
    if tolerance <= 0:
        return value
    return round(value / tolerance) * tolerance


def _compute_topology_hash(fn_mesh, tol=0.05):
    edge_lengths = []
    max_len = 0.0

    for i in range(fn_mesh.numEdges):
        v1, v2 = fn_mesh.getEdgeVertices(i)
        p1 = om2.MVector(fn_mesh.getPoint(v1, om2.MSpace.kObject))
        p2 = om2.MVector(fn_mesh.getPoint(v2, om2.MSpace.kObject))
        length = (p1 - p2).length()
        edge_lengths.append(length)
        max_len = max(max_len, length)

    if max_len <= 1e-8:
        return ""

    values = sorted(_round_to(length / max_len, tol) for length in edge_lengths)
    mesh_hash = hashlib.md5()

    for value in values:
        mesh_hash.update(struct.pack("f", float(value)))

    return mesh_hash.hexdigest()


def _compute_signature(transform_name, use_shape_hash=True, tol=0.05):
    fn_mesh, _ = _get_mesh_fn(transform_name)
    if fn_mesh is None:
        return None

    sig = MeshSignature()
    sig.transform = transform_name
    sig.vertex_count = fn_mesh.numVertices
    sig.face_count = fn_mesh.numPolygons
    sig.edge_count = fn_mesh.numEdges

    if use_shape_hash:
        sig.shape_hash = _compute_topology_hash(fn_mesh, tol=tol)

    return sig


def find_groups(signatures, use_shape_hash=True):
    groups_exact = {}
    groups_similar = {}
    uniques = []
    gid = 0

    if use_shape_hash:
        buckets = defaultdict(list)
        for sig in signatures:
            if sig.shape_hash:
                key = (sig.vertex_count, sig.face_count, sig.edge_count, sig.shape_hash)
                buckets[key].append(sig.transform)
            else:
                uniques.append(sig.transform)

        for key, transforms in buckets.items():
            if len(transforms) > 1:
                label = "group_{}".format(key[3][:8])
                groups_exact[label] = transforms
            else:
                uniques.extend(transforms)
        return groups_exact, groups_similar, uniques

    buckets = defaultdict(list)
    for sig in signatures:
        key = (sig.vertex_count, sig.face_count, sig.edge_count)
        buckets[key].append(sig.transform)

    for transforms in buckets.values():
        if len(transforms) > 1:
            label = "group_{:03d}".format(gid)
            groups_similar[label] = transforms
            gid += 1
        else:
            uniques.extend(transforms)

    return groups_exact, groups_similar, uniques


def _compute_alignment_from_geometry(master_transform, original_transform):
    if np is None:
        cmds.warning("[IC] NumPy not available. Complex alignment disabled.")
        return None

    master_fn, _ = _get_mesh_fn(master_transform)
    target_fn, target_dag = _get_mesh_fn(original_transform)

    if not master_fn or not target_fn:
        return None
    if master_fn.numVertices != target_fn.numVertices:
        return None

    src_pts = master_fn.getPoints(om2.MSpace.kObject)
    tgt_pts = target_fn.getPoints(om2.MSpace.kObject)
    tgt_world = target_dag.inclusiveMatrix()

    src = np.array([[p.x, p.y, p.z] for p in src_pts], dtype=np.float64)
    dst = np.array([[(p * tgt_world).x, (p * tgt_world).y, (p * tgt_world).z] for p in tgt_pts], dtype=np.float64)

    if src.shape[0] < 3:
        return None

    src_center = src.mean(axis=0)
    dst_center = dst.mean(axis=0)
    src_centered = src - src_center
    dst_centered = dst - dst_center

    try:
        matrix_3x3, _, _, _ = np.linalg.lstsq(src_centered, dst_centered, rcond=None)
    except Exception as error:
        cmds.warning("[IC] Alignment failed: {}".format(error))
        return None

    translation = dst_center - src_center.dot(matrix_3x3)

    return [
        float(matrix_3x3[0, 0]), float(matrix_3x3[0, 1]), float(matrix_3x3[0, 2]), 0.0,
        float(matrix_3x3[1, 0]), float(matrix_3x3[1, 1]), float(matrix_3x3[1, 2]), 0.0,
        float(matrix_3x3[2, 0]), float(matrix_3x3[2, 1]), float(matrix_3x3[2, 2]), 0.0,
        float(translation[0]),    float(translation[1]),    float(translation[2]),    1.0,
    ]


def _fallback_align(instance, original):
    _apply_world_matrix(instance, _get_world_matrix(original))
    original_center, _ = _world_bbox(original)
    instance_center, _ = _world_bbox(instance)

    try:
        pos = cmds.xform(instance, q=True, ws=True, t=True)
        cmds.xform(
            instance,
            ws=True,
            t=(
                pos[0] + original_center[0] - instance_center[0],
                pos[1] + original_center[1] - instance_center[1],
                pos[2] + original_center[2] - instance_center[2],
            ),
        )
    except Exception:
        pass


class MasterManager(object):
    def __init__(self):
        self.masters = {}

    def find_existing_master(self, group_label):
        found = cmds.ls("MASTER_{}*".format(group_label), long=True) or []
        return found[0] if found else None

    def create_master(self, group_label, reference_mesh, group_id, spacing=200.0, index=0):
        _, masters_group, _, _, _ = _ensure_ic_groups()
        layer_masters, _, _, _ = _ensure_ic_layers()

        existing = self.find_existing_master(group_label)
        if existing and cmds.objExists(existing):
            self.masters[group_label] = existing
            _add_to_layer(layer_masters, [existing])
            return existing

        base = _safe_name(_short(reference_mesh))
        master_name = "MASTER_{}_{}".format(group_label, base)

        duplicate = cmds.duplicate(reference_mesh, rr=True)[0]
        duplicate = cmds.rename(duplicate, master_name)
        duplicate = cmds.parent(duplicate, masters_group, absolute=True)[0]

        _center_shape_on_transform(duplicate)

        try:
            cmds.xform(duplicate, ws=True, t=(index * spacing, 0, 0), ro=(0, 0, 0))
            cmds.setAttr(duplicate + ".scaleX", 1)
            cmds.setAttr(duplicate + ".scaleY", 1)
            cmds.setAttr(duplicate + ".scaleZ", 1)
            cmds.setAttr(duplicate + ".visibility", 1)
        except Exception:
            pass

        duplicate = cmds.ls(duplicate, long=True)[0]

        _tag_node(duplicate, "master", group_id, reference_mesh, group_label)
        _add_ic_attr(duplicate, ATTR_IC_PROCESSED, True, "bool")
        _add_to_layer(layer_masters, [duplicate])

        self.masters[group_label] = duplicate
        return duplicate

    def replace_with_instances(self, group_label, group_meshes, group_id, keep_hidden_backups=True, delete_originals=False):
        if group_label not in self.masters:
            return [], [], []

        master_path = self.masters[group_label]
        if not cmds.objExists(master_path):
            return [], [], []

        _, _, instances_root, backups_root, _ = _ensure_ic_groups()
        layer_masters, layer_instances, layer_backups, _ = _ensure_ic_layers()

        instances_group = _ensure_group("{}_INSTANCES".format(group_label), instances_root)
        backups_group = _ensure_group("{}_BACKUPS".format(group_label), backups_root)

        instances_created = []
        backups_created = []
        originals_visible = []

        _add_to_layer(layer_masters, [master_path])

        for idx, mesh in enumerate(group_meshes):
            if not cmds.objExists(mesh):
                continue

            full_mesh = cmds.ls(mesh, long=True)[0]

            if full_mesh.startswith("|" + ROOT_GROUP):
                continue
            if _get_ic_attr(full_mesh, ATTR_IC_PROCESSED, False):
                continue
            if _is_referenced(full_mesh):
                cmds.warning("[IC] Referenced skipped: {}".format(full_mesh))
                continue

            try:
                instance = cmds.instance(master_path)[0]
                instance = cmds.rename(instance, "{}_INST_{:03d}".format(group_label, idx))
                instance = cmds.parent(instance, instances_group, absolute=True)[0]

                align_matrix = _compute_alignment_from_geometry(master_path, full_mesh)
                if align_matrix:
                    _apply_world_matrix(instance, align_matrix)
                else:
                    _fallback_align(instance, full_mesh)

                instance = cmds.ls(instance, long=True)[0]

                try:
                    cmds.setAttr(instance + ".visibility", 1)
                except Exception:
                    pass

                _tag_node(instance, "instance", group_id, master_path, group_label)
                _add_ic_attr(instance, ATTR_IC_PROCESSED, True, "bool")
                _add_to_layer(layer_instances, [instance])
                instances_created.append(instance)

            except Exception as error:
                cmds.warning("[IC] Instance failed for {}: {}".format(full_mesh, error))
                continue

            try:
                if delete_originals:
                    cmds.delete(full_mesh)

                elif keep_hidden_backups:
                    backup = cmds.parent(full_mesh, backups_group, absolute=True)[0]
                    backup = cmds.rename(backup, "{}_BACKUP_{:03d}".format(group_label, idx))
                    backup = cmds.ls(backup, long=True)[0]

                    try:
                        cmds.setAttr(backup + ".visibility", 1)
                    except Exception:
                        pass

                    _tag_node(backup, "backup", group_id, master_path, group_label)
                    _add_ic_attr(backup, ATTR_IC_PROCESSED, True, "bool")
                    _add_to_layer(layer_backups, [backup])
                    backups_created.append(backup)

                else:
                    try:
                        cmds.setAttr(full_mesh + ".visibility", 1)
                    except Exception:
                        pass

                    _tag_node(full_mesh, "original_visible", group_id, master_path, group_label)
                    _add_ic_attr(full_mesh, ATTR_IC_PROCESSED, True, "bool")
                    _add_to_layer(layer_backups, [full_mesh])
                    originals_visible.append(full_mesh)

            except Exception as error:
                cmds.warning("[IC] Cleanup failed for {}: {}".format(full_mesh, error))

        _add_to_layer(layer_instances, instances_created)
        _add_to_layer(layer_backups, backups_created)
        _add_to_layer(layer_masters, [master_path])

        try:
            cmds.setAttr(layer_masters + ".visibility", 1)
            cmds.setAttr(layer_instances + ".visibility", 1)
            cmds.setAttr(layer_backups + ".visibility", 0)
        except Exception:
            pass

        return instances_created, backups_created, originals_visible


class InstanceCleaner(object):
    def __init__(self):
        self.master_manager = MasterManager()
        self.signatures = []
        self.groups_exact = {}
        self.groups_similar = {}
        self.uniques = []
        self.validated_groups = {}

    def _append_processed_groups(self):
        if not cmds.objExists(INSTANCES_GROUP):
            return

        instance_root = cmds.ls(INSTANCES_GROUP, long=True)
        if not instance_root:
            return

        processed_meshes = _iter_mesh_transforms(instance_root[0], include_ic=True)
        buckets = defaultdict(list)

        for mesh in processed_meshes:
            ic_type = _get_ic_attr(mesh, ATTR_IC_TYPE, "")
            if ic_type != "instance":
                continue

            group_name = _get_ic_attr(mesh, ATTR_IC_GROUP_NAME, "") or "processed"
            buckets[group_name].append(mesh)

        for group_name, meshes in buckets.items():
            label = "{}_DONE".format(group_name)
            if label in self.validated_groups:
                continue

            self.validated_groups[label] = {
                "meshes": meshes,
                "type": MATCH_PROCESSED,
                "accepted": False,
                "group_id": -1,
                "processed": True,
                "base_label": group_name,
            }

    def scan(self, root=None, use_shape_hash=True, hash_tol=0.05, progress_cb=None):
        if root:
            transforms = _iter_mesh_transforms(root)
        else:
            transforms = []
            selection = _get_selected_transforms()

            for item in selection:
                children = _iter_mesh_transforms(item)
                if children:
                    transforms.extend(children)
                else:
                    fn_mesh, _ = _get_mesh_fn(item)
                    if fn_mesh:
                        transforms.append(item)

            if not transforms:
                transforms = _iter_mesh_transforms(None)

        seen = set()
        transforms = [item for item in transforms if not (item in seen or seen.add(item))]
        transforms = [item for item in transforms if not _get_ic_attr(item, ATTR_IC_PROCESSED, False)]

        self.signatures = []

        if transforms:
            total = len(transforms)
            for i, transform in enumerate(transforms):
                if progress_cb:
                    progress_cb(int(i * 100.0 / max(1, total)), transform)

                sig = _compute_signature(transform, use_shape_hash=use_shape_hash, tol=hash_tol)
                if sig:
                    self.signatures.append(sig)

            self.groups_exact, self.groups_similar, self.uniques = find_groups(
                self.signatures,
                use_shape_hash=use_shape_hash,
            )
        else:
            self.groups_exact = {}
            self.groups_similar = {}
            self.uniques = []

        self.validated_groups = {}
        gid = 0

        for label, meshes in self.groups_exact.items():
            self.validated_groups[label] = {
                "meshes": meshes,
                "type": MATCH_EXACT,
                "accepted": True,
                "group_id": gid,
                "processed": False,
                "base_label": label,
            }
            gid += 1

        for label, meshes in self.groups_similar.items():
            self.validated_groups[label] = {
                "meshes": meshes,
                "type": MATCH_SIMILAR,
                "accepted": None,
                "group_id": gid,
                "processed": False,
                "base_label": label,
            }
            gid += 1

        self._append_processed_groups()
        return len(self.validated_groups)

    def accept_group(self, label):
        if label in self.validated_groups and not self.validated_groups[label].get("processed"):
            self.validated_groups[label]["accepted"] = True

    def reject_group(self, label):
        if label in self.validated_groups:
            self.validated_groups[label]["accepted"] = False

    def select_group(self, label):
        if label not in self.validated_groups:
            return
        meshes = [mesh for mesh in self.validated_groups[label]["meshes"] if cmds.objExists(mesh)]
        if meshes:
            cmds.select(meshes, r=True)

    def _base_label(self, label):
        if label not in self.validated_groups:
            return label
        return self.validated_groups[label].get("base_label", label)

    def select_master(self, label):
        base_label = self._base_label(label)
        masters = cmds.ls("MASTER_{}*".format(base_label), long=True) or []
        if masters:
            cmds.select(masters, r=True)

    def select_instances(self, label):
        base_label = self._base_label(label)
        found = []
        if cmds.objExists(INSTANCES_GROUP):
            root = cmds.ls(INSTANCES_GROUP, long=True)[0]
            meshes = _iter_mesh_transforms(root, include_ic=True)
            for mesh in meshes:
                if _get_ic_attr(mesh, ATTR_IC_TYPE, "") == "instance" and _get_ic_attr(mesh, ATTR_IC_GROUP_NAME, "") == base_label:
                    found.append(mesh)
        if found:
            cmds.select(found, r=True)

    def select_backups(self, label):
        base_label = self._base_label(label)
        found = []
        if cmds.objExists(BACKUP_GROUP):
            root = cmds.ls(BACKUP_GROUP, long=True)[0]
            meshes = _iter_mesh_transforms(root, include_ic=True)
            for mesh in meshes:
                if _get_ic_attr(mesh, ATTR_IC_TYPE, "") == "backup" and _get_ic_attr(mesh, ATTR_IC_GROUP_NAME, "") == base_label:
                    found.append(mesh)
        if found:
            cmds.select(found, r=True)

    def select_all_masters(self):
        masters = []
        if cmds.objExists(MASTERS_GROUP):
            root = cmds.ls(MASTERS_GROUP, long=True)[0]
            masters = _iter_mesh_transforms(root, include_ic=True)
        if masters:
            cmds.select(masters, r=True)
        return len(masters)

    def _bbox_dims_center(self, node):
        center, size = _world_bbox(node)
        return center, size

    def organize_masters(self, spacing=10.0):
        if not cmds.objExists(MASTERS_GROUP):
            cmds.warning("[IC] No master group found.")
            return {"organized": 0}

        masters_root = cmds.ls(MASTERS_GROUP, long=True)[0]
        masters = _iter_mesh_transforms(masters_root, include_ic=True)
        masters = [m for m in masters if _get_ic_attr(m, ATTR_IC_TYPE, "") == "master"]

        if not masters:
            cmds.warning("[IC] No masters to organize.")
            return {"organized": 0}

        masters = sorted(masters, key=lambda x: _short(x).lower())
        count = len(masters)
        cols = max(1, int(math.ceil(math.sqrt(count))))

        centers = []
        sizes = []
        for m in masters:
            c, s = self._bbox_dims_center(m)
            centers.append(c)
            sizes.append(s)

        col_widths = [0.0] * cols
        row_depths = []

        for i, s in enumerate(sizes):
            r = i // cols
            c = i % cols
            while len(row_depths) <= r:
                row_depths.append(0.0)
            col_widths[c] = max(col_widths[c], s[0])
            row_depths[r] = max(row_depths[r], s[2])

        x_centers = []
        x = 0.0
        for c in range(cols):
            if c == 0:
                x_centers.append(0.0)
            else:
                x += (col_widths[c - 1] * 0.5) + (col_widths[c] * 0.5) + spacing
                x_centers.append(x)

        z_centers = []
        z = 0.0
        for r in range(len(row_depths)):
            if r == 0:
                z_centers.append(0.0)
            else:
                z += (row_depths[r - 1] * 0.5) + (row_depths[r] * 0.5) + spacing
                z_centers.append(z)

        total_x = x_centers[-1] if x_centers else 0.0
        total_z = z_centers[-1] if z_centers else 0.0

        with UndoChunk("InstanceCleanerOrganizeMasters"):
            for i, m in enumerate(masters):
                r = i // cols
                c = i % cols

                target = (
                    x_centers[c] - total_x * 0.5,
                    sizes[i][1] * 0.5,
                    -(z_centers[r] - total_z * 0.5),
                )

                cur_center, _ = self._bbox_dims_center(m)
                delta = (
                    target[0] - cur_center[0],
                    target[1] - cur_center[1],
                    target[2] - cur_center[2],
                )

                try:
                    cmds.move(delta[0], delta[1], delta[2], m, r=True, ws=True)
                except Exception as error:
                    cmds.warning("[IC] Organize master failed {}: {}".format(m, error))

        return {"organized": len(masters)}

    def exit_isolate(self):
        panels = cmds.getPanel(type="modelPanel") or []
        for panel in panels:
            try:
                cmds.isolateSelect(panel, state=0)
            except Exception:
                pass

    def create_masters_and_replace(self, master_spacing=200.0, keep_hidden_backups=True, delete_originals=False):
        accepted = {
            label: info
            for label, info in self.validated_groups.items()
            if info["accepted"] is True and not info.get("processed")
        }

        if not accepted:
            cmds.warning("[IC] No accepted group.")
            return {}

        stats = {
            "masters_created": 0,
            "instances_created": 0,
            "backups_created": 0,
            "originals_visible": 0,
            "groups_skipped": 0,
        }

        with UndoChunk("InstanceCleanerProcess"):
            _ensure_ic_groups()
            _ensure_ic_layers()
            process_index = 0

            for label, info in accepted.items():
                meshes = [mesh for mesh in info["meshes"] if cmds.objExists(mesh)]
                meshes = [mesh for mesh in meshes if not _get_ic_attr(mesh, ATTR_IC_PROCESSED, False)]

                if not meshes:
                    stats["groups_skipped"] += 1
                    continue

                group_id = info["group_id"]
                reference_mesh = meshes[0]
                existed = self.master_manager.find_existing_master(label)

                self.master_manager.create_master(
                    label,
                    reference_mesh,
                    group_id,
                    spacing=master_spacing,
                    index=process_index,
                )

                if not existed:
                    stats["masters_created"] += 1

                instances, backups, originals = self.master_manager.replace_with_instances(
                    label,
                    meshes,
                    group_id,
                    keep_hidden_backups=keep_hidden_backups,
                    delete_originals=delete_originals,
                )

                stats["instances_created"] += len(instances)
                stats["backups_created"] += len(backups)
                stats["originals_visible"] += len(originals)
                process_index += 1

        return stats

    def convert_instances_to_geometry(self):
        if not cmds.objExists(INSTANCES_GROUP):
            cmds.warning("[IC] No instance group found.")
            return {"converted": 0}

        _, _, instances_root, _, converted_root = _ensure_ic_groups()
        _, layer_instances, _, layer_converted = _ensure_ic_layers()

        instances = _iter_mesh_transforms(instances_root, include_ic=True)
        instances = [node for node in instances if _get_ic_attr(node, ATTR_IC_TYPE, "") == "instance"]

        if not instances:
            cmds.warning("[IC] No instances to convert.")
            return {"converted": 0}

        converted = []

        with UndoChunk("InstanceCleanerConvertInstances"):
            for idx, inst in enumerate(instances):
                if not cmds.objExists(inst):
                    continue

                try:
                    matrix = _get_world_matrix(inst)
                    new_name = "GEO_{:03d}_{}".format(idx, _safe_name(_short(inst)))
                    geo = cmds.duplicate(inst, rr=True, name=new_name)[0]
                    geo = cmds.parent(geo, converted_root, absolute=True)[0]
                    _apply_world_matrix(geo, matrix)
                    geo = cmds.ls(geo, long=True)[0]

                    _tag_node(
                        geo,
                        "converted_geo",
                        int(_get_ic_attr(inst, ATTR_IC_GROUP, 0) or 0),
                        _get_ic_attr(inst, ATTR_IC_SOURCE, ""),
                        _get_ic_attr(inst, ATTR_IC_GROUP_NAME, ""),
                    )
                    _add_ic_attr(geo, ATTR_IC_PROCESSED, True, "bool")
                    _add_to_layer(layer_converted, [geo])
                    converted.append(geo)
                    cmds.delete(inst)

                except Exception as error:
                    cmds.warning("[IC] Convert failed for {}: {}".format(inst, error))

        try:
            cmds.setAttr(layer_converted + ".visibility", 1)
            cmds.setAttr(layer_instances + ".visibility", 1)
        except Exception:
            pass

        return {"converted": len(converted)}

    def get_report(self):
        accepted = [label for label, info in self.validated_groups.items() if info["accepted"] is True]
        return {
            "total_scanned": len(self.signatures),
            "exact_groups": len(self.groups_exact),
            "similar_groups": len(self.groups_similar),
            "unique_meshes": len(self.uniques),
            "accepted_groups": len(accepted),
        }


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
            """
            QPushButton {{ background-color:{bg}; color:{fg}; border:1px solid #222; border-radius:3px; font-weight:bold; font-size:10px; padding:2px 6px; }}
            QPushButton:hover {{ background-color:{hover}; border-color:#444; }}
            QPushButton:pressed {{ background-color:#1a1a1a; }}
            QPushButton:disabled {{ background-color:#242424; color:#555; border-color:#222; }}
            """.format(bg=bg, fg=fg, hover=hover)
        )


class SectionLabel(QLabel):
    def __init__(self, text, parent=None):
        super(SectionLabel, self).__init__(text, parent)
        self.setStyleSheet("color:#555; font-size:9px; font-weight:bold; padding:4px 0 2px 0; border-bottom:1px solid #2a2a2a;")


class ParamSlider(QWidget):
    if PYSIDE_VERSION == 6:
        valueChanged = Signal(float)
    else:
        valueChanged = QtCore.Signal(float)

    def __init__(self, label, min_val, max_val, default, decimals=3, label_width=90, parent=None):
        super(ParamSlider, self).__init__(parent)
        self._multiplier = 10 ** decimals

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        label_widget = QLabel(label)
        label_widget.setFixedWidth(label_width)
        label_widget.setStyleSheet("color:#707070; font-size:9px;")

        self._slider = QSlider(Qt.Horizontal)
        self._slider.setRange(int(min_val * self._multiplier), int(max_val * self._multiplier))
        self._slider.setValue(int(default * self._multiplier))

        self._spin = QDoubleSpinBox()
        self._spin.setRange(min_val, max_val)
        self._spin.setDecimals(decimals)
        self._spin.setValue(default)
        self._spin.setFixedWidth(64)
        self._spin.setButtonSymbols(QAbstractSpinBox.NoButtons)

        layout.addWidget(label_widget)
        layout.addWidget(self._slider)
        layout.addWidget(self._spin)

        self._slider.valueChanged.connect(self._on_slider)
        self._spin.valueChanged.connect(self._on_spin)

    def _on_slider(self, value):
        real_value = value / float(self._multiplier)
        self._spin.blockSignals(True)
        self._spin.setValue(real_value)
        self._spin.blockSignals(False)
        self.valueChanged.emit(real_value)

    def _on_spin(self, value):
        self._slider.blockSignals(True)
        self._slider.setValue(int(value * self._multiplier))
        self._slider.blockSignals(False)
        self.valueChanged.emit(value)

    def value(self):
        return self._spin.value()


class GroupItem(QWidget):
    if PYSIDE_VERSION == 6:
        accept_clicked = Signal(str)
        reject_clicked = Signal(str)
        select_clicked = Signal(str)
        master_clicked = Signal(str)
        instances_clicked = Signal(str)
        backups_clicked = Signal(str)
    else:
        accept_clicked = QtCore.Signal(str)
        reject_clicked = QtCore.Signal(str)
        select_clicked = QtCore.Signal(str)
        master_clicked = QtCore.Signal(str)
        instances_clicked = QtCore.Signal(str)
        backups_clicked = QtCore.Signal(str)

    def __init__(self, label, info, parent=None):
        super(GroupItem, self).__init__(parent)
        self.label = label
        self.info = info
        self._build()
        self.refresh()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 4, 5, 4)
        layout.setSpacing(3)

        header = QHBoxLayout()

        if self.info["type"] == MATCH_EXACT:
            badge_text = "EXACT"
            badge_color = "#1e6e30"
        elif self.info["type"] == MATCH_PROCESSED:
            badge_text = "DONE"
            badge_color = "#2a5a7a"
        else:
            badge_text = "CHECK"
            badge_color = "#7a4000"

        badge = QLabel(badge_text)
        badge.setFixedSize(48, 16)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet("background:{}; color:#e0e0e0; font-size:8px; font-weight:bold; border-radius:2px;".format(badge_color))

        name_label = QLabel(self.label)
        name_label.setStyleSheet("color:#909090; font-size:10px; font-weight:bold;")

        count_label = QLabel("{} copies".format(len(self.info["meshes"])))
        count_label.setStyleSheet("color:#606060; font-size:9px;")

        header.addWidget(badge)
        header.addWidget(name_label)
        header.addStretch()
        header.addWidget(count_label)

        actions = QHBoxLayout()
        actions.setSpacing(3)

        self.src_btn = ColorBtn("SRC", "Select source/new meshes", "#252525", "#909090", 46, 21)
        self.master_btn = ColorBtn("MST", "Select master", "#253525", "#90d090", 46, 21)
        self.instances_btn = ColorBtn("INS", "Select instances", "#252535", "#9090d0", 46, 21)
        self.backups_btn = ColorBtn("BKP", "Select backups", "#352525", "#d09090", 46, 21)
        self.acc_btn = ColorBtn("✓", "Accept group", "#1a3a1a", "#60d060", 30, 21)
        self.rej_btn = ColorBtn("✗", "Reject group", "#3a1a1a", "#d06060", 30, 21)

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

        if not self.info.get("processed"):
            actions.addWidget(self.acc_btn)
            actions.addWidget(self.rej_btn)

        self.status_lbl = QLabel("")

        layout.addLayout(header)
        layout.addLayout(actions)
        layout.addWidget(self.status_lbl)

    def refresh(self):
        accepted = self.info["accepted"]
        processed = self.info.get("processed")

        self.master_btn.setEnabled(bool(processed))
        self.instances_btn.setEnabled(bool(processed))
        self.backups_btn.setEnabled(bool(processed))

        if processed:
            self.status_lbl.setText("Processed")
            bg, border, color = "#1e242a", "#2a3a4a", "#60a0d0"
        elif accepted is True:
            self.status_lbl.setText("✓ Accepted")
            bg, border, color = "#1e2a1e", "#2a4a2a", "#50c050"
        elif accepted is False:
            self.status_lbl.setText("✗ Rejected")
            bg, border, color = "#2a1e1e", "#4a2a2a", "#c05050"
        else:
            self.status_lbl.setText("Check")
            bg, border, color = "#232323", "#2e2e2e", "#707070"

        self.status_lbl.setStyleSheet("color:{}; font-size:8px;".format(color))
        self.setStyleSheet("QWidget {{ background:{}; border:1px solid {}; border-radius:3px; }}".format(bg, border))


class InstanceCleanerUI(QDialog):
    def __init__(self, parent=maya_main_window()):
        super(InstanceCleanerUI, self).__init__(parent)
        self.cleaner = InstanceCleaner()
        self.group_items = {}
        self.setWindowTitle("Instance Cleaner V1.8")
        self.setMinimumWidth(460)
        self.setMinimumHeight(580)
        self.setWindowFlags(Qt.Window | Qt.WindowCloseButtonHint)
        self._build_ui()
        self._apply_stylesheet()

    def _apply_stylesheet(self):
        self.setStyleSheet(
            """
            QDialog { background-color:#1e1e1e; }
            QLabel { color:#707070; font-size:10px; }
            QScrollArea { border:none; background:transparent; }
            QScrollBar:vertical { background:#1a1a1a; width:8px; border-radius:4px; }
            QScrollBar::handle:vertical { background:#3a3a3a; border-radius:4px; min-height:20px; }
            QSlider::groove:horizontal { height:4px; background:#2a2a2a; border-radius:2px; }
            QSlider::handle:horizontal { background:#d32f2f; width:12px; margin:-4px 0; border-radius:6px; }
            QSlider::sub-page:horizontal { background:#d32f2f; border-radius:2px; }
            QSpinBox, QDoubleSpinBox { background:#252525; color:#a0a0a0; border:1px solid #303030; border-radius:3px; padding:2px; font-size:10px; }
            QComboBox { background:#252525; color:#a0a0a0; border:1px solid #303030; border-radius:3px; padding:4px 8px; font-size:10px; }
            QProgressBar { background:#1a1a1a; border:1px solid #303030; border-radius:3px; text-align:center; color:#707070; font-size:9px; }
            QProgressBar::chunk { background:#d32f2f; border-radius:2px; }
            """
        )

    def _build_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(8, 8, 8, 8)
        main.setSpacing(6)

        title = QLabel("⚡ INSTANCE CLEANER")
        title.setStyleSheet("color:#d32f2f; font-size:14px; font-weight:bold; padding-bottom:4px;")
        main.addWidget(title)

        main.addWidget(SectionLabel("SCAN"))

        self.scan_mode_combo = QComboBox()
        self.scan_mode_combo.addItems(["Selection", "Scene"])
        main.addLayout(self._row("Source", self.scan_mode_combo))

        self.hash_tol_slider = ParamSlider("Hash tol", 0.0001, 0.05, 0.05, 4, 90)
        main.addWidget(self.hash_tol_slider)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(16)
        main.addWidget(self.progress_bar)

        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("color:#505050; font-size:9px;")
        main.addWidget(self.status_label)

        scan_btn = ColorBtn("REFRESH SCAN", "Rescan scene or selection", "#1a2a3a", "#60a0d0", h=32)
        scan_btn.clicked.connect(self.do_scan)
        main.addWidget(scan_btn)

        main.addWidget(SectionLabel("GROUPS"))

        bulk = QHBoxLayout()
        accept_all_btn = ColorBtn("ACCEPT ALL", "", "#1a3a1a", "#60d060", h=24)
        reject_all_btn = ColorBtn("REJECT ALL", "", "#3a1a1a", "#d06060", h=24)
        select_masters_btn = ColorBtn("SELECT ALL MASTERS", "", "#253525", "#90d090", h=24)
        organize_masters_btn = ColorBtn("ORGANIZE MASTERS", "", "#2a2a3a", "#a0c0ff", h=24)

        accept_all_btn.clicked.connect(self.do_accept_all)
        reject_all_btn.clicked.connect(self.do_reject_all)
        select_masters_btn.clicked.connect(self.do_select_all_masters)
        organize_masters_btn.clicked.connect(self.do_organize_masters)

        bulk.addWidget(accept_all_btn)
        bulk.addWidget(reject_all_btn)
        main.addLayout(bulk)

        master_tools = QHBoxLayout()
        master_tools.addWidget(select_masters_btn)
        master_tools.addWidget(organize_masters_btn)
        main.addLayout(master_tools)

        filter_row = QHBoxLayout()
        filter_label = QLabel("Filter")
        filter_label.setFixedWidth(50)

        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All", "Exact", "Check", "Accepted", "Rejected", "Processed"])
        self.filter_combo.currentIndexChanged.connect(self.refresh_group_list)

        filter_row.addWidget(filter_label)
        filter_row.addWidget(self.filter_combo)
        main.addLayout(filter_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        self.groups_container = QWidget()
        self.groups_layout = QVBoxLayout(self.groups_container)
        self.groups_layout.setContentsMargins(0, 0, 0, 0)
        self.groups_layout.setSpacing(3)
        self.groups_layout.addStretch()

        scroll.setWidget(self.groups_container)
        main.addWidget(scroll, 1)

        main.addWidget(SectionLabel("PROCESS"))

        self.master_spacing_spin = QDoubleSpinBox()
        self.master_spacing_spin.setRange(0, 5000)
        self.master_spacing_spin.setValue(10)
        self.master_spacing_spin.setDecimals(0)
        self.master_spacing_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        main.addLayout(self._row("Spacing", self.master_spacing_spin))

        process_row = QHBoxLayout()
        process_btn = ColorBtn("PROCESS ACCEPTED", "", "#1e3a1a", "#80e060", h=34)
        undo_btn = ColorBtn("UNDO PROCESS", "", "#3a2a1a", "#e0a060", h=34)

        process_btn.clicked.connect(self.do_process)
        undo_btn.clicked.connect(self.do_undo_process)

        process_row.addWidget(process_btn)
        process_row.addWidget(undo_btn)
        main.addLayout(process_row)

        convert_btn = ColorBtn("CONVERT INSTANCES TO GEO", "", "#3a1e3a", "#e080e0", h=34)
        convert_btn.clicked.connect(self.do_convert_instances)
        main.addWidget(convert_btn)

    def _row(self, label_text, widget, label_width=90):
        row = QHBoxLayout()
        row.setSpacing(4)
        label = QLabel(label_text)
        label.setFixedWidth(label_width)
        row.addWidget(label)
        row.addWidget(widget)
        return row

    def do_scan(self):
        root = None
        if self.scan_mode_combo.currentText() == "Selection":
            selection = _get_selected_transforms()
            root = selection[0] if selection else None

        def progress_cb(percent, label):
            self.progress_bar.setValue(percent)
            self.status_label.setText(_short(label))
            QApplication.processEvents()

        self.progress_bar.setValue(0)
        group_count = self.cleaner.scan(root=root, use_shape_hash=True, hash_tol=self.hash_tol_slider.value(), progress_cb=progress_cb)
        self.progress_bar.setValue(100)

        report = self.cleaner.get_report()
        self.status_label.setText("{} groups | {} exact | {} unique".format(group_count, report["exact_groups"], report["unique_meshes"]))
        self.refresh_group_list()

    def refresh_group_list(self):
        while self.groups_layout.count() > 1:
            item = self.groups_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.group_items = {}
        filter_text = self.filter_combo.currentText()
        insert_index = 0

        for label, info in self.cleaner.validated_groups.items():
            if filter_text == "Exact" and info["type"] != MATCH_EXACT:
                continue
            if filter_text == "Check" and info["type"] != MATCH_SIMILAR:
                continue
            if filter_text == "Accepted" and info["accepted"] is not True:
                continue
            if filter_text == "Rejected" and info["accepted"] is not False:
                continue
            if filter_text == "Processed" and not info.get("processed"):
                continue

            item_widget = GroupItem(label, info)
            item_widget.accept_clicked.connect(self.on_accept_group)
            item_widget.reject_clicked.connect(self.on_reject_group)
            item_widget.select_clicked.connect(self.on_select_group)
            item_widget.master_clicked.connect(self.on_select_master)
            item_widget.instances_clicked.connect(self.on_select_instances)
            item_widget.backups_clicked.connect(self.on_select_backups)

            self.groups_layout.insertWidget(insert_index, item_widget)
            self.group_items[label] = item_widget
            insert_index += 1

    def _refresh_item(self, label):
        if label in self.group_items:
            self.group_items[label].refresh()

    def on_accept_group(self, label):
        self.cleaner.accept_group(label)
        self._refresh_item(label)

    def on_reject_group(self, label):
        self.cleaner.reject_group(label)
        self._refresh_item(label)

    def on_select_group(self, label):
        self.cleaner.select_group(label)

    def on_select_master(self, label):
        self.cleaner.select_master(label)

    def on_select_instances(self, label):
        self.cleaner.select_instances(label)

    def on_select_backups(self, label):
        self.cleaner.select_backups(label)

    def do_accept_all(self):
        for label, info in self.cleaner.validated_groups.items():
            if not info.get("processed"):
                self.cleaner.accept_group(label)
        self.refresh_group_list()

    def do_reject_all(self):
        for label in self.cleaner.validated_groups:
            self.cleaner.reject_group(label)
        self.refresh_group_list()

    def do_select_all_masters(self):
        count = self.cleaner.select_all_masters()
        self.status_label.setText("Selected {} masters".format(count))

    def do_organize_masters(self):
        stats = self.cleaner.organize_masters(spacing=10.0)
        self.status_label.setText("Organized {} masters".format(stats.get("organized", 0)))

    def do_process(self):
        stats = self.cleaner.create_masters_and_replace(master_spacing=self.master_spacing_spin.value(), keep_hidden_backups=True, delete_originals=False)
        if not stats:
            return
        self.status_label.setText(
            "Done | masters {} | instances {} | backups {} | skipped {}".format(
                stats["masters_created"], stats["instances_created"], stats["backups_created"], stats["groups_skipped"]
            )
        )
        self.do_scan()

    def do_convert_instances(self):
        stats = self.cleaner.convert_instances_to_geometry()
        self.status_label.setText("Converted {} instances to geo".format(stats.get("converted", 0)))
        self.do_scan()

    def do_undo_process(self):
        try:
            cmds.undo()
            self.status_label.setText("Undo done | refreshing scan...")
            self.do_scan()
        except Exception as error:
            cmds.warning("[IC] Undo failed: {}".format(error))

    def closeEvent(self, event):
        try:
            self.cleaner.exit_isolate()
        except Exception:
            pass
        super(InstanceCleanerUI, self).closeEvent(event)


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
