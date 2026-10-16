"""Telas do sistema (importadas pelo app.py)."""
from datetime import date

import pandas as pd
import streamlit as st

from rede import config, db
from rede.import_estoque import importar_estoque
from rede.import_snowflake import importar_snowflake
from rede.util import ImportacaoErro


@st.cache_resource
def _conexao_base():
    return db.conectar()


def con():
    return _conexao_base().cursor()


def n(valor, casas=0) -> str:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return "-"
    texto = f"{valor:,.{casas}f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


def tabela_descartes(descartes: dict) -> None:
    if not descartes:
        st.caption("Nenhuma linha descartada.")
        return
    df = pd.DataFrame(sorted(descartes.items(), key=lambda x: -x[1]), columns=["Motivo", "Linhas"])
    st.dataframe(df, hide_index=True, width="content")


def arquivos_entrada() -> list:
    config.PASTA_ENTRADA.mkdir(parents=True, exist_ok=True)
    return sorted(
        (p for p in config.PASTA_ENTRADA.iterdir() if p.suffix.lower() in (".csv", ".txt")),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )


def _tipo_arquivo(caminho) -> str:
    """Olha só o cabeçalho para sugerir o arquivo certo em cada aba."""
    try:
        with open(caminho, "rb") as f:
            cab = f.readline().decode("utf-8", errors="replace").upper()
    except OSError:
        return ""
    if "INF_COD" in cab and "APRES_EAN" in cab:
        return "snowflake"
    if "ESTOQUE" in cab and "FILIAL" in cab:
        return "estoque"
    return ""


def escolher_arquivo(chave: str, tipo: str):
    arquivos = arquivos_entrada()
    if not arquivos:
        st.info(
            f"Nenhum CSV na pasta de entrada. Copie o arquivo para `{config.PASTA_ENTRADA}` "
            "ou envie pelo campo acima."
        )
        return None
    sugestao = next((i for i, a in enumerate(arquivos) if _tipo_arquivo(a) == tipo), 0)
    return st.selectbox(
        "Arquivo", arquivos, key=chave, index=sugestao,
        format_func=lambda p: f"{p.name}  ({p.stat().st_size / 1e6:,.1f} MB)",
    )


def recalcular(periodos: list | None = None) -> None:
    """Refaz as análises com o estoque mais recente. Chamado após importações e ajustes."""
    from rede import motor
    c = con()
    todos = motor.periodos_disponiveis(c)
    posicoes = motor.posicoes_disponiveis(c)
    alvo = [p for p in (periodos or todos[:1]) if p in todos]
    if not alvo or not posicoes:
        return
    try:
        with st.spinner("Atualizando as análises..."):
            for per in alvo:
                motor.executar(c, per, posicoes[0])
        st.success(
            "Análises atualizadas para " + ", ".join(f"{p[4:]}/{p[:4]}" for p in alvo)
            + f" com o estoque de {posicoes[0].strftime('%d/%m/%Y')}.")
    except ImportacaoErro as e:
        st.warning(f"Os dados foram importados, mas a análise não foi calculada: {e}")


