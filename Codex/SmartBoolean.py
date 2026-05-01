import math
import re
import maya.cmds as mc
import maya.mel as mel
import maya.OpenMaya as om
import maya.OpenMayaUI as omui
import maya.api.OpenMaya as oma
import __main__

try:
    import maya.plugin.polyBoolean.booltoolUtils as btUtils
except Exception:
    btUtils = None


CTX = "PlugBoolDragCtx"

SURFACE_OUTWARD_OFFSET = 0.1

DEFAULT_START_FLIPPED = True

BOOL_OP_UNION = 1
BOOL_OP_SUBTRACT = 2

CUTTER_GROUP = "_boolean_cutters"

CUTTER_DISPLAY_SUBTRACT = 2
CUTTER_DISPLAY_UNION = 0

AUTO_RESTART_DRAG_AFTER_BOOLEAN = True

VALIDATE_HOTKEY_KEY = "v"
VALIDATE_COMMAND = "PlugBoolValidateCommand"


hit_face = ""
picked_mesh_transform = []
visible_mesh_shapes = []

camera_position = [0, 0, 0]
camera_far_clip = 10000

original_parent_path = ""
current_mesh_name = ""

current_mode = "move"
mode_start_x = 0
mode_start_y = 0

initial_scale_x = 1.0
initial_scale_y = 1.0
initial_scale_z = 1.0
initial_rotate_y = 0.0

duplicate_done = False
orientation_flip_enabled = DEFAULT_START_FLIPPED
invert_combo_was_down = False

boolean_target_mesh = ""
boolean_target_shape = ""
boolean_target_face_id = -1

boolean_cutter_mesh = ""
boolean_result_mesh = ""
boolean_created = False
last_boolean_node = ""

tool_job = None
drag_session_active = False

reuse_selected_cutter_once = False
suppress_next_tool_changed = False

hotkey_installed = False
old_v_press_command = None
old_v_release_command = None


def ln(node):
    if not node or not mc.objExists(node):
        return ""
    r = mc.ls(node, long=True) or []
    return r[0] if r else ""


def sn(node):
    return node.split("|")[-1]


def is_comp(node):
    return any(x in node for x in [".vtx[", ".e[", ".f[", ".map["])


def is_mesh_xform(node):
    if not node or not mc.objExists(node) or is_comp(node):
        return False
    if mc.nodeType(node) == "mesh":
        return False
    shapes = mc.listRelatives(node, shapes=True, fullPath=True) or []
    return any(mc.nodeType(s) == "mesh" for s in shapes)


def shape_parent(shape):
    p = mc.listRelatives(shape, parent=True, fullPath=True) or []
    return p[0] if p else ""


def shape_under(shape, xform):
    shape = ln(shape)
    xform = ln(xform)
    if not shape or not xform:
        return False
    p = shape_parent(shape)
    return p == xform or p.startswith(xform + "|")


def mesh_shapes_under(xform):
    xform = ln(xform)
    if not xform:
        return []
    a = mc.listRelatives(xform, shapes=True, fullPath=True) or []
    b = mc.listRelatives(xform, allDescendents=True, shapes=True, fullPath=True) or []
    return [s for s in a + b if mc.objExists(s) and mc.nodeType(s) == "mesh"]


def first_mesh_shape(xform):
    shapes = mesh_shapes_under(xform)
    return shapes[0] if shapes else ""


def clear_temp():
    for n in ("instPicker", "instFlip", "instRot", "aimLoc", "pickerAim"):
        if mc.objExists(n):
            try:
                mc.delete(n)
            except Exception:
                pass


def cleanup_empty():
    for n in ("instPicker", "instFlip", "instRot", "aimLoc", "pickerAim"):
        if mc.objExists(n):
            try:
                mc.delete(n)
            except Exception:
                pass

    if mc.objExists(CUTTER_GROUP):
        children = mc.listRelatives(CUTTER_GROUP, children=True, fullPath=True) or []
        if not children:
            try:
                mc.delete(CUTTER_GROUP)
            except Exception:
                pass


def kill_stale_jobs():
    global tool_job

    if tool_job and mc.scriptJob(exists=tool_job):
        try:
            mc.scriptJob(kill=tool_job, force=True)
        except Exception:
            pass

    tool_job = None

    if mc.draggerContext(CTX, exists=True):
        try:
            mc.deleteUI(CTX)
        except Exception:
            pass

    clear_temp()
    cleanup_empty()


def reset_state(keep_cutter=False):
    global boolean_target_mesh, boolean_target_shape, boolean_target_face_id
    global boolean_cutter_mesh, boolean_result_mesh, boolean_created

    boolean_target_mesh = ""
    boolean_target_shape = ""
    boolean_target_face_id = -1
    boolean_result_mesh = ""
    boolean_created = False

    if not keep_cutter:
        boolean_cutter_mesh = ""


def has_flip_attr(mesh):
    mesh = ln(mesh) or ln(sn(mesh))
    return bool(mesh and mc.objExists(mesh + ".click2dFlipState"))


def get_flip(mesh):
    mesh = ln(mesh) or ln(sn(mesh))
    attr = mesh + ".click2dFlipState"

    if mc.objExists(attr):
        try:
            return bool(mc.getAttr(attr))
        except Exception:
            pass

    return DEFAULT_START_FLIPPED


