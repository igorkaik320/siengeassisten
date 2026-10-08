# Servidor MCP do Sienge (somente leitura)

Permite que o Claude Desktop consulte a API do Sienge. Baseado nos swaggers oficiais de
Empresas, Obras, Centro de Custos, Credores, Contas a Pagar, Contas a Receber, Bens imóveis
e Bulk-data.

**Só faz GET.** Não existe ferramenta para autorizar, reprovar, criar ou alterar nada.

## 1. Criar o usuário de API no Sienge
No Sienge, módulo de Integração / APIs e Conectores: crie um usuário de API e libere
**apenas permissão de consulta** nos recursos que o gestor precisa. Anote subdomínio,
usuário e senha.

## 2. Instalar (no computador do gestor)
Precisa de Python 3.10+.

    cd sienge-mcp
    python -m venv .venv
    # Windows:  .venv\Scripts\activate      Mac/Linux:  source .venv/bin/activate
    pip install -r requirements.txt

## 3. Conectar ao Claude Desktop
Claude Desktop > Configurações > Desenvolvedor > Editar configuração, e adicione o
conteúdo de `claude_desktop_config.example.json` (ajuste os caminhos e as credenciais).
Reinicie o Claude Desktop.

## 4. Testar
Peça ao Claude: "testa a conexão com o Sienge". Depois: "liste os pedidos de compra
de 2026" ou "quais obras existem?".

## Ferramentas (26)
- **Geral:** verificar_conexao, listar_recursos_permitidos, consultar (GET genérico)
- **Empresas/obras:** listar_empresas, listar_obras, detalhar_obra, listar_centros_custo, detalhar_centro_custo
- **Contas a pagar:** listar_titulos_pagar (período de emissão obrigatório), detalhar_titulo_pagar,
  parcelas_titulo_pagar, impostos_titulo_pagar, apropriacao_obra_titulo_pagar,
  parcelas_a_pagar_periodo (Bulk-data, parcelas sem baixa, valores corrigidos)
- **Contas a receber:** listar_titulos_receber (cliente obrigatório), parcelas_titulo_receber,
  parcelas_a_receber_periodo (Bulk-data)
- **Credores e patrimônio:** buscar_credores (por nome, código, CPF ou CNPJ), detalhar_credor, listar_bens_imoveis
- **Compras:** listar_pedidos_compra, detalhar_pedido_compra, itens_pedido_compra,
  listar_solicitacoes_compra, listar_notas_fiscais_compra, listar_contratos_suprimentos

**Bloqueado de propósito:** dados bancários e Pix de credores, informações de pagamento
(boleto, Pix, tributos) e anexos.

**Ainda não validados com swagger oficial** (você não enviou esses arquivos): filtros de
pedidos de compra, solicitações, notas fiscais e o recurso contratos de suprimentos.
Se mandar os .yaml deles, eu ajusto.

## Se algo falhar
- **404:** confira SIENGE_SUBDOMAIN e o ID consultado. A URL padrão é
  `/{subdomain}/public/api/v1` (e `/{subdomain}/public/api/bulk-data/v1` para Bulk-data);
  se a sua for diferente, ajuste SIENGE_API_PATH / SIENGE_BULK_PATH.
- **403:** a credencial vale, mas falta permissão do usuário de API naquele recurso.
- **401:** usuário ou senha errados.
- **429:** cota diária da API atingida. Use filtros de data e limites menores.
- **Recurso novo:** adicione em SIENGE_EXTRA_RESOURCES (ex.: `recurso1,recurso2`).

## Segurança
- Nunca coloque a senha em repositório. Ela fica só na configuração local.
- Mantenha o usuário de API restrito a leitura.
- Os nomes exatos dos filtros (datas, obra, status) estão em https://api.sienge.com.br/docs/
  e vão no parâmetro `filtros` das ferramentas.

## Para usar no claude.ai (navegador) depois
Rode com `MCP_TRANSPORT=streamable-http` em um servidor seu, com HTTPS e autenticação
na frente, e cadastre como conector personalizado. Não exponha sem autenticação.
