"""
Bool Cleaner Pro + Bevel Snap Weld
Red UI Redesign
"""

import maya.cmds as mc
import maya.mel as mel
import maya.OpenMayaUI as omui
import maya.OpenMaya as om
import maya.api.OpenMaya as om2
import math

try:
    from PySide2 import QtWidgets, QtGui, QtCore
    import shiboken2
except:
    try:
        from PySide6 import QtWidgets, QtGui, QtCore
        import shiboken6 as shiboken2
    except:
        QtWidgets = None
        shiboken2 = None


# ============================================================================
# GLOBALS
# ============================================================================

sourceListLib = []
targetListLib = []
boolCleanPointData = ''
boolBorderCVList = []
boolYellowList = []
boolRedList = []

lastMouseX = 0
currentThreshold = 0.0

bevelCleanerInstance = None


# ============================================================================
# STYLE
# ============================================================================

RED_STYLESHEET = """
QWidget {
    background-color: #1a1a1a;
    color: #c8c8c8;
    font-family: "Consolas", monospace;
    font-size: 10px;
}

/* ---- Section labels ---- */
QLabel#sectionLabel {
    color: #8a2424;
    font-size: 9px;
    font-weight: bold;
    letter-spacing: 2px;
    border-left: 2px solid #8a2424;
    padding-left: 4px;
}

/* ---- Generic button ---- */
QPushButton {
    background-color: #242424;
    color: #888888;
    border: 1px solid #383838;
    border-radius: 3px;
    padding: 4px 6px;
    font-family: "Consolas", monospace;
    font-size: 9px;
    letter-spacing: 1px;
}
QPushButton:hover {
    background-color: #303030;
    border: 1px solid #555555;
    color: #c8c8c8;
}
QPushButton:pressed {
    background-color: #383838;
}
QPushButton:disabled {
    background-color: #1e1e1e;
    color: #484848;
    border: 1px solid #2a2a2a;
}

/* ---- Red action button ---- */
QPushButton#redBtn {
    background-color: #341818;
    color: #e07070;
    border: 1px solid #5a2222;
}
QPushButton#redBtn:hover {
    background-color: #3e1a1a;
    border: 1px solid #c45050;
    color: #f09090;
}
QPushButton#redBtn:pressed {
    background-color: #4a2020;
}

/* ---- Green confirm button ---- */
QPushButton#greenBtn {
    background-color: #1a3028;
    color: #6ec492;
    border: 1px solid #254838;
}
QPushButton#greenBtn:hover {
    background-color: #1e3830;
    border: 1px solid #4a9e6a;
}
QPushButton#greenBtn:pressed {
    background-color: #223c30;
}

/* ---- Slider ---- */
QSlider::groove:horizontal {
    height: 3px;
    background: #141414;
    border-radius: 1px;
}
QSlider::handle:horizontal {
    background: #c45050;
    border: 2px solid #1a1a1a;
    width: 11px;
    margin: -4px 0;
    border-radius: 6px;
}
QSlider::handle:horizontal:hover {
    background: #e07070;
}
QSlider::sub-page:horizontal {
    background: #5a2222;
    border-radius: 1px;
}

/* ---- Float field ---- */
QDoubleSpinBox, QLineEdit {
    background-color: #341818;
    color: #c45050;
    border: 1px solid #5a2222;
    border-radius: 2px;
    padding: 1px 4px;
    font-family: "Consolas", monospace;
    font-size: 10px;
}

/* ---- CheckBox ---- */
QCheckBox {
    color: #777777;
    spacing: 5px;
    font-size: 9px;
}
QCheckBox::indicator {
    width: 12px;
    height: 12px;
    border-radius: 6px;
    background: #272727;
    border: 1px solid #444444;
}
QCheckBox::indicator:checked {
    background: #341818;
    border: 1px solid #c45050;
}
QCheckBox:disabled {
    color: #484848;
}

/* ---- Separators ---- */
QFrame[frameShape="4"],
QFrame[frameShape="5"] {
    color: #333333;
}

/* ---- Precision int slider ---- */
QSlider#precSlider::handle:horizontal {
    background: #8a2424;
    width: 9px;
    margin: -3px 0;
}
QSlider#precSlider::sub-page:horizontal {
    background: #5a1818;
}

/* ---- Status bar label ---- */
QLabel#statusLabel {
    color: #484848;
    font-size: 8px;
    letter-spacing: 1px;
}
QLabel#statusLabel[active="true"] {
    color: #c45050;
}
"""


def applyRedQtStyle(window_name):
    if QtWidgets is None or shiboken2 is None:
        return
    ptr = omui.MQtUtil.findWindow(window_name)
    if not ptr:
        return
    widget = shiboken2.wrapInstance(int(ptr), QtWidgets.QWidget)
    widget.setStyleSheet(RED_STYLESHEET)


