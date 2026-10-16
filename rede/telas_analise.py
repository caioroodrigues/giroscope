"""Telas de análise (Etapa 5). Todas usam os filtros da barra lateral."""
import re

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from .filtros import Filtros
from .telas import con, n

VERDE = "#0B6E4F"
CINZA = "#A3ADB3"
AMBAR = "#C47F17"
VERMELHO = "#B3412E"

NUM = st.column_config.NumberColumn(format="localized")
PCT = st.column_config.ProgressColumn(format="%.0f%%", min_value=0, max_value=100)
JUST = st.column_config.TextColumn(width="large")

ORDEM_PRIO = "CASE {c} WHEN 'Alta' THEN 1 WHEN 'Média' THEN 2 ELSE 3 END"


# ---------------------------------------------------------------- utilitários
def _sem_dados(f: Filtros) -> bool:
    if not f.periodo:
        st.info("Nenhuma análise calculada ainda. Importe o estoque e o arquivo do Snowflake na tela "
                "Importar arquivos; o cálculo é feito automaticamente.")
        return True
    return False


def _sem_lojas(f: Filtros) -> bool:
    """Telas por loja: avisa quando a seleção não tem nenhuma loja analisada."""
    if _q(Filtros.LOJAS_SQL, f).empty:
        st.info("A seleção não tem nenhuma loja analisada. Esta tela trabalha por loja: escolha uma "
                "região com loja Rede ou coloque as lojas no escopo em Lojas e regiões. "
                "Para ver só a concorrência de uma região, use Estudo de mercado, Consulta por cidade "
                "ou Incluir no mix.")
        return True
    return False


def _reais(v: float) -> str:
    if abs(v) >= 1e6:
        return f"R$ {n(v / 1e6, 2)} mi"
    if abs(v) >= 1e3:
        return f"R$ {n(v / 1e3)} mil"
    return f"R$ {n(v)}"


def _cabecalho(titulo: str, pergunta: str, f: Filtros) -> None:
    st.title(titulo)
    st.write(pergunta)
    st.caption(f"{f.periodo[4:]}/{f.periodo[:4]}, estoque de {f.data_posicao.strftime('%d/%m/%Y')}. "
               f"Filtros: {f.descricao()}.")


def _baixar(df: pd.DataFrame, nome: str, chave: str) -> None:
    st.download_button(
        "Baixar lista (CSV)", df.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
        file_name=nome, mime="text/csv", key=f"dl_{chave}")


def _tabela(df: pd.DataFrame, chave: str, nome_arquivo: str, config: dict | None = None,
            limite: int = 2000) -> None:
    if df.empty:
        st.info("Nenhum item com os filtros escolhidos.")
        return
    cfg = {c: NUM for c in df.select_dtypes("number").columns}
    if "Justificativa" in df.columns:
        cfg["Justificativa"] = JUST
    cfg.update(config or {})
    if len(df) > limite:
        st.caption(f"Mostrando os {n(limite)} primeiros de {n(len(df))}. O arquivo leva todos.")
    st.dataframe(df.head(limite), hide_index=True, width="stretch", column_config=cfg)
    _baixar(df, nome_arquivo, chave)


def _q(sql: str, f: Filtros, extra: dict | None = None) -> pd.DataFrame:
    p = f.params()
    p.update(extra or {})
    usados = set(re.findall(r"\$([a-z_]+)", sql))
    return con().execute(sql, {k: v for k, v in p.items() if k in usados}).df()


def _barras(df, y, series, titulo, altura=None, sufixo=""):
    fig = go.Figure()
    for col, nome, cor in series:
        fig.add_bar(y=df[y], x=df[col], name=nome, orientation="h", marker_color=cor,
                    hovertemplate=f"%{{y}}<br>{nome}: %{{x:,.0f}}{sufixo}<extra></extra>")
    fig.update_layout(
        barmode="stack", title=dict(text=titulo, font=dict(size=15)),
        height=altura or max(260, 34 * len(df) + 90), margin=dict(l=10, r=10, t=50, b=10),
        legend=dict(orientation="h", y=-0.12), separators=",.",
        yaxis=dict(autorange="reversed"), plot_bgcolor="white",
        xaxis=dict(gridcolor="#E6ECE9"),
    )
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


