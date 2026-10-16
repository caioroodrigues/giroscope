"""Banco local DuckDB: criação das tabelas, macros e visões."""
import json

import duckdb

from . import config

SCHEMA = """
CREATE SEQUENCE IF NOT EXISTS seq_importacao START 1;

CREATE TABLE IF NOT EXISTS importacoes (
    id            INTEGER PRIMARY KEY,
    tipo          VARCHAR,          -- ESTOQUE | SNOWFLAKE
    arquivo       VARCHAR,
    hash          VARCHAR,
    importado_em  TIMESTAMP,
    referencia    VARCHAR,          -- data de posição ou meses importados
    linhas_lidas  BIGINT,
    linhas_gravadas BIGINT,
    descartes     JSON,             -- {motivo: linhas}
    controle      JSON,             -- totais de conciliação
    status        VARCHAR,
    mensagem      VARCHAR
);

CREATE TABLE IF NOT EXISTS parametros (
    chave         VARCHAR PRIMARY KEY,
    valor         VARCHAR,
    descricao     VARCHAR,
    atualizado_em TIMESTAMP
);

CREATE TABLE IF NOT EXISTS filiais (
    filial_cod    INTEGER PRIMARY KEY,
    nome_origem   VARCHAR,
    nome_exibicao VARCHAR,
    cnpj          VARCHAR,
    tipo          VARCHAR,          -- LOJA | CD
    ativa         BOOLEAN,
    no_escopo     BOOLEAN,
    atualizado_em TIMESTAMP
);

CREATE TABLE IF NOT EXISTS regioes (
    utc_id         VARCHAR PRIMARY KEY,
    utc_desc       VARCHAR,
    municipios     VARCHAR,
    cod_municipios VARCHAR,
    atuante        BOOLEAN,
    atualizado_em  TIMESTAMP
);

CREATE TABLE IF NOT EXISTS filial_regiao (
    cnpj          VARCHAR PRIMARY KEY,
    utc_id        VARCHAR,
    origem        VARCHAR,          -- AUTOMATICA | MANUAL
    periodo_ref   VARCHAR,
    atualizado_em TIMESTAMP
);

CREATE TABLE IF NOT EXISTS produtos_mercado (
    gtin               VARCHAR PRIMARY KEY,
    apres_cod          VARCHAR,
    descricao          VARCHAR,
    secao_origem       VARCHAR,
    tipo_merc_seg      VARCHAR,
    sigla_tipo_mercado VARCHAR,
    marca              VARCHAR,
    fabricante         VARCHAR,
    corporacao         VARCHAR,
    forma              VARCHAR,
    atc1 VARCHAR, atc2 VARCHAR, atc3 VARCHAR, atc4 VARCHAR,
    molecula           VARCHAR,
    concentracao       VARCHAR,
    cod_rede            VARCHAR,
    marca_propria      VARCHAR,
    periodo_ref        VARCHAR,
    atualizado_em      TIMESTAMP
);

CREATE TABLE IF NOT EXISTS produtos_rede (
    gtin          VARCHAR PRIMARY KEY,
    cod_rede       VARCHAR,
    descricao     VARCHAR,
    laboratorio   VARCHAR,
    atualizado_em TIMESTAMP
);

CREATE TABLE IF NOT EXISTS classificacao_secao (
    secao_origem  VARCHAR PRIMARY KEY,
    grupo         VARCHAR,
    manual        BOOLEAN,
    atualizado_em TIMESTAMP
);

CREATE TABLE IF NOT EXISTS classificacao_produto_manual (
    gtin          VARCHAR PRIMARY KEY,
    grupo         VARCHAR,
    secao         VARCHAR,
    tipo          VARCHAR,
    observacao    VARCHAR,
    atualizado_em TIMESTAMP
);

CREATE TABLE IF NOT EXISTS estoque (
    data_posicao  DATE,
    filial_cod    INTEGER,
    gtin          VARCHAR,
    cod_rede       VARCHAR,
    quantidade    DOUBLE,
    importacao_id INTEGER,
    PRIMARY KEY (data_posicao, filial_cod, gtin)
);

CREATE TABLE IF NOT EXISTS vendas_rede (
    periodo       VARCHAR,          -- AAAAMM
    cnpj          VARCHAR,
    utc_id        VARCHAR,
    gtin          VARCHAR,
    und           DOUBLE,
    fat_venda     DOUBLE,           -- VAL_R$CON (preço de venda)
    custo         DOUBLE,           -- VAL_R$PCF (preço de custo)
    fat_pf        DOUBLE,           -- VAL_R$ (preço de fábrica)
    importacao_id INTEGER,
    PRIMARY KEY (periodo, cnpj, utc_id, gtin)
);

CREATE SEQUENCE IF NOT EXISTS seq_execucao START 1;

CREATE TABLE IF NOT EXISTS analise_execucoes (
    id            INTEGER PRIMARY KEY,
    executado_em  TIMESTAMP,
    periodo       VARCHAR,
    data_posicao  DATE,
    parametros    JSON,
    resumo        JSON
);

CREATE TABLE IF NOT EXISTS analise_loja (
    execucao_id     INTEGER,
    periodo         VARCHAR,
    data_posicao    DATE,
    filial_cod      INTEGER,
    utc_id          VARCHAR,
    gtin            VARCHAR,
    est_loja        DOUBLE,
    est_cd          DOUBLE,
    est_outras_lojas DOUBLE,
    venda_und       DOUBLE,
    venda_fat       DOUBLE,
    venda_dia       DOUBLE,
    cobertura_dias  DOUBLE,
    giro            VARCHAR,     -- Giro | Baixo giro | Sem venda
    conc_und        DOUBLE,
    conc_fat        DOUBLE,
    venda_rede_regiao DOUBLE,
    recomendacao    VARCHAR,
    origem          VARCHAR,     -- CD | Outra loja | Compra
    origem_filial   INTEGER,
    qtd_sugerida    DOUBLE,
    urgencia        VARCHAR,
    score           DOUBLE,
    prioridade      VARCHAR,
    custo_unit      DOUBLE,
    capital         DOUBLE,
    alternativa     VARCHAR,
    justificativa   VARCHAR,
    PRIMARY KEY (periodo, filial_cod, gtin)
);

CREATE TABLE IF NOT EXISTS analise_mix (
    execucao_id     INTEGER,
    periodo         VARCHAR,
    data_posicao    DATE,
    utc_id          VARCHAR,
    gtin            VARCHAR,
    conc_und        DOUBLE,
    conc_fat        DOUBLE,
    pct_und         DOUBLE,
    pct_fat         DOUBLE,
    score           DOUBLE,
    prioridade      VARCHAR,
    cadastrado_rede  BOOLEAN,
    cod_rede         VARCHAR,
    tem_alternativa BOOLEAN,
    alternativa     VARCHAR,
    justificativa   VARCHAR,
    PRIMARY KEY (periodo, utc_id, gtin)
);

CREATE TABLE IF NOT EXISTS demanda_concorrencia (
    periodo       VARCHAR,
    utc_id        VARCHAR,
    gtin          VARCHAR,
    und           DOUBLE,
    fat_venda     DOUBLE,
    custo         DOUBLE,
    fat_pf        DOUBLE,
    importacao_id INTEGER,
    PRIMARY KEY (periodo, utc_id, gtin)
);
"""