# ============================================================================
# UTILS
# ============================================================================

def getThresholdValue():
    if mc.floatSliderGrp("boolCleanerValue", exists=True):
        return mc.floatSliderGrp("boolCleanerValue", q=True, value=True)
    return 0.0


def unifiedThresholdChanged(*args):
    global bevelCleanerInstance
    if bevelCleanerInstance and bevelCleanerInstance.is_valid:
        bevelCleanerInstance.apply_preview()
        return
    if sourceListLib and targetListLib:
        cleanBoolUpdate()


def getCurrentCam():
    view = omui.M3dView.active3dView()
    cam = om.MDagPath()
    view.getCamera(cam)
    camPath = cam.fullPathName()
    cameraTrans = mc.listRelatives(camPath, type='transform', p=True)
    return cameraTrans


# ============================================================================
# DISPLAY DOTS
# ============================================================================

def boolDispRedOff():
    cleanList = ['boolDispRedDo*', 'boolCamDistanc*', 'BoolRedDotLaye*']
    for l in cleanList:
        if mc.objExists(l):
            mc.delete(l)


def boolDispRedOn():
    global sourceListLib, targetListLib

    if len(sourceListLib) == 0 or len(targetListLib) == 0:
        return

    boolCleanSide = mc.checkBox('boolCleanSide', q=True, v=True) if mc.checkBox('boolCleanSide', exists=True) else 0
    toDOList = sourceListLib if boolCleanSide else targetListLib

    if not toDOList:
        return

    boolDispRedOff()

    if not mc.objExists('boolCleanShader'):
        shd = mc.shadingNode('surfaceShader', name='boolCleanShader', asShader=True)
        shdSG = mc.sets(name='boolCleanShaderSG', empty=True, renderable=True, noSurfaceShader=True)
        mc.connectAttr(shd + '.outColor', shdSG + '.surfaceShader')

    mc.setAttr('boolCleanShader.outColor', 1, 0, 0, type='double3')

    mc.polySphere(r=0.01, sx=12, sy=12, ch=1, n='boolDispRedDot')
    mc.setAttr("boolDispRedDotShape.overrideEnabled", 1)
    mc.setAttr("boolDispRedDotShape.overrideColor", 4)
    mc.setAttr("boolDispRedDot.hiddenInOutliner", 1)
    mc.sets('boolDispRedDot', e=True, forceElement='boolCleanShaderSG')

    newNode = mc.distanceDimension(sp=(1, 0, 0), ep=(-1, 0, 0))
    mc.rename(newNode, 'boolCamDistance')

    connected_node = mc.listConnections('boolCamDistance', source=True, destination=False)
    mc.connectAttr('boolDispRedDot.center', 'boolCamDistance.endPoint', f=True)

    camName = getCurrentCam()
    if camName:
        mc.connectAttr(camName[0] + '.center', 'boolCamDistance.startPoint', f=True)

    if connected_node:
        mc.delete(connected_node)

    mc.rename('boolCamDistanceNode')
    mc.setAttr("boolCamDistanceNode.hiddenInOutliner", 1)
    mc.setAttr("boolCamDistanceNode.visibility", 0)

    expression = """
    if (boolCamDistance.distance < 2){
        boolDispRedDot.scaleX = boolCamDistance.distance / 4;
        boolDispRedDot.scaleY = boolCamDistance.distance / 4;
        boolDispRedDot.scaleZ = boolCamDistance.distance / 4;
    }
    """
    mc.expression(s=expression, o='boolCamDistance', ae=True, uc='all')

    for i, data in enumerate(toDOList):
        if i == 0:
            mc.move(data[1][0], data[1][1], data[1][2], 'boolDispRedDot', a=True, ws=True)
        else:
            inst = mc.instance('boolDispRedDot')[0]
            mc.move(data[1][0], data[1][1], data[1][2], inst, a=True, ws=True)

    dots = mc.ls('boolDispRedDot*', fl=True, transforms=True)
    for d in dots[1:]:
        mc.connectAttr('boolDispRedDot.scale', d + '.scale', f=True)

    mc.createDisplayLayer(name='BoolRedDotLayer')
    mc.editDisplayLayerMembers('BoolRedDotLayer', dots)
    mc.setAttr('BoolRedDotLayer.displayType', 2)