# ---------------------------------------------------------------- Estudo de mercado
MERCADO_SQL = """
WITH lojas AS ({lojas}),
regs AS ({regioes}),
prod AS (SELECT * FROM v_produtos p WHERE {produto}),
conc AS (
    SELECT d.utc_id, d.gtin, d.und, d.fat_venda AS fat FROM demanda_concorrencia d
    JOIN prod USING (gtin) WHERE d.periodo = $periodo AND d.utc_id IN (SELECT utc_id FROM regs)),
rede AS (
    SELECT v.utc_id, v.gtin, sum(v.und) AS und, sum(v.fat_venda) AS fat FROM vendas_rede v
    JOIN prod USING (gtin) WHERE v.periodo = $periodo AND v.utc_id IN (SELECT utc_id FROM regs)
    GROUP BY ALL),
est AS (
    SELECT l.utc_id, e.gtin, sum(e.quantidade) AS q FROM v_estoque e JOIN lojas l USING (filial_cod)
    WHERE e.data_posicao = $posicao GROUP BY ALL),
base AS (
    SELECT coalesce(c.utc_id, m.utc_id) AS utc_id, coalesce(c.gtin, m.gtin) AS gtin,
           coalesce(c.fat, 0) AS conc_fat, coalesce(m.fat, 0) AS rede_fat,
           coalesce(c.und, 0) AS conc_und, coalesce(m.und, 0) AS rede_und,
           e.q IS NOT NULL AS tem_estoque
    FROM conc c FULL OUTER JOIN rede m ON m.utc_id = c.utc_id AND m.gtin = c.gtin
    LEFT JOIN est e ON e.utc_id = coalesce(c.utc_id, m.utc_id) AND e.gtin = coalesce(c.gtin, m.gtin))
SELECT {dim} AS dim,
       sum(b.rede_fat) + sum(b.conc_fat)                                 AS mercado,
       sum(b.rede_fat)                                                   AS rede,
       sum(b.conc_fat)                                                  AS conc,
       100 * sum(b.rede_fat) / nullif(sum(b.rede_fat) + sum(b.conc_fat), 0) AS participacao,
       100 * sum(b.conc_fat) FILTER (WHERE b.tem_estoque) / nullif(sum(b.conc_fat), 0) AS cobertura
FROM base b JOIN prod p USING (gtin) JOIN regioes r USING (utc_id)
GROUP BY 1
HAVING sum(b.rede_fat) + sum(b.conc_fat) > 0
ORDER BY mercado DESC
"""

MIX_DIM_SQL = """
WITH regs AS ({regioes})
SELECT {dim} AS dim, count(*) AS oport_mix, count(*) FILTER (WHERE m.prioridade = 'Alta') AS oport_alta
FROM analise_mix m JOIN v_produtos p USING (gtin) JOIN regioes r USING (utc_id)
WHERE m.periodo = $periodo AND m.utc_id IN (SELECT utc_id FROM regs) AND {produto}
GROUP BY 1
"""

DIMENSOES = {
    "Seção": "coalesce(p.secao, 'Pendente')",
    "Grupo": "coalesce(p.grupo, 'Pendente')",
    "Tipo de mercado": "coalesce(p.tipo, 'Sem tipo')",
    "Fabricante": "coalesce(p.fabricante, 'Sem fabricante')",
    "Região": "replace(r.utc_desc, 'MG-', '')",
}


def _mercado(f: Filtros, dim: str) -> pd.DataFrame:
    d = DIMENSOES[dim]
    merc = _q(MERCADO_SQL.format(lojas=Filtros.LOJAS_SQL, regioes=Filtros.REGIOES_SQL,
                                 produto=Filtros.PRODUTO_SQL, dim=d), f, {"posicao": f.data_posicao})
    mix = _q(MIX_DIM_SQL.format(regioes=Filtros.REGIOES_SQL, produto=Filtros.PRODUTO_SQL, dim=d), f)
    df = merc.merge(mix, on="dim", how="left")
    df[["oport_mix", "oport_alta"]] = df[["oport_mix", "oport_alta"]].fillna(0).astype(int)
    return df


