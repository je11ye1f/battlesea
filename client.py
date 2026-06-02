"""
client.py - Client grafico per Battaglia Navale con Tkinter.

Organizzato in schermate (Frame) che si alternano durante il gioco:
  1. LoginScreen     – inserimento nickname e indirizzo server
  2. PlacementScreen – posizionamento manuale delle navi sulla griglia
  3. GameScreen      – schermata di gioco con le due griglie e la chat

La comunicazione di rete avviene in un thread separato per non bloccare
l'interfaccia grafica (che gira nel thread principale di Tkinter).
"""

import socket
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import json
import sys

from protocol import (
    HOST, PORT, BUFFER_SIZE, FLEET, GRID_SIZE,
    MSG_JOIN, MSG_WAIT, MSG_START, MSG_PLACE_OK, MSG_PLACE_ERR,
    MSG_YOUR_TURN, MSG_WAIT_TURN, MSG_SHOT, MSG_SHOT_RESULT, MSG_OPP_SHOT,
    MSG_WIN, MSG_LOSE, MSG_OPPONENT_DC, MSG_CHAT, MSG_ERROR,
    RESULT_MISS, RESULT_HIT, RESULT_SUNK,
    encode, decode
)
from database import init_db, get_stats

# ──────────────────────────────────────────────
# Palette colori
# ──────────────────────────────────────────────
C_BG        = "#0a1628"   # Sfondo scuro navy
C_BG2       = "#112244"   # Sfondo secondario
C_ACCENT    = "#00d4ff"   # Azzurro brillante
C_TEXT      = "#e0f0ff"   # Testo chiaro
C_WATER     = "#1a3a5c"   # Cella acqua
C_SHIP      = "#4a7fa5"   # Cella nave
C_HIT       = "#ff4444"   # Cella colpita
C_MISS      = "#334455"   # Cella mancato
C_PREVIEW   = "#2a6080"   # Preview posizionamento
C_SUNK      = "#ff8800"   # Nave affondata
C_WIN       = "#00ff88"   # Verde vittoria
C_LOSE      = "#ff3344"   # Rosso sconfitta
C_ENEMY_HIT = "#ff6600"   # Colpo su nave nemica

CELL_SIZE = 38            # Pixel per cella


class BattagliaNavaleApp(tk.Tk):
    """
    Finestra principale dell'applicazione: contenitore di tutte le schermate.
    Gestisce il socket TCP e il thread di ricezione, condivisi tra le schermate.
    """

    def __init__(self):
        super().__init__()
        self.title("⚓ Battaglia Navale")
        self.configure(bg=C_BG)
        self.resizable(False, False)

        init_db()  # Assicura che il database esista

        # Stato di rete
        self.sock: socket.socket | None = None
        self.recv_thread: threading.Thread | None = None
        self.nickname = ""

        # Schermata corrente
        self.current_screen = None
        self._show_login()

    # ──────────────────────────────────────────────
    # Navigazione tra schermate
    # ──────────────────────────────────────────────

    def _show_login(self):
        """Mostra la schermata di login."""
        if self.current_screen:
            self.current_screen.destroy()
        self.current_screen = LoginScreen(self)
        self.current_screen.pack(fill="both", expand=True)

    def _show_placement(self):
        """Mostra la schermata di posizionamento navi."""
        if self.current_screen:
            self.current_screen.destroy()
        self.current_screen = PlacementScreen(self)
        self.current_screen.pack(fill="both", expand=True)

    def _show_game(self, opp_nick: str):
        """
        Mostra la schermata di gioco.

        Args:
            opp_nick: Nickname dell'avversario (ricevuto dal server con MSG_START).
        """
        if self.current_screen:
            self.current_screen.destroy()
        self.current_screen = GameScreen(self, opp_nick)
        self.current_screen.pack(fill="both", expand=True)

    # ──────────────────────────────────────────────
    # Gestione socket
    # ──────────────────────────────────────────────

    def connect(self, host: str, port: int, nickname: str) -> bool:
        """
        Apre la connessione TCP e invia il messaggio JOIN.

        Args:
            host:     Indirizzo IP del server.
            port:     Porta TCP.
            nickname: Nickname del giocatore.

        Returns:
            True se la connessione è riuscita, False altrimenti.
        """
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((host, port))
            self.nickname = nickname
            # Invia subito il messaggio di join
            self.sock.sendall(encode(MSG_JOIN, {"nickname": nickname}))
            # Avvia il thread di ricezione
            self.recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
            self.recv_thread.start()
            return True
        except (ConnectionRefusedError, OSError) as e:
            messagebox.showerror("Errore connessione", f"Impossibile connettersi: {e}")
            return False

    def send(self, msg_type: str, payload: dict = None):
        """
        Invia un messaggio al server (thread-safe).

        Args:
            msg_type: Tipo di messaggio del protocollo.
            payload:  Dati aggiuntivi.
        """
        if self.sock:
            try:
                self.sock.sendall(encode(msg_type, payload))
            except OSError:
                pass

    def _recv_loop(self):
        """
        Thread di ricezione: legge dati dal socket e li dispatcha alla schermata attiva.
        Gira in background per non bloccare la GUI di Tkinter.
        I messaggi vengono inoltrati alla GUI tramite self.after() per thread-safety.
        """
        buffer = ""
        try:
            while True:
                chunk = self.sock.recv(BUFFER_SIZE).decode("utf-8")
                if not chunk:
                    break
                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if line.strip():
                        msg = decode(line)
                        # Invia il messaggio alla GUI nel thread principale
                        self.after(0, self._dispatch, msg)
        except (OSError, json.JSONDecodeError):
            pass
        finally:
            # Connessione chiusa: notifica la GUI
            self.after(0, self._dispatch, {"type": MSG_OPPONENT_DC, "payload": {}})

    def _dispatch(self, msg: dict):
        """
        Distribuisce i messaggi ricevuti alla schermata corrente.
        Chiamato sempre nel thread principale (via self.after).

        Args:
            msg: Dizionario {"type": ..., "payload": ...}.
        """
        if self.current_screen and hasattr(self.current_screen, "handle_message"):
            self.current_screen.handle_message(msg)


