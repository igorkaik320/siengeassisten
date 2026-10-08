# Rodando online (qualquer IA acessa pela URL)

O servidor fala **MCP via Streamable HTTP** em `https://SEU-DOMINIO/mcp` e exige
`Authorization: Bearer <token>`. `GET /health` responde sem token (não expõe dados).

## 1. Gerar o token de acesso
    python -c "import secrets; print(secrets.token_urlsafe(48))"
Guarde em um gerenciador de senhas. Para trocar de token sem parar tudo, coloque dois
em `MCP_AUTH_TOKENS` separados por vírgula, atualize os clientes e remova o antigo.

## 2. Subir em um provedor com HTTPS automático (exemplos: Render, Railway, Fly.io)
1. Suba esta pasta para um repositório **privado** (o `.gitignore` já exclui `.env`).
2. Crie um serviço web a partir do repositório (ele usa o `Dockerfile`).
3. Cadastre as variáveis de ambiente **no painel do provedor** (nunca no código):

| Variável | Valor |
|---|---|
| SIENGE_SUBDOMAIN | subdomínio da empresa |
| SIENGE_USERNAME / SIENGE_PASSWORD | usuário de API do Sienge (só leitura) |
| MCP_TRANSPORT | http |
| MCP_AUTH_TOKENS | o token gerado |
| TRUSTED_PROXY_HOPS | 1 (o provedor é o único proxy na frente). Use 0 se expuser o servidor direto, sem proxy |

4. Health check: caminho `/health`.
5. Teste: `curl https://SEU-DOMINIO/health` deve devolver `{"status":"ok"}`.
   Evite planos gratuitos que "dormem": a primeira chamada demora e pode dar timeout.
   Confira o preço atual no provedor (planos de entrada costumam ser poucos dólares/mês).

Alternativa: um VPS com Docker e um proxy com HTTPS (Caddy ou Nginx) na frente.
**Nunca exponha em HTTP puro.**

## 3. Conectar cada IA
URL do servidor: `https://SEU-DOMINIO/mcp`

| Cliente | Como |
|---|---|
| Claude Code | `claude mcp add --transport http sienge https://SEU-DOMINIO/mcp --header "Authorization: Bearer TOKEN"` |
| Claude Desktop | via `mcp-remote` com o header (veja abaixo) |
| Gemini CLI | `gemini mcp add --transport http sienge https://SEU-DOMINIO/mcp --header "Authorization: Bearer TOKEN"` |
| ChatGPT | Configurações > Conectores > modo desenvolvedor > conector personalizado. Escolha autenticação por token/chave de API, se a sua versão oferecer |
| APIs (Anthropic, OpenAI, Gemini) | declare o servidor MCP remoto na requisição, passando o token |
| claude.ai (navegador/celular) | conectores personalizados usam **OAuth**; este servidor ainda usa token fixo (veja limitações) |

Claude Desktop (`claude_desktop_config.json`):

    {
      "mcpServers": {
        "sienge": {
          "command": "npx",
          "args": ["-y", "mcp-remote", "https://SEU-DOMINIO/mcp",
                   "--header", "Authorization: Bearer ${SIENGE_MCP_TOKEN}"],
          "env": { "SIENGE_MCP_TOKEN": "SEU-TOKEN" }
        }
      }
    }

O suporte a conectores muda rápido em cada produto e depende do plano; confira a
documentação atual de cada um.

## Limitações atuais
- **Um token compartilha todo o acesso.** Quem tiver o token consulta tudo que o usuário de
  API do Sienge enxerga. Não há permissão por pessoa.
- **claude.ai web/celular e ChatGPT com OAuth** exigem um fluxo OAuth que este servidor não
  implementa. Dá para adicionar depois, com um provedor de identidade (Auth0, Clerk, etc.).
- Os dados consultados vão para a IA usada (OpenAI, Google, Anthropic). Valide isso com a
  empresa (LGPD, contratos, política de dados) antes de ligar.

## Segurança: checklist
- [ ] Usuário de API do Sienge só com permissão de consulta
- [ ] Repositório privado e sem senhas no código
- [ ] Token longo, guardado em gerenciador de senhas, trocado se alguém sair da equipe
- [ ] Só HTTPS
- [ ] Olhar os logs do provedor de vez em quando (cada chamada registra recurso e status, nunca token)
