@echo off
setlocal
cd /d "%~dp0" || exit /b 1
set "APP_PY=.venv\Scripts\python.exe"
set "PYTHON_VERSION=3.13.17"
set "PYTHON_HOME=%LOCALAPPDATA%\Programs\Python\Python313"
set "PYTHON_CHECK=import sys; assert sys.version_info >= (3, 11)"

if exist "%APP_PY%" (
    "%APP_PY%" -c "%PYTHON_CHECK%" >nul 2>&1
    if not errorlevel 1 goto dependencies
)
py -3 -c "%PYTHON_CHECK%" >nul 2>&1
if not errorlevel 1 (
    py -3 -m venv --clear .venv
    goto check_venv
)
where python.exe 2>nul | findstr /vi /c:"\Microsoft\WindowsApps\" >nul
if not errorlevel 1 (
    python.exe -c "%PYTHON_CHECK%" >nul 2>&1
    if not errorlevel 1 (
        python.exe -m venv --clear .venv
        goto check_venv
    )
)
if exist "%PYTHON_HOME%\python.exe" (
    "%PYTHON_HOME%\python.exe" -c "%PYTHON_CHECK%" >nul 2>&1
    if not errorlevel 1 goto create_venv
)

echo Dang tai va cai Python %PYTHON_VERSION% cho tai khoan Windows hien tai...
if not exist "data\setup" mkdir "data\setup"
set "PYTHON_SETUP_DIR=%CD%\data\setup"
powershell.exe -NoProfile -Command "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; $arch=$env:PROCESSOR_ARCHITEW6432; if (-not $arch) {$arch=$env:PROCESSOR_ARCHITECTURE}; if ($arch -notin @('AMD64','ARM64')) {throw 'Can Windows 64-bit.'}; $suffix=$arch.ToLowerInvariant(); $ver=$env:PYTHON_VERSION; $exe=Join-Path $env:PYTHON_SETUP_DIR ('python-'+[guid]::NewGuid()+'.exe'); $log=Join-Path $env:PYTHON_SETUP_DIR 'python-install.log'; try {[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri ('https://www.python.org/ftp/python/'+$ver+'/python-'+$ver+'-'+$suffix+'.exe') -OutFile $exe -TimeoutSec 180; $sig=Get-AuthenticodeSignature -FilePath $exe; if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'CN=Python Software Foundation(?:,|$)') {throw 'Chu ky bo cai Python khong hop le.'}; $argsList='/passive InstallAllUsers=0 Include_launcher=0 Include_test=0 Include_pip=1 PrependPath=0 TargetDir='+[char]34+$env:PYTHON_HOME+[char]34+' /log '+[char]34+$log+[char]34; $p=Start-Process -FilePath $exe -ArgumentList $argsList -Wait -PassThru; if ($p.ExitCode -notin @(0,3010)) {throw ('Bo cai Python bao loi '+$p.ExitCode+'. Xem '+$log)}} finally {Remove-Item -LiteralPath $exe -Force -ErrorAction SilentlyContinue}"
if errorlevel 1 goto error
:create_venv
"%PYTHON_HOME%\python.exe" -m venv --clear .venv
:check_venv
if errorlevel 1 goto error
:dependencies
if exist ".venv\requirements.installed" (
    fc /b requirements.txt ".venv\requirements.installed" >nul
    if not errorlevel 1 goto run
)
"%APP_PY%" -m pip install -r requirements.txt
if errorlevel 1 goto error
copy /y requirements.txt ".venv\requirements.installed" >nul
:run
"%APP_PY%" main.py
if errorlevel 1 goto error
exit /b 0
:error
echo Khong the khoi dong. Kiem tra mang va log trong data\setup neu co.
pause
exit /b 1
