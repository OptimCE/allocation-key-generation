<p align="center">
  <img src="logo.svg" alt="OptimCE-logo" width="160">
</p>

# OptimCE — Generatie van verdeelsleutels

[![Website](https://img.shields.io/badge/Website-optimce.be-2e7d32.svg)](https://www.optimce.be/nl/)
[![Licentie](https://img.shields.io/badge/Licentie-Apache%202.0-blue.svg)](../LICENSE)
[![en](https://img.shields.io/badge/lang-en-lightgrey.svg)](../README.md)
[![fr](https://img.shields.io/badge/lang-fr-lightgrey.svg)](README.fr.md)
[![de](https://img.shields.io/badge/lang-de-lightgrey.svg)](README.de.md)
[![nl](https://img.shields.io/badge/lang-nl-43a047.svg)](README.nl.md)

De dienst voor **generatie van verdeelsleutels** berekent *verdeelsleutels*
(Frans *clés de répartition*) voor energiedeling in hernieuwbare-energie­­­­gemeenschappen.
Op basis van de meetgegevens van een gemeenschap — één verbruikstijdreeks per
lid plus één gedeelde injectiereeks (productie) — voert hij een
optimalisatiealgoritme uit om te bepalen hoe de gedeelde productie onder de
leden moet worden verdeeld, en produceert hij kandidaat-verdeelsleutels die een
gebruiker kan bekijken en opslaan.

Deze dienst maakt deel uit van het OptimCE-platform. Hij wordt ontwikkeld binnen
de [OptimCE-ontwikkelingsmonorepo](https://github.com/OptimCE/monorepo), die de
volledige stack lokaal draait; de rest van het platform is beschikbaar onder de
[OptimCE-organisatie](https://github.com/OptimCE). Ga voor meer informatie over
het project naar [www.optimce.be](https://www.optimce.be).

## Algoritmen

De verdeelalgoritmen zijn **modulair**: elk bevindt zich onder
`algorithms/algorithms_implemented/` en registreert zichzelf in een automatisch
ontdekt register, waarbij lichte metadata (gebruikt door de API) gescheiden
wordt aangeboden van de zware implementatie (gebruikt door de worker). Er worden
tot nu toe twee algoritmen meegeleverd:

| Algoritme | Aanpak | Opmerkingen |
|---|---|---|
| `olagsa` | Genetisch algoritme met convexe warme start (Linear Optimization and Genetic Algorithm with Atypical Speciation) | Instelbare hyperparameters (populatiegrootte, generaties, kruisings-/mutatiepercentages, …) |
| `brute_force` | Uitputtende opsomming van verdeelsleutels | Beperkt tot een klein aantal iteraties |

Elk algoritme declareert een Pydantic-invoerschema dat als JSON Schema wordt
aangeboden, zodat de frontend een bijpassend dynamisch formulier kan tonen, met
per verzoek vertaalde veldlabels.

## Architectuur

De dienst bestaat uit twee deploybare onderdelen die één codebasis delen:

- **API** (`main.py`, `api/`) — een FastAPI-toepassing. Ze ontvangt
  gegevensuploads, valideert ze, slaat het bestand op, bewaart een
  generatierecord en publiceert een taak op NATS. Ze bedient ook de
  lees-/lijst-/verwijderendpoints en de algoritmecatalogus.
- **Worker** (`worker/`) — een langlopende NATS JetStream-consumer. Hij
  downloadt het bestand, parseert het naar matrices, voert het CPU-intensieve
  algoritme uit in een procespool en schrijft de resultaten (of een
  geregistreerde fout) terug naar de database.

Belangrijkste technologieën:

- **FastAPI** + **Uvicorn** (Python 3.12)
- **SQLAlchemy** (async) + **asyncpg** op **PostgreSQL** — twee databases: de
  CRM-database (lezen) en de eigen database van de dienst
- **Pydantic** / **pydantic-settings** voor validatie en configuratie
- **NATS** (JetStream) voor berichtenverkeer API → worker
- **S3-compatibele objectopslag** (MinIO in ontwikkeling) voor geüploade
  bestanden
- **cvxpy**, **NumPy**, **pandas** voor het numerieke en optimalisatiewerk
  (worker)
- **OpenTelemetry** voor tracing, metrieken en logs
- **i18n** met catalogi voor Engels, Frans, Duits en Nederlands (`locales/`)

### Authenticatie

De dienst vertrouwt op een voorliggende API-gateway (KrakenD, vóór Keycloak): hij
verifieert tokens niet zelf. Door de gateway aangeleverde headers identificeren
de gebruiker en zijn gemeenschap, verzoeken worden voor multi-tenant-isolatie tot
één gemeenschap beperkt, en toegang tot de generatie-endpoints vereist dat de
overeenkomstige functie voor de gemeenschap is ingeschakeld. De dienst is niet
bedoeld om rechtstreeks aan het internet te worden blootgesteld.

## API

Alle generatie-endpoints worden aan de wortel van de dienst aangeboden (het
externe padvoorvoegsel wordt door de gateway toegevoegd). De interactieve
OpenAPI-documentatie (`/docs`, `/redoc`, `/openapi.json`) wordt **alleen bij
`ENV=local`** blootgesteld.

| Methode | Pad | Doel |
|---|---|---|
| `GET` | `/` | Generaties van de gemeenschap van de aanroeper opsommen (gepagineerd) |
| `GET` | `/algorithms` | Beschikbare algoritmen met vertaalde metadata en invoerschema's opsommen |
| `GET` | `/algorithms/{algorithm_name}` | Het vertaalde invoerschema van één algoritme ophalen |
| `GET` | `/key/{id_key}` | Eén gegenereerde verdeelsleutel ophalen |
| `GET` | `/{id}` | De door een generatie geproduceerde sleutels opsommen (gepagineerd) |
| `POST` | `/` | Een generatie starten (`multipart/form-data`: `file`, `name`, `injection_name`, `algorithm_name`, `inputs`) |
| `POST` | `/save` | Een gegenereerde sleutel in het CRM opslaan |
| `DELETE` | `/generation/{id_generation}` | Een volledige generatie verwijderen |
| `DELETE` | `/key/{id_key}` | Eén sleutel verwijderen |

De health-probes bevinden zich onder `/health`: `GET /health/liveness`,
`GET /health/readiness` (controleert de database en NATS) en
`GET /health/health`.

## Projectstructuur

```
allocation-key-generation/
├── main.py            # FastAPI-app: middleware, routers, lifespan (NATS + tracing)
├── api/               # HTTP-laag (generatie-endpoints + health-probes)
├── algorithms/        # Modulair algoritmeframework + geïmplementeerde algoritmen
├── core/              # Overkoepelende infrastructuur (config, db, queue, opslag,
│                      #   beveiliging, middleware, i18n, tracing, fouten)
├── worker/            # NATS-achtergrondworker (dispatcher, solver-pool, persistentie)
├── shared/            # Modellen, constanten en helpers voor API en worker
├── locales/           # i18n-berichtencatalogi (en, fr, de, nl)
├── scripts/           # export_openapi.py + sql/schema.sql (DDL van de lokale DB)
├── tests/             # pytest-suites en fixtures
├── requirements/      # base / api / worker / development / testing / all
└── Dockerfile*        # API- (dev + productie) en worker-images
```

## Aan de slag

### Vereisten

- **Python 3.12**
- **PostgreSQL**
- **Docker** (gebruikt door de testsuite; en de eenvoudigste manier om NATS en
  MinIO voor de volledige flow te verkrijgen)
- Voor de volledige pipeline: een **NATS**-server en een **S3-compatibele**
  objectopslag. De ontwikkelingsstack van de
  [monorepo](https://github.com/OptimCE/monorepo) draaien levert dit alles.

### Installatie

```bash
git clone https://github.com/OptimCE/allocation-key-generation.git
cd allocation-key-generation

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements/development.txt

cp .env.exemple .env.local       # pas vervolgens de waarden aan
```

### Configuratie

De configuratie wordt gelezen uit een `.env.<ENV>`-bestand dat wordt gekozen
door de variabele `ENV` (bijv. `ENV=local` laadt `.env.local`). De
gezaghebbende lijst met instellingen staat in `core/config.py`; voor elke
omgeving worden voorbeeldbestanden geleverd (`.env.exemple` voor lokaal,
`.env.staging.exemple`, `.env.production.exemple` en `.env.test`).

| Variabele | Beschrijving |
|---|---|
| `ENV` | `local`, `test`, `staging` of `production` |
| `CRM_DATABASE_URL` | Async-DSN van de CRM-database (`postgresql+asyncpg://…`) |
| `LOCAL_DATABASE_URL` | Async-DSN van de eigen database van de dienst |
| `NATS_URL` | URL van de NATS-server (vereist buiten local/test) |
| `STORAGE_ENDPOINT` | S3-compatibel endpoint (bijv. MinIO); vereist buiten local/test |
| `STORAGE_BUCKET` | Objectopslag-bucket (standaard `crm-files`) |
| `STORAGE_ACCESS_KEY` / `STORAGE_SECRET_KEY` | Referenties van de objectopslag |
| `STORAGE_REGION` | Regio van de objectopslag (standaard `us-east-1`) |
| `ALLOW_ORIGIN` | Door komma's gescheiden CORS-origins; jokertekens worden buiten local geweigerd |
| `LOGGING_TOKEN`, `LOGGING_TRACES_URL`, `LOGGING_LOGS_URL`, `LOGGING_METRICS_URL` | Configuratie van de OpenTelemetry-exporter (vereist in productie) |

Optionele instellingen voor de verbindingspool (`*_DB_POOL_SIZE`,
`*_DB_MAX_OVERFLOW`, `*_DB_POOL_RECYCLE`, `*_DB_POOL_TIMEOUT`, `*_DB_SSL`) hebben
verstandige standaardwaarden. Pydantic valideert de configuratie bij het
opstarten en weigert te starten als een voor de huidige omgeving vereiste
instelling ontbreekt.

## Uitvoeren

### API

```bash
uvicorn main:app --reload --port 8002
```

Open vervolgens <http://localhost:8002/docs> (beschikbaar omdat `ENV=local`).

### Worker

```bash
python -m worker.main
```

### Met Docker

```bash
# ontwikkelings-API-image (hot-reload)
docker build -t allocation-key-generation .

# worker-image
docker build -f Dockerfile.worker -t allocation-key-generation-worker .
```

`Dockerfile.production` bouwt de productie-API-image. In de
ontwikkelingsstack van de monorepo is de API bereikbaar op hostpoort **8002**.

## Tests en kwaliteit

```bash
pytest            # testsuite (start een wegwerp-PostgreSQL-container via Docker)
ruff check .      # lint
ruff format --check .
mypy .            # typecontrole
```

## Bijdragen

Bijdragen zijn welkom! Lees de
[bijdragerichtlijnen](../CONTRIBUTING.md) en onze
[gedragscode](../CODE_OF_CONDUCT.md) (in het Engels) voordat je een issue of pull
request opent.

## Beveiliging

Om een beveiligingslek te melden, volg je het
[beveiligingsbeleid](../SECURITY.md) — open geen openbaar issue.

## Licentie

Dit project valt onder de [Apache-licentie 2.0](../LICENSE).
