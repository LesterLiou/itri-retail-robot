#!/usr/bin/env python3
"""Reopen the ROS viewer using live MoveIt parameters; never restart controllers."""
import fcntl
import os
from pathlib import Path
import subprocess
import tempfile
import time
import yaml


def main():
    # Compose exec stays in the existing sim container and its ROS graph.
    lock_path=Path(tempfile.gettempdir())/f'retail-rviz-{os.getuid()}.lock'
    with lock_path.open('a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print('RViz launcher already active; see /tmp/retail-rviz.log',flush=True)
            return
        for process in Path('/proc').iterdir():
            if not process.name.isdigit():continue
            try:
                if (process/'comm').read_text().strip()=='rviz2':
                    print('RViz is already running on '+os.environ.get('DISPLAY',''),flush=True)
                    return
            except (OSError,PermissionError):pass
        if not os.environ.get('DISPLAY'):
            raise RuntimeError('DISPLAY missing; use make rviz from the local desktop terminal')
        subprocess.run(['xdpyinfo'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=5)
        # Use the running model, including camera profile and assembly offsets.
        deadline=time.monotonic()+30
        while True:
            try:
                dump=subprocess.run(['ros2','param','dump','/move_group'],capture_output=True,text=True,check=True,timeout=5)
                data=yaml.safe_load(dump.stdout)
                parameters=next(value['ros__parameters'] for value in data.values() if 'ros__parameters' in value)
                if parameters.get('robot_description') and parameters.get('robot_description_semantic'):break
            except (subprocess.SubprocessError,ValueError,AttributeError,TypeError,StopIteration):pass
            if time.monotonic()>=deadline:raise RuntimeError('MoveIt model unavailable after viewer startup wait; inspect ROS launch logs')
            time.sleep(.25)
        viewer={name:value for name,value in parameters.items()
                if name.startswith('robot_description') or name=='use_sim_time'}
        if not viewer.get('robot_description') or not viewer.get('robot_description_semantic'):
            raise RuntimeError('Running MoveIt model unavailable; start make up / make nav-vqa / make moveit first')
        from ament_index_python.packages import get_package_share_directory
        profile='retail.rviz' if os.environ.get('CONTROL_MODE')=='moveit' else 'navigation.rviz'
        config=Path(get_package_share_directory('retail_bringup'))/'rviz'/profile
        with tempfile.TemporaryDirectory(prefix='retail-viewer-') as directory:
            world_file=os.environ.get('SIM_WORLD_FILE','')
            if world_file and profile=='navigation.rviz':
                bounds=yaml.safe_load(Path(world_file).read_text())['bounds']
                settings=yaml.safe_load(config.read_text())
                current=settings['Visualization Manager']['Views']['Current']
                current['X']=(bounds[0]+bounds[1])/2;current['Y']=(bounds[2]+bounds[3])/2
                adjusted=Path(directory)/profile;adjusted.write_text(yaml.safe_dump(settings,sort_keys=False));config=adjusted
            parameter_file=Path(directory)/'parameters.yaml'
            parameter_file.write_text(yaml.safe_dump({'/rviz2':{'ros__parameters':viewer}}))
            print('Opening RViz on '+os.environ['DISPLAY']+'; log: /tmp/retail-rviz.log',flush=True)
            result=subprocess.run(['rviz2','-d',str(config),'--ros-args','--params-file',str(parameter_file)])
            if result.returncode:
                raise SystemExit(f'RViz exited with {result.returncode}; inspect /tmp/retail-rviz.log')


if __name__=='__main__':main()
