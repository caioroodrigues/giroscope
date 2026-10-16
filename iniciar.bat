@echo off
chcp 65001 >nul
cd /d "%~dp0"
title GiroScope - Inteligencia Comercial e Estoque

if not exist ".venv\Scripts\python.exe" (
    echo Preparando o sistema pela primeira vez. Isso leva alguns minutos...
    set "PY=python"
    where python >nul 2>nul || set "PY=py"
    call %%PY%% -m venv .venv
    if errorlevel 1 (
        echo.
        echo Nao foi possivel criar o ambiente Python.
        echo Verifique se o Python esta instalado com a opcao "Add Python to PATH".
        pause
        exit /b 1
    )
)

echo Conferindo bibliotecas...
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo.
    echo Falha ao instalar as bibliotecas. Verifique a conexao com a internet.
    pause
    exit /b 1
)

echo.
echo Abrindo o sistema no navegador. Para encerrar, feche esta janela.
".venv\Scripts\python.exe" -m streamlit run app.py --server.headless false
pause
