#!/usr/bin/env python3
"""Extract the supplied chassis CAD only; never import its separate Kinova arm."""
import hashlib
import argparse
import json
import math
from pathlib import Path
import struct
import xml.etree.ElementTree as ET
from pxr import Usd, UsdGeom

root=Path('/workspace')
parser=argparse.ArgumentParser()
parser.add_argument('--source',type=Path,default=root/'external/tidybot_isaac_local_2')
source=parser.parse_args().source.resolve()
if not source.is_relative_to(root):
    raise ValueError('Mount source beneath /workspace for reproducible source provenance')
if not (source/'payloads/base.usda').is_file():
    raise FileNotFoundError(f'USD source missing: {source}; restore the supplied source or pass --source')
stage=Usd.Stage.Open(str(source/'payloads/base.usda'))
assert stage and stage.GetMetadata('metersPerUnit')==1 and str(stage.GetMetadata('upAxis'))=='Z'
path='/tidybot/Geometry/world/joint_x_jointbody/joint_y_jointbody/joint_th_jointbody/base/Kinova/Kinova'
prim=stage.GetPrimAtPath(path)
assert prim and prim.IsA(UsdGeom.Mesh), 'Expected chassis mesh missing; review changed source'
mesh=UsdGeom.Mesh(prim)
transform=UsdGeom.XformCache().GetLocalToWorldTransform(prim)
points=[tuple(transform.Transform(p)) for p in mesh.GetPointsAttr().Get()]
assert points and all(math.isfinite(v) for p in points for v in p)
minimum=[min(p[i] for p in points) for i in range(3)]
maximum=[max(p[i] for p in points) for i in range(3)]
size=[maximum[i]-minimum[i] for i in range(3)]
assert 0.4<size[0]<0.8 and 0.4<size[1]<0.8 and 0.2<size[2]<0.5, size
offset=[-(minimum[i]+maximum[i])/2 for i in range(2)]+[-minimum[2]]
points=[tuple(p[i]+offset[i] for i in range(3)) for p in points]
indices=list(mesh.GetFaceVertexIndicesAttr().Get())
counts=list(mesh.GetFaceVertexCountsAttr().Get())
triangles=[];cursor=0
for count in counts:
    face=indices[cursor:cursor+count];cursor+=count
    for i in range(1,count-1):
        a,b,c=[points[j] for j in (face[0],face[i],face[i+1])]
        u=[b[j]-a[j] for j in range(3)];v=[c[j]-a[j] for j in range(3)]
        normal=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]]
        norm=math.sqrt(sum(x*x for x in normal))
        if norm>1e-14:triangles.append(([x/norm for x in normal],a,b,c))
package=root/'ros2_ws/src/retail_description'
(package/'meshes').mkdir(parents=True,exist_ok=True)
(package/'urdf').mkdir(exist_ok=True)
with (package/'meshes/tidybot_chassis.stl').open('wb') as f:
    f.write(b'TidyBot chassis from supplied USD; units metres'.ljust(80,b'\0'))
    f.write(struct.pack('<I',len(triangles)))
    for normal,a,b,c in triangles:f.write(struct.pack('<12fH',*normal,*a,*b,*c,0))
robot=ET.Element('robot',name='tidybot_platform')
base=ET.SubElement(robot,'link',name='base_link')
visual=ET.SubElement(base,'visual')
ET.SubElement(ET.SubElement(visual,'geometry'),'mesh',filename='package://retail_description/meshes/tidybot_chassis.stl')
ET.SubElement(ET.SubElement(visual,'material',name='tidybot_gray'),'color',rgba='0.35 0.37 0.4 1')
collision=ET.SubElement(base,'collision')
ET.SubElement(collision,'origin',xyz=f'0 0 {size[2]/2}')
ET.SubElement(ET.SubElement(collision,'geometry'),'box',size=' '.join(map(str,size)))
ET.indent(robot)
(package/'urdf/tidybot.urdf').write_text(ET.tostring(robot,encoding='unicode')+'\n')
report=dict(source=str((source/'payloads/base.usda').relative_to(root)),prim=path,
    units='metres',source_minimum=minimum,source_maximum=maximum,size=size,
    visual_alignment_translation=offset,triangles=len(triangles),
    note='Chassis only; Kinova articulation excluded. Alignment is a display baseline, not measured OpenArm mounting calibration.',
    files={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(source.rglob('*')) if p.is_file()})
(package/'source.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k!='files'},indent=2))
