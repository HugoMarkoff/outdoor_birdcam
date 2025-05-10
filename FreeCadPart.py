import FreeCAD, Part
from FreeCAD import Base

# ─────────────────────────────────────────────────────────────────────────────
# PARAMETERS (mm)
W      = 50      # door width
H0     = 75      # door rectangle height
R      = 25      # door semicircle radius
Z      = 30      # extrusion thickness (front → back, Z-axis)

T      = 1.5     # wall / roof thickness  ← changed from 3 mm
TAB_W  = 20      # tab width  (X)
TAB_D  = 8       # tab depth  (–Y)
TAB_H  = 10      # tab height / extension (Z)  ← changed from 25 mm

HOLE_W = 16      # mortise width  (X)
HOLE_H = 22      # mortise height (Y)

EXTRA_Y = 3      # how much taller (in +Y) the plates must grow
FUDGE   = 1      # widens back-wall 1 mm each side to avoid micro-gap
# ─────────────────────────────────────────────────────────────────────────────

doc = FreeCAD.newDocument("DoorTab_DualMortise")

# 1) 2-D profile wires ---------------------------------------------------------
door_wire = Part.Wire([
    Part.LineSegment(Base.Vector(-W/2, 0, 0), Base.Vector( W/2, 0, 0)).toShape(),
    Part.LineSegment(Base.Vector( W/2, 0, 0), Base.Vector( W/2, H0, 0)).toShape(),
    Part.Arc       (Base.Vector(-W/2, H0, 0),
                    Base.Vector(   0, H0+R, 0),
                    Base.Vector( W/2, H0, 0)).toShape(),
    Part.LineSegment(Base.Vector(-W/2, H0, 0), Base.Vector(-W/2, 0, 0)).toShape()
])

tab_wire = Part.Wire([
    Part.LineSegment(Base.Vector(-TAB_W/2,  0, 0), Base.Vector( TAB_W/2,  0, 0)).toShape(),
    Part.LineSegment(Base.Vector( TAB_W/2,  0, 0), Base.Vector( TAB_W/2, -TAB_D, 0)).toShape(),
    Part.LineSegment(Base.Vector( TAB_W/2, -TAB_D, 0), Base.Vector(-TAB_W/2, -TAB_D, 0)).toShape(),
    Part.LineSegment(Base.Vector(-TAB_W/2, -TAB_D, 0), Base.Vector(-TAB_W/2,  0, 0)).toShape()
])

# 2) inside offsets (1.5 mm walls)
door_in = door_wire.makeOffset2D(-T)
tab_in  = tab_wire.makeOffset2D(-T)

# 3) outer shell ---------------------------------------------------------------
door_out = Part.Face(door_wire).extrude(Base.Vector(0, 0, Z))        # 0 → 30 Z
tab_out  = Part.Face(tab_wire ).extrude(Base.Vector(0, 0, TAB_H))    # 0 → 10 Z
tab_out.translate(Base.Vector(0, 0, Z - TAB_H))                      # 20 → 30 Z
outer = door_out.fuse(tab_out)

# 4) hollow cavity -------------------------------------------------------------
door_cut = Part.Face(door_in).extrude(Base.Vector(0, 0, Z - T))      # 0 → 28.5 Z
door_cut.translate(Base.Vector(0, 0, T))                             # 1.5 → 30 Z

tab_cut  = Part.Face(tab_in ).extrude(Base.Vector(0, 0, TAB_H - T))  # 0 → 8.5 Z
tab_cut.translate(Base.Vector(0, 0, T))                              # 1.5 Z
tab_cut.translate(Base.Vector(0, 0, Z - TAB_H))                      # 21.5 → 30 Z

inner = door_cut.fuse(tab_cut)
shell = outer.cut(inner)

# 5) mortises (door side + mirrored tab side) -------------------------------
for y0 in (0, -HOLE_H):
    shell = shell.cut(Part.makeBox(
        HOLE_W, HOLE_H, TAB_H,                       # depth 10 Z
        Base.Vector(-HOLE_W/2, y0, Z - TAB_H)        # starts Z = 20
    ))
final = shell

# 6) tab closures --------------------------------------------------------------
cw_w         = TAB_W - 2*T + 2*FUDGE          # inner span in X
inner_depth  = TAB_D - T                      # 6.5 mm (–6.5 Y → 0 Y)
new_depth    = inner_depth + EXTRA_Y          # 9.5 mm (–6.5 Y → +3 Y)

# back-wall (unchanged)
cross_wall = Part.makeBox(
    cw_w, T, TAB_H,
    Base.Vector(-cw_w/2, -TAB_D, Z - TAB_H)          # 20 → 30 Z
)

# bottom plate: lower face at Y = –8 mm, upper face now at +3 mm
bottom_plate = Part.makeBox(
    cw_w, new_depth, T,                              # Y-length 9.5 mm
    Base.Vector(-cw_w/2, -TAB_D + T, Z - TAB_H)      # 20 → 21.5 Z
)

# top plate: lower face at Y = –8 mm, upper face now at +3 mm
top_plate = Part.makeBox(
    cw_w, new_depth, T,
    Base.Vector(-cw_w/2, -TAB_D + T, Z - T)          # 28.5 → 30 Z
)

final = final.fuse(cross_wall).fuse(bottom_plate).fuse(top_plate)

# 7) show result ----------------------------------------------------------------
obj = doc.addObject("Part::Feature", "DoorTab_DualMortise")
obj.Shape = final
doc.recompute()
