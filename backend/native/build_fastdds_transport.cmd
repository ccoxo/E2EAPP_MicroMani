@echo off
setlocal
rem Build the small C ABI DLL used by backend\hal_client\dds_runtime.py.
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 (
  echo vcvars64 setup failed
  exit /b 1
)

set NATIVE_DIR=%~dp0
set BUILD=%NATIVE_DIR%build
set FASTDDS_ROOT=F:\opt\ros\jazzy
set SRC=%NATIVE_DIR%appstation_fastdds_transport.cpp
set CXX_FLAGS=/nologo /utf-8 /std:c++20 /EHsc /MD /O2 /DEPROSIMA_ALL_DYN_LINK /D_WIN32_WINNT=0x0601 /I "%FASTDDS_ROOT%\include" /I "%FASTDDS_ROOT%\include\fastrtps" /I "%FASTDDS_ROOT%\include\fastcdr"
set TARGET_DLL=appstation_fastdds_transport.dll
set NEXT_DLL=appstation_fastdds_transport.next.dll

if not exist "%BUILD%" mkdir "%BUILD%"
pushd "%BUILD%"

echo Compiling appstation_fastdds_transport.cpp ...
cl %CXX_FLAGS% /c "%SRC%" /Fo"appstation_fastdds_transport.next.obj" || goto :err

echo Linking appstation_fastdds_transport.next.dll ...
link /nologo /DLL /OUT:"appstation_fastdds_transport.next.dll" /IMPLIB:"appstation_fastdds_transport.next.lib" ^
  appstation_fastdds_transport.next.obj ^
  /LIBPATH:"%FASTDDS_ROOT%\Lib" "%FASTDDS_ROOT%\Lib\fastrtps-2.14.lib" "%FASTDDS_ROOT%\Lib\fastcdr-2.2.lib" "%FASTDDS_ROOT%\Lib\foonathan_memory-0.7.3.lib" ws2_32.lib iphlpapi.lib ^
  || goto :err

for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set BUILD_STAMP=%%I
if exist "%TARGET_DLL%" (
  copy /Y "%TARGET_DLL%" "appstation_fastdds_transport.backup-%BUILD_STAMP%.dll" >nul 2>nul
  if errorlevel 1 goto :deploy_skipped
)
copy /Y "%NEXT_DLL%" "%TARGET_DLL%" >nul 2>nul
if errorlevel 1 goto :deploy_skipped

echo Build succeeded: %BUILD%\%TARGET_DLL%
popd
exit /b 0

:deploy_skipped
echo Build succeeded: %BUILD%\%NEXT_DLL%
echo Deploy skipped: %BUILD%\%TARGET_DLL% is in use. Stop backend and copy %NEXT_DLL% over %TARGET_DLL%.
popd
exit /b 0

:err
echo Build FAILED
popd
exit /b 1
