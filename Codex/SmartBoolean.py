import math
import re
import maya.cmds as mc
import maya.mel as mel
import maya.OpenMaya as om
import maya.OpenMayaUI as omui
import maya.api.OpenMaya as oma

try:
    import maya.plugin.polyBoolean.booltoolUtils as btUtils
except Exception:
    btUtils = None

CTX = "PlugBoolDragCtx"
SURFACE_OUTWARD_OFFSET = 0.1
DEFAULT_BOOLEAN_FLIP = True
AUTO_BOOLEAN_ON_RELEASE = False
BOOL_OP_UNION = 1
BOOL_OP_SUBTRACT = 2
CUTTER_GROUP = "_boolean_cutters"
CUTTER_DISPLAY_TYPE = 2

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
orientation_flip_enabled = False
invert_combo_was_down = False
boolean_target_mesh = ""
boolean_target_shape = ""
boolean_target_face_id = -1
boolean_cutter_mesh = ""
boolean_result_mesh = ""
boolean_created = False
tool_job = None
drag_session_active = False


def ln(node):
    if not node or not mc.objExists(node):
        return ""
    r = mc.ls(node, l=True) or []
    return r[0] if r else ""


def sn(node):
    return node.split("|")[-1]


def is_comp(node):
    return any(x in node for x in [".vtx[", ".e[", ".f[", ".map["])


def is_mesh_xform(node):
    if not node or not mc.objExists(node) or is_comp(node) or mc.nodeType(node) == "mesh":
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
    a = mc.listRelatives(xform, shapes=True, fullPath=True) or []
    b = mc.listRelatives(xform, ad=True, shapes=True, fullPath=True) or []
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


def get_flip(mesh):
    attr = mesh + ".click2dFlipState"
    return bool(mc.getAttr(attr)) if mc.objExists(attr) else False


def set_flip(mesh, state):
    if not mesh or not mc.objExists(mesh):
        return
    attr = mesh + ".click2dFlipState"
    if not mc.objExists(attr):
        try:
            mc.addAttr(mesh, ln="click2dFlipState", at="bool", dv=False)
        except Exception:
            return
    try:
        mc.setAttr(attr, bool(state))
    except Exception:
        pass


def duplicate_selected_plug(sel):
    global boolean_cutter_mesh
    if drag_session_active and boolean_cutter_mesh and sel and ln(sel[0]) == ln(boolean_cutter_mesh):
        return [ln(boolean_cutter_mesh)]
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
    mc.select(boolean_cutter_mesh, r=True)
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


def ensure_group(name):
    return name if mc.objExists(name) else mc.group(empty=True, name=name)


def set_cutter_display(cutter):
    if not cutter or not mc.objExists(cutter):
        return
    try:
        mc.setAttr(cutter + ".overrideEnabled", 1)
        mc.setAttr(cutter + ".overrideDisplayType", CUTTER_DISPLAY_TYPE)
    except Exception:
        pass


def group_cutter(cutter):
    if not cutter or not mc.objExists(cutter):
        return
    try:
        mc.parent(cutter, ensure_group(CUTTER_GROUP))
    except Exception:
        pass


def bool_nodes(result):
    h = mc.listHistory(result, pruneDagObjects=True) or []
    return [n for n in h if mc.objExists(n) and mc.nodeType(n) == "polyBoolean"]


def tune_bool_node(node):
    for a, v in [("interactiveUpdate", 1), ("maya2025", 1)]:
        p = node + "." + a
        if mc.objExists(p):
            try:
                mc.setAttr(p, v)
            except Exception:
                pass


def current_bool_operation():
    return BOOL_OP_SUBTRACT if orientation_flip_enabled == DEFAULT_BOOLEAN_FLIP else BOOL_OP_UNION


def create_boolean():
    global boolean_result_mesh, boolean_created
    if boolean_created:
        return boolean_result_mesh
    target = ln(boolean_target_mesh)
    cutter = ln(boolean_cutter_mesh)
    if not target:
        mc.warning("No boolean target detected.")
        return None
    if not cutter:
        mc.warning("No boolean cutter found.")
        return None
    if target == cutter:
        mc.warning("Target and cutter are the same object.")
        return None
    try:
        if not mc.pluginInfo("polyBoolean", q=True, loaded=True):
            mc.loadPlugin("polyBoolean")
    except Exception as e:
        mc.warning("Could not load polyBoolean plugin: {0}".format(e))
        return None
    if btUtils is None:
        mc.warning("Could not import booltoolUtils.")
        return None
    try:
        mc.select(target, cutter, r=True)
        btUtils.createBoolTool(current_bool_operation())
    except Exception as e:
        mc.warning("Modern Bool Tool failed: {0}".format(e))
        return None
    sel = mc.ls(sl=True, l=True) or []
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
    for n in bool_nodes(boolean_result_mesh):
        tune_bool_node(n)
    set_cutter_display(cutter)
    group_cutter(cutter)
    if boolean_result_mesh and mc.objExists(boolean_result_mesh):
        mc.select(boolean_result_mesh, r=True)
    return boolean_result_mesh