# ──────────────────────────────────────────────
# SCHERMATA 1: Login
# ──────────────────────────────────────────────

class LoginScreen(tk.Frame):
    """Schermata iniziale per inserire nickname e indirizzo server."""

    def __init__(self, app: BattagliaNavaleApp):
        super().__init__(app, bg=C_BG)
        self.app = app
        self._build_ui()

    def _build_ui(self):
        """Costruisce l'interfaccia grafica del login."""
        # Titolo
        tk.Label(self, text="⚓ BATTAGLIA NAVALE", font=("Courier", 28, "bold"),
                 fg=C_ACCENT, bg=C_BG).pack(pady=(40, 5))
        tk.Label(self, text="Gioco via rete TCP", font=("Courier", 12),
                 fg=C_TEXT, bg=C_BG).pack(pady=(0, 30))

        frame = tk.Frame(self, bg=C_BG2, padx=30, pady=30)
        frame.pack(padx=50, pady=10)

        # Campo nickname
        tk.Label(frame, text="Nickname:", font=("Courier", 11), fg=C_TEXT, bg=C_BG2).grid(
            row=0, column=0, sticky="w", pady=5)
        self.nick_var = tk.StringVar(value="Capitano")
        tk.Entry(frame, textvariable=self.nick_var, font=("Courier", 11),
                 bg=C_WATER, fg=C_TEXT, insertbackground=C_ACCENT, width=20).grid(
            row=0, column=1, padx=10, pady=5)

        # Campo server host
        tk.Label(frame, text="Server:", font=("Courier", 11), fg=C_TEXT, bg=C_BG2).grid(
            row=1, column=0, sticky="w", pady=5)
        self.host_var = tk.StringVar(value=HOST)
        tk.Entry(frame, textvariable=self.host_var, font=("Courier", 11),
                 bg=C_WATER, fg=C_TEXT, insertbackground=C_ACCENT, width=20).grid(
            row=1, column=1, padx=10, pady=5)

        # Campo porta
        tk.Label(frame, text="Porta:", font=("Courier", 11), fg=C_TEXT, bg=C_BG2).grid(
            row=2, column=0, sticky="w", pady=5)
        self.port_var = tk.StringVar(value=str(PORT))
        tk.Entry(frame, textvariable=self.port_var, font=("Courier", 11),
                 bg=C_WATER, fg=C_TEXT, insertbackground=C_ACCENT, width=20).grid(
            row=2, column=1, padx=10, pady=5)

        # Bottone connetti
        tk.Button(self, text="CONNETTI", font=("Courier", 13, "bold"),
                  bg=C_ACCENT, fg=C_BG, activebackground=C_WIN,
                  command=self._on_connect, pady=8, padx=20).pack(pady=20)

        # Label status
        self.status_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self.status_var, font=("Courier", 10),
                 fg=C_TEXT, bg=C_BG).pack()

        # Statistiche giocatore
        tk.Button(self, text="Le mie statistiche", font=("Courier", 9),
                  bg=C_BG2, fg=C_ACCENT, relief="flat",
                  command=self._show_stats).pack(pady=(10, 0))

    def _on_connect(self):
        """Callback del bottone Connetti: valida i campi e apre la connessione."""
        nick = self.nick_var.get().strip()
        host = self.host_var.get().strip()
        try:
            port = int(self.port_var.get().strip())
        except ValueError:
            messagebox.showerror("Errore", "La porta deve essere un numero intero.")
            return

        if not nick:
            messagebox.showerror("Errore", "Inserisci un nickname.")
            return

        self.status_var.set("Connessione in corso…")
        if self.app.connect(host, port, nick):
            self.status_var.set("Connesso! In attesa dell'avversario…")

    def _show_stats(self):
        """Mostra le statistiche del giocatore attuale in una finestra popup."""
        nick = self.nick_var.get().strip()
        stats = get_stats(nick)
        if stats:
            msg = (f"Giocatore: {stats['nickname']}\n"
                   f"Vittorie:  {stats['wins']}\n"
                   f"Sconfitte: {stats['losses']}\n"
                   f"Partite:   {stats['games']}")
        else:
            msg = f"Nessuna statistica trovata per '{nick}'."
        messagebox.showinfo("Statistiche", msg)

    def handle_message(self, msg: dict):
        """
        Gestisce i messaggi ricevuti dal server nella fase di login/attesa.

        Args:
            msg: Dizionario del messaggio.
        """
        t = msg.get("type")
        if t == MSG_WAIT:
            self.status_var.set("⏳ In attesa del secondo giocatore…")
        elif t == MSG_START:
            # Partita iniziata: passa alla schermata di posizionamento
            self.app._show_placement()
        elif t == MSG_PLACE_OK:
            # Il server ha confermato il posizionamento durante l'attesa
            pass


