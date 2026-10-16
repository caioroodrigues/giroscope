"""Motor de recomendações.

Duas saídas:
- analise_mix:  decisão da REGIÃO (produto vendido pela concorrência que a rede não tem).
- analise_loja: decisão de cada LOJA (levar produto, abastecer, estoque parado, manter).

Todas as contas usam o mês de vendas escolhido e a posição de estoque escolhida.
"""
import calendar
import json
from dataclasses import dataclass
from datetime import date

from . import db
from .util import ImportacaoErro

RECOMENDACOES = {
    "LEVAR": "Levar para a loja",
    "ABASTECER": "Abastecer loja",
    "PARADO": "Estoque parado",
    "MANTER": "Manter",
}


@dataclass
class Parametros:
    cobertura_minima_dias: float
    cobertura_alvo_dias: float
    giro_minimo_mes: float
    demanda_minima_concorrencia: float
    percentil_alta: float
    percentil_media: float
    peso_unidades: float
    qtd_inicial_exposicao: float

    @classmethod
    def do_banco(cls, con) -> "Parametros":
        valores = {}
        for campo in cls.__dataclass_fields__:
            v = db.parametro(con, campo)
            if v in (None, ""):
                raise ImportacaoErro(f"Parâmetro '{campo}' não definido. Preencha na tela Parâmetros.")
            try:
                valores[campo] = float(v)
            except (TypeError, ValueError):
                raise ImportacaoErro(f"Parâmetro '{campo}' com valor inválido: {v}")
        p = cls(**valores)
        if not 0 <= p.peso_unidades <= 1:
            raise ImportacaoErro("O peso das unidades deve estar entre 0 e 1.")
        if not 0 <= p.percentil_media <= p.percentil_alta <= 1:
            raise ImportacaoErro("Os percentis devem respeitar 0 ≤ média ≤ alta ≤ 1.")
        if p.cobertura_alvo_dias < p.cobertura_minima_dias:
            raise ImportacaoErro("A cobertura alvo não pode ser menor que a cobertura mínima.")
        return p


def periodos_disponiveis(con) -> list:
    return [r[0] for r in con.execute(
        "SELECT DISTINCT periodo FROM demanda_concorrencia ORDER BY 1 DESC").fetchall()]


def posicoes_disponiveis(con) -> list:
    return [r[0] for r in con.execute(
        "SELECT DISTINCT data_posicao FROM estoque ORDER BY 1 DESC").fetchall()]


