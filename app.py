"""GiroScope — Inteligência Comercial e Estoque.

Para abrir: dê dois cliques em iniciar.bat (Windows).
"""
import streamlit as st

st.set_page_config(page_title="GiroScope | Inteligência Comercial", page_icon="💊", layout="wide")

st.markdown(
    """
    <style>
    .block-container {padding-top: 2rem; max-width: 1400px;}
    h1 {font-weight: 700; letter-spacing: -0.01em;}
    div[data-testid="stMetricValue"] {font-variant-numeric: tabular-nums;}
    section[data-testid="stSidebar"] h3 {margin-top: 0.5rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

from rede import filtros, telas, telas_analise as ta  # noqa: E402

estado = {}


def com_filtros(funcao):
    def pagina():
        funcao(estado["filtros"])
    pagina.__name__ = funcao.__name__
    return pagina


analises = [
    st.Page(com_filtros(ta.pagina_mercado), title="Estudo de mercado", icon="📊", url_path="mercado", default=True),
    st.Page(com_filtros(ta.pagina_consulta), title="Consulta por cidade", icon="🔎", url_path="consulta"),
    st.Page(com_filtros(ta.pagina_mix), title="Incluir no mix", icon="➕", url_path="mix"),
    st.Page(com_filtros(ta.pagina_levar), title="Levar do CD para a loja", icon="🚚", url_path="levar"),
    st.Page(com_filtros(ta.pagina_abastecer), title="Loja precisa abastecer", icon="📦", url_path="abastecer"),
    st.Page(com_filtros(ta.pagina_parado), title="Estoque parado", icon="⏸️", url_path="parado"),
]
dados = [
    st.Page(telas.pagina_inicio, title="Situação dos dados", icon="🏠", url_path="inicio"),
    st.Page(telas.pagina_importar, title="Importar arquivos", icon="📥", url_path="importar"),
    st.Page(telas.pagina_historico, title="Histórico", icon="🗂️", url_path="historico"),
    st.Page(telas.pagina_lojas, title="Lojas e regiões", icon="🏪", url_path="lojas"),
    st.Page(telas.pagina_classificacao, title="Classificação", icon="🏷️", url_path="classificacao"),
    st.Page(telas.pagina_parametros, title="Parâmetros", icon="⚙️", url_path="parametros"),
]

pg = st.navigation({"Análises": analises, "Dados e ajustes": dados})
if pg.url_path in {p.url_path for p in analises} or pg.url_path == "":
    estado["filtros"] = filtros.barra_lateral(telas.con())
pg.run()
