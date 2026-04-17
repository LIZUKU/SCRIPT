# -*- coding: utf-8 -*-
"""Interactive slot builder for selected NURBS curves in Maya.

This module is intentionally standalone so it can be reused from PR Curve UI.
"""

import math
import maya.cmds as cmds
import maya.api.OpenMaya as om


_SLOT_TOOL = {
    "context_name": "qdSlotToolContext",
    "source_curves": [],
    "preview_nodes": [],
    "width": 1.0,
    "start_width": 1.0,
    "anchor_x": 0.0,
    "press_button": 1,
    "axis_mode": "auto",   # auto / x / y / z
    "active": False,
    "miter_limit": 4.0,
    "smooth_amount": 0.0,
    "start_smooth_amount": 0.0,
    "hard_angle_deg": 170.0,
}


def _info(msg):
    om.MGlobal.displayInfo("[SlotTool] {}".format(msg))


def _warn(msg):
    om.MGlobal.displayWarning("[SlotTool] {}".format(msg))


def _obj_exists(node):
    return bool(node) and cmds.objExists(node)


def _get_curve_shape(node):
    if not _obj_exists(node):
        return None
    if cmds.nodeType(node) == "nurbsCurve":
        return node
    shapes = cmds.listRelatives(node, s=True, ni=True, f=True) or []
    for s in shapes:
        if cmds.nodeType(s) == "nurbsCurve":
            return s
    return None


def _get_curve_fn(node):
    shape = _get_curve_shape(node)
    if not shape:
        raise RuntimeError("Selection invalide : pas de nurbsCurve.")
    sel = om.MSelectionList()
    sel.add(shape)
    dag = sel.getDagPath(0)
    return om.MFnNurbsCurve(dag), shape


def _point_to_tuple(p):
    return (p.x, p.y, p.z)


def _delete_nodes(nodes):
    for n in nodes or []:
        if _obj_exists(n):
            try:
                cmds.delete(n)
            except Exception:
                pass


def _clamp(v, vmin, vmax):
    return max(vmin, min(vmax, v))


def _read_modifiers():
    mods = cmds.getModifiers()
    return bool(mods & 1), bool(mods & 4), bool(mods & 8)


def _cycle_axis_mode(current_mode):
    order = ["auto", "x", "y", "z"]
    try:
        idx = order.index(current_mode)
    except ValueError:
        idx = 0
    return order[(idx + 1) % len(order)]


def _length2d(v):
    return math.sqrt(v[0] * v[0] + v[1] * v[1])


def _normalize2d(v):
    l = _length2d(v)
    if l < 1e-12:
        return (0.0, 0.0)
    return (v[0] / l, v[1] / l)


def _distance2d(a, b):
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return math.sqrt(dx * dx + dy * dy)


def _perp2d(v):
    return (-v[1], v[0])


def _dot2d(a, b):
    return a[0] * b[0] + a[1] * b[1]


def _cross2d(a, b):
    return a[0] * b[1] - a[1] * b[0]


def _signed_area_2d(points):
    n = len(points)
    if n < 3:
        return 0.0
    area = 0.0
    for i in range(n):
        a = points[i]
        b = points[(i + 1) % n]
        area += (a[0] * b[1]) - (b[0] * a[1])
    return area * 0.5


def _line_intersection_2d(p1, d1, p2, d2):
    denom = _cross2d(d1, d2)
    if abs(denom) < 1e-10:
        return None
    diff = (p2[0] - p1[0], p2[1] - p1[1])
    t = _cross2d(diff, d2) / denom
    return (p1[0] + d1[0] * t, p1[1] + d1[1] * t)


def _segment_intersection_2d(a1, a2, b1, b2):
    da = (a2[0] - a1[0], a2[1] - a1[1])
    db = (b2[0] - b1[0], b2[1] - b1[1])
    denom = _cross2d(da, db)
    if abs(denom) < 1e-10:
        return None
    diff = (b1[0] - a1[0], b1[1] - a1[1])
    t = _cross2d(diff, db) / denom
    u = _cross2d(diff, da) / denom
    if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
        return (a1[0] + da[0] * t, a1[1] + da[1] * t)
    return None


