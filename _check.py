import shutil, os
path = r'c:\Users\a\Desktop\ai_pipeline_prototype\新方案_Qt项目副本\robot_modbus_lite\__pycache__'
if os.path.exists(path):
    shutil.rmtree(path)
    print('cleared')
else:
    print('not found')
