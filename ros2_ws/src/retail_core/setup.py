from setuptools import setup
setup(name='retail_core', version='0.1.0', packages=['retail_core'],
      data_files=[('share/ament_index/resource_index/packages',['resource/retail_core']),('share/retail_core',['package.xml'])],
      install_requires=['setuptools'], zip_safe=True, maintainer='ITRI', maintainer_email='dev@example.invalid', description='Integration adapters', license='Apache-2.0',
      entry_points={'console_scripts':[
          'vqa_ui = retail_core.vqa_ui:main',
          'manual_control = retail_core.manual_control:main',
          'manual_watchdog = retail_core.manual_watchdog:main',
          'camera_source = retail_core.camera:main',
          'vqa_adapter = retail_core.vqa:main',
          'base_gate = retail_core.gate:main',
          'arm_gate = retail_core.arm_gate:main',
          'fake_base = retail_core.fake_base:main',
          'scene_manager = retail_core.scene:main',
          'view_planner = retail_core.views:main',
          'hand_adapter = retail_core.hands:main',
          'reconstruction_adapter = retail_core.reconstruction:main',
          'mission_manager = retail_core.mission:main',
          'demo_control = retail_core.demo_control:main',
      ]})