def set_flip(mesh, state):
    mesh = ln(mesh) or ln(sn(mesh))
    if not mesh:
        return

    attr = mesh + ".click2dFlipState"

    if not mc.objExists(attr):
        try:
            mc.addAttr(mesh, longName="click2dFlipState", attributeType="bool", defaultValue=DEFAULT_START_FLIPPED)
        except Exception:
            return

    try:
        mc.setAttr(attr, bool(state))
    except Exception:
        pass


def set_cutter_bool_node(cutter, node):
    cutter = ln(cutter) or ln(sn(cutter))
    if not cutter or not node or not mc.objExists(node):
        return

    attr = cutter + ".plugBoolNode"

    if not mc.objExists(attr):
        try:
            mc.addAttr(cutter, longName="plugBoolNode", dataType="string")
        except Exception:
            return

    try:
        mc.setAttr(attr, node, type="string")
    except Exception:
        pass


def get_cutter_bool_node(cutter):
    cutter = ln(cutter) or ln(sn(cutter))
    if not cutter:
        return ""

    attr = cutter + ".plugBoolNode"

    if mc.objExists(attr):
        try:
            node = mc.getAttr(attr)
            if node and mc.objExists(node):
                return node
        except Exception:
            pass

    return ""


def current_bool_operation():
    if orientation_flip_enabled == DEFAULT_START_FLIPPED:
        return BOOL_OP_SUBTRACT
    return BOOL_OP_UNION


def is_current_subtract():
    return current_bool_operation() == BOOL_OP_SUBTRACT


def current_offset_sign():
    if orientation_flip_enabled == DEFAULT_START_FLIPPED:
        return 1.0
    return -1.0


def ensure_group(name):
    if mc.objExists(name):
        return name
    return mc.group(empty=True, name=name)


def set_display_visible(obj, display_type=0, force_override=False):
    obj = ln(obj) or ln(sn(obj))
    if not obj:
        return

    nodes = [obj] + (mc.listRelatives(obj, allDescendents=True, fullPath=True) or [])

    for n in nodes:
        if not mc.objExists(n):
            continue

        if mc.objExists(n + ".visibility"):
            try:
                mc.setAttr(n + ".visibility", 1)
            except Exception:
                pass

        if mc.objExists(n + ".template"):
            try:
                mc.setAttr(n + ".template", 0)
            except Exception:
                pass

        if mc.objExists(n + ".overrideEnabled"):
            try:
                mc.setAttr(n + ".overrideEnabled", 1 if force_override else 0)
            except Exception:
                pass

        if mc.objExists(n + ".overrideDisplayType"):
            try:
                mc.setAttr(n + ".overrideDisplayType", display_type)
            except Exception:
                pass

        if mc.nodeType(n) == "mesh":
            for attr, value in [
                ("intermediateObject", 0),
                ("hiddenInOutliner", 0),
            ]:
                p = n + "." + attr
                if mc.objExists(p):
                    try:
                        mc.setAttr(p, value)
                    except Exception:
                        pass

            try:
                mel.eval('displaySurface -xRay 0 "{0}";'.format(n))
            except Exception:
                pass


def apply_visibility_for_boolean_mode(cutter=None, target=None, result=None):
    cutter = ln(cutter or boolean_cutter_mesh) or ln(sn(cutter or boolean_cutter_mesh))
    target = ln(target or boolean_target_mesh)
    result = ln(result or boolean_result_mesh)

    if is_current_subtract():
        if cutter:
            set_display_visible(cutter, display_type=CUTTER_DISPLAY_SUBTRACT, force_override=True)
        if target:
            set_display_visible(target, display_type=0, force_override=False)
        if result:
            set_display_visible(result, display_type=0, force_override=False)
    else:
        if cutter:
            set_display_visible(cutter, display_type=CUTTER_DISPLAY_UNION, force_override=False)
        if target:
            set_display_visible(target, display_type=0, force_override=False)
        if result:
            set_display_visible(result, display_type=0, force_override=False)

    if last_boolean_node and mc.objExists(last_boolean_node):
        r = result_from_bool_node(last_boolean_node)
        if r:
            set_display_visible(r, display_type=0, force_override=False)


def group_cutter(cutter):
    cutter = ln(cutter)
    if not cutter:
        return ""

    name = sn(cutter)

    try:
        grp = ln(ensure_group(CUTTER_GROUP))
        parent = mc.listRelatives(cutter, parent=True, fullPath=True) or []

        if parent and parent[0] == grp:
            return cutter

        mc.parent(cutter, grp)

        new_path = "|" + CUTTER_GROUP + "|" + name
        return ln(new_path) or ln(name) or new_path

    except Exception:
        return ln(name) or cutter


def bool_nodes(result):
    result = ln(result)
    if not result:
        return []

    h = mc.listHistory(result, pruneDagObjects=True) or []
    return [n for n in h if mc.objExists(n) and mc.nodeType(n) == "polyBoolean"]


def tune_bool_node(node):
    if not node or not mc.objExists(node):
        return

    for attr, value in [
        ("interactiveUpdate", 1),
        ("maya2025", 1),
    ]:
        p = node + "." + attr
        if mc.objExists(p):
            try:
                mc.setAttr(p, value)
            except Exception:
                pass

    p = node + ".newInputOperation"
    if mc.objExists(p):
        try:
            mc.setAttr(p, current_bool_operation())
        except Exception:
            pass


