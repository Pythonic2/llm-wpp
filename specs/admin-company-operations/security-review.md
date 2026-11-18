# Revisão de segurança — operações do Django Admin

Data: 17/11/2026. Escopo: concessão de teste, arquivamento, registros financeiros e permissões no Admin. Nenhum banco real ou serviço externo foi alterado.

## Controles verificados

- Concessão/encerramento de teste e arquivamento/restauração exigem superusuário no servidor. POST forjado por staff não altera a empresa. Ações usam transação e `select_for_update`; o histórico do Django registra ator, empresa e operação.
- O gate central verifica `archived_at` antes de qualquer assinatura/teste. Arquivamento corta CRM, IA e envio; eventos posteriores do Asaas podem mudar `billing_status`, mas não removem o bloqueio.
- Prazo é medido por `timezone.now()`; `trial_expires_at` não é editável diretamente. A concessão transforma `legacy` em `pending`, evitando teste que expira apenas visualmente.
- Checkouts, eventos de pagamento, vendas, pedidos e integrações ficam somente para consulta do superusuário; o Admin não permite fabricar estado `paid` ou apagar histórico. Dados pessoais de cobrança não são exibidos no formulário de consulta.
- Alteração de `Membership` e `WhatsAppNumber` no Admin foi limitada ao superusuário. Isso fecha dois caminhos de contorno do papel de usuário e do limite de números.
- Inserção manual de catálogo valida que categoria e mídias pertencem à empresa escolhida.

## Riscos e limites

1. **Alto — cobrança externa permanece ativa ao arquivar.** A ação não chama o Asaas. Operador deve consultar e cancelar a assinatura no provedor, quando aplicável; o Admin mostra aviso após o arquivamento.
2. **Médio — teste de banco não cobriu PostgreSQL real.** Os testes foram executados em SQLite isolado porque o `.env` aponta para um hostname externo; não foi seguro criar um banco de teste nesse servidor. A migração aditiva foi verificada por `makemigrations --check --dry-run`, mas execução real exige ambiente autorizado.
3. **Médio — conta inativa permanece inativa após concessão.** O operador deve conferir identidade/estado do e-mail e ativar o usuário manualmente. O Admin avisa após a concessão.

## Evidência

- `python manage.py check`: passou.
- `makemigrations --check --dry-run` com banco SQLite isolado: nenhuma alteração pendente.
- Suíte com SQLite isolado: 137 testes passaram; um teste de migração antiga (`test_timestamp_backfill_preserves_existing_values`) não é compatível com editor de schema SQLite dentro de transação e foi excluído da execução final. Não houve alteração nesse teste.
