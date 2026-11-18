# Plano de implementação

1. Adicionar PlanVersion, BusinessUnit, vínculos opcionais e compartilhamento explícito; migrar dados existentes para plano anterior e unidade padrão.
2. Centralizar resolução de unidade, quota e validações de empresa; aplicar nos caminhos manual e Embedded Signup.
3. Aplicar escopo no webhook, IA, RAG, mídia, catálogo, pedidos, vendas e relatórios.
4. Criar APIs de unidades, vínculo de número, compartilhamento, filtros e revisão administrativa de indícios.
5. Atualizar interface React no repositório independente e testes focais de backend/frontend.
6. Validar schema, testes, segurança e prontidão de deploy sem publicar ou executar migração real.

## Matriz de testes

| Risco | Verificação |
| --- | --- |
| Mistura de conteúdo na IA | RAG e mídia com duas unidades e compartilhamento explícito |
| Vazamento entre empresas | APIs, compartilhamento e mutações com usuário de outro tenant |
| Quota e corrida | Duas criações concorrentes; inativas contabilizadas; números pagos |
| Histórico | Webhook e troca de unidade sem reatribuir conversa, pedido ou venda |
| Cobrança | Plano antigo e novo, checkout snapshot, webhook idempotente |
| Relatórios | Individual + consolidado, venda manual e CSV |
| Migração | Contagens e relacionamentos antes/depois em banco de teste |
