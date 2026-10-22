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
│   ├── features.py
│   ├── main.py
│   └── menu.py
├── tests/
│   ├── e2e/test_mongodb_redis.py
│   └── test_menu.py
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

## Contrato de integração do menu

| Opção | Operação | Integração/responsável |
|---|---|---|
| 1 | Gerar dados | João |
| 2 | Consultar sensor por intervalo | Lucas |
| 3 | Resumo horário por sensor | Lucas |
| 4 | Resumo horário por cidade | Willian |
| 5 | Atualizar Hashes no Redis | Willian |
| 6 | Exibir Hashes do Redis | Willian |

Os módulos de cada responsável devem ser ligados às funções correspondentes em `app/features.py`; as chamadas e a ordem do menu já estão cobertas por testes unitários.