def boolCleanToogleRdot():
    global boolYellowList, boolRedList

    if not mc.checkBox('boolCleanShowDot', exists=True):
        return

    checkState = mc.checkBox('boolCleanShowDot', q=True, value=True)

    if checkState:
        currentSel = mc.ls(sl=True)
        boolCV = mc.filterExpand(sm=31)
        boolFace = mc.filterExpand(sm=34)

        boolDispRedOn()

        if boolCV:
            meshName = boolCV[0].split('.')[0]
            mel.eval('doMenuComponentSelectionExt("' + meshName + '", "vertex", 0);')
        elif boolFace:
            meshName = boolFace[0].split('.')[0]
            mel.eval('doMenuComponentSelectionExt("' + meshName + '", "facet", 0);')
        else:
            mc.select(currentSel)
    else:
        boolDispRedOff()

    boolCleanSide = mc.checkBox('boolCleanSide', q=True, v=True) if mc.checkBox('boolCleanSide', exists=True) else 0
    mc.select(boolYellowList if boolCleanSide else boolRedList)


# ============================================================================
# EDGE / FACE SELECTION
# ============================================================================

def _get_edge_loops_from_selection(edges):
    if not edges:
        return []

    edges_flat = mc.ls(edges, fl=True)
    edges_remaining = set(edges_flat)
    loops = []

    while edges_remaining:
        start_edge = next(iter(edges_remaining))
        current_loop = set([start_edge])
        to_process = [start_edge]

        while to_process:
            edge = to_process.pop()
            verts = mc.polyListComponentConversion(edge, fromEdge=True, toVertex=True)
            verts = mc.ls(verts, fl=True)

            for vert in verts:
                connected_edges = mc.polyListComponentConversion(vert, fromVertex=True, toEdge=True)
                connected_edges = mc.ls(connected_edges, fl=True)

                for ce in connected_edges:
                    if ce in edges_remaining and ce not in current_loop:
                        current_loop.add(ce)
                        to_process.append(ce)

        loops.append(list(current_loop))
        edges_remaining -= current_loop

    return loops


def _on_faces_selected_callback():
    inner_faces = mc.ls(sl=True, fl=True)
    mc.SelectFacetMask()

    try:
        mc.polyUVSet(d=True, uvSet="BoolCleaner_UV")
    except:
        pass

    if inner_faces:
        mc.select(inner_faces)
        mc.evalDeferred(cleanBoolNow)


def selectFacesFromEdgeLoop():
    selected_edges = mc.filterExpand(sm=32)
    selected_faces = mc.filterExpand(sm=34)

    if selected_faces and not selected_edges:
        mc.select(selected_faces, r=True)
        cleanBoolNow()
        return

    if not selected_edges:
        mc.warning("Select edge loop(s) or faces first!")
        return

    mc.undoInfo(openChunk=True, infinity=True)

    all_edges = mc.ls(selected_edges, fl=True)
    sel_obj = mc.ls(all_edges, fl=True, o=True)

    if not sel_obj:
        mc.warning("Cannot determine object.")
        mc.undoInfo(closeChunk=True)
        return

    loops = _get_edge_loops_from_selection(all_edges)
    num_loops = len(loops)

    if num_loops == 0:
        mc.warning("No edge loop detected.")
        mc.undoInfo(closeChunk=True)
        return

    if num_loops > 2:
        mc.warning(str(num_loops) + " loops detected. Max 2.")
        mc.undoInfo(closeChunk=True)
        return

    mc.select(all_edges, r=True)
    mc.ConvertSelectionToFaces()
    adjacent_faces = mc.ls(sl=True, fl=True)

    if not adjacent_faces:
        mc.warning("Cannot convert to faces.")
        mc.undoInfo(closeChunk=True)
        return

    mc.polyProjection(
        sel_obj[0], ch=1, type="Planar", ibd=False, cm=True,
        uvSetName="BoolCleaner_UV", kir=True, md="c"
    )
    mc.polyUVSet(cuv=True, uvSet="BoolCleaner_UV")
    mc.select(all_edges, r=True)
    mc.polyMapCut()

    if num_loops == 1:
        mc.select(adjacent_faces[0], r=True)
        mc.SelectMeshUVShell()
    else:
        mc.select(loops[0], r=True)
        mc.ConvertSelectionToFaces()
        faces_loop1 = set(mc.ls(sl=True, fl=True))

        mc.select(loops[1], r=True)
        mc.ConvertSelectionToFaces()
        faces_loop2 = set(mc.ls(sl=True, fl=True))

        common_faces = faces_loop1 & faces_loop2

        if common_faces:
            mc.select(list(common_faces)[0], r=True)
            mc.SelectMeshUVShell()
        else:
            mc.select(list(faces_loop1)[0], r=True)
            mc.SelectMeshUVShell()

    mc.scriptJob(runOnce=True, e=["SelectionChanged", _on_faces_selected_callback])
    mc.undoInfo(closeChunk=True)


