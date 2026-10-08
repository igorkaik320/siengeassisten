"""
Servidor MCP do Sienge (SOMENTE LEITURA).

Baseado nas documentações oficiais (swagger) da API do Sienge:
Empresas, Empreendimentos/Obras, Centro de Custos, Credores, Contas a Pagar (bills),
Contas a Receber, Bens imóveis e Bulk-data (parcelas a pagar / a receber),
além de pedidos de compra, solicitações e notas fiscais de compra.

Só faz requisições GET. Não existe ferramenta que autorize, reprove, crie ou
altere dados. Dados bancários, Pix e códigos de pagamento são bloqueados.

Variáveis de ambiente: SIENGE_SUBDOMAIN, SIENGE_USERNAME, SIENGE_PASSWORD
"""
import hmac
import json
import os
import sys
import functools
import time
from collections import defaultdict, deque
from typing import Any, Optional

import anyio
import httpx
from mcp.server.fastmcp import FastMCP

HTTP_HOST = os.environ.get("HOST", "0.0.0.0")
HTTP_PORT = int(os.environ.get("PORT", "8000"))  # Render/Railway/Fly definem PORT

# stateless_http + json_response: simples e estável atrás de proxies/balanceadores
mcp = FastMCP(
    "sienge",
    host=HTTP_HOST,
    port=HTTP_PORT,
    stateless_http=True,
    json_response=True,
)



