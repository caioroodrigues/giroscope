# GiroScope

### Inteligência comercial para decisões de mix, abastecimento e estoque

**O mercado vende. A rede tem o produto? Em qual loja? Existe saldo no centro de distribuição?** O GiroScope reúne essas respostas em uma aplicação local para apoiar a rotina de compradores e analistas comerciais.

Desenvolvido em **Python, Streamlit e DuckDB**, cruza estoques exportados do ERP com vendas da própria rede e demanda da concorrência, transformando arquivos CSV em análises por produto, loja e região.

## A dor do comercial

Ter relatórios de vendas e estoque não significa ter uma decisão pronta. Para avaliar uma oportunidade, o comercial precisa consultar bases diferentes, relacionar produtos por EAN e código interno, identificar a região e verificar se a necessidade é de compra ou apenas de distribuição do estoque existente.

Esse trabalho manual dificulta responder perguntas práticas:

- O concorrente vende um produto que ainda não trabalhamos na região?
- O produto falta na loja, mas já existe no CD ou em outra filial?
- Qual item tem giro e cobertura abaixo do necessário?
- Onde há estoque sem venda no período analisado?
- Em quais regiões e seções nossa presença está abaixo do mercado observado?

Sem esse cruzamento, uma indisponibilidade local pode parecer falta de mix, uma oportunidade pode passar despercebida e uma compra pode ser avaliada antes de consultar o estoque disponível.

## Como o projeto enfrenta essa dor

O GiroScope organiza as bases em um banco analítico local, relaciona lojas e regiões e aplica regras comerciais para separar **inclusão no mix, transferência, abastecimento, manutenção e estoque parado**.

O resultado é uma lista de trabalho filtrável e exportável em CSV, com o contexto da recomendação. O objetivo é tornar a análise comercial mais consistente e reduzir consultas manuais entre arquivos. Não foram medidos ganhos financeiros, percentuais de redução de ruptura ou economia de horas nesta versão pública.

| Pergunta comercial | Recurso entregue |
| --- | --- |
| Quanto vendemos em relação à concorrência? | Estudo de mercado com participação e cobertura de mix por região/seção |
| O que a concorrência vende e não temos na região analisada? | Lista de oportunidades de inclusão ou reativação de produtos |
| O produto já está disponível para redistribuição? | Consulta por cidade e comparação entre lojas e CD |
| O que precisa chegar à loja? | Sugestões de levar produtos e abastecer itens com cobertura insuficiente |
| Onde há saldo sem giro? | Lista de estoque parado no mês selecionado |
| Como levar a análise para a rotina do comprador? | Filtros compartilhados e exportação das listas em CSV |

## Fluxo dos dados

1. Importar o estoque por filial e informar a data de posição.
2. Importar um CSV de vendas com a rede e a concorrência identificadas por código.
3. Conferir cadastro de lojas, vínculo com regiões e classificação dos produtos.
4. Ajustar os parâmetros de giro, cobertura e prioridade.
5. Consultar as análises e exportar as listas para avaliação comercial.

Os dados ficam em `dados/giroscope.duckdb`. O aplicativo não conecta diretamente ao ERP ou Snowflake: trabalha com arquivos exportados. Não é necessário Docker ou servidor de banco separado.

## Funcionalidades

- Importação de CSV com detecção de separador e codificação UTF-8/Windows-1252.
- Normalização de EAN como texto em GTIN-14 e indicação de inconsistências no dígito verificador.
- Conciliação de unidades lidas e gravadas e histórico das importações.
- Reimportação do estoque por data e de vendas por mês/região, substituindo os recortes correspondentes.
- Associação automática entre loja e região pelo identificador de estabelecimento, com ajustes manuais preservados.
- Classificação de produtos por grupo, seção e tipo, com revisão de equivalências de EAN por código interno.
- Análises de mercado, consulta por cidade, inclusão no mix, distribuição, abastecimento e estoque parado.
- Filtros de mês, região, loja, grupo, seção, tipo e busca por produto.
- Prioridades calculadas dentro de cada região/seção, usando unidades e faturamento.