def start_drag():
    clear_temp()
    reset_state(False)
    global hit_face, tool_job, drag_session_active
    drag_session_active = False
    hit_face = ""
    if mc.draggerContext(CTX, exists=True):
        mc.deleteUI(CTX)
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
    if tool_job and mc.scriptJob(exists=tool_job):
        mc.scriptJob(kill=tool_job, force=True)
    tool_job = mc.scriptJob(event=["ToolChanged", on_tool_changed], protected=True)


def on_press():
    global picked_mesh_transform, visible_mesh_shapes, camera_position, camera_far_clip
    global original_parent_path, current_mesh_name, current_mode, mode_start_x, mode_start_y
    global initial_scale_x, initial_scale_y, initial_scale_z, initial_rotate_y
    global hit_face, duplicate_done, orientation_flip_enabled, invert_combo_was_down, drag_session_active

    hit_face = ""
    current_mode = "move"
    duplicate_done = False
    invert_combo_was_down = False
    if not drag_session_active:
        orientation_flip_enabled = DEFAULT_BOOLEAN_FLIP
        reset_state(False)
    else:
        reset_state(True)

    vx, vy, _ = mc.draggerContext(CTX, q=True, anchorPoint=True)
    mode_start_x = vx
    mode_start_y = vy

    sel = duplicate_selected_plug(mc.ls(sl=True, fl=True, l=True))
    if not sel:
        return

    view = omui.M3dView.active3dView()
    cam_path = om.MDagPath()
    view.getCamera(cam_path)
    cam_shape = cam_path.fullPathName()
    cam = mc.listRelatives(cam_shape, type="transform", p=True)[0]
    camera_far_clip = mc.getAttr(cam + ".farClipPlane")
    camera_position = mc.xform(cam, q=True, ws=True, rp=True)

    visible_mesh_shapes = get_visible_mesh_shapes()
    picked_mesh_transform = [ln(sel[0])]
    if not picked_mesh_transform[0] or not mc.objExists(picked_mesh_transform[0]):
        return

    children = mc.listRelatives(picked_mesh_transform[0], fullPath=True, ad=True) or []
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
    wm = oma.MMatrix(mc.xform(current_mesh_name, q=True, ws=True, matrix=True))
    pw = oma.MPoint(px, min_y, pz) * wm

    mc.move(pw.x, pw.y, pw.z, current_mesh_name + ".scalePivot", current_mesh_name + ".rotatePivot", ws=True, a=True)
    mc.group(empty=True, n="instPicker")
    mc.duplicate("instPicker")
    mc.rename("instFlip")
    mc.parent("instFlip", "instPicker")
    mc.duplicate("instFlip")
    mc.rename("instRot")
    mc.parent("instRot", "instFlip")

    mc.select("instPicker", picked_mesh_transform[0])
    mc.matchTransform(pos=True, rot=True)
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
        mc.delete(ch=True)
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
            mc.parent(mesh_path, w=True)

    final_path = current_mesh_name if not original_parent_path else original_parent_path + "|" + current_mesh_name
    final_path = ln(final_path) or final_path
    if mc.objExists(final_path):
        set_flip(final_path, orientation_flip_enabled)
        boolean_cutter_mesh = final_path

    clear_temp()
    invert_combo_was_down = False

    if AUTO_BOOLEAN_ON_RELEASE:
        create_boolean()
    elif mc.objExists(final_path):
        mc.select(final_path, r=True)


def on_tool_changed():
    global tool_job, drag_session_active
    if mc.currentCtx() == CTX:
        return
    if tool_job and mc.scriptJob(exists=tool_job):
        try:
            mc.scriptJob(kill=tool_job, force=True)
        except Exception:
            pass
    tool_job = None
    if drag_session_active:
        clear_temp()
        create_boolean()
    drag_session_active = False


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
        if mc.objExists("instRot"):
            initial_rotate_y = mc.getAttr("instRot.rotateY")
    invert_combo_was_down = down


def apply_flip():
    if mc.objExists("instFlip"):
        mc.setAttr("instFlip.rotateX", 180 if orientation_flip_enabled else 0)


def switch_mode(new_mode, vx, vy):
    global current_mode, mode_start_x, mode_start_y
    global initial_scale_x, initial_scale_y, initial_scale_z, initial_rotate_y, duplicate_done
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
    if not picked_mesh_transform or not mc.objExists("instPicker") or not mc.objExists("instRot"):
        return
    if mc.draggerContext(CTX, q=True, button=True) == 2:
        duplicate_continue()
        return

    vx, vy, _ = mc.draggerContext(CTX, q=True, dragPoint=True)
    update_flip_toggle()
    nm = mode_from_modifiers()
    if nm != current_mode:
        switch_mode(nm, vx, vy)
        mc.refresh(cv=True, f=True)
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
    mc.refresh(cv=True, f=True)


