# Plano de implementação

1. Banco/modelos: alterações aditivas em empresa/contato/mensagem; catálogo, venda/linhas, pagamentos, métodos/taxas e auditoria. Índices por empresa/período e constraints de unicidade/idempotência. Nenhuma migration em dados reais durante desenvolvimento.
2. Regras de domínio: validação de relações por empresa, cálculo Decimal e snapshots; vendas, cancelamentos, recebimentos e reembolsos transacionais; atribuição de IA baseada em evidência.
3. API DRF: rotas de perfil, catálogo, vendas, pagamentos, indicadores e exportação CSV; sessão/CSRF, papéis e paginação existentes. Separar APIs comerciais de `saas/api.py` para manter a revisão possível.
4. IA: adicionar catálogo ativo ao contexto de resposta com somente campos públicos; preservar RAG, mídias e bloqueios de confirmação financeira/pedidos.
5. React: perfil opcional, catálogo, formulário/lista de vendas, registro de pagamentos e dashboard/relatórios responsivos com estados vazios. Reaproveitar autenticação, HTTP e identidade visual.
6. Validar com testes focais e regressão: multi-tenant, operações concorrentes, idempotência, custo ausente/parcial, cálculos, IA, CSV, frontend, migração e segurança. Revisar queries dos gráficos.
7. Preparar release e caminho de recuperação. Produção somente após testes e autorização específica para migrations, push e deploy; preservar dados e não operar finanças reais.

## Recuperação

Migrações são aditivas. Em falha, retornar à versão anterior da aplicação mantendo tabelas/colunas novas, sem `drop` ou backfill destrutivo. Registros financeiros são auditados e nunca removidos para reverter operação. Ativar funcionalidades novas em etapas para não interromper WhatsApp/IA.
