import re
import math
import statistics
import maya.cmds as cmds
import maya.cmds as mc
import maya.mel as mel
import maya.api.OpenMaya as om
import maya.api.OpenMaya as om2


MERGE_DISTANCE = 0.01
LONG_EDGE_FACTOR = 3.0


# =========================================================
# VERTEX UNBEVEL
# =========================================================

def get_mesh_from_component(component):
    return component.split(".")[0]


def get_vtx_id(component):
    m = re.search(r"\.vtx\[(\d+)\]", component)
    return int(m.group(1)) if m else None


def get_dag(mesh):
    sel = om.MSelectionList()
    sel.add(mesh)
    dag = sel.getDagPath(0)

    if dag.node().hasFn(om.MFn.kTransform):
        dag.extendToShape()

    return dag


def get_point(mesh_fn, v_id):
    return om.MVector(mesh_fn.getPoint(v_id, om.MSpace.kWorld))


def build_adjacency_and_border_edges(dag, mesh_fn):
    adjacency = {}
    edge_lengths = {}
    border_edges = set()

    it_vtx = om.MItMeshVertex(dag)
    for i in range(mesh_fn.numVertices):
        it_vtx.setIndex(i)
        adjacency[i] = list(it_vtx.getConnectedVertices())

    it_edge = om.MItMeshEdge(dag)
    while not it_edge.isDone():
        v1 = it_edge.vertexId(0)
        v2 = it_edge.vertexId(1)

        p1 = get_point(mesh_fn, v1)
        p2 = get_point(mesh_fn, v2)

        key = tuple(sorted((v1, v2)))
        edge_lengths[key] = (p1 - p2).length()

        if it_edge.onBoundary():
            border_edges.add(key)

        it_edge.next()

    return adjacency, edge_lengths, border_edges


def is_border_connection(a, b, border_edges):
    return tuple(sorted((a, b))) in border_edges


def filtered_connected_components(selected_ids, adjacency, edge_lengths):
    selected_ids = set(selected_ids)
    selected_edge_lengths = []

    for v in selected_ids:
        for n in adjacency.get(v, []):
            if n in selected_ids:
                key = tuple(sorted((v, n)))
                if key in edge_lengths:
                    selected_edge_lengths.append(edge_lengths[key])

    if selected_edge_lengths:
        typical = statistics.median(selected_edge_lengths)
        max_allowed = typical * LONG_EDGE_FACTOR
    else:
        max_allowed = 999999999.0

    visited = set()
    components = []

    for v in selected_ids:
        if v in visited:
            continue

        stack = [v]
        comp = []
        visited.add(v)

        while stack:
            cur = stack.pop()
            comp.append(cur)

            for n in adjacency.get(cur, []):
                if n not in selected_ids or n in visited:
                    continue

                key = tuple(sorted((cur, n)))
                length = edge_lengths.get(key, 0.0)

                if length > max_allowed:
                    continue

                visited.add(n)
                stack.append(n)

        components.append(sorted(comp))

    return components


def line_intersection_2d(p1, d1, p2, d2):
    x1, y1 = p1
    dx1, dy1 = d1
    x2, y2 = p2
    dx2, dy2 = d2

    denom = dx1 * dy2 - dy1 * dx2

    if abs(denom) < 1e-8:
        return None

    rx = x2 - x1
    ry = y2 - y1
    t = (rx * dy2 - ry * dx2) / denom

    return (x1 + dx1 * t, y1 + dy1 * t)


def make_local_plane(points):
    origin = sum(points, om.MVector()) / float(len(points))

    axis_x = None
    max_dist = 0.0

    for a in points:
        for b in points:
            d = b - a
            dist = d.length()
            if dist > max_dist:
                max_dist = dist
                axis_x = d

    if axis_x is None or axis_x.length() < 1e-8:
        return None

    axis_x.normalize()

    normal = None
    best_area = 0.0

    for a in points:
        for b in points:
            for c in points:
                n = (b - a) ^ (c - a)
                area = n.length()
                if area > best_area:
                    best_area = area
                    normal = n

    if normal is None or normal.length() < 1e-8:
        up = om.MVector(0, 1, 0)
        normal = axis_x ^ up

        if normal.length() < 1e-8:
            up = om.MVector(0, 0, 1)
            normal = axis_x ^ up

    normal.normalize()

    axis_y = normal ^ axis_x

    if axis_y.length() < 1e-8:
        return None

    axis_y.normalize()

    return origin, axis_x, axis_y


