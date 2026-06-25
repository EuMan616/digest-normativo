#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FASE 3 - SINTESI
================
Per ogni ATTO PRIMARIO del digest del giorno, scarica il testo dalla fonte
(HTML/XML o PDF), e genera 10-15 righe in italiano VINCOLATE al testo fornito,
con il link sempre presente. Le SEGNALAZIONI dirittobancario non vengono mai
sintetizzate: restano elenco di link.

Se il testo di un atto non e' recuperabile, ripiega sull'estratto del feed e lo
segnala ("sintesi da estratto, verificare alla fonte"), senza spacciare un
riassunto parziale per completo.

Richiede la variabile d'ambiente ANTHROPIC_API_KEY (impostata nei GitHub
Secrets). Si lancia dopo process.py:  python summarize.py
"""

from __future__ import annotations

import os
import sys
import io
import json
import glob
import time
import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"

# --- Parametri (modificabili) ------------------------------------------------
MODEL = "claude-haiku-4-5-20251001"   # modello economico; se l'API segnala
                                      # "model not found", aggiorna questa stringa
MAX_INPUT_CHARS = 24000               # taglio del testo sorgente dato al modello
MAX_OUTPUT_TOKENS = 700               # ~10-15 righe
MAX_SUMMARIES = 40                    # tetto di sicurezza sul numero di chiamate/giorno
HTTP_TIMEOUT = 45
USER_AGENT = "digest-normativo/0.1 (uso personale)"
API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

SYSTEM_PROMPT = (
    "Sei un assistente che riassume atti di regolamentazione finanziaria per un "
    "avvocato esperto. Regole tassative: riassumi ESCLUSIVAMENTE sulla base del "
    "testo fornito; non aggiungere informazioni, numeri, date o riferimenti che "
    "non siano nel testo; non inventare nulla. Se il testo e' insufficiente o "
    "sembra troncato, dillo esplicitamente. Scrivi in italiano, 10-15 righe. "
    "Per atti complessi struttura il riassunto in: cosa cambia, soggetti incisi, "
    "decorrenza/entrata in vigore. Tono tecnico e asciutto, niente preamboli."
)


def _today() -> str:
    return datetime.date.today().isoformat()


def _latest_digest() -> Path | None:
    files = sorted(glob.glob(str(DATA_DIR / "digest_*.json")))
    # esclude eventuali file gia' sintetizzati
    files = [f for f in files if "summarized" not in f]
    return Path(files[-1]) if files else None


# --- Recupero testo dalla fonte ---------------------------------------------
def _extract_pdf_text(content: bytes) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(content))
        parts = []
        for page in reader.pages[:30]:        # max 30 pagine
            parts.append(page.extract_text() or "")
        return "\n".join(parts).strip()
    except Exception:
        return ""


def fetch_source_text(url: str) -> tuple[str, str]:
    """Ritorna (testo, nota). Testo vuoto se non recuperabile."""
    if not url:
        return "", "nessun link"
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
    except Exception as ex:
        return "", f"download fallito: {ex}"

    ctype = r.headers.get("Content-Type", "").lower()
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        text = _extract_pdf_text(r.content)
        if not text:
            return "", "PDF senza testo estraibile (forse scansione)"
    else:
        soup = BeautifulSoup(r.content, "lxml")
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        main = soup.find("main") or soup.find("article") or soup.body or soup
        text = main.get_text(" ", strip=True)

    text = " ".join(text.split())
    if len(text) < 40:
        return "", "testo troppo breve / pagina dinamica"
    return text[:MAX_INPUT_CHARS], "ok"


# --- Chiamata al modello -----------------------------------------------------
def call_anthropic(api_key: str, title: str, source_text: str) -> str:
    body = {
        "model": MODEL,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": [{
            "role": "user",
            "content": f"Titolo dell'atto:\n{title}\n\nTesto della fonte "
                       f"(eventualmente troncato):\n{source_text}\n\n"
                       f"Scrivi il riassunto seguendo le regole.",
        }],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    for attempt in (1, 2):
        try:
            r = requests.post(API_URL, headers=headers, json=body, timeout=HTTP_TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                return "".join(
                    b.get("text", "") for b in data.get("content", [])
                    if b.get("type") == "text"
                ).strip()
            if r.status_code in (429, 500, 502, 503, 529) and attempt == 1:
                time.sleep(5)
                continue
            return f"[errore API {r.status_code}: {r.text[:200]}]"
        except Exception as ex:
            if attempt == 1:
                time.sleep(5)
                continue
            return f"[errore chiamata: {ex}]"
    return "[errore: nessuna risposta]"


def summarize_item(api_key: str, item: dict) -> dict:
    text, note = fetch_source_text(item.get("link", ""))
    if text:
        basis = "testo primario"
        source_text = text
    else:
        source_text = item.get("summary_raw", "")
        basis = f"estratto del feed (verificare alla fonte - {note})"
    if not source_text:
        item["summary"] = "(testo non disponibile: aprire il link alla fonte)"
        item["summary_basis"] = note
        return item
    item["summary"] = call_anthropic(api_key, item.get("title", ""), source_text)
    item["summary_basis"] = basis
    return item


def run() -> int:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("ERRORE: manca ANTHROPIC_API_KEY (impostala nei GitHub Secrets).")
        return 1

    digest_path = _latest_digest()
    if not digest_path:
        print("ERRORE: nessun digest_*.json. Esegui prima process.py.")
        return 1

    digest = json.loads(digest_path.read_text(encoding="utf-8"))
    primary = digest.get("primary", [])
    signals = digest.get("signals", [])

    if len(primary) > MAX_SUMMARIES:
        print(f"ATTENZIONE: {len(primary)} atti, sintetizzo i primi {MAX_SUMMARIES}.")
        primary = primary[:MAX_SUMMARIES]

    print(f"Sintesi di {len(primary)} atti primari...\n")
    out = []
    for i, item in enumerate(primary, 1):
        print(f"  [{i}/{len(primary)}] {item.get('title','')[:70]}")
        out.append(summarize_item(api_key, item))
        time.sleep(1)        # gentile verso le fonti e l'API

    result = {"generated_at": _today(), "primary": out, "signals": signals}
    out_path = DATA_DIR / f"digest_summarized_{_today()}.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 60)
    print(f"  SINTESI COMPLETATA  {_today()}")
    print("=" * 60)
    print(f"  Atti sintetizzati: {len(out)}")
    print(f"  Segnalazioni (link): {len(signals)}")
    print(f"  File: {out_path.relative_to(ROOT)}")
    print("=" * 60 + "\n")
    for it in out[:5]:
        print(f"- {it.get('title','')[:75]}")
        print(f"  [base: {it.get('summary_basis')}]")
        print(f"  {it.get('summary','')[:300]}...\n")
    return 0


if __name__ == "__main__":
    sys.exit(run())
