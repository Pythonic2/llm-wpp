# Plano e riscos de implantação

1. Modelos aditivos de conta de cobrança, compra/checkout e evento de Webhook. Campo de status comercial da empresa com `legacy` como padrão para preservar clientes existentes. Conta pública criada pendente.
2. Cliente Asaas em serviço isolado com timeout, erros sanitizados e base URL sandbox configurável. Nenhum token no frontend ou resposta HTTP. Criar checkout com referência externa e callback para página de status.
3. API pública de cadastro/status/verificação de e-mail e API autenticada de compras adicionais. CSRF obrigatório nas mutações, throttling e idempotência. Webhook autenticado com token separado e deduplicação por ID de evento.
4. Aplicar gate de capacidade nos dois caminhos de conexão WhatsApp; serializar aquisições concorrentes por empresa.
5. Frontend público de cadastro/pagamento, página de espera, seletor de empresas revisado e compra de vaga adicional.
6. Testes de segurança/integração com cliente Asaas mockado; sandbox apenas após variáveis configuradas de forma segura. Verificação de eventos de pagamento, repetição, ordem, tenant e falhas.
7. Release no Raspberry somente após validação, configuração do token de Webhook e autorização específica para migração/produção. Migrações aditivas; rollback por versão anterior mantendo novas tabelas.

## Decisões pendentes para produção

- Meio de pagamento inicial decidido: cartão recorrente. Pix recorrente manual fica para etapa futura.
- Carência definida pelo usuário: 7 dias corridos após vencimento. Estorno/chargeback revoga imediatamente; uma vaga excedente permanece cadastrada, mas não envia mensagens quando o limite cai. A aplicação impede atendimento por IA quando a assinatura principal ou a vaga do número está indisponível.
- Domínio público da landing page e URL final de retorno; podem usar `crm.gestcloud.com.br` se esta for a origem desejada.
- Verificação de e-mail implementada com link assinado; configurar e testar conta SMTP transacional antes da abertura pública.
