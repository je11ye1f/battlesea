"""
server.py - Server TCP per Battaglia Navale.

Il server gestisce l'intera partita tra due client:
  1. Attende le connessioni dei due giocatori.
  2. Coordina la fase di posizionamento navi.
  3. Arbitral i turni di gioco, verificando colpi e condizioni di vittoria.
  4. Invia i risultati ad entrambi i client.
  5. Gestisce disconnessioni impreviste senza andare in crash.

Architettura dei thread:
  - Thread principale: accetta connessioni e avvia la partita.
  - Un thread per ogni client: legge i messaggi in arrivo in modo non bloccante.
"""

import socket
import threading
import json
import sys

from protocol import (
    HOST, PORT, BUFFER_SIZE,
    MSG_JOIN, MSG_WAIT, MSG_START, MSG_PLACE_OK, MSG_PLACE_ERR,
    MSG_YOUR_TURN, MSG_WAIT_TURN, MSG_SHOT, MSG_SHOT_RESULT, MSG_OPP_SHOT,
    MSG_WIN, MSG_LOSE, MSG_OPPONENT_DC, MSG_CHAT, MSG_ERROR,
    FLEET, encode, decode
)
from game_logic import Board
from database import init_db, record_result


class GameSession:
    """
    Gestisce una singola sessione di gioco tra due client connessi.

    Ogni GameSession ha il proprio stato (bacheche, turno corrente, lock)
    e viene eseguita in un thread separato per non bloccare il server principale
    nell'accettare nuove connessioni future.
    """

    def __init__(self, conn1: socket.socket, conn2: socket.socket,
                 nick1: str, nick2: str):
        """
        Inizializza la sessione con le due connessioni socket e i nickname.

        Args:
            conn1, conn2: Socket dei due client.
            nick1, nick2: Nickname dei due giocatori.
        """
        self.conns = [conn1, conn2]
        self.nicks = [nick1, nick2]
        # Ogni giocatore ha la propria Board (la griglia dove vengono colpito)
        self.boards = [Board(), Board()]
        # Indice del giocatore il cui turno è attivo (0 o 1)
        self.current_turn = 0
        # Conta le navi posizionate: la partita inizia quando entrambi hanno finito
        self.ready_count = 0
        self.ready_lock = threading.Lock()
        # Flag per segnalare che la partita è terminata
        self.game_over = False

    # ──────────────────────────────────────────────
    # Invio messaggi
    # ──────────────────────────────────────────────

    def send(self, player_idx: int, msg_type: str, payload: dict = None):
        """
        Invia un messaggio JSON a uno specifico giocatore.

        Racchiude l'invio in un try/except per gestire disconnessioni silenziose.

        Args:
            player_idx: 0 o 1.
            msg_type:   Tipo di messaggio dal protocollo.
            payload:    Dati aggiuntivi.
        """
        try:
            self.conns[player_idx].sendall(encode(msg_type, payload))
        except (BrokenPipeError, OSError):
            # Il client si è disconnesso: ignora l'errore qui,
            # il thread di ricezione se ne accorgerà
            pass

    def broadcast(self, msg_type: str, payload: dict = None):
        """Invia lo stesso messaggio ad entrambi i giocatori."""
        for i in range(2):
            self.send(i, msg_type, payload)

    # ──────────────────────────────────────────────
    # Ricezione messaggi (loop per singolo client)
    # ──────────────────────────────────────────────

    def _recv_loop(self, player_idx: int):
        """
        Loop di ricezione per un singolo client, eseguito in un thread dedicato.

        Legge dati dal socket finché la connessione è aperta, poi li processa.
        Il buffer interno accumula dati finché non trova un newline (\\n),
        che segnala la fine di un messaggio completo.

        Args:
            player_idx: Indice del giocatore (0 o 1) da cui leggere.
        """
        conn = self.conns[player_idx]
        buffer = ""
        try:
            while not self.game_over:
                chunk = conn.recv(BUFFER_SIZE).decode("utf-8")
                if not chunk:
                    # Connessione chiusa dal client
                    raise ConnectionResetError("Client disconnesso")
                buffer += chunk
                # Un messaggio termina con \n; potrebbero arrivarne più di uno in un chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if line.strip():
                        self._handle_message(player_idx, decode(line))
        except (ConnectionResetError, OSError, json.JSONDecodeError) as e:
            if not self.game_over:
                print(f"[SERVER] Giocatore {self.nicks[player_idx]} disconnesso: {e}")
                self._handle_disconnect(player_idx)

    def _handle_disconnect(self, player_idx: int):
        """
        Gestisce la disconnessione improvvisa di un giocatore.

        Notifica l'avversario e chiude entrambe le connessioni in modo pulito.

        Args:
            player_idx: Indice del giocatore che si è disconnesso.
        """
        if self.game_over:
            return  # Evita doppia gestione
        self.game_over = True
        opponent = 1 - player_idx
        self.send(opponent, MSG_OPPONENT_DC,
                  {"message": f"{self.nicks[player_idx]} si è disconnesso."})
        for conn in self.conns:
            try:
                conn.close()
            except OSError:
                pass

    # ──────────────────────────────────────────────
    # Gestione dei messaggi ricevuti
    # ──────────────────────────────────────────────

    def _handle_message(self, player_idx: int, msg: dict):
        """
        Dispatcher centrale: instrada ogni messaggio alla funzione corretta.

        Args:
            player_idx: Chi ha inviato il messaggio.
            msg:        Dizionario con "type" e "payload".
        """
        msg_type = msg.get("type")
        payload  = msg.get("payload", {})

        if msg_type == MSG_PLACE_OK:
            self._handle_placement(player_idx, payload)
        elif msg_type == MSG_SHOT:
            self._handle_shot(player_idx, payload)
        elif msg_type == MSG_CHAT:
            self._handle_chat(player_idx, payload)
        else:
            print(f"[SERVER] Messaggio sconosciuto da {self.nicks[player_idx]}: {msg_type}")

    def _handle_placement(self, player_idx: int, payload: dict):
        """
        Elabora il posizionamento delle navi di un giocatore.

        Il payload contiene una lista di navi con posizione e orientamento.
        Verifica la validità e, quando entrambi i giocatori sono pronti, avvia la partita.

        Args:
            player_idx: Chi ha inviato il posizionamento.
            payload:    {"ships": [{"name":..., "row":..., "col":..., "length":..., "horizontal":...}, ...]}
        """
        board = self.boards[player_idx]
        ships_data = payload.get("ships", [])

        # Posiziona ogni nave ricevuta
        for ship_info in ships_data:
            ok = board.place_ship(
                ship_info["name"],
                ship_info["row"],
                ship_info["col"],
                ship_info["length"],
                ship_info["horizontal"]
            )
            if not ok:
                self.send(player_idx, MSG_PLACE_ERR, {"message": "Posizionamento non valido."})
                return

        self.send(player_idx, MSG_PLACE_OK, {"message": "Navi posizionate correttamente."})
        print(f"[SERVER] {self.nicks[player_idx]} ha posizionato le navi.")

        # Controlla se entrambi i giocatori sono pronti
        with self.ready_lock:
            self.ready_count += 1
            if self.ready_count == 2:
                self._start_game()

    def _start_game(self):
        """
        Avvia ufficialmente la partita inviando il segnale di inizio e assegnando il primo turno.
        Il giocatore 0 inizia sempre per semplicità.
        """
        print("[SERVER] Entrambi i giocatori pronti. Partita iniziata!")
        # Informa entrambi i giocatori
        self.broadcast(MSG_START, {
            "player1": self.nicks[0],
            "player2": self.nicks[1]
        })
        # Assegna il primo turno
        self.send(0, MSG_YOUR_TURN,  {"message": "Sei tu a cominciare!"})
        self.send(1, MSG_WAIT_TURN,  {"message": f"Aspetta: tocca a {self.nicks[0]}."})

    def _handle_shot(self, player_idx: int, payload: dict):
        """
        Elabora un colpo sparato da un giocatore.

        Verifica che sia il turno del giocatore, applica il colpo alla griglia
        dell'avversario, comunica il risultato ad entrambi e controlla la vittoria.

        Args:
            player_idx: Chi ha sparato.
            payload:    {"row": int, "col": int}
        """
        # Verifica del turno: ignora messaggi fuori turno
        if player_idx != self.current_turn or self.game_over:
            return

        row = payload.get("row")
        col = payload.get("col")
        opponent_idx = 1 - player_idx
        opponent_board = self.boards[opponent_idx]

        # Applica il colpo sulla griglia dell'avversario
        result, sunk_name = opponent_board.receive_shot(row, col)

        shot_payload = {"row": row, "col": col, "result": result}
        if sunk_name:
            shot_payload["sunk_name"] = sunk_name

        # Informa chi ha sparato dell'esito
        self.send(player_idx, MSG_SHOT_RESULT, shot_payload)
        # Informa l'avversario di dove è stato colpito
        self.send(opponent_idx, MSG_OPP_SHOT, shot_payload)

        print(f"[SERVER] {self.nicks[player_idx]} spara in ({row},{col}) → {result}"
              + (f" ({sunk_name} affondata!)" if sunk_name else ""))

        # Controlla condizione di vittoria
        if opponent_board.all_sunk():
            self._end_game(winner_idx=player_idx)
            return

        # Passa il turno all'avversario
        self.current_turn = opponent_idx
        self.send(player_idx,   MSG_WAIT_TURN, {"message": f"Aspetta: tocca a {self.nicks[opponent_idx]}."})
        self.send(opponent_idx, MSG_YOUR_TURN,  {"message": "Tocca a te!"})

    def _handle_chat(self, player_idx: int, payload: dict):
        """
        Inola un messaggio di chat all'avversario, aggiungendo il nome del mittente.

        Args:
            player_idx: Chi ha inviato il messaggio.
            payload:    {"text": "..."}
        """
        text = payload.get("text", "")
        self.send(
            1 - player_idx,
            MSG_CHAT,
            {"from": self.nicks[player_idx], "text": text}
        )

    def _end_game(self, winner_idx: int):
        """
        Chiude la partita: notifica vincitore e perdente, salva le statistiche.

        Args:
            winner_idx: Indice (0 o 1) del giocatore vincitore.
        """
        self.game_over = True
        loser_idx = 1 - winner_idx
        winner_nick = self.nicks[winner_idx]
        loser_nick  = self.nicks[loser_idx]

        self.send(winner_idx, MSG_WIN,  {"message": f"Hai vinto, {winner_nick}! Complimenti!"})
        self.send(loser_idx,  MSG_LOSE, {"message": f"Hai perso. {winner_nick} ha affondato tutta la tua flotta."})

        # Salva le statistiche nel database
        record_result(winner_nick, won=True)
        record_result(loser_nick,  won=False)
        print(f"[SERVER] Partita terminata. Vincitore: {winner_nick}")

    # ──────────────────────────────────────────────
    # Avvio della sessione
    # ──────────────────────────────────────────────

    def run(self):
        """
        Punto di ingresso della sessione: avvia i thread di ricezione per entrambi i client.
        Questo metodo blocca finché entrambi i thread non terminano.
        """
        threads = [
            threading.Thread(target=self._recv_loop, args=(0,), daemon=True),
            threading.Thread(target=self._recv_loop, args=(1,), daemon=True),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()


# ──────────────────────────────────────────────
# Entry point del server
# ──────────────────────────────────────────────

def run_server(host: str = HOST, port: int = PORT):
    """
    Avvia il server TCP e rimane in ascolto di coppie di giocatori.

    Per ogni coppia di client connessi, crea una GameSession e la esegue
    in un thread separato, così il server può accettare nuove partite
    mentre quella corrente è in corso.

    Args:
        host: Indirizzo IP su cui ascoltare.
        port: Porta TCP su cui ascoltare.
    """
    init_db()  # Inizializza il database SQLite

    # Crea il socket del server
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # SO_REUSEADDR permette di riutilizzare la porta subito dopo un riavvio
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((host, port))
    server_sock.listen(2)
    print(f"[SERVER] In ascolto su {host}:{port} …")

    try:
        while True:
            print("[SERVER] In attesa del giocatore 1 …")
            conn1, addr1 = server_sock.accept()
            print(f"[SERVER] Giocatore 1 connesso da {addr1}")

            # Ricevi il nickname del giocatore 1
            nick1 = _recv_nickname(conn1)
            # Informa il giocatore 1 che deve aspettare il secondo
            conn1.sendall(encode(MSG_WAIT, {"message": "In attesa del secondo giocatore…"}))

            print("[SERVER] In attesa del giocatore 2 …")
            conn2, addr2 = server_sock.accept()
            print(f"[SERVER] Giocatore 2 connesso da {addr2}")
            nick2 = _recv_nickname(conn2)

            print(f"[SERVER] Match: {nick1} vs {nick2}")

            # Avvia la sessione in un thread separato
            session = GameSession(conn1, conn2, nick1, nick2)
            threading.Thread(target=session.run, daemon=True).start()

    except KeyboardInterrupt:
        print("\n[SERVER] Arresto in corso…")
    finally:
        server_sock.close()


def _recv_nickname(conn: socket.socket) -> str:
    """
    Riceve il messaggio JOIN dal client e restituisce il nickname.

    Args:
        conn: Socket del client.

    Returns:
        Il nickname ricevuto, o "Anonimo" in caso di errore.
    """
    try:
        data = conn.recv(BUFFER_SIZE).decode("utf-8").strip()
        msg = decode(data)
        if msg.get("type") == MSG_JOIN:
            return msg["payload"].get("nickname", "Anonimo")
    except Exception:
        pass
    return "Anonimo"


if __name__ == "__main__":
    # Permette di specificare host e porta da riga di comando:
    # python server.py [host] [port]
    h = sys.argv[1] if len(sys.argv) > 1 else HOST
    p = int(sys.argv[2]) if len(sys.argv) > 2 else PORT
    run_server(h, p)
