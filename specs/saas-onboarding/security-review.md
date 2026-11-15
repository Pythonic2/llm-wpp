# Revisão de segurança — cadastro e cobrança

Data: 15/11/2026. Escopo: API de cadastro, checkout, Webhook, isolamento por empresa e limite de números. Testes externos e produção não foram acessados.

## Superfícies e controles verificados

- API Key Asaas exclusivamente no backend/worker; `ASAAS_BASE_URL` limitado aos dois hosts oficiais e produção exige opt-in. Erros do provedor são sanitizados. `.env` permanece fora do Git.
- Webhook exige token independente no cabeçalho `asaas-access-token`, compara em tempo constante, limita corpo a 128 KiB, deduplica por ID e confere ID do checkout e cliente quando presentes. Redirecionamento do navegador não concede acesso.
- API de compra adicional exige sessão, papel owner, CSRF, limite de requisições e chave de idempotência. Empresa pendente/suspensa não aparece na lista nem nas rotas do CRM. Envio de mensagens por número excedente é bloqueado também no serviço de envio.
- Preço e tipo de produto são definidos pelo servidor. O cadastro guarda CPF/CNPJ e endereço apenas até a criação do cliente no Asaas; depois limpa esses campos locais.

## Achados e pendências

1. **Alto — fluxo Asaas ainda sem teste real de sandbox.** Os contratos de Checkout, Webhook e vínculo com assinatura foram validados contra documentação e mocks; é necessário confirmar payloads e sequência reais, incluindo checkout pago, renovação vencida, pagamento tardio, estorno e expiração.
2. **Alto — envio de e-mail ainda sem provedor configurado.** O código exige confirmação de e-mail, tem link assinado de 48 horas, reenvio limitado e bloqueia acesso pago até a confirmação. Sem SMTP real, o usuário fica impedido de entrar; configurar e validar entrega antes de ativar o cadastro público.
3. **Médio — dados pessoais temporários em claro.** Se o provedor falha antes de registrar o cliente, CPF/CNPJ e endereço permanecem no banco local para retomada/suporte. Definir retenção curta e limpeza de cadastros pendentes abandonados, ou criptografar em repouso.
4. **Médio — tentativa ambígua de checkout requer suporte.** Após timeout de criação, a compra entra em `review` para evitar assinatura duplicada. Conciliação manual ou fluxo seguro de recuperação ainda precisa de interface operacional.
5. **Médio — limite de requisições por processo.** O throttle padrão do DRF usa cache local se não houver cache compartilhado configurado. Configurar Redis para garantir limite consistente entre réplicas.
6. **Médio — usuários legados sem limite.** Empresas existentes ficam com estado `legacy` e capacidade ilimitada para preservar contratos vigentes. Definir migração comercial individual antes de prometer limite universal de dois números.

## Testes relevantes

`tests/test_billing_onboarding.py` cobre Webhook inválido, idempotência, isolamento de compra, empresa pendente, sete dias de carência, número excedente, conciliação de assinatura e estorno. A suíte completa de backend e frontend foi executada localmente; detalhes em `deployment-readiness.md`.