def bool_nodes_from_cutter(cutter):
    cutter = ln(cutter) or ln(sn(cutter))
    if not cutter:
        return []

    nodes = []

    stored = get_cutter_bool_node(cutter)
    if stored and stored not in nodes:
        nodes.append(stored)

    for shape in mesh_shapes_under(cutter):
        cons = mc.listConnections(shape, source=True, destination=True) or []
        for n in cons:
            if mc.objExists(n) and mc.nodeType(n) == "polyBoolean" and n not in nodes:
                nodes.append(n)

    hist = mc.listHistory(cutter, pruneDagObjects=True) or []
    for n in hist:
        if mc.objExists(n) and mc.nodeType(n) == "polyBoolean" and n not in nodes:
            nodes.append(n)

    if last_boolean_node and mc.objExists(last_boolean_node) and last_boolean_node not in nodes:
        nodes.append(last_boolean_node)

    return nodes


def bool_input_count(node):
    p = node + ".operation"

    if not mc.objExists(p):
        return 2

    try:
        v = mc.getAttr(p)

        if isinstance(v, (list, tuple)):
            if v and isinstance(v[0], (list, tuple)):
                return max(2, len(v[0]))
            return max(2, len(v))

    except Exception:
        pass

    return 2


def input_index_from_plug(plug):
    if "[" not in plug or "]" not in plug:
        return None

    try:
        return int(plug.split("[")[-1].split("]")[0])
    except Exception:
        return None


def cutter_input_indices(node, cutter):
    cutter = ln(cutter) or ln(sn(cutter))

    if not node or not mc.objExists(node) or not cutter:
        return []

    indices = []

    for shape in mesh_shapes_under(cutter):
        cons = mc.listConnections(
            shape,
            source=False,
            destination=True,
            plugs=True,
            connections=True,
        ) or []

        for plug in cons:
            if not isinstance(plug, str):
                continue

            if not plug.startswith(node + "."):
                continue

            idx = input_index_from_plug(plug)

            if idx is not None and idx not in indices:
                indices.append(idx)

    return indices


def set_bool_node_operation_for_cutter(node, cutter, op):
    cutter = ln(cutter) or ln(sn(cutter))

    if not node or not mc.objExists(node) or not cutter:
        return False

    p = node + ".operation"

    if not mc.objExists(p):
        return False

    count = bool_input_count(node)
    vals = [0] + [BOOL_OP_SUBTRACT for _ in range(max(1, count - 1))]

    try:
        current = mc.getAttr(p)

        if isinstance(current, (list, tuple)):
            if current and isinstance(current[0], (list, tuple)):
                vals = list(current[0])
            else:
                vals = list(current)

    except Exception:
        pass

    if len(vals) < count:
        vals += [BOOL_OP_SUBTRACT] * (count - len(vals))

    if vals:
        vals[0] = 0

    ids = cutter_input_indices(node, cutter)

    if not ids:
        ids = [len(vals) - 1]

    for idx in ids:
        if idx <= 0:
            continue

        while idx >= len(vals):
            vals.append(BOOL_OP_SUBTRACT)

        vals[idx] = op

    try:
        mc.setAttr(p, len(vals), *vals, type="Int32Array")
        mc.dgdirty(node)
        mc.refresh(force=True)
        return True

    except Exception:
        return False


def result_from_bool_node(node):
    if not node or not mc.objExists(node):
        return ""

    con = mc.listConnections(node, source=False, destination=True, shapes=True) or []

    for c in con:
        if not mc.objExists(c):
            continue

        if mc.nodeType(c) == "mesh":
            p = mc.listRelatives(c, parent=True, fullPath=True) or []
            if p and "_boolCutter" not in p[0]:
                return p[0]

        if mc.nodeType(c) == "transform" and "_boolCutter" not in c:
            return ln(c)

    return ""


def update_current_cutter_boolean_operation():
    global boolean_cutter_mesh, last_boolean_node

    cutter = ln(boolean_cutter_mesh) or ln(current_mesh_name) or ln(sn(boolean_cutter_mesh))

    if not cutter:
        return

    nodes = bool_nodes_from_cutter(cutter)

    if not nodes and last_boolean_node and mc.objExists(last_boolean_node):
        nodes = [last_boolean_node]

    op = current_bool_operation()

    for n in nodes:
        tune_bool_node(n)

        if set_bool_node_operation_for_cutter(n, cutter, op):
            last_boolean_node = n
            set_cutter_bool_node(cutter, n)

    apply_visibility_for_boolean_mode(cutter=cutter)

    if cutter and mc.objExists(cutter):
        mc.select(cutter, replace=True)


def duplicate_selected_plug(sel):
    global boolean_cutter_mesh, reuse_selected_cutter_once, last_boolean_node

    if sel and (
        reuse_selected_cutter_once
        or (
            drag_session_active
            and boolean_cutter_mesh
            and ln(sel[0]) == ln(boolean_cutter_mesh)
        )
    ):
        reuse_selected_cutter_once = False
        boolean_cutter_mesh = ln(sel[0])

        stored = get_cutter_bool_node(boolean_cutter_mesh)
        if stored:
            last_boolean_node = stored

        return [boolean_cutter_mesh]

    reuse_selected_cutter_once = False
    boolean_cutter_mesh = ""

    if not sel:
        mc.warning("Select one plug mesh.")
        return []

    src = sel[0]

    if is_comp(src):
        mc.warning("Select the plug transform, not a component.")
        return []

    src = ln(src)

    if not src or not is_mesh_xform(src):
        mc.warning("Selected object is not a mesh transform.")
        return []

    dup = mc.duplicate(src, rr=True, name=sn(src) + "_boolCutter#") or []

    if not dup:
        return []

    boolean_cutter_mesh = ln(dup[0])

    set_flip(boolean_cutter_mesh, DEFAULT_START_FLIPPED)

    mc.select(boolean_cutter_mesh, replace=True)

    return [boolean_cutter_mesh]


