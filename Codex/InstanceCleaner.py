# -*- coding: utf-8 -*-
"""
Instance Cleaner for Maya - V1.1
----------------------------------
Outil d'optimisation d'assets complexes :
- Détection automatique des meshes dupliqués/similaires
- Extraction d'un mesh "master"
- Remplacement par instances
- Organisation dans une zone dédiée
- Validation manuelle des groupes détectés

Basé sur OpenMaya 2.0 (maya.api.OpenMaya) pour les performances.
Compatible avec le Mesh Organizer V10.1 (même style UI).

Changelog V1.1 :
- Fix : replace ne duplique plus le master par erreur (comparaison chemin corrigée)
- Fix : couleurs viewport via shaders Lambert dédiés + display layers (plus propre)
- Fix : isolate auto-quitte l'isolation précédente avant d'en lancer une nouvelle
- Fix : panel viewport résolu via modelPanel actif, pas withFocus (UI-safe)
- Ajout : display layers IC_Exact / IC_Similar / IC_Unique créés automatiquement
- Ajout : cleanup shaders/layers dans clear_colors()
"""

import hashlib
import struct
from collections import defaultdict

import maya.cmds as cmds
import maya.OpenMayaUI as omui

# OpenMaya 2.0
import maya.api.OpenMaya as om2

# ------------------------------------------------------------
# Qt imports
# ------------------------------------------------------------
try:
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtWidgets import *
    from PySide6.QtCore import *
    from PySide6.QtGui import *
    PYSIDE_VERSION = 6
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets
    from PySide2.QtWidgets import *
    from PySide2.QtCore import *
    from PySide2.QtGui import *
    PYSIDE_VERSION = 2

try:
    from shiboken6 import wrapInstance
except ImportError:
    from shiboken2 import wrapInstance


# ============================================================
#  CONSTANTES
# ============================================================
MASTERS_GROUP   = "_INSTANCE_CLEANER_MASTERS"
INSTANCES_GROUP = "_INSTANCE_CLEANER_INSTANCES"
BACKUP_GROUP    = "_INSTANCE_CLEANER_BACKUP"
ATTR_IC_TYPE    = "ic_type"
ATTR_IC_GROUP   = "ic_group_id"
ATTR_IC_SOURCE  = "ic_source"
ATTR_IC_REPLACED = "ic_replaced"

MATCH_EXACT     = "exact"
MATCH_SIMILAR   = "similar"
MATCH_UNIQUE    = "unique"

# Couleurs viewport (RGB 0-1)
COLOR_EXACT   = (0.1, 0.8, 0.2)   # vert
COLOR_SIMILAR = (0.9, 0.5, 0.1)   # orange
COLOR_UNIQUE  = (0.8, 0.1, 0.1)   # rouge


# ============================================================
#  MAYA WINDOW
# ============================================================
def maya_main_window():
    ptr = omui.MQtUtil.mainWindow()
    return wrapInstance(int(ptr), QWidget) if ptr else None


# ============================================================
#  UNDO CHUNK
# ============================================================
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


# ============================================================
#  UTILITAIRES SCÈNE
# ============================================================
def _short(obj):
    return obj.split("|")[-1] if obj else obj


def _is_referenced(obj):
    try:
        return cmds.referenceQuery(obj, isNodeReferenced=True)
    except Exception:
        return False


def _get_dag_path(node_name):
    """Retourne MDagPath pour un node."""
    sel = om2.MSelectionList()
    sel.add(node_name)
    return sel.getDagPath(0)


def _get_mesh_fn(transform_name):
    """Retourne MFnMesh depuis un transform ayant un shape mesh."""
    try:
        dag = _get_dag_path(transform_name)
        dag.extendToShape()
        if dag.apiType() == om2.MFn.kMesh:
            return om2.MFnMesh(dag), dag
    except Exception:
        pass
    return None, None


def _get_world_matrix(transform_name):
    """Retourne MMatrix world du transform."""
    try:
        dag = _get_dag_path(transform_name)
        return dag.inclusiveMatrix()
    except Exception:
        return om2.MMatrix()


def _iter_mesh_transforms(root=None):
    """
    Itère sur tous les transforms ayant un shape mesh visible.
    Si root=None, itère sur toute la scène.
    Utilise MItDag (API 2.0).
    """
    results = []

    if root:
        try:
            sel = om2.MSelectionList()
            sel.add(root)
            root_dag = sel.getDagPath(0)
            it = om2.MItDag(om2.MItDag.kDepthFirst, om2.MFn.kTransform)
            it.reset(root_dag)
        except Exception:
            return results
    else:
        it = om2.MItDag(om2.MItDag.kDepthFirst, om2.MFn.kTransform)

    fn_dag = om2.MFnDagNode()

    while not it.isDone():
        dag = it.getPath()
        fn_dag.setObject(dag)

        # Cherche un shape mesh non intermédiaire
        for i in range(dag.childCount()):
            child = dag.child(i)
            if child.apiType() == om2.MFn.kMesh:
                fn_mesh_node = om2.MFnDependencyNode(child)
                try:
                    plug = fn_mesh_node.findPlug("intermediateObject", False)
                    if plug.asBool():
                        continue
                except Exception:
                    pass

                full_path = dag.fullPathName()
                if full_path not in results:
                    results.append(full_path)
                break

        it.next()

    return results


def _get_selected_transforms():
    """Transforms sélectionnés (résout shapes vers parents)."""
    sel = cmds.ls(sl=True, long=True) or []
    out = []
    seen = set()
    for obj in sel:
        if "." in obj:
            obj = obj.split(".")[0]
        if not cmds.objExists(obj):
            continue
        ntype = cmds.nodeType(obj)
        if ntype in ("mesh", "nurbsSurface"):
            parents = cmds.listRelatives(obj, parent=True, fullPath=True) or []
            if parents:
                obj = parents[0]
        if obj not in seen:
            seen.add(obj)
            out.append(obj)
    return out


# ============================================================
#  VIEWPORT COLORING — Lambert shaders + Display Layers
# ============================================================

# Noms des shaders et layers créés par l'outil
_IC_SHADER_EXACT    = "IC_shader_exact"
_IC_SHADER_SIMILAR  = "IC_shader_similar"
_IC_SHADER_UNIQUE   = "IC_shader_unique"
_IC_LAYER_EXACT     = "IC_Matches_Exact"
_IC_LAYER_SIMILAR   = "IC_Matches_Similar"
_IC_LAYER_UNIQUE    = "IC_Matches_Unique"

_IC_SAVED_SHADERS   = {}   # node -> [shadingEngine avant colorisation]


def _ensure_ic_shader(name, rgb):
    """
    Crée (ou récupère) un shader Lambert IC avec la couleur donnée.
    Retourne le shadingEngine associé.
    """
    sg_name = name + "_SG"
    if not cmds.objExists(name):
        shader = cmds.shadingNode("lambert", asShader=True, name=name)
        sg     = cmds.sets(renderable=True, noSurfaceShader=True,
                           empty=True, name=sg_name)
        cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader", f=True)
    else:
        sg_name = name + "_SG"
        if not cmds.objExists(sg_name):
            # recrée le SG si manquant
            sg = cmds.sets(renderable=True, noSurfaceShader=True,
                           empty=True, name=sg_name)
            cmds.connectAttr(name + ".outColor", sg + ".surfaceShader", f=True)

    # Set color
    try:
        cmds.setAttr(name + ".color", rgb[0], rgb[1], rgb[2], type="double3")
        cmds.setAttr(name + ".transparency", 0.35, 0.35, 0.35, type="double3")
    except Exception:
        pass

    return sg_name