# EAN como texto: só dígitos, até 14, completado com zeros à esquerda (GTIN-14).
MACROS = """
CREATE OR REPLACE MACRO norm_gtin(e) AS
    CASE WHEN regexp_full_match(trim(e), '[0-9]{1,14}')
              AND ltrim(trim(e), '0') <> ''
         THEN lpad(trim(e), 14, '0') END;

CREATE OR REPLACE MACRO gtin_ok(g) AS
    coalesce(
        regexp_full_match(g, '[0-9]{14}')
        AND (10 - list_sum([TRY_CAST(substr(g, i, 1) AS INTEGER)
                            * CASE WHEN i % 2 = 1 THEN 3 ELSE 1 END
                            FOR i IN range(1, 14)]) % 10) % 10
            = TRY_CAST(substr(g, 14, 1) AS INTEGER),
        false);

CREATE OR REPLACE MACRO gtin_exibe(g) AS
    CASE WHEN length(ltrim(g, '0')) <= 8  THEN lpad(ltrim(g, '0'), 8, '0')
         WHEN length(ltrim(g, '0')) <= 13 THEN lpad(ltrim(g, '0'), 13, '0')
         ELSE g END;

CREATE OR REPLACE MACRO fmt_n(x) AS
    replace(format('{:,.0f}', coalesce(x, 0)), ',', '.');

CREATE OR REPLACE MACRO fmt_r(x) AS
    'R$ ' || replace(format('{:,.0f}', coalesce(x, 0)), ',', '.');

CREATE OR REPLACE MACRO num_br(v) AS
    TRY_CAST(replace(trim(v), ',', '.') AS DOUBLE);
"""