# ---------------------------------------------------------------- Início
def pagina_inicio():
    st.title("GiroScope — Mix e Estoque")
    c = con()
    resumo = c.execute("""
        SELECT
          (SELECT count(*) FROM filiais WHERE ativa AND tipo = 'LOJA'),
          (SELECT count(*) FROM filiais WHERE no_escopo),
          (SELECT count(DISTINCT utc_id) FROM filial_regiao),
          (SELECT max(data_posicao) FROM estoque),
          (SELECT string_agg(DISTINCT periodo, ', ' ORDER BY periodo) FROM demanda_concorrencia),
          (SELECT count(*) FROM v_produtos),
          (SELECT count(*) FROM importacoes)
    """).fetchone()
    lojas, escopo, regioes, ult_est, periodos, produtos, imports = resumo

    if not imports:
        st.subheader("Comece pela importação")
        st.write(
            "1. Importe o CSV de estoque do ERP, informando a data de posição.\n"
            "2. Importe o CSV do Snowflake (vendas Rede e concorrência no mesmo arquivo).\n"
            "3. Confira lojas, regiões e a classificação dos produtos."
        )
        return

    a, b, d, e = st.columns(4)
    a.metric("Lojas ativas", n(lojas), help="Filiais do tipo loja marcadas como ativas")
    b.metric("Filiais no escopo", n(escopo))
    d.metric("Regiões com loja Rede", n(regioes))
    e.metric("Produtos no cadastro", n(produtos))
    st.write(
        f"**Estoque mais recente:** {ult_est.strftime('%d/%m/%Y') if ult_est else 'não importado'}  \n"
        f"**Meses de vendas e concorrência:** {periodos or 'não importados'}"
    )

    pend = c.execute("""
        SELECT count(DISTINCT e.gtin)
        FROM v_estoque e JOIN filiais f USING (filial_cod)
        JOIN v_produtos p USING (gtin)
        WHERE f.no_escopo AND p.origem_classificacao = 'Pendente'
          AND e.data_posicao = (SELECT max(data_posicao) FROM estoque)
    """).fetchone()[0]
    sem_regiao = c.execute("""
        SELECT count(*) FROM v_filiais WHERE ativa AND tipo = 'LOJA' AND no_escopo AND utc_id IS NULL
    """).fetchone()[0]
    if sem_regiao:
        st.warning(f"{sem_regiao} loja(s) do escopo sem região. Confira em Lojas e regiões.")
    if pend:
        st.info(
            f"{n(pend)} produtos com estoque nas filiais do escopo estão sem classificação. "
            "Confira em Classificação."
        )


# ---------------------------------------------------------------- Importar
def pagina_importar():
    st.title("Importar arquivos")
    st.write(
        "Arquivos grandes: copie direto para a pasta de entrada, que é mais rápido que enviar "
        "pelo navegador."
    )
    st.code(str(config.PASTA_ENTRADA), language=None)

    enviado = st.file_uploader("Ou envie um CSV", type=["csv", "txt"])
    if enviado is not None:
        destino = config.PASTA_ENTRADA / enviado.name
        if not destino.exists() or destino.stat().st_size != enviado.size:
            with open(destino, "wb") as f:
                f.write(enviado.getbuffer())
            st.success(f"{enviado.name} salvo na pasta de entrada.")

    aba_est, aba_sf = st.tabs(["Estoque (ERP)", "Vendas e concorrência (Snowflake)"])

    with aba_est:
        st.write(
            "Colunas esperadas: COD MERCADORIA, MERCADORIA, EAN, ESTOQUE, LABORATORIO, FILIAL, CNPJ. "
            "Importar de novo a mesma data de posição substitui a anterior."
        )
        arq = escolher_arquivo("arq_est", "estoque")
        data_pos = st.date_input("Data de posição do estoque", value=None, format="DD/MM/YYYY",
                                 max_value=date.today())
        if st.button("Importar estoque", type="primary", disabled=arq is None or data_pos is None):
            try:
                with st.spinner("Lendo e validando o estoque..."):
                    r = importar_estoque(con(), arq, data_pos)
            except ImportacaoErro as e:
                st.error(str(e))
            else:
                st.success(
                    f"Estoque de {data_pos.strftime('%d/%m/%Y')} importado."
                    + (" A posição anterior desta data foi substituída." if r.substituiu_anterior else "")
                )
                a, b, d = st.columns(3)
                a.metric("Linhas lidas", n(r.linhas_lidas))
                b.metric("Linhas gravadas", n(r.linhas_gravadas))
                d.metric("Linhas descartadas", n(r.linhas_lidas - r.linhas_gravadas))
                st.markdown("**Conciliação**")
                st.write(
                    f"Unidades válidas lidas: {n(r.controle['unidades_validas_lidas'])}  \n"
                    f"Unidades gravadas: {n(r.controle['unidades_gravadas'])}"
                )
                st.markdown("**Descartes por motivo**")
                tabela_descartes(r.descartes)
                for aviso in r.avisos:
                    st.warning(aviso)
                st.caption(
                    f"Arquivo lido como {r.controle['codificacao']}, separador "
                    f"'{r.controle['separador']}'."
                )
                recalcular()

    with aba_sf:
        st.write(
            "Exporte da tabela exportação de mercado com INF_COD 1001 e 9001 no mesmo arquivo. "
            "Físico e digital são somados; devoluções são descartadas. Importar de novo o mesmo mês "
            "e região substitui os dados anteriores."
        )
        arq = escolher_arquivo("arq_sf", "snowflake")
        if st.button("Importar vendas e concorrência", type="primary", disabled=arq is None):
            try:
                with st.spinner("Lendo, consolidando e conferindo totais..."):
                    r = importar_snowflake(con(), arq)
            except ImportacaoErro as e:
                st.error(str(e))
            else:
                st.success(f"Mês(es) {', '.join(r.periodos)} importado(s) para {r.regioes} região(ões).")
                a, b, d = st.columns(3)
                a.metric("Linhas lidas", n(r.linhas_lidas))
                b.metric("Registros consolidados", n(r.linhas_gravadas),
                         help="Linhas agrupadas por mês, região/loja e EAN")
                d.metric("Linhas descartadas", n(sum(r.descartes.values())))
                st.markdown("**Conciliação de unidades**")
                st.dataframe(
                    pd.DataFrame(
                        [(nome, n(l, 1), n(g, 1), "Confere" if abs(l - g) < 0.01 else "Diferença")
                         for nome, l, g in r.conciliacao],
                        columns=["Informante", "Unidades lidas", "Unidades gravadas", "Situação"],
                    ),
                    hide_index=True,
                )
                st.markdown("**Descartes por motivo**")
                tabela_descartes(r.descartes)
                for aviso in r.avisos:
                    st.warning(aviso)
                sem_regiao = con().execute("""
                    SELECT nome_exibicao, no_escopo FROM v_filiais
                    WHERE ativa AND tipo = 'LOJA' AND utc_id IS NULL ORDER BY filial_cod
                """).fetchall()
                do_escopo = [x[0] for x in sem_regiao if x[1]]
                fora = len(sem_regiao) - len(do_escopo)
                if do_escopo:
                    st.warning(
                        "Lojas do escopo sem região (não tiveram venda Rede nos arquivos importados): "
                        + ", ".join(do_escopo)
                    )
                if fora:
                    st.caption(
                        f"{fora} lojas fora do escopo ainda sem região. É o esperado quando só "
                        "parte das regiões foi importada."
                    )
                recalcular(r.periodos)


