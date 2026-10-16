"""Filtros da barra lateral, compartilhados por todas as telas de análise.

Os valores ficam guardados no session_state com prefixo "f_", então continuam valendo
ao trocar de tela.
"""
from dataclasses import dataclass, field
from datetime import date

import streamlit as st


@dataclass
class Filtros:
    periodo: str | None = None
    data_posicao: date | None = None
    regioes: list = field(default_factory=list)   # utc_id
    lojas: list = field(default_factory=list)     # filial_cod
    grupos: list = field(default_factory=list)
    secoes: list = field(default_factory=list)
    tipos: list = field(default_factory=list)
    busca: str = ""

    # ------------------------------------------------------------- SQL
    def params(self) -> dict:
        return {
            "periodo": self.periodo,
            "regioes": self.regioes, "lojas": self.lojas,
            "grupos": self.grupos, "secoes": self.secoes, "tipos": self.tipos,
            "busca": (self.busca or "").strip().upper(),
        }

    # Lojas efetivas: ativas, no escopo, com região, respeitando região e loja escolhidas.
    LOJAS_SQL = """
        SELECT f.filial_cod, f.cnpj, f.nome_exibicao AS loja, fr.utc_id
        FROM filiais f JOIN filial_regiao fr USING (cnpj)
        WHERE f.ativa AND f.no_escopo AND f.tipo = 'LOJA'
          AND (len($regioes) = 0 OR list_contains($regioes, fr.utc_id))
          AND (len($lojas) = 0 OR list_contains($lojas, f.filial_cod))
    """

    # Regiões efetivas: as escolhidas; se nenhuma, as das lojas escolhidas; se nenhuma loja,
    # todas as regiões com dados no mês (com ou sem loja Rede).
    REGIOES_SQL = """
        SELECT DISTINCT d.utc_id FROM demanda_concorrencia d
        WHERE d.periodo = $periodo
          AND (len($regioes) = 0 OR list_contains($regioes, d.utc_id))
          AND (len($lojas) = 0 OR d.utc_id IN (
                SELECT fr.utc_id FROM filiais f JOIN filial_regiao fr USING (cnpj)
                WHERE list_contains($lojas, f.filial_cod)))
    """

    # Produtos que passam nos filtros de grupo, seção, tipo e busca (alias p = v_produtos).
    PRODUTO_SQL = """
        (len($grupos) = 0 OR list_contains($grupos, coalesce(p.grupo, 'Pendente')))
        AND (len($secoes) = 0 OR list_contains($secoes, coalesce(p.secao, 'Pendente')))
        AND (len($tipos) = 0 OR list_contains($tipos, coalesce(p.tipo, 'Sem tipo')))
        AND ($busca = '' OR p.ean LIKE '%' || $busca || '%'
             OR upper(p.descricao) LIKE '%' || $busca || '%'
             OR upper(coalesce(p.descricao_mercado, '')) LIKE '%' || $busca || '%'
             OR upper(coalesce(p.molecula, '')) LIKE '%' || $busca || '%')
    """

    def descricao(self) -> str:
        partes = []
        if self.regioes:
            partes.append(f"{len(self.regioes)} região(ões)")
        if self.lojas:
            partes.append(f"{len(self.lojas)} loja(s)")
        for nome, lista in (("grupo", self.grupos), ("seção", self.secoes), ("tipo", self.tipos)):
            if lista:
                partes.append(f"{nome}: {', '.join(map(str, lista))}")
        if self.busca:
            partes.append(f'busca "{self.busca}"')
        return "; ".join(partes) if partes else "sem filtros"


def _persistente(widget, rotulo, chave, **kw):
    """Widget cujo valor sobrevive à troca de página."""
    wkey = f"_w_{chave}"
    if wkey not in st.session_state and chave in st.session_state:
        st.session_state[wkey] = st.session_state[chave]

    def _salvar():
        st.session_state[chave] = st.session_state[wkey]

    return widget(rotulo, key=wkey, on_change=_salvar, **kw)