def to_2d(point, origin, axis_x, axis_y):
    v = point - origin
    return (v * axis_x, v * axis_y)


def from_2d(point_2d, origin, axis_x, axis_y):
    x, y = point_2d
    return origin + axis_x * x + axis_y * y


def get_best_endpoints(comp, adjacency):
    comp_set = set(comp)

    endpoints = [
        v for v in comp
        if len([n for n in adjacency[v] if n in comp_set]) <= 1
    ]

    if len(endpoints) >= 2:
        return endpoints[0], endpoints[-1]

    return comp[0], comp[-1]


def component_is_on_border(comp, adjacency, border_edges):
    comp_set = set(comp)

    for v in comp:
        for n in adjacency[v]:
            if n in comp_set and is_border_connection(v, n, border_edges):
                return True

    return False


def get_outside_neighbors(endpoint, comp_set, adjacency, border_edges, prefer_border):
    outside = [n for n in adjacency[endpoint] if n not in comp_set]

    if not prefer_border:
        return outside

    border_outside = [
        n for n in outside
        if is_border_connection(endpoint, n, border_edges)
    ]

    return border_outside if border_outside else outside


def find_corner_by_2d_intersection(mesh_fn, adjacency, border_edges, comp):
    comp_set = set(comp)

    e1, e2 = get_best_endpoints(comp, adjacency)
    prefer_border = component_is_on_border(comp, adjacency, border_edges)

    outside_1 = get_outside_neighbors(e1, comp_set, adjacency, border_edges, prefer_border)
    outside_2 = get_outside_neighbors(e2, comp_set, adjacency, border_edges, prefer_border)

    if not outside_1 or not outside_2:
        return None

    p1 = get_point(mesh_fn, e1)
    p2 = get_point(mesh_fn, e2)

    plane_points = [get_point(mesh_fn, v) for v in comp]
    plane_points += [p1, p2]

    for o in outside_1 + outside_2:
        plane_points.append(get_point(mesh_fn, o))

    plane = make_local_plane(plane_points)

    if plane is None:
        return None

    origin, axis_x, axis_y = plane

    p1_2d = to_2d(p1, origin, axis_x, axis_y)
    p2_2d = to_2d(p2, origin, axis_x, axis_y)

    best_corner = None
    best_score = 999999999.0

    for o1 in outside_1:
        po1 = get_point(mesh_fn, o1)
        d1_3d = p1 - po1

        if d1_3d.length() < 1e-8:
            continue

        d1_target_2d = to_2d(p1 + d1_3d, origin, axis_x, axis_y)
        d1_2d = (
            d1_target_2d[0] - p1_2d[0],
            d1_target_2d[1] - p1_2d[1]
        )

        for o2 in outside_2:
            po2 = get_point(mesh_fn, o2)
            d2_3d = p2 - po2

            if d2_3d.length() < 1e-8:
                continue

            d2_target_2d = to_2d(p2 + d2_3d, origin, axis_x, axis_y)
            d2_2d = (
                d2_target_2d[0] - p2_2d[0],
                d2_target_2d[1] - p2_2d[1]
            )

            inter_2d = line_intersection_2d(p1_2d, d1_2d, p2_2d, d2_2d)

            if inter_2d is None:
                continue

            corner_3d = from_2d(inter_2d, origin, axis_x, axis_y)
            score = (corner_3d - p1).length() + (corner_3d - p2).length()

            if score < best_score:
                best_score = score
                best_corner = corner_3d

    return best_corner


def move_vertices_to_point(mesh_fn, comp, point):
    for v in comp:
        mesh_fn.setPoint(v, om.MPoint(point), om.MSpace.kWorld)


def merge_vertices(mesh, comp):
    verts = [f"{mesh}.vtx[{v}]" for v in comp]
    cmds.select(verts, r=True)
    cmds.polyMergeVertex(d=MERGE_DISTANCE, ch=False)


