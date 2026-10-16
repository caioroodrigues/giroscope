"""Importação do CSV do Snowflake: vendas Rede (1001) e concorrência (9001) no mesmo arquivo."""
from dataclasses import dataclass, field
from pathlib import Path

from . import db
from .config import GRUPOS_POR_SECAO, INF_CONCORRENCIA, INF_REDE
from .util import (
    ImportacaoErro, colunas_csv, exigir_colunas, hash_arquivo, limpar_temp, preparar_utf8, q,
)

COLUNAS = [
    "PER_MES", "UTC_ID_VAREJO", "UTC_DESC_VAREJO", "UTC_MUNICIPIO", "UTC_COD_MUNICIPIO",
    "UTC_FLG_ATUANTE", "INF_COD", "PDV_CNPJ", "TX_TIPO_TRANSACAO",
    "APRES_EAN", "APRES_COD", "APRES_DESC", "APRES_SECAO_LOJA", "APRES_TIPO_MERC_SEG",
    "APRES_SIGLA_TIPO_MERCADO", "APRES_MARCA", "APRES_FAB", "APRES_DESC_CORP", "APRES_FORMA",
    "APRES_ATCI", "APRES_ATCII", "APRES_ATCIII", "APRES_ATCIV", "APRES_MOLECULA_ASSOC",
    "APRES_CONCETRACAO_DESC", "APRES_COD_PROD_INFORMANTE", "APRES_MARCA_PROPRIA",
    "VAL_UND", "VAL_R$", "VAL_R$CON", "VAL_R$PCF",
]


@dataclass
class ResultadoSnowflake:
    importacao_id: int
    periodos: list
    linhas_lidas: int
    linhas_gravadas: int
    descartes: dict
    controle: dict
    conciliacao: list           # [(informante, und lidas, und gravadas)]
    regioes: int
    lojas_sem_cadastro: list    # CNPJs com venda que não existem no estoque
    avisos: list = field(default_factory=list)


