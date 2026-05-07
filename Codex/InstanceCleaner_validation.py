# -*- coding: utf-8 -*-
"""Maya validation script for Codex.Instance Instance Cleaner.

Run inside Maya's Script Editor or mayapy after adding this repository to
PYTHONPATH::

    import Codex.InstanceCleaner_validation as v
    v.run_all()

The checks cover:
  * identical meshes moved/rotated/scaled;
  * different meshes with equal topology/count-like values rejected by geometry;
  * forced alignment failure preserves the original;
  * cancel/rollback restores parent/name/visibility/display layers;
  * instance-to-geometry conversion creates independent mesh shapes.
"""

from __future__ import print_function

import maya.cmds as cmds
import Codex.Instance as IC


def _assert(condition, message):
    if not condition:
        raise AssertionError(message)


def _cleanup():
    cmds.file(new=True, force=True)


def _cube(name, tx=0, ry=0, scale=1):
    node = cmds.polyCube(name=name, ch=False)[0]
    cmds.xform(node, ws=True, t=(tx, 0, 0), ro=(0, ry, 0), s=(scale, scale, scale))
    return cmds.ls(node, long=True)[0]


def validate_moved_rotated_scaled():
    _cleanup()
    a = _cube("same_A", tx=0, ry=0, scale=1)
    b = _cube("same_B", tx=5, ry=90, scale=2)
    cleaner = IC.InstanceCleaner()
    count = cleaner.scan(detect_method="exact", ignore_scale=True, compare_tolerance=0.001)
    _assert(count >= 1, "exact mode should group translated/rotated/scaled identical cubes when ignore_scale=True")
    return True


def validate_same_counts_area_volume_rejected():
    _cleanup()
    a = _cube("shape_A")
    b = _cube("shape_B", tx=5)
    # Move one vertex while keeping topology/counts identical.  This is enough
    # to prove geometry mode is not count-only.
    cmds.move(0.35, 0.0, 0.0, b + ".vtx[0]", relative=True, objectSpace=True)
    cleaner = IC.InstanceCleaner()
    count = cleaner.scan(detect_method="geometry", ignore_scale=True, compare_tolerance=0.001)
    _assert(count == 0, "geometry mode should reject meshes that only share counts/topology")
    return True


def validate_forced_alignment_failure_preserves_original():
    _cleanup()
    a = _cube("align_A")
    b = _cube("align_B", tx=4)
    cleaner = IC.InstanceCleaner()
    cleaner.scan(detect_method="geometry", ignore_scale=True, compare_tolerance=0.01)
    for info in cleaner.validated_groups.values():
        info["accepted"] = True
    old_verify = IC._verify_instance_matches_original
    try:
        IC._verify_instance_matches_original = lambda *args, **kwargs: (False, 99.0)
        stats = cleaner.create_masters_and_replace(keep_hidden_backups=True, delete_originals=False, use_pca_icp_alignment=False)
    finally:
        IC._verify_instance_matches_original = old_verify
    _assert(cmds.objExists(b), "original mesh should remain when alignment verification fails")
    _assert(stats.get("alignment_skipped", 0) > 0, "stats should report skipped alignment")
    return True


def validate_cancel_rollback_restores_state():
    _cleanup()
    grp = cmds.group(em=True, name="origParent")
    layer = cmds.createDisplayLayer(name="ORIG_LAYER", empty=True)
    a = _cube("rollback_A")
    b = _cube("rollback_B", tx=4)
    b = cmds.parent(b, grp, absolute=True)[0]
    cmds.setAttr(b + ".visibility", 0)
    cmds.editDisplayLayerMembers(layer, b, noRecurse=True)
    cleaner = IC.InstanceCleaner()
    cleaner.scan(detect_method="geometry", ignore_scale=True, compare_tolerance=0.01)
    for info in cleaner.validated_groups.values():
        info["accepted"] = True
    stats = cleaner.create_masters_and_replace(keep_hidden_backups=True, delete_originals=False, use_pca_icp_alignment=False)
    _assert(stats.get("backups_created", 0) > 0, "process should create backups")
    cancel = cleaner.cancel_last_process()
    restored = cmds.ls("rollback_B", long=True) or []
    _assert(cancel.get("restored", 0) > 0 and restored, "rollback should restore original name")
    parent = cmds.listRelatives(restored[0], parent=True, fullPath=False) or []
    _assert(parent and parent[0] == "origParent", "rollback should restore parent")
    _assert(cmds.getAttr(restored[0] + ".visibility") == 0, "rollback should restore visibility")
    _assert(layer in (cmds.listConnections(restored[0], type="displayLayer") or []), "rollback should restore display layer")
    return True


def validate_convert_instances_to_geometry_independent():
    _cleanup()
    a = _cube("conv_A")
    b = _cube("conv_B", tx=4)
    cleaner = IC.InstanceCleaner()
    cleaner.scan(detect_method="geometry", ignore_scale=True, compare_tolerance=0.01)
    for info in cleaner.validated_groups.values():
        info["accepted"] = True
    cleaner.create_masters_and_replace(keep_hidden_backups=True, delete_originals=False, use_pca_icp_alignment=False)
    result = cleaner.convert_instances_to_geometry()
    _assert(result.get("converted", 0) > 0, "conversion should create geometry")
    converted = [n for n in IC._iter_mesh_transforms(IC.CONVERTED_GROUP, include_ic=True)]
    _assert(converted, "converted group should contain mesh transforms")
    for node in converted:
        _assert(not IC._is_instanced_mesh_transform(node), "converted mesh should not share an instanced shape: " + node)
    return True


def run_all():
    tests = [
        validate_moved_rotated_scaled,
        validate_same_counts_area_volume_rejected,
        validate_forced_alignment_failure_preserves_original,
        validate_cancel_rollback_restores_state,
        validate_convert_instances_to_geometry_independent,
    ]
    results = []
    for test in tests:
        test()
        results.append(test.__name__)
        print("[IC validation] PASS", test.__name__)
    return results
