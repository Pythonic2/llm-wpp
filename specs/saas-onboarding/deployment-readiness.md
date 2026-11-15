# Prontidão de implantação — Raspberry

**Estado: NO-GO para cobrança pública/produção em 15/11/2026.** O Cloud Run do CRM não existe e não é alvo deste plano. Nenhuma migration de produção, alteração de segredo, criação de Webhook, deploy ou cobrança real foi executada nesta implementação.

## Evidências locais

- Backend: 100 testes passaram, incluindo 10 de cobrança. `manage.py check`, `makemigrations --check --dry-run` e `git diff --check` passaram.
- Frontend: 24 testes, lint e build passaram. Build gerou artefatos apenas no repositório de frontend.
- `docker compose config --quiet` passou. Migration `saas.0015` é aditiva e depende de `0014`; `0013` a `0015` ainda precisam de plano de aplicação em produção conforme estado real do banco.
- A execução de migrations em SQLite isolado parou na migration inicial `saas.0001`, que usa `CREATE EXTENSION` do PostgreSQL/pgvector. Portanto a aplicação real de `0013`–`0015` ainda não foi verificada em PostgreSQL local ou sandbox; não foi usado banco de produção.

## Antes de liberar produção

1. Configurar e validar SMTP transacional e remetente. Verificação de e-mail com link assinado já está implementada. Definir limpeza/criptografia de CPF/CNPJ pendente; verificar política de privacidade e texto de consentimento no cadastro.
   - Para o Gmail autorizado pelo usuário, usar `smtp.gmail.com:587` com STARTTLS e senha de aplicativo da conta após ativar verificação em duas etapas. O cadastro público agora falha antes de criar conta se as credenciais Asaas/Webhook/SMTP estiverem ausentes em produção.
2. Configurar variáveis de sandbox sem versionar segredos: `ASAAS_API_KEY`, `ASAAS_WEBHOOK_AUTH_TOKEN` independente, `ASAAS_BASE_URL=https://api-sandbox.asaas.com/v3`, `GESTTECH_PUBLIC_URL` com HTTPS. Criar Webhook sandbox autenticado para `/api/webhooks/asaas/` com eventos de Checkout, assinatura e pagamentos necessários.
3. Realizar compra de teste sandbox de cada produto (primeira empresa, adicional, vaga), duplicidade/ordem de eventos, renovação vencida + 7 dias, pagamento tardio, estorno, falha da API e conciliação diária. Confirmar IDs de assinatura e preço no retorno real do Asaas. Não usar valor de segredo em logs.
4. Configurar cache Redis para throttle compartilhado. Monitorar Webhooks rejeitados, checkouts em `review`, tarefas de conciliação com falha, empresas com carência vencida e divergências entre Asaas e CRM.
5. Fazer backup verificado do banco. Construir imagens backend/frontend revisáveis, conferir variáveis no Portainer/backend/worker/scheduler, aplicar migrations aditivas com janela e autorização específica, criar Webhook de produção somente com token próprio, testar cadastro real controlado e acompanhar logs sem dados pessoais.
6. Documentar política para clientes legados e suporte de compra ambígua. Produção exige chave Asaas de produção distinta da sandbox e `ASAAS_ALLOW_PRODUCTION=true` somente quando os critérios anteriores estiverem atendidos.

## Recuperação

Se o frontend ou Webhook falhar, retornar imagens anteriores e desabilitar entrada pública nova. Preservar tabelas aditivas e eventos já recebidos para conciliação; não reverter migrations destrutivamente. Pausar Webhook/checkout no Asaas se houver inconsistência de cobrança; reconciliar assinaturas e pagamentos pelo ID de checkout antes de reabrir. Nenhuma operação de recuperação deve apagar registros financeiros.