def _threaded_tool(fn):
    """Registra a ferramenta rodando em thread: uma consulta lenta ao Sienge
    (até SIENGE_TIMEOUT s) não trava as demais conexões no modo online."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))

    return mcp.tool()(wrapper)


# ----------------------------------------------------------------------------
# Configuração
# ----------------------------------------------------------------------------
SUBDOMAIN = os.environ.get("SIENGE_SUBDOMAIN", "").strip()
USERNAME = os.environ.get("SIENGE_USERNAME", "").strip()
PASSWORD = os.environ.get("SIENGE_PASSWORD", "")
BASE_HOST = os.environ.get("SIENGE_BASE_URL", "https://api.sienge.com.br").rstrip("/")
API_PATH = os.environ.get("SIENGE_API_PATH", "/{subdomain}/public/api/v1")
BULK_PATH = os.environ.get("SIENGE_BULK_PATH", "/{subdomain}/public/api/bulk-data/v1")
TIMEOUT = float(os.environ.get("SIENGE_TIMEOUT", "60"))
MAX_LIMIT = int(os.environ.get("SIENGE_MAX_LIMIT", "200"))  # máximo da API: 200
MAX_CHARS = int(os.environ.get("SIENGE_MAX_CHARS", "60000"))

# Recursos liberados (primeiro trecho do caminho), por base da API.
V1_RESOURCES = {
    "accounts-receivable",
    "bills",
    "companies",
    "cost-centers",
    "creditors",
    "customers",
    "enterprises",
    "patrimony",
    "purchase-invoices",
    "purchase-orders",
    "purchase-requests",
    "supply-contracts",
    "units",
}
V1_RESOURCES |= {
    r.strip() for r in os.environ.get("SIENGE_EXTRA_RESOURCES", "").split(",") if r.strip()
}
BULK_RESOURCES = {"income", "outcome"}

# Trechos de caminho bloqueados mesmo em consultas GET (dados bancários, Pix,
# códigos de pagamento e download de arquivos).
BLOCKED_SEGMENTS = {
    "bank-informations",
    "pix-informations",
    "payment-information",
    "attachments",
}

_calls = {"total": 0, "since": time.strftime("%Y-%m-%d %H:%M:%S")}


def _base_url(bulk: bool = False) -> str:
    return BASE_HOST + (BULK_PATH if bulk else API_PATH).format(subdomain=SUBDOMAIN)


def _config_error() -> Optional[str]:
    missing = [
        n
        for n, v in (
            ("SIENGE_SUBDOMAIN", SUBDOMAIN),
            ("SIENGE_USERNAME", USERNAME),
            ("SIENGE_PASSWORD", PASSWORD),
        )
        if not v
    ]
    if missing:
        return "Configuração incompleta. Faltam as variáveis: " + ", ".join(missing)
    return None


def _validate_path(path: str, bulk: bool) -> Optional[str]:
    if not path.startswith("/"):
        return "O caminho deve começar com '/', por exemplo: /purchase-orders"
    if ".." in path or "?" in path or "#" in path or "//" in path:
        return "Caminho inválido. Passe filtros no parâmetro 'filtros', não na URL."
    parts = path.strip("/").lower().split("/")
    allowed = BULK_RESOURCES if bulk else V1_RESOURCES
    if parts[0] not in allowed:
        return f"Recurso '{parts[0]}' não liberado. Liberados: " + ", ".join(sorted(allowed))
    blocked = BLOCKED_SEGMENTS.intersection(parts)
    if blocked:
        return "Trecho bloqueado por segurança (dados bancários/Pix/pagamento/anexos): " + ", ".join(
            sorted(blocked)
        )
    return None


def _csv(value: Any) -> Any:
    """A API do Sienge (swagger 2.0) espera listas separadas por vírgula."""
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value)
    return value


def _get(path: str, params: Optional[dict] = None, bulk: bool = False) -> str:
    """Faz um GET no Sienge e devolve texto pronto para o Claude."""
    err = _config_error() or _validate_path(path, bulk)
    if err:
        return err

    clean = {}
    for k, v in (params or {}).items():
        if v is None or v == "":
            continue
        clean[k] = ("true" if v else "false") if isinstance(v, bool) else v
    url = _base_url(bulk) + path
    try:
        with httpx.Client(timeout=TIMEOUT, auth=httpx.BasicAuth(USERNAME, PASSWORD)) as client:
            resp = client.get(url, params=clean, headers={"Accept": "application/json"})
    except httpx.HTTPError as e:
        return f"Erro de conexão com o Sienge: {type(e).__name__}. Verifique a internet e SIENGE_BASE_URL."

    _calls["total"] += 1
    print(f"[sienge-mcp] GET {path} -> {resp.status_code}", file=sys.stderr)

    if resp.status_code == 401:
        return "401: usuário ou senha da API inválidos."
    if resp.status_code == 403:
        return (
            "403: credenciais válidas, mas o usuário de API não tem permissão para este "
            "recurso. Ajuste as permissões no módulo de Integração do Sienge."
        )
    if resp.status_code == 404:
        return "404: não encontrado. Confira o subdomínio, o caminho da API e o ID informado."
    if resp.status_code == 429:
        return "429: limite de requisições da API do Sienge atingido. Tente mais tarde."
    if resp.status_code >= 400:
        return f"Erro {resp.status_code} do Sienge: {resp.text[:600]}"

    try:
        text = json.dumps(resp.json(), ensure_ascii=False, indent=1)
    except ValueError:
        text = resp.text
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n... [cortado: use filtros mais estreitos ou limite/offset menores]"
    return text


def _page(limite: int, offset: int) -> dict:
    return {"limit": max(1, min(limite, MAX_LIMIT)), "offset": max(0, offset)}


# ----------------------------------------------------------------------------
# Geral
# ----------------------------------------------------------------------------
@_threaded_tool
def verificar_conexao() -> str:
    """Testa a configuração e a credencial do Sienge (1 chamada leve em /companies)."""
    err = _config_error()
    if err:
        return err
    result = _get("/companies", {"limit": 1})
    ok = not result[:3].isdigit()
    return (
        ("Conexão OK." if ok else "Falha na conexão:")
        + f"\nURL base: {_base_url()}\n"
        + f"Chamadas nesta sessão: {_calls['total']} (desde {_calls['since']})\n"
        + ("" if ok else result)
    )


@_threaded_tool
def listar_recursos_permitidos() -> str:
    """Lista os recursos que este servidor pode consultar (somente leitura)."""
    return (
        "API v1 (GET): " + ", ".join(sorted(V1_RESOURCES))
        + "\nBulk-data (GET): " + ", ".join(sorted(BULK_RESOURCES))
        + "\nBloqueados por segurança: " + ", ".join(sorted(BLOCKED_SEGMENTS))
    )


@_threaded_tool
def consultar(
    caminho: str,
    filtros: Optional[dict[str, Any]] = None,
    limite: int = 20,
    offset: int = 0,
) -> str:
    """Consulta genérica (GET) na API v1 do Sienge, em recursos liberados.

    caminho: começa com '/', ex.: '/purchase-orders' ou '/purchase-orders/123/items'.
    filtros: parâmetros da API (nomes da documentação oficial). Listas viram CSV.
    """
    params = {k: _csv(v) for k, v in (filtros or {}).items()}
    params.update(_page(limite, offset))
    return _get(caminho, params)


# ----------------------------------------------------------------------------
# Empresas, obras e centros de custo
# ----------------------------------------------------------------------------
@_threaded_tool
def listar_empresas(limite: int = 100, offset: int = 0) -> str:
    """Lista as empresas cadastradas (id, nome, nome fantasia, CNPJ)."""
    return _get("/companies", _page(limite, offset))


@_threaded_tool
def listar_obras(
    empresa_id: Optional[int] = None,
    tipo: Optional[int] = None,
    somente_integradas_orcamento: Optional[bool] = None,
    limite: int = 100,
    offset: int = 0,
) -> str:
    """Lista empreendimentos (obras/centros de custo).

    tipo: 1=Obra e Centro de custo, 2=Obra, 3=Centro de custo, 4=Centro de custo associado a obra.
    """
    p = _page(limite, offset)
    p.update(
        companyId=empresa_id,
        type=tipo,
        onlyBuildingsEnabledForIntegration=somente_integradas_orcamento,
    )
    return _get("/enterprises", p)


@_threaded_tool
def detalhar_obra(obra_id: int) -> str:
    """Detalhes de um empreendimento/obra."""
    return _get(f"/enterprises/{int(obra_id)}")


@_threaded_tool
def listar_centros_custo(limite: int = 100, offset: int = 0) -> str:
    """Lista centros de custo (id, nome, empresa, CNPJ)."""
    return _get("/cost-centers", _page(limite, offset))


@_threaded_tool
def detalhar_centro_custo(centro_custo_id: int) -> str:
    """Detalhes de um centro de custo, incluindo setores/obras e responsáveis."""
    return _get(f"/cost-centers/{int(centro_custo_id)}")


# ----------------------------------------------------------------------------
# Contas a pagar (títulos)
# ----------------------------------------------------------------------------
@_threaded_tool
def listar_titulos_pagar(
    data_inicial: str,
    data_final: str,
    credor_id: Optional[int] = None,
    empresa_id: Optional[int] = None,
    centro_custo_id: Optional[int] = None,
    numero_documento: Optional[str] = None,
    status: Optional[str] = None,
    origem: Optional[str] = None,
    limite: int = 50,
    offset: int = 0,
) -> str:
    """Lista títulos do contas a pagar por DATA DE EMISSÃO (período obrigatório, yyyy-MM-dd).

    status: S=Completo, N=Incompleto, I=Em inclusão.
    origem: AC=Adm. Compras, RA=Adm. Obras, AI=Apuração de Impostos, CO=Comercial,
    CF=Conhecimento de Frete, CP=Contas a Pagar, ME=Contratos e Medições,
    MO=Mão de Obra, DV=Devolução NF, RF=Financiamento, FP=Folha, FE=Frota, GI=Guia, LO, SE.
    """
    p = _page(limite, offset)
    p.update(
        startDate=data_inicial,
        endDate=data_final,
        creditorId=credor_id,
        debtorId=empresa_id,
        costCenterId=centro_custo_id,
        documentNumber=numero_documento,
        status=status,
        originId=origem,
    )
    return _get("/bills", p)


@_threaded_tool
def detalhar_titulo_pagar(titulo_id: int) -> str:
    """Detalhes de um título do contas a pagar."""
    return _get(f"/bills/{int(titulo_id)}")


@_threaded_tool
def parcelas_titulo_pagar(titulo_id: int, limite: int = 100, offset: int = 0) -> str:
    """Parcelas de um título do contas a pagar."""
    return _get(f"/bills/{int(titulo_id)}/installments", _page(limite, offset))


@_threaded_tool
def impostos_titulo_pagar(titulo_id: int) -> str:
    """Impostos de um título do contas a pagar."""
    return _get(f"/bills/{int(titulo_id)}/taxes", _page(100, 0))


@_threaded_tool
def apropriacao_obra_titulo_pagar(titulo_id: int) -> str:
    """Apropriações de obra de um título do contas a pagar."""
    return _get(f"/bills/{int(titulo_id)}/buildings-cost", _page(100, 0))


@_threaded_tool
def parcelas_a_pagar_periodo(
    data_inicial: str,
    data_final: str,
    tipo_data: str,
    indexador_correcao_id: int,
    data_correcao: str,
    empresa_id: Optional[int] = None,
    centros_custo_ids: Optional[list[int]] = None,
    obra_id: Optional[int] = None,
    com_autorizacoes: Optional[bool] = None,
) -> str:
    """Parcelas SEM BAIXA do contas a pagar em um período (Bulk-data /outcome), com valores corrigidos.

    tipo_data: I=emissão do título, D=vencimento da parcela, P=pagamento, B=competência.
    indexador_correcao_id e data_correcao (yyyy-MM-dd) são obrigatórios na API.
    Use períodos curtos: a resposta pode ser grande.
    """
    return _get(
        "/outcome",
        {
            "startDate": data_inicial,
            "endDate": data_final,
            "selectionType": tipo_data,
            "correctionIndexerId": indexador_correcao_id,
            "correctionDate": data_correcao,
            "companyId": empresa_id,
            "costCentersId": _csv(centros_custo_ids),
            "buildingId": obra_id,
            "withAuthorizations": com_autorizacoes,
        },
        bulk=True,
    )


# ----------------------------------------------------------------------------
# Contas a receber
# ----------------------------------------------------------------------------
@_threaded_tool
def listar_titulos_receber(
    cliente_id: int,
    empresa_id: Optional[int] = None,
    centro_custo_id: Optional[int] = None,
    somente_quitados: Optional[bool] = None,
    limite: int = 50,
    offset: int = 0,
) -> str:
    """Lista títulos a receber de UM cliente (cliente_id obrigatório na API)."""
    p = _page(limite, offset)
    p.update(
        customerId=cliente_id,
        companyId=empresa_id,
        costCenterId=centro_custo_id,
        paidOff=somente_quitados,
    )
    return _get("/accounts-receivable/receivable-bills", p)


@_threaded_tool
def parcelas_titulo_receber(titulo_id: int, limite: int = 100, offset: int = 0) -> str:
    """Parcelas de um título a receber."""
    return _get(
        f"/accounts-receivable/receivable-bills/{int(titulo_id)}/installments",
        _page(limite, offset),
    )


@_threaded_tool
def parcelas_a_receber_periodo(
    data_inicial: str,
    data_final: str,
    tipo_data: str,
    empresa_id: Optional[int] = None,
    centros_custo_ids: Optional[list[int]] = None,
    somente_titulos_completos: Optional[bool] = None,
) -> str:
    """Parcelas SEM BAIXA do contas a receber em um período (Bulk-data /income).

    tipo_data: I=emissão do título, D=vencimento da parcela, P=pagamento, B=competência.
    Use períodos curtos: a resposta pode ser grande.
    """
    return _get(
        "/income",
        {
            "startDate": data_inicial,
            "endDate": data_final,
            "selectionType": tipo_data,
            "companyId": empresa_id,
            "costCentersId": _csv(centros_custo_ids),
            "completedBills": "S" if somente_titulos_completos else None,
        },
        bulk=True,
    )


# ----------------------------------------------------------------------------
# Credores, patrimônio e compras
# ----------------------------------------------------------------------------
@_threaded_tool
def buscar_credores(
    busca: Optional[str] = None,
    cpf: Optional[str] = None,
    cnpj: Optional[list[str]] = None,
    limite: int = 20,
    offset: int = 0,
) -> str:
    """Busca credores/fornecedores.

    busca: nome, nome fantasia OU código do credor.
    cpf/cnpj: somente números, sem máscara (cnpj aceita vários).
    """
    p = _page(limite, offset)
    p.update(creditor=busca, cpf=cpf, cnpj=cnpj)
    return _get("/creditors", p)


@_threaded_tool
def detalhar_credor(credor_id: int) -> str:
    """Detalhes de um credor (dados cadastrais; dados bancários/Pix ficam bloqueados)."""
    return _get(f"/creditors/{int(credor_id)}")


@_threaded_tool
def listar_bens_imoveis(
    patrimonio_id: Optional[int] = None,
    centro_custo: Optional[str] = None,
    detalhe: Optional[str] = None,
    situacao: Optional[str] = None,
    limite: int = 50,
    offset: int = 0,
) -> str:
    """Lista bens imóveis (patrimônio). situacao: A=Ativo, B=Baixado."""
    p = _page(limite, offset)
    p.update(patrimonyId=patrimonio_id, costCenter=centro_custo, detail=detalhe, situation=situacao)
    return _get("/patrimony/fixed", p)


@_threaded_tool
def listar_pedidos_compra(
    filtros: Optional[dict[str, Any]] = None, limite: int = 20, offset: int = 0
) -> str:
    """Lista pedidos de compra (/purchase-orders).

    filtros: nomes conforme https://api.sienge.com.br/docs/#/purchase-orders-v1
    (ainda não validados aqui com o swagger oficial: se a API reclamar, ajuste o nome).
    """
    params = {k: _csv(v) for k, v in (filtros or {}).items()}
    params.update(_page(limite, offset))
    return _get("/purchase-orders", params)


@_threaded_tool
def detalhar_pedido_compra(pedido_id: int) -> str:
    """Detalhes de um pedido de compra pelo número."""
    return _get(f"/purchase-orders/{int(pedido_id)}")


@_threaded_tool
def itens_pedido_compra(pedido_id: int) -> str:
    """Itens de um pedido de compra."""
    return _get(f"/purchase-orders/{int(pedido_id)}/items")


@_threaded_tool
def listar_solicitacoes_compra(
    filtros: Optional[dict[str, Any]] = None, limite: int = 20, offset: int = 0
) -> str:
    """Lista solicitações de compra (/purchase-requests). Filtros: ver documentação oficial."""
    params = {k: _csv(v) for k, v in (filtros or {}).items()}
    params.update(_page(limite, offset))
    return _get("/purchase-requests", params)


@_threaded_tool
def listar_notas_fiscais_compra(
    filtros: Optional[dict[str, Any]] = None, limite: int = 20, offset: int = 0
) -> str:
    """Lista notas fiscais de compra (/purchase-invoices). Filtros: ver documentação oficial."""
    params = {k: _csv(v) for k, v in (filtros or {}).items()}
    params.update(_page(limite, offset))
    return _get("/purchase-invoices", params)


@_threaded_tool
def listar_contratos_suprimentos(
    filtros: Optional[dict[str, Any]] = None, limite: int = 20, offset: int = 0
) -> str:
    """Lista contratos de suprimentos (/supply-contracts).

    O nome do recurso e os filtros ainda não foram confirmados com o swagger oficial.
    Se der 404/403, confira o nome na documentação e a permissão do usuário de API.
    """
    params = {k: _csv(v) for k, v in (filtros or {}).items()}
    params.update(_page(limite, offset))
    return _get("/supply-contracts", params)


# ----------------------------------------------------------------------------
# Modo online (HTTP) com autenticação por token Bearer
# ----------------------------------------------------------------------------
class BearerAuthMiddleware:
    """ASGI: exige 'Authorization: Bearer <token>' em tudo, exceto GET /health.

    - Compara tokens em tempo constante.
    - Bloqueia temporariamente IPs com muitas tentativas erradas.
    - Nunca registra o token em log.
    """

    MAX_FAILS = 10  # por minuto, por IP
    MAX_TRACKED = 10_000  # teto de IPs rastreados (evita crescer sem limite)

    def __init__(self, app, tokens: list[str]):
        self.app = app
        self.tokens = [t.encode() for t in tokens]
        self.fails: dict[str, deque] = {}
        # Quantos proxies confiáveis existem na frente (Render/Railway/Fly = 1).
        # O IP real é o que o PROXY MAIS PRÓXIMO anexou ao final de X-Forwarded-For;
        # entradas à esquerda podem ser forjadas pelo cliente e são ignoradas.
        self.hops = max(0, int(os.environ.get("TRUSTED_PROXY_HOPS", "1")))

    def _client_ip(self, scope) -> str:
        if self.hops:
            for k, v in scope.get("headers", []):
                if k == b"x-forwarded-for":
                    ips = [x.strip() for x in v.decode(errors="ignore").split(",") if x.strip()]
                    if len(ips) >= self.hops:
                        return ips[-self.hops]
        c = scope.get("client")
        return c[0] if c else "?"

    def _prune(self, now: float):
        if len(self.fails) <= self.MAX_TRACKED:
            return
        for ip in [i for i, q in self.fails.items() if not q or now - q[-1] > 60]:
            del self.fails[ip]
        if len(self.fails) > self.MAX_TRACKED:  # ainda cheio: descarta os mais antigos
            for ip in sorted(self.fails, key=lambda i: self.fails[i][-1])[: self.MAX_TRACKED // 2]:
                del self.fails[ip]

    async def _reply(self, send, status: int, body: bytes, extra=None):
        headers = [(b"content-type", b"application/json")] + (extra or [])
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":  # lifespan etc.
            return await self.app(scope, receive, send)

        if scope["method"] == "GET" and scope["path"] == "/health":
            return await self._reply(send, 200, b'{"status":"ok"}')

        ip = self._client_ip(scope)
        now = time.time()
        q = self.fails.get(ip) or deque()
        while q and now - q[0] > 60:
            q.popleft()
        if not q:
            self.fails.pop(ip, None)
        if len(q) >= self.MAX_FAILS:
            return await self._reply(send, 429, b'{"error":"muitas tentativas"}', [(b"retry-after", b"60")])

        auth = b""
        for k, v in scope.get("headers", []):
            if k == b"authorization":
                auth = v
                break
        supplied = auth[7:].strip() if auth[:7].lower() == b"bearer " else b""
        ok = False
        for t in self.tokens:  # sem 'break' antecipado: tempo constante entre tokens
            ok |= hmac.compare_digest(supplied, t)
        if not ok:
            q.append(now)
            self.fails[ip] = q
            self._prune(now)
            return await self._reply(
                send, 401, b'{"error":"nao autorizado"}', [(b"www-authenticate", b"Bearer")]
            )
        return await self.app(scope, receive, send)


def build_http_app():
    raw_value = os.environ.get("MCP_AUTH_TOKENS", "")
    print(f"[DEBUG] MCP_AUTH_TOKENS value length: {len(raw_value)}", file=sys.stderr)
    print(f"[DEBUG] MCP_AUTH_TOKENS value: '{raw_value[:20]}...' if raw_value else 'EMPTY'", file=sys.stderr)
    tokens = [t.strip() for t in raw_value.split(",") if t.strip()]
    print(f"[DEBUG] Tokens found: {len(tokens)}, lengths: {[len(t) for t in tokens]}", file=sys.stderr)
    if not tokens or any(len(t) < 32 for t in tokens):
        raise SystemExit(
            "Defina MCP_AUTH_TOKENS com um ou mais tokens de pelo menos 32 caracteres "
            "(separados por vírgula). Gere um com: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )
    err = _config_error()
    if err:
        raise SystemExit(err)
    return BearerAuthMiddleware(mcp.streamable_http_app(), tokens)


if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "stdio")  # "stdio" (local) ou "http" (online)
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        import uvicorn

        print(f"[sienge-mcp] online em http://{HTTP_HOST}:{HTTP_PORT}/mcp", file=sys.stderr)
        uvicorn.run(
            build_http_app(),
            host=HTTP_HOST,
            port=HTTP_PORT,
            proxy_headers=False,  # o IP é tratado no middleware (TRUSTED_PROXY_HOPS)
            log_level="warning",
        )