# ---------------------------------------------------------------- Histórico
def pagina_historico():
    st.title("Histórico de importações")
    df = con().execute("""
        SELECT id AS "Nº", tipo AS "Tipo", arquivo AS "Arquivo",
               strftime(importado_em, '%d/%m/%Y %H:%M') AS "Importado em",
               referencia AS "Referência", linhas_lidas AS "Lidas",
               linhas_gravadas AS "Gravadas", status AS "Situação", mensagem AS "Observações",
               descartes, controle
        FROM importacoes ORDER BY id DESC
    """).df()
    if df.empty:
        st.info("Nenhuma importação feita ainda.")
        return
    st.dataframe(df.drop(columns=["descartes", "controle"]), hide_index=True, width="stretch")
    escolha = st.selectbox("Ver detalhes da importação", df["Nº"])
    linha = df[df["Nº"] == escolha].iloc[0]
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Descartes**")
        st.json(linha["descartes"])
    with c2:
        st.markdown("**Totais de controle**")
        st.json(linha["controle"])


# ---------------------------------------------------------------- Lojas e regiões
def pagina_lojas():
    st.title("Lojas e regiões")
    c = con()
    df = c.execute("""
        SELECT filial_cod, nome_exibicao, nome_origem, cnpj, tipo, ativa, no_escopo,
               coalesce(utc_desc, '') AS regiao, coalesce(municipios, '') AS cidades,
               coalesce(origem_regiao, '') AS origem_regiao
        FROM v_filiais ORDER BY filial_cod
    """).df()
    if df.empty:
        st.info("Importe o estoque para carregar as filiais.")
        return

    st.write(
        "A região de cada loja é ligada automaticamente pelas vendas Rede do Snowflake (CNPJ). "
        "Edite o nome de exibição, se a filial está ativa e se entra no escopo das análises."
    )
    b1, b2, _ = st.columns([1, 1, 2])
    if b1.button("Colocar todas as lojas ativas no escopo"):
        c.execute("UPDATE filiais SET no_escopo = true, atualizado_em = now() WHERE ativa")
        st.success("Todas as filiais ativas (lojas e CD) estão no escopo.")
        recalcular()
    if b2.button("Deixar só o CD no escopo"):
        c.execute("UPDATE filiais SET no_escopo = (tipo = 'CD'), atualizado_em = now()")
        st.info("Lojas retiradas do escopo. Marque as que quer analisar e salve.")
    editado = st.data_editor(
        df, hide_index=True, width="stretch", key="ed_filiais",
        disabled=["filial_cod", "nome_origem", "cnpj", "regiao", "cidades", "origem_regiao"],
        column_config={
            "filial_cod": st.column_config.NumberColumn("Filial", format="%d"),
            "nome_exibicao": "Nome de exibição",
            "nome_origem": "Nome no ERP",
            "cnpj": "CNPJ",
            "tipo": st.column_config.SelectboxColumn("Tipo", options=["LOJA", "CD"]),
            "ativa": "Ativa",
            "no_escopo": "No escopo",
            "regiao": "Região",
            "cidades": "Cidades da região",
            "origem_regiao": "Ligação",
        },
    )
    if st.button("Salvar filiais", type="primary"):
        for _, r in editado.iterrows():
            c.execute(
                "UPDATE filiais SET nome_exibicao=?, tipo=?, ativa=?, no_escopo=?, atualizado_em=now() "
                "WHERE filial_cod=?",
                [r.nome_exibicao, r.tipo, bool(r.ativa), bool(r.no_escopo), int(r.filial_cod)],
            )
        st.success("Filiais salvas.")
        recalcular()

    st.subheader("Ajustar região de uma loja")
    regioes = c.execute("SELECT utc_id, utc_desc, municipios FROM regioes ORDER BY utc_desc").fetchall()
    if not regioes:
        st.info("Importe o arquivo do Snowflake para carregar as regiões.")
        return
    lojas = df[df.tipo == "LOJA"]
    loja = st.selectbox("Loja", lojas.filial_cod,
                        format_func=lambda x: f"{x} - {lojas.set_index('filial_cod').nome_exibicao[x]}")
    cnpj = lojas.set_index("filial_cod").cnpj[loja]
    atual = c.execute("SELECT utc_id, origem FROM filial_regiao WHERE cnpj=?", [cnpj]).fetchone()
    ids = [r[0] for r in regioes]
    rotulos = {r[0]: f"{r[1]} ({r[2]})" for r in regioes}
    idx = ids.index(atual[0]) if atual and atual[0] in ids else 0
    nova = st.selectbox("Região", ids, index=idx, format_func=rotulos.get)
    col1, col2 = st.columns(2)
    if col1.button("Salvar região manual"):
        c.execute("DELETE FROM filial_regiao WHERE cnpj=?", [cnpj])
        c.execute("INSERT INTO filial_regiao VALUES (?, ?, 'MANUAL', NULL, now())", [cnpj, nova])
        st.success("Região salva. Ela não será alterada pelas próximas importações.")
        st.rerun()
    if atual and atual[1] == "MANUAL" and col2.button("Voltar para ligação automática"):
        c.execute("DELETE FROM filial_regiao WHERE cnpj=?", [cnpj])
        st.info("Ajuste removido. A região será ligada de novo na próxima importação do Snowflake.")
        st.rerun()