def remember_target(shape, face_id):
    global boolean_target_mesh, boolean_target_shape, boolean_target_face_id

    if not shape or not mc.objExists(shape):
        return

    if boolean_cutter_mesh and shape_under(shape, boolean_cutter_mesh):
        return

    parent = shape_parent(shape)

    if parent:
        boolean_target_shape = shape
        boolean_target_mesh = parent
        boolean_target_face_id = int(face_id)


def create_boolean(restart=True):
    global boolean_result_mesh, boolean_created, last_boolean_node

    cutter = ln(boolean_cutter_mesh)

    if not cutter:
        mc.warning("No boolean cutter found.")
        return None

    existing_nodes = bool_nodes_from_cutter(cutter)

    if existing_nodes:
        for n in existing_nodes:
            tune_bool_node(n)
            set_bool_node_operation_for_cutter(n, cutter, current_bool_operation())

        last_boolean_node = existing_nodes[0]
        set_cutter_bool_node(cutter, existing_nodes[0])

        boolean_created = True

        apply_visibility_for_boolean_mode(cutter=cutter)

        if restart and AUTO_RESTART_DRAG_AFTER_BOOLEAN:
            cutter = group_cutter(cutter) or ln(sn(cutter)) or cutter

        if cutter and mc.objExists(cutter):
            mc.select(cutter, replace=True)

            if restart and AUTO_RESTART_DRAG_AFTER_BOOLEAN:
                restart_drag_on_cutter(cutter)

        return cutter

    if boolean_created and restart:
        return boolean_result_mesh

    target = ln(boolean_target_mesh)

    if not target:
        mc.warning("No boolean target detected.")
        return None

    if target == cutter:
        mc.warning("Target and cutter are the same object.")
        return None

    try:
        if not mc.pluginInfo("polyBoolean", query=True, loaded=True):
            mc.loadPlugin("polyBoolean")
    except Exception as e:
        mc.warning("Could not load polyBoolean plugin: {0}".format(e))
        return None

    if btUtils is None:
        mc.warning("Could not import booltoolUtils.")
        return None

    op = current_bool_operation()

    try:
        mc.select(target, cutter, replace=True)
        btUtils.createBoolTool(op)
    except Exception as e:
        mc.warning("Modern Bool Tool failed: {0}".format(e))
        return None

    sel = mc.ls(selection=True, long=True) or []

    if not sel:
        mc.warning("Boolean failed.")
        return None

    result = sel[0]

    if mc.objExists(result) and mc.nodeType(result) != "transform":
        p = mc.listRelatives(result, parent=True, fullPath=True) or []
        if p:
            result = p[0]

    boolean_result_mesh = ln(result) or result
    boolean_created = True

    nodes = bool_nodes(boolean_result_mesh)

    if nodes:
        last_boolean_node = nodes[0]
        set_cutter_bool_node(cutter, nodes[0])

    for n in nodes:
        tune_bool_node(n)

    apply_visibility_for_boolean_mode(cutter=cutter, target=target, result=boolean_result_mesh)

    if restart and AUTO_RESTART_DRAG_AFTER_BOOLEAN:
        cutter = group_cutter(cutter) or ln(sn(cutter)) or cutter
    else:
        cutter = group_cutter(cutter) or cutter

    apply_visibility_for_boolean_mode(cutter=cutter, target=target, result=boolean_result_mesh)

    if cutter and mc.objExists(cutter):
        mc.select(cutter, replace=True)
    elif boolean_result_mesh and mc.objExists(boolean_result_mesh):
        mc.select(boolean_result_mesh, replace=True)

    if restart and AUTO_RESTART_DRAG_AFTER_BOOLEAN and cutter and mc.objExists(cutter):
        restart_drag_on_cutter(cutter)

    return boolean_result_mesh


def bake_boolean_result():
    global drag_session_active
    global boolean_cutter_mesh, boolean_result_mesh, boolean_target_mesh

    cutter = ln(boolean_cutter_mesh) or ln(current_mesh_name)
    nodes = bool_nodes_from_cutter(cutter)

    result = ""

    if nodes:
        result = result_from_bool_node(nodes[0])

    if not result:
        result = ln(boolean_result_mesh)

    if not result:
        result = ln(boolean_target_mesh)

    if not result:
        mc.warning("No boolean result found to bake.")
        return

    try:
        mc.select(result, replace=True)
        mel.eval("DeleteHistory;")
        mel.eval("FreezeTransformations;")
        mel.eval("CenterPivot;")

        if mc.objExists(CUTTER_GROUP):
            try:
                mc.delete(CUTTER_GROUP)
            except Exception:
                pass

        clear_temp()
        cleanup_empty()

        mc.select(result, replace=True)

    except Exception as e:
        mc.warning("Bake boolean failed: {0}".format(e))

    drag_session_active = False


def plug_bool_validate_hotkey():
    global drag_session_active
    global suppress_next_tool_changed
    global tool_job

    suppress_next_tool_changed = True

    if tool_job and mc.scriptJob(exists=tool_job):
        try:
            mc.scriptJob(kill=tool_job, force=True)
        except Exception:
            pass

    tool_job = None

    clear_temp()

    create_boolean(restart=False)
    bake_boolean_result()

    drag_session_active = False

    restore_validate_hotkey()

    try:
        mel.eval("setToolTo $gMove;")
    except Exception:
        pass


__main__.plug_bool_validate_hotkey = plug_bool_validate_hotkey