def pagina_mercado(f: Filtros):
    if _sem_dados(f):
        return
    _cabecalho("Estudo de mercado",
               "Tamanho do mercado, quanto dele é da Rede e quanto do que a concorrência vende a Rede tem "
               "em loja. Escolha uma ou mais regiões na barra lateral; sem escolha, soma todas as "
               "regiões importadas.", f)
    tot = _mercado(f, "Grupo")
    if tot.empty:
        st.info("Sem vendas com os filtros escolhidos.")
        return
    mercado, rede, conc = tot.mercado.sum(), tot.rede.sum(), tot.conc.sum()
    cob_df = _mercado(f, "Região")
    cob = (cob_df.cobertura.fillna(0) * cob_df.conc).sum() / conc if conc else 0
    a, b, c, d = st.columns(4)
    a.metric("Mercado no mês", _reais(mercado), help="Venda Rede + concorrência, preço de venda")
    b.metric("Participação Rede", f"{n(100 * rede / mercado, 1)}%" if mercado else "-")
    c.metric("Cobertura de mix", f"{n(cob, 1)}%",
             help="Do faturamento da concorrência, quanto está em produtos que as lojas filtradas têm em estoque")
    d.metric("Oportunidades de mix", n(tot.oport_mix.sum()),
             help=f"{n(tot.oport_alta.sum())} de prioridade alta")

    dim = st.segmented_control("Ver por", list(DIMENSOES), default="Seção", key="mercado_dim")
    df = _mercado(f, dim or "Seção")
    top = df.head(25)
    _barras(top.assign(rede_mil=top.rede / 1e3, conc_mil=top.conc / 1e3), "dim",
            [("rede_mil", "Rede", VERDE), ("conc_mil", "Concorrência", CINZA)],
            f"Mercado por {dim.lower()} (R$ mil)" + (" — 25 maiores" if len(df) > 25 else ""),
            sufixo=" mil")
    tabela = df.rename(columns={
        "dim": dim, "mercado": "Mercado (R$)", "rede": "Rede (R$)", "conc": "Concorrência (R$)",
        "participacao": "Participação Rede", "cobertura": "Cobertura de mix",
        "oport_mix": "Oportunidades de mix", "oport_alta": "Prioridade alta"})
    _tabela(tabela, "mercado", f"estudo_mercado_{dim.lower()}_{f.periodo}.csv",
            {"Participação Rede": PCT, "Cobertura de mix": PCT})
    st.caption(
        "Participação: venda Rede ÷ (Rede + concorrência), a preço de venda, em todas as lojas Rede da região. "
        "Cobertura de mix: parte do faturamento da concorrência em produtos com estoque nas lojas filtradas.")


