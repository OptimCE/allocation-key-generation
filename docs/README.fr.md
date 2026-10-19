<p align="center">
  <img src="logo.svg" alt="Logo OptimCE" width="160">
</p>

# OptimCE — Génération des clés de répartition

[![Site web](https://img.shields.io/badge/Site%20web-optimce.be-2e7d32.svg)](https://www.optimce.be)
[![Licence](https://img.shields.io/badge/Licence-Apache%202.0-blue.svg)](../LICENSE)
[![en](https://img.shields.io/badge/lang-en-lightgrey.svg)](../README.md)
[![fr](https://img.shields.io/badge/lang-fr-43a047.svg)](README.fr.md)
[![de](https://img.shields.io/badge/lang-de-lightgrey.svg)](README.de.md)
[![nl](https://img.shields.io/badge/lang-nl-lightgrey.svg)](README.nl.md)

Le service de **génération des clés de répartition** calcule les *clés de
répartition* du partage d'énergie pour les communautés d'énergie renouvelable.
À partir des données de mesure d'une communauté — une série temporelle de
consommation par membre plus une série d'injection (production) partagée —, il
exécute un algorithme d'optimisation pour déterminer comment répartir la
production partagée entre les membres, et produit des clés de répartition
candidates qu'un utilisateur peut examiner et enregistrer.

Ce service fait partie de la plateforme OptimCE. Il est développé au sein du
[monorepo de développement OptimCE](https://github.com/OptimCE/monorepo), qui
exécute la stack complète en local ; le reste de la plateforme est disponible
sous l'[organisation OptimCE](https://github.com/OptimCE). Pour en savoir plus
sur le projet, consultez [www.optimce.be](https://www.optimce.be).

## Algorithmes

Les algorithmes de répartition sont **modulaires** : chacun se trouve sous
`algorithms/algorithms_implemented/` et s'enregistre dans un registre
auto-découvert, en exposant des métadonnées légères (utilisées par l'API)
séparément de son implémentation lourde (utilisée par le worker). Deux
algorithmes sont fournis à ce jour :

| Algorithme | Approche | Remarques |
|---|---|---|
| `olagsa` | Algorithme génétique avec démarrage à chaud convexe (Linear Optimization and Genetic Algorithm with Atypical Speciation) | Hyperparamètres ajustables (taille de population, générations, taux de croisement/mutation, …) |
| `brute_force` | Énumération exhaustive des clés de répartition | Limité à un petit nombre d'itérations |

Chaque algorithme déclare un schéma d'entrée Pydantic servi sous forme de JSON
Schema, afin que le frontend puisse afficher un formulaire dynamique
correspondant, avec des libellés de champs traduits à chaque requête.

## Architecture

Le service se décompose en deux déployables partageant une base de code unique :

- **API** (`main.py`, `api/`) — une application FastAPI. Elle reçoit les envois
  de données, les valide, stocke le fichier, persiste un enregistrement de
  génération et publie une tâche sur NATS. Elle sert également les endpoints de
  lecture/liste/suppression et le catalogue d'algorithmes.
- **Worker** (`worker/`) — un consommateur NATS JetStream de longue durée. Il
  télécharge le fichier, le convertit en matrices, exécute l'algorithme
  gourmand en CPU dans un pool de processus, et réécrit les résultats (ou un
  échec enregistré) dans la base de données.

Technologies clés :

- **FastAPI** + **Uvicorn** (Python 3.12)
- **SQLAlchemy** (async) + **asyncpg** sur **PostgreSQL** — deux bases de
  données : la base du CRM (lecture) et la base propre au service
- **Pydantic** / **pydantic-settings** pour la validation et la configuration
- **NATS** (JetStream) pour la messagerie API → worker
- **Stockage objet compatible S3** (MinIO en développement) pour les fichiers
  envoyés
- **cvxpy**, **NumPy**, **pandas** pour le calcul numérique et l'optimisation
  (worker)
- **OpenTelemetry** pour le traçage, les métriques et les logs
- **i18n** avec des catalogues en anglais, français, allemand et néerlandais
  (`locales/`)

### Authentification

Le service fait confiance à une passerelle API en amont (KrakenD, devant
Keycloak) : il ne vérifie pas les jetons lui-même. Des en-têtes fournis par la
passerelle identifient l'utilisateur et sa communauté, les requêtes sont
cloisonnées à une seule communauté pour l'isolation multi-tenant, et l'accès aux
endpoints de génération requiert que la fonctionnalité correspondante soit
activée pour la communauté. Le service n'est pas destiné à être exposé
directement sur Internet.

## API

Tous les endpoints de génération sont servis à la racine du service (le préfixe
de chemin externe est ajouté par la passerelle). La documentation OpenAPI
interactive (`/docs`, `/redoc`, `/openapi.json`) n'est exposée **que lorsque
`ENV=local`**.

| Méthode | Chemin | Objet |
|---|---|---|
| `GET` | `/` | Lister les générations de la communauté de l'appelant (paginé) |
| `GET` | `/algorithms` | Lister les algorithmes disponibles avec métadonnées et schémas d'entrée traduits |
| `GET` | `/algorithms/{algorithm_name}` | Obtenir le schéma d'entrée traduit d'un algorithme |
| `GET` | `/key/{id_key}` | Obtenir une clé de répartition générée |
| `GET` | `/{id}` | Lister les clés produites par une génération (paginé) |
| `POST` | `/` | Démarrer une génération (`multipart/form-data` : `file`, `name`, `injection_name`, `algorithm_name`, `inputs`) |
| `POST` | `/save` | Enregistrer une clé générée dans le CRM |
| `DELETE` | `/generation/{id_generation}` | Supprimer une génération entière |
| `DELETE` | `/key/{id_key}` | Supprimer une clé |

Les sondes de santé se trouvent sous `/health` : `GET /health/liveness`,
`GET /health/readiness` (vérifie la base de données et NATS) et
`GET /health/health`.

## Structure du projet

```
allocation-key-generation/
├── main.py            # App FastAPI : middleware, routeurs, lifespan (NATS + traçage)
├── api/               # Couche HTTP (endpoints de génération + sondes de santé)
├── algorithms/        # Framework d'algorithmes modulaire + algorithmes implémentés
├── core/              # Infrastructure transverse (config, db, queue, stockage,
│                      #   sécurité, middleware, i18n, traçage, erreurs)
├── worker/            # Worker NATS en arrière-plan (dispatcher, pool de solveurs, persistance)
├── shared/            # Modèles, constantes et helpers utilisés par l'API et le worker
├── locales/           # Catalogues i18n (en, fr, de, nl)
├── scripts/           # export_openapi.py + sql/schema.sql (DDL de la base locale)
├── tests/             # Suites de tests pytest et fixtures
├── requirements/      # base / api / worker / development / testing / all
└── Dockerfile*        # Images API (dev + production) et worker
```

## Prise en main

### Prérequis

- **Python 3.12**
- **PostgreSQL**
- **Docker** (utilisé par la suite de tests ; et le moyen le plus simple
  d'obtenir NATS et MinIO pour le flux complet)
- Pour le pipeline complet : un serveur **NATS** et un stockage objet
  **compatible S3**. Exécuter la stack de développement du
  [monorepo](https://github.com/OptimCE/monorepo) fournit tout cela.

### Installation

```bash
git clone https://github.com/OptimCE/allocation-key-generation.git
cd allocation-key-generation

python -m venv .venv
source .venv/bin/activate        # Windows : .venv\Scripts\activate
pip install -r requirements/development.txt

cp .env.exemple .env.local       # puis modifiez les valeurs
```

### Configuration

La configuration est lue depuis un fichier `.env.<ENV>` sélectionné par la
variable `ENV` (p. ex. `ENV=local` charge `.env.local`). La liste de référence
des paramètres se trouve dans `core/config.py` ; des fichiers d'exemple sont
fournis pour chaque environnement (`.env.exemple` pour local,
`.env.staging.exemple`, `.env.production.exemple` et `.env.test`).

| Variable | Description |
|---|---|
| `ENV` | `local`, `test`, `staging` ou `production` |
| `CRM_DATABASE_URL` | DSN async de la base du CRM (`postgresql+asyncpg://…`) |
| `LOCAL_DATABASE_URL` | DSN async de la base propre au service |
| `NATS_URL` | URL du serveur NATS (requise hors local/test) |
| `STORAGE_ENDPOINT` | Endpoint compatible S3 (p. ex. MinIO) ; requis hors local/test |
| `STORAGE_BUCKET` | Bucket de stockage objet (défaut `crm-files`) |
| `STORAGE_ACCESS_KEY` / `STORAGE_SECRET_KEY` | Identifiants du stockage objet |
| `STORAGE_REGION` | Région du stockage objet (défaut `us-east-1`) |
| `ALLOW_ORIGIN` | Origines CORS séparées par des virgules ; les jokers sont rejetés hors local |
| `LOGGING_TOKEN`, `LOGGING_TRACES_URL`, `LOGGING_LOGS_URL`, `LOGGING_METRICS_URL` | Configuration de l'exporteur OpenTelemetry (requise en production) |

Les paramètres de pool de connexions optionnels (`*_DB_POOL_SIZE`,
`*_DB_MAX_OVERFLOW`, `*_DB_POOL_RECYCLE`, `*_DB_POOL_TIMEOUT`, `*_DB_SSL`) ont
des valeurs par défaut raisonnables. Pydantic valide la configuration au
démarrage et refuse de démarrer si un paramètre requis pour l'environnement
courant est manquant.

## Exécution

### API

```bash
uvicorn main:app --reload --port 8002
```

Ouvrez ensuite <http://localhost:8002/docs> (disponible car `ENV=local`).

### Worker

```bash
python -m worker.main
```

### Avec Docker

```bash
# image API de développement (rechargement à chaud)
docker build -t allocation-key-generation .

# image du worker
docker build -f Dockerfile.worker -t allocation-key-generation-worker .
```

`Dockerfile.production` construit l'image API de production. Dans la stack de
développement du monorepo, l'API est accessible sur le port hôte **8002**.

## Dimensionnement des ressources

Le worker résout dans un pool de processus, un par CPU attribué moins un (les
fichiers compose lui donnent 2 CPU, donc un processus de résolution). Le coût
d'une génération croît avec **participants × pas de temps**, la taille de sa
matrice de consommation ; une série au quart d'heure compte 96 pas par jour.

Processus de résolution d'OLAGSA, mesuré avec cvxpy 1.6.3 dans l'image du worker
sur un CPU :

| Participants × pas de temps | Par exemple | Mémoire maximale | Une résolution |
|---|---|---|---|
| 176 640 | 20 compteurs × 92 jours | 0,5 Go | 2,5 s |
| 353 280 | 20 compteurs × 184 jours | 0,85 Go | 5,5 s |
| 706 560 | 40 compteurs × 184 jours | 1,5 Go | 10,5 s |

Une génération résout quelques centaines de fois : 208 résolutions et 9 minutes
pour 20 compteurs × 92 jours avec les hyperparamètres par défaut et 2 itérations.
La taille de population et le nombre de générations changent la durée, pas la
mémoire maximale.

- **Limite.** Une génération au-delà de **400 000** participants × pas de temps
  (environ 1 Go par processus de résolution) est refusée avec l'erreur 2024 :
  par l'aperçu CRM et `POST /from-crm` (parmi les autres blocages), par `POST /`
  pour un fichier envoyé, puis par le worker une fois les données lues. Cela
  correspond à 20 compteurs sur 208 jours au plus, ou 10 compteurs sur un an. La
  valeur se trouve dans `shared/problem_size.py` ; ne l'augmentez qu'avec la
  mémoire du worker.
- **Mémoire du conteneur.** Prévoyez pour le worker environ 0,3 Go plus 1 Go par
  processus de résolution. Les fichiers compose du monorepo et de production
  fixent `mem_limit: 2g` pour un processus. Sans limite, un processus qui dépasse
  la mémoire de l'hôte est tué (OOM) et d'autres conteneurs peuvent tomber
  aussi.
- **Plantages du solveur.** Un processus de résolution tué faute de mémoire (ou
  qui plante) casse tout le pool : chaque génération en cours de résolution à ce
  moment échoue avec lui. Chacune est redistribuée ; un plantage à partir de la
  troisième distribution d'une génération la marque en échec avec
  `solver_crashed (likely out of memory)`. Une génération simplement trop grande
  pour le conteneur s'exécute donc jusqu'à trois fois.
- **brute_force** ne garde en mémoire que les séries temporelles des quelques
  itérations en cours de calcul, et non celles de chaque clé candidate. Avec
  20 compteurs, 3 itérations (1 782 clés) culminent à 0,09 Go pour 31 jours,
  0,12 Go pour 92 jours et 0,21 Go à la limite ci-dessus (208 jours), en 25 s,
  70 s et 2,7 minutes, passées surtout dans la répartition `prorata`. 1 ou
  2 itérations culminent plus bas et se terminent en 8 s au plus.
- **Exécutions longues.** Une génération peut durer bien au-delà des 10 minutes
  d'`ack_wait` de JetStream (`worker/dispatcher.py`) : pendant son traitement, y
  compris pendant qu'elle attend un processus de résolution libre, le worker
  envoie chaque minute un acquittement « en cours ». Elle n'est redistribuée que
  lorsque ceux-ci s'arrêtent, 10 minutes après le dernier : le worker a planté,
  a perdu NATS, ou a été arrêté en pleine exécution.

## Tests et qualité

```bash
pytest            # suite de tests (démarre un conteneur PostgreSQL jetable via Docker)
ruff check .      # lint
ruff format --check .
mypy .            # vérification de types
```

## Contribuer

Les contributions sont les bienvenues ! Merci de lire le
[guide de contribution](../CONTRIBUTING.md) et notre
[code de conduite](../CODE_OF_CONDUCT.md) (en anglais) avant d'ouvrir une issue
ou une pull request.

## Sécurité

Pour signaler une faille de sécurité, veuillez suivre la
[politique de sécurité](../SECURITY.md) — n'ouvrez pas d'issue publique.

## Licence

Ce projet est distribué sous la [licence Apache 2.0](../LICENSE).
