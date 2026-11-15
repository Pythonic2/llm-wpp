# Cadastro público, assinatura Asaas e limites por empresa

Status: especificação de implementação; nenhuma operação real de cobrança autorizada. Complementa `specs/commercial-finance` sem misturar vendas do cliente com faturamento do SaaS.

## Reuso

- Autenticação Django com sessão/CSRF, `User`, `Company`, `Membership`, isolamento por empresa, frontend React e Embedded Signup da Meta.
- Infraestrutura atual do Raspberry para backend, worker, frontend, Redis e PostgreSQL. Cloud Run do CRM não existe; só Raspberry é alvo autorizado anteriormente.
- O módulo comercial registra vendas do cliente final e **não** será usado para cobrar assinaturas Gest Tech.

## Fluxos e preços propostos

1. Usuário público preenche nome, CPF/CNPJ, contato, e-mail, endereço, nome do negócio e senha. Validar CPF/CNPJ, e-mail e senha. Criar conta inativa e empresa pendente, com uma tentativa de checkout identificável. Enviar link de confirmação de e-mail assinado com validade de 48 horas, com reenvio limitado. Dados sensíveis enviados ao Asaas pelo backend; não enviar API Key ao navegador e não registrar CPF/CNPJ em logs.
2. Plano da primeira empresa: R$ 249,90/mês com 2 números WhatsApp. Depois de pagamento confirmado por Webhook autenticado, ativar a empresa; ativar a conta quando o e-mail também tiver sido confirmado. Retorno do navegador à página de status não ativa acesso por si só.
3. Usuário owner pode solicitar mais uma empresa por R$ 199,90/mês por empresa. Criar empresa pendente com dados próprios e ativar somente após pagamento. Mostrar lista de empresas e botão `+ Adicionar empresa` no seletor, com melhor apresentação visual. Membros de equipe não compram em nome do owner.
4. Cada empresa tem 2 números incluídos. Cada vaga adicional custa R$ 49,90/mês. Criar checkout para vaga adicional; somente após confirmação conceder capacidade. Checar limite em cadastro manual e Embedded Signup antes de consumir código da Meta. Impedir corrida de dois números para a última vaga.
5. Asaas Checkout recorrente com cartão de crédito é a opção desta entrega, confirmada pelo usuário. Pix fica para evolução posterior: assinatura Pix exige pagamento manual a cada mês.
6. O Asaas usa `POST /v3/customers`, `POST /v3/checkouts`, `GET /v3/payments` para conciliação pontual, e Webhooks. `access_token` autentica chamadas de saída. `asaas-access-token` valida Webhooks com token independente. `CHECKOUT_PAID` ativa a compra; eventos de assinaturas e pagamentos acompanham renovação, reembolso/chargeback e inadimplência. Webhooks são idempotentes por `id`.
   - Renovação vencida mantém acesso e capacidade extra por 7 dias corridos a partir do vencimento. Depois, a empresa ou a vaga extra perde acesso/capacidade até a cobrança ser confirmada. Estorno e chargeback revogam imediatamente a compra correspondente. Os números ativos mais antigos preservam as vagas incluídas; um número excedente permanece cadastrado, mas seu envio automático/manual é bloqueado até regularização.
   - Conciliar o ID da assinatura a partir das cobranças do checkout e verificar assinatura e cobranças diariamente como rede de segurança para Webhooks perdidos.
7. `walletId` identifica destino de split e não é necessário para cobrança integral na própria conta; não usar split sem regra comercial explícita.
8. Configuração de Asaas somente em `.env` ignorado pelo Git: ambiente sandbox por padrão, chave e token de Webhook separados. Nunca copiar valores para documentos ou testes. Preços fixados no servidor com Decimal e snapshots na compra; frontend apenas exibe.

## Critérios de aceitação

- Cadastro pendente nunca acessa CRM antes de evento pago autenticado e confirmação do e-mail.
- Webhook duplicado/reordenado não duplica empresa, vaga ou concessão de acesso.
- Usuário de outra empresa não consegue comprar ou usar capacidade alheia.
- Terceiro número é rejeitado antes do pagamento, tanto por API manual quanto pela Meta; dois primeiros seguem gratuitos.
- Compra adicional mantém dados e arquivos isolados entre empresas.
- Falha da API Asaas não ativa conta nem empresa e permite retomada segura sem cobrar em duplicidade.
- Cancelamentos, estornos, chargebacks e renovação inadimplente têm política explícita e auditada antes de produção.
- Fluxo de sandbox validado com testes automatizados e teste manual sem pagamento real.

## Fontes oficiais consultadas em 15/11/2026

- https://docs.asaas.com/docs/authentication-2
- https://docs.asaas.com/docs/criando-um-cliente
- https://docs.asaas.com/docs/checkout-with-subscription-recurring
- https://docs.asaas.com/docs/checkout-link-and-customer-redirection
- https://docs.asaas.com/docs/checkout-events
- https://docs.asaas.com/docs/webhooks-faq
- https://docs.asaas.com/docs/payment-events