# ──────────────────────────────────────────────
# SCHERMATA 2: Posizionamento navi
# ──────────────────────────────────────────────

class PlacementScreen(tk.Frame):
    """
    Schermata per posizionare manualmente le navi sulla griglia.

    Il giocatore seleziona una nave dalla lista, sceglie l'orientamento
    e clicca sulla griglia per posizionarla. Passando il mouse sulla griglia
    viene mostrata un'anteprima (preview).
    """

    def __init__(self, app: BattagliaNavaleApp):
        super().__init__(app, bg=C_BG)
        self.app = app
        # Copia della flotta da posizionare: lista di [nome, lunghezza]
        self.fleet_to_place = list(FLEET)
        self.current_ship_idx = 0
        self.horizontal = True
        # Griglia locale per evitare sovrapposizioni
        self.grid_state = [[None] * GRID_SIZE for _ in range(GRID_SIZE)]
        # Lista delle navi già piazzate (da inviare al server)
        self.placed_ships = []
        self._build_ui()

    def _build_ui(self):
        """Costruisce l'interfaccia di posizionamento."""
        tk.Label(self, text="POSIZIONA LA TUA FLOTTA", font=("Courier", 16, "bold"),
                 fg=C_ACCENT, bg=C_BG).pack(pady=(15, 5))

        main = tk.Frame(self, bg=C_BG)
        main.pack(padx=20, pady=10)

        # Pannello sinistro: griglia
        left = tk.Frame(main, bg=C_BG)
        left.pack(side="left", padx=10)

        tk.Label(left, text="La tua griglia", font=("Courier", 11),
                 fg=C_TEXT, bg=C_BG).pack(pady=(0, 5))
        self.canvas = tk.Canvas(left,
                                width=CELL_SIZE * GRID_SIZE + 40,
                                height=CELL_SIZE * GRID_SIZE + 40,
                                bg=C_BG, highlightthickness=0)
        self.canvas.pack()
        self._draw_grid()
        self.canvas.bind("<Motion>", self._on_hover)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Leave>", self._on_leave)

        # Pannello destro: controlli
        right = tk.Frame(main, bg=C_BG2, padx=15, pady=15)
        right.pack(side="left", padx=10, fill="y")

        tk.Label(right, text="Nave da piazzare:", font=("Courier", 10),
                 fg=C_TEXT, bg=C_BG2).pack(anchor="w")
        self.ship_label = tk.Label(right, text="", font=("Courier", 11, "bold"),
                                   fg=C_ACCENT, bg=C_BG2)
        self.ship_label.pack(anchor="w", pady=(0, 10))

        tk.Label(right, text="Orientamento:", font=("Courier", 10),
                 fg=C_TEXT, bg=C_BG2).pack(anchor="w")
        self.orient_var = tk.StringVar(value="Orizzontale")
        tk.Radiobutton(right, text="Orizzontale", variable=self.orient_var,
                       value="Orizzontale", bg=C_BG2, fg=C_TEXT,
                       selectcolor=C_BG, font=("Courier", 10),
                       command=self._update_orientation).pack(anchor="w")
        tk.Radiobutton(right, text="Verticale", variable=self.orient_var,
                       value="Verticale", bg=C_BG2, fg=C_TEXT,
                       selectcolor=C_BG, font=("Courier", 10),
                       command=self._update_orientation).pack(anchor="w")

        tk.Label(right, text="\nNavi rimanenti:", font=("Courier", 10),
                 fg=C_TEXT, bg=C_BG2).pack(anchor="w")
        self.fleet_listbox = tk.Listbox(right, bg=C_WATER, fg=C_TEXT,
                                        font=("Courier", 9), height=8, width=22,
                                        selectbackground=C_ACCENT)
        self.fleet_listbox.pack()
        self._update_fleet_list()

        # Bottone posizionamento automatico
        tk.Button(right, text="Auto-posiziona\nrimanenti",
                  font=("Courier", 9), bg=C_BG2, fg=C_ACCENT,
                  relief="flat", command=self._auto_place).pack(pady=10)

        # Status bar
        self.status_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self.status_var, font=("Courier", 10),
                 fg=C_TEXT, bg=C_BG).pack()

        self._update_ship_label()

    # ──────────────────────────────────────────────
    # Disegno griglia
    # ──────────────────────────────────────────────

    MARGIN = 20  # pixel di margine per le etichette

    def _cell_to_canvas(self, row: int, col: int) -> tuple[int, int, int, int]:
        """Converte coordinate cella in coordinate canvas (x1, y1, x2, y2)."""
        M = self.MARGIN
        x1 = M + col * CELL_SIZE
        y1 = M + row * CELL_SIZE
        return x1, y1, x1 + CELL_SIZE, y1 + CELL_SIZE

    def _canvas_to_cell(self, x: int, y: int) -> tuple[int, int] | None:
        """Converte coordinate canvas in (row, col), o None se fuori griglia."""
        M = self.MARGIN
        col = (x - M) // CELL_SIZE
        row = (y - M) // CELL_SIZE
        if 0 <= row < GRID_SIZE and 0 <= col < GRID_SIZE:
            return row, col
        return None

    def _draw_grid(self):
        """Disegna la griglia completa sul Canvas, incluse etichette A-J e 1-10."""
        self.canvas.delete("all")
        M = self.MARGIN

        # Etichette colonne (lettere)
        for c in range(GRID_SIZE):
            x = M + c * CELL_SIZE + CELL_SIZE // 2
            self.canvas.create_text(x, M // 2, text=chr(65 + c),
                                    fill=C_ACCENT, font=("Courier", 8, "bold"))
        # Etichette righe (numeri)
        for r in range(GRID_SIZE):
            y = M + r * CELL_SIZE + CELL_SIZE // 2
            self.canvas.create_text(M // 2, y, text=str(r + 1),
                                    fill=C_ACCENT, font=("Courier", 8, "bold"))

        # Celle
        for r in range(GRID_SIZE):
            for c in range(GRID_SIZE):
                x1, y1, x2, y2 = self._cell_to_canvas(r, c)
                state = self.grid_state[r][c]
                color = C_SHIP if state == "S" else C_WATER
                self.canvas.create_rectangle(x1, y1, x2, y2,
                                             fill=color, outline=C_BG2,
                                             tags=f"cell_{r}_{c}")

    def _preview_cells(self, row: int, col: int) -> list[tuple[int, int]]:
        """
        Calcola le celle che la nave corrente occuperebbe partendo da (row, col).

        Returns:
            Lista di tuple (r, c), vuota se fuori griglia.
        """
        if self.current_ship_idx >= len(self.fleet_to_place):
            return []
        _, length = self.fleet_to_place[self.current_ship_idx]
        cells = []
        for i in range(length):
            r = row + (0 if self.horizontal else i)
            c = col + (i if self.horizontal else 0)
            if not (0 <= r < GRID_SIZE and 0 <= c < GRID_SIZE):
                return []  # Esce dalla griglia → preview non valida
            cells.append((r, c))
        return cells

    # ──────────────────────────────────────────────
    # Event handlers Canvas
    # ──────────────────────────────────────────────

    def _on_hover(self, event):
        """Mostra un'anteprima della nave mentre il cursore scorre sulla griglia."""
        cell = self._canvas_to_cell(event.x, event.y)
        self._draw_grid()  # Ridisegna per cancellare preview precedente
        if cell:
            row, col = cell
            cells = self._preview_cells(row, col)
            valid = cells and all(self.grid_state[r][c] is None for r, c in cells)
            for r, c in cells:
                x1, y1, x2, y2 = self._cell_to_canvas(r, c)
                color = C_PREVIEW if valid else C_HIT
                self.canvas.create_rectangle(x1, y1, x2, y2,
                                             fill=color, outline=C_BG2)

    def _on_leave(self, event):
        """Rimuove la preview quando il cursore lascia il canvas."""
        self._draw_grid()

    def _on_click(self, event):
        """Posiziona la nave corrente sulla cella cliccata."""
        if self.current_ship_idx >= len(self.fleet_to_place):
            return
        cell = self._canvas_to_cell(event.x, event.y)
        if not cell:
            return
        row, col = cell
        cells = self._preview_cells(row, col)
        if not cells:
            self.status_var.set("⚠ Posizione non valida: fuori dalla griglia!")
            return
        # Verifica sovrapposizioni
        if any(self.grid_state[r][c] is not None for r, c in cells):
            self.status_var.set("⚠ Posizione occupata!")
            return

        # Posizionamento confermato
        name, length = self.fleet_to_place[self.current_ship_idx]
        for r, c in cells:
            self.grid_state[r][c] = "S"
        self.placed_ships.append({
            "name": name, "row": row, "col": col,
            "length": length, "horizontal": self.horizontal
        })
        self.current_ship_idx += 1
        self.status_var.set(f"✓ {name} posizionata!")
        self._draw_grid()
        self._update_fleet_list()
        self._update_ship_label()

        # Tutte le navi posizionate: invia al server
        if self.current_ship_idx >= len(self.fleet_to_place):
            self._send_placement()

    # ──────────────────────────────────────────────
    # Controlli UI
    # ──────────────────────────────────────────────

    def _update_orientation(self):
        """Aggiorna il flag di orientamento in base al RadioButton selezionato."""
        self.horizontal = self.orient_var.get() == "Orizzontale"

    def _update_ship_label(self):
        """Aggiorna l'etichetta della nave corrente da posizionare."""
        if self.current_ship_idx < len(self.fleet_to_place):
            name, length = self.fleet_to_place[self.current_ship_idx]
            self.ship_label.config(text=f"{name}\n({'▬' * length})")
        else:
            self.ship_label.config(text="✓ Tutte posizionate!")

    def _update_fleet_list(self):
        """Aggiorna la listbox con le navi ancora da posizionare."""
        self.fleet_listbox.delete(0, tk.END)
        for i, (name, length) in enumerate(self.fleet_to_place):
            prefix = "✓ " if i < self.current_ship_idx else "  "
            self.fleet_listbox.insert(tk.END, f"{prefix}{name} ({'▬'*length})")
            if i < self.current_ship_idx:
                self.fleet_listbox.itemconfig(i, fg="#556677")

    def _auto_place(self):
        """
        Posiziona automaticamente le navi rimanenti con algoritmo random.
        Utile per velocizzare il testing.
        """
        from game_logic import Board
        board = Board()
        # Prima piazza quelle già messe dal giocatore
        for sp in self.placed_ships:
            board.place_ship(sp["name"], sp["row"], sp["col"], sp["length"], sp["horizontal"])

        remaining = self.fleet_to_place[self.current_ship_idx:]
        for name, length in remaining:
            import random
            placed = False
            for _ in range(1000):
                h = random.choice([True, False])
                r = random.randint(0, GRID_SIZE - 1)
                c = random.randint(0, GRID_SIZE - 1)
                if board.place_ship(name, r, c, length, h):
                    self.placed_ships.append({
                        "name": name, "row": r, "col": c,
                        "length": length, "horizontal": h
                    })
                    # Aggiorna griglia locale
                    for i in range(length):
                        nr = r + (0 if h else i)
                        nc = c + (i if h else 0)
                        self.grid_state[nr][nc] = "S"
                    placed = True
                    break
            if not placed:
                self.status_var.set("⚠ Auto-posizionamento fallito, riprova.")
                return

        self.current_ship_idx = len(self.fleet_to_place)
        self._draw_grid()
        self._update_fleet_list()
        self._update_ship_label()
        self._send_placement()

    def _send_placement(self):
        """Invia la lista di navi posizionate al server."""
        self.status_var.set("📡 Invio posizionamento al server…")
        self.app.send(MSG_PLACE_OK, {"ships": self.placed_ships})

    # ──────────────────────────────────────────────
    # Messaggi dal server
    # ──────────────────────────────────────────────

    def handle_message(self, msg: dict):
        """
        Gestisce i messaggi del server durante la fase di posizionamento.

        Args:
            msg: Messaggio ricevuto.
        """
        t = msg.get("type")
        p = msg.get("payload", {})

        if t == MSG_PLACE_OK:
            self.status_var.set("✓ Server: posizionamento accettato. In attesa dell'avversario…")
        elif t == MSG_PLACE_ERR:
            messagebox.showerror("Errore", p.get("message", "Posizionamento non valido."))
            # Reset e riposiziona
            self.grid_state = [[None] * GRID_SIZE for _ in range(GRID_SIZE)]
            self.placed_ships = []
            self.current_ship_idx = 0
            self._draw_grid()
            self._update_fleet_list()
            self._update_ship_label()
        elif t == MSG_START:
            # L'altro giocatore è pronto: vai in game
            opp = p.get("player2") if self.app.nickname == p.get("player1") else p.get("player1")
            self.app._show_game(opp)
        elif t == MSG_YOUR_TURN:
            # Il server ha già inviato START; questo arriva subito dopo
            pass
        elif t == MSG_OPPONENT_DC:
            messagebox.showwarning("Disconnessione", "L'avversario si è disconnesso.")


