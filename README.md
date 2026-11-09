# Simulateur de bornes énergétiques — V2.1

## 1. Présentation

Ce dépôt contient une plateforme cohérente en **version V2.1**, pilotée par une
API métier, un stockage central PostgreSQL et des services Docker. Les bornes
restent des simulateurs : elles échangent avec le backend par MQTT et ne
pilotent aucun équipement réel.

Le projet contient un seul protocole applicatif : MQTT versionné `1.0`, utilisé
par les simulateurs et le backend.
Les fichiers de configuration JSON ne contiennent que l'identité des bornes et
les plages aléatoires de simulation ; l'état, les réservations, les mesures et
les commandes métier sont centralisés dans PostgreSQL.

## 2. Architecture

```text
Postman / client HTTP
        │
        ▼
   API FastAPI ───────────┐
        │                 │
        ▼                 ▼
 PostgreSQL ◄──────── Worker MQTT ◄──────► Mosquitto ◄──────► Simulateurs
```

- **API FastAPI** : reçoit les opérations métier et expose les états, historiques
  et estimations.
- **PostgreSQL** : base centrale unique du backend, partagée par l'API et le
  worker. Alembic gère l'évolution du schéma.
- **Worker MQTT** : valide et traite les messages des bornes, puis publie les
  commandes sauvegardées dans l'outbox.
- **Mosquitto** : broker de transport des états, mesures et commandes.
- **Simulateurs** : un processus Docker par borne, avec configuration de
  simulation propre. Aucun simulateur ne possède de base locale.
- **Postman** : collection de parcours de l'API.

L'API et le worker sont des processus séparés pour isoler le trafic HTTP du
traitement MQTT. Ils font partie du même backend V2.1 et utilisent le même
modèle de données ; cette séparation n'est pas un mode ancien/nouveau.

### Technologies

| Technologie | Rôle |
|---|---|
| Python 3.11 | Backend, simulateurs et tests. |
| FastAPI + Pydantic | API HTTP, validation des entrées et documentation OpenAPI. |
| SQLAlchemy 2 + Alembic | Modèles relationnels, persistance et migrations. |
| PostgreSQL + psycopg 3 | Stockage central métier. |
| Paho MQTT 2 + Mosquitto | Messages versionnés entre bornes et backend. |
| pytest + httpx | Tests unitaires et d'intégration de l'API et du worker. |
| Docker + Docker Compose | Images et orchestration du système local complet. |
| Postman | Essais fonctionnels manuels de l'API. |

## 3. Structure et rôle des fichiers

```text
.
├── backend/
│   ├── __main__.py                 # Contrôle de configuration
│   ├── settings.py                 # Variables d'environnement
│   ├── api/                        # FastAPI, routes, schémas et sessions
│   ├── application/                # Cas d'utilisation métier
│   ├── domain/                     # Entités, ports, messages, tarifs
│   ├── infrastructure/
│   │   ├── database/               # SQLAlchemy, repositories, migrations
│   │   └── mqtt/                   # Validation et traitement des messages
│   └── worker/                     # Processus consommateur/producteur MQTT
├── business/
│   └── gestionnaire_borne.py       # Cycle du simulateur V2.1
├── infrastructure/
│   └── mqtt_communication.py       # Client MQTT du simulateur
├── config/
│   ├── simulator.json              # Bornes déclarées et valeurs globales
│   └── bornes/                     # Profils de génération de chaque borne
├── mosquitto/mosquitto.conf        # Broker local Docker
├── postman/Borne-V21.postman_collection.json
├── tests/
│   ├── backend/                    # Tests unitaires et API
│   └── integration/                # Test sur broker MQTT réel
├── main.py                         # Point d'entrée des simulateurs
├── Dockerfile.backend
├── Dockerfile.simulator
├── docker-compose.yml
├── alembic.ini
├── requirements.txt
├── requirements-dev.txt
├── pytest.ini
├── .env.example
├── .dockerignore
└── README.md
```

### Détail des composants principaux

