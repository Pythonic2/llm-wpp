# Gest Tech — backend SaaS

Backend Django/DRF multiempresa para atendimento por WhatsApp Cloud API, respostas com Gemini e RAG em PostgreSQL/pgvector. A interface React/TypeScript vive no projeto independente `../frontend-llm-wpp` e consome a API administrativa versionada.

## Arquitetura

- Django + DRF; webhook público da Meta `GET/POST /api/webhooks/whatsapp/` e API administrativa autenticada em `/api/v1/`.
- WhiteNoise serve os arquivos estáticos do Admin no processo web.
- PostgreSQL 16 com pgvector e índice HNSW; Redis + Celery para processamento assíncrono.
- SDK oficial `google-genai` para geração e embeddings projetados em 768 dimensões, compatíveis com o índice HNSW.
- Empresas, associações de usuário, múltiplos números com toggles de IA, contatos, conversas, mensagens, bases, documentos e chunks.
- Conversas incluem `status`, `lead_stage` e `lead_data` para qualificação futura.

O isolamento é aplicado por `company` em todas as entidades, validação de relações, constraints compostas, filtro por associação no Admin e filtro obrigatório na busca vetorial. Superusuários veem tudo; usuários staff veem somente suas empresas.

## Executar localmente

```bash
cp .env.example .env
# preencha DJANGO_SECRET_KEY; integrações externas podem ficar vazias inicialmente
docker compose up --build -d
docker compose exec backend uv run python manage.py createsuperuser
```

Acesse `http://localhost:5173` para abrir o frontend ou `http://localhost:8123/admin/` para a administração técnica. Crie Empresa, Associação, Número do WhatsApp, Base de conhecimento e Documento. Uploads aceitos: PDF, TXT e CSV. O worker extrai, divide, cria embeddings Gemini e grava no pgvector.

Sem executar a aplicação em container (PostgreSQL/Redis ainda necessários):

```bash
uv sync
cp .env.example .env
uv run python manage.py migrate
uv run python manage.py runserver 8123
# em outro terminal
uv run celery -A gesttech worker -l info
```

## Configurar o número de testes da Meta

No Meta for Developers, use o `Phone number ID` do número de testes para criar um `WhatsAppNumber` no Admin. Preencha no `.env`:

- `WHATSAPP_ACCESS_TOKEN`: token da Cloud API (o temporário serve para testes).
- `WHATSAPP_APP_SECRET`: segredo do app, usado para validar `X-Hub-Signature-256`.
- `WHATSAPP_VERIFY_TOKEN`: valor criado por você e repetido ao cadastrar o webhook.
- Callback HTTPS: `https://SEU-DOMINIO/api/webhooks/whatsapp/`; localmente, exponha a porta 8123 com um túnel HTTPS.
- Assine o campo `messages` do WhatsApp Business Account e cadastre o destinatário permitido no ambiente de testes.

O webhook valida o desafio GET e a assinatura POST, e usa o `wamid` como chave idempotente. A resposta só é enfileirada se o número estiver ativo e com IA ligada.

## Gemini e RAG

Defina `GEMINI_API_KEY`. A geração usa `gemini-3.8-flash`; a busca cosseno sempre filtra `company_id` e bases ativas, podendo ainda filtrar uma base específica. O prompt exige português brasileiro, resposta objetiva e uso exclusivo do contexto.

Não coloque chaves no banco ou Git. Reinicie backend e worker ao alterar variáveis.

## Testes

Os testes de integração usam PostgreSQL/pgvector e mocks para Gemini e WhatsApp:

```bash
docker compose up -d db redis
.venv/bin/python -m pytest -q
```

Eles cobrem assinatura/verificação do webhook, idempotência, isolamento do Admin, relações cruzadas, contexto Gemini e envio à Cloud API.

## Configuração externa pendente

O fluxo ponta a ponta precisa de Gemini API Key; app Meta/WhatsApp com número de testes, token e App Secret; callback HTTPS; e destinatário autorizado na Meta. Sem isso, banco, Admin e testes mockados continuam utilizáveis.

Fora do escopo: cobrança e CRM completo. Consulte [API.md](./API.md) para os contratos administrativos e o [frontend independente](../frontend-llm-wpp/README.md) para execução e validação da interface.
