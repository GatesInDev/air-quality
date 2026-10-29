# Monitoramento da Qualidade do Ar

Projeto acadêmico da Etapa I: integração de MongoDB e Redis com Python para armazenar medições ambientais, produzir resumos horários e apresentá-los no console.

## Escopo desta etapa

Esta pasta prepara a responsabilidade do Vitor indicada na divisão de tarefas:

- ambiente local com MongoDB e Redis via Docker Compose;
- configuração compartilhada em `app/config.py` (banco `QualidadeAr`);
- menu de console com as seis operações acordadas;
- testes de menu e teste end to end de conectividade/persistência nos dois bancos.

Os módulos de simulador, consultas, agregações e gravação/leitura dos resumos são pontos de integração para os responsáveis João, Lucas e Willian. Os itens do menu estão conectados a funções provisórias que identificam o responsável e a entrega pendente.

## Estrutura

```text
qualidade-ar/
├── app/
│   ├── config.py
│   ├── consultas.py
│   ├── db.py
│   ├── features.py
│   ├── main.py
│   ├── menu.py
│   ├── periodo.py
│   ├── sensores.py
│   └── simulador.py
├── tests/
│   ├── e2e/test_consultas_mongo.py
│   ├── e2e/test_mongodb_redis.py
│   ├── e2e/test_simulador_mongo.py
│   ├── test_consultas.py
│   ├── test_menu.py
│   ├── test_periodo.py
│   └── test_simulador.py
├── .env.example
├── .gitignore
├── docker-compose.yml
└── requirements.txt
```

## Iniciar os bancos

Requer Docker Engine e Docker Compose v2.

```bash
cd qualidade-ar
docker compose up -d --wait
```

MongoDB fica disponível em `localhost:27017` e Redis em `localhost:6379`. Os dados persistem nos volumes `mongodb_data` e `redis_data`.

Para parar os serviços mantendo os dados:

```bash
docker compose down
```

Para apagar também os dados persistidos (operação destrutiva):

```bash
docker compose down -v
```

## Executar o programa

```bash
cd qualidade-ar
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python -m app.main
```

No Windows, ative o ambiente com `.venv\\Scripts\\activate`.

## Testes

```bash
python -m pip install -r requirements.txt
pytest -q
```

O teste end to end requer os serviços iniciados com Docker Compose:

```bash
docker compose up -d --wait
pytest -q -m e2e
```

Ele usa um banco MongoDB de teste separado (`QualidadeAr_e2e`), cria uma medição e um Hash Redis de forma isolada e verifica persistência/leitura nos dois serviços. Não apaga nem modifica as coleções de trabalho.

## Geração de dados (João)

A opção 1 do menu chama `app/simulador.py`, que prepara as coleções, cadastra o catálogo de sensores e grava as medições simuladas. Também pode ser executado direto:

```bash
python -m app.simulador --horas 24 --inicio 2026-10-20T00:00:00Z --seed 42
```

Catálogo em `app/sensores.py`: seis sensores (S001 a S006) em Chapecó/SC, Florianópolis/SC e Curitiba/PR. Padrão de geração: 24 horas a cada 5 minutos a partir de `2026-10-20T00:00Z`, totalizando 1.728 documentos. A mesma `seed` produz sempre os mesmos valores. Rodar de novo substitui as medições dos mesmos sensores no mesmo intervalo, sem duplicar.

Modelo gravado no banco `QualidadeAr`, conforme a seção 4 do enunciado:

| Coleção | Campos |
|---|---|
| `Sensores` | `_id` (string, ex.: `S001`), `modelo`, `descricao`, `localizacao{cidade, uf, latitude, longitude}`, `ativo`, `grandezas` |
| `Medicoes` | `sensor_id` (string), `timestamp` (date, UTC), `pm25`, `pm10` (µg/m³), `co2` (ppm), `temperatura` (°C), `umidade` (%) |

Índices em `Medicoes`: `{sensor_id: 1, timestamp: -1}` único (o do enunciado) e `{timestamp: 1}` para filtrar janelas horárias antes do `$lookup` em `Sensores`. As duas coleções têm validador `$jsonSchema` para campos obrigatórios e tipos.

Pontos de contrato para consultas e agregações:

- `timestamp` é sempre UTC e múltiplo de 5 minutos, então a janela `inicio <= timestamp < fim` de uma hora fecha 12 medições por sensor.
- `localizacao.cidade` fica com acento (`Chapecó`); a normalização para a chave Redis (`chapeco`) é feita por quem grava o Hash.
- Para ler `timestamp` como datetime ciente de fuso use `app.db.get_database()`, que cria o cliente com `tz_aware=True`.

## Consultas e resumo por sensor (Lucas)

