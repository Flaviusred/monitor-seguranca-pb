# Deploy no servidor de testes

O sistema roda em **Docker**, com dois serviços:

- **monitor**: o sistema (coleta, API e painel). Fica só na rede interna do Docker.
- **caddy**: servidor web na frente. Com um domínio, gera e renova o **certificado HTTPS sozinho**;
  sem domínio, publica em HTTP pelo IP do servidor.

O banco (SQLite) fica num volume do Docker e **não se perde** ao reiniciar ou atualizar.

> Testado localmente com Docker 29 / Compose 5: senha de acesso, coleta (8/8 fontes), tempo real pelo
> proxy, limite de coletas manuais e persistência dos dados após derrubar e subir os serviços.

## 1. O que você precisa

| Item | Recomendado |
|---|---|
| Servidor | VPS com **Ubuntu 22.04 ou 24.04**, 1 vCPU, 1 GB de RAM, 10 GB de disco |
| Acesso | SSH com usuário que tenha `sudo` |
| Domínio (opcional) | Um subdomínio, ex.: `monitor.seudominio.com.br`, com **registro A** apontando para o IP do servidor |
| Portas liberadas | 22 (SSH), 80 e 443 |

## 2. Preparar o servidor (uma vez só)

> Usando a Oracle Cloud grátis? Siga o [DEPLOY-ORACLE.md](DEPLOY-ORACLE.md), que substitui este passo.

Conecte por SSH e rode:

```bash
# Docker (script oficial) e permissão para seu usuário
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
# saia e entre de novo no SSH para a permissão valer

# Firewall: só SSH, HTTP e HTTPS
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

## 3. Levar o código para o servidor

O código está no repositório privado **https://github.com/Flaviusred/monitor-seguranca-pb**.
O servidor baixa por uma **chave de implantação** (deploy key): só lê, só este repositório.

### Opção A — pelo Git (recomendada)

No servidor, crie a chave e mostre a parte pública:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/monitor_deploy -N "" -C "servidor-testes-monitor"
cat ~/.ssh/monitor_deploy.pub
```

Cadastre a chave no GitHub, **sem** marcar "Allow write access": no repositório, vá em
**Settings → Deploy keys → Add deploy key** e cole o conteúdo exibido. Ou, no Windows, salve o conteúdo
num arquivo `chave.pub` e rode:

```powershell
gh repo deploy-key add chave.pub --repo Flaviusred/monitor-seguranca-pb --title "servidor-testes"
```

De volta ao servidor, aponte o GitHub para essa chave e baixe o código:

```bash
cat >> ~/.ssh/config <<'EOF'
Host github-monitor
  HostName github.com
  User git
  IdentityFile ~/.ssh/monitor_deploy
  IdentitiesOnly yes
EOF
git clone git@github-monitor:Flaviusred/monitor-seguranca-pb.git ~/monitor
cd ~/monitor
cp .env.exemplo .env
nano .env
```

### Opção B — por pacote (sem Git no servidor)

No Windows, dê dois cliques em **`empacotar.bat`**. Ele gera `monitor-deploy.tar.gz` (cerca de 60 KB),
**sem** o banco local e sem senhas. Envie e descompacte:

```powershell
scp monitor-deploy.tar.gz usuario@IP-DO-SERVIDOR:~
```

```bash
mkdir -p ~/monitor && tar -xzf ~/monitor-deploy.tar.gz -C ~/monitor && cd ~/monitor
cp .env.exemplo .env
nano .env
```

### Configurar o `.env`

No `.env`, ajuste:

- `DOMINIO`: o subdomínio (ex.: `monitor.seudominio.com.br`), ou deixe vazio para acessar pelo IP.
- `ACESSO_USUARIO` e `ACESSO_SENHA`: login do painel. **Troque a senha.** Gere uma forte com
  `openssl rand -base64 18`.
- `ANTHROPIC_API_KEY`: só se quiser a classificação por IA (tem custo).

## 4. Subir

```bash
docker compose up -d --build
docker compose ps          # "monitor" deve aparecer como (healthy) após ~1 minuto
docker compose logs -f monitor   # acompanhe a primeira coleta; Ctrl+C para sair
```

Acesse `https://SEU-DOMINIO` (ou `http://IP-DO-SERVIDOR`) e entre com o usuário e a senha do `.env`.
A primeira coleta leva alguns segundos; depois ela roda sozinha a cada 5 minutos.

