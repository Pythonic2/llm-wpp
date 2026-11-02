# Gest Tech — backend SaaS

Backend Django/DRF multiempresa para atendimento por WhatsApp Cloud API, respostas com Gemini e RAG em PostgreSQL/pgvector. A interface React/TypeScript vive no projeto independente `../frontend-llm-wpp` e consome a API administrativa versionada.

## Arquitetura

- Django + DRF; webhook público da Meta `GET/POST /api/webhooks/whatsapp/` e API administrativa autenticada em `/api/v1/`.
- WhiteNoise serve os arquivos estáticos do Admin no processo web.
- PostgreSQL 16 com pgvector e índice HNSW; Redis + Celery para processamento assíncrono.
- SDK oficial `google-genai` para geração e embeddings projetados em 768 dimensões, compatíveis com o índice HNSW.
- Empresas, associações de usuário, múltiplos números com toggles de IA, contatos, conversas, mensagens, bases, documentos e chunks.
- Conversas incluem `status`, `lead_stage` e `lead_data` para qualificação futura.
- Cada empresa pode personalizar a persona, o tom e a abordagem da IA em Configurações; as regras de segurança e uso do contexto permanecem fixas no servidor.

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

Defina `GEMINI_API_KEY` e `GEMINI_CHAT_MODEL`. A busca cosseno sempre filtra `company_id` e bases ativas, podendo ainda filtrar uma base específica. O prompt exige português brasileiro, resposta objetiva e uso exclusivo do contexto.

O consumo pode ser ajustado por `GEMINI_MAX_OUTPUT_TOKENS` (respostas),
`GEMINI_ANALYSIS_MAX_OUTPUT_TOKENS` (JSON da análise comercial),
`GEMINI_THINKING_LEVEL`, `GEMINI_HISTORY_MAX_MESSAGES`, `GEMINI_RAG_TOP_K` e
`GEMINI_RAG_CHUNK_MAX_CHARS`. Os preços por milhão de tokens de entrada, saída
e raciocínio são opcionais e configurados por `GEMINI_INPUT_COST_PER_MILLION`,
`GEMINI_OUTPUT_COST_PER_MILLION` e `GEMINI_THINKING_COST_PER_MILLION`; com zero,
os logs registram tokens e latência sem atribuir custo.

Não coloque chaves no banco ou Git. Reinicie backend e worker ao alterar variáveis.

## Biblioteca de mídias e áudio

A API permite categorias hierárquicas e upload privado de imagens JPEG/PNG/WebP/GIF,
vídeos MP4/3GPP e PDFs. Os arquivos são indexados pelos metadados com os embeddings
existentes. Quando o cliente pede foto, vídeo, catálogo, PDF, manual ou tutorial, o
backend pesquisa apenas as mídias ativas e autorizadas da empresa, oferece até
`MEDIA_SEARCH_TOP_K` candidatos ao Gemini e só aceita um ID real dessa lista. O envio
é feito pela Cloud API, reaproveita o ID da Meta e registra uma mensagem idempotente.

Áudios recebidos são baixados exclusivamente da URL HTTPS retornada pela Meta,
limitados por `WHATSAPP_AUDIO_MAX_BYTES`, transcritos pelo `GEMINI_AUDIO_MODEL` e
encaminhados ao fluxo normal de RAG. O áudio não é persistido; somente a transcrição
fica no histórico.

Por padrão, o storage local existente continua funcionando. Para usar um bucket GCS
privado, instale as dependências do lock, forneça credenciais por Application Default
Credentials e configure:

```env
GCS_BUCKET_NAME=meu-bucket-privado
GCS_SIGNED_URL_EXPIRATION=300
GEMINI_AUDIO_MODEL=
GEMINI_AUDIO_MAX_OUTPUT_TOKENS=1000
WHATSAPP_AUDIO_MAX_BYTES=20971520
MEDIA_LIBRARY_MAX_BYTES=16777216
MEDIA_SEARCH_TOP_K=5
```

`GEMINI_AUDIO_MODEL` vazio reutiliza `GEMINI_CHAT_MODEL`. O bucket deve manter
Public Access Prevention habilitado e a conta da aplicação precisa apenas das
permissões de leitura, criação e exclusão de objetos. Downloads do CRM passam por
endpoint autenticado e não expõem o caminho interno no JSON.

## Testes

Os testes de integração usam PostgreSQL/pgvector e mocks para Gemini e WhatsApp. A aplicação lê exclusivamente `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DB_SCHEMA`, `DB_SSLMODE`, `DB_CHANNEL_BINDING`, `DB_CONNECT_TIMEOUT` e `DB_CONN_MAX_AGE`; não existe fallback para banco local nem `DATABASE_URL`. O entrypoint cria o schema configurado antes das migrações. No Neon, habilite a extensão `vector` uma vez no banco.

```bash
.venv/bin/python -m pytest -q
```

Eles cobrem assinatura/verificação do webhook, idempotência, isolamento do Admin, relações cruzadas, contexto Gemini e envio à Cloud API.

## Configuração externa pendente

O fluxo ponta a ponta precisa de Gemini API Key; app Meta/WhatsApp com número de testes, token e App Secret; callback HTTPS; e destinatário autorizado na Meta. Sem isso, banco, Admin e testes mockados continuam utilizáveis.

Fora do escopo: cobrança e CRM completo. Consulte [API.md](./API.md) para os contratos administrativos e o [frontend independente](../frontend-llm-wpp/README.md) para execução e validação da interface.

## Implantar pelo Portainer

Use **Repository**, branch `main` e o caminho `docker-compose.yml`. Esse arquivo sobe backend, worker, PostgreSQL e Redis sem depender da pasta local `../frontend-llm-wpp`.

Antes do deploy, crie a rede externa `pi_default` no servidor e cadastre no Portainer as variáveis `DB_*` do Neon, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS` e `CSRF_TRUSTED_ORIGINS`. Para o domínio de produção, use por exemplo `DJANGO_ALLOWED_HOSTS=llm-wpp.gestcloud.com.br` e `CSRF_TRUSTED_ORIGINS=https://llm-wpp.gestcloud.com.br`. Mantenha `DJANGO_DEBUG=false`, `SESSION_COOKIE_SECURE=true` e `CSRF_COOKIE_SECURE=true`. Ative HSTS e redirecionamento HTTPS somente quando todo o tráfego do domínio já estiver protegido pelo proxy. Cadastre também as credenciais Gemini e WhatsApp como variáveis da stack; nunca coloque valores reais no repositório.

O frontend de produção encaminha `/api/` ao backend pela rede Docker, mantendo navegador e API na mesma origem. `FRONTEND_ORIGINS` é necessário apenas quando o navegador chama o backend diretamente a partir de outra origem, como no Vite local; nesse caso, informe a origem exata, incluindo esquema e porta.
