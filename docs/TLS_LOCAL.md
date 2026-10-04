# TLS en développement local (Windows)

Le service appelle `https://api.anthropic.com` via le SDK `anthropic` (donc `httpx`),
qui vérifie les certificats avec le bundle **certifi**. Cette page ne concerne que
le poste de développement : **rien dans le code du service ne touche à la sécurité
TLS, et la vérification ne doit jamais être désactivée** (`verify=False`,
`CURL_CA_BUNDLE=""`, etc.).

## Ce qui a été observé (poste de dev, Python 3.14, OpenSSL 3.0.18, certifi 2026.07.22)

- Pendant une session : tous les appels Python échouaient avec
  `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`, alors que
  `curl` (qui utilise le magasin Windows, Schannel) réussissait.
- La chaîne présentée par `api.anthropic.com` était authentique :
  `api.anthropic.com` ← `GTS WE1` ← `GTS Root R4` (certificat croisé) ←
  `GlobalSign Root CA`. Aucune interception (pas d'émetteur d'antivirus ou de proxy).
- Le magasin Windows contient `GlobalSign Root CA` ; **certifi ne le contient pas**
  (il contient `GTS Root R4`, mais pas son ancre `GlobalSign Root CA`). Un chemin de
  validation qui passe par le certificat croisé échoue donc avec certifi seul et
  réussit avec le magasin Windows.
- Le défaut a disparu ensuite, sans modification : la même requête réussit
  aujourd'hui avec certifi seul, y compris le SDK. **Je n'ai pas pu établir la cause
  avec certitude** (la plus cohérente : le chemin de validation proposé par le
  serveur a changé). Le contournement ci-dessous reste valable si le défaut revient.
- Aucune variable `SSL_CERT_FILE`, `SSL_CERT_DIR` ni `REQUESTS_CA_BUNDLE` n'est
  définie par défaut, et le fichier `.env` ne peut pas les définir : il alimente
  seulement `Settings`, pas l'environnement du processus.

## Diagnostic (vérification toujours active)

```powershell
curl.exe -sS -m 10 -o NUL -w "%{http_code}`n" https://api.anthropic.com   # 404 = TLS accepté
.\.venv\Scripts\python -c "import httpx; print(httpx.get('https://api.anthropic.com').status_code)"
```

`curl` OK et Python en échec = le magasin Windows connaît une autorité que certifi
ne connaît pas.

## Configuration locale correcte

1. Construire un bundle certifi + magasin Windows, hors du dépôt :

   ```powershell
   .\.venv\Scripts\python scripts\ca_bundle_windows.py   # écrit %USERPROFILE%\.virelyon\ca-bundle.pem
   ```

2. Le déclarer pour la session PowerShell qui lance le service ou les tests réels :

   ```powershell
   $env:SSL_CERT_FILE = "$env:USERPROFILE\.virelyon\ca-bundle.pem"
   uvicorn app.main:app --reload --port 8080
   ```

   (ou en permanence : `setx SSL_CERT_FILE "%USERPROFILE%\.virelyon\ca-bundle.pem"`,
   puis ouvrir un nouveau terminal.)

`SSL_CERT_FILE` est lu par `httpx` : il **remplace** le bundle par défaut, d'où un
fichier qui reprend certifi puis ajoute les autorités Windows. Si un proxy
d'entreprise intercepte le TLS, ajouter plutôt le certificat racine de l'entreprise
(fourni par l'informatique) au Windows store : le script le reprendra.

## Production

Ne rien changer : l'image (`python:3.12-slim`, voir `Dockerfile`) s'appuie sur le
certifi installé par pip avec `httpx`. Ne pas copier ce bundle ni cette variable dans l'image ou dans
`.env.example` comme valeur par défaut.