| Fichier | Rôle |
|---|---|
| `main.py` | Charge et valide les paramètres globaux et par borne, construit les clients MQTT et démarre les simulateurs configurés. `BORNE_ID` permet de démarrer un seul simulateur. |
| `business/gestionnaire_borne.py` | Génère les index cumulatifs, publie les télémétries V2.1, traite activation/coupure et renvoie les accusés MQTT. N'écrit aucune donnée sur disque. |
| `infrastructure/mqtt_communication.py` | Encapsule Paho MQTT : connexion/reconnexion, état online/offline retained, Last Will, abonnements aux commandes et publication. |
| `config/simulator.json` | Liste des bornes, quantité lancée par défaut, hôte/port MQTT locaux et niveau de journalisation. |
| `config/bornes/borne_A07.json`, `borne_B12.json`, `borne_C03.json` | Ressources, intervalle de mesure et plages de génération propres à chacune des bornes. Les prix et réservations n'y sont pas stockés. |
| `backend/settings.py` | Lit et valide les paramètres de connexion PostgreSQL/MQTT du backend. |
| `backend/__main__.py` | Exécute `python -m backend --check` pour valider la configuration du backend. |
| `backend/api/app.py` | Configure FastAPI, les endpoints de santé, la durée de vie SQLAlchemy et les erreurs de base. |
| `backend/api/routes.py` | Endpoints HTTP pour consommateurs, tags, réservations, tarifs, sessions, mesures et seuils. |
| `backend/api/schemas.py` | Modèles Pydantic de requêtes et réponses. |
| `backend/api/dependencies.py` | Ouvre et ferme les sessions SQLAlchemy des requêtes HTTP avec commit/rollback. |
| `backend/api/__main__.py` | Lance Uvicorn sur le port 8000. |
| `backend/application/services.py` | Cas d'utilisation métier indépendants des frameworks et du stockage. |
| `backend/domain/entities.py` | Objets et valeurs métier, incluant les ressources, sessions et tarifs. |
| `backend/domain/ports.py` | Interfaces entre les règles métier et les adaptateurs de stockage. |
| `backend/domain/messages.py` | Contrats JSON MQTT `1.0` et validation des topics et payloads. |
| `backend/domain/tariffs.py` | Calcul du tarif progressif avec des décimaux exacts (`Decimal`). |
| `backend/infrastructure/database/base.py`, `engine.py` | Base SQLAlchemy, moteur et factory de sessions. |
| `backend/infrastructure/database/models.py` | Schéma de consommateurs, tags, bornes, réservations, sessions, mesures, tarifs, seuils et commandes. |
| `backend/infrastructure/database/repositories.py` | Requêtes SQLAlchemy et adaptation des ports métier. |
| `backend/infrastructure/database/migrations/env.py` | Configuration Alembic et sélection de l'URL de base. |
| `backend/infrastructure/database/migrations/versions/73d8da71ddf0_initial_backend_schema.py` | Création du schéma initial. |
| `backend/infrastructure/database/migrations/versions/ad7b9eca39e5_add_command_outbox_and_reading_type_key.py` | Outbox et contraintes d'idempotence/type des mesures. |
| `backend/infrastructure/database/migrations/versions/4d91b2d1eec1_add_threshold_event_history.py` | Historique des événements de seuil. |
| `backend/infrastructure/database/migrations/script.py.mako` | Modèle de génération de révision Alembic. |
| `backend/infrastructure/mqtt/processor.py` | Déduplication et traitement transactionnel des messages, index→delta, changements d'état, seuils et outbox de commandes. |
| `backend/worker/mqtt_worker.py` | Abonnements MQTT, appels au processeur et publication/réessai des commandes de l'outbox. |
| `backend/worker/__main__.py` | Point d'entrée du processus worker. |
| `requirements.txt`, `requirements-dev.txt` | Dépendances d'exécution et outils de tests. |
| `alembic.ini` | Emplacement des révisions de base. |
| `pytest.ini` | Déclare le marqueur `integration` de pytest. |
| `Dockerfile.backend`, `Dockerfile.simulator` | Images Python pour le backend et les simulateurs. |
| `docker-compose.yml` | PostgreSQL, Mosquitto, migrations, API, worker et trois simulateurs. |
| `mosquitto/mosquitto.conf` | Configuration de développement locale du broker. |
| `.env.example` | Exemple des variables d'environnement Compose. |
| `.dockerignore`, `.gitignore` | Exclusion des fichiers locaux, environnements et caches. |
| `postman/Borne-V21.postman_collection.json` | Scénarios HTTP de réservation, tarification, activation, mesures et seuils. |
| `tests/backend/test_settings.py` | Valeurs de configuration et gestion des paramètres invalides. |
| `tests/backend/test_tariffs.py` | Calcul progressif et validation des tranches tarifaires. |
| `tests/backend/test_database_models.py` | Modèles, précision décimale et unicité des messages. |
| `tests/backend/test_api.py` | Tests des endpoints FastAPI et des principaux cas métier. |
| `tests/backend/test_mqtt_processor.py` | Contrat, déduplication, facturation des index et seuils. |
| `tests/backend/test_simulator.py` | Contrat MQTT du simulateur, activation et coupure. |
| `tests/integration/test_mqtt_worker.py` | Test réel de la commande outbox, réponse MQTT et télémétrie persistée. |
| `tests/integration/test_simulator_mqtt.py` | Connexion Paho du simulateur, télémétrie V2.1 et déconnexion propre. |

