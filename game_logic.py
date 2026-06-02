"""
game_logic.py - Logica di gioco per Battaglia Navale.

Questo modulo è completamente indipendente dalla rete: gestisce solo lo stato
della griglia, il posizionamento delle navi e la verifica dei colpi.
Separare la logica dal layer di rete rende il codice più testabile e riusabile.
"""

from protocol import GRID_SIZE, FLEET, RESULT_MISS, RESULT_HIT, RESULT_SUNK


class Ship:
    """
    Rappresenta una singola nave sul campo di gioco.

    Attributi:
        name (str):              Nome della nave (es. "Portaerei").
        cells (set of tuples):   Insieme delle coordinate (riga, colonna) occupate.
        hits (set of tuples):    Celle colpite fin'ora.
    """

    def __init__(self, name: str, cells: list[tuple[int, int]]):
        """
        Inizializza la nave con il suo nome e le celle che occupa.

        Args:
            name:  Nome identificativo della nave.
            cells: Lista di tuple (row, col) che la nave occupa.
        """
        self.name = name
        self.cells = set(cells)
        self.hits: set[tuple[int, int]] = set()

    def receive_hit(self, row: int, col: int) -> bool:
        """
        Registra un colpo sulla nave.

        Args:
            row: Riga del colpo.
            col: Colonna del colpo.

        Returns:
            True se il colpo ha colpito questa nave, False altrimenti.
        """
        if (row, col) in self.cells:
            self.hits.add((row, col))
            return True
        return False

    @property
    def is_sunk(self) -> bool:
        """
        Proprietà calcolata: la nave è affondata quando tutte le sue celle sono state colpite.
        """
        return self.cells == self.hits


class Board:
    """
    Rappresenta il campo di gioco di un giocatore: griglia + flotta.

    La griglia è una matrice GRID_SIZE x GRID_SIZE di celle, ognuna con un
    valore di stato: None (vuota), 'S' (nave), 'H' (colpita), 'M' (mancato).
    """

    def __init__(self):
        """
        Inizializza una griglia vuota e una lista di navi vuota.
        """
        # Griglia visibile solo al proprietario (contiene posizioni navi)
        self.grid: list[list] = [[None] * GRID_SIZE for _ in range(GRID_SIZE)]
        self.ships: list[Ship] = []
        # Griglia "nebbia di guerra": traccia i colpi ricevuti dall'avversario
        self.shot_grid: list[list] = [[None] * GRID_SIZE for _ in range(GRID_SIZE)]

    def place_ship(self, name: str, row: int, col: int, length: int, horizontal: bool) -> bool:
        """
        Posiziona una nave sulla griglia.

        Verifica che le celle richieste siano all'interno della griglia e non
        sovrapposte ad altre navi già presenti.

        Args:
            name:       Nome della nave.
            row:        Riga di inizio (0-indexed).
            col:        Colonna di inizio (0-indexed).
            length:     Lunghezza della nave in celle.
            horizontal: True = nave orizzontale, False = verticale.

        Returns:
            True se il posizionamento è valido e avvenuto con successo, False altrimenti.
        """
        cells = []
        for i in range(length):
            r = row + (0 if horizontal else i)
            c = col + (i if horizontal else 0)
            # Controlla limiti griglia
            if not (0 <= r < GRID_SIZE and 0 <= c < GRID_SIZE):
                return False
            # Controlla sovrapposizioni
            if self.grid[r][c] is not None:
                return False
            cells.append((r, c))

        # Posizionamento valido: aggiorna griglia e lista navi
        for r, c in cells:
            self.grid[r][c] = "S"
        self.ships.append(Ship(name, cells))
        return True

    def receive_shot(self, row: int, col: int) -> tuple[str, str | None]:
        """
        Processa un colpo ricevuto in questa cella.

        Aggiorna sia la griglia sia lo stato della nave colpita.

        Args:
            row: Riga del colpo (0-indexed).
            col: Colonna del colpo (0-indexed).

        Returns:
            Una tupla (risultato, nome_nave_affondata_o_None).
            Il risultato è una delle costanti RESULT_*.
        """
        if self.grid[row][col] == "S":
            self.grid[row][col] = "H"
            self.shot_grid[row][col] = "H"
            # Cerca la nave colpita e aggiorna il suo stato
            for ship in self.ships:
                if ship.receive_hit(row, col):
                    if ship.is_sunk:
                        return RESULT_SUNK, ship.name
                    return RESULT_HIT, None
        else:
            self.shot_grid[row][col] = "M"
            return RESULT_MISS, None

        return RESULT_MISS, None  # fallback (non dovrebbe accadere)

    def all_sunk(self) -> bool:
        """
        Controlla se tutta la flotta è stata affondata (condizione di sconfitta).

        Returns:
            True se tutte le navi sono affondate.
        """
        return all(ship.is_sunk for ship in self.ships)

    def ships_count(self) -> int:
        """Restituisce il numero di navi ancora a galla."""
        return sum(1 for ship in self.ships if not ship.is_sunk)

    def place_fleet_default(self, fleet=None):
        """
        Posiziona la flotta in modo predefinito (per testing o se il giocatore non piazza manualmente).
        Usa un algoritmo greedy che prova posizioni finché non trova una valida.

        Args:
            fleet: Lista di (nome, lunghezza); se None usa FLEET globale.
        """
        import random
        if fleet is None:
            fleet = FLEET

        for name, length in fleet:
            placed = False
            attempts = 0
            while not placed and attempts < 1000:
                horizontal = random.choice([True, False])
                row = random.randint(0, GRID_SIZE - 1)
                col = random.randint(0, GRID_SIZE - 1)
                placed = self.place_ship(name, row, col, length, horizontal)
                attempts += 1
