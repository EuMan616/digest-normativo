#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FASE 2 - SOLO LE NOVITA', SENZA DOPPIONI
========================================
Legge l'ultimo file prodotto dalla raccolta (data/collected_<data>.json),
tiene solo gli atti NON gia' visti (storico in state/seen.json) ed entro una
finestra di pochi giorni, elimina i doppioni tra fonti, e salva l'elenco degli
atti "nuovi di oggi" in data/digest_<data>.json.

Aggiorna poi state/seen.json con tutto cio' che ha visto, cosi' domani non
ripropone le stesse cose. Si lancia dopo collect.py:  python process.py
"""

from __future__ import annotations

import sys
import re
import json
import glob
import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
STATE_DIR = ROOT / "state"
SEEN_FILE = STATE_DIR / "seen.json"

WINDOW_DAYS = 3        # alla prima esecuzione tiene solo gli atti datati negli ultimi N giorni

# Titoli da scartare tra le "segnalazioni" discovery (eventi/marketing, non normativa)
SIGNAL_STOPWORDS = [
    "webinar", "convegno", "evento", "ne parliamo", "save the date",
    "iscriviti", "iscrizioni", "corso ", "master ", "podcast", "intervista",
    "rassegna stampa", "in vigore dal", "appuntamento",
]


def _is_signal_noise(title: str) -> bool:
    t = title.lower()
    return any(w in t for w in SIGNAL_STOPWORDS)


def _today() -> str:
    return datetime.date.today().isoformat()


def _latest_collected() -> Path | None:
    files = sorted(glob.glob(str(DATA_DIR / "collected_*.json")))
    return Path(files[-1]) if files else None


def _load_seen() -> tuple[set[str], bool]:
    """Ritorna (insieme_uid_gia_visti, prima_esecuzione)."""
    if not SEEN_FILE.exists():
        return set(), True
    try:
        data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        return set(data.get("seen", [])), False
    except Exception:
        return set(), False


def _within_window(published: str | None) -> bool:
    if not published:
        return False
    try:
        d = datetime.date.fromisoformat(published)
    except Exception:
        return False
    return (datetime.date.today() - d).days <= WINDOW_DAYS


def _norm_title(title: str) -> str:
    """Chiave per la deduplica: minuscolo, senza punteggiatura ne' spazi multipli."""
    t = title.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:80]


def select_new(items: list[dict], seen: set[str], first_run: bool) -> list[dict]:
    """Tiene gli atti non gia' visti. Per quelli datati applica la finestra
    temporale; quelli senza data si tengono solo dopo la prima esecuzione
    (alla prima si registrano nello storico senza inondare il primo digest)."""
    out = []
    for it in items:
        if it.get("uid") in seen:
            continue
        published = it.get("published")
        if published:
            if _within_window(published):
                out.append(it)
        else:
            if not first_run:
                out.append(it)
    return out


def deduplicate(items: list[dict]) -> list[dict]:
    """Unisce gli atti con lo stesso titolo normalizzato (stesso atto da piu'
    fonti). Tiene il primo e annota tutte le fonti in cui e' comparso."""
    groups: dict[str, dict] = {}
    for it in items:
        key = _norm_title(it.get("title", "")) or it.get("uid")
        if key not in groups:
            it = dict(it)
            it["also_in"] = []
            groups[key] = it
        else:
            src = it.get("source_name", it.get("source_id", ""))
            if src and src not in groups[key]["also_in"] \
                    and src != groups[key].get("source_name"):
                groups[key]["also_in"].append(src)
    return list(groups.values())


def run() -> int:
    collected_path = _latest_collected()
    if not collected_path:
        print("ERRORE: nessun file collected_*.json. Esegui prima collect.py.")
        return 1

    items = json.loads(collected_path.read_text(encoding="utf-8"))
    seen, first_run = _load_seen()

    new_items = select_new(items, seen, first_run)

    # separa fonti ufficiali (digest primario) da antenna discovery (segnalazioni)
    primary = [it for it in new_items if it.get("item_type") != "discovery"]
    signals = [it for it in new_items if it.get("item_type") == "discovery"]

    primary = deduplicate(primary)
    primary.sort(key=lambda x: x.get("published") or "", reverse=True)

    # segnalazioni: togli rumore (eventi/marketing), deduplica, e scarta quelle
    # che ripetono un atto gia' presente tra le fonti ufficiali
    primary_keys = {_norm_title(it.get("title", "")) for it in primary}
    signals = [it for it in signals if not _is_signal_noise(it.get("title", ""))]
    signals = deduplicate(signals)
    signals = [it for it in signals if _norm_title(it.get("title", "")) not in primary_keys]
    signals.sort(key=lambda x: x.get("published") or "", reverse=True)

    # salva il digest del giorno (due sezioni)
    DATA_DIR.mkdir(exist_ok=True)
    digest = {"generated_at": _today(), "primary": primary, "signals": signals}
    digest_path = DATA_DIR / f"digest_{_today()}.json"
    digest_path.write_text(json.dumps(digest, ensure_ascii=False, indent=2), encoding="utf-8")

    # aggiorna lo storico con TUTTO cio' che e' stato visto oggi
    STATE_DIR.mkdir(exist_ok=True)
    updated = seen | {it["uid"] for it in items if it.get("uid")}
    SEEN_FILE.write_text(
        json.dumps({"seen": sorted(updated), "updated_at": _today()},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # riepilogo
    print("\n" + "=" * 60)
    print(f"  ELABORAZIONE  {_today()}")
    print("=" * 60)
    print(f"  Voci grezze in ingresso:        {len(items)}")
    print(f"  Prima esecuzione:               {'si' if first_run else 'no'}")
    print(f"  Atti primari (da sintetizzare): {len(primary)}")
    print(f"  Segnalazioni dirittobancario:   {len(signals)}")
    print(f"  Storico totale memorizzato:     {len(updated)}")
    print(f"  Digest del giorno: {digest_path.relative_to(ROOT)}")
    print("=" * 60 + "\n")
    if primary:
        print("ATTI PRIMARI:")
        for it in primary[:15]:
            also = f"  [+{len(it['also_in'])} fonti]" if it.get("also_in") else ""
            print(f"  - ({it.get('source_id')}) {it.get('title','')[:78]}{also}")
    if signals:
        print("\nSEGNALAZIONI (solo link, non sintetizzate):")
        for it in signals[:10]:
            print(f"  - {it.get('title','')[:78]}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
