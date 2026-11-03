# Runtime nativi per le connessioni AI

Disponibili da **v2.5.0** e facoltativi. Ogni utente sceglie connessione e
motori delle attività nella propria installazione. La release e gli
aggiornamenti non attivano abbonamenti o configurazioni globali per altri utenti.

Job Finder può ottenere completions dal client ufficiale Codex o Claude Code
installato sul computer dell'utente. La sessione usa l'accesso nativo tramite
abbonamento; non converte un abbonamento in una chiave API e non usa un proxy
OpenAI-compatible. I limiti dell'abbonamento continuano ad applicarsi.

## Avvio nel pacchetto Windows

Apri soltanto `JobFinder.exe` nella cartella principale. Questo launcher avvia
`app/JobFinder.exe` senza richiedere un terminale aperto. La cartella `app/`
contiene anche `Updater.exe`, `JobFinderMCP.exe`, `_internal/` e `vendor/`
con gli strumenti OCR; non sono elementi da spostare o avviare singolarmente.
Le risorse PyInstaller restano in `app/_internal` (`sys._MEIPASS`), mentre
il workspace è la cartella principale esterna.

Il marker `app/.jobfinder-layout.json` versione 1 distingue il layout del
pacchetto. I dati privati sono direttamente in `state/`: per esempio
`state/searcher.db`, `state/ai_connections.json` e
`state/agent/mcp-token.txt`. Non vengono usate junction. I workspace sorgente
e le vecchie installazioni con `data/` rimangono un caso di compatibilità;
non rinominare o spostare manualmente i dati.

Codex e Claude Code sono runtime ufficiali installati separatamente
dall'utente, non componenti da copiare dentro `app/`. Per attivare Codex:

1. Installa il client ufficiale e accedi dal flusso del fornitore con il tuo
   account ChatGPT.
2. Apri **Impostazioni → IA → Connessioni AI e assistenti**, modifica Codex
   e abilita la connessione.
3. Selezionala per le attività desiderate e salva i motori delle attività.
4. Usa la diagnostica per verificare il runtime. **Prova connessione** invia
   invece una piccola richiesta sintetica e consuma la quota dell'account.

Claude Code è facoltativo: richiede un piano attuale che ne consenta l'uso.
Un account gratuito o un vecchio login con etichetta del piano memorizzata
non dimostrano che l'abbonamento sia attivo. Nessun fallback a pagamento
viene abilitato automaticamente se il piano non è disponibile.

Il client e il login appartengono al profilo personale dell'utente, non alla
cartella di un progetto. Job Finder li può rilevare anche quando viene avviato
da un'altra cartella, mantenendo le proprie elaborazioni in directory neutre.
Gli utenti di altri computer o profili Windows devono installare e configurare
il proprio runtime/account. Non distribuire directory di autenticazione insieme
al programma. Per usare Job Finder dalla propria CLI Codex in tutte le cartelle,
configurare separatamente il MCP personale come descritto nella
[guida MCP](jobfinder-mcp.md): quel collegamento non abilita i motori AI dell'app.

Per il backup chiudi completamente l'applicazione e copia tutta `state/`
in un'altra posizione privata. Non eliminare `app/` o `state/` e non
condividere quest'ultima: contiene dati personali e può contenere credenziali.

## Contratto Python

`app.agent_runtime.create_runtime_provider(kind, connection_id, model=None,
timeout_seconds=300)` restituisce un `LLMProvider` con `name=connection_id`.
`kind` è `codex` oppure `claude`. Sono disponibili i metodi già usati dal
manager: `is_available`, `list_models`, `select_model`, `complete_text`,
`complete_json` e `chat`. `last_usage` riporta i token dichiarati dal runtime,
senza ricavare costi monetari o quote residue dai token.

`get_runtime_status(kind)` verifica installazione, versione e autenticazione
senza generare una risposta AI. Restituisce `installed`, `version`,
`authenticated`, `auth_method`, `plan`, `quota`, `checked_at` ed `error`.
Gli errori sono codici chiusi, non messaggi grezzi del client. Email, token,
percorsi di autenticazione e credenziali non sono inclusi nella risposta.
`authenticated` e `plan` descrivono ciò che dichiara il client nativo: una
credenziale ancora presente o una vecchia etichetta del piano non provano che
l'abbonamento sia tuttora attivo. Solo una richiesta riuscita verifica
l'accesso effettivo in quel momento.

Per Codex, `quota` contiene soltanto le finestre `primary`/`secondary` esposte
dal protocollo, con `usedPercent`, `windowDurationMins` e `resetsAt` quando
disponibili. Per Claude rimane `null`: il client non espone un equivalente
stabile tramite questo percorso. Una quota sconosciuta non significa zero.

`complete_json` accetta inoltre il parametro keyword facoltativo
`output_schema`. Senza schema esplicito, i due runtime producono una busta
strutturata con una stringa JSON, decodificata localmente: ciò conserva anche
oggetti arbitrari e array di scoring senza irrigidire il contratto esistente.
Un risultato invalido fallisce; non parte una seconda inferenza di riparazione.