## Tecnologias

| Tecnologia | Papel |
| --- | --- |
| Python | Importação, regras de negócio e organização do projeto |
| Streamlit | Interface e navegação entre análises |
| DuckDB e SQL | Persistência local, agregações e cruzamento das bases |
| Pandas | Manipulação de tabelas e saídas para interface |
| Plotly | Gráficos de comparação comercial |
| OpenPyXL | Dependência disponível no projeto para suporte a arquivos Excel |

## Executar localmente

Recomendado: **Python 3.11 ou superior** e internet para instalar dependências.

### Windows

Extraia o projeto e execute `iniciar.bat`. O inicializador cria o ambiente virtual, instala as dependências e abre o Streamlit. O Python precisa estar instalado e acessível como `python` ou `py`.

Ou use o PowerShell na pasta do projeto:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

### Linux ou macOS

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m streamlit run app.py
```

Acesse `http://localhost:8501`. Para encerrar pelo terminal, pressione `Ctrl+C`.

## Conhecer o sistema com dados fictícios

A pasta `examples/` contém arquivos sintéticos, sem produtos, documentos ou estabelecimentos reais.

1. Abra **Dados e ajustes → Importar arquivos**.
2. Envie `examples/estoque_demo.csv`, selecione estoque e informe a posição **31/01/2026**.
3. Envie `examples/mercado_demo.csv` no importador Snowflake/mercado. A referência é **janeiro de 2026**.
4. Confira as lojas 101, 102 e o CD 900 em **Lojas e regiões**. Eles já fazem parte do escopo inicial de demonstração.
5. Abra as análises, selecionando janeiro/2026 e a região de demonstração.

Os exemplos exercitam quatro situações: produto com necessidade de abastecimento, produto disponível no CD para levar à loja, saldo sem venda e oportunidade de mix sem estoque na região/CD.

Os códigos e identificadores são exclusivamente de demonstração. EANs foram gerados para o teste e não identificam produtos comerciais neste projeto.

## Usar suas próprias bases

Edite `rede/config.py` antes da primeira importação:

| Configuração | Exemplo público | Ajuste necessário |
| --- | --- | --- |
| `INF_REDE` | `1001` | Código da sua rede em `INF_COD` |
| `INF_CONCORRENCIA` | `9001` | Código da concorrência em `INF_COD` |
| `FILIAIS_ESCOPO` | `[101, 102, 900]` | Filiais que entram inicialmente na análise |
| `FILIAIS_INATIVAS` | `[]` | Filiais que devem iniciar inativas |
| `LABORATORIOS_EXCLUIDOS` | `[]` | Categorias/laboratórios que não representam mercadorias |
| `NOMES_EXIBICAO` | `{}` | Nomes opcionais para exibição de filiais |

As listas iniciais alimentam um banco novo. Em um banco já criado, ajuste parâmetros e cadastros pelas telas; alterar apenas o arquivo não sobrescreve configurações persistidas. Uma filial cujo nome termina em `CD` é inicialmente reconhecida como centro de distribuição; confira o tipo no cadastro.

### Estoque do ERP

Colunas obrigatórias:

```text
COD MERCADORIA;MERCADORIA;EAN;ESTOQUE;LABORATORIO;FILIAL;CNPJ
```

`FILIAL` usa o formato `101 - LOJA EXEMPLO`. A identificação entre estoque e vendas depende de `CNPJ`. O importador descarta saldos negativos/zero e registros sem identificação válida, além das exclusões configuradas. Vírgula decimal é aceita; evite separador de milhares na quantidade.

### Vendas da rede e mercado

O importador mantém o contrato de uma exportação analítica orientada ao varejo farmacêutico. O exemplo `examples/mercado_demo.csv` traz **todos os cabeçalhos obrigatórios**; adapte sua exportação a esse formato.

