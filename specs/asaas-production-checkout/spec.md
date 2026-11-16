# Asaas: checkout correto e passagem segura para produção

## Problema e evidências

O backend gera o checkout na API sandbox e grava sempre um link do site de produção. Um checkout sandbox recente abre no host sandbox, mas retorna "Página não encontrada" no host de produção. A chave de produção está nas variáveis salvas do Portainer, porém o Compose e o container em execução ainda têm chave e URL sandbox. A conta de produção não possui webhook do CRM.

## Objetivo

Permitir assinaturas reais com cartão após validação, preservando a distinção entre compras sandbox e produção e ativando acesso somente por evento autenticado da mesma origem.

## Regras e cenários

- Dada uma configuração sandbox, quando um checkout é criado, o link aponta para `sandbox.asaas.com`.
- Dada uma configuração produção, quando um checkout é criado, o link aponta para `asaas.com`.
- Dada uma chave de um ambiente e URL de API de outro, a compra falha sem criar cliente ou checkout externo.
- Dada uma compra antiga sandbox, quando o ambiente muda, seu link e seu status permanecem vinculados ao sandbox; ela não pode ser usada para ativar acesso em produção.
- Dado um cadastro pendente sandbox, quando o mesmo titular retoma em produção com senha e dados válidos, o CRM cria um checkout novo para a mesma empresa sem reutilizar o cliente externo sandbox. Uma compra já paga exige revisão humana.
- Dado um webhook com token inválido ou referente a outro ambiente, o CRM não ativa empresa ou número.
- Dado um webhook duplicado, o efeito ocorre uma só vez.
- Dado um pagamento real sem webhook, o CRM mantém acesso pendente; a operação deve ser conciliável.

## Critérios e verificação

1. URL de checkout segue o ambiente, inclusive compras sandbox já registradas: testes de API e banco.
2. Chave/URL incoerentes bloqueiam criação: teste unitário.
3. IDs externos sandbox não são usados em produção; retomada cria compra nova sem segunda empresa: teste de API.
4. Webhook sandbox não ativa compra de produção nem vice-versa; duplicação é idempotente: teste de API.
5. Reconciliação periódica só consulta compras do ambiente ativo: teste da tarefa.
6. Webhook de produção existente e saudável antes da liberação; sem segredo em Git/logs: verificação operacional.
7. Frontend não anuncia sandbox após a passagem: build e inspeção da configuração publicada.

## Segurança e recuperação

Não guardar chave ou token em código. CPF/endereço são enviados somente ao Asaas e apagados após cadastro externo. Asaas não deve ser consultado com ID de outro ambiente. Evento autenticado continua sujeito à checagem de referência, cliente e ambiente. Antes da virada: backup do Compose e banco, registrar imagens atuais; se falhar, restaurar configuração sandbox e imagens anteriores sem apagar registros de produção, e conciliar manualmente qualquer pagamento real já iniciado.
