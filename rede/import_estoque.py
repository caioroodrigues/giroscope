"""Importação do estoque por filial (CSV do ERP)."""
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import db
from .util import (
    ImportacaoErro, colunas_csv, exigir_colunas, hash_arquivo, limpar_temp, preparar_utf8, q,
)

COLUNAS = ["COD MERCADORIA", "MERCADORIA", "EAN", "ESTOQUE", "LABORATORIO", "FILIAL", "CNPJ"]

MOTIVOS = [
    "Filial não identificada",
    "Filial inativa",
    "Item contábil ou de serviço",
    "Sem EAN",
    "EAN inválido",
    "Estoque não numérico",
    "Estoque negativo",
    "Estoque zero",
]


@dataclass
class ResultadoEstoque:
    importacao_id: int
    data_posicao: date
    linhas_lidas: int
    linhas_gravadas: int
    descartes: dict
    controle: dict
    avisos: list = field(default_factory=list)
    substituiu_anterior: bool = False


def _nome_exibicao(cod: int, nome_origem: str, nomes_fixos: dict) -> str:
    if cod in nomes_fixos:
        return nomes_fixos[cod]
    nome = (nome_origem or "").strip(" -")
    return nome or f"FILIAL {cod}"


def importar_estoque(con, caminho: Path, data_posicao: date) -> ResultadoEstoque:
    caminho = Path(caminho)
    if not caminho.exists():
        raise ImportacaoErro(f"Arquivo não encontrado: {caminho}")
    if data_posicao is None:
        raise ImportacaoErro("Informe a data de posição do estoque.")

    leitura, codificacao, sep = preparar_utf8(caminho)
    try:
        cols = colunas_csv(con, leitura, sep)
        exigir_colunas(cols, COLUNAS, caminho.name)

        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE _est_raw AS
            SELECT
                trim("COD MERCADORIA")                         AS cod_rede,
                trim("MERCADORIA")                             AS descricao,
                trim("EAN")                                    AS ean_original,
                norm_gtin("EAN")                               AS gtin,
                trim("ESTOQUE")                                AS estoque_txt,
                num_br("ESTOQUE")                              AS quantidade,
                trim("LABORATORIO")                            AS laboratorio,
                TRY_CAST(trim(split_part("FILIAL", ' - ', 1)) AS INTEGER) AS filial_cod,
                trim(substr("FILIAL", strpos("FILIAL", ' - ') + 3)) AS filial_nome,
                lpad(regexp_replace(trim("CNPJ"), '[^0-9]', '', 'g'), 14, '0') AS cnpj
            FROM read_csv({q(leitura)}, delim={q(sep)}, header=true, all_varchar=true)
        """)
    finally:
        limpar_temp(leitura, caminho)

    lidas = con.execute("SELECT count(*) FROM _est_raw").fetchone()[0]
    if lidas == 0:
        raise ImportacaoErro("O arquivo de estoque não tem linhas de dados.")

    inativas_ini = set(db.parametro(con, "filiais_inativas_iniciais", []))
    escopo_ini = set(db.parametro(con, "filiais_escopo_iniciais", []))
    labs_excluidos = db.parametro(con, "laboratorios_excluidos", [])
    from .config import NOMES_EXIBICAO

    # 1) Filiais: cria as novas, atualiza nome/CNPJ das existentes, preserva ajustes manuais.
    filiais_arq = con.execute("""
        SELECT filial_cod, any_value(filial_nome), any_value(cnpj)
        FROM _est_raw WHERE filial_cod IS NOT NULL GROUP BY 1
    """).fetchall()
    for cod, nome, cnpj in filiais_arq:
        tipo = "CD" if re.search(r"(^|[\s-])CD$", (nome or "").strip()) else "LOJA"
        existe = con.execute("SELECT 1 FROM filiais WHERE filial_cod = ?", [cod]).fetchone()
        if existe:
            con.execute(
                "UPDATE filiais SET nome_origem = ?, cnpj = ?, atualizado_em = now() WHERE filial_cod = ?",
                [nome, cnpj, cod],
            )
        else:
            con.execute(
                "INSERT INTO filiais VALUES (?, ?, ?, ?, ?, ?, ?, now())",
                [cod, nome, _nome_exibicao(cod, nome, NOMES_EXIBICAO), cnpj, tipo,
                 cod not in inativas_ini, cod in escopo_ini],
            )

    # 2) Motivo de descarte por linha (primeiro motivo que se aplica).
    con.execute("""
        CREATE OR REPLACE TEMP TABLE _est AS
        SELECT r.*,
            CASE
                WHEN r.filial_cod IS NULL                    THEN 'Filial não identificada'
                WHEN NOT f.ativa                             THEN 'Filial inativa'
                WHEN list_contains(?, r.laboratorio)         THEN 'Item contábil ou de serviço'
                WHEN r.ean_original IS NULL OR r.ean_original = '' THEN 'Sem EAN'
                WHEN r.gtin IS NULL                          THEN 'EAN inválido'
                WHEN r.quantidade IS NULL                    THEN 'Estoque não numérico'
                WHEN r.quantidade < 0                        THEN 'Estoque negativo'
                WHEN r.quantidade = 0                        THEN 'Estoque zero'
            END AS motivo
        FROM _est_raw r
        LEFT JOIN filiais f ON f.filial_cod = r.filial_cod
    """, [labs_excluidos])

    descartes = dict(con.execute(
        "SELECT motivo, count(*) FROM _est WHERE motivo IS NOT NULL GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall())

    validas, und_validas, duplicadas = con.execute("""
        SELECT count(*), coalesce(sum(quantidade), 0),
               count(*) - count(DISTINCT (filial_cod, gtin))
        FROM _est WHERE motivo IS NULL
    """).fetchone()

    avisos = []
    if duplicadas:
        avisos.append(
            f"{duplicadas} linhas repetiam o mesmo EAN na mesma filial (códigos internos diferentes). "
            "As quantidades foram somadas."
        )
    invalidos_dv = con.execute(
        "SELECT count(DISTINCT gtin) FROM _est WHERE motivo IS NULL AND NOT gtin_ok(gtin)"
    ).fetchone()[0]
    if invalidos_dv:
        avisos.append(
            f"{invalidos_dv} EANs têm dígito verificador incorreto. Foram mantidos, mas marcados "
            "como inválidos no cadastro para revisão."
        )

    # 3) Grava substituindo a mesma data de posição (reprocessamento sem duplicar).
    imp_id = db.proximo_id_importacao(con)
    anterior = con.execute(
        "SELECT count(*) FROM estoque WHERE data_posicao = ?", [data_posicao]
    ).fetchone()[0]
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute("DELETE FROM estoque WHERE data_posicao = ?", [data_posicao])
        con.execute("""
            INSERT INTO estoque
            SELECT ?, filial_cod, gtin, min(cod_rede), sum(quantidade), ?
            FROM _est WHERE motivo IS NULL
            GROUP BY filial_cod, gtin
        """, [data_posicao, imp_id])

        # 4) Cadastro Rede (código, descrição, laboratório) por EAN.
        con.execute("""
            INSERT OR REPLACE INTO produtos_rede
            SELECT gtin, arg_min(cod_rede, filial_cod), arg_min(descricao, filial_cod),
                   arg_min(laboratorio, filial_cod), now()
            FROM _est WHERE motivo IS NULL GROUP BY gtin
        """)
        gravadas, und_gravadas = con.execute(
            "SELECT count(*), coalesce(sum(quantidade), 0) FROM estoque WHERE importacao_id = ?",
            [imp_id],
        ).fetchone()
        if abs(und_gravadas - und_validas) > 0.001:
            raise ImportacaoErro(
                f"Conciliação falhou: {und_validas:,.0f} unidades válidas lidas e "
                f"{und_gravadas:,.0f} gravadas."
            )
        controle = {
            "codificacao": codificacao,
            "separador": sep,
            "unidades_validas_lidas": und_validas,
            "unidades_gravadas": und_gravadas,
            "filiais_no_arquivo": len(filiais_arq),
            "linhas_validas": validas,
            "registros_gravados": gravadas,
        }
        db.registrar_importacao(
            con, id=imp_id, tipo="ESTOQUE", arquivo=caminho.name, hash=hash_arquivo(caminho),
            referencia=data_posicao.isoformat(), linhas_lidas=lidas, linhas_gravadas=gravadas,
            descartes=descartes, controle=controle, status="OK",
            mensagem=" ".join(avisos) or None,
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    return ResultadoEstoque(
        importacao_id=imp_id, data_posicao=data_posicao, linhas_lidas=lidas,
        linhas_gravadas=gravadas, descartes=descartes, controle=controle, avisos=avisos,
        substituiu_anterior=anterior > 0,
    )