def _ensure_display_layer(layer_name, color_index):
    """
    Crée (ou récupère) un display layer avec un index couleur Maya.
    color_index : entier Maya (14=vert, 12=rouge, 26=orange approx)
    """
    if not cmds.objExists(layer_name):
        cmds.createDisplayLayer(name=layer_name, empty=True)
    try:
        cmds.setAttr(layer_name + ".color", color_index)
        cmds.setAttr(layer_name + ".displayType", 0)  # Normal (pas de template)
    except Exception:
        pass
    return layer_name


def _add_to_display_layer(layer_name, transforms):
    """Ajoute des transforms à un display layer."""
    existing = [t for t in transforms if cmds.objExists(t)]
    if existing:
        try:
            cmds.editDisplayLayerMembers(layer_name, *existing, noRecurse=True)
        except Exception:
            pass


def _apply_ic_shader(transform, sg_name):
    """Assigne le shader IC au(x) shape(s) visible(s) d'un transform."""
    global _IC_SAVED_SHADERS
    shapes = cmds.listRelatives(transform, shapes=True, fullPath=True) or []
    saved = []
    for shape in shapes:
        try:
            # Sauvegarde le SG courant
            cur_sgs = cmds.listConnections(shape, type="shadingEngine") or []
            saved.extend(cur_sgs)
            # Assigne le nouveau
            cmds.sets(shape, e=True, forceElement=sg_name)
        except Exception:
            pass
    _IC_SAVED_SHADERS[transform] = saved


def _restore_ic_shader(transform):
    """Restaure le shader original d'un transform."""
    global _IC_SAVED_SHADERS
    saved_sgs = _IC_SAVED_SHADERS.get(transform, [])
    shapes = cmds.listRelatives(transform, shapes=True, fullPath=True) or []
    for shape in shapes:
        for sg in saved_sgs:
            if cmds.objExists(sg):
                try:
                    cmds.sets(shape, e=True, forceElement=sg)
                except Exception:
                    pass
                break
        else:
            # Fallback : assigne lambert1
            try:
                cmds.sets(shape, e=True, forceElement="initialShadingGroup")
            except Exception:
                pass
    _IC_SAVED_SHADERS.pop(transform, None)


def _remove_from_display_layers(transforms):
    """Retire les transforms des layers IC."""
    for layer in (_IC_LAYER_EXACT, _IC_LAYER_SIMILAR, _IC_LAYER_UNIQUE):
        if not cmds.objExists(layer):
            continue
        members = cmds.editDisplayLayerMembers(layer, query=True) or []
        to_remove = [t for t in transforms
                     if cmds.objExists(t) and _short(t) in [_short(m) for m in members]]
        if to_remove:
            try:
                cmds.editDisplayLayerMembers("defaultLayer", *to_remove, noRecurse=True)
            except Exception:
                pass


def _delete_ic_layers_and_shaders():
    """Supprime tous les layers et shaders IC (cleanup complet)."""
    for layer in (_IC_LAYER_EXACT, _IC_LAYER_SIMILAR, _IC_LAYER_UNIQUE):
        if cmds.objExists(layer):
            try:
                cmds.delete(layer)
            except Exception:
                pass
    for shader in (_IC_SHADER_EXACT, _IC_SHADER_SIMILAR, _IC_SHADER_UNIQUE):
        sg = shader + "_SG"
        if cmds.objExists(sg):
            try:
                cmds.delete(sg)
            except Exception:
                pass
        if cmds.objExists(shader):
            try:
                cmds.delete(shader)
            except Exception:
                pass


# ============================================================
#  TAGGING
# ============================================================
def _add_ic_attr(node, attr_name, value, attr_type="string"):
    """Ajoute un attribut custom ic_ si absent, et le set."""
    if not cmds.objExists(node):
        return
    if not cmds.attributeQuery(attr_name, node=node, exists=True):
        try:
            if attr_type == "string":
                cmds.addAttr(node, ln=attr_name, dt="string")
            elif attr_type == "int":
                cmds.addAttr(node, ln=attr_name, at="long")
        except Exception:
            pass
    try:
        if attr_type == "string":
            cmds.setAttr(node + "." + attr_name, value, type="string")
        else:
            cmds.setAttr(node + "." + attr_name, int(value))
    except Exception:
        pass


def _tag_node(node, ic_type, group_id, source=""):
    _add_ic_attr(node, ATTR_IC_TYPE,   ic_type,        "string")
    _add_ic_attr(node, ATTR_IC_GROUP,  group_id,       "int")
    _add_ic_attr(node, ATTR_IC_SOURCE, source,         "string")


# ============================================================
#  MESH SCANNER — OpenMaya 2.0
# ============================================================
class MeshSignature(object):
    """Signature géométrique d'un mesh pour comparaison."""

    __slots__ = (
        "transform", "vertex_count", "face_count", "edge_count",
        "bbox_x", "bbox_y", "bbox_z",
        "bbox_rx", "bbox_ry", "bbox_rz",
        "vertex_hash"
    )

    def __init__(self):
        self.transform    = ""
        self.vertex_count = 0
        self.face_count   = 0
        self.edge_count   = 0
        self.bbox_x = self.bbox_y = self.bbox_z = 0.0
        self.bbox_rx = self.bbox_ry = self.bbox_rz = 0.0
        self.vertex_hash  = ""


def _round_to(v, tol):
    """Arrondit v à la tolérance donnée."""
    if tol <= 0:
        return v
    return round(v / tol) * tol


def _compute_signature(transform_name, vertex_hash=False, tol=0.001):
    """
    Calcule la signature géométrique d'un mesh.
    Utilise MFnMesh (API 2.0) pour accès direct.
    """
    sig = MeshSignature()
    sig.transform = transform_name

    fn_mesh, dag = _get_mesh_fn(transform_name)
    if fn_mesh is None:
        return None

    sig.vertex_count = fn_mesh.numVertices
    sig.face_count   = fn_mesh.numPolygons
    sig.edge_count   = fn_mesh.numEdges

    # BBox via MFnMesh
    bb = fn_mesh.boundingBox
    bmin = bb.min
    bmax = bb.max

    # Dimensions triées (invariant à la rotation d'axes)
    dims = sorted([
        abs(bmax.x - bmin.x),
        abs(bmax.y - bmin.y),
        abs(bmax.z - bmin.z)
    ])
    sig.bbox_x, sig.bbox_y, sig.bbox_z = dims[0], dims[1], dims[2]

    # Ratios normalisés (invariants à l'échelle)
    max_dim = max(sig.bbox_x, sig.bbox_y, sig.bbox_z)
    if max_dim > 1e-9:
        sig.bbox_rx = sig.bbox_x / max_dim
        sig.bbox_ry = sig.bbox_y / max_dim
        sig.bbox_rz = sig.bbox_z / max_dim
    else:
        sig.bbox_rx = sig.bbox_ry = sig.bbox_rz = 0.0

    # Hash vertices (optionnel — lent mais précis)
    if vertex_hash:
        pts = fn_mesh.getPoints(om2.MSpace.kObject)
        h = hashlib.md5()
        for pt in pts:
            rx = _round_to(pt.x, tol)
            ry = _round_to(pt.y, tol)
            rz = _round_to(pt.z, tol)
            h.update(struct.pack("fff", rx, ry, rz))
        sig.vertex_hash = h.hexdigest()

    return sig


# ============================================================
#  MESH MATCHER
# ============================================================
class MatchResult(object):
    """Résultat de matching entre deux signatures."""
    def __init__(self, match_type, score=1.0):
        self.match_type = match_type  # MATCH_EXACT / MATCH_SIMILAR / MATCH_UNIQUE
        self.score      = score       # 0.0 = identique, +grand = plus différent


