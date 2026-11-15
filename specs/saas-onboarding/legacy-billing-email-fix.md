# E-mail de cobrança em contas legadas — 15/11/2026

Risco alto: cobrança, dados pessoais e recuperação de tentativa em produção.

## Defeito observado

Uma conta legada sem e-mail conseguiu criar cliente no Asaas sandbox, mas o checkout recorrente foi recusado porque o cliente Asaas não tinha e-mail. O checkout local ficou em `review`, sem ID externo. Uma repetição cega poderia duplicar empresa ou checkout.

## Comportamento esperado

1. A tela de compra adicional pede e-mail de cobrança quando a conta não o possui, inclusive quando o perfil de cobrança já foi criado.
2. O backend valida e salva esse e-mail na conta autenticada, rejeitando colisão com outra conta; um cliente Asaas existente recebe atualização do e-mail por `PUT /customers/{id}` antes do checkout.
3. Uma tentativa `review` de empresa adicional só é retomada na mesma empresa/checkout se não tiver ID de checkout externo e se a leitura do cliente Asaas confirmar e-mail vazio, que foi a causa reproduzida da rejeição. Caso contrário, a tentativa permanece em análise para conciliação manual.
4. O retry tem nova chave de idempotência e não cria outra empresa. Erros de resposta ambígua continuam bloqueados para revisão.
5. Novas compras de número extra também pedem e-mail quando necessário.

## Matriz de testes

- Conta legada sem e-mail: erro antes de criar nova empresa se o campo estiver ausente.
- Conta legada com e-mail informado: atualização do cliente Asaas, checkout único e e-mail salvo.
- Tentativa `review` com cliente sem e-mail: reaproveita checkout e empresa.
- Tentativa `review` com cliente já com e-mail: bloqueia para suporte, sem nova cobrança.
- E-mail duplicado: rejeita sem alterar outra conta.
- Regressões: suíte backend, frontend, build, checagem pública e estado dos containers.

## Recuperação

Sem migration. Manter backup e versões anteriores das imagens. Se o retry Asaas for inconclusivo, preservar `review` e conciliar manualmente; não apagar registros de cobrança.