def unbevel_selected_vertices():
    selection = cmds.ls(sl=True, fl=True)

    if not selection:
        cmds.warning("Sélectionne les vertices des bevels à supprimer.")
        return

    mesh_to_ids = {}

    for item in selection:
        if ".vtx[" not in item:
            continue

        mesh = get_mesh_from_component(item)
        v_id = get_vtx_id(item)

        if v_id is not None:
            mesh_to_ids.setdefault(mesh, set()).add(v_id)

    if not mesh_to_ids:
        cmds.warning("La sélection doit contenir des vertices.")
        return

    cmds.undoInfo(openChunk=True)

    try:
        for mesh, selected_ids in mesh_to_ids.items():
            if not cmds.objExists(mesh):
                continue

            cmds.delete(mesh, ch=True)

            dag = get_dag(mesh)
            mesh_fn = om.MFnMesh(dag)
            adjacency, edge_lengths, border_edges = build_adjacency_and_border_edges(dag, mesh_fn)

            components = filtered_connected_components(selected_ids, adjacency, edge_lengths)
            components.sort(key=lambda c: max(c), reverse=True)

            for comp in components:
                if not cmds.objExists(mesh):
                    continue

                dag = get_dag(mesh)
                mesh_fn = om.MFnMesh(dag)
                adjacency, edge_lengths, border_edges = build_adjacency_and_border_edges(dag, mesh_fn)

                comp = [v for v in comp if v < mesh_fn.numVertices]

                if len(comp) < 2:
                    continue

                corner = find_corner_by_2d_intersection(mesh_fn, adjacency, border_edges, comp)

                if corner is None:
                    cmds.warning("Intersection introuvable pour un groupe.")
                    continue

                move_vertices_to_point(mesh_fn, comp, corner)
                cmds.refresh()

                try:
                    merge_vertices(mesh, comp)
                except:
                    cmds.warning("Merge impossible pour un groupe.")

            cmds.select(mesh, r=True)

        cmds.refresh()
        print("Un-bevel vertex terminé.")

    finally:
        cmds.undoInfo(closeChunk=True)


# =========================================================
# EDGE UNBEVEL — SCRIPT EDGE CORRIGÉ
# =========================================================

def _flatten_component_list(data):
    result = []

    if data is None:
        return result

    if isinstance(data, (str, unicode)) if 'unicode' in dir(__builtins__) else isinstance(data, str):
        return [data]

    for item in data:
        if isinstance(item, list):
            result.extend(_flatten_component_list(item))
        else:
            result.append(item)

    return result


def unBevel():
    global ppData
    global vLData
    global cLData
    global cumulative_fractions
    global storeUniBevelCountA
    global storeUniBevelCountB

    storeUniBevelCountA = 100
    storeUniBevelCountB = 100
    ppData = []
    vLData = []
    cLData = []
    cumulative_fractions = []

    selEdge = mc.filterExpand(expand=True, sm=32)

    if selEdge:
        selEdge = mc.ls(selEdge, fl=True)

        if mc.objExists('saveSel'):
            mc.delete('saveSel')

        mc.sets(selEdge, name="saveSel", text="saveSel")

        sortGrp = getEdgeRingGroup()

        clean_groups = []
        for g in sortGrp:
            clean_g = mc.ls(_flatten_component_list(g), fl=True)
            if clean_g:
                clean_groups.append(clean_g)

        for e in clean_groups:
            pPoint, vList, cList = unBevelEdgeLoop(e)
            ppData.append(pPoint)
            vLData.append(vList)
            cLData.append(cList)

        mc.select(selEdge, r=True)

        selEdges = mc.ls(sl=1, fl=1)
        tVer = mc.ls(mc.polyListComponentConversion(selEdges, tv=True), fl=True)
        tFac = mc.ls(mc.polyListComponentConversion(tVer, tf=True, internal=1), fl=True)
        tEdg = mc.ls(mc.polyListComponentConversion(tFac, te=True, internal=1), fl=True)

        findLoop = list(set(tEdg) - set(selEdge))
        goodLoop = []

        if findLoop:
            oneLoop = mc.polySelectSp(findLoop[0], q=1, loop=1)
            oneLoop = mc.ls(oneLoop, fl=1)
            goodLoop = list(set(oneLoop) & set(tEdg))
        else:
            goodLoop = selEdges

        goodLoop = mc.ls(goodLoop, fl=1)

        getCircleState, getVOrder = vtxLoopOrderCheck(goodLoop)
        distances = calculate_edge_distances(getVOrder)
        distances.insert(0, 0)

        total_distance = sum(distances)

        if total_distance == 0:
            total_distance = 1.0

        cumulative_fractions = []
        cumulative_sum = 0

        for distance in distances:
            cumulative_sum += distance
            fraction = cumulative_sum / total_distance
            cumulative_fractions.append(round(fraction, 3))

        global ctx
        ctx = 'unBevelCtx'

        if mc.draggerContext(ctx, exists=True):
            mc.deleteUI(ctx)

        mc.draggerContext(
            ctx,
            pressCommand=unBevelPress,
            rc=unBevelOff,
            dragCommand=unBevelDrag,
            name=ctx,
            cursor='crossHair',
            undoMode='step'
        )

        mc.setToolTo(ctx)


