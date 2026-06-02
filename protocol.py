"""
protocol.py - Definizione del protocollo applicativo per Battaglia Navale TCP.

Questo modulo centralizza tutti i tipi di messaggi scambiati tra client e server,
garantendo che entrambe le parti "parlino la stessa lingua".
Il formato scelto è JSON, per leggibilità e facilità di parsing.

Struttura di un messaggio:
    {"type": "<MSG_TYPE>", "payload": {...}}
"""

import json


# ──────────────────────────────────────────────
# Costanti di configurazione del gioco
# ──────────────────────────────────────────────

GRID_SIZE = 10          # Dimensione della griglia (10x10)
HOST = "127.0.0.1"      # Indirizzo di default del server
PORT = 5555             # Porta TCP di default
BUFFER_SIZE = 4096      # Dimensione del buffer di ricezione in byte

# Flotta di default: lista di (nome, lunghezza)
FLEET = [
    ("Portaerei",   5),
    ("Corazzata",   4),
    ("Incrociatore",3),
    ("Cacciatorpediniere", 2),
    ("Cacciatorpediniere", 2),
    ("Sottomarino", 1),
]

# ──────────────────────────────────────────────
# Tipi di messaggio del protocollo
# ──────────────────────────────────────────────

# Fase di connessione / setup
MSG_JOIN        = "JOIN"        # Client → Server: richiesta di entrare nella partita
MSG_WAIT        = "WAIT"        # Server → Client: in attesa del secondo giocatore
MSG_START       = "START"       # Server → Client: la partita inizia
MSG_PLACE_OK    = "PLACE_OK"    # Server → Client: posizionamento navi accettato
MSG_PLACE_ERR   = "PLACE_ERR"   # Server → Client: posizionamento navi non valido

# Fase di gioco
MSG_YOUR_TURN   = "YOUR_TURN"   # Server → Client: è il tuo turno di sparare
MSG_WAIT_TURN   = "WAIT_TURN"   # Server → Client: aspetta il turno dell'avversario
MSG_SHOT        = "SHOT"        # Client → Server: coordinate del colpo {row, col}
MSG_SHOT_RESULT = "SHOT_RESULT" # Server → Client: esito del colpo {result, row, col, sunk_name?}
MSG_OPP_SHOT    = "OPP_SHOT"    # Server → Client: l'avversario ha sparato qui {row, col, result}

# Esiti di un colpo
RESULT_MISS     = "ACQUA"
RESULT_HIT      = "COLPITO"
RESULT_SUNK     = "AFFONDATO"

# Fine partita
MSG_WIN         = "WIN"         # Server → Client: hai vinto!
MSG_LOSE        = "LOSE"        # Server → Client: hai perso.
MSG_OPPONENT_DC = "OPPONENT_DC" # Server → Client: l'avversario si è disconnesso

# Chat
MSG_CHAT        = "CHAT"        # Bidirezionale: {text: "..."}

# Errori generici
MSG_ERROR       = "ERROR"


# ──────────────────────────────────────────────
# Funzioni di serializzazione / deserializzazione
# ──────────────────────────────────────────────

def encode(msg_type: str, payload: dict = None) -> bytes:
    """
    Serializza un messaggio in bytes pronti per essere inviati via socket.

    Il messaggio viene convertito in JSON e terminato con un newline (\\n),
    che funge da delimitatore per la ricezione.

    Args:
        msg_type: Una delle costanti MSG_* definite sopra.
        payload:  Dizionario con i dati aggiuntivi del messaggio.

    Returns:
        bytes: Il messaggio codificato in UTF-8.
    """
    message = {"type": msg_type, "payload": payload or {}}
    return (json.dumps(message) + "\n").encode("utf-8")


def decode(data: str) -> dict:
    """
    Deserializza una stringa JSON ricevuta dal socket in un dizionario Python.

    Args:
        data: La stringa grezza ricevuta dal socket (senza il \\n finale).

    Returns:
        dict con chiavi "type" e "payload".

    Raises:
        json.JSONDecodeError: Se la stringa non è JSON valido.
    """
    return json.loads(data.strip())
