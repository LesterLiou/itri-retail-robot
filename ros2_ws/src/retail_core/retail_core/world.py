"""Validated shared 2D geometry for the isolated kinematic test world."""
import math
from pathlib import Path
import yaml


def load_world(path):
    data=yaml.safe_load(Path(path).read_text())
    def rectangle(value):
        if len(value)!=4 or not all(math.isfinite(float(v)) for v in value):raise ValueError('Finite rectangle required')
        x0,x1,y0,y1=map(float,value)
        if not x0<x1 or not y0<y1:raise ValueError('Rectangle has no area')
        return (x0,x1,y0,y1)
    bounds=rectangle(data['bounds']);obstacles=[rectangle(item) for item in data['obstacles']]
    x0,x1,y0,y1=bounds
    for a,b,c,d in obstacles:
        if not (x0<=a<b<=x1 and y0<=c<d<=y1):raise ValueError('Obstacle outside world')
    pose=tuple(map(float,data['initial_pose']))
    if len(pose)!=3 or not all(math.isfinite(v) for v in pose):raise ValueError('Finite initial pose required')
    if not (x0<pose[0]<x1 and y0<pose[1]<y1):raise ValueError('Initial pose outside world')
    if any(a<=pose[0]<=b and c<=pose[1]<=d for a,b,c,d in obstacles):raise ValueError('Initial pose in obstacle')
    resolution=float(data['resolution'])
    if not math.isfinite(resolution) or resolution<=0:raise ValueError('Positive resolution required')
    return dict(data,bounds=bounds,obstacles=obstacles,initial_pose=pose,resolution=resolution)


def footprint_clear(x,y,yaw,bounds,obstacles,hx=.4,hy=.45):
    """Conservative rectangle SAT check, matching the configured Nav2 footprint."""
    c,s=math.cos(yaw),math.sin(yaw)
    points=[(x+c*dx-s*dy,y+s*dx+c*dy) for dx,dy in [(-hx,-hy),(-hx,hy),(hx,hy),(hx,-hy)]]
    x0,x1,y0,y1=bounds
    if any(not(x0<px<x1 and y0<py<y1) for px,py in points):return False
    for a,b,d,e in obstacles:
        box=[(a,d),(a,e),(b,e),(b,d)];separate=False
        for ax,ay in [(1,0),(0,1),(c,s),(-s,c)]:
            p=[px*ax+py*ay for px,py in points];q=[px*ax+py*ay for px,py in box]
            if max(p)<min(q) or max(q)<min(p):separate=True;break
        if not separate:return False
    return True
