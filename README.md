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
│   ├── db.py
│   ├── features.py
│   ├── main.py
│   ├── menu.py
│   ├── sensores.py
│   └── simulador.py
├── tests/
│   ├── e2e/test_mongodb_redis.py
│   ├── e2e/test_simulador_mongo.py
│   ├── test_menu.py
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

## Contrato de integração do menu

| Opção | Operação | Integração/responsável |
|---|---|---|
| 1 | Gerar dados | João (integrado: `app/simulador.py`) |
| 2 | Consultar sensor por intervalo | Lucas |
| 3 | Resumo horário por sensor | Lucas |
| 4 | Resumo horário por cidade | Willian |
| 5 | Atualizar Hashes no Redis | Willian |
| 6 | Exibir Hashes do Redis | Willian |

Os módulos de cada responsável devem ser ligados às funções correspondentes em `app/features.py`; as chamadas e a ordem do menu já estão cobertas por testes unitários.