def importar_snowflake(con, caminho: Path) -> ResultadoSnowflake:
    caminho = Path(caminho)
    if not caminho.exists():
        raise ImportacaoErro(f"Arquivo não encontrado: {caminho}")

    leitura, codificacao, sep = preparar_utf8(caminho)
    try:
        cols = colunas_csv(con, leitura, sep)
        exigir_colunas(cols, COLUNAS, caminho.name)
        # Só as colunas usadas; o DuckDB lê o arquivo em fluxo, sem carregar tudo na memória.
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE _sf AS
            SELECT
                trim(PER_MES)                         AS periodo,
                trim(UTC_ID_VAREJO)                   AS utc_id,
                trim(UTC_DESC_VAREJO)                 AS utc_desc,
                trim(UTC_MUNICIPIO)                   AS municipios,
                trim(UTC_COD_MUNICIPIO)               AS cod_municipios,
                trim(UTC_FLG_ATUANTE) = 'S'           AS atuante,
                trim(INF_COD)                         AS inf,
                lpad(regexp_replace(trim(PDV_CNPJ), '[^0-9]', '', 'g'), 14, '0') AS cnpj,
                trim(TX_TIPO_TRANSACAO)               AS transacao,
                trim(APRES_EAN)                       AS ean_original,
                norm_gtin(APRES_EAN)                  AS gtin,
                TRY_CAST(VAL_UND AS DOUBLE)           AS und,
                TRY_CAST("VAL_R$CON" AS DOUBLE)       AS fat_venda,
                TRY_CAST("VAL_R$PCF" AS DOUBLE)       AS custo,
                TRY_CAST("VAL_R$" AS DOUBLE)          AS fat_pf,
                APRES_COD, APRES_DESC, APRES_SECAO_LOJA, APRES_TIPO_MERC_SEG,
                APRES_SIGLA_TIPO_MERCADO, APRES_MARCA, APRES_FAB, APRES_DESC_CORP, APRES_FORMA,
                APRES_ATCI, APRES_ATCII, APRES_ATCIII, APRES_ATCIV, APRES_MOLECULA_ASSOC,
                APRES_CONCETRACAO_DESC,
                nullif(ltrim(trim(APRES_COD_PROD_INFORMANTE), '0'), '') AS cod_rede,
                APRES_MARCA_PROPRIA
            FROM read_csv({q(leitura)}, delim={q(sep)}, header=true, all_varchar=true)
        """)
    finally:
        limpar_temp(leitura, caminho)

    lidas = con.execute("SELECT count(*) FROM _sf").fetchone()[0]
    if lidas == 0:
        raise ImportacaoErro("O arquivo do Snowflake não tem linhas de dados.")

    con.execute(f"""
        ALTER TABLE _sf ADD COLUMN motivo VARCHAR;
        UPDATE _sf SET motivo = CASE
            WHEN inf NOT IN ('{INF_REDE}', '{INF_CONCORRENCIA}') THEN 'Informante fora do escopo'
            WHEN transacao IS DISTINCT FROM 'Demanda'          THEN 'Devolução ou outra transação'
            WHEN NOT regexp_full_match(coalesce(periodo, ''), '[0-9]{{6}}') THEN 'Período inválido'
            WHEN utc_id IS NULL OR utc_id = ''                 THEN 'Sem região'
            WHEN ean_original IS NULL OR ean_original = ''     THEN 'Sem EAN'
            WHEN gtin IS NULL                                  THEN 'EAN inválido'
            WHEN und IS NULL                                   THEN 'Unidades não numéricas'
            WHEN inf = '{INF_REDE}' AND ltrim(cnpj, '0') = ''   THEN 'Venda Rede sem CNPJ'
        END;
    """)

    descartes = dict(con.execute(
        "SELECT motivo, count(*) FROM _sf WHERE motivo IS NOT NULL GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall())
    periodos = [r[0] for r in con.execute(
        "SELECT DISTINCT periodo FROM _sf WHERE motivo IS NULL ORDER BY 1"
    ).fetchall()]
    if not periodos:
        raise ImportacaoErro(
            "Nenhuma linha válida de demanda encontrada. Confira se o arquivo é da tabela "
            "exportação de mercado com INF_COD 1001 ou 9001."
        )

    lidos_por_inf = dict(con.execute(
        "SELECT inf, sum(und) FROM _sf WHERE motivo IS NULL GROUP BY 1"
    ).fetchall())

    imp_id = db.proximo_id_importacao(con)
    con.execute("BEGIN TRANSACTION")
    try:
        # Reprocessamento: substitui apenas os pares mês × região presentes no arquivo.
        con.execute("""
            CREATE OR REPLACE TEMP TABLE _chaves AS
            SELECT DISTINCT periodo, utc_id FROM _sf WHERE motivo IS NULL
        """)
        substituidas = con.execute("""
            SELECT count(*) FROM (
                SELECT periodo, utc_id FROM demanda_concorrencia
                UNION SELECT periodo, utc_id FROM vendas_rede
            ) x SEMI JOIN _chaves k USING (periodo, utc_id)
        """).fetchone()[0]
        for tabela in ("demanda_concorrencia", "vendas_rede"):
            con.execute(f"""
                DELETE FROM {tabela}
                WHERE (periodo, utc_id) IN (SELECT (periodo, utc_id) FROM _chaves)
            """)

        # Concorrência: físico + digital somados por mês × região × EAN.
        con.execute(f"""
            INSERT INTO demanda_concorrencia
            SELECT periodo, utc_id, gtin, sum(und), sum(fat_venda), sum(custo), sum(fat_pf), ?
            FROM _sf WHERE motivo IS NULL AND inf = '{INF_CONCORRENCIA}'
            GROUP BY periodo, utc_id, gtin
        """, [imp_id])

        # Vendas Rede por loja (CNPJ) × região × EAN.
        con.execute(f"""
            INSERT INTO vendas_rede
            SELECT periodo, cnpj, utc_id, gtin, sum(und), sum(fat_venda), sum(custo), sum(fat_pf), ?
            FROM _sf WHERE motivo IS NULL AND inf = '{INF_REDE}'
            GROUP BY periodo, cnpj, utc_id, gtin
        """, [imp_id])

        # Regiões.
        con.execute("""
            INSERT OR REPLACE INTO regioes
            SELECT utc_id, any_value(utc_desc), any_value(municipios), any_value(cod_municipios),
                   bool_or(atuante), now()
            FROM _sf WHERE utc_id IS NOT NULL AND utc_id <> '' GROUP BY utc_id
        """)

        # Ligação automática loja -> região (região onde a loja mais vendeu no mês mais recente).
        # Correções manuais nunca são sobrescritas.
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE _lig AS
            SELECT cnpj, arg_max(utc_id, fat) AS utc_id, max(periodo) AS periodo
            FROM (
                SELECT cnpj, utc_id, periodo, sum(coalesce(fat_venda, 0)) AS fat
                FROM _sf WHERE motivo IS NULL AND inf = '{INF_REDE}'
                  AND periodo = (SELECT max(periodo) FROM _sf WHERE motivo IS NULL AND inf = '{INF_REDE}')
                GROUP BY ALL
            ) GROUP BY cnpj;
            DELETE FROM filial_regiao
            WHERE origem = 'AUTOMATICA' AND cnpj IN (SELECT cnpj FROM _lig);
            INSERT INTO filial_regiao
            SELECT cnpj, utc_id, 'AUTOMATICA', periodo, now() FROM _lig
            WHERE cnpj NOT IN (SELECT cnpj FROM filial_regiao);
        """)

        # Cadastro de mercado: o mês mais recente prevalece.
        con.execute("""
            CREATE OR REPLACE TEMP TABLE _prod AS
            SELECT gtin,
                   arg_max(APRES_COD, periodo)               AS apres_cod,
                   arg_max(APRES_DESC, periodo)              AS descricao,
                   arg_max(APRES_SECAO_LOJA, periodo)        AS secao_origem,
                   arg_max(APRES_TIPO_MERC_SEG, periodo)     AS tipo_merc_seg,
                   arg_max(APRES_SIGLA_TIPO_MERCADO, periodo) AS sigla,
                   arg_max(APRES_MARCA, periodo)             AS marca,
                   arg_max(APRES_FAB, periodo)               AS fabricante,
                   arg_max(APRES_DESC_CORP, periodo)         AS corporacao,
                   arg_max(APRES_FORMA, periodo)             AS forma,
                   arg_max(APRES_ATCI, periodo)              AS atc1,
                   arg_max(APRES_ATCII, periodo)             AS atc2,
                   arg_max(APRES_ATCIII, periodo)            AS atc3,
                   arg_max(APRES_ATCIV, periodo)             AS atc4,
                   arg_max(APRES_MOLECULA_ASSOC, periodo)    AS molecula,
                   arg_max(APRES_CONCETRACAO_DESC, periodo)  AS concentracao,
                   max(cod_rede)                              AS cod_rede,
                   arg_max(APRES_MARCA_PROPRIA, periodo)     AS marca_propria,
                   max(periodo)                              AS periodo_ref
            FROM _sf WHERE motivo IS NULL GROUP BY gtin;
            DELETE FROM produtos_mercado
            WHERE gtin IN (SELECT p.gtin FROM _prod p JOIN produtos_mercado m USING (gtin)
                           WHERE p.periodo_ref >= m.periodo_ref);
            INSERT INTO produtos_mercado
            SELECT p.*, now() FROM _prod p
            WHERE p.gtin NOT IN (SELECT gtin FROM produtos_mercado);
        """)

        # Seções novas entram na classificação (agrupadas se conhecidas, senão pendentes).
        for (secao,) in con.execute(
            "SELECT DISTINCT secao_origem FROM _prod WHERE secao_origem IS NOT NULL"
        ).fetchall():
            con.execute(
                "INSERT INTO classificacao_secao SELECT ?, ?, false, now() WHERE NOT EXISTS "
                "(SELECT 1 FROM classificacao_secao WHERE secao_origem = ?)",
                [secao, GRUPOS_POR_SECAO.get(secao), secao],
            )

        # Conciliação: unidades lidas = unidades gravadas, por informante.
        gravado = {
            INF_CONCORRENCIA: con.execute(
                "SELECT coalesce(sum(und),0), count(*) FROM demanda_concorrencia WHERE importacao_id = ?",
                [imp_id]).fetchone(),
            INF_REDE: con.execute(
                "SELECT coalesce(sum(und),0), count(*) FROM vendas_rede WHERE importacao_id = ?",
                [imp_id]).fetchone(),
        }
        conciliacao = []
        for inf, nome in ((INF_REDE, "Rede"), (INF_CONCORRENCIA, "Concorrência")):
            lido = lidos_por_inf.get(inf, 0) or 0
            grav = gravado[inf][0]
            conciliacao.append((nome, lido, grav))
            if abs(lido - grav) > 0.01:
                raise ImportacaoErro(
                    f"Conciliação falhou para {nome}: {lido:,.3f} unidades lidas e {grav:,.3f} gravadas."
                )
        gravadas = gravado[INF_CONCORRENCIA][1] + gravado[INF_REDE][1]
        regioes = con.execute("SELECT count(*) FROM _chaves").fetchone()[0]

        lojas_sem_cadastro = [r[0] for r in con.execute(f"""
            SELECT DISTINCT cnpj FROM _sf
            WHERE motivo IS NULL AND inf = '{INF_REDE}'
              AND cnpj NOT IN (SELECT cnpj FROM filiais WHERE cnpj IS NOT NULL)
        """).fetchall()]

        avisos = []
        if substituidas:
            avisos.append(
                f"{substituidas} combinações de mês × região já existiam e foram substituídas "
                "pelos dados deste arquivo."
            )
        if lojas_sem_cadastro:
            avisos.append(
                f"{len(lojas_sem_cadastro)} CNPJs com venda Rede não aparecem no estoque importado. "
                "Importe o estoque para ligar essas vendas às filiais."
            )

        controle = {
            "codificacao": codificacao,
            "separador": sep,
            "periodos": periodos,
            "regioes": regioes,
            "und_rede_lidas": conciliacao[0][1], "und_rede_gravadas": conciliacao[0][2],
            "und_conc_lidas": conciliacao[1][1], "und_conc_gravadas": conciliacao[1][2],
        }
        db.registrar_importacao(
            con, id=imp_id, tipo="SNOWFLAKE", arquivo=caminho.name, hash=hash_arquivo(caminho),
            referencia=", ".join(periodos), linhas_lidas=lidas, linhas_gravadas=gravadas,
            descartes=descartes, controle=controle, status="OK",
            mensagem=" ".join(avisos) or None,
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    return ResultadoSnowflake(
        importacao_id=imp_id, periodos=periodos, linhas_lidas=lidas, linhas_gravadas=gravadas,
        descartes=descartes, controle=controle, conciliacao=conciliacao, regioes=regioes,
        lojas_sem_cadastro=lojas_sem_cadastro, avisos=avisos,
    )