def barra_lateral(con) -> Filtros:
    sb = st.sidebar
    execs = con.execute("""
        SELECT periodo, arg_max(data_posicao, id) FROM analise_execucoes GROUP BY 1 ORDER BY 1 DESC
    """).fetchall()
    if not execs:
        sb.info("Nenhuma análise calculada. Importe os arquivos para começar.")
        return Filtros()
    posicoes = dict(execs)
    periodos = list(posicoes)
    for chave, padrao in (("f_periodo", periodos[0]),):
        if st.session_state.get(chave) not in periodos:
            st.session_state[chave] = padrao
            st.session_state.pop(f"_w_{chave}", None)

    sb.markdown("### Filtros")
    periodo = _persistente(sb.selectbox, "Mês de vendas", "f_periodo", options=periodos,
                           format_func=lambda p: f"{p[4:]}/{p[:4]}")
    sb.caption(f"Estoque de {posicoes[periodo].strftime('%d/%m/%Y')}")

    lojas_df = con.execute("""
        SELECT filial_cod, nome_exibicao, utc_id FROM v_filiais
        WHERE ativa AND no_escopo AND tipo = 'LOJA' AND utc_id IS NOT NULL
        ORDER BY nome_exibicao
    """).df()
    regs_df = con.execute("""
        SELECT r.utc_id, replace(r.utc_desc, 'MG-', '') AS nome,
               count(DISTINCT f.filial_cod) AS lojas
        FROM (SELECT DISTINCT utc_id FROM demanda_concorrencia WHERE periodo = ?) d
        JOIN regioes r USING (utc_id)
        LEFT JOIN v_filiais f ON f.utc_id = r.utc_id AND f.ativa AND f.no_escopo AND f.tipo = 'LOJA'
        GROUP BY ALL ORDER BY lojas = 0, nome
    """, [periodo]).df()
    reg_map = {
        u: (nm if q else f"{nm} (sem loja analisada)")
        for u, nm, q in zip(regs_df.utc_id, regs_df.nome, regs_df.lojas)
    }
    for chave, validos in (("f_regioes", set(reg_map)), ("f_lojas", set(lojas_df.filial_cod))):
        atual = st.session_state.get(chave, [])
        filtrado = [x for x in atual if x in validos]
        if filtrado != atual:
            st.session_state[chave] = filtrado
            st.session_state.pop(f"_w_{chave}", None)

    sb.caption(f"{len(reg_map)} regiões com dados neste mês, "
               f"{int((regs_df.lojas > 0).sum())} com loja Rede analisada.")
    regioes = _persistente(sb.multiselect, "Região", "f_regioes", options=list(reg_map),
                           format_func=reg_map.get, placeholder="Todas")
    opc_lojas = lojas_df[lojas_df.utc_id.isin(regioes)] if regioes else lojas_df
    nomes = dict(zip(lojas_df.filial_cod, lojas_df.nome_exibicao))
    if any(l not in set(opc_lojas.filial_cod) for l in st.session_state.get("f_lojas", [])):
        st.session_state["f_lojas"] = [l for l in st.session_state["f_lojas"] if l in set(opc_lojas.filial_cod)]
        st.session_state.pop("_w_f_lojas", None)
    lojas = _persistente(sb.multiselect, "Loja", "f_lojas", options=list(opc_lojas.filial_cod),
                         format_func=nomes.get, placeholder="Todas")

    cat = con.execute("""
        SELECT DISTINCT coalesce(grupo, 'Pendente') AS grupo, coalesce(secao, 'Pendente') AS secao,
               coalesce(tipo, 'Sem tipo') AS tipo
        FROM v_produtos
    """).df()
    grupos = _persistente(sb.multiselect, "Grupo", "f_grupos", options=sorted(cat.grupo.unique()),
                          placeholder="Todos")
    sec_opc = sorted(cat[cat.grupo.isin(grupos)].secao.unique() if grupos else cat.secao.unique())
    if any(s not in sec_opc for s in st.session_state.get("f_secoes", [])):
        st.session_state["f_secoes"] = [s for s in st.session_state["f_secoes"] if s in sec_opc]
        st.session_state.pop("_w_f_secoes", None)
    secoes = _persistente(sb.multiselect, "Seção", "f_secoes", options=sec_opc, placeholder="Todas")
    tipos = _persistente(sb.multiselect, "Tipo de mercado", "f_tipos",
                         options=sorted(cat.tipo.unique()), placeholder="Todos",
                         help="Genérico, Marca, Trade, Exclusivo ou NMed (não medicamento)")
    busca = _persistente(sb.text_input, "EAN, descrição ou molécula", "f_busca")

    if sb.button("Limpar filtros"):
        for k in ("f_regioes", "f_lojas", "f_grupos", "f_secoes", "f_tipos", "f_busca"):
            st.session_state.pop(k, None)
            st.session_state.pop(f"_w_{k}", None)
        st.rerun()

    return Filtros(periodo=periodo, data_posicao=posicoes[periodo], regioes=regioes or [],
                   lojas=lojas or [], grupos=grupos or [], secoes=secoes or [],
                   tipos=tipos or [], busca=busca or "")
