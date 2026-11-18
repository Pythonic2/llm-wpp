# Revisão de segurança e prontidão

Status: implementação local concluída; publicação pendente de validação em PostgreSQL e autorização específica.

## Segurança

- Todas as APIs novas verificam associação à empresa. Mutações de unidade, número e compartilhamento exigem papel administrativo. IDs de unidade externos são resolvidos dentro da empresa.
- A consulta vetorial aplica `company_id` e unidade, incluindo apenas documentos compartilhados explicitamente. A busca de mídia e o catálogo da resposta seguem a unidade histórica da conversa. Mídia selecionada para resposta é validada novamente antes de montar o prompt.
- Conversas antigas não mudam ao mover um número. Pedidos e vendas preservam a unidade atribuída; vendas manuais em empresa com duas unidades exigem escolha.
- O Admin mantém cobrança e histórico financeiro de leitura apenas. Revisão de uso exige justificativa registrada; não existe bloqueio automático.
- Vínculos cruzados de unidade são recusados nas APIs e na validação dos modelos usados no Admin. O banco ainda usa FKs opcionais nesta etapa aditiva; scripts que gravem diretamente no ORM devem atribuir a unidade da mesma empresa.

## Verificações locais

- Django `check`: passou.
- `makemigrations --check --dry-run` com configuração SQLite isolada: nenhuma diferença.
- Pytest com SQLite isolado: 152 passaram; um teste de migração preexistente foi excluído por incompatibilidade do editor de schema SQLite dentro de transação.
- TypeScript `tsc --noEmit`: passou. Vitest: 35 passaram. Vite compilou para `/tmp/gest-tech-build`.
- PostgreSQL, migração real, lock de concorrência e integração com Asaas/Meta não foram exercitados neste ambiente. A execução real de migrações e chamadas externas com dados de produção permanece proibida sem autorização específica.

## Antes de publicar

1. Fazer backup e ensaio da migração `0020` e `0021` em cópia isolada de PostgreSQL, comparando contagens de empresas, números, bases, mídias, conversas, pedidos e vendas antes e depois.
2. Validar em PostgreSQL duas criações concorrentes da segunda unidade. Confirmar limite de duas unidades e estado de excesso de uso após redução do plano.
3. Confirmar que todas as instâncias do backend aplicam filtros por unidade antes de expor a criação da segunda unidade. Não executar versão anterior da IA com dados de duas unidades.
4. Conferir no sandbox Asaas os valores de R$ 30,00 para plano novo e R$ 49,90 para plano anterior, além de renovação, inadimplência e capacidade de números.
5. Verificar em sandbox Meta Embedded Signup, webhook assinado, coexistência e preservação de conversas ao mover número.
6. Monitorar erros de migração, consultas vetoriais, mensagens e webhooks após publicação autorizada. O retorno a código antigo exige suspender IA das empresas com duas unidades até restaurar o isolamento.
