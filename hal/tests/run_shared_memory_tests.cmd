@echo off
setlocal
rem Real DDS, isolated test domain, device SDKs deliberately disabled.
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 exit /b 1
set "REPO=%~dp0..\.."
set "TEST_BUILD=%REPO%\hal\build\shm-tests"
set "DDS_ROOT=F:\opt\ros\jazzy"
set "PATH=%DDS_ROOT%\bin;%DDS_ROOT%\.pixi\envs\default\Library\bin;%PATH%"
if not exist "%TEST_BUILD%" mkdir "%TEST_BUILD%"
pushd "%TEST_BUILD%"
set FLAGS=/nologo /utf-8 /std:c++20 /EHsc /MD /O2 /DEPROSIMA_ALL_DYN_LINK /I "%REPO%\hal\include" /I "%DDS_ROOT%\include\fastrtps" /I "%DDS_ROOT%\include\fastcdr"
set LIBS=/LIBPATH:"%DDS_ROOT%\Lib" fastrtps-2.14.lib fastcdr-2.2.lib foonathan_memory-0.7.3.lib
cl %FLAGS% /LD "%REPO%\backend\native\appstation_fastdds_transport.cpp" /Fe"appstation_fastdds_transport.dll" /link %LIBS% || goto :failed
for %%S in (LTDMCDriver MotionControlThread MotionExecutor NativeTeleopController Omega7Driver JodellGripperDriver ForceControlRuntime ForceSafetyLatch ForceComplianceController HkvlForceDriver HkvlForceProtocol HalJson HalCommandDispatcher HalDdsControlServer) do (
  cl %FLAGS% /c "%REPO%\hal\src\%%S.cpp" /Fo"%%S.obj" || goto :failed
)
cl %FLAGS% "%REPO%\hal\tests\ShmControlServerFixture.cpp" LTDMCDriver.obj MotionControlThread.obj MotionExecutor.obj NativeTeleopController.obj Omega7Driver.obj JodellGripperDriver.obj ForceControlRuntime.obj ForceSafetyLatch.obj ForceComplianceController.obj HkvlForceDriver.obj HkvlForceProtocol.obj HalJson.obj HalCommandDispatcher.obj HalDdsControlServer.obj /Fe"ShmControlServerFixture.exe" /link %LIBS% || goto :failed
popd
pushd "%REPO%"
backend\.venv\Scripts\python.exe hal\tests\verify_shared_memory.py
set "TEST_RESULT=%errorlevel%"
popd
exit /b %TEST_RESULT%
:failed
popd
exit /b 1