Les dossiers `business/` et `infrastructure/` portent uniquement le code du
simulateur MQTT V2.1.

## 4. Modèle de données et logique métier

Le schéma PostgreSQL contient :

- `consumers` et `access_tags` : consommateurs, codes de présentation, état et
  expiration des tags ;
- `charging_points` : état online/offline, état de prise et dernière activité ;
- `reservations` et `charging_sessions` : réservations temporelles et sessions ;
- `tariffs`, `tariff_tiers`, `reservation_tariffs` : versions et tranches de prix ;
- `measurements` : index bruts et deltas par ressource/session ;
- `processed_messages` : idempotence à partir de l'UUID MQTT ;
- `device_commands` : demandes, tentatives, réponses et expiration ;
- `thresholds`, `threshold_events` : seuils et événements d'alerte/coupure.

Règles principales :

1. Les ressources sont `ELECTRICITE`, `EAU` et `GAZ`.
2. Le worker valide la version de schéma, le topic, l'identifiant de borne, les
   UUID, la date UTC, l'unité et le type de relevé avant d'enregistrer un
   message.
3. Le message est traité une seule fois même en cas de redélivrance QoS 1.
4. Une télémétrie transporte des index cumulatifs. Le backend conserve ces
   index et calcule le delta à partir du dernier index de **la même session**.
   Le premier relevé d'une session est comparé à zéro. Un index qui diminue est
   considéré comme un compteur réinitialisé et n'est pas facturé.
5. La consommation n'est rattachée à une session que si l'activation est
   confirmée, la prise est active, la réservation correspond et le relevé tombe
   dans la période de réservation.
6. Le tarif progressif utilise `Decimal`. Il est possible de calculer une
   estimation, mais le système ne produit pas de facture.
7. Une activation crée d'abord une session en attente et une commande durable
   dans l'outbox. Seule une confirmation MQTT positive active la session.
8. Les seuils quantité/coût sont évalués sur la consommation de la session. Une
   coupure crée une commande MQTT ; elle n'est effective qu'après la réponse du
   simulateur.
9. Les valeurs sont synthétiques. Les tags sont présentés par l'appelant API ;
   aucun lecteur RFID ou compteur matériel n'est connecté.

## 5. Scénarios de fonctionnement

### Démarrage de la plateforme

1. PostgreSQL et Mosquitto démarrent et passent leurs healthchecks.
2. Le conteneur `migrate` applique `alembic upgrade head`.
3. L'API et le worker démarrent contre la même base PostgreSQL et le même broker.
4. Chaque conteneur simulateur lit le profil JSON de sa borne et publie son état
   MQTT.
5. Le worker crée/met à jour les bornes connues dans la base.

### Créer une réservation et un tarif

1. Créer le consommateur : `POST /api/v1/consumers`.
2. Créer son tag : `POST /api/v1/tags`.
3. Une fois la borne connue via MQTT, créer une réservation :
   `POST /api/v1/reservations`.
4. Créer un tarif progressif : `POST /api/v1/tariffs`.
5. L'associer à la réservation :
   `PUT /api/v1/reservations/{reservation_id}/tariffs`.
6. Estimer le coût :
   `POST /api/v1/reservations/{reservation_id}/cost-estimates`.

### Présenter un tag et activer une borne

1. `POST /api/v1/stations/{charging_point_id}/activate` reçoit le code du tag.
2. Le backend vérifie le tag, son expiration, son statut et l'existence d'une
   réservation en cours pour ce consommateur et cette borne.
3. Le backend crée une session `pending_activation` et une commande `activate`
   dans PostgreSQL, puis répond `202 Accepted`.
4. Le worker MQTT publie la commande depuis l'outbox sur
   `port/bornes/{id}/commandes/activer`.
5. Le simulateur active l'état logique de la prise et répond sur
   `port/bornes/{id}/commandes/activer/reponse`.
6. Le worker vérifie la réponse et met à jour la commande, la session et la
   réservation.
7. Le client interroge `GET /api/v1/commands/{command_id}` et
   `GET /api/v1/sessions/{session_id}` pour lire l'état confirmé.

### Publier et consulter la consommation

1. Chaque simulateur génère à intervalle régulier une télémétrie versionnée avec
   un index par ressource sur `port/bornes/{id}/telemetrie`.
