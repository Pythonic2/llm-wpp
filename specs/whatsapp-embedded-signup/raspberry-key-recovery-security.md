# Revisão de segurança: recuperação da chave WhatsApp

## Superfície e limites de confiança

- Ativo: chave Fernet que abre tokens da Meta vinculados a `WhatsAppNumber`.
- Leitores necessários: processos `app` do backend, worker e agendador no Raspberry. O administrador do host e o daemon Docker mantêm acesso operacional.
- A chave permanece fora do Git, da imagem, do `docker inspect` de variáveis de ambiente e de respostas HTTP. O Compose versionado contém somente o caminho do arquivo.

## Achados e mitigação

- **Alto, configuração anterior:** o Compose injetava a variável vazia. `load_dotenv` não substitui uma variável existente, mesmo vazia; depois do redeploy GitOps, números com token criptografado falhavam ao enviar. Remover essa entrada e montar o arquivo privado corrige a origem.
- **Médio, dependência de host:** a stack depende de `/home/pi/gesttech-secrets/whatsapp.env`. A montagem usa `create_host_path: false`, para falhar explicitamente se o arquivo faltar. O arquivo foi preparado a partir da chave existente, sem geração ou rotação.
- **Baixo, exposição no container:** o arquivo fica legível ao usuário `app` porque a aplicação precisa abrir tokens. Arquivo `0400`, UID 100/GID 101, montagem somente leitura; diretório no host restrito. Não há impressão do valor em logs ou testes.

## Verificações

- `docker compose config` confirmou a montagem nos três serviços e ausência da variável vazia.
- Teste isolado com a imagem atual e UID/GID do app confirmou `load_dotenv=True` e chave Fernet válida, sem rede e sem mostrar o segredo.
- Ainda falta confirmar no container de produção a abertura do token existente e a saúde dos serviços após o redeploy. Nenhuma mensagem será enviada a contato real para teste.
