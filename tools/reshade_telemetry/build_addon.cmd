@echo off
setlocal
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
if errorlevel 1 exit /b 1
cl /nologo /std:c++17 /O2 /EHsc /MD /W4 /LD ^
  /I"%~dp0vendor\reshade\include" ^
  "%~dp0mc3_telemetry_addon.cpp" ^
  /link /OUT:"%~dp0output\mc3_telemetry.addon64" Ws2_32.lib
exit /b %errorlevel%