Opções 2 e 3 do menu, implementadas em `app/consultas.py`. A janela horária compartilhada fica em `app/periodo.py`:

```python
from app.periodo import janela_horaria

janela_horaria(datetime(2026, 10, 20, 10, 37, tzinfo=timezone.utc))
# -> (2026-10-20 10:00 UTC, 2026-10-20 11:00 UTC)  # inicio_periodo <= t < fim_periodo
```

**Opção 2 (consulta por sensor e intervalo).** Pede o sensor, o início (inclusivo) e o fim (exclusivo) em UTC, no formato ISO 8601 (`2026-10-20T10:00Z`). Deixando em branco, usa a primeira hora com dados do sensor. Executa `find({sensor_id, timestamp: {$gte, $lt}})` ordenado por `timestamp`, consulta atendida pelo índice `{sensor_id: 1, timestamp: -1}`. Medições inválidas aparecem marcadas com `*`.

**Opção 3 (resumo horário por sensor).** Pede o sensor e o intervalo; em branco, resume todas as horas com dados. Pipeline de agregação:

| Estágio | O que faz |
|---|---|
| `$match` | sensor, janela `[$gte inicio, $lt fim)` e filtro de medição válida |
| `$group` | agrupa por `sensor_id` e hora (`$dateTrunc` em UTC); `$sum` para `qt_medicoes`, `$avg` das cinco grandezas, `$max` de `pm25` e `pm10` |
| `$sort` | por sensor e hora |
| `$project` | `sensor_id`, `inicio_periodo`, `fim_periodo` (`$dateAdd` +1 h), `qt_medicoes`, `pm25_media`, `pm10_media`, `co2_media`, `temperatura_media`, `umidade_media` (arredondadas com `$round` em 2 casas), `pm25_max`, `pm10_max` |

### Medição válida

Uma medição só entra nos resumos se as cinco grandezas existirem, forem numéricas (não nulas, não texto, não NaN) e estiverem nestas faixas:

| Campo | Faixa aceita | Motivo |
|---|---|---|
| `pm25`, `pm10` | ≥ 0 µg/m³ | concentração não pode ser negativa |
| `co2` | ≥ 0 ppm | idem |
| `temperatura` | −50 a 60 °C | fora disso é falha de leitura |
| `umidade` | 0 a 100 % | umidade relativa é percentual |

O validador `$jsonSchema` de `Medicoes` já recusa quase todos esses casos na inserção, mas o filtro é repetido no `$match` porque dados gravados antes do validador ou com `bypass_document_validation` não passam por ele. O teste e2e insere medições inválidas dessa forma e confere que o resumo as ignora. Horas sem nenhuma medição válida não geram resumo (`None`).

### Funções para o Redis (Willian)

Todas devolvem dicionários Python com os campos acima, nessa ordem, com `inicio_periodo`/`fim_periodo` como `datetime` UTC. `para_redis(resumo)` converte um resumo nos campos de texto do Hash, no formato da seção 6 do enunciado (`inicio_periodo` = `"2026-10-20T10:00:00Z"`), sem `sensor_id`, que já está na chave:

```python
from app.consultas import para_redis, ultimos_resumos_por_sensor

for sensor_id, resumo in ultimos_resumos_por_sensor(db).items():
    redis_client.hset(f"qualidadear:sensor:{sensor_id}", mapping=para_redis(resumo))
```

| Função | Retorno |
|---|---|
| `resumo_horario_sensor(db, sensor_id, hora)` | resumo da hora que contém `hora`, ou `None` |
| `ultimo_resumo_sensor(db, sensor_id)` | resumo da hora da medição válida mais recente, ou `None` |
| `ultimos_resumos_por_sensor(db)` | `{"S001": {...}, ...}` com o último resumo de cada sensor |
| `resumos_horarios(db, sensor_id, inicio, fim)` | lista de resumos por hora; `sensor_id=None` traz todos os sensores |
| `consultar_medicoes(db, sensor_id, inicio, fim)` | lista das medições, sem `_id` |
| `para_redis(resumo)` | `dict[str, str]` pronto para `HSET ... mapping=` |

## Contrato de integração do menu

| Opção | Operação | Integração/responsável |
|---|---|---|
| 1 | Gerar dados | João (integrado: `app/simulador.py`) |
| 2 | Consultar sensor por intervalo | Lucas (integrado: `app/consultas.py`) |
| 3 | Resumo horário por sensor | Lucas (integrado: `app/consultas.py`) |
| 4 | Resumo horário por cidade | Willian |
| 5 | Atualizar Hashes no Redis | Willian |
| 6 | Exibir Hashes do Redis | Willian |

Os módulos de cada responsável devem ser ligados às funções correspondentes em `app/features.py`; as chamadas e a ordem do menu já estão cobertas por testes unitários.
