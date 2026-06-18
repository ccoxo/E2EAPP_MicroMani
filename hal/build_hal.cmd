@echo off
setlocal
rem Enter the MSVC x64 environment before invoking cl/link directly.
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 (
  echo vcvars64 setup failed
  exit /b 1
)

rem Resolve paths from this script so it works from any current directory.
set REPO=%~dp0
set BUILD=%REPO%build
set SRC=%REPO%src
set INC=%REPO%include
set FASTDDS_ROOT=F:\opt\ros\jazzy
set LEISHINE_LIB=%REPO%vendor\leishine\lib\x64\LTDMC.lib
rem /utf-8 keeps C++ Chinese comments and strings parsed consistently by MSVC.
rem EPROSIMA_ALL_DYN_LINK imports Fast-DDS/Fast-CDR symbols from the ROS2 DLLs.
set CXX_FLAGS=/nologo /utf-8 /std:c++20 /EHsc /MD /O2 /DAPPSTATION_ENABLE_VENDOR_SDKS=1 /DAPPSTATION_ENABLE_DDS=1 /DEPROSIMA_ALL_DYN_LINK /D_WIN32_WINNT=0x0601 /I "%INC%" /I "%FASTDDS_ROOT%\include" /I "%FASTDDS_ROOT%\include\fastrtps" /I "%FASTDDS_ROOT%\include\fastcdr"

if not exist "%BUILD%" mkdir "%BUILD%"
pushd "%BUILD%"

rem Compile into *.next.obj first so failed builds do not overwrite the last usable objects.
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

echo Compiling HalJson.cpp ...
cl %CXX_FLAGS% /c "%SRC%\HalJson.cpp" /Fo"HalJson.next.obj" || goto :err

echo Compiling HalCommandDispatcher.cpp ...
cl %CXX_FLAGS% /c "%SRC%\HalCommandDispatcher.cpp" /Fo"HalCommandDispatcher.next.obj" || goto :err

echo Compiling HalDdsControlServer.cpp ...
cl %CXX_FLAGS% /c "%SRC%\HalDdsControlServer.cpp" /Fo"HalDdsControlServer.next.obj" || goto :err

echo Compiling HalHttpServer.cpp ...
cl %CXX_FLAGS% /c "%SRC%\HalHttpServer.cpp" /Fo"HalHttpServer.next.obj" || goto :err

echo Compiling TeleopLeaderPublisher.cpp ...
cl %CXX_FLAGS% /c "%SRC%\TeleopLeaderPublisher.cpp" /Fo"TeleopLeaderPublisher.next.obj" || goto :err

echo Compiling TeleopMappingNode.cpp ...
cl %CXX_FLAGS% /c "%SRC%\TeleopMappingNode.cpp" /Fo"TeleopMappingNode.next.obj" || goto :err

echo Compiling TeleopHardwareTargetExecutor.cpp ...
cl %CXX_FLAGS% /c "%SRC%\TeleopHardwareTargetExecutor.cpp" /Fo"TeleopHardwareTargetExecutor.next.obj" || goto :err

echo Compiling TeleopFollowerTargetSubscriber.cpp ...
cl %CXX_FLAGS% /c "%SRC%\TeleopFollowerTargetSubscriber.cpp" /Fo"TeleopFollowerTargetSubscriber.next.obj" || goto :err

echo Compiling HalServer.cpp ...
cl %CXX_FLAGS% /c "%SRC%\HalServer.cpp" /Fo"HalServer.next.obj" || goto :err

echo Compiling JodellGripperWorker.cpp ...
cl %CXX_FLAGS% /c "%SRC%\JodellGripperWorker.cpp" /Fo"JodellGripperWorker.next.obj" || goto :err

rem HalServer links motion, master-hand, gripper, the Winsock HTTP boundary, and optional Fast-DDS.
echo Linking HalServer.next.exe ...
link /nologo /OUT:"HalServer.next.exe" ^
  HalServer.next.obj HalJson.next.obj HalCommandDispatcher.next.obj HalDdsControlServer.next.obj HalHttpServer.next.obj TeleopLeaderPublisher.next.obj TeleopMappingNode.next.obj TeleopHardwareTargetExecutor.next.obj TeleopFollowerTargetSubscriber.next.obj LTDMCDriver.next.obj JodellGripperDriver.next.obj MotionControlThread.next.obj NativeTeleopController.next.obj Omega7Driver.next.obj ^
  /LIBPATH:"%FASTDDS_ROOT%\Lib" ws2_32.lib iphlpapi.lib "%FASTDDS_ROOT%\Lib\fastrtps-2.14.lib" "%FASTDDS_ROOT%\Lib\fastcdr-2.2.lib" "%FASTDDS_ROOT%\Lib\foonathan_memory-0.7.3.lib" ^
  || goto :err

rem The gripper worker is a small isolated process that only links the gripper driver.
echo Linking JodellGripperWorker.next.exe ...
link /nologo /OUT:"JodellGripperWorker.next.exe" ^
  JodellGripperWorker.next.obj JodellGripperDriver.next.obj ^
  || goto :err

rem Timestamp backups let a successful build avoid overwriting an executable currently in use.
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
  echo Build succeeded: %BUILD%\HalServer.exe and %BUILD%\JodellGripperWorker.next.exe
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