def executar(con, periodo: str, data_posicao: date) -> dict:
    p = Parametros.do_banco(con)
    ano, mes = int(periodo[:4]), int(periodo[4:])
    dias = calendar.monthrange(ano, mes)[1]
    pu, pf = p.peso_unidades, 1 - p.peso_unidades

    lojas = con.execute("""
        SELECT count(*) FROM filiais f JOIN filial_regiao fr USING (cnpj)
        WHERE f.ativa AND f.no_escopo AND f.tipo = 'LOJA'
    """).fetchone()[0]
    if not lojas:
        raise ImportacaoErro(
            "Nenhuma loja ativa no escopo com região definida. Confira a tela Lojas e regiões.")

    # ---------------------------------------------------------------- bases
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE _lojas AS
        SELECT f.filial_cod, f.cnpj, f.nome_exibicao AS loja, fr.utc_id
        FROM filiais f JOIN filial_regiao fr USING (cnpj)
        WHERE f.ativa AND f.no_escopo AND f.tipo = 'LOJA';

    CREATE OR REPLACE TEMP TABLE _est AS
        SELECT e.filial_cod, e.gtin, e.quantidade
        FROM v_estoque e JOIN _lojas USING (filial_cod)
        WHERE e.data_posicao = DATE '{data_posicao.isoformat()}';

    CREATE OR REPLACE TEMP TABLE _cd AS
        SELECT e.gtin, sum(e.quantidade) AS est_cd
        FROM v_estoque e JOIN filiais f USING (filial_cod)
        WHERE f.tipo = 'CD' AND f.ativa AND e.data_posicao = DATE '{data_posicao.isoformat()}'
        GROUP BY e.gtin;

    CREATE OR REPLACE TEMP TABLE _vend AS
        SELECT l.filial_cod, v.gtin, sum(v.und) AS und, sum(v.fat_venda) AS fat
        FROM vendas_rede v JOIN _lojas l USING (cnpj)
        WHERE v.periodo = '{periodo}'
        GROUP BY ALL;

    -- Venda Rede na região inteira (inclui lojas fora do escopo)
    CREATE OR REPLACE TEMP TABLE _vend_reg AS
        SELECT utc_id, gtin, sum(und) AS und
        FROM vendas_rede WHERE periodo = '{periodo}' GROUP BY ALL;

    CREATE OR REPLACE TEMP TABLE _conc AS
        SELECT utc_id, gtin, und, fat_venda AS fat
        FROM demanda_concorrencia
        WHERE periodo = '{periodo}' AND und >= {p.demanda_minima_concorrencia};

    -- Custo unitário estimado: só das vendas da própria Rede. O custo do mercado não é usado
    -- porque a unidade da base de mercado pode ser diferente da unidade do estoque (ex.: caixa x 100).
    CREATE OR REPLACE TEMP TABLE _custo AS
        SELECT gtin, sum(custo) / nullif(sum(und), 0) AS custo_unit
        FROM vendas_rede WHERE und > 0 AND custo > 0 GROUP BY gtin;

    -- Participação da Rede por região e seção (faturamento) e nº de lojas Rede com venda na região
    CREATE OR REPLACE TEMP TABLE _part AS
        WITH x AS (
            SELECT v.utc_id, coalesce(p.secao, 'Pendente') AS secao, sum(v.fat_venda) AS rede, 0 AS conc
            FROM vendas_rede v LEFT JOIN v_produtos p USING (gtin)
            WHERE v.periodo = '{periodo}' GROUP BY ALL
            UNION ALL
            SELECT d.utc_id, coalesce(p.secao, 'Pendente'), 0, sum(d.fat_venda)
            FROM demanda_concorrencia d LEFT JOIN v_produtos p USING (gtin)
            WHERE d.periodo = '{periodo}' GROUP BY ALL
        )
        SELECT utc_id, secao, sum(rede) / nullif(sum(rede) + sum(conc), 0) AS part
        FROM x GROUP BY ALL;
    CREATE OR REPLACE TEMP TABLE _nlojas AS
        SELECT utc_id, count(DISTINCT cnpj) AS n FROM vendas_rede
        WHERE periodo = '{periodo}' GROUP BY utc_id;

    -- Seção e chave de equivalência (molécula + concentração + forma)
    CREATE OR REPLACE TEMP TABLE _prod AS
        SELECT gtin, ean, descricao, coalesce(secao, 'Pendente') AS secao, tipo, cod_rede,
               no_cadastro_rede,
               CASE WHEN molecula IS NOT NULL AND upper(molecula) NOT IN ('SEM MOLECULA', '')
                    THEN upper(molecula) || '|' || upper(coalesce(concentracao, '')) || '|'
                         || upper(coalesce(forma, '')) END AS chave_eq
        FROM v_produtos;
    """)

    # ---------------------------------------------------------------- universo loja × produto
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE _u AS
        SELECT DISTINCT filial_cod, gtin FROM (
            SELECT filial_cod, gtin FROM _est
            UNION SELECT filial_cod, gtin FROM _vend
            UNION SELECT l.filial_cod, c.gtin FROM _lojas l JOIN _conc c USING (utc_id)
        );

    CREATE OR REPLACE TEMP TABLE _m AS
        SELECT u.filial_cod, l.utc_id, u.gtin,
               coalesce(e.quantidade, 0)              AS est_loja,
               coalesce(cd.est_cd, 0)                 AS est_cd,
               coalesce(v.und, 0)                     AS venda_und,
               coalesce(v.fat, 0)                     AS venda_fat,
               coalesce(v.und, 0) / {dias}            AS venda_dia,
               coalesce(c.und, 0)                     AS conc_und,
               coalesce(c.fat, 0)                     AS conc_fat,
               coalesce(vr.und, 0)                    AS venda_rede_regiao,
               CASE WHEN coalesce(v.und, 0) >= {p.giro_minimo_mes} THEN 'Giro'
                    WHEN coalesce(v.und, 0) > 0 THEN 'Baixo giro'
                    ELSE 'Sem venda' END              AS giro
        FROM _u u
        JOIN _lojas l USING (filial_cod)
        LEFT JOIN _est e   ON e.filial_cod = u.filial_cod AND e.gtin = u.gtin
        LEFT JOIN _cd cd   ON cd.gtin = u.gtin
        LEFT JOIN _vend v  ON v.filial_cod = u.filial_cod AND v.gtin = u.gtin
        LEFT JOIN _conc c  ON c.utc_id = l.utc_id AND c.gtin = u.gtin
        LEFT JOIN _vend_reg vr ON vr.utc_id = l.utc_id AND vr.gtin = u.gtin;

    -- Excedente de cada loja: o que sobra acima da cobertura alvo (quem não vende, tudo sobra)
    ALTER TABLE _m ADD COLUMN reserva DOUBLE;
    ALTER TABLE _m ADD COLUMN excedente DOUBLE;
    UPDATE _m SET
        reserva = CASE WHEN venda_und > 0
                       THEN greatest(1, ceil(venda_dia * {p.cobertura_alvo_dias})) ELSE 0 END;
    UPDATE _m SET excedente = greatest(est_loja - reserva, 0);

    -- Estoque nas outras lojas do escopo na mesma região + melhor origem para transferência
    CREATE OR REPLACE TEMP TABLE _outras AS
        SELECT d.filial_cod, d.gtin,
               sum(o.est_loja)                       AS est_outras,
               max(o.excedente)                      AS exc_qualquer,
               arg_max(o.filial_cod, o.excedente)    AS origem_qualquer,
               -- Para levar um produto novo à loja, só tira de quem vende e tem sobra
               max(o.excedente) FILTER (WHERE o.venda_und > 0)                   AS exc_vendendo,
               arg_max(o.filial_cod, o.excedente) FILTER (WHERE o.venda_und > 0) AS origem_vendendo
        FROM _m d JOIN _m o ON o.utc_id = d.utc_id AND o.gtin = d.gtin AND o.filial_cod <> d.filial_cod
        GROUP BY d.filial_cod, d.gtin;
    """)

    # ---------------------------------------------------------------- regras da loja
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE _r AS
    SELECT m.*,
        coalesce(o.est_outras, 0) AS est_outras,
        o.exc_qualquer, o.origem_qualquer, o.exc_vendendo, o.origem_vendendo,
        CASE WHEN m.venda_dia > 0 THEN m.est_loja / m.venda_dia END AS cobertura_dias,
        (m.est_cd + coalesce(o.est_outras, 0) + m.est_loja) > 0 AS rede_tem,
        CASE
            -- Loja vende: compara com a cobertura
            WHEN m.giro = 'Giro' AND m.est_loja / m.venda_dia < {p.cobertura_minima_dias} THEN 'ABASTECER'
            WHEN m.giro = 'Baixo giro' AND m.est_loja < 1                               THEN 'ABASTECER'
            WHEN m.giro IN ('Giro', 'Baixo giro')                                      THEN 'MANTER'
            -- Loja não vende e tem estoque
            WHEN m.est_loja > 0                                                        THEN 'PARADO'
            -- Loja não vende, não tem, a região demanda e a rede tem de onde tirar
            WHEN m.conc_und > 0 AND (m.est_cd > 0 OR coalesce(o.exc_vendendo, 0) > 0) THEN 'LEVAR'
        END AS recomendacao
    FROM _m m LEFT JOIN _outras o USING (filial_cod, gtin);

    ALTER TABLE _r ADD COLUMN melhor_excedente DOUBLE;
    ALTER TABLE _r ADD COLUMN melhor_origem INTEGER;
    UPDATE _r SET
        melhor_excedente = CASE WHEN recomendacao = 'LEVAR' THEN exc_vendendo ELSE exc_qualquer END,
        melhor_origem    = CASE WHEN recomendacao = 'LEVAR' THEN origem_vendendo ELSE origem_qualquer END;

    DELETE FROM _r WHERE recomendacao IS NULL;

    -- Quantidade necessária e origem (CD primeiro; depois outra loja com sobra; senão compra)
    -- Venda estimada por loja para produto que ela ainda não vende:
    -- demanda da concorrência × participação Rede na seção ÷ (1 − participação) ÷ lojas Rede da região
    CREATE OR REPLACE TEMP TABLE _ve AS
        SELECT r.filial_cod, r.gtin, x.part AS part_secao,
               r.conc_und * x.part / nullif(1 - x.part, 0) / greatest(coalesce(nl.n, 1), 1)
                   AS venda_estimada
        FROM _r r
        JOIN _prod pr ON pr.gtin = r.gtin
        LEFT JOIN _part x ON x.utc_id = r.utc_id AND x.secao = pr.secao
        LEFT JOIN _nlojas nl ON nl.utc_id = r.utc_id
        WHERE r.recomendacao = 'LEVAR';
    ALTER TABLE _r ADD COLUMN venda_estimada DOUBLE;
    ALTER TABLE _r ADD COLUMN part_secao DOUBLE;
    UPDATE _r SET venda_estimada = v.venda_estimada, part_secao = v.part_secao
    FROM _ve v WHERE _r.filial_cod = v.filial_cod AND _r.gtin = v.gtin;

    ALTER TABLE _r ADD COLUMN necessidade DOUBLE;
    UPDATE _r SET necessidade = CASE
        WHEN recomendacao = 'ABASTECER' AND giro = 'Giro'
             THEN greatest(ceil(venda_dia * {p.cobertura_alvo_dias}) - est_loja, 1)
        WHEN recomendacao = 'ABASTECER' THEN greatest(1 - est_loja, 1)
        WHEN recomendacao = 'LEVAR' THEN greatest({p.qtd_inicial_exposicao},
             coalesce(ceil(venda_estimada / {dias} * {p.cobertura_alvo_dias}), 0))
        ELSE 0 END;

    ALTER TABLE _r ADD COLUMN origem VARCHAR;
    ALTER TABLE _r ADD COLUMN origem_filial INTEGER;
    ALTER TABLE _r ADD COLUMN qtd_sugerida DOUBLE;
    UPDATE _r SET
        origem = CASE
            WHEN recomendacao NOT IN ('ABASTECER', 'LEVAR') THEN NULL
            WHEN est_cd > 0 THEN 'CD'
            WHEN coalesce(melhor_excedente, 0) > 0 THEN 'Outra loja'
            ELSE 'Compra' END;
    UPDATE _r SET
        origem_filial = CASE WHEN origem = 'Outra loja' THEN melhor_origem END,
        qtd_sugerida = CASE
            WHEN origem = 'CD' THEN least(necessidade, est_cd)
            WHEN origem = 'Outra loja' THEN least(necessidade, melhor_excedente)
            WHEN origem = 'Compra' THEN necessidade END;
    """)

    # ---------------------------------------------------------------- pontuação e prioridade
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE _r2 AS
    SELECT r.*, pr.ean, pr.descricao, pr.secao, pr.tipo, pr.chave_eq, cu.custo_unit,
        r.est_loja * cu.custo_unit AS capital,
        CASE
            WHEN recomendacao = 'ABASTECER' AND est_loja <= 0 THEN 'Zerado'
            WHEN recomendacao = 'ABASTECER' AND giro = 'Giro' AND cobertura_dias < 7 THEN 'Crítica'
            WHEN recomendacao = 'ABASTECER' THEN 'Abaixo do mínimo'
        END AS urgencia
    FROM _r r
    JOIN _prod pr USING (gtin)
    LEFT JOIN _custo cu USING (gtin);

    -- Abastecer/manter: pela venda da própria loja dentro da seção.
    -- Levar/parado: pela demanda da concorrência na região dentro da seção.
    CREATE OR REPLACE TEMP TABLE _score AS
    SELECT filial_cod, gtin,
        CASE WHEN recomendacao IN ('ABASTECER', 'MANTER') THEN
            {pu} * percent_rank() OVER (PARTITION BY filial_cod, secao, (recomendacao IN ('ABASTECER','MANTER')) ORDER BY venda_und)
          + {pf} * percent_rank() OVER (PARTITION BY filial_cod, secao, (recomendacao IN ('ABASTECER','MANTER')) ORDER BY venda_fat)
        ELSE
            {pu} * percent_rank() OVER (PARTITION BY filial_cod, secao, (recomendacao IN ('ABASTECER','MANTER')) ORDER BY conc_und)
          + {pf} * percent_rank() OVER (PARTITION BY filial_cod, secao, (recomendacao IN ('ABASTECER','MANTER')) ORDER BY conc_fat)
        END AS score
    FROM _r2;
    """)

    # ---------------------------------------------------------------- alternativa interna
    con.execute("""
    -- Para cada produto, o equivalente (mesma molécula, concentração e forma) que a loja
    -- tem ou vende, priorizando o que mais vende.
    CREATE OR REPLACE TEMP TABLE _alt_loja AS
    SELECT a.filial_cod, a.gtin,
           arg_max(b.descricao || ' (' || coalesce(b.tipo, '?') || '), estoque '
                   || fmt_n(b.est_loja) || ', vendeu ' || fmt_n(b.venda_und),
                   b.venda_und * 1e6 + b.est_loja) AS alternativa
    FROM _r2 a JOIN _r2 b
      ON b.filial_cod = a.filial_cod AND b.chave_eq = a.chave_eq AND b.gtin <> a.gtin
    WHERE a.chave_eq IS NOT NULL AND (b.est_loja > 0 OR b.venda_und > 0)
      AND a.recomendacao = 'LEVAR'
    GROUP BY a.filial_cod, a.gtin;
    """)

    # ---------------------------------------------------------------- gravação loja
    exec_id = con.execute("SELECT nextval('seq_execucao')").fetchone()[0]
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute(f"DELETE FROM analise_loja WHERE periodo = '{periodo}'")
        con.execute(f"""
        INSERT INTO analise_loja
        SELECT {exec_id}, '{periodo}', DATE '{data_posicao.isoformat()}',
            r.filial_cod, r.utc_id, r.gtin, r.est_loja, r.est_cd, r.est_outras,
            r.venda_und, r.venda_fat, r.venda_dia, r.cobertura_dias, r.giro,
            r.conc_und, r.conc_fat, r.venda_rede_regiao,
            r.recomendacao, r.origem, r.origem_filial, r.qtd_sugerida, r.urgencia,
            s.score,
            CASE WHEN r.recomendacao = 'ABASTECER' AND r.giro = 'Baixo giro' THEN 'Baixa'
                 WHEN s.score >= {p.percentil_alta}  THEN 'Alta'
                 WHEN s.score >= {p.percentil_media} THEN 'Média'
                 ELSE 'Baixa' END,
            r.custo_unit, r.capital, al.alternativa,
            CASE r.recomendacao
            WHEN 'ABASTECER' THEN
                l.loja || ' vendeu ' || fmt_n(r.venda_und) || ' un em ' || '{mes:02d}/{ano}'
                || ' e tem ' || fmt_n(r.est_loja) || ' em estoque'
                || CASE WHEN r.giro = 'Giro' AND r.est_loja > 0
                        THEN CASE WHEN r.cobertura_dias < 1 THEN ', o que dura menos de 1 dia'
                                  WHEN round(r.cobertura_dias) = 1 THEN ', o que dura cerca de 1 dia'
                                  ELSE ', o que dura cerca de ' || fmt_n(r.cobertura_dias) || ' dias' END
                        WHEN r.giro = 'Baixo giro' THEN ' (baixo giro: manter ao menos 1 unidade)'
                        ELSE '' END
                || '. '
                || CASE r.origem
                     WHEN 'CD' THEN 'O CD tem ' || fmt_n(r.est_cd) || ' un. Sugestão: enviar '
                                    || fmt_n(r.qtd_sugerida) || ' un do CD.'
                     WHEN 'Outra loja' THEN ol.loja || ' tem ' || fmt_n(r.melhor_excedente)
                                    || ' un acima da necessidade dela. Sugestão: transferir '
                                    || fmt_n(r.qtd_sugerida) || ' un.'
                     ELSE 'Sem estoque no CD nem sobra nas outras lojas. Sugestão: comprar '
                          || fmt_n(r.qtd_sugerida) || ' un.' END
            WHEN 'LEVAR' THEN
                'A concorrência vendeu ' || fmt_n(r.conc_und) || ' un (' || fmt_r(r.conc_fat)
                || ') na região de ' || replace(rg.utc_desc, 'MG-', '') || ' e ' || l.loja
                || ' não tem o produto. '
                || CASE WHEN r.venda_rede_regiao > 0
                        THEN 'Outras lojas Rede da região venderam ' || fmt_n(r.venda_rede_regiao) || ' un. '
                        ELSE '' END
                || CASE WHEN r.venda_estimada >= 0.5
                        THEN 'Venda estimada para a loja: cerca de ' || fmt_n(r.venda_estimada)
                             || ' un/mês (participação Rede de ' || fmt_n(r.part_secao * 100)
                             || '% na seção). '
                        ELSE '' END
                || CASE r.origem
                     WHEN 'CD' THEN 'O CD tem ' || fmt_n(r.est_cd) || ' un. Sugestão: enviar '
                     ELSE ol.loja || ' tem ' || fmt_n(r.melhor_excedente)
                          || ' un sobrando. Sugestão: transferir ' END
                || fmt_n(r.qtd_sugerida) || ' un.'
            WHEN 'PARADO' THEN
                l.loja || ' tem ' || fmt_n(r.est_loja) || ' un'
                || CASE WHEN r.capital > 0 THEN ' (cerca de ' || fmt_r(r.capital) || ' em custo)' ELSE '' END
                || ' e não vendeu em ' || '{mes:02d}/{ano}' || '. '
                || CASE WHEN r.conc_und > 0
                        THEN 'A concorrência vendeu ' || fmt_n(r.conc_und)
                             || ' un na região: verificar exposição e preço.'
                        WHEN r.venda_rede_regiao > 0
                        THEN 'Outras lojas Rede da região venderam ' || fmt_n(r.venda_rede_regiao)
                             || ' un: avaliar transferência.'
                        ELSE 'Nem a Rede nem a concorrência venderam na região.' END
            WHEN 'MANTER' THEN
                'Cobertura adequada: ' || fmt_n(r.est_loja) || ' un para ' || fmt_n(r.venda_und)
                || ' un vendidas no mês.'
            END
        FROM _r2 r
        JOIN _score s USING (filial_cod, gtin)
        JOIN _lojas l USING (filial_cod)
        JOIN regioes rg ON rg.utc_id = r.utc_id
        LEFT JOIN _lojas ol ON ol.filial_cod = r.melhor_origem
        LEFT JOIN _alt_loja al USING (filial_cod, gtin)
        """)

        # ------------------------------------------------------------ incluir no mix (região)
        con.execute(f"DELETE FROM analise_mix WHERE periodo = '{periodo}'")
        con.execute(f"""
        INSERT INTO analise_mix
        WITH rede AS (   -- estoque da rede por região (lojas do escopo) + CD
            SELECT l.utc_id, e.gtin, sum(e.quantidade) AS est
            FROM _est e JOIN _lojas l USING (filial_cod) GROUP BY ALL
        ),
        cand AS (
            SELECT c.utc_id, c.gtin, c.und, c.fat, pr.secao, pr.chave_eq, pr.cod_rede,
                   pr.no_cadastro_rede
            FROM _conc c JOIN _prod pr USING (gtin)
            LEFT JOIN rede r ON r.utc_id = c.utc_id AND r.gtin = c.gtin
            LEFT JOIN _cd cd ON cd.gtin = c.gtin
            LEFT JOIN _vend_reg vr ON vr.utc_id = c.utc_id AND vr.gtin = c.gtin
            WHERE coalesce(r.est, 0) = 0 AND coalesce(cd.est_cd, 0) = 0
              AND coalesce(vr.und, 0) = 0
        ),
        pct AS (
            SELECT *, percent_rank() OVER (PARTITION BY utc_id, secao ORDER BY und) AS pct_und,
                      percent_rank() OVER (PARTITION BY utc_id, secao ORDER BY fat) AS pct_fat
            FROM cand
        ),
        alt AS (   -- equivalente que a rede tem em estoque (lojas da região ou CD) ou vendeu
            SELECT c.utc_id, c.gtin,
                   arg_max(pb.descricao || ' (' || coalesce(pb.tipo, '?') || '), estoque rede '
                           || fmt_n(coalesce(rb.est, 0) + coalesce(cb.est_cd, 0))
                           || ', vendas Rede ' || fmt_n(coalesce(vb.und, 0)),
                           coalesce(vb.und, 0) * 1e6 + coalesce(rb.est, 0) + coalesce(cb.est_cd, 0))
                       AS alternativa
            FROM cand c
            JOIN _prod pb ON pb.chave_eq = c.chave_eq AND pb.gtin <> c.gtin
            LEFT JOIN rede rb ON rb.utc_id = c.utc_id AND rb.gtin = pb.gtin
            LEFT JOIN _cd cb ON cb.gtin = pb.gtin
            LEFT JOIN _vend_reg vb ON vb.utc_id = c.utc_id AND vb.gtin = pb.gtin
            WHERE c.chave_eq IS NOT NULL
              AND (coalesce(rb.est, 0) + coalesce(cb.est_cd, 0) + coalesce(vb.und, 0)) > 0
            GROUP BY c.utc_id, c.gtin
        )
        SELECT {exec_id}, '{periodo}', DATE '{data_posicao.isoformat()}',
            p.utc_id, p.gtin, p.und, p.fat, p.pct_und, p.pct_fat,
            {pu} * p.pct_und + {pf} * p.pct_fat AS score,
            CASE WHEN {pu} * p.pct_und + {pf} * p.pct_fat >= {p.percentil_alta} THEN 'Alta'
                 WHEN {pu} * p.pct_und + {pf} * p.pct_fat >= {p.percentil_media} THEN 'Média'
                 ELSE 'Baixa' END,
            p.no_cadastro_rede OR p.cod_rede IS NOT NULL,
            p.cod_rede,
            a.alternativa IS NOT NULL,
            a.alternativa,
            'A concorrência vendeu ' || fmt_n(p.und) || ' un (' || fmt_r(p.fat) || ') em '
            || '{mes:02d}/{ano}' || ' na região de ' || replace(rg.utc_desc, 'MG-', '')
            || CASE WHEN p.utc_id IN (SELECT utc_id FROM _lojas)
                    THEN '. Nenhuma loja da região nem o CD tem o produto, e a Rede não vendeu. '
                    ELSE '. A Rede não tem loja analisada nesta região, o CD não tem o produto e a Rede não vendeu. ' END
            || 'Está entre os ' || fmt_n(greatest(1, ceil((1 - p.pct_und) * 100)))
            || '% mais vendidos em unidades da seção ' || p.secao || '. '
            || CASE WHEN p.cod_rede IS NOT NULL OR p.no_cadastro_rede
                    THEN 'Já existe cadastro Rede' || coalesce(' (cód. ' || p.cod_rede || ')', '')
                         || ': avaliar reativação.'
                    ELSE 'Sem cadastro na Rede.' END
        FROM pct p
        JOIN regioes rg USING (utc_id)
        LEFT JOIN alt a USING (utc_id, gtin)
        """)

        resumo = {
            "loja": dict(con.execute(
                f"SELECT recomendacao, count(*) FROM analise_loja WHERE execucao_id = {exec_id} GROUP BY 1"
            ).fetchall()),
            "mix": con.execute(
                f"SELECT count(*) FROM analise_mix WHERE execucao_id = {exec_id}").fetchone()[0],
            "dias_mes": dias,
        }
        con.execute(
            "INSERT INTO analise_execucoes VALUES (?, now(), ?, ?, ?, ?)",
            [exec_id, periodo, data_posicao, json.dumps(p.__dict__), json.dumps(resumo)],
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    resumo["execucao_id"] = exec_id
    resumo["defasagem_dias"] = (data_posicao - date(ano, mes, dias)).days
    return resumo
