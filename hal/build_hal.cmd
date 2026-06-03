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

echo Compiling JodellGripperDriver.cpp ...
cl %CXX_FLAGS% /c "%SRC%\JodellGripperDriver.cpp" /Fo"JodellGripperDriver.next.obj" || goto :err

echo Compiling MotionControlThread.cpp ...
cl %CXX_FLAGS% /c "%SRC%\MotionControlThread.cpp" /Fo"MotionControlThread.next.obj" || goto :err

echo Compiling NativeTeleopController.cpp ...
cl %CXX_FLAGS% /c "%SRC%\NativeTeleopController.cpp" /Fo"NativeTeleopController.next.obj" || goto :err

echo Compiling Omega7Driver.cpp ...
cl %CXX_FLAGS% /c "%SRC%\Omega7Driver.cpp" /Fo"Omega7Driver.next.obj" || goto :err

echo Compiling HalServer.cpp ...
cl %CXX_FLAGS% /c "%SRC%\HalServer.cpp" /Fo"HalServer.next.obj" || goto :err

echo Compiling JodellGripperWorker.cpp ...
cl %CXX_FLAGS% /c "%SRC%\JodellGripperWorker.cpp" /Fo"JodellGripperWorker.next.obj" || goto :err

echo Linking HalServer.next.exe ...
link /nologo /OUT:"HalServer.next.exe" ^
  HalServer.next.obj LTDMCDriver.next.obj JodellGripperDriver.next.obj MotionControlThread.next.obj NativeTeleopController.next.obj Omega7Driver.next.obj ^
  ws2_32.lib ^
  || goto :err

echo Linking JodellGripperWorker.next.exe ...
link /nologo /OUT:"JodellGripperWorker.next.exe" ^
  JodellGripperWorker.next.obj JodellGripperDriver.next.obj ^
  || goto :err

for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set BUILD_STAMP=%%I
if exist "HalServer.exe" copy /Y "HalServer.exe" "HalServer.backup-%BUILD_STAMP%.exe" >nul 2>nul
if errorlevel 1 (
  echo Build succeeded: %BUILD%\HalServer.next.exe and %BUILD%\JodellGripperWorker.next.exe
  echo Deploy skipped: %BUILD%\HalServer.exe is in use. Stop HalServer.exe and copy both .next.exe files over the matching .exe names.
  popd
  exit /b 0
)
copy /Y "HalServer.next.exe" "HalServer.exe" >nul 2>nul
if errorlevel 1 (
  echo Build succeeded: %BUILD%\HalServer.next.exe and %BUILD%\JodellGripperWorker.next.exe
  echo Deploy skipped: %BUILD%\HalServer.exe is in use. Stop HalServer.exe and copy both .next.exe files over the matching .exe names.
  popd
  exit /b 0
)
if exist "JodellGripperWorker.exe" copy /Y "JodellGripperWorker.exe" "JodellGripperWorker.backup-%BUILD_STAMP%.exe" >nul 2>nul
if errorlevel 1 (
  echo Build succeeded: %BUILD%\HalServer.next.exe and %BUILD%\JodellGripperWorker.next.exe
  echo Deploy skipped: %BUILD%\JodellGripperWorker.exe is in use. Stop the worker process and copy JodellGripperWorker.next.exe over it.
  popd
  exit /b 0
)
copy /Y "JodellGripperWorker.next.exe" "JodellGripperWorker.exe" >nul 2>nul
if errorlevel 1 (
  echo Build succeeded: %BUILD%\HalServer.exe and %BUILD%\JodellGripperWorker.next.exe
  echo Deploy skipped: %BUILD%\JodellGripperWorker.exe is in use. Stop the worker process and copy JodellGripperWorker.next.exe over it.
  popd
  exit /b 0
)

echo Build succeeded: %BUILD%\HalServer.exe and %BUILD%\JodellGripperWorker.exe
popd
exit /b 0

:err
echo Build FAILED
popd
exit /b 1
