@echo off
setlocal
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 (
  echo vcvars64 setup failed
  exit /b 1
)

set REPO=%~dp0
set BUILD=%REPO%build
set SRC=%REPO%src
set INC=%REPO%include
set LEISHINE_LIB=%REPO%vendor\leishine\lib\x64\LTDMC.lib
set CXX_FLAGS=/nologo /utf-8 /std:c++20 /EHsc /O2 /DAPPSTATION_ENABLE_VENDOR_SDKS=1 /D_WIN32_WINNT=0x0601 /I "%INC%"

if not exist "%BUILD%" mkdir "%BUILD%"
pushd "%BUILD%"

echo Compiling LTDMCDriver.cpp ...
cl %CXX_FLAGS% /c "%SRC%\LTDMCDriver.cpp" /Fo"LTDMCDriver.next.obj" || goto :err

echo Compiling MotionControlThread.cpp ...
cl %CXX_FLAGS% /c "%SRC%\MotionControlThread.cpp" /Fo"MotionControlThread.next.obj" || goto :err

echo Compiling Omega7Driver.cpp ...
cl %CXX_FLAGS% /c "%SRC%\Omega7Driver.cpp" /Fo"Omega7Driver.next.obj" || goto :err

echo Compiling HalServer.cpp ...
cl %CXX_FLAGS% /c "%SRC%\HalServer.cpp" /Fo"HalServer.next.obj" || goto :err

echo Linking HalServer.next.exe ...
link /nologo /OUT:"HalServer.next.exe" ^
  HalServer.next.obj LTDMCDriver.next.obj MotionControlThread.next.obj Omega7Driver.next.obj ^
  ws2_32.lib ^
  || goto :err

for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set BUILD_STAMP=%%I
if exist "HalServer.exe" copy /Y "HalServer.exe" "HalServer.backup-%BUILD_STAMP%.exe" >nul || goto :err
copy /Y "HalServer.next.exe" "HalServer.exe" >nul || goto :err

echo Build succeeded: %BUILD%\HalServer.exe
popd
exit /b 0

:err
echo Build FAILED
popd
exit /b 1
