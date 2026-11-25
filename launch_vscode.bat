@echo off

REM Add EUD to PATH
set PATH=%EUD_INSTALL_PATH%;%PATH%

REM Update .vscode/tasks.json from chipsets under config/target
python openocd/scripts/utils/sync_chipset_tasks.py

REM Launch VS Code using the 'openocd' data folder
@echo | code -data openocd | exit /b