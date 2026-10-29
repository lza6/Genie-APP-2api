@echo off
call "C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
cd /d D:\apk-analysis\blutter
python blutter.py "C:\Users\Administrator.DESKTOP-EGNE9ND\Desktop\逆向产物\Genie v7.9.2 (Premium).apk" D:\apk-analysis\genie792\blutter_out
echo EXITCODE=%ERRORLEVEL%