def _point_line_distance_2d(p, a, b):
    ab = (b[0] - a[0], b[1] - a[1])
    ap = (p[0] - a[0], p[1] - a[1])
    ab_len = _length2d(ab)
    if ab_len < 1e-12:
        return _distance2d(p, a)
    return abs(_cross2d(ab, ap)) / ab_len


def _cleanup_2d_points(points, eps=1e-5, closed=False):
    if not points:
        return []
    out = [points[0]]
    for p in points[1:]:
        if _distance2d(p, out[-1]) > eps:
            out.append(p)
    if closed and len(out) > 2 and _distance2d(out[0], out[-1]) < eps:
        out.pop(-1)
    return out


def _remove_collinear_points(points, closed=False, tolerance=1e-4):
    pts = points[:]
    if len(pts) < 3:
        return pts
    changed = True
    while changed:
        changed = False
        n = len(pts)
        if n < 3:
            break
        new_pts = []
        if closed:
            for i in range(n):
                prev_p = pts[(i - 1) % n]
                p = pts[i]
                next_p = pts[(i + 1) % n]
                if _distance2d(prev_p, p) < tolerance or _distance2d(p, next_p) < tolerance:
                    changed = True
                    continue
                if _point_line_distance_2d(p, prev_p, next_p) < tolerance:
                    changed = True
                    continue
                new_pts.append(p)
            pts = _cleanup_2d_points(new_pts, eps=tolerance, closed=True)
        else:
            new_pts.append(pts[0])
            for i in range(1, n - 1):
                prev_p, p, next_p = pts[i - 1], pts[i], pts[i + 1]
                if _distance2d(prev_p, p) < tolerance or _distance2d(p, next_p) < tolerance:
                    changed = True
                    continue
                if _point_line_distance_2d(p, prev_p, next_p) < tolerance:
                    changed = True
                    continue
                new_pts.append(p)
            new_pts.append(pts[-1])
            pts = _cleanup_2d_points(new_pts, eps=tolerance, closed=False)
    return pts


def _append_unique(out, pts, eps=1e-5):
    if not pts:
        return
    if not out:
        out.extend(pts)
        return
    for p in pts:
        if _distance2d(out[-1], p) > eps:
            out.append(p)


def _corner_angle_deg(d_prev, d_next):
    return math.degrees(math.acos(_clamp(_dot2d(d_prev, d_next), -1.0, 1.0)))


def _is_hard_corner(d_prev, d_next, hard_angle_deg):
    return _corner_angle_deg(d_prev, d_next) < hard_angle_deg


def _build_arc_with_sweep(center, radius, a0, sweep, steps):
    cx, cy = center
    pts = []
    steps = max(2, int(steps))
    for i in range(steps + 1):
        t = float(i) / float(steps)
        ang = a0 + sweep * t
        pts.append((cx + math.cos(ang) * radius, cy + math.sin(ang) * radius))
    return pts


