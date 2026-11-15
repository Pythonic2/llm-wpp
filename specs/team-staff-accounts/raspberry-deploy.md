# Deploy no Raspberry — 15/11/2026

Estado da migration: `saas.0016` aplicada no Raspberry, mantendo Asaas sandbox. Cloud Run fora do escopo. A versão de equipe descrita abaixo foi publicada inicialmente, mas não é a versão de backend atualmente executada após a sincronização GitOps do Portainer.

## Versões

- Backend, worker e agendador: `ghcr.io/pythonic2/llm-wpp:pi-staff-20261115-d0ec1f78`.
- Frontend: `ghcr.io/pythonic2/llm-wpp-frontend:pi-staff-sandbox-20261115-4561ba38`.
- Banco Neon, esquema `crm`: `saas.0016_staff_password_state` aplicada após confirmar que era a única migration pendente.
- Backup imediatamente anterior: `/home/pi/gesttech-staff-20261115/backup/pre-staff-0016.dump`, formato customizado verificado por `pg_restore --list`, modo 600. Manifests e arquivos de ambiente anteriores estão no mesmo diretório privado.

## Verificação

- Antes do deploy: 115 testes Django em PostgreSQL pgvector local descartável; 27 testes frontend, lint e build; `manage.py check`, `makemigrations --check --dry-run` e `git diff --check`.
- No Raspberry: build ARM64 das duas imagens; `manage.py check` sem problemas; `showmigrations` confirma 0016; backend, worker, agendador, Redis e frontend em execução.
- Público: `/` e `/team` respondem 200, `/api/v1/auth/csrf/` responde 200. As rotas de equipe e troca de senha respondem 403 sem autenticação, como esperado. Nenhuma conta de funcionário de teste foi criada em produção.
- Um redeploy do Portainer substituiu o backend pela tag antiga. A atualização manual dos arquivos `stack.env` não persistiu na configuração GitOps. Em 15/11/2026, a imagem de backend observada voltou a `sha-51c9e4b5111ab03b4c04bcdaeee42a56e87869cc`; o hotfix de equipe ainda precisa de sincronização persistente das tags. A correção separada da chave WhatsApp foi aplicada via Compose GitOps e está registrada em `specs/whatsapp-embedded-signup/raspberry-key-recovery.md`.

## Recuperação e pendência

Em falha, voltar às imagens registradas em `specs/saas-onboarding/raspberry-staging.md` usando os manifests e ambientes preservados em `/home/pi/gesttech-staff-20261115/backup`. Manter a tabela 0016 no banco; a migration é aditiva. As variáveis de tag salvas na interface do Portainer podem continuar com valores antigos e devem ser alinhadas antes de usar “Update the stack” na UI. A entrega real do e-mail e o primeiro acesso ainda precisam de validação com uma conta autorizada, sem usar credenciais de clientes existentes.