def install_validate_hotkey():
    global old_v_press_command, old_v_release_command, hotkey_installed

    if not hotkey_installed:
        try:
            old_v_press_command = mc.hotkey(
                keyShortcut=VALIDATE_HOTKEY_KEY,
                query=True,
                name=True,
            )
        except Exception:
            old_v_press_command = None

        try:
            old_v_release_command = mc.hotkey(
                keyShortcut=VALIDATE_HOTKEY_KEY,
                query=True,
                releaseName=True,
            )
        except Exception:
            old_v_release_command = None

    try:
        if not mc.runTimeCommand(VALIDATE_COMMAND, exists=True):
            mc.runTimeCommand(
                VALIDATE_COMMAND,
                annotation="Validate Plug Boolean",
                category="User",
                commandLanguage="python",
                command='import __main__; __main__.plug_bool_validate_hotkey()',
            )
    except Exception:
        pass

    try:
        mc.nameCommand(
            VALIDATE_COMMAND + "_name",
            annotation="Validate Plug Boolean",
            command=VALIDATE_COMMAND,
        )
    except Exception:
        pass

    try:
        mc.hotkey(
            keyShortcut=VALIDATE_HOTKEY_KEY,
            name=VALIDATE_COMMAND + "_name",
        )
        hotkey_installed = True
    except Exception:
        pass


def restore_validate_hotkey():
    global hotkey_installed

    try:
        mc.hotkey(
            keyShortcut=VALIDATE_HOTKEY_KEY,
            name=old_v_press_command,
            releaseName=old_v_release_command,
        )
    except Exception:
        pass

    hotkey_installed = False


def start_drag():
    global hit_face, tool_job, drag_session_active, suppress_next_tool_changed

    clear_temp()
    reset_state(False)

    drag_session_active = False
    suppress_next_tool_changed = False
    hit_face = ""

    if mc.draggerContext(CTX, exists=True):
        try:
            mc.deleteUI(CTX)
        except Exception:
            pass

    mc.draggerContext(
        CTX,
        pressCommand=on_press,
        dragCommand=on_move,
        releaseCommand=on_release,
        name=CTX,
        cursor="crossHair",
        undoMode="step",
    )

    mc.setToolTo(CTX)

    install_validate_hotkey()

    if tool_job and mc.scriptJob(exists=tool_job):
        try:
            mc.scriptJob(kill=tool_job, force=True)
        except Exception:
            pass

    tool_job = mc.scriptJob(event=["ToolChanged", on_tool_changed], protected=True)

    apply_visibility_for_boolean_mode()


def on_press():
    global picked_mesh_transform, visible_mesh_shapes, camera_position, camera_far_clip
    global original_parent_path, current_mesh_name, current_mode, mode_start_x, mode_start_y
    global initial_scale_x, initial_scale_y, initial_scale_z, initial_rotate_y
    global hit_face, duplicate_done, orientation_flip_enabled, invert_combo_was_down
    global drag_session_active

    hit_face = ""
    current_mode = "move"
    duplicate_done = False
    invert_combo_was_down = False

    vx, vy, _ = mc.draggerContext(CTX, query=True, anchorPoint=True)
    mode_start_x = vx
    mode_start_y = vy

    sel = duplicate_selected_plug(mc.ls(selection=True, flatten=True, long=True))

    if not sel:
        return

    if has_flip_attr(sel[0]):
        orientation_flip_enabled = get_flip(sel[0])
    else:
        orientation_flip_enabled = DEFAULT_START_FLIPPED
        set_flip(sel[0], orientation_flip_enabled)

    if not drag_session_active:
        reset_state(True)
    else:
        reset_state(True)

    view = omui.M3dView.active3dView()
    cam_path = om.MDagPath()
    view.getCamera(cam_path)

    cam_shape = cam_path.fullPathName()
    cam = mc.listRelatives(cam_shape, type="transform", parent=True)[0]

    camera_far_clip = mc.getAttr(cam + ".farClipPlane")
    camera_position = mc.xform(cam, query=True, worldSpace=True, rotatePivot=True)

    visible_mesh_shapes = get_visible_mesh_shapes()

    picked_mesh_transform = [ln(sel[0])]

    if not picked_mesh_transform[0] or not mc.objExists(picked_mesh_transform[0]):
        return

    children = mc.listRelatives(picked_mesh_transform[0], fullPath=True, allDescendents=True) or []

    original_parent_path = "|".join(picked_mesh_transform[0].split("|")[0:-1])
    current_mesh_name = picked_mesh_transform[0].split("|")[-1]

    drag_session_active = True

    saved_rot = mc.getAttr(current_mesh_name + ".rotate")[0]
    saved_scl = mc.getAttr(current_mesh_name + ".scale")[0]

    mesh_shape = first_mesh_shape(picked_mesh_transform[0])

    if not mesh_shape:
        mc.warning("Dragged plug has no mesh shape.")
        return

    sl = oma.MSelectionList()
    sl.add(mesh_shape)

    fn = oma.MFnMesh(sl.getDagPath(0))
    pts = fn.getPoints(oma.MSpace.kObject)

    min_y = min(p.y for p in pts)
    bottoms = [p for p in pts if abs(p.y - min_y) < 0.001]

    px = sum(p.x for p in bottoms) / len(bottoms) if bottoms else 0.0
    pz = sum(p.z for p in bottoms) / len(bottoms) if bottoms else 0.0

    wm = oma.MMatrix(mc.xform(current_mesh_name, query=True, worldSpace=True, matrix=True))
    pw = oma.MPoint(px, min_y, pz) * wm

    mc.move(
        pw.x,
        pw.y,
        pw.z,
        current_mesh_name + ".scalePivot",
        current_mesh_name + ".rotatePivot",
        worldSpace=True,
        absolute=True,
    )

    mc.group(empty=True, name="instPicker")

    mc.duplicate("instPicker")
    mc.rename("instFlip")
    mc.parent("instFlip", "instPicker")

    mc.duplicate("instFlip")
    mc.rename("instRot")
    mc.parent("instRot", "instFlip")

    mc.select("instPicker", picked_mesh_transform[0])
    mc.matchTransform(position=True, rotation=True)

    mc.parent(picked_mesh_transform[0], "instRot")

    mc.setAttr("instPicker.rotate", saved_rot[0], saved_rot[1], saved_rot[2])
    mc.setAttr("instFlip.rotate", 0, 0, 0)
    mc.setAttr("instRot.rotate", 0, 0, 0)
    mc.setAttr("instRot.scale", 1, 1, 1)

    mesh_path = "instPicker|instFlip|instRot|" + current_mesh_name

    mc.setAttr(mesh_path + ".scaleX", saved_scl[0])
    mc.setAttr(mesh_path + ".scaleY", saved_scl[1])
    mc.setAttr(mesh_path + ".scaleZ", saved_scl[2])

    try:
        mc.delete(constructionHistory=True)
    except Exception:
        pass

    for n in children:
        if n in visible_mesh_shapes:
            visible_mesh_shapes.remove(n)

    for s in mesh_shapes_under(picked_mesh_transform[0]):
        if s in visible_mesh_shapes:
            visible_mesh_shapes.remove(s)

    initial_scale_x = mc.getAttr(mesh_path + ".scaleX")
    initial_scale_y = mc.getAttr(mesh_path + ".scaleY")
    initial_scale_z = mc.getAttr(mesh_path + ".scaleZ")

    initial_rotate_y = mc.getAttr("instRot.rotateY")

    apply_flip()
    apply_visibility_for_boolean_mode(cutter=picked_mesh_transform[0])


