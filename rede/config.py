"""Configurações e valores iniciais.

Listas e parâmetros iniciais preenchem o banco na primeira execução.
Depois, ajuste cadastros e parâmetros pelas telas. Os códigos INF_REDE e
INF_CONCORRENCIA são lidos deste módulo em cada execução do processo.
"""
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PASTA_DADOS = BASE / "dados"
PASTA_ENTRADA = BASE / "entrada"
PASTA_TEMP = PASTA_DADOS / "tmp"
DB_PATH = PASTA_DADOS / "giroscope.duckdb"

# Laboratórios que não são mercadoria (ativo, combustível, taxas, material de uso).
LABORATORIOS_EXCLUIDOS = []

# Filiais técnicas ou inativas (poucos itens, quase todos negativos).
FILIAIS_INATIVAS = []

# Filiais fictícias dos exemplos. Ajuste para seu cadastro antes de importar.
FILIAIS_ESCOPO = [101, 102, 900]

NOMES_EXIBICAO = {}

# Informantes na tabela do Snowflake.
INF_REDE = "1001"
INF_CONCORRENCIA = "9001"

# Agrupamento inicial das seções da base de mercado (editável na tela Classificação).
GRUPOS_POR_SECAO = {
    "Medicamentos Prescritos": "Medicamentos",
    "Medicamentos Isentos de Prescricao": "Medicamentos",
    "OTC": "Medicamentos",
    "Dermo/Nutricosmeticos": "Dermocosméticos",
    "Cuidados com a pele": "Dermocosméticos",
    "Cabelos": "Higiene e beleza",
    "Higiene Pessoal": "Higiene e beleza",
    "Higiene Oral": "Higiene e beleza",
    "Produtos de Beleza": "Higiene e beleza",
    "Nutricao adulta": "Vitaminas e suplementos",
    "Infantil": "Infantil",
    "Primeiros Socorros": "Saúde e cuidados",
    "Cuidados ao Paciente": "Saúde e cuidados",
    "Conveniencia": "Conveniência",
}

GRUPOS = [
    "Medicamentos",
    "Dermocosméticos",
    "Higiene e beleza",
    "Vitaminas e suplementos",
    "Infantil",
    "Saúde e cuidados",
    "Conveniência",
    "Outros",
]

TIPOS_MERCADO = ["Generico", "Marca", "Trade", "Exclusivo", "NMed"]