## 5. Levar os dados que você já coletou (opcional)

Por padrão o servidor começa com o banco vazio. Para levar o histórico do seu computador:

1. Feche o `iniciar.bat` no Windows (o banco precisa estar fechado).
2. Envie o arquivo: `scp dados\monitor.db usuario@IP-DO-SERVIDOR:~/monitor/`
3. No servidor:

```bash
cd ~/monitor
docker compose stop monitor
docker compose cp monitor.db monitor:/dados/monitor.db
docker compose run --rm --user root monitor chown monitor:monitor /dados/monitor.db
docker compose start monitor && rm monitor.db
```

## 6. Atualizar para uma nova versão

No Windows, envie as mudanças para o GitHub (`git add -A`, `git commit -m "..."`, `git push`). No servidor:

```bash
cd ~/monitor && git pull && docker compose up -d --build
```

Se usou a opção B (pacote), gere um novo com `empacotar.bat`, envie com `scp` e rode
`tar -xzf ~/monitor-deploy.tar.gz -C ~/monitor && docker compose up -d --build`.

O `.env` e o banco são preservados. Se a nova versão mudar as regras de agrupamento, o banco é
reagrupado sozinho na inicialização, com cópia de segurança em `/dados/monitor.vN.bak`.

## 7. Cópia de segurança do banco

```bash
cd ~/monitor && mkdir -p backups
docker compose exec monitor python -c "import sqlite3; sqlite3.connect('/dados/monitor.db').backup(sqlite3.connect('/dados/copia.db'))"
docker compose cp monitor:/dados/copia.db backups/monitor-$(date +%F).db
```

Para fazer isso todo dia às 3h, rode `crontab -e` e acrescente (ajuste o caminho):

```
0 3 * * * cd /home/USUARIO/monitor && docker compose exec -T monitor python -c "import sqlite3; sqlite3.connect('/dados/monitor.db').backup(sqlite3.connect('/dados/copia.db'))" && docker compose cp monitor:/dados/copia.db backups/monitor-$(date +\%F).db && find backups -name '*.db' -mtime +30 -delete
```

## 8. Comandos do dia a dia

| Para | Comando (na pasta `~/monitor`) |
|---|---|
| Ver se está tudo de pé | `docker compose ps` |
| Ver os registros | `docker compose logs -f monitor` |
| Reiniciar | `docker compose restart monitor` |
| Parar tudo | `docker compose down` (os dados ficam) |
| Verificar por fora | `curl http://IP-DO-SERVIDOR/api/saude` |

## 9. Segurança (checklist)

- [ ] Senha forte no `.env` (o painel inteiro pede login; só `/api/saude` fica aberto).
- [ ] O `.env` nunca vai para o Git nem para o pacote (`.gitignore` e `empacotar.bat` já o deixam de fora).
- [ ] A deploy key do servidor é só de leitura; se o servidor for desativado, remova-a em Settings → Deploy keys.
- [ ] Firewall com apenas 22, 80 e 443.
- [ ] SSH com chave, de preferência sem senha: `PasswordAuthentication no` em `/etc/ssh/sshd_config`.
- [ ] Atualizações automáticas do Ubuntu: `sudo apt install unattended-upgrades`.
- [ ] O botão "Atualizar" aceita no máximo uma coleta por minuto, para não sobrecarregar os portais.

## 10. Problemas comuns

| Sintoma | Causa provável |
|---|---|
| HTTPS não funciona com domínio | DNS ainda não aponta para o servidor, ou portas 80/443 fechadas. Veja `docker compose logs caddy`. |
| Navegador pede a senha em loop | Usuário/senha diferentes do `.env`. Depois de mudar o `.env`, rode `docker compose up -d`. |
| Feed não atualiza sozinho | Se houver outro proxy (nginx, Cloudflare) na frente, ele precisa repassar o tempo real (SSE) sem buffer. |
| "Fontes ativas" abaixo de 8/8 | Algum portal fora do ar ou bloqueando. Veja o motivo em `docker compose logs monitor`. |
| `port is already allocated` | Outro serviço já usa a porta 80/443 no servidor (ex.: Apache ou nginx). Pare-o ou mude as portas no `docker-compose.yml`. |
