import maya.cmds as cmds
import maya.mel as mel

cmds.optionVar(iv=("inViewMessageEnable", 1))

def vp_popup(state):
    if state:
        msg = '<span style="color:white;">SURFACE SLIDE </span><span style="color:#4dff88;"><b>ON</b></span>'
    else:
        msg = '<span style="color:white;">SURFACE SLIDE </span><span style="color:#ff5555;"><b>OFF</b></span>'

    cmds.inViewMessage(
        amg=msg,
        pos="midCenter",
        fade=True,
        fadeStayTime=100,
        fadeOutTime=200,
        backColor=0x202020,
        alpha=0.45
    )

def toggle_surface_slide_move_tool():
    state = mel.eval('xformConstraint -q -type')

    if state != 'surface':
        mel.eval('xformConstraint -type surface')
        cmds.setToolTo('Move')
        cmds.softSelect(e=True, softSelectEnabled=False)
        vp_popup(True)
    else:
        mel.eval('xformConstraint -type none')
        cmds.setToolTo('Move')
        vp_popup(False)

toggle_surface_slide_move_tool()