2. Le worker déduplique le message, met à jour l'état de borne et enregistre les
   index.
3. Pour les sessions actives et valides, le backend calcule le delta facturable
   par ressource et l'ajoute aux totaux de session.
4. Consulter la borne avec `GET /api/v1/stations/{id}/state`, les relevés avec
   `GET /api/v1/stations/{id}/measurements`, et les totaux avec
   `GET /api/v1/sessions/{session_id}/consumption`.

### Déclencher un seuil et couper

1. Configurer les seuils par ressource avec
   `PUT /api/v1/stations/{id}/thresholds/{resource}`.
2. Quand quantité ou coût atteint le seuil, le worker persiste un événement.
3. Si le seuil de coupure est atteint, une commande `cut_off` est ajoutée à
   l'outbox puis publiée sur `port/bornes/{id}/commandes/couper`.
4. Le simulateur confirme l'arrêt ; l'événement se consulte via
   `GET /api/v1/sessions/{session_id}/threshold-events`.

## 6. API HTTP

L'API est disponible sur `http://localhost:8000` et sa documentation OpenAPI
sur `http://localhost:8000/docs`.

| Méthode | Route | Fonction |
|---|---|---|
| `GET` | `/health/live`, `/health/ready` | Vie du processus et disponibilité PostgreSQL. |
| `POST` | `/api/v1/consumers` | Créer un consommateur. |
| `POST`, `PATCH` | `/api/v1/tags`, `/api/v1/tags/{code}` | Créer, activer ou désactiver un tag. |
| `POST` | `/api/v1/stations/{id}/activate` | Demander l'activation via un tag. |
| `GET` | `/api/v1/commands/{id}`, `/api/v1/sessions/{id}` | Consulter la commande ou session. |
| `POST`, `GET` | `/api/v1/reservations`, `/api/v1/reservations/{id}` | Créer et consulter les réservations. |
| `POST` | `/api/v1/tariffs` | Créer un tarif et ses tranches. |
| `PUT` | `/api/v1/reservations/{id}/tariffs` | Assigner un tarif à une ressource. |
| `POST` | `/api/v1/reservations/{id}/cost-estimates` | Estimer un coût. |
| `GET` | `/api/v1/stations/{id}/state` | Lire l'état connu de la borne. |
| `GET` | `/api/v1/stations/{id}/measurements` | Lire l'historique paginé. |
| `GET` | `/api/v1/sessions/{id}/consumption` | Obtenir les totaux par ressource. |
| `PUT`, `GET` | `/api/v1/stations/{id}/thresholds/{resource}`, `/api/v1/stations/{id}/thresholds` | Gérer les seuils. |
| `GET` | `/api/v1/sessions/{id}/threshold-events` | Lire l'historique des seuils déclenchés. |

Les réponses utilisent entre autres `201` pour les créations, `202` pour les
commandes asynchrones, `404` pour une ressource absente, `409` pour un conflit,
`422` pour une entrée invalide et `503` si PostgreSQL est indisponible.

## 7. Contrat MQTT

Tous les payloads utilisent `schema_version: "1.0"`, un UUID `message_id`, un
`id_borne` et un horodatage UTC ISO-8601.

| Sens | Topic | Contenu |
|---|---|---|
| Simulateur → backend | `port/bornes/{id}/etat` | `online`/`offline`, retained ; Last Will configuré. |
| Simulateur → backend | `port/bornes/{id}/telemetrie` | Index par ressource, session/réservation et état logique. |
| Backend → simulateur | `port/bornes/{id}/commandes/activer` | Activation liée aux identifiants session/réservation. |
| Backend → simulateur | `port/bornes/{id}/commandes/couper` | Coupure logique d'une session. |
| Simulateur → backend | `port/bornes/{id}/commandes/{activer\|couper}/reponse` | Confirmation ou refus, avec `command_id`. |

Les messages utilisent QoS 1. Seul l'état de borne est retained ; mesures et
réponses de commande ne le sont pas.

## 8. Démarrer le projet

### Avec Docker Compose (recommandé)

Prérequis : Docker Desktop démarré en mode conteneurs Linux et Docker Compose
V2.

Depuis PowerShell à la racine :

```powershell
Copy-Item .env.example .env
docker compose up --build -d
docker compose ps
```

Vérifier l'API et ouvrir la documentation :

```powershell
Invoke-RestMethod http://localhost:8000/health/ready
Start-Process http://localhost:8000/docs
docker compose logs -f api worker simulator-a07
```

Arrêter sans supprimer les données PostgreSQL et Mosquitto :

```powershell
docker compose down
```

