@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Gera monitor-deploy.tar.gz com o necessário para o servidor (sem banco local e sem senhas)
tar -czf monitor-deploy.tar.gz --exclude=__pycache__ app web Dockerfile docker-compose.yml Caddyfile requirements.txt .dockerignore .env.exemplo .gitignore README.md DEPLOY.md
if errorlevel 1 (
  echo Falha ao gerar o pacote.
) else (
  echo Pacote gerado: %~dp0monitor-deploy.tar.gz
  echo Envie para o servidor com:  scp monitor-deploy.tar.gz usuario@IP-DO-SERVIDOR:~
)
pause
