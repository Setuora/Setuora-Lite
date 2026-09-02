@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PROJECT_ROOT=%%~fI"
set "DEPLOY_SCRIPT=%PROJECT_ROOT%\deploy.py"

if not exist "%DEPLOY_SCRIPT%" (
    echo Setuora deployment entry point was not found: "%DEPLOY_SCRIPT%"
    echo Run this script from a complete Setuora source checkout.
    endlocal & exit /b 1
)

powershell.exe -NoProfile -Command "$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent()); if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { exit 0 } else { exit 1 }" >nul 2>&1
if errorlevel 1 (
    echo Setuora update must be run with Administrator privileges.
    echo Open an elevated Command Prompt, or right-click scripts\update.bat and choose Run as administrator.
    endlocal & exit /b 1
)

call "%SCRIPT_DIR%find_python.bat"
if errorlevel 1 (
    echo Python 3.11 or newer was not found. Install it, then rerun this script.
    endlocal & exit /b 1
)

call :sync_source
if errorlevel 1 (
    echo Setuora source update failed; deploy.py was not run.
    endlocal & exit /b %ERRORLEVEL%
)

"%PYTHON_EXE%" %PYTHON_ARGS% "%DEPLOY_SCRIPT%" update
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" echo Setuora update failed with exit code %EXIT_CODE%.
endlocal & exit /b %EXIT_CODE%

:sync_source
where git.exe >nul 2>&1
if errorlevel 1 (
    echo Git for Windows was not found. Install Git, then rerun this script.
    exit /b 1
)
git.exe -C "%PROJECT_ROOT%" rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo This Setuora source checkout is not a Git worktree.
    exit /b 1
)
if not defined TEMP (
    echo Windows temporary storage is unavailable; could not check Git worktree status.
    exit /b 1
)
set "STATUS_FILE=%TEMP%\setuora-update-status-%RANDOM%-%RANDOM%.txt"
> "%STATUS_FILE%" git.exe -C "%PROJECT_ROOT%" status --porcelain --untracked-files=all
set "STATUS_EXIT=%ERRORLEVEL%"
if not "%STATUS_EXIT%"=="0" (
    del /q "%STATUS_FILE%" >nul 2>&1
    echo Could not determine Git worktree status.
    exit /b %STATUS_EXIT%
)
set "WORKTREE_DIRTY="
for /f "usebackq delims=" %%I in ("%STATUS_FILE%") do set "WORKTREE_DIRTY=1"
del /q "%STATUS_FILE%" >nul 2>&1
if defined WORKTREE_DIRTY (
    echo Source update requires a clean worktree, including no untracked files.
    echo Commit, stash, or remove those files, then rerun this script.
    exit /b 1
)
set "CURRENT_BRANCH="
for /f "delims=" %%I in ('git.exe -C "%PROJECT_ROOT%" branch --show-current') do set "CURRENT_BRANCH=%%I"
if not defined CURRENT_BRANCH (
    echo Source update requires a checked-out branch, not a detached HEAD.
    exit /b 1
)
echo Fetching "origin/%CURRENT_BRANCH%"...
git.exe -C "%PROJECT_ROOT%" fetch --quiet origin
if errorlevel 1 exit /b %ERRORLEVEL%
git.exe -C "%PROJECT_ROOT%" rev-parse --verify --quiet "refs/remotes/origin/%CURRENT_BRANCH%" >nul 2>&1
if errorlevel 1 (
    echo "origin/%CURRENT_BRANCH%" was not found. Check the remote and branch name.
    exit /b 1
)
git.exe -C "%PROJECT_ROOT%" merge-base --is-ancestor HEAD "origin/%CURRENT_BRANCH%" >nul 2>&1
if errorlevel 1 (
    echo Local changes are not a fast-forward of "origin/%CURRENT_BRANCH%"; refusing to merge or reset.
    exit /b 1
)
echo Fast-forwarding to "origin/%CURRENT_BRANCH%"...
git.exe -C "%PROJECT_ROOT%" merge --ff-only "origin/%CURRENT_BRANCH%"
exit /b %ERRORLEVEL%
