# Preparação no Raspberry — 15/11/2026

Estado: **aguardando configuração para publicar tudo junto**, conforme escolha do usuário. Nenhuma migration nova foi aplicada; stacks e containers ativos não foram trocados.

## Preparado e verificado

- Raspberry acessível por SSH como `pi@192.168.0.6`. Backend/worker/front permanecem nas imagens `pi-embedded-20261115`; Redis permanece ativo.
- Banco de produção com `saas.0013` aplicada; `0014_commercial_finance` e `0015_billing_onboarding` pendentes. Antes da preparação, havia 3 empresas, nenhum contato com `wa_id` vazio e 2 números ativos. Apenas contagens foram consultadas.
- Imagens ARM64 locais construídas, sem push: backend `ghcr.io/pythonic2/llm-wpp:pi-gesttech-20261115-f1aaee`; frontend `ghcr.io/pythonic2/llm-wpp-frontend:pi-gesttech-20261115-4e651b`. Módulos Python compilaram e `nginx -t` passou em containers isolados.
- Pacotes de código sem `.env` e backups dos arquivos Compose/ambiente guardados em `/home/pi/gesttech-predeploy-20261115` (diretório privado). Backup do esquema `crm` em `/home/pi/gesttech-predeploy-20261115/pre-deploy-crm.dump`, 227810 bytes, permissão `600`; `pg_restore --list` validou o formato. Foi usado `pg_dump` 18 em container temporário, compatível com o Neon PostgreSQL 18. O primeiro `pg_dump` 17 não alterou o banco e falhou por incompatibilidade de versão.
- As imagens foram preparadas a partir do estado local de trabalho, incluindo arquivos ainda não commitados. Após esta preparação, o cadastro passou a verificar antecipadamente a presença de Asaas/Webhook/SMTP; **a imagem backend preparada não contém esse ajuste e deve ser reconstruída antes da implantação**. Tags e hashes dos pacotes identificam a preparação anterior.

## Pendências antes do deploy

1. Configurar Asaas e SMTP em ambiente seguro, sem publicar credenciais: `ASAAS_API_KEY`, `ASAAS_WEBHOOK_AUTH_TOKEN`, `GESTTECH_PUBLIC_URL`, `GESTTECH_FROM_EMAIL`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`. Atualmente nenhuma dessas variáveis está presente no backend do Raspberry.
   - O `.env` local ignorado pelo Git recebeu URL sandbox, URL pública, remetente e usuário Gmail, host/porta STARTTLS e um token de Webhook novo. Depois, por pedido explícito do usuário, a chave sandbox do Asaas e a senha de aplicativo do Gmail foram configuradas apenas nesse arquivo local, com permissão `600`. Uma consulta autenticada de leitura ao Asaas sandbox passou; o Gmail autenticou e aceitou um e-mail de teste enviado para a própria conta. Nenhum desses valores foi copiado para este documento ou para o Raspberry.
2. Validar e-mail transacional e fluxo completo com Asaas sandbox (checkout, Webhook, renovação, 7 dias, estorno) antes de habilitar cobrança real. Produção usa chave Asaas distinta da sandbox e exige opt-in explícito.
3. Revisar e aplicar migrations `0014` e `0015` com a imagem nova; `0014` substitui a restrição de unicidade de contato por uma parcial e cria tabelas comerciais; `0015` cria tabelas de cobrança e marca empresas existentes como `legacy`.
4. Atualizar Compose/stack.env do backend, worker, scheduler e frontend usando as tags locais e `pull_policy: never`, verificar saúde, HTTP e logs. O scheduler novo é necessário para a conciliação diária.

O usuário optou por aguardar a publicação completa. Nenhum passo da seção de pendências foi executado.