# ---------------------------------------------------------------- Classificação
def pagina_classificacao():
    st.title("Classificação de produtos")
    c = con()
    if not c.execute("SELECT count(*) FROM v_produtos").fetchone()[0]:
        st.info("Importe os arquivos para montar o cadastro de produtos.")
        return

    st.write(
        "A seção e o tipo de mercado vêm da base de mercado (Snowflake). Produtos que só existem no estoque "
        "ficam pendentes até aparecerem no mercado ou serem classificados aqui."
    )
    resumo = c.execute("""
        WITH est AS (
            SELECT DISTINCT gtin FROM v_estoque e JOIN filiais f USING (filial_cod)
            WHERE f.no_escopo AND e.data_posicao = (SELECT max(data_posicao) FROM estoque)
        )
        SELECT p.origem_classificacao, count(*) FROM v_produtos p SEMI JOIN est USING (gtin) GROUP BY 1
    """).fetchall()
    mapa = dict(resumo)
    a, b, d = st.columns(3)
    a.metric("Classificados pela base de mercado", n(mapa.get("base de mercado", 0)))
    b.metric("Classificados manualmente", n(mapa.get("Manual", 0)))
    d.metric("Pendentes", n(mapa.get("Pendente", 0)))
    st.caption("Contagem de produtos com estoque nas filiais do escopo, na posição mais recente.")

    aba1, aba2, aba3 = st.tabs(["Agrupamento das seções", "Produtos pendentes", "Revisão de EAN"])

    with aba1:
        sec = c.execute("""
            SELECT cs.secao_origem, cs.grupo, cs.manual, count(pm.gtin) AS produtos
            FROM classificacao_secao cs
            LEFT JOIN produtos_mercado pm ON pm.secao_origem = cs.secao_origem
            GROUP BY ALL ORDER BY produtos DESC
        """).df()
        ed = st.data_editor(
            sec, hide_index=True, key="ed_secoes",
            disabled=["secao_origem", "manual", "produtos"],
            column_config={
                "secao_origem": "Seção base de mercado",
                "grupo": st.column_config.SelectboxColumn("Grupo padronizado", options=config.GRUPOS),
                "manual": "Ajustado",
                "produtos": st.column_config.NumberColumn("Produtos", format="%d"),
            },
        )
        if st.button("Salvar agrupamento", type="primary"):
            for _, r in ed.merge(sec, on="secao_origem", suffixes=("", "_old")).iterrows():
                antes = None if pd.isna(r.grupo_old) else r.grupo_old
                depois = None if pd.isna(r.grupo) else r.grupo
                if antes != depois:
                    c.execute(
                        "UPDATE classificacao_secao SET grupo=?, manual=true, atualizado_em=now() "
                        "WHERE secao_origem=?", [depois, r.secao_origem])
            st.success("Agrupamento salvo.")
            st.rerun()

    with aba2:
        busca = st.text_input("Buscar por descrição, EAN ou laboratório")
        pend = c.execute("""
            WITH est AS (
                SELECT gtin, sum(quantidade) AS estoque FROM v_estoque e JOIN filiais f USING (filial_cod)
                WHERE f.no_escopo AND e.data_posicao = (SELECT max(data_posicao) FROM estoque)
                GROUP BY gtin
            )
            SELECT p.gtin, p.ean, p.descricao, p.laboratorio_rede, est.estoque,
                   NULL::VARCHAR AS grupo, p.tipo, NULL::VARCHAR AS observacao
            FROM v_produtos p JOIN est USING (gtin)
            WHERE p.origem_classificacao = 'Pendente'
              AND (? = '' OR p.descricao ILIKE '%' || ? || '%' OR p.ean LIKE '%' || ? || '%'
                   OR p.laboratorio_rede ILIKE '%' || ? || '%')
            ORDER BY est.estoque DESC
            LIMIT 500
        """, [busca, busca, busca, busca]).df()
        st.caption("Até 500 produtos, do maior para o menor estoque. Preencha o grupo e salve.")
        ed = st.data_editor(
            pend, hide_index=True, width="stretch", key="ed_pend",
            disabled=["gtin", "ean", "descricao", "laboratorio_rede", "estoque"],
            column_config={
                "gtin": None,
                "ean": "EAN",
                "descricao": "Descrição Rede",
                "laboratorio_rede": "Laboratório",
                "estoque": st.column_config.NumberColumn("Estoque", format="%d"),
                "grupo": st.column_config.SelectboxColumn("Grupo", options=config.GRUPOS),
                "tipo": st.column_config.SelectboxColumn("Tipo de mercado", options=config.TIPOS_MERCADO),
                "observacao": "Observação",
            },
        )
        if st.button("Salvar classificações", type="primary"):
            salvos = 0
            for _, r in ed[ed.grupo.notna()].iterrows():
                c.execute(
                    "INSERT OR REPLACE INTO classificacao_produto_manual VALUES (?, ?, NULL, ?, ?, now())",
                    [r.gtin, r.grupo, r.tipo if pd.notna(r.tipo) else None,
                     r.observacao if pd.notna(r.observacao) else None])
                salvos += 1
            st.success(f"{salvos} produto(s) classificados. A classificação vale para as próximas importações.")
            st.rerun()

    with aba3:
        st.markdown("**EAN com dígito verificador incorreto**")
        st.dataframe(c.execute("""
            SELECT ean AS "EAN", descricao AS "Descrição", cod_rede AS "Código Rede",
                   CASE WHEN no_cadastro_rede THEN 'Estoque Rede' ELSE 'Só mercado' END AS "Origem"
            FROM v_produtos WHERE NOT ean_valido ORDER BY no_cadastro_rede DESC, descricao
        """).df(), hide_index=True, width="stretch")
        st.markdown("**Mesmo produto com EAN diferente no estoque e na base de mercado**")
        st.caption(
            "Ligados automaticamente pelo código interno Rede que a base de mercado informa. Nas análises, "
            "o estoque desses itens é somado ao EAN da base de mercado.")
        st.dataframe(c.execute("""
            SELECT gtin_exibe(eq.gtin_rede) AS "EAN no estoque", h.descricao AS "Descrição Rede",
                   gtin_exibe(eq.gtin_canonico) AS "EAN base de mercado", m.descricao AS "Descrição base de mercado",
                   eq.cod_rede AS "Código Rede"
            FROM v_equiv_ean eq JOIN produtos_rede h ON h.gtin = eq.gtin_rede
            JOIN produtos_mercado m ON m.gtin = eq.gtin_canonico ORDER BY h.descricao
        """).df(), hide_index=True, width="stretch")
        st.markdown("**Código Rede diferente entre estoque e base de mercado**")
        div = c.execute("""
            SELECT p.ean AS "EAN", p.descricao_rede AS "Descrição Rede",
                   h.cod_rede AS "Código no estoque", m.cod_rede AS "Código na base de mercado"
            FROM v_produtos p JOIN produtos_rede h USING (gtin) JOIN produtos_mercado m USING (gtin)
            WHERE p.divergencia_codigo
        """).df()
        if div.empty:
            st.caption("Nenhuma divergência.")
        else:
            st.dataframe(div, hide_index=True, width="stretch")


