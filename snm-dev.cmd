@echo off
setlocal

REM =========================================================
REM SNM Development Helper
REM Windows CMD -> WSL Ubuntu -> Docker Compose
REM Project path inside WSL:
REM   ~/projects/messenger
REM =========================================================

set WSL_DISTRO=Ubuntu
set PROJECT_DIR=~/projects/messenger

:MENU
cls
echo.
echo ========================================================
echo   SNM Development Control
echo ========================================================
echo.
echo   1. Start normal development stack
echo   2. Rebuild backend only
echo   3. Rebuild frontend only
echo   4. Rebuild everything
echo   5. Restart nginx only
echo   6. Show running containers
echo   7. Show backend logs
echo   8. Show nginx logs
echo   9. Stop everything
echo   0. Exit
echo.
set /p choice=Choose option: 

if "%choice%"=="1" goto START_NORMAL
if "%choice%"=="2" goto REBUILD_BACKEND
if "%choice%"=="3" goto REBUILD_FRONTEND
if "%choice%"=="4" goto REBUILD_ALL
if "%choice%"=="5" goto RESTART_NGINX
if "%choice%"=="6" goto PS
if "%choice%"=="7" goto WEB_LOGS
if "%choice%"=="8" goto NGINX_LOGS
if "%choice%"=="9" goto STOP_ALL
if "%choice%"=="0" goto END

goto MENU


:START_NORMAL
echo.
echo Starting SNM stack...
wsl -d %WSL_DISTRO% -- bash -lc "cd %PROJECT_DIR% && docker compose up -d"
pause
goto MENU


:REBUILD_BACKEND
echo.
echo Rebuilding backend services: web worker beat...
wsl -d %WSL_DISTRO% -- bash -lc "cd %PROJECT_DIR% && docker compose up -d --build web worker beat"
pause
goto MENU


:REBUILD_FRONTEND
echo.
echo Rebuilding frontend and nginx...
wsl -d %WSL_DISTRO% -- bash -lc "cd %PROJECT_DIR% && docker compose build --no-cache frontend && docker compose up -d frontend nginx"
pause
goto MENU


:REBUILD_ALL
echo.
echo Rebuilding full stack...
wsl -d %WSL_DISTRO% -- bash -lc "cd %PROJECT_DIR% && docker compose down && docker compose up -d --build"
pause
goto MENU


:RESTART_NGINX
echo.
echo Restarting nginx...
wsl -d %WSL_DISTRO% -- bash -lc "cd %PROJECT_DIR% && docker compose restart nginx"
pause
goto MENU


:PS
echo.
wsl -d %WSL_DISTRO% -- bash -lc "cd %PROJECT_DIR% && docker compose ps"
pause
goto MENU


:WEB_LOGS
echo.
echo Showing backend logs. Press CTRL+C to stop logs.
wsl -d %WSL_DISTRO% -- bash -lc "cd %PROJECT_DIR% && docker compose logs -f web"
pause
goto MENU


:NGINX_LOGS
echo.
echo Showing nginx logs. Press CTRL+C to stop logs.
wsl -d %WSL_DISTRO% -- bash -lc "cd %PROJECT_DIR% && docker compose logs -f nginx"
pause
goto MENU


:STOP_ALL
echo.
echo Stopping SNM stack...
wsl -d %WSL_DISTRO% -- bash -lc "cd %PROJECT_DIR% && docker compose down"
pause
goto MENU


:END
endlocal
exit /b