def _sig_distance(a, b, tol_ratio=0.01):
    """
    Distance normalisée entre deux signatures.
    Retourne 0.0 si identiques, >0 sinon.
    """
    # Compte de polygones / vertices / arêtes
    if a.vertex_count != b.vertex_count:
        return float("inf")
    if a.face_count != b.face_count:
        return float("inf")
    if a.edge_count != b.edge_count:
        return float("inf")

    # Distance sur les ratios bbox (invariant à l'échelle)
    dr = (abs(a.bbox_rx - b.bbox_rx) +
          abs(a.bbox_ry - b.bbox_ry) +
          abs(a.bbox_rz - b.bbox_rz))

    return dr


def _classify_pair(a, b, tol_similar=0.05, use_vertex_hash=False):
    """
    Classifie la relation entre deux signatures.
    """
    dist = _sig_distance(a, b)

    if dist == float("inf"):
        return MatchResult(MATCH_UNIQUE, float("inf"))

    if dist < 1e-6:
        # Exact uniquement si hash vertices activé ET identique.
        # Sans hash fiable, on reste volontairement en "similar" (safe-first).
        if use_vertex_hash and a.vertex_hash and b.vertex_hash:
            if a.vertex_hash == b.vertex_hash:
                return MatchResult(MATCH_EXACT, 0.0)
            return MatchResult(MATCH_SIMILAR, dist)
        return MatchResult(MATCH_SIMILAR, dist)

    if dist <= tol_similar:
        return MatchResult(MATCH_SIMILAR, dist)

    return MatchResult(MATCH_UNIQUE, dist)


def find_groups(signatures, tol_similar=0.05, use_vertex_hash=False):
    """
    Regroupe les signatures par similarité.
    Retourne:
        groups_exact   : {group_id: [transform, ...]}
        groups_similar : {group_id: [transform, ...]}
        uniques        : [transform, ...]
        group_type     : {group_id: MATCH_EXACT | MATCH_SIMILAR}
    """
    n = len(signatures)
    parent = list(range(n))  # Union-Find

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    # Comparaison O(n²) — acceptable pour centaines de meshes
    # Pour milliers, on ferait un bucket par (vc, fc, ec) d'abord
    buckets = defaultdict(list)
    for i, sig in enumerate(signatures):
        key = (sig.vertex_count, sig.face_count, sig.edge_count)
        buckets[key].append(i)

    group_match_type = {}  # group_root -> type

    for key, indices in buckets.items():
        for ii in range(len(indices)):
            for jj in range(ii + 1, len(indices)):
                i, j = indices[ii], indices[jj]
                res = _classify_pair(
                    signatures[i], signatures[j],
                    tol_similar=tol_similar,
                    use_vertex_hash=use_vertex_hash
                )
                if res.match_type in (MATCH_EXACT, MATCH_SIMILAR):
                    union(i, j)
                    # mémoriser le type du groupe (similar prime sur exact)
                    ri = find(i)
                    cur = group_match_type.get(ri, MATCH_EXACT)
                    if res.match_type == MATCH_SIMILAR:
                        group_match_type[ri] = MATCH_SIMILAR
                    elif cur != MATCH_SIMILAR:
                        group_match_type[ri] = MATCH_EXACT

    # Regroupement final
    raw_groups = defaultdict(list)
    for i, sig in enumerate(signatures):
        raw_groups[find(i)].append(i)

    groups_exact   = {}
    groups_similar = {}
    uniques        = []
    gid            = 0

    for root, members in raw_groups.items():
        if len(members) == 1:
            uniques.append(signatures[members[0]].transform)
            continue

        transforms = [signatures[m].transform for m in members]
        mtype = group_match_type.get(root, MATCH_EXACT)
        label = "group_{:03d}".format(gid)

        if mtype == MATCH_EXACT:
            groups_exact[label] = transforms
        else:
            groups_similar[label] = transforms

        gid += 1

    return groups_exact, groups_similar, uniques


# ============================================================
#  MASTER MANAGER
# ============================================================
class MasterManager(object):
    """Gère la création et l'organisation des masters."""

    def __init__(self):
        self.masters = {}  # group_label -> master_transform

    def _ensure_masters_group(self):
        if not cmds.objExists(MASTERS_GROUP):
            cmds.group(em=True, name=MASTERS_GROUP)
        return MASTERS_GROUP

    def _ensure_backup_group(self):
        if not cmds.objExists(BACKUP_GROUP):
            cmds.group(em=True, name=BACKUP_GROUP)
        return BACKUP_GROUP

    def _ensure_instances_group(self):
        if not cmds.objExists(INSTANCES_GROUP):
            cmds.group(em=True, name=INSTANCES_GROUP)
        return INSTANCES_GROUP

    def _find_existing_master(self, group_label):
        cached = self.masters.get(group_label)
        if cached and cmds.objExists(cached):
            return cached
        candidates = cmds.ls("MASTER_{}".format(group_label), long=True) or []
        if candidates:
            self.masters[group_label] = candidates[0]
            return candidates[0]
        return None

    def create_master(self, group_label, reference_mesh, group_id,
                      all_meshes, index=0):
        """
        Duplique reference_mesh comme MASTER et le place dans la grille.
        Retourne le nom court du master (pour comparaison fiable).
        """
        existing = self._find_existing_master(group_label)
        if existing:
            _tag_node(existing, "master", group_id, reference_mesh)
            return existing

        masters_grp = self._ensure_masters_group()

        # Duplicate (returnRoots=True pour ne récupérer que la racine)
        dup = cmds.duplicate(reference_mesh, rr=True)[0]
        master_name = "MASTER_{}".format(group_label)
        try:
            dup = cmds.rename(dup, master_name)
        except Exception:
            pass

        # Placement au centre du monde pour édition du master
        cmds.xform(dup, ws=True, t=(0, 0, 0))

        # Parent au groupe masters (retourne le chemin complet)
        try:
            dup = cmds.parent(dup, masters_grp)[0]
        except Exception:
            pass

        # Stocke le chemin COMPLET pour lookup fiable
        try:
            full_path = cmds.ls(dup, long=True)[0]
        except Exception:
            full_path = dup

        # Tag
        _tag_node(full_path, "master", group_id, reference_mesh)

        self.masters[group_label] = full_path
        return full_path

    def replace_with_instances(self, group_label, group_meshes,
                                preserve_transforms=True,
                                preserve_materials=True,
                                keep_hierarchy=False,
                                hide_original=True,
                                backup=True):
        """
        Remplace chaque mesh du groupe par une instance du master.
        Le mesh de référence (devenu master) est ignoré — on ne l'instancie pas.
        """
        if group_label not in self.masters:
            cmds.warning("[IC] Master non trouvé pour {}".format(group_label))
            return []

        master_path = self.masters[group_label]
        if not cmds.objExists(master_path):
            # Tentative de retrouver par nom court
            candidates = cmds.ls("MASTER_{}".format(group_label), long=True)
            if candidates:
                master_path = candidates[0]
                self.masters[group_label] = master_path
            else:
                cmds.warning("[IC] Master {} n'existe plus.".format(master_path))
                return []

        master_short = _short(master_path)
        # Collecte les chemins longs des meshes membres du groupe du master
        master_orig_longs = cmds.ls(master_path, long=True) or [master_path]

        instances_created = []
        instances_grp = self._ensure_instances_group()
        backup_grp = self._ensure_backup_group() if backup else None

        # Matériaux : en cas de signatures différentes dans le groupe d'instances,
        # on évite un transfert potentiellement destructif.
        source_mat_sig = {}
        group_mat_sigs = set()
        for gm in group_meshes:
            if cmds.objExists(gm):
                sig = _material_signature(gm)
                source_mat_sig[gm] = sig
                group_mat_sigs.add(sig)
        unsafe_material_mix = preserve_materials and len(group_mat_sigs) > 1
        if unsafe_material_mix:
            cmds.warning(
                "[IC] {}: matériaux différents détectés dans le groupe. "
                "Preserve Materials désactivé pour éviter de casser les instances.".format(group_label)
            )

        for mesh in group_meshes:
            if not cmds.objExists(mesh):
                continue

            # Exclure le master lui-même (comparaison nom court ET chemin long)
            mesh_longs = cmds.ls(mesh, long=True) or [mesh]
            if _short(mesh) == master_short:
                continue
            if any(ml in master_orig_longs for ml in mesh_longs):
                continue
            # Déjà traité sur un précédent run
            if cmds.attributeQuery(ATTR_IC_REPLACED, node=mesh, exists=True):
                try:
                    if cmds.getAttr(mesh + "." + ATTR_IC_REPLACED):
                        continue
                except Exception:
                    pass

            # Récupère world matrix via OpenMaya
            wm = _get_world_matrix(mesh)
            if _has_negative_scale(wm):
                cmds.warning("[IC] Mirror/negative scale détecté sur {}. "
                             "La matrice monde complète sera préservée.".format(mesh))

            # Backup
            if backup and backup_grp:
                try:
                    dup_bkp = cmds.duplicate(mesh, rr=True)[0]
                    cmds.parent(dup_bkp, backup_grp)
                    cmds.setAttr(dup_bkp + ".visibility", 0)
                except Exception:
                    pass

            # Create instance depuis le master
            inst_list = cmds.instance(master_path)
            if not inst_list:
                continue
            inst = inst_list[0]

            # Parent l'instance dans le groupe dédié (sauf keep hierarchy)
            if not keep_hierarchy:
                try:
                    inst = cmds.parent(inst, instances_grp)[0]
                except Exception:
                    pass

            # Preserve materials
            if preserve_materials and not unsafe_material_mix:
                _transfer_materials(mesh, inst)

            # Keep hierarchy : place l'instance dans le même parent que l'original
            if keep_hierarchy:
                orig_parent = cmds.listRelatives(mesh, parent=True, fullPath=True)
                if orig_parent:
                    par = orig_parent[0]
                    if par and not par.startswith("|" + MASTERS_GROUP):
                        try:
                            inst = cmds.parent(inst, par)[0]
                        except Exception:
                            pass

            # Applique la matrice monde complète pour préserver mirror/shear/etc.
            if preserve_transforms:
                try:
                    cmds.xform(inst, ws=True, matrix=_matrix_to_list(wm))
                except Exception:
                    pass

            # Hide/Delete original
            if hide_original:
                try:
                    cmds.setAttr(mesh + ".visibility", 0)
                except Exception:
                    pass
            else:
                try:
                    cmds.delete(mesh)
                except Exception:
                    pass

            # Tag instance
            _tag_node(inst, "instance", 0, master_path)
            _add_ic_attr(mesh, ATTR_IC_REPLACED, 1, "int")

            instances_created.append(inst)

        return instances_created