# ============================================================================
# BOOL CLEANER
# ============================================================================

def snap2Closest():
    shortest = 10000
    closestVtx = ""
    selectVtx = mc.filterExpand(sm=31)

    if selectVtx and len(selectVtx) == 1:
        mc.GrowPolygonSelectionRegion()
        grow = mc.ls(sl=True, fl=True)

        mc.polySelectConstraint(mode=3, type=0x8000, where=1)
        mc.polySelectConstraint(disable=True)

        mc.ConvertSelectionToVertices()
        mc.select(grow, add=True)
        mc.select(selectVtx, d=True)

        grow = mc.ls(sl=True, fl=True)
        p1 = mc.pointPosition(selectVtx[0], w=True)

        for vtx in grow:
            p2 = mc.pointPosition(vtx, w=True)
            dist = math.sqrt(sum([(a - b) ** 2 for a, b in zip(p1, p2)]))
            if dist < shortest:
                closestVtx = vtx
                shortest = dist

        if closestVtx:
            vertPos = mc.pointPosition(closestVtx, w=True)
            mc.move(vertPos[0], vertPos[1], vertPos[2], selectVtx[0])
            mc.polyMergeVertex(closestVtx, selectVtx, d=0.0001, am=True, ch=False)

    elif selectVtx and len(selectVtx) > 1:
        mc.MergeToCenter()


def boolCleanTriangle():
    boolCleanSide = mc.checkBox('boolCleanSide', q=True, v=True) if mc.checkBox('boolCleanSide', exists=True) else 0
    boolFaces = mc.filterExpand(sm=34)

    if not boolFaces:
        return

    mesh = boolFaces[0].split('.')[0]

    if mc.objExists('boolFacesSet'):
        mc.delete('boolFacesSet')

    mc.sets(name="boolFacesSet", text="boolFacesSet")

    if boolCleanSide == 0:
        mc.ConvertSelectionToEdgePerimeter()
        mc.ConvertSelectionToFaces()
        mc.select(boolFaces, d=True)
    else:
        mc.ConvertSelectionToVertexPerimeter()
        mc.ConvertSelectionToFaces()
        findFaces = mc.ls(sl=True, fl=True)
        otherSideFace = list(set(findFaces) - set(boolFaces))
        getFaces = list(set(findFaces) - set(otherSideFace))
        mc.select(getFaces)

    mc.polySelectConstraint(m=2, sz=3, type=0x0008)
    mc.polySelectConstraint(disable=True)
    mc.Triangulate()
    mc.delete(mesh, ch=True)

    mc.select('boolFacesSet')
    mc.delete('boolFacesSet')

    if mc.button('boolCleanTriangleButton', exists=True):
        mc.button('boolCleanTriangleButton', e=True, en=False)


def boolCleanRestore():
    global boolCleanPointData

    boolDispRedOff()

    if not boolCleanPointData:
        return

    meshName = boolCleanPointData.split('.')[0]

    if not mc.objExists(meshName):
        return

    dataList = boolCleanPointData.split('@')
    del dataList[-1]

    for r in dataList:
        rawData = r.split(',')
        mc.move(float(rawData[1]), float(rawData[2]), float(rawData[3]), rawData[0], a=True, ws=True)

    mel.eval('doMenuComponentSelectionExt("' + meshName + '", "facet", 0);')


def cleanBoolNow():
    global sourceListLib, targetListLib, boolCleanPointData
    global boolBorderCVList, boolYellowList, boolRedList
    global bevelCleanerInstance

    bevelCleanerInstance = None
    boolYellowList = []
    boolRedList = []
    sourceListLib = []
    targetListLib = []

    boolFaces = mc.filterExpand(sm=34)

    if not boolFaces:
        mc.warning("No faces selected!")
        return

    if mc.objExists('boolFacesSet'):
        mc.delete('boolFacesSet')

    mc.sets(name="boolFacesSet", text="boolFacesSet")

    mc.ConvertSelectionToContainedEdges()
    mc.ConvertSelectionToVertices()
    innerCVList = mc.ls(sl=True, fl=True)

    mc.select(boolFaces)
    mc.ConvertSelectionToVertexPerimeter()
    boolBorderCVList = mc.ls(sl=True, fl=True)

    unwantList = list(set(innerCVList) - set(boolBorderCVList))
    targetList = list(set(innerCVList) - set(unwantList))
    snapSource = list(set(boolBorderCVList) - set(targetList))

    boolYellowList = targetList
    boolRedList = snapSource

    for t in targetList:
        pp = mc.pointPosition(t, w=True)
        targetListLib.append([t, pp])

    for s in snapSource:
        pp = mc.pointPosition(s, w=True)
        sourceListLib.append([s, pp])

    for btn in ["boolCleanUndoButton", "boolCleanDoneButton"]:
        if mc.button(btn, exists=True):
            mc.button(btn, e=True, en=True)

    if mc.floatSliderGrp("boolCleanerValue", exists=True):
        mc.floatSliderGrp("boolCleanerValue", e=True, en=True)

    if mc.checkBox('boolCleanSide', exists=True):
        mc.checkBox('boolCleanSide', e=True, en=True)

    if mc.button('boolCleanTriangleButton', exists=True):
        mc.button('boolCleanTriangleButton', e=True, en=True)

    if boolBorderCVList:
        boolCleanPointData = ''
        for s in boolBorderCVList:
            xA, yA, zA = mc.pointPosition(s, w=True)
            boolCleanPointData += '%s,%.4f,%.4f,%.4f@' % (s, xA, yA, zA)

    boolCleanSide = mc.checkBox('boolCleanSide', q=True, v=True) if mc.checkBox('boolCleanSide', exists=True) else 0
    mc.select(boolYellowList if boolCleanSide else boolRedList)

    boolCleanToogleRdot()
    activateInteractiveTool()

    mc.inViewMessage(amg='<hl>Bool Clean active</hl> / Middle-click + Drag', pos='topCenter', fade=True, fst=1200)