VIEWS = """
-- O mesmo produto pode ter EAN diferente no estoque Rede e na base de mercado.
-- O código interno Rede (APRES_COD_PROD_INFORMANTE) liga os dois; a base de mercado vira o EAN canônico.
CREATE OR REPLACE VIEW v_equiv_ean AS
SELECT h.gtin AS gtin_rede, m.gtin AS gtin_canonico, h.cod_rede
FROM produtos_rede h
JOIN produtos_mercado m ON m.cod_rede = h.cod_rede AND m.gtin <> h.gtin
WHERE NOT EXISTS (SELECT 1 FROM produtos_mercado x WHERE x.gtin = h.gtin)
  -- Se o estoque já usa o EAN canônico em outro código, não junta (fica para revisão).
  AND NOT EXISTS (SELECT 1 FROM produtos_rede y WHERE y.gtin = m.gtin);

CREATE OR REPLACE VIEW v_produtos_rede AS
SELECT coalesce(eq.gtin_canonico, h.gtin) AS gtin, h.gtin AS gtin_estoque,
       h.cod_rede, h.descricao, h.laboratorio
FROM produtos_rede h LEFT JOIN v_equiv_ean eq ON eq.gtin_rede = h.gtin;

CREATE OR REPLACE VIEW v_estoque AS
SELECT e.data_posicao, e.filial_cod, coalesce(eq.gtin_canonico, e.gtin) AS gtin,
       sum(e.quantidade) AS quantidade
FROM estoque e LEFT JOIN v_equiv_ean eq ON eq.gtin_rede = e.gtin
GROUP BY ALL;

CREATE OR REPLACE VIEW v_produtos AS
SELECT
    coalesce(m.gtin, h.gtin)                         AS gtin,
    gtin_exibe(coalesce(m.gtin, h.gtin))             AS ean,
    gtin_ok(coalesce(m.gtin, h.gtin))                AS ean_valido,
    CASE WHEN h.gtin_estoque <> coalesce(m.gtin, h.gtin)
         THEN gtin_exibe(h.gtin_estoque) END         AS ean_estoque_diferente,
    coalesce(h.descricao, m.descricao)               AS descricao,
    h.descricao                                      AS descricao_rede,
    m.descricao                                      AS descricao_mercado,
    coalesce(h.cod_rede, m.cod_rede)                   AS cod_rede,
    coalesce(m.fabricante, h.laboratorio)            AS fabricante,
    h.laboratorio                                    AS laboratorio_rede,
    m.marca, m.molecula, m.concentracao, m.forma,
    m.atc1, m.atc2, m.atc3, m.atc4,
    m.sigla_tipo_mercado,
    coalesce(pm.secao, m.secao_origem)               AS secao,
    coalesce(pm.grupo, cs.grupo)                     AS grupo,
    coalesce(pm.tipo, m.tipo_merc_seg,
             CASE WHEN h.laboratorio ILIKE '%GENERICO%' THEN 'Generico' END) AS tipo,
    CASE WHEN pm.gtin IS NOT NULL THEN 'Manual'
         WHEN cs.grupo IS NOT NULL THEN 'base de mercado'
         ELSE 'Pendente' END                         AS origem_classificacao,
    (h.cod_rede IS NOT NULL AND m.cod_rede IS NOT NULL
        AND h.cod_rede <> m.cod_rede)                  AS divergencia_codigo,
    h.gtin IS NOT NULL                               AS no_cadastro_rede,
    m.gtin IS NOT NULL                               AS no_mercado
FROM produtos_mercado m
FULL OUTER JOIN v_produtos_rede h ON h.gtin = m.gtin
LEFT JOIN classificacao_produto_manual pm ON pm.gtin = coalesce(m.gtin, h.gtin)
LEFT JOIN classificacao_secao cs ON cs.secao_origem = m.secao_origem;

CREATE OR REPLACE VIEW v_filiais AS
SELECT f.filial_cod, f.nome_exibicao, f.nome_origem, f.cnpj, f.tipo, f.ativa, f.no_escopo,
       fr.utc_id, r.utc_desc, r.municipios, fr.origem AS origem_regiao
FROM filiais f
LEFT JOIN filial_regiao fr ON fr.cnpj = f.cnpj
LEFT JOIN regioes r ON r.utc_id = fr.utc_id;
"""


