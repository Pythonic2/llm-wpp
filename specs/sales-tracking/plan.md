# Plano incremental — acompanhamento de vendas

## 0. Fechar regras de negócio

Validar as cinco decisões pendentes em `spec.md`, nome comercial dos indicadores e permissão de agentes. Fixar contratos de API e exemplos de agregação. Esta etapa precede qualquer alteração de banco.

## 1. Persistência e integridade

- Criar migration **aditiva** após `saas.0013`, com `Sale`, `SaleEvent` de auditoria e timestamp de envio necessário para evidência da IA. Não transformar pedidos aprovados ou oportunidades `won` antigas em vendas.
- Criar índices por empresa/status/data e constraints para chave de idempotência, referência externa e pedido ativo. Validar relações empresa–contato–conversa–pedido no serviço de domínio dentro de transação; `Model.clean()` sozinho não protege gravações.
- Manter `Decimal` e timezone explícita. A chave de idempotência fica no escopo da empresa, inclusive após cancelamento.

## 2. Registro e cancelamento por API

- Endpoints de listagem/detalhe, confirmação e cancelamento sob `/api/v1/companies/{company_id}/sales/`, reutilizando sessão, CSRF, paginação e `_company_for_request`. Restringir também os novos agregados financeiros do dashboard por papel, pois o dashboard atual é acessível a agentes.
- Serviço transacional de confirmação calcula assistência da IA e grava evento; cancelamento grava motivo e evento. Sem webhook de pagamento, envio automático de WhatsApp ou exclusão física.
- Repetições e concorrência protegidas por constraints do PostgreSQL, não apenas por checagem prévia em Python. Guardar impressão do payload para diferenciar retry idêntico de reutilização indevida da chave. Conflitos devem retornar erro claro e não duplicar receita.

## 3. Interface e continuidade do fluxo atual

- Tela de vendas com filtros, status, totais, pedido associado e ação de cancelamento; formulário a partir da conversa e do pedido, preenchendo contato e responsável sugerido.
- Manter `mark-won` como status de oportunidade. Exibir **Registrar venda confirmada** como ação separada, sem conversão automática. Mostrar evidência/estado de assistência da IA como informação calculada pelo servidor.
- Atualizar tipos TypeScript, cliente API, cache e estados de erro; preservar o fluxo atual de pedidos e caixa de entrada.

## 4. Dashboard e conversão

- Agregar por empresa e período: vendas ativas, valor registrado, subconjunto assistido, valor associado, cancelamentos e taxa de conversão da coorte definida.
- Usar consultas agregadas no PostgreSQL e índices; evitar calcular valores no React ou somar vendas por `Order` e `Conversation` em um join que duplique linhas.
- Adicionar cartões e explicações sobre período, cancelamentos e significado de “assistida”. Não chamar valor registrado de pagamento recebido.

## 5. Validação e implantação futura

- Matriz de testes: autorização e isolamento entre empresas; consistência das FKs; papel de agente; valor inválido; timestamps e evidência da IA; pedido aprovado sem venda; `mark-won` sem venda; duas vendas do mesmo contato; idempotência e concorrência; cancelamento e correção; agregações por período/timezone e divisão por zero.
- Revisar planos de execução SQL com volume representativo, migração aditiva e compatibilidade com aplicação anterior. Implantar primeiro esquema e API, depois interface; rollback de aplicação mantém tabelas e dados, sem `drop` destrutivo.
- Validar com dados de teste. Migração/implantação em produção exigem autorização específica; nenhuma faz parte desta proposta.

## Riscos e recuperação

- Supercontagem se contatos/conversas forem confundidos com vendas ou se joins multiplicarem pedidos e mensagens; centralizar indicadores na tabela `Sale` e testar contagens distintas.
- Atribuição indevida se a IA apenas analisou o lead, se a resposta falhou ou se ocorreu depois da venda; guardar mensagem de evidência e timestamp, com regra versionada.
- Relações entre empresas e gravações concorrentes exigem validação no servidor mais constraints no banco.
- Payloads financeiros e dados pessoais não devem aparecer integralmente em logs; registrar IDs, transições e falhas de forma suficiente para auditoria operacional.
- Vendas antigas não devem ser inferidas de `won` ou `approved`; os novos indicadores começam sem backfill automático.
