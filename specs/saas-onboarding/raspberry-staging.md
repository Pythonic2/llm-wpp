# Implantação no Raspberry — 15/11/2026

Estado: **publicado para testes com Asaas sandbox** em `https://crm.gestcloud.com.br`. A cobrança real permanece desativada (`ASAAS_ALLOW_PRODUCTION=false`). Cloud Run continua fora do escopo.

## Versão publicada

- Backend, worker e Celery Beat: `ghcr.io/pythonic2/llm-wpp:pi-billingfix-20261115-5c2f9b` (ARM64 local no Raspberry).
- Frontend: `ghcr.io/pythonic2/llm-wpp-frontend:pi-billingfix-sandbox-20261115-caf9d8` (ARM64 local no Raspberry; banner visível de pagamentos simulados).
- Banco Neon, esquema `crm`: `saas.0014_commercial_finance` e `saas.0015_billing_onboarding` aplicadas. `0013` já estava aplicada. Empresas existentes receberam o estado `legacy`.
- Backup imediatamente anterior às migrations: `/home/pi/gesttech-predeploy-20261115/pre-deploy-crm.dump` (formato customizado, verificado por `pg_restore --list`, modo 600). Cópia do backup anterior em `pre-deploy-crm-initial.dump`.
- Arquivos Compose e de ambiente anteriores preservados em `/home/pi/gesttech-predeploy-20261115/`. Credenciais de Asaas sandbox e Gmail estão apenas no `.env` local ignorado pelo Git e em arquivos privados `stack.env`/backup do Raspberry; os valores não estão neste documento.
- Webhook Asaas sandbox habilitado para `https://crm.gestcloud.com.br/api/webhooks/asaas/`, versão 3, envio sequencial, 11 eventos. Token próprio separado da API key.

## Verificações executadas

- Testes locais: backend 111 testes completos, `manage.py check`, `makemigrations --check --dry-run`, `git diff --check`; frontend 25 testes, lint e build.
- No Raspberry: serviços de backend, worker, scheduler, Redis e frontend em execução; worker alcança Redis; cache Redis compartilhado testado entre backend e worker; migrations 0013–0015 marcadas como aplicadas.
- URL pública: `/` e `/signup` responderam 200; `/api/v1/auth/csrf/` respondeu 200 após a estabilização do backend; cadastro vazio com CSRF respondeu 400 JSON; webhook autenticado com evento inválido respondeu 400 JSON, sem gravar evento. Link `/invite/accept` respondeu 200. Bundle frontend contém formulário de convite, consentimento visível e catálogo.
- Consulta à API Asaas sandbox mostrou um webhook ativo com 11 eventos. Autenticação SMTP Gmail e e-mail de teste para a própria conta foram validados na preparação.

## Pendências de validação

- Executar uma compra com cartão de teste no Asaas sandbox e confirmar a sequência completa: checkout, webhook, ativação da empresa, renovação e carência de sete dias. Nenhuma compra de teste foi criada no banco de produção durante o deploy.
- Validar visualmente as telas autenticadas com usuário real após atualização do navegador. O formulário de equipe agora convida novos funcionários por e-mail; o texto `teste` sozinho continua sem conta e precisa de e-mail para um convite novo.
- Uma tentativa real de adicionar empresa no sandbox revelou que um usuário legado sem e-mail criava cliente Asaas sem e-mail e recebia HTTP 400 ao tentar criar checkout. A tentativa local ficou em `review`, sem ID de checkout Asaas. Um probe com o mesmo cliente reproduziu o erro `invalid_object` por e-mail ausente e não criou checkout. A correção exige e-mail de cobrança e permite retomar a mesma empresa/checkout apenas após confirmar por leitura que o cliente Asaas ainda está sem e-mail; consulte [legacy-billing-email-fix.md](legacy-billing-email-fix.md). A pessoa deve recarregar a página e informar o e-mail; nenhum e-mail foi presumido ou gravado por este deploy.
- As stacks do Portainer guardam variáveis de tag antigas. Os manifestos locais em `/data/compose/203` e `/data/compose/205` foram atualizados para as tags publicadas, mas uma nova edição/reimplantação pela interface do Portainer pode sobrescrever esses arquivos com os valores antigos. Antes de usar “Update the stack”, alinhar no Portainer `APP_IMAGE_TAG=pi-billingfix-20261115-5c2f9b`, `FRONTEND_IMAGE_TAG=pi-billingfix-sandbox-20261115-caf9d8` e `APP_PULL_POLICY=never`/`FRONTEND_PULL_POLICY=never`.

## Recuperação

Se houver falha, voltar às imagens anteriores preservadas nos arquivos Compose/ambiente do diretório de backup. As migrations são aditivas; preservar as tabelas e eventos financeiros para conciliação em vez de reverter o banco. Se o webhook gerar divergência, desativá-lo no Asaas sandbox e reconciliar checkouts antes de reabrir cadastros.