def conectar(caminho=None) -> duckdb.DuckDBPyConnection:
    config.PASTA_DADOS.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(caminho or config.DB_PATH))
    con.execute(f"SET temp_directory = '{config.PASTA_TEMP.as_posix()}'")
    con.execute(SCHEMA)
    con.execute(MACROS)
    con.execute(VIEWS)
    _semear(con)
    return con


def _semear(con) -> None:
    padrao = {
        "laboratorios_excluidos": (
            json.dumps(config.LABORATORIOS_EXCLUIDOS, ensure_ascii=False),
            "Laboratórios do estoque que não são mercadoria",
        ),
        "filiais_inativas_iniciais": (
            json.dumps(config.FILIAIS_INATIVAS),
            "Filiais marcadas como inativas ao aparecerem pela primeira vez",
        ),
        "filiais_escopo_iniciais": (
            json.dumps(config.FILIAIS_ESCOPO),
            "Filiais incluídas no escopo ao aparecerem pela primeira vez",
        ),
        "cobertura_minima_dias": ("15", "Dias de venda que a loja deve ter em estoque; abaixo disso sugere abastecer"),
        "cobertura_alvo_dias": ("30", "Dias de venda usados para calcular a quantidade sugerida ao abastecer"),
        "giro_minimo_mes": ("3", "Unidades por mês a partir das quais o produto é de giro (abaixo: baixo giro)"),
        "demanda_minima_concorrencia": ("1", "Unidades mínimas da concorrência na região para considerar demanda"),
        "percentil_alta": ("0.9", "Pontuação a partir da qual a prioridade é Alta (0 a 1, dentro da seção)"),
        "percentil_media": ("0.7", "Pontuação a partir da qual a prioridade é Média (0 a 1, dentro da seção)"),
        "peso_unidades": ("0.5", "Peso das unidades na pontuação (o restante é faturamento)"),
        "qtd_inicial_exposicao": ("1", "Unidades sugeridas para levar um produto que a loja ainda não vende"),
    }
    for chave, (valor, desc) in padrao.items():
        con.execute(
            "INSERT INTO parametros SELECT ?, ?, ?, now() WHERE NOT EXISTS "
            "(SELECT 1 FROM parametros WHERE chave = ?)",
            [chave, valor, desc, chave],
        )
    # Versão anterior criava a cobertura vazia; o valor definido pela Rede é 15 dias.
    con.execute(
        "UPDATE parametros SET valor = '15', descricao = ? WHERE chave = 'cobertura_minima_dias' "
        "AND coalesce(valor, '') = ''",
        [padrao["cobertura_minima_dias"][1]],
    )
    for secao, grupo in config.GRUPOS_POR_SECAO.items():
        con.execute(
            "INSERT INTO classificacao_secao SELECT ?, ?, false, now() WHERE NOT EXISTS "
            "(SELECT 1 FROM classificacao_secao WHERE secao_origem = ?)",
            [secao, grupo, secao],
        )


def parametro(con, chave: str, padrao=None):
    r = con.execute("SELECT valor FROM parametros WHERE chave = ?", [chave]).fetchone()
    if not r or r[0] in (None, ""):
        return padrao
    try:
        return json.loads(r[0])
    except (json.JSONDecodeError, TypeError):
        return r[0]


def salvar_parametro(con, chave: str, valor) -> None:
    texto = valor if isinstance(valor, str) else json.dumps(valor, ensure_ascii=False)
    con.execute(
        "UPDATE parametros SET valor = ?, atualizado_em = now() WHERE chave = ?", [texto, chave]
    )


def proximo_id_importacao(con) -> int:
    return con.execute("SELECT nextval('seq_importacao')").fetchone()[0]


def registrar_importacao(con, **campos) -> None:
    campos.setdefault("descartes", {})
    campos.setdefault("controle", {})
    con.execute(
        """INSERT INTO importacoes
           (id, tipo, arquivo, hash, importado_em, referencia, linhas_lidas, linhas_gravadas,
            descartes, controle, status, mensagem)
           VALUES (?, ?, ?, ?, now(), ?, ?, ?, ?, ?, ?, ?)""",
        [
            campos["id"], campos["tipo"], campos["arquivo"], campos.get("hash"),
            campos.get("referencia"), campos.get("linhas_lidas"), campos.get("linhas_gravadas"),
            json.dumps(campos["descartes"], ensure_ascii=False),
            json.dumps(campos["controle"], ensure_ascii=False, default=float),
            campos["status"], campos.get("mensagem"),
        ],
    )