Les données PostgreSQL sont persistées dans un volume nommé. Pour réinitialiser
et supprimer les données locales de Compose, utiliser `docker compose down -v`.

Le fichier `mosquitto/mosquitto.conf` autorise les connexions anonymes et
convient uniquement au développement local ; ne pas exposer ce broker sur un
réseau non fiable.

### Lancement local sans conteneurs

Prérequis : Python 3.11, PostgreSQL et Mosquitto accessibles. Préparer les
dépendances et variables dans PowerShell :

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
$env:DATABASE_URL = "postgresql+psycopg://borne:borne_dev@localhost:5432/borne_v21"
$env:MQTT_HOST = "localhost"
$env:MQTT_PORT = "1883"
python -m backend --check
python -m alembic upgrade head
```

Lancer dans trois terminaux partageant ces variables :

```powershell
python -m backend.api
```

```powershell
python -m backend.worker
```

```powershell
python main.py
```

`main.py` lance le nombre de bornes indiqué dans `config/simulator.json`.
Définir `BORNE_ID=A07` (ou `B12`, `C03`) pour lancer une seule borne. Le système
utilise exclusivement le protocole MQTT V2.1.

## 9. Configuration

| Variable | Usage |
|---|---|
| `DATABASE_URL` | URL PostgreSQL commune à l'API, au worker et à Alembic. |
| `MQTT_HOST`, `MQTT_PORT` | Adresse du broker ; `mosquitto:1883` dans Compose, `localhost:1883` en local. |
| `LOG_LEVEL` | Niveau de log du backend. |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | Paramètres du conteneur PostgreSQL Compose. Valeurs exemple réservées au développement. |
| `BORNE_ID` | Lance seulement la borne nommée, si elle existe dans la configuration. |
| `BORNE_INTERVALLE_RELEVE` | Remplace l'intervalle du profil de la borne, en secondes. |
| `MQTT_USERNAME`, `MQTT_PASSWORD` | Identifiants broker facultatifs pour un broker authentifié. |
| `MQTT_TEST_HOST`, `MQTT_TEST_PORT` | Active le test d'intégration MQTT sur le broker indiqué. |

## 10. Tests et validation

### Types de tests

- **Unitaires** : configuration, tarification, modèles, règles de traitement et
  comportement simulé avec client MQTT factice.
- **Intégration de l'API** : requêtes FastAPI/TestClient et base SQLite
  temporaire en mémoire. Cette SQLite n'est utilisée que par les tests.
- **Intégration MQTT réelle** : les tests opt-in vérifient l'échange commande,
  accusé et télémétrie du worker ainsi que la connexion, la télémétrie et
  l'arrêt propre du simulateur via Mosquitto.
- **Migrations** : Alembic applique le schéma à une base de test ou à PostgreSQL
  local. En Compose, la migration s'exécute automatiquement au démarrage.
- **Postman** : essais fonctionnels manuels, non exécutés par pytest.

Lancer les tests backend :

```powershell
python -m pytest -q tests\backend
```

Lancer tous les tests ; le test du broker est ignoré si les variables MQTT ne
sont pas configurées :

```powershell
python -m pytest -q
```

Pour tester le worker avec un Mosquitto local actif :

```powershell
$env:MQTT_TEST_HOST = "localhost"
$env:MQTT_TEST_PORT = "1883"
python -m pytest -q tests\integration
```

Les tests utilisent une base temporaire et ne touchent pas à PostgreSQL de
l'application.

### Parcours Postman

Importer `postman/Borne-V21.postman_collection.json`, vérifier `baseUrl`, puis
exécuter dans l'ordre : santé, consommateur/tag/réservation, tarification,
activation, mesures et seuils. Les simulateurs doivent avoir publié leur état
avant de créer une réservation. Les identifiants créés par la collection sont
réutilisés entre les requêtes du parcours.

## 11. Limites

- Les mesures et les réponses des bornes sont simulées ; aucune interface
  matérielle ni lecteur de tags physiques n'est intégré.
- Compose est un environnement local mono-instance, sans haute disponibilité
  configurée pour PostgreSQL, Mosquitto, l'API ou le worker.
- Le broker d'exemple autorise l'accès anonyme ; une installation réelle
  nécessiterait authentification, ACL et configuration réseau sécurisée.
- L'activation est asynchrone : `202 Accepted` signifie que la commande est
  enregistrée, pas que la prise est déjà active. L'état doit être relu.
- Les bases locales SQLite précédemment utilisées par les bornes ont été
  supprimées sur demande ; leur contenu n'a pas été importé dans PostgreSQL.