def on_release():
    global current_mode, duplicate_done, invert_combo_was_down, boolean_cutter_mesh

    current_mode = "move"
    duplicate_done = False

    if mc.objExists("instPicker"):
        mesh_path = "instPicker|instFlip|instRot|" + current_mesh_name
        full_mesh_path = "|instPicker|instFlip|instRot|" + current_mesh_name

        if original_parent_path:
            if mc.objExists(full_mesh_path):
                mc.parent(full_mesh_path, original_parent_path)
        elif mc.objExists(mesh_path):
            mc.parent(mesh_path, world=True)

    final_path = current_mesh_name if not original_parent_path else original_parent_path + "|" + current_mesh_name
    final_path = ln(final_path) or final_path

    if mc.objExists(final_path):
        set_flip(final_path, orientation_flip_enabled)
        boolean_cutter_mesh = final_path
        apply_visibility_for_boolean_mode(cutter=final_path)

    clear_temp()
    invert_combo_was_down = False

    if mc.objExists(final_path):
        mc.select(final_path, replace=True)


def on_tool_changed():
    global tool_job, drag_session_active, suppress_next_tool_changed

    if suppress_next_tool_changed:
        suppress_next_tool_changed = False
        return

    ctx = mc.currentCtx()

    if ctx == CTX:
        return

    if tool_job and mc.scriptJob(exists=tool_job):
        try:
            mc.scriptJob(kill=tool_job, force=True)
        except Exception:
            pass

    tool_job = None

    if drag_session_active:
        clear_temp()
        create_boolean(restart=True)

    drag_session_active = False


def _deferred_restart_drag():
    global suppress_next_tool_changed

    suppress_next_tool_changed = False
    start_drag()
    install_validate_hotkey()


def restart_drag_on_cutter(cutter):
    global reuse_selected_cutter_once, suppress_next_tool_changed

    cutter = ln(cutter) or ln(sn(cutter))

    if not cutter:
        return

    reuse_selected_cutter_once = True
    suppress_next_tool_changed = True

    apply_visibility_for_boolean_mode(cutter=cutter)
    mc.select(cutter, replace=True)

    mc.evalDeferred(_deferred_restart_drag, lowestPriority=True)


def mode_from_modifiers():
    m = mc.getModifiers()

    shift = bool(m & 1)
    ctrl = bool(m & 4)
    alt = bool(m & 8)

    if alt:
        return "aim"

    if ctrl and shift:
        return "move"

    if ctrl:
        return "scale"

    if shift:
        return "rotate"

    return "move"


def update_flip_toggle():
    global orientation_flip_enabled, invert_combo_was_down, initial_rotate_y

    m = mc.getModifiers()

    down = bool(m & 1) and bool(m & 4)

    if down and not invert_combo_was_down:
        orientation_flip_enabled = not orientation_flip_enabled

        apply_flip()

        if boolean_cutter_mesh and mc.objExists(boolean_cutter_mesh):
            set_flip(boolean_cutter_mesh, orientation_flip_enabled)

        update_current_cutter_boolean_operation()

        try:
            vx, vy, _ = mc.draggerContext(CTX, query=True, dragPoint=True)
            drag_move(vx, vy)
        except Exception:
            pass

        if mc.objExists("instRot"):
            initial_rotate_y = mc.getAttr("instRot.rotateY")

    invert_combo_was_down = down


def apply_flip():
    if mc.objExists("instFlip"):
        mc.setAttr("instFlip.rotateX", 180 if orientation_flip_enabled else 0)