def unBevelOff():
    try:
        mc.headsUpDisplay('HUDunBevelStep', rem=True)
    except:
        pass

    global vLData

    flattenList = []

    for v in vLData:
        for x in range(len(v)):
            flattenList.append(v[x])

    flattenList = mc.ls(flattenList, fl=True)

    if flattenList:
        mc.polyMergeVertex(flattenList, d=0.001, am=0, ch=0)

        meshName = flattenList[0].split('.')[0]

        if mc.objExists('saveSel'):
            mc.select('saveSel')
            mc.delete('saveSel')

        cmd = 'doMenuNURBComponentSelection("' + meshName + '", "edge");'
        mel.eval(cmd)

    mc.setToolTo('selectSuperContext')


def currentStep():
    global viewPortCount

    if viewPortCount >= 1:
        getPercent = viewPortCount / 100.0
    elif viewPortCount < 1 and viewPortCount > 0:
        getPercent = 0.1
    else:
        getPercent = 0

    return '%.2f' % getPercent


def unBevelPress():
    global ctx
    global screenX, screenY
    global lockCount
    global storeCount
    global viewPortCount

    viewPortCount = 0
    lockCount = 50
    storeCount = 0

    vpX, vpY, _ = mc.draggerContext(ctx, query=True, anchorPoint=True)
    screenX = vpX
    screenY = vpY

    try:
        if mc.headsUpDisplay('HUDunBevelStep', exists=True):
            mc.headsUpDisplay('HUDunBevelStep', rem=True)
    except:
        pass

    mc.headsUpDisplay(
        'HUDunBevelStep',
        section=3,
        block=1,
        blockSize='large',
        label='unBevel',
        labelFontSize='large',
        command=currentStep,
        atr=1,
        ao=1
    )


