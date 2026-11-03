# Job Finder: MCP locale

Disponibile da **v2.5.0** come collegamento facoltativo da configurare nel
proprio client. Pubblicazione e aggiornamento di Job Finder non registrano
server MCP o account globali per gli altri utenti.

Il server MCP usa le operazioni dell'applicazione già aperta. Il database viene
letto e modificato dal processo Job Finder; l'adapter avviato dal client desktop
non apre un secondo archivio, non avvia scheduler e non importa `app.main`.

## Collegamento

1. Apri il `JobFinder.exe` nella cartella principale e vai in
   **Impostazioni → IA → Connessioni AI e assistenti**.
2. Espandi la sezione MCP e usa **Genera configurazione MCP**. Questa azione crea una capability
   privata dell'installazione e restituisce i blocchi di configurazione.
3. Aggiungi il server STDIO al client desiderato. I parametri contengono il
   percorso del file token, non il token stesso.
4. Mantieni Job Finder aperto durante l'utilizzo. Il client può essere riavviato
   senza riavviare l'applicazione e senza cambiare configurazioni globali.

Da sorgente l'adapter è `python app/mcp_server.py --url http://127.0.0.1:8000
--token-file <data-dir>\agent\mcp-token.txt`. Nel pacchetto Windows è
`app/JobFinderMCP.exe`, con gli stessi argomenti. Non aprirlo con doppio clic:
lo avvia l'assistente usando la configurazione generata. Usa quei valori,
perché porta, eseguibile e percorso dipendono dall'installazione effettiva.

Nel layout Windows corrente, `JobFinder.exe` nella root è il launcher e
`app/JobFinder.exe` è il programma effettivo. Tutti i componenti distribuiti
rimangono in `app/`, inclusi `Updater.exe`, `_internal/` e `vendor/` per OCR;
`sys._MEIPASS` continua a indicare `app/_internal`. Il marker
`app/.jobfinder-layout.json` versione 1 identifica questo layout, senza
junction. Il workspace resta la root esterna e i dati sono direttamente in
`state/`, quindi la capability MCP è `state/agent/mcp-token.txt`. I workspace
sorgente e gli archivi legacy possono ancora usare `data/`: il setup rileva
il percorso effettivo.

Non cancellare o rinominare `app/` e `state/`, non modificare il marker e non
condividere i file di stato. Per una copia di sicurezza chiudi completamente
Job Finder e copia l'intera cartella `state/` in una posizione separata e
privata. Conservare soltanto gli eseguibili non conserva CV, offerte o chiavi.

### Client

- **Codex nell'app desktop, CLI e IDE:** usa il blocco TOML generato o inserisci
  comando e argomenti nelle impostazioni dei server MCP. La configurazione è
  condivisa dai client locali dello stesso host Codex.
- **Claude Desktop:** usa il blocco `mcpServers` come server locale STDIO.
- **Cowork:** la stessa entry MCP deve essere dichiarata in un plugin locale
  supportato dal client. Non incollare un URL `localhost` tra i connettori
  remoti: questi vengono contattati dal cloud del fornitore. Disponibilità e
  compatibilità vanno verificate nella versione effettiva di Cowork.
- **Altri client MCP:** STDIO funziona senza esporre il computer su Internet.
  Il trasporto Streamable HTTP è disponibile sullo stesso host a `/mcp/`, con
  autenticazione Bearer tramite capability. Non pubblicare il token.

Nessuna configurazione client viene installata o sovrascritta automaticamente.
Nessun tunnel pubblico o registrazione in un marketplace è richiesto.

### Codex CLI personale, da qualsiasi cartella

Per rendere il server disponibile nelle nuove sessioni Codex del proprio
utente, aggiungi il blocco TOML generato alla configurazione **utente** Codex
(normalmente `~/.codex/config.toml`), non soltanto alla configurazione di una
singola repository. In alternativa il client ufficiale supporta questo comando
PowerShell; l'esempio usa un percorso fittizio da sostituire con i valori
generati dalla propria installazione:

```powershell
codex mcp add jobfinder -- "C:\Apps\JobFinder\app\JobFinderMCP.exe" --url "http://127.0.0.1:8000" --token-file "C:\Apps\JobFinder\state\agent\mcp-token.txt"
codex mcp list
```

Apri poi una nuova sessione CLI, anche in un'altra cartella, lasciando Job Finder
in esecuzione. Il secondo comando verifica la registrazione, non esegue una
ricerca e non dimostra da solo che il server sia raggiungibile. La prova reale
consiste nel chiedere al client di consultare i tool o il contesto di Job Finder.
La configurazione resta personale: non contiene il valore del token e non deve
essere copiata come impostazione universale per altri utenti. Il login Codex
rimane quello gestito dal client ufficiale dell'utente.

L'accesso MCP e l'AI dentro Job Finder sono due scelte distinte: puoi usare il
MCP da un assistente senza cambiare i provider API dell'app. Per usare Codex o
Claude Code al posto delle API **dentro** Job Finder devi anche abilitare e
selezionare il runtime nelle connessioni dell'app. Claude resta facoltativo e
richiede un piano attuale idoneo; non basta avere un vecchio login salvato.

## Strumenti

| Strumento | Risultato |
|---|---|
| `jobfinder_list_jobs` | Elenco filtrato e paginato, descrizioni in anteprima |
| `jobfinder_get_job` | Annuncio, analisi esistente e timeline |
| `jobfinder_get_context` | CV, criteri e ricerche salvate, con Privacy Mode |
| `jobfinder_start_search` | Ricerca **solo raccolta**, con identificativo operazione |
| `jobfinder_get_operation` | Avanzamento/esito ricerca oppure stato proposta |
| `jobfinder_cancel_operation` | Annullamento ricerca MCP, conservando i risultati |
| `jobfinder_prepare_analysis` | Prompt, schema e ticket per 1–20 annunci distinti |
| `jobfinder_submit_analysis` | Salvataggio validato delle valutazioni del client |
| `jobfinder_manage_job` | Nota, preferito, archiviazione e ripristino reversibili |
| `jobfinder_propose_change` | Proposta per profilo, criteri o stato candidatura |

Le scritture richiedono `request_id`: riutilizzalo per ripetere la stessa richiesta
dopo un problema di trasporto. Riutilizzarlo per un contenuto diverso viene
rifiutato. Una scrittura rimasta incerta dopo un arresto non viene rieseguita
automaticamente: controllare lo stato effettivo nell'applicazione.

## Flusso senza doppie inferenze

Chiedi all'assistente di raccogliere offerte oppure di consultare quelle salvate.
`start_search` non usa i provider AI dell'applicazione. Dopo la raccolta:

1. Il client prepara un ticket con gli identificativi degli annunci.
2. Legge il prompt corrente e valuta gli annunci nella conversazione in corso.
3. Invia **tutte** le valutazioni, ciascuna con `job_id`, `punteggio` intero 0–10,
   `riassunto` e gli altri campi richiesti dal prompt.
4. Job Finder verifica che CV, criteri e annunci non siano cambiati, applica i
   vincoli deterministici esistenti e salva la provenienza `external/<modello>`.

Il modello è dichiarato dal client, non certificato dal server. Ticket scaduti,
annunci mancanti/duplicati, punteggi non validi o valutazioni clonate vengono
rifiutati prima del salvataggio. Una descrizione insufficiente rimane non
valutata. Il ticket scade dopo 24 ore; se cambia il contesto occorre prepararlo
di nuovo. Nessuno di questi strumenti richiama un secondo modello AI.

## Conferme e confini

Profili, preferenze di ricerca e stati di candidatura sono **proposte**.
L'assistente non possiede strumenti per approvarle. La conferma o il rifiuto
avvengono nella UI tramite una sessione browser distinta dalla capability MCP,
cookie HttpOnly/SameSite, controllo dell'origine e token CSRF. Una proposta
che non corrisponde più al profilo/criteri correnti non viene applicata.

Confermare un fatto CV invariato non lo trasforma in una correzione manuale.
Cambiare i ruoli preferiti aggiorna sia le preferenze sia il riepilogo del
profilo, mantenendo allineate le fonti esistenti.

Il MCP non espone credenziali, accesso arbitrario a file/SQL, posta, invio di
email o candidature, cancellazioni definitive o modifica delle configurazioni
dei provider. Le descrizioni degli annunci sono dati non attendibili, non
istruzioni da eseguire. L'applicazione deve restare su loopback: host e origini
estranei sono rifiutati anche se viene presentata una capability valida.

## Verifica

I test isolati coprono migrazione idempotente, privacy, validazione e vincoli
delle analisi, rilevamento di contesto arretrato, idempotenza, ricerca senza
inferenze annidate, conferme UI e negoziazione MCP con SDK ufficiale.
La compatibilità con un'app desktop si dichiara solo dopo una prova reale su
quel client. Le prove di protocollo non equivalgono a un collaudo di Cowork o
di Claude Desktop.
Anche il blocco generato per Codex desktop è una configurazione da verificare
nel client: non certifica da solo un collegamento riuscito. Claude Code e
Cowork sono opzioni facoltative e il loro utilizzo AI richiede un piano
attivo e idoneo dell'utente.

Riferimenti ufficiali:
- [SDK Python MCP](https://py.sdk.modelcontextprotocol.io/)
- [Configurazione MCP OpenAI](https://learn.chatgpt.com/docs/extend/mcp)
- [Connettori e plugin Claude](https://support.claude.com/en/articles/11725091-when-to-use-desktop-and-web-connectors)
