@echo off
cd /d "c:\Users\a\Desktop\ai_pipeline_prototype\license-server"
if not exist .env copy .env.example .env
"C:\Users\a\AppData\Local\Programs\Python\Python310\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 2>&1
