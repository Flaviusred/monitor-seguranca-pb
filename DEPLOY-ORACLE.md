# Publicar de graça na Oracle Cloud (Always Free)

Guia para colocar o painel num endereço público, com HTTPS, numa máquina virtual gratuita da Oracle.
Os passos de Docker são os mesmos do [DEPLOY.md](DEPLOY.md); aqui ficam só o que é específico da Oracle.

Resultado: o painel em `https://SEU-IP.sslip.io` (sem comprar domínio), sempre ligado, coletando a
cada 5 minutos e guardando o histórico.

## 1. Criar a conta

1. Acesse <https://www.oracle.com/cloud/free/> e clique em **Start for free**.
2. Preencha os dados. O cartão de crédito é pedido **só para verificar a identidade**; a conta "Always Free"
   não cobra nada enquanto você usar só recursos marcados como *Always Free-eligible*.
3. **Home region:** escolha **Brazil East (São Paulo)** ou **Brazil Southeast (Vinhedo)**. Ela não pode ser
   trocada depois, e as máquinas grátis só podem ser criadas nela.

A aprovação da conta pode levar de alguns minutos a algumas horas.

## 2. Criar a máquina virtual

No console (<https://cloud.oracle.com>), menu ☰ → **Compute → Instances → Create instance**:

| Campo | Valor |
|---|---|
| Name | `monitor-pb` |
| Image | **Canonical Ubuntu 24.04** (botão *Change image*) |
| Shape | *Change shape* → **Ampere → VM.Standard.A1.Flex**, com **1 OCPU e 6 GB** de memória. Deve aparecer a etiqueta *Always Free-eligible* |
| Networking | *Create new virtual cloud network* e *Create new public subnet*, com **Assign a public IPv4 address** marcado |
| Add SSH keys | **Generate a key pair for me** → clique em **Save private key** e guarde o arquivo `.key` |
| Boot volume | deixe o padrão (até 200 GB somados são grátis) |

Clique em **Create**. Em um ou dois minutos o estado muda para *Running*. Anote o **Public IP address**.

> **"Out of capacity for shape VM.Standard.A1.Flex"**: comum em São Paulo. Tente de novo mais tarde, troque o
> *Availability domain*, ou use o shape **VM.Standard.E2.1.Micro** (AMD, 1 GB de RAM, também grátis e
> suficiente para este sistema).

## 3. Liberar as portas 80 e 443

A Oracle tem **dois** bloqueios; os dois precisam ser abertos.

**a) No console (lista de segurança da rede):** na página da instância, clique no nome da *Subnet* →
aba **Security** (ou *Security Lists*) → *Default Security List* → **Add Ingress Rules**. Crie duas regras:

| Source CIDR | IP Protocol | Destination Port Range |
|---|---|---|
| `0.0.0.0/0` | TCP | `80` |
| `0.0.0.0/0` | TCP | `443` |

**b) Dentro da máquina (iptables):** feito no passo 5.

## 4. Entrar por SSH

No Windows (PowerShell), na pasta onde salvou a chave:

```powershell
icacls .\ssh-key-*.key /inheritance:r /grant:r "$($env:USERNAME):(R)"
ssh -i .\ssh-key-*.key ubuntu@IP-DA-MAQUINA
```

O usuário é sempre `ubuntu`. O `icacls` só é preciso uma vez: sem ele, o SSH recusa a chave com
"permissions are too open".

## 5. Preparar a máquina

Já conectado por SSH:

```bash
# Docker
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu

# Abrir 80 e 443 no firewall do Ubuntu da Oracle e manter após reiniciar
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save

# Atualizações de segurança automáticas
sudo apt-get install -y unattended-upgrades

exit
```

Entre de novo pelo SSH (para a permissão do Docker valer).

> **Não use o `ufw`** do passo 2 do DEPLOY.md nesta máquina: a imagem da Oracle já usa `iptables`, e
> ligar o `ufw` por cima pode bloquear o próprio SSH.

## 6. Baixar o código e configurar

Siga o **passo 3 do [DEPLOY.md](DEPLOY.md)** (opção A, com deploy key, ou opção B, com pacote).
No `.env`, use:

```ini
# troque pelos números do seu IP, separados por hífen
DOMINIO=150-230-10-20.sslip.io
ACESSO_USUARIO=equipe
ACESSO_SENHA=uma-senha-forte
```

O `sslip.io` é um serviço gratuito que transforma o IP num nome (`150-230-10-20.sslip.io` aponta para
`150.230.10.20`). Com ele o Caddy consegue emitir o certificado HTTPS sozinho. Gere a senha com
`openssl rand -base64 18`. Para deixar o painel aberto sem login, deixe `ACESSO_USUARIO` e
`ACESSO_SENHA` vazios.

## 7. Subir

```bash
cd ~/monitor
docker compose up -d --build
docker compose ps                  # "monitor" fica (healthy) em ~1 minuto
docker compose logs caddy | grep -i certificate   # confirma o HTTPS
```

Abra `https://150-230-10-20.sslip.io` (com o seu IP). Atualizações, cópia de segurança e comandos do dia a
dia estão no DEPLOY.md, a partir do passo 5.

## 8. Cuidados com a conta grátis

- **Máquina ociosa pode ser recuperada.** A Oracle pode desligar instâncias Always Free que passam 7 dias
  com uso muito baixo de CPU, rede e memória. A coleta a cada 5 minutos costuma bastar; para eliminar o risco,
  converta a conta para **Pay As You Go** (*Billing → Upgrade*). Continua sem custo enquanto você usar só
  recursos Always Free, e essas contas não sofrem a recuperação. Se fizer isso, crie um
  **orçamento com alerta** (*Billing → Budgets*, ex.: R$ 1) para ser avisado de qualquer cobrança.
- **Não crie recursos sem a etiqueta *Always Free-eligible*** (outros shapes, load balancer pago, etc.).
- **IP fixo:** o IP público padrão muda se a máquina for *terminada* (reiniciar não muda). Para garantir,
  troque por um **Reserved Public IP** (gratuito, um por conta) em *Instance → Attached VNICs → IPv4 Addresses*.

## Problemas comuns

| Sintoma | Causa provável |
|---|---|
| Navegador não abre nada (tempo esgotado) | Falta a regra do passo 3a ou do 5. Teste por fora: `curl http://IP/api/saude` |
| Abre em HTTP mas o HTTPS falha | `DOMINIO` com o IP errado, ou porta 443 fechada. Veja `docker compose logs caddy` |
| `ssh: Permission denied (publickey)` | Usuário diferente de `ubuntu` ou chave errada |
| `Out of capacity` ao criar a máquina | Veja a nota do passo 2 |