# ---------------------------------------------------------------- Consulta por cidade
def pagina_consulta(f: Filtros):
    if _sem_dados(f):
        return
    _cabecalho("Consulta por cidade",
               "Escolha produtos e veja lado a lado a concorrência na região, a venda e o estoque de "
               "cada loja e o estoque do CD.", f)
    c1, c2 = st.columns([2, 1])
    eans_txt = c1.text_area("EANs (um por linha, ou separados por vírgula)", height=90,
                            placeholder="7891317007560\n7896006297529")
    molecula = c2.text_input("Molécula", placeholder="Ex.: dapagliflozina")
    eans = [e.strip() for e in re.split(r"[\s,;]+", eans_txt) if e.strip()]
    tem_filtro_prod = bool(eans or molecula or f.busca or f.secoes or f.grupos or f.tipos)
    if not tem_filtro_prod:
        st.info("Informe EANs, uma molécula ou use os filtros de seção, tipo ou busca na barra lateral.")
        return

    extra = {"posicao": f.data_posicao, "eans": [e.lstrip("0") for e in eans],
             "molecula": molecula.strip().upper()}
    prod_sql = f"""
        SELECT p.* FROM v_produtos p
        WHERE {Filtros.PRODUTO_SQL}
          AND (len($eans) = 0 OR list_contains($eans, ltrim(p.ean, '0'))
               OR list_contains($eans, ltrim(coalesce(p.ean_estoque_diferente, ''), '0')))
          AND ($molecula = '' OR upper(coalesce(p.molecula, '')) LIKE '%' || $molecula || '%'
               OR upper(p.descricao) LIKE '%' || $molecula || '%')"""

    # Linha = produto × região (concorrência da região e estoque do CD)
    reg = _q(f"""
        WITH prod AS ({prod_sql}), regs AS ({Filtros.REGIOES_SQL}),
        cd AS (SELECT e.gtin, sum(e.quantidade) AS q FROM v_estoque e JOIN filiais fi USING (filial_cod)
               WHERE fi.tipo = 'CD' AND fi.ativa AND e.data_posicao = $posicao GROUP BY 1),
        rede AS (SELECT utc_id, gtin, sum(und) AS und FROM vendas_rede WHERE periodo = $periodo GROUP BY ALL)
        SELECT pr.gtin, r.utc_id, pr.ean AS "EAN", pr.descricao AS "Produto", pr.tipo AS "Tipo",
               pr.fabricante AS "Fabricante", replace(r.utc_desc, 'MG-', '') AS "Região",
               coalesce(c.und, 0) AS "Concorrência (un)", coalesce(c.fat_venda, 0) AS "Concorrência (R$)",
               coalesce(m.und, 0) AS "Rede na região (un)", coalesce(cd.q, 0) AS "Estoque CD"
        FROM prod pr CROSS JOIN regs
        JOIN regioes r ON r.utc_id = regs.utc_id
        LEFT JOIN demanda_concorrencia c ON c.periodo = $periodo AND c.utc_id = regs.utc_id AND c.gtin = pr.gtin
        LEFT JOIN rede m ON m.utc_id = regs.utc_id AND m.gtin = pr.gtin
        LEFT JOIN cd ON cd.gtin = pr.gtin
    """, f, extra)
    regs_com_loja = set(_q(Filtros.LOJAS_SQL, f).utc_id)
    reg = reg[(reg["Concorrência (un)"] > 0) | (reg["Rede na região (un)"] > 0)
              | ((reg["Estoque CD"] > 0) & reg.utc_id.isin(regs_com_loja))]

    # Colunas por loja (só lojas analisadas das regiões escolhidas)
    lojas = _q(f"""
        WITH prod AS ({prod_sql}), lojas AS ({Filtros.LOJAS_SQL})
        SELECT l.filial_cod, l.loja, l.utc_id, pr.gtin,
               coalesce(v.und, 0) AS venda, coalesce(e.quantidade, 0) AS estoque, a.recomendacao
        FROM lojas l CROSS JOIN prod pr
        LEFT JOIN (SELECT cnpj, gtin, sum(und) AS und FROM vendas_rede WHERE periodo = $periodo GROUP BY ALL) v
               ON v.cnpj = l.cnpj AND v.gtin = pr.gtin
        LEFT JOIN v_estoque e ON e.filial_cod = l.filial_cod AND e.gtin = pr.gtin AND e.data_posicao = $posicao
        LEFT JOIN analise_loja a ON a.periodo = $periodo AND a.filial_cod = l.filial_cod AND a.gtin = pr.gtin
        WHERE l.utc_id IN ({Filtros.REGIOES_SQL})
    """, f, extra)
    if not lojas.empty:
        mov = lojas.groupby(["utc_id", "gtin"])[["venda", "estoque"]].sum().sum(axis=1)
        com_loja = set(mov[mov > 0].index)
        extra_linhas = [k for k in com_loja if k not in set(zip(reg.utc_id, reg.gtin))]
        if extra_linhas:
            todos = _q(f"""
                WITH prod AS ({prod_sql}), regs AS ({Filtros.REGIOES_SQL})
                SELECT pr.gtin, r.utc_id, pr.ean AS "EAN", pr.descricao AS "Produto", pr.tipo AS "Tipo",
                       pr.fabricante AS "Fabricante", replace(r.utc_desc, 'MG-', '') AS "Região",
                       0.0 AS "Concorrência (un)", 0.0 AS "Concorrência (R$)",
                       0.0 AS "Rede na região (un)", 0.0 AS "Estoque CD"
                FROM prod pr CROSS JOIN regs JOIN regioes r ON r.utc_id = regs.utc_id
            """, f, extra)
            todos = todos[[(u, g) in set(extra_linhas) for u, g in zip(todos.utc_id, todos.gtin)]]
            reg = pd.concat([reg, todos], ignore_index=True)

    if reg.empty:
        st.info("Nenhum dos produtos escolhidos teve venda, demanda ou estoque nas regiões filtradas.")
        return
    if len(reg) > 1000:
        st.warning(f"{n(len(reg))} linhas encontradas. Mostrando as 1.000 com maior demanda da "
                   "concorrência; refine os filtros para ver as demais.")
        reg = reg.sort_values("Concorrência (un)", ascending=False).head(1000)

    acao = {"LEVAR": "Levar", "ABASTECER": "Abastecer", "PARADO": "Parado", "MANTER": "Manter"}
    tabela = reg.copy()
    if not lojas.empty:
        lojas["acao"] = lojas.recomendacao.map(acao).fillna("—")
        for loja in lojas.drop_duplicates("filial_cod").sort_values(["utc_id", "loja"]).loja:
            parte = lojas[lojas.loja == loja]
            idx = dict(zip(zip(parte.utc_id, parte.gtin), parte.index))
            chave = list(zip(tabela.utc_id, tabela.gtin))
            pos = [idx.get(k) for k in chave]
            for col, nome in (("venda", "venda"), ("estoque", "estoque"), ("acao", "ação")):
                tabela[f"{loja}: {nome}"] = [parte.at[i, col] if i is not None else None for i in pos]
    tabela = tabela.sort_values(["Região", "Concorrência (un)"], ascending=[True, False]).drop(
        columns=["gtin", "utc_id"])
    st.write(f"**{n(len(tabela))} linha(s)**: um produto por região.")
    _tabela(tabela, "consulta", f"consulta_{f.periodo}.csv")
    st.caption("Concorrência e Rede na região são da região inteira. As colunas de cada loja só aparecem "
               "para lojas analisadas (no escopo) e ficam vazias nas linhas de outras regiões.")