def drag_move(vx, vy):
    global hit_face
    shape, hit, face_id, normal = raycast(vx, vy)
    if not shape:
        return
    remember_target(shape, face_id)
    face = shape + ".f[" + str(face_id) + "]"
    pos = [hit[0] + normal[0] * SURFACE_OUTWARD_OFFSET, hit[1] + normal[1] * SURFACE_OUTWARD_OFFSET, hit[2] + normal[2] * SURFACE_OUTWARD_OFFSET]
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
    f = max(0.01, 1.0 + dx * 0.01)
    mesh_path = "instPicker|instFlip|instRot|" + current_mesh_name
    if mc.objExists(mesh_path):
        mc.setAttr(mesh_path + ".scaleX", max(0.01, initial_scale_x * f))
        mc.setAttr(mesh_path + ".scaleY", max(0.01, initial_scale_y * f))
        mc.setAttr(mesh_path + ".scaleZ", max(0.01, initial_scale_z * f))


def duplicate_continue():
    global current_mesh_name, initial_scale_x, initial_scale_y, initial_scale_z, initial_rotate_y
    global hit_face, duplicate_done, boolean_cutter_mesh
    if not picked_mesh_transform or not mc.objExists("instPicker") or not mc.objExists("instRot"):
        return
    if duplicate_done:
        vx, vy, _ = mc.draggerContext(CTX, q=True, dragPoint=True)
        drag_move(vx, vy)
        mc.refresh(cv=True, f=True)
        return

    old_path = "|instPicker|instFlip|instRot|" + current_mesh_name
    if not mc.objExists(old_path):
        return
    dup = mc.duplicate(old_path, rr=True)
    dup_name = dup[0]
    set_flip(old_path, orientation_flip_enabled)

    if original_parent_path:
        mc.parent(old_path, original_parent_path)
    else:
        mc.parent(old_path, w=True)

    old_cutter = ln(current_mesh_name)
    if old_cutter:
        boolean_cutter_mesh = old_cutter
        create_boolean()
        reset_state(True)

    current_mesh_name = dup_name
    boolean_cutter_mesh = ln("|instPicker|instFlip|instRot|" + current_mesh_name) or current_mesh_name
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
    vx, vy, _ = mc.draggerContext(CTX, q=True, dragPoint=True)
    drag_move(vx, vy)
    mc.refresh(cv=True, f=True)


def drag_aim(vx, vy):
    shape, hit, face_id, normal = raycast(vx, vy)
    if not shape:
        return
    remember_target(shape, face_id)
    if not mc.objExists("aimLoc"):
        mc.spaceLocator(p=[0, 0, 0], n="aimLoc")
    if not mc.objExists("pickerAim"):
        mc.aimConstraint("aimLoc", "instPicker", offset=[0, 0, 0], weight=1, aimVector=[0, 1, 0], upVector=[0, 1, 0], worldUpType="vector", worldUpVector=[0, 1, 0], n="pickerAim")
    pos = [hit[0] + normal[0] * SURFACE_OUTWARD_OFFSET, hit[1] + normal[1] * SURFACE_OUTWARD_OFFSET, hit[2] + normal[2] * SURFACE_OUTWARD_OFFSET]
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

        ok = fn.closestIntersection(src, om.MFloatVector(wd), None, None, False, om.MSpace.kWorld, camera_far_clip, False, None, hp, None, fp, None, None, None)
        if ok:
            d = math.sqrt((float(camera_position[0]) - hp.x) ** 2 + (float(camera_position[1]) - hp.y) ** 2 + (float(camera_position[2]) - hp.z) ** 2)
            if d < best_dist:
                best_face = fu.getInt(fp)
                best_dist = d
                best_shape = shape
                best_hit = [hp.x, hp.y, hp.z]
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
        om.MGlobal.selectFromScreen(0, 0, view.portWidth(), view.portHeight(), om.MGlobal.kReplaceList)
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
    visible = mc.listRelatives(objs, shapes=True, f=True) or []
    all_meshes = mc.ls(type="mesh", l=True) or []
    return list(set(all_meshes) & set(visible)) if all_meshes and visible else []


def face_rotation(face):
    shape = mc.listRelatives(face, fullPath=True, parent=True)
    xform = mc.listRelatives(shape[0], fullPath=True, parent=True)
    wm = oma.MMatrix(mc.xform(xform, q=True, ws=True, matrix=True))
    txt = mc.polyInfo(face, faceNormals=True)[0]
    vals = [float(v) for v in re.findall(r"-?\d*\.\d*", txt)]
    if len(vals) < 3:
        return 0.0, 0.0, 0.0
    n = oma.MVector(vals[:3]) * wm
    n.normalize()
    q = oma.MQuaternion(oma.MVector(0, 1, 0), n)
    e = q.asEulerRotation()
    return math.degrees(e.x), math.degrees(e.y), math.degrees(e.z)


def smart_boolean_plug_drag():
    sel = mc.ls(sl=True, fl=True, l=True)
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


smart_boolean_plug_drag()
