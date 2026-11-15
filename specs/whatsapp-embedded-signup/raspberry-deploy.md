# Implantação no Raspberry — 15/11/2026

## Escopo e estado verificado

- Escopo autorizado pelo usuário: Raspberry Pi. Cloud Run fica para depois.
- Stack Portainer `saas` (ID 203), com `backend`, `worker` e Redis; stack `front-saas` (ID 205), com frontend Nginx.
- Backend e worker atuais: `ghcr.io/pythonic2/llm-wpp:sha-e2e95679485cf7a6f6f3a0708dc39a600f02fbda`.
- Frontend atual: `ghcr.io/pythonic2/llm-wpp-frontend:SHA-a3341164272baf3741766db7812a90346d9e84c9`.
- Antes da implantação, `saas.0012` estava aplicada e `saas.0013_embedded_signup_state` pendente.
- Imagens ARM64 locais construídas no Raspberry: `ghcr.io/pythonic2/llm-wpp:pi-embedded-20261115` e `ghcr.io/pythonic2/llm-wpp-frontend:pi-embedded-20261115`. Nenhuma imagem foi enviada ao GHCR.
- App ID Meta `2728539627563279` e Config ID V4 `1792802512388725`, obtidos no painel Meta conectado. Nenhum token ou segredo foi extraído do painel.

## Sequência de aplicação

1. Guardar cópia restrita dos dois arquivos de stack e seus `stack.env` no próprio Raspberry. Não registrar valores no terminal ou Git.
2. Preservar as variáveis existentes e acrescentar `META_APP_ID`, `META_EMBEDDED_SIGNUP_CONFIG_ID` e uma chave Fernet nova e estável em `WHATSAPP_TOKEN_ENCRYPTION_KEY`. O segredo existente `WHATSAPP_APP_SECRET` deve permanecer intacto.
3. Aplicar `saas.0013` uma única vez com a imagem nova, usando as variáveis de banco da stack. A migration adiciona sete colunas a `WhatsAppNumber` e cria três tabelas; não apaga dados. Aguardar conclusão e confirmar `showmigrations`.
4. Alterar as stacks para as tags locais acima e `APP_PULL_POLICY=never` / `FRONTEND_PULL_POLICY=never`, de modo que o Portainer não tente buscar essas tags no GHCR. Atualizar backend, worker e frontend. Manter Redis.
5. Confirmar containers saudáveis, HTTP 200 no frontend e backend, acesso autenticado ao endpoint de configuração do Embedded Signup e ausência de erro novo nos logs. Não concluir cadastro com número real durante a validação.

## Recuperação

- Se um container novo falhar, restaurar os arquivos de stack/ambiente guardados e as tags anteriores; executar `docker compose up -d` com as configurações anteriores.
- A migration é aditiva; manter colunas e tabelas em caso de retorno à imagem antiga. Não reverter a migration de modo destrutivo.
- Se o teste revelar falha no Embedded Signup, retirar `META_APP_ID`/Config ID do ambiente para desabilitar o fluxo, preservando a chave Fernet e tokens já gravados.
- Guardar a chave Fernet em local seguro fora do Git. Sua perda impede abrir tokens dos números conectados.

## Verificação disponível

- Suíte backend: 79 aprovados, 1 falha anterior não relacionada à integração. Testes usam SQLite temporário.
- Frontend: 19 testes aprovados na etapa de implementação; build de produção passou novamente nesta preparação.
- Compose backend e frontend validaram sintaxe. Imagens ARM64 locais foram construídas com sucesso.
- Não houve teste real com Meta antes da aplicação autorizada.

## Resultado

- O usuário autorizou especificamente a migration `saas.0013` e a criação/configuração da chave Fernet. A migration foi aplicada com sucesso e `showmigrations` confirma `[X] 0013_embedded_signup_state`.
- As stacks Portainer 203 e 205 receberam os novos arquivos Compose e `stack.env`, com permissão `600` nos arquivos de ambiente. Backend, worker e frontend estão nas tags locais `pi-embedded-20261115`; Redis continuou saudável.
- Os containers carregam App ID e Config ID esperados, segredo Meta existente e chave Fernet válida. O Celery informou `ready`.
- Frontend e rota de autenticação do backend retornaram HTTP 200 tanto no Raspberry quanto em `https://crm.gestcloud.com.br`.
- Cópias das configurações anteriores, novas configurações e imagens Docker foram guardadas em `/home/pi/llm-wpp-deploy-backup-20261115`, no Raspberry. Credenciais e imagens estão com permissão `600`.
- Nenhum push ou publicação de imagem no GHCR foi feito. As imagens estão apenas no Raspberry; uma futura atualização pelo Git ou limpeza das imagens Docker exige cuidado com as tags locais. Não foi feito cadastro real de número na Meta.