# ---------------------------------------------------------------- Incluir no mix
def pagina_mix(f: Filtros):
    if _sem_dados(f):
        return
    _cabecalho("Incluir no mix",
               "Produtos que a concorrência vende na região e que nenhuma loja Rede da região, nem o CD, tem. "
               "Funciona para qualquer região importada, mesmo sem loja Rede. Sem região escolhida, "
               "mostra todas.", f)
    df = _q(f"""
        WITH regs AS ({Filtros.REGIOES_SQL})
        SELECT replace(r.utc_desc, 'MG-', '') AS "Região", p.ean AS "EAN", p.descricao AS "Descrição",
               coalesce(p.secao, 'Pendente') AS "Seção", p.tipo AS "Tipo", p.fabricante AS "Fabricante",
               m.conc_und AS "Concorrência (un)", m.conc_fat AS "Concorrência (R$)",
               m.prioridade AS "Prioridade",
               CASE WHEN m.cadastrado_rede THEN 'Reativar' ELSE 'Novo' END AS "Cadastro",
               CASE WHEN m.tem_alternativa THEN 'Sim' ELSE 'Não' END AS "Tem alternativa",
               m.alternativa AS "Alternativa interna", m.justificativa AS "Justificativa"
        FROM analise_mix m JOIN v_produtos p USING (gtin) JOIN regioes r USING (utc_id)
        WHERE m.periodo = $periodo AND m.utc_id IN (SELECT utc_id FROM regs) AND {Filtros.PRODUTO_SQL}
        ORDER BY {ORDEM_PRIO.format(c='m.prioridade')}, m.score DESC, m.conc_fat DESC
    """, f)
    c1, c2, c3 = st.columns(3)
    prio = c1.multiselect("Prioridade", ["Alta", "Média", "Baixa"], default=["Alta", "Média"], key="mix_prio")
    cad = c2.multiselect("Cadastro", ["Novo", "Reativar"], key="mix_cad",
                         help="Reativar: o produto já tem código Rede, mas está sem estoque e sem venda")
    alt = c3.segmented_control("Alternativa interna", ["Todas", "Sem alternativa", "Com alternativa"],
                               default="Todas", key="mix_alt")
    if prio:
        df = df[df.Prioridade.isin(prio)]
    if cad:
        df = df[df.Cadastro.isin(cad)]
    if alt == "Sem alternativa":
        df = df[df["Tem alternativa"] == "Não"]
    elif alt == "Com alternativa":
        df = df[df["Tem alternativa"] == "Sim"]

    a, b, c, d = st.columns(4)
    a.metric("Produtos", n(len(df)))
    b.metric("Prioridade alta", n((df.Prioridade == "Alta").sum()))
    c.metric("Faturamento da concorrência", _reais(df['Concorrência (R$)'].sum()))
    d.metric("Sem alternativa interna", n((df["Tem alternativa"] == "Não").sum()))
    if not df.empty:
        por_secao = (df.groupby("Seção")["Concorrência (R$)"].sum().sort_values(ascending=False)
                     .head(15).reset_index())
        por_secao["mil"] = por_secao["Concorrência (R$)"] / 1e3
        _barras(por_secao, "Seção", [("mil", "Concorrência", VERDE)],
                "Faturamento da concorrência nas oportunidades, por seção (R$ mil)", sufixo=" mil")
    _tabela(df, "mix", f"incluir_no_mix_{f.periodo}.csv")


