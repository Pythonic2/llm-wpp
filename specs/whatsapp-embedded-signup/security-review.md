# Revisão de segurança

## Superfícies e controles verificados

- **Browser → API:** sessão autenticada, papel admin e CSRF no POST; `state` armazenado no banco por usuário/empresa, com expiração, bloqueio de linha e consumo único. Evento e IDs do browser são pistas, não autoridade.
- **API → Meta:** o code é trocado apenas no servidor; WABA e número são consultados pela credencial recebida. O backend confere elegibilidade por `is_on_biz_app` e `platform_type` antes de conectar. IDs numéricos e comprimentos são validados.
- **Empresas:** Phone Number ID é único; WABA oficial tem vínculo único por empresa no banco. A API nunca atualiza um número de outra empresa e os testes cobrem acesso cruzado.
- **Credenciais:** token por número criptografado, ausente dos serializadores e dos logs de erro; números desconectados não caem no token global. O PIN do registro normal é derivado da chave secreta estável do servidor e não é retornado.
- **Webhook:** assinatura HMAC verificada sobre bytes originais. Roteamento por Phone Number ID/WABA, ID externo único para mensagens, impressão digital para lotes de histórico e processamento repetível. Eco/manual e histórico não acionam IA. Mensagens apenas com BSUID são guardadas sem resposta automática até existir destinatário válido.

## Riscos residuais e operação

- **Médio — validação real da Meta pendente:** sem App ID/Config ID/chave no ambiente local, o contrato Graph não foi exercitado com um número de teste. Testes usaram mocks fiéis ao formato documentado. Verificar com configuração de teste antes de ativar clientes.
- **Médio — sincronização única:** contato/histórico só podem ser solicitados uma vez; resposta ambígua exige análise dos `request_id` e possível novo onboarding após offboarding. Nenhum retry automático da chamada única.
- **Baixo — anexos históricos:** mensagens de histórico são importadas, mas anexos de mídia antigos não são baixados pelos webhooks de mídia em fases separadas. A limitação fica visível na documentação.
- **Baixo — desconexão local:** remove a credencial e desativa o CRM, mas a saída da Cloud API no Business app é responsabilidade do cliente; `PARTNER_REMOVED` também desativa localmente.
- **Médio — cadastro manual legado:** números criados manualmente antes desta integração ainda podem usar `WHATSAPP_ACCESS_TOKEN` global. O novo fluxo oficial exige token próprio por número e bloqueia WABA/número de outra empresa; a eliminação do fallback legado exige migração específica para não interromper números existentes.

Não houve deploy, migration em banco real, chamada autenticada à Meta, conexão de número real ou alteração de configuração de produção.
