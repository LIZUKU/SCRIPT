# -*- coding: utf-8 -*-
import maya.cmds as cmds
import maya.mel as mel
import maya.api.OpenMaya as om2
import __main__

if not hasattr(__main__, "_ULTRA_MC_REGISTRY"):
    __main__._ULTRA_MC_REGISTRY = {"callback_ids": []}

_REG = __main__._ULTRA_MC_REGISTRY

_STATE = {
    "active": False,
    "tool_ctx": None,
    "target_objs": [],
    "mask_set": "maskFaceSet",
    "old_edges_set": "oldEdgesSet",
    "restoring_selection": False,
    "pending_cleanup": False,
}


def _remove_all_callbacks():
    for cbid in (_REG.get("callback_ids") or []):
        try:
            om2.MMessage.removeCallback(cbid)
        except Exception:
            pass
    _REG["callback_ids"] = []


def _cleanup_temp_sets():
    for s in (_STATE["mask_set"], _STATE["old_edges_set"]):
        try:
            if cmds.objExists(s):
                cmds.delete(s)
        except Exception:
            pass


def _reset():
    _remove_all_callbacks()
    _cleanup_temp_sets()
    _STATE["active"] = False
    _STATE["tool_ctx"] = None
    _STATE["target_objs"] = []
    _STATE["restoring_selection"] = False
    _STATE["pending_cleanup"] = False


def _all_edges_of_targets():
    if not _STATE["target_objs"]:
        return []
    return cmds.ls(["{}.e[*]".format(obj) for obj in _STATE["target_objs"]], fl=True) or []


def _get_new_edges():
    """Retourne les edges créés par le multicut (présents maintenant mais pas avant)."""
    current_edges = _all_edges_of_targets()
    if not current_edges or not cmds.objExists(_STATE["old_edges_set"]):
        return []
    cmds.select(current_edges, r=True)
    cmds.select(_STATE["old_edges_set"], d=True)
    return cmds.ls(sl=True, fl=True) or []


def _get_edges_in_mask():
    """Retourne les edges qui tombent dans la zone mask (faces sélectionnées initialement)."""
    if not cmds.objExists(_STATE["mask_set"]):
        return []
    cmds.select(_STATE["mask_set"], r=True)
    mel.eval("ConvertSelectionToEdges;")
    return cmds.ls(sl=True, fl=True) or []


def _restore_mask_faces():
    if not _STATE["active"]:
        return
    if _STATE["restoring_selection"]:
        return
    if not cmds.objExists(_STATE["mask_set"]):
        return

    try:
        current_ctx = cmds.currentCtx()
    except Exception:
        return

    if current_ctx != _STATE["tool_ctx"]:
        return

    _STATE["restoring_selection"] = True
    cmds.select(_STATE["mask_set"], r=True)
    # On remet le flag à False APRÈS que le SelectionChanged de ce select soit passé
    cmds.evalDeferred(lambda: _STATE.update({"restoring_selection": False}))


def _on_selection_changed(*args):
    if not _STATE["active"] or _STATE["restoring_selection"] or _STATE["pending_cleanup"]:
        return

    try:
        current_ctx = cmds.currentCtx()
    except Exception:
        return

    if current_ctx != _STATE["tool_ctx"]:
        return

    cmds.evalDeferred(_restore_mask_faces, lowestPriority=True)


def _final_cleanup():
    if not _STATE["active"]:
        _STATE["pending_cleanup"] = False
        return

    # Désactive immédiatement + tue les callbacks avant toute opération
    _STATE["active"] = False
    _remove_all_callbacks()

    cmds.undoInfo(openChunk=True)
    try:
        if not cmds.objExists(_STATE["old_edges_set"]) or not cmds.objExists(_STATE["mask_set"]):
            cmds.select(cl=True)
            return

        # Edges créés par le multicut
        new_edges = _get_new_edges()

        if not new_edges:
            # Rien de créé → sélection vide, on nettoie et on sort
            cmds.select(cl=True)
            return

        # Edges dans la zone mask
        edges_in_mask = _get_edges_in_mask()

        # Supprime les edges créés HORS du mask
        to_delete = list(set(new_edges) - set(edges_in_mask))
        if to_delete:
            cmds.polyDelEdge(to_delete, cv=True)

        # Recalcule après suppression
        new_edges_final = _get_new_edges()
        edges_in_mask_final = _get_edges_in_mask()

        # Sélection finale = edges créés ET dans le mask
        final_selection = list(set(new_edges_final) & set(edges_in_mask_final))

        if final_selection:
            cmds.select(final_selection, r=True)
        else:
            cmds.select(cl=True)

    except Exception as e:
        print("ultra_multicut_mask | cleanup error: {}".format(e))
        cmds.select(cl=True)

    finally:
        # Suppression des sets garantie dans tous les cas
        _cleanup_temp_sets()
        _STATE["target_objs"] = []
        _STATE["pending_cleanup"] = False
        cmds.undoInfo(closeChunk=True)


def _on_tool_changed(*args):
    if not _STATE["active"] or _STATE["pending_cleanup"]:
        return

    try:
        current_ctx = cmds.currentCtx()
    except Exception:
        current_ctx = None

    if current_ctx == _STATE["tool_ctx"]:
        return

    # On passe par evalDeferred : on ne kill jamais un callback depuis lui-même
    _STATE["pending_cleanup"] = True
    cmds.evalDeferred(_final_cleanup)


def stop_ultra_multicut_mask():
    """Arrêt manuel de sécurité — appelle ça si quelque chose coince."""
    _reset()
    print("ultra_multicut_mask | stopped and cleaned up.")


def ultra_multicut_mask():
    _reset()

    initial_sel = cmds.ls(sl=True, fl=True) or []
    initial_faces = cmds.filterExpand(initial_sel, sm=34) or []

    if not initial_faces:
        mel.eval("dR_multiCutTool;")
        return

    target_objs = list(set(face.split(".")[0] for face in initial_faces))
    if not target_objs:
        mel.eval("dR_multiCutTool;")
        return

    cmds.sets(initial_faces, name=_STATE["mask_set"])
    all_edges_before = cmds.ls(["{}.e[*]".format(obj) for obj in target_objs], fl=True) or []
    cmds.sets(all_edges_before, name=_STATE["old_edges_set"])

    _STATE["active"] = True
    _STATE["target_objs"] = target_objs

    cmds.select(_STATE["mask_set"], r=True)
    mel.eval("dR_multiCutTool;")

    try:
        _STATE["tool_ctx"] = cmds.currentCtx()
    except Exception:
        _STATE["tool_ctx"] = None

    cb_tool = om2.MEventMessage.addEventCallback("ToolChanged", _on_tool_changed)
    cb_sel  = om2.MEventMessage.addEventCallback("SelectionChanged", _on_selection_changed)
    _REG["callback_ids"] = [cb_tool, cb_sel]


# run
ultra_multicut_mask()