## Processi, autenticazione e strumenti

- Codex: processo dedicato `codex app-server --listen stdio://`, protocollo
  JSON-RPC `initialize`, `account/read`, `thread/start` e `turn/start`.
  Il thread è effimero. Non viene usato il daemon condiviso o il suo proxy.
- Claude: `claude -p --output-format json`, prompt attraverso stdin e schema
  attraverso `--json-schema`. `--tools ""`, `--strict-mcp-config`,
  `--setting-sources ""`, `--safe-mode`, `--no-session-persistence` e
  `--disable-slash-commands` escludono gli strumenti e le personalizzazioni.
  `--bare` non è utilizzato, perché esclude l'accesso nativo da abbonamento.
- Il client controlla prima il metodo di accesso e rifiuta API key e provider
  cloud esterni. Non avvia login/logout, non legge file di credenziali e non
  estrae OAuth token. L'eventuale refresh resta interamente nel client nativo.
- L'ambiente del figlio conserva le variabili del sistema operativo ma esclude
  chiavi API, token ambientali, override di endpoint e identità dei wrapper.
  Le sole directory di configurazione native vengono conservate.
- Ogni processo usa una directory temporanea neutra, senza accesso alla root
  del progetto come workspace. Codex disabilita shell, web, plugin, app, hook,
  memoria e MCP configurati per nome. Un nome MCP non rappresentabile in modo
  sicuro causa un errore prima dell'inferenza. Le richieste di approvazione
  inattese vengono rifiutate; non vengono mai approvate automaticamente.
- Gli eseguibili Windows sono avviati direttamente, senza shell o wrapper
  `.cmd`. Il resolver riconosce anche il binario nativo nel pacchetto npm
  ufficiale Codex. Non installa o aggiorna alcun client.

Il timeout predefinito complessivo è 300 secondi, includendo attesa in coda,
controllo di autenticazione ed esecuzione. `max_tokens` rimane
un'indicazione nel prompt: queste interfacce CLI non offrono lo stesso tetto
hard della Chat Completions API. JSON valido e completamento del turno restano
necessari; troncamenti dichiarati, quota esaurita e autenticazione mancante
vengono propagati come errori classificati.

Il runtime nativo porta con sé un contesto di base: il consumo non coincide
con i soli token del prompt applicativo. Un catalogo modelli o un alias Claude
non dimostra il diritto a usare un modello: questo si verifica soltanto con
una richiesta completata.

## Concorrenza e annullamento

Un lock condiviso per `connection_id` impedisce due completions contemporanee
sulla stessa connessione, anche se esistono due oggetti provider. Le richieste
concorrenti aspettano in una coda cancellabile, controllata ogni 100ms al massimo,
senza avviare processi finché la connessione è occupata.

`cancel_active()` interrompe soltanto il processo posseduto dal provider e
mantiene la connessione annullata. Le offerte ancora in coda non avviano altre
inferenze. Il manager chiama `reset_cancel()` all'inizio di una nuova operazione
esplicita; non durante il tentativo di terminare quella precedente.

I processi propri vengono chiusi anche dopo errore o timeout. La pulizia di una
directory temporanea ancora bloccata da Windows è best effort e non maschera
il risultato. Nessun processo esterno, daemon o altra sessione viene terminato.
Stderr è scartato e non vengono registrati prompt o payload nativi nei log.

## Verifica

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_agent_runtime.py -q
.\.venv\Scripts\python.exe -m mypy app\agent_runtime.py
.\.venv\Scripts\python.exe -m ruff check app\agent_runtime.py
```

I test avviano processi Python reali che parlano protocolli nativi minimi e
deterministici: verifica login senza inferenza, JSON con array e chiavi libere,
usage, auth API rifiutata, quota, troncamento, richieste tool inattese, timeout,
annullamento, reset e concorrenza. Non consumano abbonamenti o API.

Una prova reale va eseguita soltanto su dati sintetici, dopo aver verificato il
metodo di autenticazione, con il consenso previsto dal piano. Il mancato login
va risolto dall'utente nel client ufficiale; non si ripiega su una API pagata.
Un test runtime riuscito non equivale a una verifica dell'integrazione nelle
app desktop di ChatGPT, Claude Desktop o Cowork: ciascun client richiede una
propria prova del collegamento MCP.

## Riferimenti ufficiali

- [Codex App Server](https://learn.chatgpt.com/docs/app-server)
- [Configurazione Codex](https://learn.chatgpt.com/docs/config-file/config-reference)
- [Claude Code CLI](https://code.claude.com/docs/en/cli-reference)
- [Claude Agent SDK e abbonamento](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)

Questa integrazione locale non implica che sia permesso ospitare account o
credenziali condivise per altri utenti: distribuzione, termini e modalità di
autenticazione vanno verificati per il caso d'uso effettivo.