def _matrix_to_list(mmat):
    """Convertit MMatrix en liste 16 float (row-major) pour cmds.xform(matrix=...)."""
    return [mmat[r][c] for r in range(4) for c in range(4)]


def _has_negative_scale(mmat):
    """
    Détecte une inversion de handedness (scale négatif / mirror) via déterminant 3x3.
    """
    r00, r01, r02 = mmat[0], mmat[1], mmat[2]
    r10, r11, r12 = mmat[4], mmat[5], mmat[6]
    r20, r21, r22 = mmat[8], mmat[9], mmat[10]

    det = (
        r00 * (r11 * r22 - r12 * r21)
        - r01 * (r10 * r22 - r12 * r20)
        + r02 * (r10 * r21 - r11 * r20)
    )
    return det < 0.0


def _material_signature(transform):
    """Signature simple des shadingEngines connectés à la shape (triée)."""
    try:
        shapes = cmds.listRelatives(transform, shapes=True, fullPath=True) or []
        if not shapes:
            return tuple()
        sgs = cmds.listConnections(shapes[0], type="shadingEngine") or []
        return tuple(sorted(set(sgs)))
    except Exception:
        return tuple()


def _transfer_materials(source, dest):
    """Transfère les shading groups de source vers dest."""
    try:
        src_shapes = cmds.listRelatives(source, shapes=True, fullPath=True) or []
        dst_shapes = cmds.listRelatives(dest,   shapes=True, fullPath=True) or []
        if src_shapes and dst_shapes:
            sgs = cmds.listConnections(src_shapes[0], type="shadingEngine") or []
            if sgs:
                cmds.sets(dst_shapes[0], e=True, forceElement=sgs[0])
    except Exception:
        pass


