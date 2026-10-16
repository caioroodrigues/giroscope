"""Leitura segura de arquivos CSV de origens diferentes."""
import codecs
import hashlib
import uuid
from pathlib import Path

from .config import PASTA_TEMP


def q(valor) -> str:
    """Literal SQL seguro para caminhos e textos fixos."""
    return "'" + str(valor).replace("'", "''") + "'"


class ImportacaoErro(Exception):
    """Erro de importação com mensagem pronta para o usuário."""


def hash_arquivo(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def detectar_codificacao(caminho: Path, amostra: int = 8 << 20) -> str:
    """UTF-8 se a amostra decodifica sem erro; senão Windows-1252 (padrão do ERP)."""
    with open(caminho, "rb") as f:
        dados = f.read(amostra)
    if dados.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    try:
        dados.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError as e:
        # Corte no meio de um caractere multibyte no fim da amostra não conta.
        if e.start >= len(dados) - 4:
            return "utf-8"
        return "cp1252"


def detectar_separador(caminho: Path, codificacao: str) -> str:
    with open(caminho, "r", encoding=codificacao, errors="replace") as f:
        cabecalho = f.readline()
    candidatos = {";": cabecalho.count(";"), ",": cabecalho.count(","), "\t": cabecalho.count("\t")}
    sep = max(candidatos, key=candidatos.get)
    if candidatos[sep] == 0:
        raise ImportacaoErro(
            "Não foi possível identificar o separador de colunas na primeira linha do arquivo."
        )
    return sep


def preparar_utf8(caminho: Path) -> tuple[Path, str, str]:
    """Garante uma cópia em UTF-8 para o DuckDB ler. Converte em blocos, sem carregar tudo."""
    cod = detectar_codificacao(caminho)
    sep = detectar_separador(caminho, cod)
    if cod == "utf-8":
        return caminho, cod, sep
    PASTA_TEMP.mkdir(parents=True, exist_ok=True)
    destino = PASTA_TEMP / f"{uuid.uuid4().hex}.csv"
    with open(caminho, "r", encoding=cod, errors="replace", newline="") as src, open(
        destino, "w", encoding="utf-8", newline=""
    ) as dst:
        for bloco in iter(lambda: src.read(1 << 20), ""):
            dst.write(bloco)
    return destino, cod, sep


def limpar_temp(caminho: Path, original: Path) -> None:
    if caminho != original and caminho.exists():
        try:
            caminho.unlink()
        except OSError:
            pass


def colunas_csv(con, caminho: Path, sep: str) -> list[str]:
    rel = con.sql(
        f"SELECT * FROM read_csv({q(caminho)}, delim={q(sep)}, header=true, all_varchar=true) LIMIT 0"
    )
    return [c.strip() for c in rel.columns]


def exigir_colunas(encontradas: list[str], exigidas: list[str], nome_arquivo: str) -> None:
    faltando = [c for c in exigidas if c not in encontradas]
    if faltando:
        raise ImportacaoErro(
            f"O arquivo {nome_arquivo} não tem as colunas obrigatórias: {', '.join(faltando)}. "
            f"Colunas encontradas: {', '.join(encontradas[:20])}"
            + ("..." if len(encontradas) > 20 else "")
        )