def boolCleanDone():
    global boolBorderCVList, sourceListLib, targetListLib

    if boolBorderCVList:
        mc.polyMergeVertex(boolBorderCVList, d=0.001, am=False, ch=True)

    if mc.floatSliderGrp("boolCleanerValue", exists=True):
        mc.floatSliderGrp("boolCleanerValue", e=True, value=0)

    targetListLib = []
    sourceListLib = []

    for btn in ["boolCleanUndoButton", "boolCleanDoneButton", "boolCleanTriangleButton"]:
        if mc.button(btn, exists=True):
            mc.button(btn, e=True, en=False)

    if mc.floatSliderGrp("boolCleanerValue", exists=True):
        mc.floatSliderGrp("boolCleanerValue", e=True, en=True)

    if mc.checkBox('boolCleanSide', exists=True):
        mc.checkBox('boolCleanSide', e=True, en=False)

    if mc.objExists('boolFacesSet'):
        mc.select('boolFacesSet')
        mc.delete('boolFacesSet')

    boolDispRedOff()
    deactivateInteractiveTool()


def boolCleanRev():
    boolCleanRestore()
    cleanBoolNow()
    boolDispRedOff()
    boolCleanToogleRdot()

    if mc.floatSliderGrp("boolCleanerValue", exists=True):
        mc.floatSliderGrp("boolCleanerValue", e=True, value=0)


def boolCleanUndo():
    global sourceListLib, targetListLib

    if mc.floatSliderGrp("boolCleanerValue", exists=True):
        mc.floatSliderGrp("boolCleanerValue", e=True, value=0)

    boolCleanRestore()

    if mc.objExists('boolFacesSet'):
        mc.select('boolFacesSet')

    for btn in ["boolCleanUndoButton", "boolCleanDoneButton", "boolCleanTriangleButton"]:
        if mc.button(btn, exists=True):
            mc.button(btn, e=True, en=False)

    if mc.checkBox('boolCleanSide', exists=True):
        mc.checkBox('boolCleanSide', e=True, en=False)

    targetListLib = []
    sourceListLib = []

    if mc.objExists('boolFacesSet'):
        mc.delete('boolFacesSet')

    boolDispRedOff()
    deactivateInteractiveTool()


def cleanBoolUpdate(thresholdValue=None):
    global sourceListLib, targetListLib

    if thresholdValue is None:
        minDistance = getThresholdValue()
    else:
        minDistance = thresholdValue
        if mc.floatSliderGrp("boolCleanerValue", exists=True):
            mc.floatSliderGrp("boolCleanerValue", e=True, value=minDistance)

    boolCleanSide = mc.checkBox('boolCleanSide', q=True, v=True) if mc.checkBox('boolCleanSide', exists=True) else 0

    if boolCleanSide == 1:
        for t in targetListLib:
            smallestDist = minDistance
            storeFound = t[1]

            for s in sourceListLib:
                dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(s[1], t[1])))
                if dist < minDistance and dist < smallestDist:
                    smallestDist = dist
                    storeFound = s[1]

            mc.move(storeFound[0], storeFound[1], storeFound[2], t[0], ws=True)
    else:
        for s in sourceListLib:
            smallestDist = minDistance
            storeFound = s[1]

            for t in targetListLib:
                dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(s[1], t[1])))
                if dist < minDistance and dist < smallestDist:
                    smallestDist = dist
                    storeFound = t[1]

            mc.move(storeFound[0], storeFound[1], storeFound[2], s[0], ws=True)

    mc.refresh(f=True)