# ---------------------------------------------------------------- listas por loja
def _lista_loja(f: Filtros, rec: str, colunas: str, ordem: str) -> pd.DataFrame:
    return _q(f"""
        WITH lojas AS ({Filtros.LOJAS_SQL})
        SELECT l.loja AS "Loja", p.ean AS "EAN", p.descricao AS "Descrição",
               coalesce(p.secao, 'Pendente') AS "Seção", p.tipo AS "Tipo", {colunas},
               a.prioridade AS "Prioridade", a.justificativa AS "Justificativa"
        FROM analise_loja a JOIN lojas l USING (filial_cod) JOIN v_produtos p USING (gtin)
        LEFT JOIN filiais fo ON fo.filial_cod = a.origem_filial
        WHERE a.periodo = $periodo AND a.recomendacao = '{rec}' AND {Filtros.PRODUTO_SQL}
        ORDER BY {ordem}
    """, f)


def _resumo_lojas(df: pd.DataFrame, valor: str, rotulo: str) -> None:
    if df.empty or df.Loja.nunique() < 2:
        return
    r = df.groupby("Loja").agg(itens=("EAN", "count"), soma=(valor, "sum")).reset_index()
    r = r.rename(columns={"itens": "Itens", "soma": rotulo})
    st.dataframe(r, hide_index=True, width="content",
                 column_config={c: NUM for c in ("Itens", rotulo)})


def pagina_levar(f: Filtros):
    if _sem_dados(f) or _sem_lojas(f):
        return
    _cabecalho("Levar do CD para a loja",
               "A loja não tem e não vende o produto, a região tem demanda e o CD (ou outra loja que vende "
               "e tem sobra) tem estoque.", f)
    df = _lista_loja(f, "LEVAR", """
        a.conc_und AS "Concorrência (un)", a.conc_fat AS "Concorrência (R$)",
        a.est_cd AS "Estoque CD",
        CASE WHEN a.origem = 'Outra loja' THEN 'Loja ' || fo.nome_exibicao ELSE a.origem END AS "Origem",
        a.qtd_sugerida AS "Qtd sugerida", a.alternativa AS "Alternativa na loja" """,
        f"{ORDEM_PRIO.format(c='a.prioridade')}, a.score DESC, a.conc_und DESC")
    c1, c2 = st.columns(2)
    prio = c1.multiselect("Prioridade", ["Alta", "Média", "Baixa"], default=["Alta", "Média"], key="lev_prio")
    origem = c2.segmented_control("Origem", ["Todas", "CD", "Outra loja"], default="Todas", key="lev_org")
    if prio:
        df = df[df.Prioridade.isin(prio)]
    if origem == "CD":
        df = df[df.Origem == "CD"]
    elif origem == "Outra loja":
        df = df[df.Origem.str.startswith("Loja ", na=False)]
    a, b, c = st.columns(3)
    a.metric("Itens", n(len(df)))
    b.metric("Unidades a enviar", n(df["Qtd sugerida"].sum()))
    c.metric("Do CD", n((df.Origem == "CD").sum()))
    _resumo_lojas(df, "Qtd sugerida", "Unidades a enviar")
    _tabela(df, "levar", f"levar_para_loja_{f.periodo}.csv")
    st.caption("Quantidade sugerida: venda estimada da loja (demanda da concorrência × participação da Rede "
               "na seção, dividida pelas lojas Rede da região) para a cobertura alvo, limitada ao que a origem tem.")