def switch_mode(new_mode, vx, vy):
    global current_mode, mode_start_x, mode_start_y
    global initial_scale_x, initial_scale_y, initial_scale_z, initial_rotate_y
    global duplicate_done

    current_mode = new_mode

    mode_start_x = vx
    mode_start_y = vy

    mesh_path = "instPicker|instFlip|instRot|" + current_mesh_name

    if mc.objExists(mesh_path):
        initial_scale_x = mc.getAttr(mesh_path + ".scaleX")
        initial_scale_y = mc.getAttr(mesh_path + ".scaleY")
        initial_scale_z = mc.getAttr(mesh_path + ".scaleZ")

    if mc.objExists("instRot"):
        initial_rotate_y = mc.getAttr("instRot.rotateY")

    duplicate_done = False


def on_move():
    global current_mode

    if not picked_mesh_transform:
        return

    if not mc.objExists("instPicker") or not mc.objExists("instRot"):
        return

    if mc.draggerContext(CTX, query=True, button=True) == 2:
        duplicate_continue()
        return

    vx, vy, _ = mc.draggerContext(CTX, query=True, dragPoint=True)

    update_flip_toggle()

    new_mode = mode_from_modifiers()

    if new_mode != current_mode:
        switch_mode(new_mode, vx, vy)
        mc.refresh(currentView=True, force=True)
        return

    if current_mode != "aim" and mc.objExists("pickerAim"):
        mc.delete("pickerAim")

    if current_mode == "move":
        drag_move(vx, vy)
    elif current_mode == "rotate":
        drag_rotate(vx)
    elif current_mode == "scale":
        drag_scale(vx)
    elif current_mode == "aim":
        drag_aim(vx, vy)

    mc.refresh(currentView=True, force=True)


def drag_move(vx, vy):
    global hit_face

    shape, hit, face_id, normal = raycast(vx, vy)

    if not shape:
        return

    remember_target(shape, face_id)

    face = shape + ".f[" + str(face_id) + "]"

    sign = current_offset_sign()

    pos = [
        hit[0] + normal[0] * SURFACE_OUTWARD_OFFSET * sign,
        hit[1] + normal[1] * SURFACE_OUTWARD_OFFSET * sign,
        hit[2] + normal[2] * SURFACE_OUTWARD_OFFSET * sign,
    ]

    mc.setAttr("instPicker.translate", pos[0], pos[1], pos[2])

    if hit_face != face:
        rx, ry, rz = face_rotation(face)
        mc.setAttr("instPicker.rotate", rx, ry, rz)
        hit_face = face

    apply_flip()


def drag_rotate(vx):
    dx = vx - mode_start_x
    step = int(dx / 4.0) * 15
    mc.setAttr("instRot.rotateY", initial_rotate_y + step)


def drag_scale(vx):
    dx = vx - mode_start_x
    factor = max(0.01, 1.0 + dx * 0.01)

    mesh_path = "instPicker|instFlip|instRot|" + current_mesh_name

    if not mc.objExists(mesh_path):
        return

    mc.setAttr(mesh_path + ".scaleX", max(0.01, initial_scale_x * factor))
    mc.setAttr(mesh_path + ".scaleY", max(0.01, initial_scale_y * factor))
    mc.setAttr(mesh_path + ".scaleZ", max(0.01, initial_scale_z * factor))


def duplicate_continue():
    global current_mesh_name, initial_scale_x, initial_scale_y, initial_scale_z
    global initial_rotate_y, hit_face, duplicate_done, boolean_cutter_mesh

    if not picked_mesh_transform:
        return

    if not mc.objExists("instPicker") or not mc.objExists("instRot"):
        return

    if duplicate_done:
        vx, vy, _ = mc.draggerContext(CTX, query=True, dragPoint=True)
        drag_move(vx, vy)
        mc.refresh(currentView=True, force=True)
        return

    old_path = "|instPicker|instFlip|instRot|" + current_mesh_name

    if not mc.objExists(old_path):
        return

    dup = mc.duplicate(old_path, rr=True)

    if not dup:
        return

    dup_name = dup[0]

    set_flip(old_path, orientation_flip_enabled)

    if original_parent_path:
        mc.parent(old_path, original_parent_path)
    else:
        mc.parent(old_path, world=True)

    old_cutter = ln(current_mesh_name)

    if old_cutter:
        boolean_cutter_mesh = old_cutter
        create_boolean(restart=False)
        reset_state(True)

    current_mesh_name = dup_name

    boolean_cutter_mesh = ln("|instPicker|instFlip|instRot|" + current_mesh_name) or current_mesh_name

    set_flip(boolean_cutter_mesh, orientation_flip_enabled)

    duplicate_done = True

    mesh_path = "instPicker|instFlip|instRot|" + current_mesh_name

    if mc.objExists(mesh_path):
        initial_scale_x = mc.getAttr(mesh_path + ".scaleX")
        initial_scale_y = mc.getAttr(mesh_path + ".scaleY")
        initial_scale_z = mc.getAttr(mesh_path + ".scaleZ")

        set_flip(mesh_path, orientation_flip_enabled)

    if mc.objExists("instRot"):
        initial_rotate_y = mc.getAttr("instRot.rotateY")

    hit_face = ""

    vx, vy, _ = mc.draggerContext(CTX, query=True, dragPoint=True)

    drag_move(vx, vy)

    mc.refresh(currentView=True, force=True)