# ============================================================================
# BOOL INTERACTIVE TOOL
# ============================================================================

def createInteractiveContext():
    if mc.draggerContext('boolCleanerDraggerCtx', exists=True):
        mc.deleteUI('boolCleanerDraggerCtx')

    mc.draggerContext(
        'boolCleanerDraggerCtx',
        name='BoolCleaner',
        cursor='hand',
        pressCommand=onDragPress,
        dragCommand=onDragMove,
        releaseCommand=onDragRelease,
        space='screen',
        button=2
    )


def onDragPress():
    global lastMouseX, currentThreshold
    pressPosition = mc.draggerContext('boolCleanerDraggerCtx', q=True, anchorPoint=True)
    lastMouseX = pressPosition[0]
    currentThreshold = getThresholdValue()


def onDragMove():
    global lastMouseX, currentThreshold

    dragPosition = mc.draggerContext('boolCleanerDraggerCtx', q=True, dragPoint=True)
    deltaX = dragPosition[0] - lastMouseX

    precision = mc.floatField("boolFloatPrecision", q=True, value=True) if mc.floatField("boolFloatPrecision", exists=True) else 0.1
    sensitivity = precision / 100.0

    newThreshold = max(0, currentThreshold + deltaX * sensitivity)

    if mc.floatSliderGrp("boolCleanerValue", exists=True):
        maxVal = mc.floatSliderGrp("boolCleanerValue", q=True, maxValue=True)
        newThreshold = min(newThreshold, maxVal)

    cleanBoolUpdate(newThreshold)
    mc.inViewMessage(amg='<hl>%.4f</hl>' % newThreshold, pos='topCenter', fade=False)


def onDragRelease():
    global currentThreshold
    currentThreshold = getThresholdValue()


def activateInteractiveTool():
    createInteractiveContext()
    mc.setToolTo('boolCleanerDraggerCtx')


def deactivateInteractiveTool():
    mc.setToolTo('selectSuperContext')


# ============================================================================
# BEVEL SNAP WELD
# ============================================================================

class BevelSnapWeldCleaner:

    def __init__(self):
        self.is_previewing = False
        self.is_valid = False
        self.mesh_name = None
        self.snap_map = []
        self.dag_path = None
        self.component = None

        selected_faces = mc.filterExpand(sm=34)

        if not selected_faces:
            mc.warning("Select bevel faces first.")
            return

        sel = om2.MGlobal.getActiveSelectionList()

        if sel.isEmpty():
            mc.warning("Select bevel faces first.")
            return

        self.dag_path, self.component = sel.getComponent(0)
        self.mesh_name = self.dag_path.fullPathName()

        self.border_vertices = self.get_selection_border_vertices()
        self.snap_map = self.precompute_snap_map()

        if not self.snap_map:
            mc.warning("No valid bevel snap points found.")
            return

        self.is_valid = True

    def get_selection_border_vertices(self):
        face_it = om2.MItMeshPolygon(self.dag_path, self.component)
        edge_count = {}

        while not face_it.isDone():
            for edge_id in face_it.getEdges():
                edge_count[edge_id] = edge_count.get(edge_id, 0) + 1
            face_it.next()

        border_edges = [e_id for e_id, count in edge_count.items() if count == 1]
        border_vertices = set()
        edge_it = om2.MItMeshEdge(self.dag_path)

        for e_id in border_edges:
            edge_it.setIndex(e_id)
            border_vertices.add(edge_it.vertexId(0))
            border_vertices.add(edge_it.vertexId(1))

        return border_vertices

    def precompute_snap_map(self):
        vtx_it = om2.MItMeshVertex(self.dag_path)
        vtx_info = {}

        for v_id in self.border_vertices:
            vtx_it.setIndex(v_id)
            vtx_info[v_id] = {
                "pos": vtx_it.position(om2.MSpace.kWorld),
                "valence": len(vtx_it.getConnectedEdges()),
                "connected_vtx": vtx_it.getConnectedVertices()
            }

        mapping = []

        for v_id, info in vtx_info.items():
            if info["valence"] == 4:
                continue

            best_target = None
            min_dist = 999999.0

            for neighbor_id in info["connected_vtx"]:
                if neighbor_id not in self.border_vertices:
                    continue
                if vtx_info[neighbor_id]["valence"] == 4:
                    dist = info["pos"].distanceTo(vtx_info[neighbor_id]["pos"])
                    if dist < min_dist:
                        min_dist = dist
                        best_target = neighbor_id

            if best_target is not None:
                mapping.append({
                    "mobile_id": v_id,
                    "target_id": best_target,
                    "target_pos": vtx_info[best_target]["pos"],
                    "dist": min_dist
                })

        return mapping

    def apply_preview(self):
        if not self.is_valid:
            return

        if self.is_previewing:
            mc.undo()
            self.is_previewing = False

        threshold = getThresholdValue()

        if threshold <= 0.0001:
            return

        mc.undoInfo(openChunk=True)
        self.is_previewing = True

        for item in self.snap_map:
            if item["dist"] <= threshold:
                v_name = "{}.vtx[{}]".format(self.mesh_name, item["mobile_id"])
                p = item["target_pos"]
                mc.xform(v_name, ws=True, t=(p.x, p.y, p.z))

        mc.undoInfo(closeChunk=True)
        mc.refresh(f=True)

    def cancel(self):
        if self.is_previewing:
            mc.undo()
            self.is_previewing = False
        self.is_valid = False

    def validate(self):
        if not self.is_valid:
            mc.warning("No active Bevel Snap Weld.")
            return

        threshold = getThresholdValue()
        verts_to_merge = []

        self.is_previewing = False

        for item in self.snap_map:
            if item["dist"] <= threshold:
                verts_to_merge.append("{}.vtx[{}]".format(self.mesh_name, item["mobile_id"]))
                verts_to_merge.append("{}.vtx[{}]".format(self.mesh_name, item["target_id"]))

        if verts_to_merge:
            mc.undoInfo(openChunk=True)
            mc.polyMergeVertex(verts_to_merge, d=0.01, am=False, ch=False)
            mc.polySoftEdge(self.mesh_name, angle=30, ch=False)
            mc.undoInfo(closeChunk=True)
            mc.select(self.mesh_name)

        self.is_valid = False