def unBevelDrag():
    global storeUniBevelCountA
    global storeUniBevelCountB
    global storeCount
    global viewPortCount
    global ppData
    global vLData
    global screenX, screenY
    global lockCount
    global cLData
    global cumulative_fractions

    modifiers = mc.getModifiers()
    vpX, vpY, _ = mc.draggerContext(ctx, query=True, dragPoint=True)

    if modifiers == 5:
        for i in range(len(ppData)):
            mc.scale(0, 0, 0, vLData[i], cs=1, r=1, p=(ppData[i][0], ppData[i][1], ppData[i][2]))

        viewPortCount = 0

    elif modifiers == 8:
        lockCount = lockCount - 1 if screenX > vpX else lockCount + 1
        screenX = vpX

        if lockCount > 0:
            getX = int(lockCount / 10) * 10

            if storeCount != getX:
                storeCount = getX

                for i in range(len(ppData)):
                    for v in range(len(vLData[i])):
                        moveX = ppData[i][0] - (cLData[i][v][0] * lockCount)
                        moveY = ppData[i][1] - (cLData[i][v][1] * lockCount)
                        moveZ = ppData[i][2] - (cLData[i][v][2] * lockCount)
                        mc.move(moveX, moveY, moveZ, vLData[i][v], absolute=1, ws=1)

            viewPortCount = storeCount
        else:
            viewPortCount = 0.1

    else:
        if modifiers == 13:
            lockCount = lockCount - 0.1 if screenX > vpX else lockCount + 0.1
        else:
            lockCount = lockCount - 5 if screenX > vpX else lockCount + 5

        screenX = vpX

        if lockCount > 0:
            if modifiers == 1:
                lockCountA = lockCount
                lockCountB = storeUniBevelCountB

                for i in range(len(ppData)):
                    fraction = cumulative_fractions[i] if i < len(cumulative_fractions) else 1.0
                    factor = lockCountB + ((lockCountA - lockCountB) * fraction)

                    for v in range(len(vLData[i])):
                        moveX = ppData[i][0] - (cLData[i][v][0] * factor)
                        moveY = ppData[i][1] - (cLData[i][v][1] * factor)
                        moveZ = ppData[i][2] - (cLData[i][v][2] * factor)
                        mc.move(moveX, moveY, moveZ, vLData[i][v], absolute=1, ws=1)

                storeUniBevelCountA = lockCount

            elif modifiers == 4:
                lockCountA = storeUniBevelCountA
                lockCountB = lockCount

                for i in range(len(ppData)):
                    fraction = cumulative_fractions[i] if i < len(cumulative_fractions) else 1.0
                    factor = lockCountB + ((lockCountA - lockCountB) * fraction)

                    for v in range(len(vLData[i])):
                        moveX = ppData[i][0] - (cLData[i][v][0] * factor)
                        moveY = ppData[i][1] - (cLData[i][v][1] * factor)
                        moveZ = ppData[i][2] - (cLData[i][v][2] * factor)
                        mc.move(moveX, moveY, moveZ, vLData[i][v], absolute=1, ws=1)

                storeUniBevelCountB = lockCount

            else:
                storeUniBevelCountA = lockCount
                storeUniBevelCountB = lockCount

                for i in range(len(ppData)):
                    for v in range(len(vLData[i])):
                        moveX = ppData[i][0] - (cLData[i][v][0] * lockCount)
                        moveY = ppData[i][1] - (cLData[i][v][1] * lockCount)
                        moveZ = ppData[i][2] - (cLData[i][v][2] * lockCount)
                        mc.move(moveX, moveY, moveZ, vLData[i][v], absolute=1, ws=1)

            viewPortCount = lockCount
        else:
            viewPortCount = 0.1

    mc.refresh(f=True)


def unBevelEdgeLoop(edgelist):
    edgelist = mc.ls(_flatten_component_list(edgelist), fl=True)

    getCircleState, listVtx = vtxLoopOrderCheck(edgelist)

    if len(listVtx) < 3:
        raise RuntimeError("Pas assez de vertices pour unbevel edge.")

    checkA = angleBetweenThreeP(listVtx[1], listVtx[0], listVtx[-1])
    angleA = math.degrees(checkA)

    checkB = angleBetweenThreeP(listVtx[-2], listVtx[-1], listVtx[0])
    angleB = math.degrees(checkB)

    angleC = 180 - angleA - angleB

    if abs(math.sin(math.radians(angleC))) < 1e-8:
        raise RuntimeError("Angle invalide pour unbevel edge.")

    distanceC = distanceBetween(listVtx[0], listVtx[-1])
    distanceB = distanceC / math.sin(math.radians(angleC)) * math.sin(math.radians(angleB))

    oldDistB = distanceBetween(listVtx[0], listVtx[1])

    if oldDistB < 1e-8:
        raise RuntimeError("Distance invalide pour unbevel edge.")

    scalarB = distanceB / oldDistB

    pA = mc.pointPosition(listVtx[0], w=1)
    pB = mc.pointPosition(listVtx[1], w=1)

    newP = [0, 0, 0]
    newP[0] = ((pB[0] - pA[0]) * scalarB) + pA[0]
    newP[1] = ((pB[1] - pA[1]) * scalarB) + pA[1]
    newP[2] = ((pB[2] - pA[2]) * scalarB) + pA[2]

    listVtx = listVtx[1:-1]

    storeDist = []

    for l in listVtx:
        sotreXYZ = [0, 0, 0]
        p = mc.xform(l, q=True, t=True, ws=True)

        sotreXYZ[0] = (newP[0] - p[0]) / 100
        sotreXYZ[1] = (newP[1] - p[1]) / 100
        sotreXYZ[2] = (newP[2] - p[2]) / 100

        storeDist.append(sotreXYZ)

    return newP, listVtx, storeDist


