@echo off
setlocal
rem Offline tests only: no SDK, DDS runtime, or hardware startup.
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 exit /b 1
set "HAL_ROOT=%~dp0.."
set "TEST_BUILD=%HAL_ROOT%\build\motion-tests"
if not exist "%TEST_BUILD%" mkdir "%TEST_BUILD%"
pushd "%TEST_BUILD%"
set FLAGS=/nologo /utf-8 /std:c++20 /EHsc /MD /O2 /I "%HAL_ROOT%\include"
for %%S in (LTDMCDriver MotionControlThread MotionExecutor NativeTeleopController Omega7Driver JodellGripperDriver ForceControlRuntime ForceSafetyLatch ForceComplianceController HkvlForceDriver HkvlForceProtocol HalJson HalCommandDispatcher TeleopHardwareTargetExecutor) do (
  cl %FLAGS% /c "%HAL_ROOT%\src\%%S.cpp" /Fo"%%S.obj" || goto :failed
)
set OBJECTS=LTDMCDriver.obj MotionControlThread.obj MotionExecutor.obj NativeTeleopController.obj Omega7Driver.obj JodellGripperDriver.obj ForceControlRuntime.obj ForceSafetyLatch.obj ForceComplianceController.obj HkvlForceDriver.obj HkvlForceProtocol.obj HalJson.obj HalCommandDispatcher.obj TeleopHardwareTargetExecutor.obj
set TEST_RESULT=0
for %%T in (MotionExecutorTests EmergencyStopTests ControlLeaseTests ThreadStabilityTests WorkerResilienceTests ForceTareRuntimeTests ForceCoreTests StateSemanticsTests) do (
  cl %FLAGS% "%HAL_ROOT%\tests\%%T.cpp" %OBJECTS% /Fe"%%T.exe" || goto :failed
  call :run_test %%T
  if errorlevel 1 set TEST_RESULT=1
)
rem 此测试注入 SDK 函数指针，不加载 vendor DLL 或连接设备。
cl %FLAGS% "%HAL_ROOT%\tests\HardwareHomingTests.cpp" /Fe"HardwareHomingTests.exe" || goto :failed
call :run_test HardwareHomingTests
if errorlevel 1 set TEST_RESULT=1
cl %FLAGS% "%HAL_ROOT%\tests\HardwareReferenceReturnTests.cpp" /Fe"HardwareReferenceReturnTests.exe" || goto :failed
call :run_test HardwareReferenceReturnTests
if errorlevel 1 set TEST_RESULT=1
popd
exit /b %TEST_RESULT%
:failed
popd
exit /b 1

:run_test
pushd "%HAL_ROOT%\build"
"%TEST_BUILD%\%~1.exe"
set "CASE_RESULT=%errorlevel%"
popd
if not "%CASE_RESULT%"=="0" (
  echo [FAIL] %~1 exit code %CASE_RESULT%
  exit /b 1
)
echo [PASS] %~1
exit /b 0