# ---------------------------------------------------------------- Parâmetros
PARAMETROS_TELA = [
    ("cobertura_minima_dias", "Cobertura mínima (dias)", 1.0, 1.0, 180.0),
    ("cobertura_alvo_dias", "Cobertura alvo ao abastecer (dias)", 1.0, 1.0, 365.0),
    ("giro_minimo_mes", "Venda mínima para ser produto de giro (un/mês)", 1.0, 1.0, 100.0),
    ("demanda_minima_concorrencia", "Demanda mínima da concorrência na região (un/mês)", 1.0, 0.0, 1000.0),
    ("percentil_alta", "Pontuação para prioridade Alta (0 a 1)", 0.05, 0.0, 1.0),
    ("percentil_media", "Pontuação para prioridade Média (0 a 1)", 0.05, 0.0, 1.0),
    ("peso_unidades", "Peso das unidades na pontuação (0 a 1; o resto é faturamento)", 0.1, 0.0, 1.0),
    ("qtd_inicial_exposicao", "Quantidade mínima ao levar produto novo para a loja (un)", 1.0, 1.0, 100.0),
]


def pagina_parametros():
    st.title("Parâmetros")
    st.write("Critérios usados no cálculo das recomendações. Ao salvar, as análises são atualizadas.")
    c = con()
    with st.form("form_param"):
        novos = {}
        for chave, rotulo, passo, minimo, maximo in PARAMETROS_TELA:
            atual = db.parametro(c, chave)
            desc = c.execute("SELECT descricao FROM parametros WHERE chave = ?", [chave]).fetchone()
            novos[chave] = st.number_input(
                rotulo, min_value=minimo, max_value=maximo, step=passo,
                value=float(atual) if atual not in (None, "") else minimo,
                help=desc[0] if desc else None,
            )
        if st.form_submit_button("Salvar parâmetros", type="primary"):
            if novos["percentil_media"] > novos["percentil_alta"]:
                st.error("A pontuação da prioridade Média não pode ser maior que a da Alta.")
            elif novos["cobertura_alvo_dias"] < novos["cobertura_minima_dias"]:
                st.error("A cobertura alvo não pode ser menor que a cobertura mínima.")
            else:
                for k, v in novos.items():
                    db.salvar_parametro(c, k, f"{v:g}")
                st.success("Parâmetros salvos.")
                recalcular()

    st.divider()
    st.write("Ajustou classificação, lojas ou regiões? Atualize as análises para aplicar.")
    if st.button("Atualizar análises"):
        recalcular()


# ---------------------------------------------------------------- Recomendações (prévia)