def drag_aim(vx, vy):
    shape, hit, face_id, normal = raycast(vx, vy)

    if not shape:
        return

    remember_target(shape, face_id)

    if not mc.objExists("aimLoc"):
        mc.spaceLocator(position=[0, 0, 0], name="aimLoc")

    if not mc.objExists("pickerAim"):
        mc.aimConstraint(
            "aimLoc",
            "instPicker",
            offset=[0, 0, 0],
            weight=1,
            aimVector=[0, 1, 0],
            upVector=[0, 1, 0],
            worldUpType="vector",
            worldUpVector=[0, 1, 0],
            name="pickerAim",
        )

    sign = current_offset_sign()

    pos = [
        hit[0] + normal[0] * SURFACE_OUTWARD_OFFSET * sign,
        hit[1] + normal[1] * SURFACE_OUTWARD_OFFSET * sign,
        hit[2] + normal[2] * SURFACE_OUTWARD_OFFSET * sign,
    ]

    mc.setAttr("aimLoc.translate", pos[0], pos[1], pos[2])


def raycast(vx, vy):
    wp = om.MPoint()
    wd = om.MVector()

    omui.M3dView().active3dView().viewToWorld(int(vx), int(vy), wp, wd)

    src = om.MFloatPoint(wp.x, wp.y, wp.z)

    best_shape = ""
    best_dist = camera_far_clip
    best_hit = [0.0, 0.0, 0.0]
    best_face = 0
    best_normal = [0.0, 1.0, 0.0]

    for shape in visible_mesh_shapes:
        if not shape or not mc.objExists(shape):
            continue

        if boolean_cutter_mesh and shape_under(shape, boolean_cutter_mesh):
            continue

        sl = om.MSelectionList()
        sl.add(shape)

        dag = om.MDagPath()
        sl.getDagPath(0, dag)

        fn = om.MFnMesh(dag)

        hp = om.MFloatPoint()

        fu = om.MScriptUtil()
        fu.createFromInt(0)
        fp = fu.asIntPtr()

        ok = fn.closestIntersection(
            src,
            om.MFloatVector(wd),
            None,
            None,
            False,
            om.MSpace.kWorld,
            camera_far_clip,
            False,
            None,
            hp,
            None,
            fp,
            None,
            None,
            None,
        )

        if ok:
            dist = math.sqrt(
                (float(camera_position[0]) - hp.x) ** 2
                + (float(camera_position[1]) - hp.y) ** 2
                + (float(camera_position[2]) - hp.z) ** 2
            )

            if dist < best_dist:
                best_dist = dist
                best_shape = shape
                best_hit = [hp.x, hp.y, hp.z]
                best_face = fu.getInt(fp)
                best_normal = face_world_normal(shape, best_face)

    return best_shape, best_hit, best_face, best_normal


def face_world_normal(shape, face_id):
    try:
        sl = oma.MSelectionList()
        sl.add(shape)

        fn = oma.MFnMesh(sl.getDagPath(0))
        n = fn.getPolygonNormal(int(face_id), oma.MSpace.kWorld)
        n.normalize()

        return [n.x, n.y, n.z]

    except Exception:
        return [0.0, 1.0, 0.0]


def get_visible_mesh_shapes():
    view = omui.M3dView.active3dView()

    old = om.MSelectionList()
    om.MGlobal.getActiveSelectionList(old)

    try:
        om.MGlobal.selectFromScreen(
            0,
            0,
            view.portWidth(),
            view.portHeight(),
            om.MGlobal.kReplaceList,
        )

        sel = om.MSelectionList()
        om.MGlobal.getActiveSelectionList(sel)

    except Exception:
        sel = om.MSelectionList()

    finally:
        try:
            om.MGlobal.setActiveSelectionList(old, om.MGlobal.kReplaceList)
        except Exception:
            pass

    objs = []
    sel.getSelectionStrings(objs)

    visible = mc.listRelatives(objs, shapes=True, fullPath=True) or []
    all_meshes = mc.ls(type="mesh", long=True) or []

    if all_meshes and visible:
        return list(set(all_meshes) & set(visible))

    return []


def face_rotation(face):
    shape = mc.listRelatives(face, fullPath=True, parent=True)

    if not shape:
        return 0.0, 0.0, 0.0

    xform = mc.listRelatives(shape[0], fullPath=True, parent=True)

    if not xform:
        return 0.0, 0.0, 0.0

    wm = oma.MMatrix(mc.xform(xform, query=True, worldSpace=True, matrix=True))

    txt = mc.polyInfo(face, faceNormals=True)

    if not txt:
        return 0.0, 0.0, 0.0

    vals = [float(v) for v in re.findall(r"-?\d*\.\d*", txt[0])]

    if len(vals) < 3:
        return 0.0, 0.0, 0.0

    n = oma.MVector(vals[:3]) * wm
    n.normalize()

    q = oma.MQuaternion(oma.MVector(0, 1, 0), n)
    e = q.asEulerRotation()

    return math.degrees(e.x), math.degrees(e.y), math.degrees(e.z)


def smart_boolean_plug_drag():
    sel = mc.ls(selection=True, flatten=True, long=True)

    if not sel:
        mc.warning("Select one plug mesh first.")
        return

    first = sel[0]

    if ".e[" in first:
        mel.eval("SelectEdgeLoopSp;")
        return

    if is_comp(first):
        mc.warning("Select the plug transform, not a component.")
        return

    if is_mesh_xform(first):
        start_drag()
        return

    mc.warning("Selected object is not a mesh transform.")


kill_stale_jobs()
smart_boolean_plug_drag()