# ============================================================
#  INSTANCE CLEANER CORE
# ============================================================
class InstanceCleaner(object):
    def __init__(self):
        self.master_manager    = MasterManager()
        self.signatures        = []       # [MeshSignature]
        self.groups_exact      = {}       # label -> [transforms]
        self.groups_similar    = {}
        self.uniques           = []
        self.validated_groups  = {}       # label -> {meshes, type, accepted}
        self.colored_meshes    = []       # pour cleanup couleurs
        self._scan_root        = None

    # ----------------------------------------------------------
    # SCAN
    # ----------------------------------------------------------
    def scan(self, root=None, use_vertex_hash=False, tol_similar=0.05,
             progress_cb=None):
        """
        Scanne les meshes sous root (ou sélection si None).
        Retourne le nombre de groupes trouvés.
        """
        self._scan_root = root

        # Collect transforms
        if root:
            transforms = _iter_mesh_transforms(root)
        else:
            sel = _get_selected_transforms()
            transforms = []
            for s in sel:
                sub = _iter_mesh_transforms(s)
                if sub:
                    transforms.extend(sub)
                elif cmds.nodeType(s) == "transform":
                    fn_mesh, _ = _get_mesh_fn(s)
                    if fn_mesh:
                        transforms.append(s)

            if not transforms:
                transforms = _iter_mesh_transforms(None)

        # Remove duplicates
        seen = set()
        unique_transforms = []
        for t in transforms:
            if t not in seen:
                seen.add(t)
                unique_transforms.append(t)
        transforms = unique_transforms

        if not transforms:
            cmds.warning("[IC] Aucun mesh trouvé.")
            return 0

        # Compute signatures
        self.signatures = []
        total = len(transforms)

        for i, t in enumerate(transforms):
            if progress_cb:
                progress_cb(int(i * 100.0 / total), t)

            sig = _compute_signature(t, vertex_hash=use_vertex_hash)
            if sig:
                self.signatures.append(sig)

        # Match groups
        self.groups_exact, self.groups_similar, self.uniques = find_groups(
            self.signatures,
            tol_similar=tol_similar,
            use_vertex_hash=use_vertex_hash
        )

        # Initialise validated_groups
        self.validated_groups = {}
        gid = 0
        for label, meshes in self.groups_exact.items():
            self.validated_groups[label] = {
                "meshes":    meshes,
                "type":      MATCH_EXACT,
                "accepted":  None,   # None = non décidé
                "group_id":  gid
            }
            gid += 1
        for label, meshes in self.groups_similar.items():
            self.validated_groups[label] = {
                "meshes":    meshes,
                "type":      MATCH_SIMILAR,
                "accepted":  None,
                "group_id":  gid
            }
            gid += 1

        return len(self.validated_groups)

    # ----------------------------------------------------------
    # COLORING
    # ----------------------------------------------------------
    def apply_colors(self):
        """
        Colore les meshes via shaders Lambert dédiés + display layers.
        - Exact   → vert  (IC_Layer_Exact,   IC_shader_exact)
        - Similar → orange (IC_Layer_Similar, IC_shader_similar)
        - Unique  → rouge  (IC_Layer_Unique,  IC_shader_unique)
        """
        self._clear_colors()

        # Crée/récupère shaders et layers
        sg_exact   = _ensure_ic_shader(_IC_SHADER_EXACT,   COLOR_EXACT)
        sg_similar = _ensure_ic_shader(_IC_SHADER_SIMILAR, COLOR_SIMILAR)
        sg_unique  = _ensure_ic_shader(_IC_SHADER_UNIQUE,  COLOR_UNIQUE)

        _ensure_display_layer(_IC_LAYER_EXACT,   14)  # vert Maya
        _ensure_display_layer(_IC_LAYER_SIMILAR, 12)  # rouge Maya (orange n'existe pas en index)
        _ensure_display_layer(_IC_LAYER_UNIQUE,  13)  # rouge vif

        exact_meshes   = []
        similar_meshes = []

        for label, info in self.validated_groups.items():
            mtype = info["type"]
            if mtype == MATCH_EXACT:
                sg    = sg_exact
                layer = _IC_LAYER_EXACT
                bucket = exact_meshes
            else:
                sg    = sg_similar
                layer = _IC_LAYER_SIMILAR
                bucket = similar_meshes

            for m in info["meshes"]:
                if cmds.objExists(m):
                    _apply_ic_shader(m, sg)
                    bucket.append(m)
                    self.colored_meshes.append(m)

        unique_meshes = []
        for m in self.uniques:
            if cmds.objExists(m):
                _apply_ic_shader(m, sg_unique)
                unique_meshes.append(m)
                self.colored_meshes.append(m)

        # Ajoute aux display layers
        if exact_meshes:
            _add_to_display_layer(_IC_LAYER_EXACT,   exact_meshes)
        if similar_meshes:
            _add_to_display_layer(_IC_LAYER_SIMILAR, similar_meshes)
        if unique_meshes:
            _add_to_display_layer(_IC_LAYER_UNIQUE,  unique_meshes)

    def _clear_colors(self):
        """Restaure les shaders originaux et retire des display layers."""
        for m in self.colored_meshes:
            if cmds.objExists(m):
                _restore_ic_shader(m)
        _remove_from_display_layers(self.colored_meshes)
        self.colored_meshes = []

    def clear_colors(self, delete_layers=False):
        """Public — optionnellement supprime aussi les layers/shaders IC."""
        self._clear_colors()
        if delete_layers:
            _delete_ic_layers_and_shaders()

    # ----------------------------------------------------------
    # VALIDATION
    # ----------------------------------------------------------
    def accept_group(self, label):
        if label in self.validated_groups:
            self.validated_groups[label]["accepted"] = True

    def reject_group(self, label):
        if label in self.validated_groups:
            self.validated_groups[label]["accepted"] = False

    # ----------------------------------------------------------
    # SELECT / FRAME
    # ----------------------------------------------------------
    def _get_main_viewport_panel(self):
        """
        Retourne le premier modelPanel visible disponible.
        Plus fiable que getPanel(withFocus=True) quand l'UI a le focus.
        """
        for panel in cmds.getPanel(type="modelPanel") or []:
            try:
                if cmds.modelPanel(panel, q=True, exists=True):
                    return panel
            except Exception:
                pass
        return None

    def select_group(self, label):
        if label not in self.validated_groups:
            return
        meshes = [m for m in self.validated_groups[label]["meshes"]
                  if cmds.objExists(m)]
        if meshes:
            cmds.select(meshes, r=True)

    def isolate_group(self, label):
        """
        Isole le groupe dans le viewport principal.
        Quitte automatiquement une isolation précédente si active.
        """
        panel = self._get_main_viewport_panel()
        if not panel:
            return

        # Quitter l'isolation précédente si active
        try:
            if cmds.isolateSelect(panel, q=True, state=True):
                cmds.isolateSelect(panel, state=0)
        except Exception:
            pass

        self.select_group(label)

        try:
            cmds.isolateSelect(panel, state=1)
            cmds.isolateSelect(panel, addSelected=True)
        except Exception:
            pass

    def frame_group(self, label):
        self.select_group(label)
        try:
            cmds.viewFit()
        except Exception:
            pass

    def exit_isolate(self):
        panel = self._get_main_viewport_panel()
        if panel:
            try:
                cmds.isolateSelect(panel, state=0)
            except Exception:
                pass

    # ----------------------------------------------------------
    # CREATE MASTERS + REPLACE
    # ----------------------------------------------------------
    def create_masters_and_replace(self,
                                   preserve_transforms=True,
                                   preserve_materials=True,
                                   keep_hierarchy=False,
                                   hide_original=True,
                                   backup=True):
        """
        Pour chaque groupe accepté :
          1. Crée le master
          2. Remplace par instances
        Retourne stats dict.
        """
        # Important: retire les shaders preview AVANT duplication du master.
        self._clear_colors()

        accepted = {k: v for k, v in self.validated_groups.items()
                    if v["accepted"] is True}

        if not accepted:
            cmds.warning("[IC] Aucun groupe accepté.")
            return {}

        stats = {
            "masters_created":   0,
            "instances_created": 0,
            "meshes_processed":  0,
        }

        with UndoChunk("InstanceCleanerReplace"):
            for idx, (label, info) in enumerate(accepted.items()):
                meshes   = [m for m in info["meshes"] if cmds.objExists(m)]
                group_id = info["group_id"]

                if not meshes:
                    continue

                # Master = premier mesh du groupe
                ref_mesh = meshes[0]

                master = self.master_manager.create_master(
                    group_label=label,
                    reference_mesh=ref_mesh,
                    group_id=group_id,
                    all_meshes=meshes,
                    index=idx
                )
                stats["masters_created"] += 1

                # Replace (tout sauf le master lui-même)
                instances = self.master_manager.replace_with_instances(
                    group_label=label,
                    group_meshes=meshes,
                    preserve_transforms=preserve_transforms,
                    preserve_materials=preserve_materials,
                    keep_hierarchy=keep_hierarchy,
                    hide_original=hide_original,
                    backup=backup
                )
                stats["instances_created"] += len(instances)
                stats["meshes_processed"]  += len(meshes)

        self._clear_colors()
        return stats

    # ----------------------------------------------------------
    # REPORT
    # ----------------------------------------------------------
    def get_report(self):
        n_exact   = sum(len(v["meshes"]) for v in self.validated_groups.values()
                        if v["type"] == MATCH_EXACT)
        n_similar = sum(len(v["meshes"]) for v in self.validated_groups.values()
                        if v["type"] == MATCH_SIMILAR)
        n_total   = len(self.signatures)

        accepted  = [k for k, v in self.validated_groups.items() if v["accepted"] is True]
        n_acc_meshes = sum(len(self.validated_groups[k]["meshes"]) for k in accepted)
        saving    = max(0, n_acc_meshes - len(accepted)) if accepted else 0

        return {
            "total_scanned":   n_total,
            "exact_groups":    len(self.groups_exact),
            "similar_groups":  len(self.groups_similar),
            "unique_meshes":   len(self.uniques),
            "accepted_groups": len(accepted),
            "meshes_in_accepted": n_acc_meshes,
            "estimated_saving":   saving,
        }


# ============================================================
#  UI WIDGETS PARTAGÉS (style Mesh Organizer)
# ============================================================
class ColorBtn(QPushButton):
    def __init__(self, text="", tip="", bg="#2d2d2d", fg="#a0a0a0",
                 w=None, h=28, parent=None):
        super(ColorBtn, self).__init__(text, parent)
        if w:
            self.setFixedSize(w, h)
        else:
            self.setFixedHeight(h)
        self.setToolTip(tip)
        self._bg = bg
        self._fg = fg
        self._update_style()

    def _update_style(self):
        lighter = QColor(self._bg).lighter(130).name()
        self.setStyleSheet("""
            QPushButton {{
                background-color: {bg};
                color: {fg};
                border: 1px solid #222;
                border-radius: 3px;
                font-weight: bold;
                font-size: 10px;
                padding: 2px 8px;
            }}
            QPushButton:hover {{
                background-color: {bgh};
                border-color: #444;
            }}
            QPushButton:pressed {{
                background-color: #1a1a1a;
            }}
        """.format(bg=self._bg, fg=self._fg, bgh=lighter))