def _choose_arc_by_outward(center, start_pt, end_pt, outward_dir, steps=8):
    cx, cy = center
    sv = (start_pt[0] - cx, start_pt[1] - cy)
    ev = (end_pt[0] - cx, end_pt[1] - cy)
    radius = _length2d(sv)
    if radius < 1e-10:
        return [start_pt, end_pt]
    a0 = math.atan2(sv[1], sv[0])
    a1 = math.atan2(ev[1], ev[0])
    ccw = a1 - a0
    while ccw <= 0.0:
        ccw += math.pi * 2.0
    cw = a1 - a0
    while cw >= 0.0:
        cw -= math.pi * 2.0
    arc_ccw = _build_arc_with_sweep(center, radius, a0, ccw, steps)
    arc_cw = _build_arc_with_sweep(center, radius, a0, cw, steps)
    mid_ccw = arc_ccw[len(arc_ccw) // 2]
    mid_cw = arc_cw[len(arc_cw) // 2]
    score_ccw = _dot2d(_normalize2d((mid_ccw[0] - cx, mid_ccw[1] - cy)), outward_dir)
    score_cw = _dot2d(_normalize2d((mid_cw[0] - cx, mid_cw[1] - cy)), outward_dir)
    return arc_ccw if score_ccw >= score_cw else arc_cw


def _arc_2d(center, start_pt, end_pt, outward_dir, steps=16):
    return _choose_arc_by_outward(center, start_pt, end_pt, outward_dir, steps=steps)


def _world_to_plane2d(p, axis):
    if axis == "x":
        return (p.y, p.z)
    if axis == "y":
        return (p.x, p.z)
    return (p.x, p.y)


def _plane2d_to_world(p2, axis, base_point):
    if axis == "x":
        return om.MPoint(base_point.x, p2[0], p2[1])
    if axis == "y":
        return om.MPoint(p2[0], base_point.y, p2[1])
    return om.MPoint(p2[0], p2[1], base_point.z)


def _sample_curve_world(fn_curve, count=120):
    total_len = fn_curve.length()
    is_closed = fn_curve.form in (om.MFnNurbsCurve.kClosed, om.MFnNurbsCurve.kPeriodic)
    samples = count if is_closed else (count + 1)
    denom = float(count) if count > 0 else 1.0
    pts3 = []
    for i in range(samples):
        param = fn_curve.findParamFromLength(total_len * (float(i) / denom))
        pts3.append(fn_curve.getPointAtParam(param, om.MSpace.kWorld))
    return pts3, is_closed


def _projection_score(points3d, axis, is_closed):
    pts2 = _cleanup_2d_points([_world_to_plane2d(p, axis) for p in points3d], eps=1e-5, closed=is_closed)
    if len(pts2) < 2:
        return -1e20
    xs, ys = [p[0] for p in pts2], [p[1] for p in pts2]
    width, height = max(xs) - min(xs), max(ys) - min(ys)
    if is_closed:
        return abs(_signed_area_2d(pts2)) + (width * height * 0.01)
    span = sum(_distance2d(pts2[i], pts2[i + 1]) for i in range(len(pts2) - 1))
    return span + max(width, height) * 0.01


def _auto_best_axis(curve_node):
    fn_curve, _ = _get_curve_fn(curve_node)
    points3d, is_closed = _sample_curve_world(fn_curve, count=90)
    best_axis, best_score = "z", -1e20
    for axis in ("x", "y", "z"):
        score = _projection_score(points3d, axis, is_closed)
        if score > best_score:
            best_score, best_axis = score, axis
    return best_axis


def _resolve_axis_for_curve(curve_node, axis_mode):
    return _auto_best_axis(curve_node) if axis_mode == "auto" else axis_mode


def _extract_curve_points_2d(fn_curve, axis):
    is_closed = fn_curve.form in (om.MFnNurbsCurve.kClosed, om.MFnNurbsCurve.kPeriodic)
    degree, num_cvs = fn_curve.degree, fn_curve.numCVs
    pts3, pts2 = [], []
    use_cvs = (degree == 1) or (num_cvs <= 12)
    if use_cvs:
        for p in fn_curve.cvPositions(om.MSpace.kWorld):
            pts3.append(p)
            pts2.append(_world_to_plane2d(p, axis))
        pts2 = _cleanup_2d_points(pts2, eps=1e-5, closed=is_closed)
        if len(pts2) > 3:
            pts2 = _remove_collinear_points(pts2, closed=is_closed, tolerance=1e-5)
        if pts3:
            return pts3, pts2, is_closed
    sample_count = 220 if not is_closed else 180
    total_len = fn_curve.length()
    samples = sample_count if is_closed else (sample_count + 1)
    denom = float(sample_count) if sample_count > 0 else 1.0
    for i in range(samples):
        param = fn_curve.findParamFromLength(total_len * (float(i) / denom))
        p = fn_curve.getPointAtParam(param, om.MSpace.kWorld)
        pts3.append(p)
        pts2.append(_world_to_plane2d(p, axis))
    pts2 = _cleanup_2d_points(pts2, eps=1e-5, closed=is_closed)
    if len(pts2) > 3:
        pts2 = _remove_collinear_points(pts2, closed=is_closed, tolerance=1e-4)
    return pts3, pts2, is_closed


def _join_intersection(p, d_prev, d_next, n_prev, n_next, radius, miter_limit):
    prev_offset = (p[0] + n_prev[0] * radius, p[1] + n_prev[1] * radius)
    next_offset = (p[0] + n_next[0] * radius, p[1] + n_next[1] * radius)
    inter = _line_intersection_2d(prev_offset, d_prev, next_offset, d_next)
    if inter is None:
        return None
    if _distance2d(inter, p) > abs(radius) * max(1.0, miter_limit):
        return None
    return inter


def _offset_is_outside_for_closed_polygon(points2d, radius):
    area = _signed_area_2d(points2d)
    return radius < 0.0 if area > 0.0 else radius > 0.0


def _corner_is_convex_original(d_prev, d_next, polygon_area):
    turn = _cross2d(d_prev, d_next)
    return turn > 0.0 if polygon_area > 0.0 else turn < 0.0


def _build_open_side(points2d, radius, miter_limit=4.0, round_amount=0.0, hard_angle_deg=170.0, arc_steps=20):
    n = len(points2d)
    if n < 2:
        return points2d[:]
    dirs, norms = [], []
    for i in range(n - 1):
        a, b = points2d[i], points2d[i + 1]
        d = _normalize2d((b[0] - a[0], b[1] - a[1]))
        if d == (0.0, 0.0):
            d = (1.0, 0.0)
        dirs.append(d)
        norms.append(_perp2d(d))
    out = [(points2d[0][0] + norms[0][0] * radius, points2d[0][1] + norms[0][1] * radius)]
    for i in range(1, n - 1):
        p = points2d[i]
        d_prev, d_next = dirs[i - 1], dirs[i]
        n_prev, n_next = norms[i - 1], norms[i]
        prev_pt = (p[0] + n_prev[0] * radius, p[1] + n_prev[1] * radius)
        next_pt = (p[0] + n_next[0] * radius, p[1] + n_next[1] * radius)
        is_outer_side = (_cross2d(d_prev, d_next) * radius) > 1e-10
        if is_outer_side and round_amount > 0.0 and _is_hard_corner(d_prev, d_next, hard_angle_deg):
            outward_dir = _normalize2d(((n_prev[0] + n_next[0]) * radius, (n_prev[1] + n_next[1]) * radius))
            if outward_dir == (0.0, 0.0):
                outward_dir = _normalize2d((n_next[0] * radius, n_next[1] * radius))
            pts = _choose_arc_by_outward(p, prev_pt, next_pt, outward_dir, steps=max(4, int(4 + round_amount * arc_steps)))
            _append_unique(out, pts[1:])
        elif is_outer_side:
            inter = _join_intersection(p, d_prev, d_next, n_prev, n_next, radius, miter_limit)
            _append_unique(out, [inter] if inter is not None else [prev_pt, next_pt])
        else:
            _append_unique(out, [prev_pt, next_pt])
    _append_unique(out, [(points2d[-1][0] + norms[-1][0] * radius, points2d[-1][1] + norms[-1][1] * radius)])
    out = _cleanup_2d_points(out, eps=1e-5, closed=False)
    if len(out) > 2:
        out = _remove_collinear_points(out, closed=False, tolerance=1e-4)
    return out


def _build_closed_offset(points2d, radius, miter_limit=4.0, round_amount=0.0, hard_angle_deg=170.0, arc_steps=20):
    n = len(points2d)
    if n < 3:
        return points2d[:]
    polygon_area = _signed_area_2d(points2d)
    is_outside_side = _offset_is_outside_for_closed_polygon(points2d, radius)
    out = []
    for i in range(n):
        p_prev, p, p_next = points2d[(i - 1) % n], points2d[i], points2d[(i + 1) % n]
        d_prev = _normalize2d((p[0] - p_prev[0], p[1] - p_prev[1]))
        d_next = _normalize2d((p_next[0] - p[0], p_next[1] - p[1]))
        if d_prev == (0.0, 0.0):
            d_prev = d_next
        if d_next == (0.0, 0.0):
            d_next = d_prev
        n_prev, n_next = _perp2d(d_prev), _perp2d(d_next)
        prev_pt = (p[0] + n_prev[0] * radius, p[1] + n_prev[1] * radius)
        next_pt = (p[0] + n_next[0] * radius, p[1] + n_next[1] * radius)
        original_convex = _corner_is_convex_original(d_prev, d_next, polygon_area)
        is_outer_corner = (original_convex and is_outside_side) or ((not original_convex) and (not is_outside_side))
        if is_outer_corner and round_amount > 0.0 and _is_hard_corner(d_prev, d_next, hard_angle_deg):
            outward_dir = _normalize2d(((n_prev[0] + n_next[0]) * radius, (n_prev[1] + n_next[1]) * radius))
            if outward_dir == (0.0, 0.0):
                outward_dir = _normalize2d((n_next[0] * radius, n_next[1] * radius))
            pts = _choose_arc_by_outward(p, prev_pt, next_pt, outward_dir, steps=max(4, int(4 + round_amount * arc_steps)))
            if not out:
                out.extend(pts)
            else:
                _append_unique(out, pts[1:])
        elif is_outer_corner:
            inter = _join_intersection(p, d_prev, d_next, n_prev, n_next, radius, miter_limit)
            if not out:
                out.extend([inter] if inter is not None else [prev_pt, next_pt])
            else:
                _append_unique(out, [inter] if inter is not None else [prev_pt, next_pt])
        else:
            if not out:
                out.extend([prev_pt, next_pt])
            else:
                _append_unique(out, [prev_pt, next_pt])
    out = _cleanup_2d_points(out, eps=1e-5, closed=True)
    if len(out) > 3:
        out = _remove_collinear_points(out, closed=True, tolerance=1e-4)
    return out


def _light_prune_self_intersections_closed(points2d):
    if len(points2d) < 4:
        return points2d[:]
    pts = points2d[:]
    for _ in range(6):
        changed = False
        n = len(pts)
        if n < 4:
            break
        for i in range(n):
            a1, a2 = pts[i], pts[(i + 1) % n]
            for j in range(i + 2, n):
                if (j + 1) % n == i or j == i:
                    continue
                b1, b2 = pts[j], pts[(j + 1) % n]
                hit = _segment_intersection_2d(a1, a2, b1, b2)
                if hit is not None:
                    new_pts = []
                    for k in range(n):
                        if k == i or k == (j + 1) % n:
                            new_pts.append(hit)
                        elif i < j:
                            if not (i < k <= j):
                                new_pts.append(pts[k])
                        else:
                            new_pts.append(pts[k])
                    pts = _cleanup_2d_points(new_pts, eps=1e-5, closed=True)
                    changed = True
                    break
            if changed:
                break
        if not changed:
            break
    return pts


def _create_curve_from_2d(points2d, axis, base_point, close=True, name="slot_crv"):
    pts2d = _cleanup_2d_points(points2d, eps=1e-5, closed=close)
    if len(pts2d) > 3:
        pts2d = _remove_collinear_points(pts2d, closed=close, tolerance=1e-4)
    pts3d = [_plane2d_to_world(p, axis, base_point) for p in pts2d]
    crv = cmds.curve(p=[_point_to_tuple(p) for p in pts3d], d=1)
    if close:
        crv = cmds.closeCurve(crv, ch=False, ps=False, rpo=True)[0]
    try:
        crv = cmds.rename(crv, name)
    except Exception:
        pass
    return crv


def build_result_for_curve(curve_node, width=1.0, axis_mode="auto", miter_limit=4.0, smooth_amount=0.0, hard_angle_deg=170.0):
    fn_curve, _shape = _get_curve_fn(curve_node)
    axis = _resolve_axis_for_curve(curve_node, axis_mode)
    radius = max(0.0001, width * 0.5)
    round_amount = _clamp(smooth_amount, 0.0, 1.0)
    pts3, pts2, is_closed = _extract_curve_points_2d(fn_curve, axis)
    if len(pts2) < 2:
        raise RuntimeError("Pas assez de points.")
    base_point = pts3[0]
    arc_steps = int(round(10 + round_amount * 28.0))
    if not is_closed:
        side_plus = _build_open_side(pts2, radius, miter_limit, round_amount, hard_angle_deg, arc_steps)
        side_minus = _build_open_side(pts2, -radius, miter_limit, round_amount, hard_angle_deg, arc_steps)
        start_dir = _normalize2d((pts2[1][0] - pts2[0][0], pts2[1][1] - pts2[0][1]))
        end_dir = _normalize2d((pts2[-1][0] - pts2[-2][0], pts2[-1][1] - pts2[-2][1]))
        start_cap = _arc_2d(pts2[0], side_minus[0], side_plus[0], (-start_dir[0], -start_dir[1]), steps=max(10, arc_steps))
        end_cap = _arc_2d(pts2[-1], side_plus[-1], side_minus[-1], end_dir, steps=max(10, arc_steps))
        final2d = side_plus + end_cap[1:-1] + list(reversed(side_minus)) + start_cap[1:-1]
        final2d = _cleanup_2d_points(final2d, eps=1e-5, closed=True)
        if len(final2d) > 3:
            final2d = _remove_collinear_points(final2d, closed=True, tolerance=1e-4)
        final2d = _light_prune_self_intersections_closed(final2d)
        final2d = _cleanup_2d_points(final2d, eps=1e-5, closed=True)
        return [_create_curve_from_2d(final2d, axis, base_point, close=True, name=curve_node.split("|")[-1] + "_slotPreview")]
    outer2d = _light_prune_self_intersections_closed(_cleanup_2d_points(_build_closed_offset(pts2, radius, miter_limit, round_amount, hard_angle_deg, arc_steps), eps=1e-5, closed=True))
    inner2d = _light_prune_self_intersections_closed(_cleanup_2d_points(_build_closed_offset(pts2, -radius, miter_limit, round_amount, hard_angle_deg, arc_steps), eps=1e-5, closed=True))
    outer_name, inner_name = curve_node.split("|")[-1] + "_outerPreview", curve_node.split("|")[-1] + "_innerPreview"
    outer_crv = _create_curve_from_2d(outer2d, axis, base_point, close=True, name=outer_name)
    inner_crv = _create_curve_from_2d(inner2d, axis, base_point, close=True, name=inner_name)
    return [cmds.group([outer_crv, inner_crv], n=curve_node.split("|")[-1] + "_bandPreview_grp")]


def _colorize_curve_or_group(node, rgb=(1.0, 0.75, 0.15)):
    if not _obj_exists(node):
        return
    shapes = []
    node_type = cmds.nodeType(node)
    if node_type == "transform":
        shapes.extend(cmds.listRelatives(node, ad=True, s=True, f=True) or [])
    elif node_type == "nurbsCurve":
        shapes.append(node)
    for s in shapes:
        if cmds.nodeType(s) == "nurbsCurve":
            try:
                cmds.setAttr(s + ".overrideEnabled", 1)
                cmds.setAttr(s + ".overrideRGBColors", 1)
                cmds.setAttr(s + ".overrideColorRGB", rgb[0], rgb[1], rgb[2])
            except Exception:
                pass


def _delete_preview():
    _delete_nodes(_SLOT_TOOL.get("preview_nodes", []))
    _SLOT_TOOL["preview_nodes"] = []


def create_or_update_slot_preview():
    _delete_preview()
    created = []
    for src in _SLOT_TOOL["source_curves"]:
        if not _obj_exists(src):
            continue
        try:
            created.extend(build_result_for_curve(src, width=_SLOT_TOOL["width"], axis_mode=_SLOT_TOOL["axis_mode"],
                                                  miter_limit=_SLOT_TOOL["miter_limit"], smooth_amount=_SLOT_TOOL["smooth_amount"],
                                                  hard_angle_deg=_SLOT_TOOL["hard_angle_deg"]))
        except Exception as e:
            _warn("{} : {}".format(src.split("|")[-1], e))
    for n in created:
        _colorize_curve_or_group(n)
    _SLOT_TOOL["preview_nodes"] = created
    axis_txt = _SLOT_TOOL["axis_mode"].upper() if _SLOT_TOOL["axis_mode"] != "auto" else "AUTO"
    cmds.inViewMessage(amg='Width: <hl>{:.3f}</hl> | Axis: <hl>{}</hl> | CornerRound: <hl>{:.2f}</hl> | HardAngle: <hl>{:.1f}</hl>'.format(
        _SLOT_TOOL["width"], axis_txt, _SLOT_TOOL["smooth_amount"], _SLOT_TOOL["hard_angle_deg"]
    ), pos='botCenter', fade=True)
    cmds.refresh(cv=True)


def finalize_slot_tool():
    previews, finals = _SLOT_TOOL.get("preview_nodes", []), []
    for node in previews:
        if not _obj_exists(node):
            continue
        base = node.replace("_slotPreview", "_slot").replace("_outerPreview", "_outer").replace("_innerPreview", "_inner").replace("_bandPreview_grp", "_band")
        try:
            node = cmds.rename(node, base)
        except Exception:
            pass
        finals.append(node)
    _SLOT_TOOL["preview_nodes"] = []
    _SLOT_TOOL["active"] = False
    if finals:
        cmds.select(finals, r=True)


def cancel_slot_tool():
    _delete_preview()
    _SLOT_TOOL["active"] = False


def slot_tool_press():
    ctx = _SLOT_TOOL["context_name"]
    anchor = cmds.draggerContext(ctx, q=True, anchorPoint=True)
    _SLOT_TOOL["anchor_x"] = anchor[0]
    _SLOT_TOOL["start_width"] = _SLOT_TOOL["width"]
    _SLOT_TOOL["start_smooth_amount"] = _SLOT_TOOL["smooth_amount"]
    _SLOT_TOOL["press_button"] = cmds.draggerContext(ctx, q=True, button=True)
    has_shift, _has_ctrl, _has_alt = _read_modifiers()
    if has_shift:
        _SLOT_TOOL["axis_mode"] = _cycle_axis_mode(_SLOT_TOOL["axis_mode"])
    create_or_update_slot_preview()


def slot_tool_drag():
    ctx = _SLOT_TOOL["context_name"]
    drag = cmds.draggerContext(ctx, q=True, dragPoint=True)
    dx = drag[0] - _SLOT_TOOL["anchor_x"]
    button = _SLOT_TOOL.get("press_button", 1)
    sensitivity = 0.01
    _has_shift, has_ctrl, _has_alt = _read_modifiers()
    if has_ctrl:
        _SLOT_TOOL["width"] = _SLOT_TOOL["start_width"]
        delta = dx * sensitivity
        new_smooth = _SLOT_TOOL["start_smooth_amount"] + (delta if button != 3 else -delta)
        _SLOT_TOOL["smooth_amount"] = _clamp(new_smooth, 0.0, 1.0)
        create_or_update_slot_preview()
        return
    delta = dx * sensitivity
    new_width = _SLOT_TOOL["start_width"] + (delta if button != 3 else -delta)
    _SLOT_TOOL["width"] = max(0.001, new_width)
    create_or_update_slot_preview()


def slot_tool_release():
    create_or_update_slot_preview()


def slot_tool_finalize():
    try:
        finalize_slot_tool()
    except Exception as e:
        _warn("Finalize error : {}".format(e))


def start_slot_tool(initial_width=1.0, miter_limit=4.0, initial_smooth=0.0, hard_angle_deg=170.0):
    sel = cmds.ls(sl=True, l=True) or []
    if not sel:
        _warn("Selectionne une ou plusieurs courbes NURBS.")
        return
    curves = [obj for obj in sel if _get_curve_shape(obj)]
    if not curves:
        _warn("Aucune courbe NURBS valide dans la selection.")
        return
    _SLOT_TOOL.update({
        "source_curves": curves,
        "preview_nodes": [],
        "width": max(0.001, initial_width),
        "start_width": max(0.001, initial_width),
        "axis_mode": "auto",
        "active": True,
        "miter_limit": max(1.0, miter_limit),
        "smooth_amount": _clamp(initial_smooth, 0.0, 1.0),
        "start_smooth_amount": _clamp(initial_smooth, 0.0, 1.0),
        "hard_angle_deg": _clamp(hard_angle_deg, 45.0, 179.9),
    })
    _delete_preview()
    create_or_update_slot_preview()
    ctx = _SLOT_TOOL["context_name"]
    if cmds.draggerContext(ctx, exists=True):
        cmds.deleteUI(ctx)
    cmds.draggerContext(
        ctx,
        pressCommand='import slot_tool; slot_tool.slot_tool_press()',
        dragCommand='import slot_tool; slot_tool.slot_tool_drag()',
        releaseCommand='import slot_tool; slot_tool.slot_tool_release()',
        finalize='import slot_tool; slot_tool.slot_tool_finalize()',
        cursor='crossHair',
        undoMode='step',
        space='screen'
    )
    cmds.setToolTo(ctx)
    _info("Actif : drag gauche=+, drag droit=-, Shift=cycle axe, Ctrl+drag=corner round local, Q=valide.")


def launch_slot_tool_from_ui():
    start_slot_tool(initial_width=20.0, miter_limit=4.0, initial_smooth=0.0, hard_angle_deg=175.0)
