#!/usr/bin/env python3
"""Dimensioned geometry -> clean occupancy PGM/YAML; no labels enter occupancy."""
import importlib.util
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('world',ROOT/'ros2_ws/src/retail_core/retail_core/world.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
folder=ROOT/'ros2_ws/src/retail_bringup/maps'
world=module.load_world(folder/'store_corridor.geometry.yaml')
x0,x1,y0,y1=world['bounds'];r=world['resolution'];pad=world['raster_padding_cells']
width=round((x1-x0)/r)+2*pad;height=round((y1-y0)/r)+2*pad
assert abs((width-2*pad)*r-(x1-x0))<1e-8
assert abs((height-2*pad)*r-(y1-y0))<1e-8
origin=(x0-pad*r,y0-pad*r);pixels=bytearray()
for row in reversed(range(height)):
    y=origin[1]+(row+.5)*r
    for col in range(width):
        x=origin[0]+(col+.5)*r
        occupied=not(x0<=x<x1 and y0<=y<y1) or any(a<=x<b and c<=y<d for a,b,c,d in world['obstacles'])
        pixels.append(0 if occupied else 254)
(folder/'store_corridor.pgm').write_bytes(f'P5\n{width} {height}\n255\n'.encode()+pixels)
(folder/'store_corridor.yaml').write_text(f'image: store_corridor.pgm\nmode: trinary\nresolution: {r}\norigin: [{origin[0]}, {origin[1]}, 0.0]\nnegate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.25\n')
assert module.footprint_clear(*world['initial_pose'],world['bounds'],world['obstacles'])
scale=100;left=70;top=65
def rect(a,b,c,d,color):
    return f'<rect x="{left+a*scale}" y="{top+(y1-d)*scale}" width="{(b-a)*scale}" height="{(d-c)*scale}" fill="{color}"/>'
svg=['<svg xmlns="http://www.w3.org/2000/svg" width="810" height="590" viewBox="0 0 810 590">',
 '<rect width="100%" height="100%" fill="#f8fafc"/>',
 '<g font-family="sans-serif" font-size="15" fill="#1e293b">',
 '<text x="70" y="25" font-size="20">Dimensioned floor plan / navigation draft</text>',
 f'<text x="320" y="50">6.60 m</text>',
 rect(x0,x1,y0,y1,'white'),
 f'<rect x="{left}" y="{top}" width="660" height="440" fill="none" stroke="#111" stroke-width="2"/>']
for a,b,c,d in world['obstacles']:svg.append(rect(a,b,c,d,'#111'))
svg.extend(['<text x="395" y="281" fill="white">5.00 x 1.60 m obstacle</text>',
 '<text x="320" y="130">1.40 m upper passage</text>',
 '<text x="320" y="455">1.40 m lower passage</text>',
 '<text x="85" y="275" transform="rotate(-90 85 275)">1.60 m left passage</text>',
 '<text x="32" y="290" transform="rotate(-90 32 290)">4.40 m</text>'])
for name,x,y in [('Start',.8,.7),('A',5.6,.7),('B',5.6,3.7),('C',.8,3.7)]:
    svg.append(f'<circle cx="{left+x*scale}" cy="{top+(y1-y)*scale}" r="7" fill="#0284c7"/><text x="{left+x*scale+10}" y="{top+(y1-y)*scale-8}">{name} ({x}, {y})</text>')
svg.extend(['<text x="70" y="540">Origin (0,0): inside bottom-left. All lengths are metres.</text>',
 '<text x="70" y="565">Draft from supplied dimensions; not surveyed or calibrated.</text></g></svg>'])
(folder/'store_corridor.svg').write_text('\n'.join(svg)+'\n')
print(json.dumps(dict(pixels=[width,height],resolution=r,origin=origin,interior_metres=[x1-x0,y1-y0],obstacle_metres=[5,1.6],initial_pose=world['initial_pose']),indent=2))
