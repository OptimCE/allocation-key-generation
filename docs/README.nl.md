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

## Dimensionering van middelen

De worker lost op in een pool van processen, één per toegewezen CPU min één (de
compose-bestanden geven hem 2 CPU's, dus één solverproces). Wat een generatie
kost, groeit met **deelnemers × tijdstappen**, de grootte van haar
verbruiksmatrix; een kwartierreeks telt 96 tijdstappen per dag.

Solverproces van OLAGSA, gemeten met cvxpy 1.6.3 in de worker-image op één CPU:

| Deelnemers × tijdstappen | Bijvoorbeeld | Piekgeheugen | Eén oplossing |
|---|---|---|---|
| 176.640 | 20 meters × 92 dagen | 0,5 GB | 2,5 s |
| 353.280 | 20 meters × 184 dagen | 0,85 GB | 5,5 s |
| 706.560 | 40 meters × 184 dagen | 1,5 GB | 10,5 s |

Een generatie lost enkele honderden keren op: 208 oplossingen en 9 minuten voor
20 meters × 92 dagen met de standaardhyperparameters en 2 iteraties.
Populatiegrootte en aantal generaties veranderen de looptijd, niet het
piekgeheugen.

- **Limiet.** Een generatie boven **400.000** deelnemers × tijdstappen (ongeveer
  1 GB per solverproces) wordt geweigerd met fout 2024: door de CRM-voorvertoning
  en `POST /from-crm` (bij de andere blokkeringen), door `POST /` voor een
  geüpload bestand, en opnieuw door de worker zodra de gegevens ingelezen zijn.
  Dat is 20 meters voor hoogstens 208 dagen, of 10 meters voor een jaar. De
  waarde staat in `shared/problem_size.py`; verhoog ze alleen samen met het
  geheugen van de worker.
- **Containergeheugen.** Geef de worker ongeveer 0,3 GB plus 1 GB per
  solverproces. De compose-bestanden van de monorepo en van productie zetten
  `mem_limit: 2g` voor één. Zonder limiet wordt een solver die het geheugen van
  de host ontgroeit door OOM beëindigd, en kunnen andere containers meegaan.
- **Solvercrashes.** Een solverproces dat wegens geheugengebrek wordt beëindigd
  (of crasht) breekt de hele pool: elke generatie die op dat moment wordt
  opgelost, faalt mee. Elke generatie wordt opnieuw afgeleverd; een crash vanaf
  de derde aflevering van een generatie markeert ze als mislukt met
  `solver_crashed (likely out of memory)`. Een generatie die gewoon te groot is
  voor de container, draait dus tot drie keer.
- **brute_force** houdt alleen de tijdreeksen in het geheugen van de paar
  iteraties die hij aan het berekenen is, niet die van elke kandidaat-sleutel.
  Met 20 meters pieken 3 iteraties (1.782 sleutels) op 0,09 GB voor 31 dagen,
  0,12 GB voor 92 dagen en 0,21 GB bij de limiet hierboven (208 dagen), in 25 s,
  70 s en 2,7 minuten, vooral in de verdeling `prorata`. 1 of 2 iteraties
  pieken lager en zijn binnen 8 s klaar.
- **Lange runs.** Een generatie mag veel langer lopen dan de `ack_wait` van 10
  minuten van JetStream (`worker/dispatcher.py`): zolang ze verwerkt wordt, ook
  terwijl ze op een vrij solverproces wacht, stuurt de worker elke minuut een
  in-progress-bevestiging. Ze wordt pas opnieuw afgeleverd als die uitblijven,
  10 minuten na de laatste: de worker is gecrasht, heeft NATS verloren of werd
  midden in de run gestopt.

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
