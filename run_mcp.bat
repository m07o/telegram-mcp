@echo off
set VIRTUAL_ENV=
set HERMES_VENV=C:\Users\Mohamed\AppData\Local\hermes\hermes-agent\venv\Scripts
set PATH=%PATH:%HERMES_VENV%=%
set PATH=B:/for-hermes/telegram-mcp/.venv\Scripts;%PATH%
python main.py %*
