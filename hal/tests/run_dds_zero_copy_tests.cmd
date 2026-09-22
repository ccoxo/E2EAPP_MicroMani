@echo off
setlocal
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 exit /b 1
set "HAL_ROOT=%~dp0.."
set "TEST_BUILD=%HAL_ROOT%\build\dds-zero-copy-tests"
set "DDS_ROOT=F:\opt\ros\jazzy"
set "PATH=%DDS_ROOT%\bin;%DDS_ROOT%\.pixi\envs\default\Library\bin;%PATH%"
if not exist "%TEST_BUILD%" mkdir "%TEST_BUILD%"
pushd "%TEST_BUILD%"
cl /nologo /utf-8 /std:c++20 /EHsc /MD /O2 /DEPROSIMA_ALL_DYN_LINK /I "%HAL_ROOT%\include" /I "%DDS_ROOT%\include\fastrtps" /I "%DDS_ROOT%\include\fastcdr" "%HAL_ROOT%\tests\TeleopDdsZeroCopyTests.cpp" /Fe"TeleopDdsZeroCopyTests.exe" /link /LIBPATH:"%DDS_ROOT%\Lib" fastrtps-2.14.lib fastcdr-2.2.lib foonathan_memory-0.7.3.lib || goto :failed
for %%m in (0 1 2 3) do (
  call :run_mode %%m
  if errorlevel 1 goto :failed
)
popd
exit /b 0
:failed
popd
exit /b 1

:run_mode
TeleopDdsZeroCopyTests.exe %1
if not "%errorlevel%"=="0" exit /b 1
exit /b 0