class SectionLabel(QLabel):
    def __init__(self, text, parent=None):
        super(SectionLabel, self).__init__(text, parent)
        self.setStyleSheet("""
            color: #555555;
            font-size: 9px;
            font-weight: bold;
            padding: 4px 0 2px 0;
            border-bottom: 1px solid #2a2a2a;
        """)


class ParamSlider(QWidget):
    if PYSIDE_VERSION == 6:
        valueChanged = Signal(float)
    else:
        valueChanged = QtCore.Signal(float)

    def __init__(self, label, min_val, max_val, default,
                 decimals=1, label_width=82, parent=None):
        super(ParamSlider, self).__init__(parent)
        self._decimals = decimals
        self._multiplier = 10 ** decimals
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._label = QLabel(label)
        self._label.setFixedWidth(label_width)
        self._label.setStyleSheet("color: #707070; font-size: 9px;")
        self._slider = QSlider(Qt.Horizontal)
        self._slider.setRange(int(min_val * self._multiplier),
                               int(max_val * self._multiplier))
        self._slider.setValue(int(default * self._multiplier))
        self._spin = QDoubleSpinBox()
        self._spin.setRange(min_val, max_val)
        self._spin.setDecimals(decimals)
        self._spin.setValue(default)
        self._spin.setFixedWidth(58)
        self._spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        layout.addWidget(self._label)
        layout.addWidget(self._slider)
        layout.addWidget(self._spin)
        self._slider.valueChanged.connect(self._on_slider)
        self._spin.valueChanged.connect(self._on_spin)

    def _on_slider(self, val):
        real_val = val / float(self._multiplier)
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

    def setValue(self, val):
        self._spin.setValue(val)


# ============================================================
#  GROUP LIST ITEM
# ============================================================
class GroupItem(QWidget):
    """Widget représentant un groupe dans la liste."""

    if PYSIDE_VERSION == 6:
        accept_clicked = Signal(str)
        reject_clicked = Signal(str)
        select_clicked = Signal(str)
        isolate_clicked = Signal(str)
        frame_clicked   = Signal(str)
    else:
        accept_clicked  = QtCore.Signal(str)
        reject_clicked  = QtCore.Signal(str)
        select_clicked  = QtCore.Signal(str)
        isolate_clicked = QtCore.Signal(str)
        frame_clicked   = QtCore.Signal(str)

    def __init__(self, label, info, parent=None):
        super(GroupItem, self).__init__(parent)
        self.label = label
        self.info  = info
        self._build()
        self._update_state()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 3, 4, 3)
        layout.setSpacing(2)

        # Header row
        hdr = QHBoxLayout()
        hdr.setSpacing(4)

        mtype = self.info["type"]
        n     = len(self.info["meshes"])

        if mtype == MATCH_EXACT:
            badge_color = "#1e6e30"
            badge_text  = "EXACT"
        else:
            badge_color = "#7a4000"
            badge_text  = "SIMILAR"

        badge = QLabel(badge_text)
        badge.setFixedSize(50, 16)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet("""
            background: {bg};
            color: #e0e0e0;
            font-size: 8px;
            font-weight: bold;
            border-radius: 2px;
        """.format(bg=badge_color))

        name_lbl = QLabel(self.label)
        name_lbl.setStyleSheet("color: #909090; font-size: 10px; font-weight: bold;")

        count_lbl = QLabel("{}×".format(n))
        count_lbl.setStyleSheet("color: #606060; font-size: 9px;")

        hdr.addWidget(badge)
        hdr.addWidget(name_lbl)
        hdr.addStretch()
        hdr.addWidget(count_lbl)

        # Actions row
        acts = QHBoxLayout()
        acts.setSpacing(2)

        self.sel_btn  = ColorBtn("SEL",     "Sélectionner",  "#252525", "#909090", 36, 20)
        self.iso_btn  = ColorBtn("ISO",     "Isoler",        "#252535", "#9090c0", 36, 20)
        self.frm_btn  = ColorBtn("FRAME",   "Cadrer",        "#252535", "#9090c0", 44, 20)
        self.acc_btn  = ColorBtn("✓ ACC",   "Accepter",      "#1a3a1a", "#60d060", 50, 20)
        self.rej_btn  = ColorBtn("✗ REJ",   "Rejeter",       "#3a1a1a", "#d06060", 50, 20)

        self.sel_btn.clicked.connect(lambda: self.select_clicked.emit(self.label))
        self.iso_btn.clicked.connect(lambda: self.isolate_clicked.emit(self.label))
        self.frm_btn.clicked.connect(lambda: self.frame_clicked.emit(self.label))
        self.acc_btn.clicked.connect(lambda: self.accept_clicked.emit(self.label))
        self.rej_btn.clicked.connect(lambda: self.reject_clicked.emit(self.label))

        acts.addWidget(self.sel_btn)
        acts.addWidget(self.iso_btn)
        acts.addWidget(self.frm_btn)
        acts.addStretch()
        acts.addWidget(self.acc_btn)
        acts.addWidget(self.rej_btn)

        layout.addLayout(hdr)
        layout.addLayout(acts)

        # Status indicator
        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("color: #505050; font-size: 8px;")
        layout.addWidget(self.status_lbl)

        self.setStyleSheet("""
            QWidget {
                background: #232323;
                border: 1px solid #2e2e2e;
                border-radius: 3px;
            }
        """)

    def _update_state(self):
        acc = self.info["accepted"]
        if acc is True:
            self.status_lbl.setText("✓ Accepté")
            self.status_lbl.setStyleSheet("color: #50c050; font-size: 8px;")
            self.setStyleSheet("""
                QWidget {
                    background: #1e2a1e;
                    border: 1px solid #2a4a2a;
                    border-radius: 3px;
                }
            """)
        elif acc is False:
            self.status_lbl.setText("✗ Rejeté")
            self.status_lbl.setStyleSheet("color: #c05050; font-size: 8px;")
            self.setStyleSheet("""
                QWidget {
                    background: #2a1e1e;
                    border: 1px solid #4a2a2a;
                    border-radius: 3px;
                }
            """)
        else:
            self.status_lbl.setText("En attente de validation")
            self.status_lbl.setStyleSheet("color: #505050; font-size: 8px;")
            self.setStyleSheet("""
                QWidget {
                    background: #232323;
                    border: 1px solid #2e2e2e;
                    border-radius: 3px;
                }
            """)

    def refresh(self):
        self._update_state()


