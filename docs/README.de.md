<p align="center">
  <img src="logo.svg" alt="OptimCE-Logo" width="160">
</p>

# OptimCE — Erzeugung von Aufteilungsschlüsseln

[![Website](https://img.shields.io/badge/Website-optimce.be-2e7d32.svg)](https://www.optimce.be/de/)
[![Lizenz](https://img.shields.io/badge/Lizenz-Apache%202.0-blue.svg)](../LICENSE)
[![en](https://img.shields.io/badge/lang-en-lightgrey.svg)](../README.md)
[![fr](https://img.shields.io/badge/lang-fr-lightgrey.svg)](README.fr.md)
[![de](https://img.shields.io/badge/lang-de-43a047.svg)](README.de.md)
[![nl](https://img.shields.io/badge/lang-nl-lightgrey.svg)](README.nl.md)

Der Dienst zur **Erzeugung von Aufteilungsschlüsseln** berechnet
*Aufteilungsschlüssel* (französisch *clés de répartition*) für das Energieteilen
in Erneuerbare-Energie-Gemeinschaften. Aus den Messdaten einer Gemeinschaft —
eine Verbrauchszeitreihe pro Mitglied sowie eine geteilte Einspeisereihe
(Erzeugung) — führt er einen Optimierungsalgorithmus aus, um zu bestimmen, wie
die geteilte Erzeugung auf die Mitglieder verteilt werden soll, und erzeugt
Kandidaten-Aufteilungsschlüssel, die ein Benutzer prüfen und speichern kann.

Dieser Dienst ist Teil der OptimCE-Plattform. Er wird innerhalb des
[OptimCE-Entwicklungs-Monorepos](https://github.com/OptimCE/monorepo)
entwickelt, das den vollständigen Stack lokal ausführt; der Rest der Plattform
ist unter der [OptimCE-Organisation](https://github.com/OptimCE) verfügbar. Um
mehr über das Projekt zu erfahren, besuchen Sie
[www.optimce.be](https://www.optimce.be).

## Algorithmen

Die Aufteilungsalgorithmen sind **modular**: Jeder liegt unter
`algorithms/algorithms_implemented/` und registriert sich in einer
automatisch erkannten Registry, wobei er leichtgewichtige Metadaten (von der API
genutzt) getrennt von seiner rechenintensiven Implementierung (vom Worker
genutzt) bereitstellt. Zwei Algorithmen werden bislang mitgeliefert:

| Algorithmus | Ansatz | Hinweise |
|---|---|---|
| `olagsa` | Genetischer Algorithmus mit konvexem Warmstart (Linear Optimization and Genetic Algorithm with Atypical Speciation) | Einstellbare Hyperparameter (Populationsgröße, Generationen, Kreuzungs-/Mutationsraten, …) |
| `brute_force` | Vollständige Aufzählung der Aufteilungsschlüssel | Auf eine kleine Anzahl von Iterationen begrenzt |

Jeder Algorithmus deklariert ein Pydantic-Eingabeschema, das als JSON Schema
bereitgestellt wird, sodass das Frontend ein passendes dynamisches Formular
anzeigen kann — mit pro Anfrage übersetzten Feldbeschriftungen.

## Architektur

Der Dienst teilt sich in zwei Deployables, die sich eine Codebasis teilen:

- **API** (`main.py`, `api/`) — eine FastAPI-Anwendung. Sie nimmt Daten-Uploads
  entgegen, validiert sie, speichert die Datei, persistiert einen
  Erzeugungsdatensatz und veröffentlicht einen Auftrag über NATS. Sie stellt
  außerdem die Lese-/Listen-/Löschendpunkte und den Algorithmenkatalog bereit.
- **Worker** (`worker/`) — ein langlaufender NATS-JetStream-Consumer. Er lädt
  die Datei herunter, parst sie in Matrizen, führt den CPU-intensiven
  Algorithmus in einem Prozesspool aus und schreibt die Ergebnisse (oder einen
  erfassten Fehler) zurück in die Datenbank.

Schlüsseltechnologien:

- **FastAPI** + **Uvicorn** (Python 3.12)
- **SQLAlchemy** (async) + **asyncpg** auf **PostgreSQL** — zwei Datenbanken:
  die CRM-Datenbank (lesend) und die eigene Datenbank des Dienstes
- **Pydantic** / **pydantic-settings** für Validierung und Konfiguration
- **NATS** (JetStream) für die Nachrichtenübermittlung API → Worker
- **S3-kompatibler Objektspeicher** (MinIO in der Entwicklung) für hochgeladene
  Dateien
- **cvxpy**, **NumPy**, **pandas** für die numerische Arbeit und Optimierung
  (Worker)
- **OpenTelemetry** für Tracing, Metriken und Logs
- **i18n** mit Katalogen für Englisch, Französisch, Deutsch und Niederländisch
  (`locales/`)

### Authentifizierung

Der Dienst vertraut einem vorgelagerten API-Gateway (KrakenD, vor Keycloak): Er
prüft Tokens nicht selbst. Vom Gateway gelieferte Header identifizieren den
Benutzer und seine Gemeinschaft, Anfragen werden zur Mandantentrennung auf eine
einzelne Gemeinschaft eingegrenzt, und der Zugriff auf die Erzeugungsendpunkte
setzt voraus, dass die entsprechende Funktion für die Gemeinschaft aktiviert
ist. Der Dienst ist nicht dafür vorgesehen, direkt im Internet exponiert zu
werden.

## API

Alle Erzeugungsendpunkte werden an der Wurzel des Dienstes bereitgestellt (das
externe Pfadpräfix wird vom Gateway hinzugefügt). Die interaktive
OpenAPI-Dokumentation (`/docs`, `/redoc`, `/openapi.json`) wird **nur bei
`ENV=local`** exponiert.

| Methode | Pfad | Zweck |
|---|---|---|
| `GET` | `/` | Erzeugungen der Gemeinschaft des Aufrufers auflisten (paginiert) |
| `GET` | `/algorithms` | Verfügbare Algorithmen mit übersetzten Metadaten und Eingabeschemata auflisten |
| `GET` | `/algorithms/{algorithm_name}` | Übersetztes Eingabeschema eines Algorithmus abrufen |
| `GET` | `/key/{id_key}` | Einen erzeugten Aufteilungsschlüssel abrufen |
| `GET` | `/{id}` | Die von einer Erzeugung produzierten Schlüssel auflisten (paginiert) |
| `POST` | `/` | Eine Erzeugung starten (`multipart/form-data`: `file`, `name`, `injection_name`, `algorithm_name`, `inputs`) |
| `POST` | `/save` | Einen erzeugten Schlüssel im CRM speichern |
| `DELETE` | `/generation/{id_generation}` | Eine gesamte Erzeugung löschen |
| `DELETE` | `/key/{id_key}` | Einen Schlüssel löschen |

Die Health-Probes liegen unter `/health`: `GET /health/liveness`,
`GET /health/readiness` (prüft Datenbank und NATS) und `GET /health/health`.

## Projektstruktur

```
allocation-key-generation/
├── main.py            # FastAPI-App: Middleware, Router, Lifespan (NATS + Tracing)
├── api/               # HTTP-Schicht (Erzeugungsendpunkte + Health-Probes)
├── algorithms/        # Modulares Algorithmen-Framework + implementierte Algorithmen
├── core/              # Querschnittsinfrastruktur (Config, DB, Queue, Speicher,
│                      #   Sicherheit, Middleware, i18n, Tracing, Fehler)
├── worker/            # NATS-Hintergrund-Worker (Dispatcher, Solver-Pool, Persistenz)
├── shared/            # Modelle, Konstanten und Helfer für API und Worker
├── locales/           # i18n-Nachrichtenkataloge (en, fr, de, nl)
├── scripts/           # export_openapi.py + sql/schema.sql (DDL der lokalen DB)
├── tests/             # pytest-Suiten und Fixtures
├── requirements/      # base / api / worker / development / testing / all
└── Dockerfile*        # API- (dev + production) und Worker-Images
```

## Erste Schritte

### Voraussetzungen

- **Python 3.12**
- **PostgreSQL**
- **Docker** (von der Test-Suite genutzt; und der einfachste Weg, NATS und MinIO
  für den vollständigen Ablauf zu erhalten)
- Für die vollständige Pipeline: ein **NATS**-Server und ein
  **S3-kompatibler** Objektspeicher. Das Ausführen des
  [Monorepo](https://github.com/OptimCE/monorepo)-Entwicklungsstacks stellt all
  dies bereit.

### Installation

```bash
git clone https://github.com/OptimCE/allocation-key-generation.git
cd allocation-key-generation

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements/development.txt

cp .env.exemple .env.local       # dann die Werte anpassen
```

### Konfiguration

Die Konfiguration wird aus einer `.env.<ENV>`-Datei gelesen, die durch die
Variable `ENV` ausgewählt wird (z. B. lädt `ENV=local` die Datei `.env.local`).
Die maßgebliche Liste der Einstellungen befindet sich in `core/config.py`; für
jede Umgebung werden Beispieldateien bereitgestellt (`.env.exemple` für lokal,
`.env.staging.exemple`, `.env.production.exemple` und `.env.test`).

| Variable | Beschreibung |
|---|---|
| `ENV` | `local`, `test`, `staging` oder `production` |
| `CRM_DATABASE_URL` | Async-DSN der CRM-Datenbank (`postgresql+asyncpg://…`) |
| `LOCAL_DATABASE_URL` | Async-DSN der eigenen Datenbank des Dienstes |
| `NATS_URL` | URL des NATS-Servers (außerhalb von local/test erforderlich) |
| `STORAGE_ENDPOINT` | S3-kompatibler Endpunkt (z. B. MinIO); außerhalb von local/test erforderlich |
| `STORAGE_BUCKET` | Objektspeicher-Bucket (Standard `crm-files`) |
| `STORAGE_ACCESS_KEY` / `STORAGE_SECRET_KEY` | Zugangsdaten des Objektspeichers |
| `STORAGE_REGION` | Objektspeicher-Region (Standard `us-east-1`) |
| `ALLOW_ORIGIN` | Kommagetrennte CORS-Ursprünge; Platzhalter werden außerhalb von local abgelehnt |
| `LOGGING_TOKEN`, `LOGGING_TRACES_URL`, `LOGGING_LOGS_URL`, `LOGGING_METRICS_URL` | Konfiguration des OpenTelemetry-Exporters (in production erforderlich) |

Optionale Verbindungspool-Einstellungen (`*_DB_POOL_SIZE`, `*_DB_MAX_OVERFLOW`,
`*_DB_POOL_RECYCLE`, `*_DB_POOL_TIMEOUT`, `*_DB_SSL`) haben sinnvolle
Standardwerte. Pydantic validiert die Konfiguration beim Start und verweigert
den Start, wenn eine für die aktuelle Umgebung erforderliche Einstellung fehlt.

## Ausführen

### API

```bash
uvicorn main:app --reload --port 8002
```

Öffnen Sie dann <http://localhost:8002/docs> (verfügbar, da `ENV=local`).

### Worker

```bash
python -m worker.main
```

### Mit Docker

```bash
# Entwicklungs-API-Image (Hot-Reload)
docker build -t allocation-key-generation .

# Worker-Image
docker build -f Dockerfile.worker -t allocation-key-generation-worker .
```

`Dockerfile.production` baut das Produktions-API-Image. Im
Entwicklungsstack des Monorepos ist die API auf dem Host-Port **8002**
erreichbar.

## Tests und Qualität

```bash
pytest            # Test-Suite (startet einen Wegwerf-PostgreSQL-Container über Docker)
ruff check .      # Lint
ruff format --check .
mypy .            # Typprüfung
```

## Mitwirken

Beiträge sind willkommen! Bitte lesen Sie die
[Beitragsrichtlinien](../CONTRIBUTING.md) und unseren
[Verhaltenskodex](../CODE_OF_CONDUCT.md) (auf Englisch), bevor Sie ein Issue oder
einen Pull Request eröffnen.

## Sicherheit

Um eine Sicherheitslücke zu melden, folgen Sie bitte der
[Sicherheitsrichtlinie](../SECURITY.md) — eröffnen Sie kein öffentliches Issue.

## Lizenz

Dieses Projekt steht unter der [Apache-Lizenz 2.0](../LICENSE).
