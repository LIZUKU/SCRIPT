# -*- coding: utf-8 -*-
import maya.cmds as cmds
import maya.mel as mel

_STATE = {
    "active": False,
    "tool_ctx": None,
    "target_objs": [],
    "job_tool_changed": None,
    "job_selection_changed": None,
    "mask_set": "maskFaceSet",
    "old_edges_set": "oldEdgesSet",
    "restoring_selection": False,
}


def _cleanup_temp_sets():
    for s in (_STATE["mask_set"], _STATE["old_edges_set"]):
        if cmds.objExists(s):
            cmds.delete(s)


def _kill_jobs():
    for jid in (_STATE["job_tool_changed"], _STATE["job_selection_changed"]):
        if jid and cmds.scriptJob(exists=jid):
            cmds.scriptJob(kill=jid, force=True)
    _STATE["job_tool_changed"] = None
    _STATE["job_selection_changed"] = None


def _reset():
    _kill_jobs()
    _cleanup_temp_sets()
    _STATE["active"] = False
    _STATE["tool_ctx"] = None
    _STATE["target_objs"] = []
    _STATE["restoring_selection"] = False


def _all_edges_of_targets():
    return cmds.ls(["{}.e[*]".format(obj) for obj in _STATE["target_objs"]], fl=True) or []


def _get_new_edges_current():
    current_edges = _all_edges_of_targets()
    if not current_edges:
        return []

    cmds.select(current_edges, r=True)
    cmds.select(_STATE["old_edges_set"], d=True)
    return cmds.ls(sl=True, fl=True) or []


def _get_edges_in_mask_current():
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

    _STATE["restoring_selection"] = True
    cmds.select(_STATE["mask_set"], r=True)
    _STATE["restoring_selection"] = False


def _on_selection_changed():
    if not _STATE["active"]:
        return

    try:
        current_ctx = cmds.currentCtx()
    except:
        current_ctx = None

    if current_ctx != _STATE["tool_ctx"]:
        return

    cmds.evalDeferred(_restore_mask_faces, lowestPriority=True)


def _final_cleanup():
    if not _STATE["active"]:
        return

    cmds.undoInfo(openChunk=True)
    try:
        if not cmds.objExists(_STATE["old_edges_set"]) or not cmds.objExists(_STATE["mask_set"]):
            return

        new_edges_before_delete = _get_new_edges_current()
        if not new_edges_before_delete:
            return

        edges_in_mask_before_delete = _get_edges_in_mask_current()

        new_edges_set = set(new_edges_before_delete)
        edges_in_mask_set = set(edges_in_mask_before_delete)

        to_delete = list(new_edges_set - edges_in_mask_set)

        if to_delete:
            cmds.polyDelEdge(to_delete, cv=True)

        new_edges_after_delete = _get_new_edges_current()
        edges_in_mask_after_delete = _get_edges_in_mask_current()

        final_keep = list(set(new_edges_after_delete) & set(edges_in_mask_after_delete))

        if final_keep:
            cmds.select(final_keep, r=True)
        else:
            cmds.select(_STATE["mask_set"], r=True)

    finally:
        _reset()
        cmds.undoInfo(closeChunk=True)


def _on_tool_changed():
    if not _STATE["active"]:
        return

    try:
        current_ctx = cmds.currentCtx()
    except:
        current_ctx = None

    if current_ctx == _STATE["tool_ctx"]:
        return

    _final_cleanup()


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

    _cleanup_temp_sets()

    cmds.sets(initial_faces, name=_STATE["mask_set"])

    all_edges_before = cmds.ls(
        ["{}.e[*]".format(obj) for obj in target_objs],
        fl=True
    ) or []

    cmds.sets(all_edges_before, name=_STATE["old_edges_set"])

    _STATE["active"] = True
    _STATE["target_objs"] = target_objs

    cmds.select(_STATE["mask_set"], r=True)

    mel.eval("dR_multiCutTool;")

    try:
        _STATE["tool_ctx"] = cmds.currentCtx()
    except:
        _STATE["tool_ctx"] = None

    _STATE["job_tool_changed"] = cmds.scriptJob(
        event=["ToolChanged", _on_tool_changed],
        protected=True
    )

    _STATE["job_selection_changed"] = cmds.scriptJob(
        event=["SelectionChanged", _on_selection_changed],
        protected=True
    )


# run
ultra_multicut_mask()
