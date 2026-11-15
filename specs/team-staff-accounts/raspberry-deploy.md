# Deploy no Raspberry — 15/11/2026

Estado: publicado em `https://crm.gestcloud.com.br`, mantendo Asaas sandbox. Cloud Run fora do escopo.

## Versões

- Backend, worker e agendador: `ghcr.io/pythonic2/llm-wpp:pi-staff-20261115-d0ec1f78`.
- Frontend: `ghcr.io/pythonic2/llm-wpp-frontend:pi-staff-sandbox-20261115-4561ba38`.
- Banco Neon, esquema `crm`: `saas.0016_staff_password_state` aplicada após confirmar que era a única migration pendente.
- Backup imediatamente anterior: `/home/pi/gesttech-staff-20261115/backup/pre-staff-0016.dump`, formato customizado verificado por `pg_restore --list`, modo 600. Manifests e arquivos de ambiente anteriores estão no mesmo diretório privado.

## Verificação

- Antes do deploy: 115 testes Django em PostgreSQL pgvector local descartável; 27 testes frontend, lint e build; `manage.py check`, `makemigrations --check --dry-run` e `git diff --check`.
- No Raspberry: build ARM64 das duas imagens; `manage.py check` sem problemas; `showmigrations` confirma 0016; backend, worker, agendador, Redis e frontend em execução.
- Público: `/` e `/team` respondem 200, `/api/v1/auth/csrf/` responde 200. As rotas de equipe e troca de senha respondem 403 sem autenticação, como esperado. Nenhuma conta de funcionário de teste foi criada em produção.
- Um redeploy do Portainer substituiu inicialmente o backend pela tag antiga; os arquivos privados `stack.env` das stacks 203/205 dentro do Portainer foram alinhados com as tags novas e `pull_policy=never`, e a stack foi reativada. A estabilidade foi observada após a atualização.

## Recuperação e pendência

Em falha, voltar às imagens registradas em `specs/saas-onboarding/raspberry-staging.md` usando os manifests e ambientes preservados em `/home/pi/gesttech-staff-20261115/backup`. Manter a tabela 0016 no banco; a migration é aditiva. As variáveis de tag salvas na interface do Portainer podem continuar com valores antigos e devem ser alinhadas antes de usar “Update the stack” na UI. A entrega real do e-mail e o primeiro acesso ainda precisam de validação com uma conta autorizada, sem usar credenciais de clientes existentes.