def bevelPreview():
    global bevelCleanerInstance
    deactivateInteractiveTool()
    boolDispRedOff()
    bevelCleanerInstance = BevelSnapWeldCleaner()

    if bevelCleanerInstance and bevelCleanerInstance.is_valid:
        bevelCleanerInstance.apply_preview()
        mc.inViewMessage(amg='<hl>Bevel Preview active</hl>', pos='topCenter', fade=True, fst=1000)


def bevelWeld():
    global bevelCleanerInstance

    if not bevelCleanerInstance or not bevelCleanerInstance.is_valid:
        bevelPreview()

    if bevelCleanerInstance and bevelCleanerInstance.is_valid:
        bevelCleanerInstance.validate()
        bevelCleanerInstance = None
        mc.inViewMessage(amg='<hl>Bevel Weld done</hl>', pos='topCenter', fade=True, fst=1000)


def bevelCancel():
    global bevelCleanerInstance

    if bevelCleanerInstance:
        bevelCleanerInstance.cancel()
        bevelCleanerInstance = None
        mc.inViewMessage(amg='<hl>Bevel canceled</hl>', pos='topCenter', fade=True, fst=1000)


# ============================================================================
# PRECISION
# ============================================================================

def boolPrecisionSliderUpdate():
    getState = mc.intSlider("boolPrecisionSlider", query=True, value=True)

    precisionMap = {
        1: (0.001, 3, 5),
        2: (0.01, 2, 4),
        3: (0.1, 1, 3),
        4: (1.0, 0, 2),
        5: (10, 0, 1)
    }

    if getState in precisionMap:
        value, fieldPrec, sliderPrec = precisionMap[getState]
        mc.floatField("boolFloatPrecision", edit=True, value=value, precision=fieldPrec)
    else:
        value = 0.1
        sliderPrec = 3

    mc.floatSliderGrp(
        "boolCleanerValue",
        edit=True,
        precision=sliderPrec,
        value=0,
        sliderStep=value / 10000.0,
        fieldStep=value / 1000.0,
        maxValue=value
    )

    unifiedThresholdChanged()


# ============================================================================
# COLORS (Maya bgc tuples)
# ============================================================================

# Dark red action
C_RED    = [0.20, 0.09, 0.09]
# Dark green confirm
C_GREEN  = [0.10, 0.19, 0.16]
# Neutral disabled-ish
C_NEUT   = [0.14, 0.14, 0.14]
# Slightly brighter neutral
C_NEUT2  = [0.17, 0.17, 0.17]


# ============================================================================
# UI
# ============================================================================

