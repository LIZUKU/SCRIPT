# -*- coding: utf-8 -*-
"""Weighted normals helper for Maya.

Fixes common selection-list API crash:
TypeError: MGlobal_getActiveSelectionList expected at least 1 arguments, got 0

Also keeps user normals editable by unlocking/unfreezing normals before and after
running weighted normals.
"""

import maya.cmds as cmds

try:
    # Maya Python API 2.0 (preferred)
    import maya.api.OpenMaya as om2
except Exception:  # pragma: no cover - Maya-only fallback
    om2 = None

try:
    # Maya Python API 1.0 fallback
    import maya.OpenMaya as om1
except Exception:  # pragma: no cover - Maya-only fallback
    om1 = None


def _selection_from_api():
    """Return selected transform names using OpenMaya API safely."""
    if om2 is not None:
        sel = om2.MGlobal.getActiveSelectionList()
        names = []
        for i in range(sel.length()):
            try:
                dag = sel.getDagPath(i)
                names.append(dag.fullPathName())
            except Exception:
                continue
        return names

    if om1 is not None:
        # IMPORTANT: API 1.0 requires an output argument.
        sel = om1.MSelectionList()
        om1.MGlobal.getActiveSelectionList(sel)
        names = []
        for i in range(sel.length()):
            dag = om1.MDagPath()
            try:
                sel.getDagPath(i, dag)
                names.append(dag.fullPathName())
            except Exception:
                continue
        return names

    return []


def get_selected_meshes():
    """Get selected mesh transforms, robust against API/version differences."""
    api_names = _selection_from_api()
    if api_names:
        meshes = []
        for node in api_names:
            if not cmds.objExists(node):
                continue
            if cmds.nodeType(node) == "transform":
                shapes = cmds.listRelatives(node, shapes=True, fullPath=True) or []
                if any(cmds.nodeType(s) == "mesh" for s in shapes):
                    meshes.append(node)
            elif cmds.nodeType(node) == "mesh":
                parent = cmds.listRelatives(node, parent=True, fullPath=True) or []
                if parent:
                    meshes.append(parent[0])
        if meshes:
            return list(dict.fromkeys(meshes))

    # Reliable fallback if API list is empty.
    return cmds.ls(selection=True, long=True, type="transform") or []


def _unlock_normals(mesh):
    """Unlock (unfreeze) vertex normals on a mesh transform."""
    cmds.polyNormalPerVertex(mesh, unFreezeNormal=True)


def apply_normals(angle=180):
    """Apply weighted normals while keeping normals unlocked/editable."""
    meshes = get_selected_meshes()
    if not meshes:
        cmds.warning("WeightedNormals: no mesh selected.")
        return []

    processed = []
    for mesh in meshes:
        if not cmds.objExists(mesh):
            continue
        try:
            # Unfreeze first to avoid locked-normal inconsistencies.
            _unlock_normals(mesh)

            # Maya's weighted normal operation.
            cmds.polySoftEdge(mesh, angle=angle, constructionHistory=False)
            cmds.polySetToFaceNormal(mesh, setUserNormal=True)
            cmds.polyAverageNormal(mesh, distance=0.0, prenormalize=True, postnormalize=True)

            # Keep them editable after weighted pass (requested behavior).
            _unlock_normals(mesh)
            processed.append(mesh)
        except Exception as exc:
            cmds.warning("WeightedNormals: failed on '{}': {}".format(mesh, exc))

    return processed

