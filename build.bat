@echo off
chcp 65001 >nul
setlocal

echo ========================================
echo  Сборка Remzona_groups
echo ========================================
echo.

:: Проверка наличия Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ОШИБКА] Python не найден. Установите Python и добавьте его в PATH.
    pause
    exit /b 1
)

:: Установка/обновление PyInstaller и зависимостей
echo [1/3] Проверяю зависимости...
python -m pip install --upgrade pip >nul
python -m pip install pyinstaller pandas openpyxl --quiet
if errorlevel 1 (
    echo [ОШИБКА] Не удалось установить зависимости.
    pause
    exit /b 1
)

:: Проверка файлов
if not exist "fill_groups_gui.py" (
    echo [ОШИБКА] Не найден файл fill_groups_gui.py
    pause
    exit /b 1
)

if not exist "logo.ico" (
    echo [ПРЕДУПРЕЖДЕНИЕ] logo.ico не найден — сборка без иконки.
    set ICON_ARG=
) else (
    set ICON_ARG=--icon=logo.ico
)

echo [2/3] Собираю exe (это может занять 1–3 минуты)...
echo.

:: Сборка: один файл, без консоли, с иконкой
python -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --windowed ^
    --name=Remzona_groups ^
    %ICON_ARG% ^
    --add-data "groups.txt;." ^
    fill_groups_gui.py

if errorlevel 1 (
    echo.
    echo [ОШИБКА] Сборка не удалась.
    pause
    exit /b 1
)

echo.
echo [3/3] Готово!
echo.
echo Исполняемый файл:
echo   dist\Remzona_groups.exe
echo.
echo Примечание:
echo   - groups.txt будет рядом с exe (если он был в папке при сборке).
echo   - matching_memory.json создаётся рядом с exe при первом запуске.
echo   - Если groups.txt нет — приложение попросит указать файл вручную.
echo.

:: Копируем groups.txt в dist, если есть
if exist "groups.txt" (
    copy /Y "groups.txt" "dist\groups.txt" >nul
    echo groups.txt скопирован в dist\
)

echo ========================================
pause
endlocal