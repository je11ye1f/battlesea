"""
database.py - Gestione della persistenza delle statistiche con SQLite.

Ogni partita completata viene salvata nel database locale 'stats.db'.
Il modulo espone funzioni semplici per aggiornare e leggere le statistiche,
mantenendo il resto del codice disaccoppiato dal layer di persistenza.
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "stats.db")


def init_db():
    """
    Crea il database e la tabella 'players' se non esistono già.

    La tabella tiene traccia del nickname, vittorie e sconfitte di ogni giocatore.
    Viene chiamata all'avvio dell'applicazione.
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS players (
                nickname TEXT PRIMARY KEY,
                wins     INTEGER DEFAULT 0,
                losses   INTEGER DEFAULT 0
            )
        """)
        conn.commit()


def record_result(nickname: str, won: bool):
    """
    Aggiorna le statistiche di un giocatore dopo una partita.

    Usa INSERT OR IGNORE per creare il record se il giocatore è nuovo,
    poi UPDATE per incrementare il contatore corretto.

    Args:
        nickname: Nome del giocatore.
        won:      True se ha vinto, False se ha perso.
    """
    with sqlite3.connect(DB_PATH) as conn:
        # Inserisce il giocatore se non esiste ancora
        conn.execute(
            "INSERT OR IGNORE INTO players (nickname, wins, losses) VALUES (?, 0, 0)",
            (nickname,)
        )
        # Incrementa il contatore vittorie o sconfitte
        if won:
            conn.execute(
                "UPDATE players SET wins = wins + 1 WHERE nickname = ?",
                (nickname,)
            )
        else:
            conn.execute(
                "UPDATE players SET losses = losses + 1 WHERE nickname = ?",
                (nickname,)
            )
        conn.commit()


def get_stats(nickname: str) -> dict:
    """
    Recupera le statistiche di un giocatore dal database.

    Args:
        nickname: Nome del giocatore.

    Returns:
        Dizionario {"nickname": ..., "wins": ..., "losses": ..., "games": ...}
        oppure None se il giocatore non esiste.
    """
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT nickname, wins, losses FROM players WHERE nickname = ?",
            (nickname,)
        ).fetchone()

    if row:
        return {
            "nickname": row[0],
            "wins": row[1],
            "losses": row[2],
            "games": row[1] + row[2],
        }
    return None


def get_leaderboard(limit: int = 10) -> list[dict]:
    """
    Restituisce la classifica dei migliori giocatori per numero di vittorie.

    Args:
        limit: Numero massimo di righe da restituire.

    Returns:
        Lista di dizionari ordinata per vittorie decrescenti.
    """
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT nickname, wins, losses FROM players ORDER BY wins DESC LIMIT ?",
            (limit,)
        ).fetchall()

    return [
        {"nickname": r[0], "wins": r[1], "losses": r[2], "games": r[1] + r[2]}
        for r in rows
    ]
