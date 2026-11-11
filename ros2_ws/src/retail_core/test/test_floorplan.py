from pathlib import Path
import math
import pytest
import yaml
from retail_core.world import load_world,footprint_clear

MAPS=Path(__file__).resolve().parents[2]/'retail_bringup/maps'

def test_dimensioned_map_has_correct_scale_and_pixel_orientation():
    world=load_world(MAPS/'store_corridor.geometry.yaml')
    metadata=yaml.safe_load((MAPS/world['map_yaml']).read_text())
    magic,shape,maximum,data=(MAPS/metadata['image']).read_bytes().split(b'\n',3)
    width,height=map(int,shape.split())
    assert (magic,maximum,width,height)==(b'P5',b'255',134,90)
    assert metadata['resolution']==.05 and metadata['origin']==[-.05,-.05,0.0]
    def pixel(x,y):
        col=math.floor((x-metadata['origin'][0])/.05)
        row=math.floor((y-metadata['origin'][1])/.05)
        return data[(height-1-row)*width+col]
    assert pixel(.8,2.2)==254  # 1.6 m left passage
    assert pixel(5,.7)==254 and pixel(5,3.7)==254  # two 1.4 m passages
    assert pixel(5,2.2)==0  # central 5 x 1.6 m obstacle
    assert pixel(-.025,2.2)==0
    assert data.count(254)==132*88-100*32

def test_conservative_footprint_fits_passages_but_not_walls():
    w=load_world(MAPS/'store_corridor.geometry.yaml')
    for pose in [(.8,.7,0),(.8,2.2,math.pi/4),(5.6,3.7,0)]:
        assert footprint_clear(*pose,w['bounds'],w['obstacles'])
    for pose in [(5,2.2,0),(.8,.3,0),(1.3,2.2,0)]:
        assert not footprint_clear(*pose,w['bounds'],w['obstacles'])

@pytest.mark.parametrize('change',[{'initial_pose':[3,2.2,0]}, {'resolution':0}, {'bounds':[0,0,0,4.4]}, {'obstacles':[[0,9,0,1]]}])
def test_invalid_fake_world_is_rejected(tmp_path,change):
    data=yaml.safe_load((MAPS/'store_corridor.geometry.yaml').read_text());data.update(change)
    path=tmp_path/'world.yaml';path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError):load_world(path)