Campos centrais: `PER_MES` (`AAAAMM`), `UTC_ID_VAREJO`, `INF_COD`, `PDV_CNPJ`, `TX_TIPO_TRANSACAO`, `APRES_EAN`, `APRES_COD_PROD_INFORMANTE`, `VAL_UND`, `VAL_R$CON` e `VAL_R$PCF`.

Somente transações `Demanda` são consideradas. A rede e a concorrência devem estar no mesmo arquivo para cada recorte completo de mês/região. **Reimportar um recorte substitui os dados anteriores desse recorte nos dois grupos**; um arquivo parcial pode remover informações que não vieram nele.

## Regras comerciais e limites

| Recomendação | Critério principal |
| --- | --- |
| Incluir no mix | Concorrência com demanda, sem estoque nas lojas do escopo da região ou CD ativo, e sem venda da rede na região no mês |
| Levar para a loja | Loja sem estoque e venda, demanda regional e disponibilidade no CD ou em outra loja com sobra, conforme regras do motor |
| Abastecer | Giro a partir de 3 unidades/mês e cobertura abaixo de 15 dias; baixo giro sem saldo também pode gerar indicação |
| Estoque parado | Saldo na loja sem venda no mês selecionado |
| Manter | Venda e cobertura consideradas adequadas pelas regras |

Parâmetros iniciais: cobertura mínima de 15 dias, alvo de 30 dias e giro mínimo de 3 unidades/mês, editáveis no sistema. A origem sugerida prioriza CD, outra loja com sobra e depois compra.

- A ausência de estoque é avaliada no **recorte de região e lojas do escopo**, além do CD. Esta versão não aplica bloqueio global de inclusão pela existência do produto em qualquer loja da rede fora desse recorte.
- Estoque e vendas têm referências diferentes: selecione datas coerentes e observe avisos de defasagem.
- “Mercado” significa rede mais concorrência presentes na base importada; não implica cobertura de todo o mercado real.
- Estoque parado considera o mês escolhido; não equivale a uma análise completa de envelhecimento de estoque.
- Estimativas de demanda e sugestões exigem validação do comprador; não geram pedidos ou transferências no ERP.
- O projeto é uma aplicação local. Não inclui autenticação, controle de acesso por usuário ou infraestrutura de produção.
- Esta entrega não inclui backend TypeScript, PostgreSQL ou integração automática com sistemas externos.

## Estrutura

```text
giroscope/
├── app.py
├── iniciar.bat
├── requirements.txt
├── README.md
├── .gitignore
├── .streamlit/config.toml
├── rede/
│   ├── config.py
│   ├── db.py
│   ├── import_estoque.py
│   ├── import_snowflake.py
│   ├── motor.py
│   ├── filtros.py
│   ├── telas.py
│   ├── telas_analise.py
│   └── util.py
├── examples/
│   ├── estoque_demo.csv
│   └── mercado_demo.csv
├── entrada/
└── dados/
```

## Validação

O preparo da versão pública incluiu compilação dos módulos, criação de banco temporário, importação dos exemplos, execução do motor e conferência dos cenários de recomendação. O teste usa dados sintéticos e não mede desempenho em bases de produção.

## Publicação no GitHub

Nome sugerido do repositório: **`giroscope`**.

Descrição sugerida: **Inteligência comercial em Python: análise de mix, demanda da concorrência, abastecimento e estoque por loja e região.**

Topics sugeridos: `python`, `streamlit`, `duckdb`, `data-analysis`, `retail-analytics`, `inventory-management`.

Crie um repositório público, extraia o ZIP e envie seu conteúdo, deixando `README.md` na raiz. A pasta `examples/` pode ser publicada. O `.gitignore` exclui bancos, arquivos de entrada, ambientes e segredos; não publique exportações operacionais ou bancos locais.

A versão pública usa nome e configurações de demonstração. O banco e os identificadores anteriores não são migrados automaticamente. Preserve sua instalação interna em uma pasta separada.

## Evolução possível

Conexão automatizada às fontes, comparação histórica, controle de acesso e validação das sugestões com o resultado comercial. São possibilidades de evolução, não funcionalidades entregues.