def boolCleanerPro():
    windowName = "boolCleanerPro"

    if mc.window(windowName, exists=True):
        mc.deleteUI(windowName, window=True)

    if mc.objExists('boolFacesSet'):
        mc.delete('boolFacesSet')

    ui_w = 400
    ui_h = 200

    mc.window(
        windowName,
        title="Bool Cleaner Pro",
        rtf=False,
        w=ui_w,
        h=ui_h,
        sizeable=False
    )

    mc.columnLayout(adj=True, rs=0, cat=("both", 5))

    # BEVEL
    mc.separator(h=4, style='none')
    mc.text(label="  BEVEL", align='left', font='smallPlainLabelFont', h=13)
    mc.separator(h=1, style='in')

    mc.rowLayout(nc=3, adj=1, h=24, cat=[(1, 'both', 1), (2, 'both', 1), (3, 'both', 1)])
    mc.button(
        'bevelPreviewButton',
        label="BEVEL PREVIEW",
        c=lambda x: bevelPreview(),
        bgc=C_RED,
        h=22
    )
    mc.button(
        'bevelWeldButton',
        label="WELD",
        c=lambda x: bevelWeld(),
        bgc=C_GREEN,
        en=True,
        h=22,
        w=78
    )
    mc.button(
        'bevelCancelButton',
        label="CANCEL",
        c=lambda x: bevelCancel(),
        bgc=C_NEUT2,
        en=True,
        h=22,
        w=62
    )
    mc.setParent('..')

    # BOOLEAN
    mc.separator(h=4, style='none')
    mc.text(label="  BOOLEAN", align='left', font='smallPlainLabelFont', h=13)
    mc.separator(h=2, style='in')

    mc.rowLayout(nc=3, adj=1, h=24, cat=[(1, 'both', 1), (2, 'both', 1), (3, 'both', 1)])
    mc.button(
        "boolStartButton",
        label="SMART BOOLEAN",
        c=lambda x: selectFacesFromEdgeLoop(),
        bgc=C_RED,
        h=22
    )
    mc.button(
        "boolCleanDoneButton",
        label="OK BOOL",
        c=lambda x: boolCleanDone(),
        bgc=C_GREEN,
        en=False,
        h=22,
        w=78
    )
    mc.button(
        "boolCleanUndoButton",
        label="ESC",
        c=lambda x: boolCleanUndo(),
        bgc=C_NEUT2,
        en=False,
        h=22,
        w=62
    )
    mc.setParent('..')

    # THRESHOLD
    mc.separator(h=4, style='none')
    mc.text(label="  THRESHOLD", align='left', font='smallPlainLabelFont', h=13)
    mc.separator(h=10, style='in')

    mc.floatSliderGrp(
        "boolCleanerValue",
        cw3=(1, 55, 250),
        en=True,
        precision=3,
        sliderStep=0.00001,
        fieldStep=0.0001,
        minValue=0,
        maxValue=0.1,
        label="",
        field=True,
        h=25,
        dc=lambda x: unifiedThresholdChanged(),
        cc=lambda x: unifiedThresholdChanged()
    )

    mc.rowLayout(nc=3, adj=2, h=18, cat=[(1, 'both', 0), (2, 'both', 1), (3, 'both', 0)])
    mc.text(label="  Prec", w=40, font='smallPlainLabelFont')
    mc.intSlider(
        "boolPrecisionSlider",
        min=1,
        max=5,
        value=3,
        step=1,
        h=18,
        dc=lambda x: boolPrecisionSliderUpdate()
    )
    mc.floatField(
        "boolFloatPrecision",
        width=46,
        precision=1,
        value=0.1,
        editable=False,
        h=18
    )
    mc.setParent('..')

    # OPTIONS
    mc.separator(h=4, style='none')
    mc.separator(h=1, style='in')

    mc.rowLayout(nc=4, adj=1, h=22, cat=[(1, 'both', 2), (2, 'both', 2), (3, 'both', 2), (4, 'both', 2)])
    mc.checkBox(
        'boolCleanSide',
        label="Reverse",
        en=False,
        value=False,
        cc=lambda x: boolCleanRev(),
        h=20
    )
    mc.checkBox(
        'boolCleanShowDot',
        label="Dots",
        en=True,
        value=True,
        cc=lambda x: boolCleanToogleRdot(),
        h=20
    )
    mc.button(
        'boolCleanTriangleButton',
        label="Triangulate",
        c=lambda x: boolCleanTriangle(),
        bgc=C_NEUT2,
        en=False,
        h=20,
        w=82
    )
    mc.button(
        'snap2ClosestButton',
        label="Snap",
        c=lambda x: snap2Closest(),
        bgc=C_NEUT2,
        en=True,
        h=20,
        w=50
    )
    mc.setParent('..')

    mc.separator(h=3, style='none')

    mc.showWindow(windowName)

    # Fenêtre fixe et compacte : pas de resize bizarre / pas de gros vide
    mc.window(windowName, e=True, wh=(ui_w, ui_h), sizeable=False)

    applyRedQtStyle(windowName)
    boolDispRedOff()


boolCleanerPro()