# ============================================================
#  MAIN UI
# ============================================================
class InstanceCleanerUI(QDialog):
    WINDOW_TITLE = "Instance Cleaner V1.1"

    def __init__(self, parent=maya_main_window()):
        super(InstanceCleanerUI, self).__init__(parent)
        self.cleaner      = InstanceCleaner()
        self.group_items  = {}   # label -> GroupItem
        self._is_isolated = False

        self.setWindowTitle(self.WINDOW_TITLE)
        self.setMinimumWidth(400)
        self.setMinimumHeight(300)
        self.setWindowFlags(Qt.Window | Qt.WindowCloseButtonHint)

        self._build_ui()
        self._apply_stylesheet()

    # ----------------------------------------------------------
    # STYLESHEET
    # ----------------------------------------------------------
    def _apply_stylesheet(self):
        self.setStyleSheet("""
            QDialog  { background-color: #1e1e1e; }
            QLabel   { color: #707070; font-size: 10px; }

            QScrollArea { border: none; background: transparent; }
            QScrollBar:vertical {
                background: #1a1a1a; width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #3a3a3a;
                border-radius: 4px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover { background: #d32f2f; }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical { height: 0px; }

            QSlider::groove:horizontal {
                height: 4px; background: #2a2a2a; border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #d32f2f; width: 12px;
                margin: -4px 0; border-radius: 6px;
            }
            QSlider::handle:horizontal:hover { background: #e53935; }
            QSlider::sub-page:horizontal {
                background: #d32f2f; border-radius: 2px;
            }

            QSpinBox, QDoubleSpinBox {
                background: #252525; color: #a0a0a0;
                border: 1px solid #303030; border-radius: 3px;
                padding: 2px; font-size: 10px;
            }

            QCheckBox {
                color: #707070; font-size: 10px; spacing: 4px;
            }
            QCheckBox::indicator {
                width: 14px; height: 14px;
                border-radius: 3px; border: 1px solid #3a3a3a;
                background: #252525;
            }
            QCheckBox::indicator:checked {
                background: #d32f2f; border-color: #d32f2f;
            }

            QComboBox {
                background: #252525; color: #a0a0a0;
                border: 1px solid #303030; border-radius: 3px;
                padding: 4px 8px; font-size: 10px;
            }
            QComboBox:hover { border-color: #d32f2f; }
            QComboBox::drop-down { border: none; width: 18px; }
            QComboBox::down-arrow {
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 4px solid #707070;
                margin-right: 4px;
            }
            QComboBox QAbstractItemView {
                background: #2a2a2a; border: 1px solid #404040;
                selection-background-color: #d32f2f; color: #a0a0a0;
            }

            QProgressBar {
                background: #1a1a1a; border: 1px solid #303030;
                border-radius: 3px; text-align: center;
                color: #707070; font-size: 9px;
            }
            QProgressBar::chunk {
                background: #d32f2f; border-radius: 2px;
            }

            QTabWidget::pane {
                border: 1px solid #2a2a2a;
                background: #1e1e1e;
            }
            QTabBar::tab {
                background: #252525; color: #606060;
                padding: 5px 12px; font-size: 9px; font-weight: bold;
                border: 1px solid #2a2a2a;
                border-bottom: none;
            }
            QTabBar::tab:selected {
                background: #1e1e1e; color: #d32f2f;
                border-color: #3a3a3a;
            }
            QTabBar::tab:hover { color: #a0a0a0; }
        """)

    # ----------------------------------------------------------
    # UI BUILD
    # ----------------------------------------------------------
    def _build_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(8, 8, 8, 8)
        main.setSpacing(6)

        # Title
        title = QLabel("⚡ INSTANCE CLEANER")
        title.setStyleSheet("""
            color: #d32f2f;
            font-size: 13px;
            font-weight: bold;
            padding-bottom: 4px;
        """)
        main.addWidget(title)

        # Tabs
        self._tabs = QTabWidget()
        main.addWidget(self._tabs)

        self._tabs.addTab(self._build_tab_scan(),    "1 · SCAN")
        self._tabs.addTab(self._build_tab_groups(),  "2 · GROUPES")
        self._tabs.addTab(self._build_tab_replace(), "3 · REPLACE")
        self._tabs.addTab(self._build_tab_report(),  "RAPPORT")

    # ------ TAB 1 : SCAN ------
    def _build_tab_scan(self):
        w = QWidget()
        ly = QVBoxLayout(w)
        ly.setContentsMargins(6, 6, 6, 6)
        ly.setSpacing(6)

        ly.addWidget(SectionLabel("SOURCE"))

        self.scan_mode_combo = QComboBox()
        self.scan_mode_combo.addItems(["Sélection courante", "Scène entière"])
        ly.addLayout(self._row("Mode scan", self.scan_mode_combo))

        ly.addWidget(SectionLabel("OPTIONS DE DÉTECTION"))

        self.tol_slider = ParamSlider("Tolérance", 0.0, 0.5, 0.02, 3, label_width=90)
        self.tol_slider.setToolTip(
            "Tolérance bbox ratio pour 'Similar Match'.\n"
            "0 = exact uniquement."
        )
        ly.addWidget(self.tol_slider)

        self.vertex_hash_check = QCheckBox("Hash vertices (précis, plus lent)")
        self.vertex_hash_check.setChecked(False)
        ly.addWidget(self.vertex_hash_check)

        ly.addWidget(SectionLabel("PROGRESSION"))

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(16)
        ly.addWidget(self.progress_bar)

        self.progress_label = QLabel("Prêt.")
        self.progress_label.setStyleSheet("color: #505050; font-size: 9px;")
        ly.addWidget(self.progress_label)

        ly.addStretch()

        # Buttons
        scan_btn = ColorBtn("🔍  SCAN RÉPÉTITIONS", "",
                             "#1a2a3a", "#60a0d0", h=32)
        scan_btn.clicked.connect(self.do_scan)
        ly.addWidget(scan_btn)

        return w

    # ------ TAB 2 : GROUPES ------
    def _build_tab_groups(self):
        w = QWidget()
        ly = QVBoxLayout(w)
        ly.setContentsMargins(6, 6, 6, 6)
        ly.setSpacing(4)

        # Bulk actions
        bulk = QHBoxLayout()
        bulk.setSpacing(3)

        acc_all_btn = ColorBtn("✓ TOUT ACCEPTER", "", "#1a3a1a", "#60d060", h=24)
        rej_all_btn = ColorBtn("✗ TOUT REJETER",  "", "#3a1a1a", "#d06060", h=24)
        acc_all_btn.clicked.connect(self.do_accept_all)
        rej_all_btn.clicked.connect(self.do_reject_all)
        reset_btn   = ColorBtn("↺ RESET",         "", "#2a2a2a", "#909090", h=24)
        reset_btn.clicked.connect(self.do_reset_validation)

        bulk.addWidget(acc_all_btn)
        bulk.addWidget(rej_all_btn)
        bulk.addWidget(reset_btn)
        ly.addLayout(bulk)

        iso_exit_btn = ColorBtn("📷  EXIT ISOLATE", "",
                                 "#252535", "#9090c0", h=22)
        iso_exit_btn.clicked.connect(self.do_exit_isolate)
        ly.addWidget(iso_exit_btn)

        # Filter bar
        filter_row = QHBoxLayout()
        filter_lbl = QLabel("Filtre:")
        filter_lbl.setFixedWidth(36)
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["Tous", "Exact", "Similaire", "Non décidé"])
        self.filter_combo.currentIndexChanged.connect(self.refresh_group_list)
        filter_row.addWidget(filter_lbl)
        filter_row.addWidget(self.filter_combo)
        ly.addLayout(filter_row)

        # Scroll area for group items
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        self.groups_container = QWidget()
        self.groups_layout    = QVBoxLayout(self.groups_container)
        self.groups_layout.setContentsMargins(0, 0, 0, 0)
        self.groups_layout.setSpacing(3)
        self.groups_layout.addStretch()

        scroll.setWidget(self.groups_container)
        ly.addWidget(scroll)

        return w

    # ------ TAB 3 : REPLACE ------
    def _build_tab_replace(self):
        w = QWidget()
        ly = QVBoxLayout(w)
        ly.setContentsMargins(6, 6, 6, 6)
        ly.setSpacing(6)

        ly.addWidget(SectionLabel("OPTIONS REMPLACEMENT"))

        self.pres_transforms_check = QCheckBox("Preserve Transforms")
        self.pres_transforms_check.setChecked(True)
        ly.addWidget(self.pres_transforms_check)

        self.pres_materials_check = QCheckBox("Preserve Materials")
        self.pres_materials_check.setChecked(True)
        ly.addWidget(self.pres_materials_check)

        self.keep_hierarchy_check = QCheckBox("Keep Hierarchy")
        self.keep_hierarchy_check.setChecked(False)
        ly.addWidget(self.keep_hierarchy_check)

        self.hide_original_check = QCheckBox("Masquer les originaux (non-destructif)")
        self.hide_original_check.setChecked(True)
        ly.addWidget(self.hide_original_check)

        ly.addWidget(SectionLabel("BACKUP"))

        self.backup_check = QCheckBox("Backup obligatoire (_INSTANCE_CLEANER_BACKUP)")
        self.backup_check.setChecked(True)
        ly.addWidget(self.backup_check)

        note = QLabel(
            "Seuls les groupes ✓ acceptés seront traités.\n"
            "Un master par groupe est placé au centre du monde."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#424242;font-size:9px;font-style:italic;")
        ly.addWidget(note)

        ly.addStretch()

        go_btn = ColorBtn("⚡  CRÉER MASTERS + REMPLACER", "",
                           "#1e3a1a", "#80e060", h=34)
        go_btn.clicked.connect(self.do_replace)
        ly.addWidget(go_btn)

        return w

    # ------ TAB 4 : RAPPORT ------
    def _build_tab_report(self):
        w = QWidget()
        ly = QVBoxLayout(w)
        ly.setContentsMargins(6, 6, 6, 6)
        ly.setSpacing(4)

        self.report_labels = {}
        fields = [
            ("total_scanned",     "Meshes scannés"),
            ("exact_groups",      "Groupes exacts"),
            ("similar_groups",    "Groupes similaires"),
            ("unique_meshes",     "Meshes uniques"),
            ("accepted_groups",   "Groupes acceptés"),
            ("meshes_in_accepted","Meshes dans groupes acc."),
            ("estimated_saving",  "Instances éco. estimées"),
        ]

        for key, label in fields:
            row = QHBoxLayout()
            lbl = QLabel(label + ":")
            lbl.setFixedWidth(160)
            val = QLabel("—")
            val.setStyleSheet("color: #a0a0a0; font-weight: bold;")
            row.addWidget(lbl)
            row.addWidget(val)
            row.addStretch()
            self.report_labels[key] = val
            ly.addLayout(row)

        ly.addSpacing(8)

        refresh_btn = ColorBtn("↺  ACTUALISER RAPPORT", "",
                               "#2a2a2a", "#909090", h=26)
        refresh_btn.clicked.connect(self.do_refresh_report)
        ly.addWidget(refresh_btn)

        ly.addStretch()
        return w

    def _row(self, label_text, widget, label_width=90):
        row = QHBoxLayout()
        row.setSpacing(4)
        lbl = QLabel(label_text)
        lbl.setFixedWidth(label_width)
        row.addWidget(lbl)
        row.addWidget(widget)
        return row

    # ----------------------------------------------------------
    # SLOT : SCAN
    # ----------------------------------------------------------
    def do_scan(self):
        self.cleaner._clear_colors()
        self.group_items = {}

        use_hash = self.vertex_hash_check.isChecked()
        tol      = self.tol_slider.value()

        # Root
        mode = self.scan_mode_combo.currentText()
        if "entière" in mode:
            root = None
        else:
            sel = _get_selected_transforms()
            root = sel[0] if sel else None

        def progress_cb(pct, label):
            self.progress_bar.setValue(pct)
            self.progress_label.setText(_short(label))
            QApplication.processEvents()

        self.progress_bar.setValue(0)
        n_groups = self.cleaner.scan(
            root=root,
            use_vertex_hash=use_hash,
            tol_similar=tol,
            progress_cb=progress_cb
        )

        self.progress_bar.setValue(100)
        self.progress_label.setText(
            "{} groupe(s) détecté(s) · {} unique(s)".format(
                n_groups, len(self.cleaner.uniques)
            )
        )

        self.refresh_group_list()
        self.do_refresh_report()
        self.cleaner.apply_colors()

        # Switch to groups tab
        self._tabs.setCurrentIndex(1)

    # ----------------------------------------------------------
    # SLOT : GROUP LIST
    # ----------------------------------------------------------
    def refresh_group_list(self):
        # Clear existing
        while self.groups_layout.count() > 1:  # keep stretch
            item = self.groups_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.group_items = {}

        filter_text = self.filter_combo.currentText()

        # Insert before stretch
        insert_idx = 0

        for label, info in self.cleaner.validated_groups.items():
            # Filter
            if filter_text == "Exact"      and info["type"] != MATCH_EXACT:
                continue
            if filter_text == "Similaire"  and info["type"] != MATCH_SIMILAR:
                continue
            if filter_text == "Non décidé" and info["accepted"] is not None:
                continue

            item_w = GroupItem(label, info)
            item_w.accept_clicked.connect(self.on_accept_group)
            item_w.reject_clicked.connect(self.on_reject_group)
            item_w.select_clicked.connect(self.on_select_group)
            item_w.isolate_clicked.connect(self.on_isolate_group)
            item_w.frame_clicked.connect(self.on_frame_group)

            self.groups_layout.insertWidget(insert_idx, item_w)
            self.group_items[label] = item_w
            insert_idx += 1

    def _refresh_item(self, label):
        if label in self.group_items:
            self.group_items[label].refresh()
        self.do_refresh_report()

    # ----------------------------------------------------------
    # SLOT : GROUP ACTIONS
    # ----------------------------------------------------------
    def on_accept_group(self, label):
        self.cleaner.accept_group(label)
        self._refresh_item(label)

    def on_reject_group(self, label):
        self.cleaner.reject_group(label)
        self._refresh_item(label)

    def on_select_group(self, label):
        self.cleaner.select_group(label)

    def on_isolate_group(self, label):
        self.cleaner.isolate_group(label)
        self._is_isolated = True

    def on_frame_group(self, label):
        self.cleaner.frame_group(label)

    def do_accept_all(self):
        for label in self.cleaner.validated_groups:
            self.cleaner.accept_group(label)
        self.refresh_group_list()
        self.do_refresh_report()

    def do_reject_all(self):
        for label in self.cleaner.validated_groups:
            self.cleaner.reject_group(label)
        self.refresh_group_list()
        self.do_refresh_report()

    def do_reset_validation(self):
        for label in self.cleaner.validated_groups:
            self.cleaner.validated_groups[label]["accepted"] = None
        self.refresh_group_list()
        self.do_refresh_report()

    def do_exit_isolate(self):
        self.cleaner.exit_isolate()
        self._is_isolated = False

    # ----------------------------------------------------------
    # SLOT : REPLACE
    # ----------------------------------------------------------
    def do_replace(self):
        stats = self.cleaner.create_masters_and_replace(
            preserve_transforms=self.pres_transforms_check.isChecked(),
            preserve_materials=self.pres_materials_check.isChecked(),
            keep_hierarchy=self.keep_hierarchy_check.isChecked(),
            hide_original=self.hide_original_check.isChecked(),
            backup=self.backup_check.isChecked()
        )

        if stats:
            msg = (
                "✓ Terminé !\n"
                "Masters : {masters_created}\n"
                "Instances : {instances_created}\n"
                "Meshes traités : {meshes_processed}"
            ).format(**stats)
            QMessageBox.information(self, "Instance Cleaner", msg)
            self.do_refresh_report()
            self.refresh_group_list()

    # ----------------------------------------------------------
    # SLOT : REPORT
    # ----------------------------------------------------------
    def do_refresh_report(self):
        r = self.cleaner.get_report()
        for key, lbl in self.report_labels.items():
            lbl.setText(str(r.get(key, "—")))

    # ----------------------------------------------------------
    # CLOSE
    # ----------------------------------------------------------
    def closeEvent(self, event):
        self.cleaner.clear_colors()
        super(InstanceCleanerUI, self).closeEvent(event)


# ============================================================
#  LAUNCH
# ============================================================
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