def distanceBetween(p1, p2):
    pA = mc.pointPosition(p1, w=1)
    pB = mc.pointPosition(p2, w=1)

    return math.sqrt(
        ((pA[0] - pB[0]) ** 2) +
        ((pA[1] - pB[1]) ** 2) +
        ((pA[2] - pB[2]) ** 2)
    )


def angleBetweenThreeP(pA, pB, pC):
    a = mc.pointPosition(pA, w=1)
    b = mc.pointPosition(pB, w=1)
    c = mc.pointPosition(pC, w=1)

    ba = [aa - bb for aa, bb in zip(a, b)]
    bc = [cc - bb for cc, bb in zip(c, b)]

    nba = math.sqrt(sum((x ** 2.0 for x in ba)))
    nbc = math.sqrt(sum((x ** 2.0 for x in bc)))

    if nba < 1e-8 or nbc < 1e-8:
        return 0.0

    ba = [x / nba for x in ba]
    bc = [x / nbc for x in bc]

    scalar = sum((aa * bb for aa, bb in zip(ba, bc)))
    scalar = max(-1.0, min(1.0, scalar))

    return math.acos(scalar)


def vtxLoopOrderCheck(edgelist):
    selEdges = mc.ls(_flatten_component_list(edgelist), fl=True)

    shapeNode = mc.listRelatives(selEdges[0], fullPath=True, parent=True)
    transformNode = mc.listRelatives(shapeNode[0], fullPath=True, parent=True)

    edgeNumberList = []

    for a in selEdges:
        checkNumber = a.split('.')[1].split('\n')[0].split(' ')

        for c in checkNumber:
            findNumber = ''.join([n for n in c.split('|')[-1] if n.isdigit()])
            if findNumber:
                edgeNumberList.append(findNumber)

    getNumber = []

    for s in selEdges:
        evlist = mc.polyInfo(s, ev=True)
        checkNumber = evlist[0].split(':')[1].split('\n')[0].split(' ')

        for c in checkNumber:
            findNumber = ''.join([n for n in c.split('|')[-1] if n.isdigit()])
            if findNumber:
                getNumber.append(findNumber)

    dup = set([x for x in getNumber if getNumber.count(x) > 1])
    getHeadTail = list(set(getNumber) - dup)

    checkCircleState = 0

    if not getHeadTail:
        checkCircleState = 1
        getHeadTail.append(getNumber[0])

    vftOrder = [getHeadTail[0]]
    count = 0

    while len(dup) > 0 and count < 1000:
        checkVtx = transformNode[0] + '.vtx[' + vftOrder[-1] + ']'
        velist = mc.polyInfo(checkVtx, ve=True)

        getNumber = []
        checkNumber = velist[0].split(':')[1].split('\n')[0].split(' ')

        for c in checkNumber:
            findNumber = ''.join([n for n in c.split('|')[-1] if n.isdigit()])
            if findNumber:
                getNumber.append(findNumber)

        findNextEdge = None

        for g in getNumber:
            if g in edgeNumberList:
                findNextEdge = g
                break

        if findNextEdge is None:
            break

        edgeNumberList.remove(findNextEdge)

        checkEdge = transformNode[0] + '.e[' + findNextEdge + ']'
        findVtx = mc.polyInfo(checkEdge, ev=True)

        getNumber = []
        checkNumber = findVtx[0].split(':')[1].split('\n')[0].split(' ')

        for c in checkNumber:
            findNumber = ''.join([n for n in c.split('|')[-1] if n.isdigit()])
            if findNumber:
                getNumber.append(findNumber)

        gotNextVtx = None

        for g in getNumber:
            if g in dup:
                gotNextVtx = g
                break

        if gotNextVtx is None:
            break

        dup.remove(gotNextVtx)
        vftOrder.append(gotNextVtx)

        count += 1

    if checkCircleState == 0 and len(getHeadTail) > 1:
        vftOrder.append(getHeadTail[1])
    elif len(vftOrder) > 1 and vftOrder[0] == vftOrder[1]:
        vftOrder = vftOrder[1:]
    elif len(vftOrder) > 1 and vftOrder[0] == vftOrder[-1]:
        vftOrder = vftOrder[0:-1]

    finalList = []

    for v in vftOrder:
        finalList.append(transformNode[0] + '.vtx[' + v + ']')

    return checkCircleState, finalList


