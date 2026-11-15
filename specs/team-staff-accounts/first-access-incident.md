# Primeiro acesso sem empresa: usuário xico

Em 15/11/2026, após trocar a senha inicial, o usuário `xico` chegou à seleção de empresa sem opções. Consulta de leitura no CRM confirmou um vínculo de atendente apenas com `docile`. Essa empresa está com cobrança inicial expirada e estado `pending`; a API corretamente não a inclui entre empresas disponíveis. A conta e a troca de senha foram concluídas.

## Correção de código

- O cadastro direto rejeita empresa indisponível mesmo quando o administrador é superusuário. O bypass administrativo de `_company_for_request` havia permitido criar o vínculo com `docile` sem assinatura ativa.
- A tela vazia informa que o acesso e a assinatura precisam ser verificados.
- Testes: 12 testes de equipe/backend, 28 frontend, lint e build. Sem migration.

## Resolução da conta existente

É necessária a escolha explícita do proprietário: atribuir `xico` a uma empresa já disponível ou concluir o pagamento da empresa `docile`. Não alterar o estado da cobrança para liberar acesso. A mudança de vínculo real depende da resposta do proprietário. Se concedida, preservar o vínculo histórico e adicionar apenas a empresa escolhida, com o papel de atendente.

## Publicação e recuperação

Atualizar imagens de backend e frontend no Raspberry, preservando as tags anteriores em backup. Não há alteração de esquema. Se houver falha, restaurar as imagens anteriores; o estado de cobrança e os vínculos permanecem inalterados.