# ──────────────────────────────────────────────
# SCHERMATA 3: Gioco
# ──────────────────────────────────────────────

class GameScreen(tk.Frame):
    """
    Schermata principale di gioco con due griglie affiancate:
      - Griglia di sinistra: la tua (mostra le navi e i colpi ricevuti)
      - Griglia di destra:   quella nemica (target per i tuoi colpi)
    Più una sezione di chat integrata.
    """

    def __init__(self, app: BattagliaNavaleApp, opp_nick: str):
        super().__init__(app, bg=C_BG)
        self.app = app
        self.opp_nick = opp_nick
        self.my_turn = False
        # Matrici che tracciano lo stato delle celle sulle due griglie
        self.my_grid   = [[None] * GRID_SIZE for _ in range(GRID_SIZE)]
        self.enemy_grid = [[None] * GRID_SIZE for _ in range(GRID_SIZE)]
        self._build_ui()

    MARGIN = 24

    def _build_ui(self):
        """Costruisce l'interfaccia di gioco."""
        # Header
        header = tk.Frame(self, bg=C_BG)
        header.pack(fill="x", padx=10, pady=(10, 0))
        tk.Label(header, text=f"⚓ {self.app.nickname}", font=("Courier", 12, "bold"),
                 fg=C_WIN, bg=C_BG).pack(side="left")
        self.turn_label = tk.Label(header, text="⏳ Attendendo…",
                                   font=("Courier", 12, "bold"), fg=C_ACCENT, bg=C_BG)
        self.turn_label.pack(side="left", padx=30)
        tk.Label(header, text=f"⚔ {self.opp_nick}", font=("Courier", 12, "bold"),
                 fg=C_LOSE, bg=C_BG).pack(side="right")

        main = tk.Frame(self, bg=C_BG)
        main.pack(padx=10, pady=5)

        # Griglia sinistra (la mia)
        left = tk.Frame(main, bg=C_BG)
        left.pack(side="left", padx=10)
        tk.Label(left, text="La tua flotta", font=("Courier", 10),
                 fg=C_TEXT, bg=C_BG).pack()
        self.my_canvas = tk.Canvas(left,
                                   width=CELL_SIZE * GRID_SIZE + self.MARGIN * 2,
                                   height=CELL_SIZE * GRID_SIZE + self.MARGIN * 2,
                                   bg=C_BG, highlightthickness=0)
        self.my_canvas.pack()

        # Griglia destra (nemica)
        right = tk.Frame(main, bg=C_BG)
        right.pack(side="left", padx=10)
        tk.Label(right, text=f"Flotta di {self.opp_nick}", font=("Courier", 10),
                 fg=C_TEXT, bg=C_BG).pack()
        self.enemy_canvas = tk.Canvas(right,
                                      width=CELL_SIZE * GRID_SIZE + self.MARGIN * 2,
                                      height=CELL_SIZE * GRID_SIZE + self.MARGIN * 2,
                                      bg=C_BG, highlightthickness=0)
        self.enemy_canvas.pack()
        self.enemy_canvas.bind("<Button-1>", self._on_shoot)
        self.enemy_canvas.bind("<Motion>", self._on_enemy_hover)
        self.enemy_canvas.bind("<Leave>", self._on_enemy_leave)

        # Pannello chat (sotto)
        chat_frame = tk.Frame(self, bg=C_BG2, padx=10, pady=8)
        chat_frame.pack(fill="x", padx=10, pady=(5, 10))
        tk.Label(chat_frame, text="💬 Chat:", font=("Courier", 9, "bold"),
                 fg=C_ACCENT, bg=C_BG2).pack(anchor="w")

        self.chat_log = scrolledtext.ScrolledText(
            chat_frame, height=5, font=("Courier", 9),
            bg=C_WATER, fg=C_TEXT, state="disabled",
            wrap=tk.WORD, relief="flat"
        )
        self.chat_log.pack(fill="x", pady=(3, 5))

        chat_input_frame = tk.Frame(chat_frame, bg=C_BG2)
        chat_input_frame.pack(fill="x")
        self.chat_entry = tk.Entry(chat_input_frame, font=("Courier", 9),
                                   bg=C_WATER, fg=C_TEXT, insertbackground=C_ACCENT)
        self.chat_entry.pack(side="left", fill="x", expand=True)
        self.chat_entry.bind("<Return>", self._send_chat)
        tk.Button(chat_input_frame, text="Invia",
                  font=("Courier", 9), bg=C_ACCENT, fg=C_BG,
                  command=self._send_chat).pack(side="right", padx=(5, 0))

        # Disegna le griglie iniziali
        self._draw_my_grid()
        self._draw_enemy_grid()

    # ──────────────────────────────────────────────
    # Disegno griglie
    # ──────────────────────────────────────────────

    def _cell_rect(self, row: int, col: int) -> tuple[int, int, int, int]:
        """Restituisce (x1, y1, x2, y2) per la cella dati row/col."""
        M = self.MARGIN
        x1 = M + col * CELL_SIZE
        y1 = M + row * CELL_SIZE
        return x1, y1, x1 + CELL_SIZE, y1 + CELL_SIZE

    def _canvas_to_cell(self, x: int, y: int) -> tuple[int, int] | None:
        """Converte coordinate canvas in (row, col)."""
        M = self.MARGIN
        col = (x - M) // CELL_SIZE
        row = (y - M) // CELL_SIZE
        if 0 <= row < GRID_SIZE and 0 <= col < GRID_SIZE:
            return row, col
        return None

    def _draw_grid_base(self, canvas: tk.Canvas, grid: list, show_ships: bool):
        """
        Disegna una griglia completa su un canvas.

        Args:
            canvas:     Il Canvas Tkinter da usare.
            grid:       Matrice di stato celle.
            show_ships: Se True, mostra le celle 'S' (navi) colorate.
        """
        canvas.delete("all")
        M = self.MARGIN
        # Etichette
        for c in range(GRID_SIZE):
            canvas.create_text(M + c * CELL_SIZE + CELL_SIZE // 2, M // 2,
                                text=chr(65 + c), fill=C_ACCENT, font=("Courier", 8, "bold"))
        for r in range(GRID_SIZE):
            canvas.create_text(M // 2, M + r * CELL_SIZE + CELL_SIZE // 2,
                                text=str(r + 1), fill=C_ACCENT, font=("Courier", 8, "bold"))
        # Celle
        COLOR_MAP = {
            None: C_WATER,
            "S":  C_SHIP if show_ships else C_WATER,
            "H":  C_HIT,
            "M":  C_MISS,
            "X":  C_SUNK,  # Nave affondata
        }
        for r in range(GRID_SIZE):
            for c in range(GRID_SIZE):
                x1, y1, x2, y2 = self._cell_rect(r, c)
                color = COLOR_MAP.get(grid[r][c], C_WATER)
                canvas.create_rectangle(x1, y1, x2, y2, fill=color, outline=C_BG2)
                # Simboli sulle celle
                state = grid[r][c]
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                if state == "H":
                    canvas.create_text(cx, cy, text="✕", fill="white", font=("Courier", 12, "bold"))
                elif state == "M":
                    canvas.create_text(cx, cy, text="·", fill=C_ACCENT, font=("Courier", 14))

    def _draw_my_grid(self):
        """Ridisegna la griglia del giocatore locale (mostra le navi)."""
        self._draw_grid_base(self.my_canvas, self.my_grid, show_ships=True)

    def _draw_enemy_grid(self, hover_cell=None):
        """
        Ridisegna la griglia nemica (nasconde le navi avversarie).

        Args:
            hover_cell: Se non None, evidenzia questa cella come preview del colpo.
        """
        self._draw_grid_base(self.enemy_canvas, self.enemy_grid, show_ships=False)
        if hover_cell and self.my_turn:
            r, c = hover_cell
            if self.enemy_grid[r][c] is None:
                x1, y1, x2, y2 = self._cell_rect(r, c)
                self.enemy_canvas.create_rectangle(x1, y1, x2, y2,
                                                   fill=C_ACCENT, outline=C_BG2)

    # ──────────────────────────────────────────────
    # Event handlers griglia nemica
    # ──────────────────────────────────────────────

    def _on_enemy_hover(self, event):
        """Preview del cursore sulla griglia nemica durante il proprio turno."""
        if not self.my_turn:
            return
        cell = self._canvas_to_cell(event.x, event.y)
        self._draw_enemy_grid(hover_cell=cell)

    def _on_enemy_leave(self, event):
        """Rimuove la preview."""
        self._draw_enemy_grid()

    def _on_shoot(self, event):
        """
        Gestisce il click sulla griglia nemica: invia il colpo al server.
        Ignora clic fuori turno o su celle già colpite.
        """
        if not self.my_turn:
            return
        cell = self._canvas_to_cell(event.x, event.y)
        if not cell:
            return
        row, col = cell
        if self.enemy_grid[row][col] is not None:
            return  # Cella già colpita
        self.my_turn = False
        self.turn_label.config(text="⏳ Aspetta…", fg=C_TEXT)
        self.app.send(MSG_SHOT, {"row": row, "col": col})

    # ──────────────────────────────────────────────
    # Chat
    # ──────────────────────────────────────────────

    def _send_chat(self, event=None):
        """Invia il messaggio di chat digitato."""
        text = self.chat_entry.get().strip()
        if not text:
            return
        self.app.send(MSG_CHAT, {"text": text})
        self._append_chat(f"Tu: {text}")
        self.chat_entry.delete(0, tk.END)

    def _append_chat(self, line: str):
        """Aggiunge una riga al log della chat."""
        self.chat_log.config(state="normal")
        self.chat_log.insert(tk.END, line + "\n")
        self.chat_log.see(tk.END)
        self.chat_log.config(state="disabled")

    # ──────────────────────────────────────────────
    # Messaggi dal server
    # ──────────────────────────────────────────────

    def handle_message(self, msg: dict):
        """
        Gestisce tutti i messaggi del server durante la fase di gioco.

        Args:
            msg: Dizionario {"type": ..., "payload": ...}.
        """
        t = msg.get("type")
        p = msg.get("payload", {})

        if t == MSG_YOUR_TURN:
            self.my_turn = True
            self.turn_label.config(text="🎯 IL TUO TURNO", fg=C_WIN)

        elif t == MSG_WAIT_TURN:
            self.my_turn = False
            self.turn_label.config(text=f"⏳ Turno di {self.opp_nick}", fg=C_TEXT)

        elif t == MSG_SHOT_RESULT:
            # Aggiorna la griglia nemica con il risultato del mio colpo
            row, col, result = p["row"], p["col"], p["result"]
            if result == RESULT_MISS:
                self.enemy_grid[row][col] = "M"
            elif result in (RESULT_HIT, RESULT_SUNK):
                self.enemy_grid[row][col] = "H"
            sunk = p.get("sunk_name")
            info = f" ({sunk} AFFONDATA! 💥)" if sunk else ""
            self._append_chat(f"[Sistema] Tiro su {chr(65+col)}{row+1}: {result}{info}")
            self._draw_enemy_grid()

        elif t == MSG_OPP_SHOT:
            # Aggiorna la mia griglia con il colpo ricevuto
            row, col, result = p["row"], p["col"], p["result"]
            if result == RESULT_MISS:
                self.my_grid[row][col] = "M"
            elif result in (RESULT_HIT, RESULT_SUNK):
                self.my_grid[row][col] = "H"
            sunk = p.get("sunk_name")
            info = f" ({sunk} AFFONDATA! 💥)" if sunk else ""
            self._append_chat(f"[Sistema] {self.opp_nick} ha sparato su {chr(65+col)}{row+1}: {result}{info}")
            self._draw_my_grid()

        elif t == MSG_CHAT:
            sender = p.get("from", self.opp_nick)
            text   = p.get("text", "")
            self._append_chat(f"{sender}: {text}")

        elif t == MSG_WIN:
            self.my_turn = False
            self.turn_label.config(text="🏆 HAI VINTO!", fg=C_WIN)
            messagebox.showinfo("🏆 Vittoria!", p.get("message", "Hai vinto!"))

        elif t == MSG_LOSE:
            self.my_turn = False
            self.turn_label.config(text="💀 HAI PERSO", fg=C_LOSE)
            messagebox.showinfo("💀 Sconfitta", p.get("message", "Hai perso."))

        elif t == MSG_OPPONENT_DC:
            self.my_turn = False
            self.turn_label.config(text="⚠ Avversario DC", fg=C_SUNK)
            messagebox.showwarning("Disconnessione", p.get("message", "L'avversario si è disconnesso."))

        elif t == MSG_START:
            # Questo può arrivare dopo il posizionamento se c'è un delay
            pass


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

if __name__ == "__main__":
    app = BattagliaNavaleApp()
    app.mainloop()