def getEdgeRingGroup():
    selEdges = mc.ls(sl=1, fl=1)

    tVer = mc.ls(mc.polyListComponentConversion(selEdges, tv=True), fl=True)
    tFac = mc.ls(mc.polyListComponentConversion(tVer, tf=True, internal=1), fl=True)
    tEdg = mc.ls(mc.polyListComponentConversion(tFac, te=True, internal=1), fl=True)

    findLoop = list(set(tEdg) - set(selEdges))

    if findLoop:
        oneLoop = mc.polySelectSp(findLoop[0], q=1, loop=1)
    else:
        oneLoop = selEdges

    oneLoop = mc.ls(oneLoop, fl=1)

    getCircleState, getVOrder = vtxLoopOrderCheck(oneLoop)

    trans = selEdges[0].split(".")[0]

    e2vInfos = mc.polyInfo(selEdges, ev=True)
    e2vDict = {}
    fEdges = []

    for info in e2vInfos:
        evList = [int(i) for i in re.findall('\\d+', info)]
        if len(evList) >= 3:
            e2vDict[evList[0]] = evList[1:]

    while True:
        try:
            startEdge, startVtxs = e2vDict.popitem()
        except:
            break

        edgesGrp = [startEdge]
        num = 0

        for vtx in startVtxs:
            curVtx = vtx

            while True:
                nextEdges = []

                for k in list(e2vDict.keys()):
                    if curVtx in e2vDict[k]:
                        nextEdges.append(k)

                if nextEdges:
                    if len(nextEdges) == 1:
                        if num == 0:
                            edgesGrp.append(nextEdges[0])
                        else:
                            edgesGrp.insert(0, nextEdges[0])

                        nextVtxs = e2vDict[nextEdges[0]]
                        curVtx = [vtx for vtx in nextVtxs if vtx != curVtx][0]
                        e2vDict.pop(nextEdges[0])
                    else:
                        break
                else:
                    break

            num += 1

        fEdges.append(edgesGrp)

    retEdges = []

    for f in fEdges:
        collectList = []

        for x in f:
            collectList.append(trans + ".e[" + str(x) + "]")

        retEdges.append(collectList)

    newOrderList = []

    for g in getVOrder:
        for e in retEdges:
            tVV = mc.ls(mc.polyListComponentConversion(e, tv=True), fl=True, l=1)

            if g in tVV:
                newOrderList.append(e)

    if not newOrderList:
        newOrderList = retEdges

    return newOrderList


def get_vertex_position(vertex_name):
    sel_list = om2.MSelectionList()
    sel_list.add(vertex_name)

    dag_path, component = sel_list.getComponent(0)
    vtx_iter = om2.MItMeshVertex(dag_path, component)

    return vtx_iter.position(om2.MSpace.kWorld)


def calculate_edge_distances(vertex_list):
    distances = []

    for i in range(len(vertex_list) - 1):
        vtx1 = get_vertex_position(vertex_list[i])
        vtx2 = get_vertex_position(vertex_list[i + 1])
        distances.append((vtx2 - vtx1).length())

    return distances


# =========================================================
# AUTO DETECT
# =========================================================

def smart_unbevel():
    sel = cmds.ls(sl=True, fl=True)

    if not sel:
        cmds.warning("Sélectionne des vertices ou des edges.")
        return

    vtx_sel = cmds.filterExpand(sel, sm=31) or []
    edge_sel = cmds.filterExpand(sel, sm=32) or []

    if vtx_sel:
        cmds.select(cmds.ls(vtx_sel, fl=True), r=True)
        print("Mode détecté : vertices.")
        unbevel_selected_vertices()
        return

    if edge_sel:
        cmds.select(cmds.ls(edge_sel, fl=True), r=True)
        print("Mode détecté : edges.")
        unBevel()
        return

    cmds.warning("Sélection invalide : sélectionne des vertices ou des edges.")


smart_unbevel()