def pagina_abastecer(f: Filtros):
    if _sem_dados(f) or _sem_lojas(f):
        return
    _cabecalho("Loja precisa abastecer",
               "Produtos que a loja vende e cujo estoque está abaixo da cobertura mínima. "
               "Origem: primeiro o CD, depois outra loja com sobra, depois compra.", f)
    df = _lista_loja(f, "ABASTECER", """
        a.urgencia AS "Urgência", a.giro AS "Giro", a.venda_und AS "Venda no mês",
        a.est_loja AS "Estoque loja", a.cobertura_dias AS "Cobertura (dias)", a.est_cd AS "Estoque CD",
        CASE WHEN a.origem = 'Outra loja' THEN 'Loja ' || fo.nome_exibicao ELSE a.origem END AS "Origem",
        a.qtd_sugerida AS "Qtd sugerida" """,
        "CASE a.urgencia WHEN 'Zerado' THEN 1 WHEN 'Crítica' THEN 2 ELSE 3 END, "
        f"{ORDEM_PRIO.format(c='a.prioridade')}, a.venda_fat DESC")
    c1, c2, c3 = st.columns(3)
    urg = c1.multiselect("Urgência", ["Zerado", "Crítica", "Abaixo do mínimo"], key="ab_urg")
    giro = c2.segmented_control("Giro", ["Todos", "Giro", "Baixo giro"], default="Giro", key="ab_giro",
                                help="Baixo giro: menos de 3 un/mês; a regra é ter ao menos 1 unidade")
    origem = c3.segmented_control("Origem", ["Todas", "CD", "Outra loja", "Compra"], default="Todas",
                                  key="ab_org")
    if urg:
        df = df[df["Urgência"].isin(urg)]
    if giro in ("Giro", "Baixo giro"):
        df = df[df.Giro == giro]
    if origem in ("CD", "Compra"):
        df = df[df.Origem == origem]
    elif origem == "Outra loja":
        df = df[df.Origem.str.startswith("Loja ", na=False)]

    a, b, c, d = st.columns(4)
    a.metric("Zerados", n((df["Urgência"] == "Zerado").sum()))
    b.metric("Críticos (menos de 7 dias)", n((df["Urgência"] == "Crítica").sum()))
    c.metric("Do CD ou outra loja", n((df.Origem != "Compra").sum()))
    d.metric("Precisam de compra", n((df.Origem == "Compra").sum()))
    _resumo_lojas(df, "Qtd sugerida", "Unidades sugeridas")
    _tabela(df, "abast", f"abastecer_loja_{f.periodo}.csv",
            {"Cobertura (dias)": st.column_config.NumberColumn(format="%.1f")})


def pagina_parado(f: Filtros):
    if _sem_dados(f) or _sem_lojas(f):
        return
    _cabecalho("Estoque parado",
               "Produtos com estoque na loja e nenhuma venda no mês.", f)
    df = _lista_loja(f, "PARADO", """
        a.est_loja AS "Estoque loja", a.capital AS "Custo estimado (R$)",
        a.conc_und AS "Concorrência (un)", a.venda_rede_regiao AS "Venda Rede na região",
        CASE WHEN a.conc_und > 0 THEN 'Concorrência vende'
             WHEN a.venda_rede_regiao > 0 THEN 'Outra loja Rede vende'
             ELSE 'Sem demanda na região' END AS "Situação" """,
        "a.capital DESC NULLS LAST, a.est_loja DESC")
    sit = st.segmented_control(
        "Situação", ["Todas", "Concorrência vende", "Outra loja Rede vende", "Sem demanda na região"],
        default="Todas", key="par_sit",
        help="Concorrência vende: verificar exposição e preço. Outra loja vende: avaliar transferência. "
             "Sem demanda: candidato a devolução ou promoção.")
    if sit and sit != "Todas":
        df = df[df["Situação"] == sit]
    a, b, c = st.columns(3)
    a.metric("Itens", n(len(df)))
    b.metric("Unidades", n(df["Estoque loja"].sum()))
    c.metric("Custo estimado", _reais(df['Custo estimado (R$)'].sum()),
             help="Só para itens com custo conhecido pelas vendas Rede")
    if not df.empty:
        g = (df.groupby("Seção")["Custo estimado (R$)"].sum().sort_values(ascending=False)
             .head(15).reset_index())
        g["mil"] = g["Custo estimado (R$)"] / 1e3
        if g["mil"].sum() > 0:
            _barras(g, "Seção", [("mil", "Custo estimado", AMBAR)],
                    "Custo estimado parado por seção (R$ mil)", sufixo=" mil")
    _resumo_lojas(df, "Custo estimado (R$)", "Custo estimado (R$)")
    _tabela(df, "parado", f"estoque_parado_{f.periodo}.csv")
    st.caption("Custo estimado pelas vendas Rede registradas na base de mercado. Itens que a base de mercado conta por "
               "embalagem (ex.: caixa com 100 seringas) podem aparecer com custo alto demais.")
