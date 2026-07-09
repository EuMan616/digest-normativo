#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FASE 4 - EMAIL
==============
Compone il digest del giorno (atti sintetizzati + segnalazioni come link) in
un'email HTML+testo e la invia via Gmail SMTP. Salva sempre un'anteprima in
data/email_<data>.html (visibile nell'artifact). Se non ci sono novita', NON
invia (giorni silenziosi = nessuna email), salvo SEND_IF_EMPTY=true.

Variabili d'ambiente (GitHub Secrets):
  GMAIL_USER          indirizzo Gmail mittente (anche login SMTP)
  GMAIL_APP_PASSWORD  app password a 16 caratteri (non la password normale)
  DIGEST_TO           destinatario (se assente, usa GMAIL_USER)
  SEND_IF_EMPTY       "true" per inviare anche senza novita' (default: no)

Si lancia dopo summarize.py:  python email_digest.py
"""

from __future__ import annotations

import os
import sys
import re
import ssl
import html
import json
import glob
import smtplib
import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465


def _today() -> str:
    return datetime.date.today().isoformat()


def _strip_md(text: str) -> str:
    """Toglie eventuale markdown residuo dal riassunto (intestazioni, grassetti,
    trattini di elenco), cosi' la formattazione e' uniforme a prescindere dal modello."""
    if not text:
        return ""
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)     # # intestazioni
    text = text.replace("**", "").replace("__", "")        # **grassetto**
    text = re.sub(r"(?m)^\s*[-*]\s+", "", text)            # - elenco
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def _latest_summarized() -> Path | None:
    files = sorted(glob.glob(str(DATA_DIR / "digest_summarized_*.json")))
    return Path(files[-1]) if files else None


_MESI = ["", "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
         "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]


def _format_date_it(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        d = datetime.date.fromisoformat(iso)
        return f"{d.day} {_MESI[d.month]} {d.year}"
    except Exception:
        return ""


def _clean_title(t: str) -> str:
    """Toglie annotazioni tipo '[pdf, 385.9 KB]' in coda al titolo."""
    if not t:
        return ""
    return re.sub(r"\s*\[\s*pdf[^\]]*\]", "", t, flags=re.I).strip()


# --- Composizione ------------------------------------------------------------
def render_html(digest: dict) -> str:
    primary = digest.get("primary", [])
    signals = digest.get("signals", [])
    date = digest.get("generated_at", _today())

    css_block = "margin:0 0 22px 0;padding:0 0 18px 0;border-bottom:1px solid #e2e2e2;"
    parts = [f"""<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
        max-width:680px;margin:0 auto;color:#1a1a1a;line-height:1.5;">
      <h1 style="font-size:20px;margin:0 0 4px 0;">Digest normativo</h1>
      <div style="color:#666;font-size:13px;margin-bottom:24px;">{date} &middot;
        {len(primary)} atti &middot; {len(signals)} segnalazioni</div>"""]

    if not primary and not signals:
        parts.append('<div style="font-size:15px;color:#444;margin:8px 0 16px;">'
                     'Nessuna novità normativa per oggi. Controllo automatico '
                     'eseguito regolarmente: il sistema funziona.</div>')

    if primary:
        parts.append('<h2 style="font-size:15px;text-transform:uppercase;'
                      'letter-spacing:.5px;color:#444;">Atti</h2>')
        for it in primary:
            title = html.escape(_clean_title(it.get("title", "")))
            source = html.escape(it.get("source_name", ""))
            link = html.escape(it.get("link", ""))
            date_it = _format_date_it(it.get("published"))
            summary = html.escape(_strip_md(it.get("summary", ""))).replace("\n", "<br>")
            basis = it.get("summary_basis", "")
            also = it.get("also_in") or []
            also_txt = (" &middot; anche: " + html.escape(", ".join(also))) if also else ""
            flag = ""
            if basis and "estratto" in basis:
                flag = ('<div style="color:#9a6700;font-size:12px;margin-top:6px;">'
                        '&#9888; sintesi da estratto, verificare alla fonte</div>')
            subtitle = (f'<div style="font-size:12px;color:#888;margin:0 0 8px 0;">{date_it}</div>'
                        if date_it else '')
            parts.append(f"""<div style="{css_block}">
              <div style="font-size:12px;color:#888;">{source}{also_txt}</div>
              <div style="font-size:16px;font-weight:600;margin:2px 0 2px 0;">{title}</div>
              {subtitle}
              <div style="font-size:14px;">{summary}</div>
              {flag}
              <div style="margin-top:8px;"><a href="{link}"
                 style="font-size:13px;color:#1a56db;text-decoration:none;">Fonte &rarr;</a></div>
            </div>""")

    if signals:
        parts.append('<h2 style="font-size:15px;text-transform:uppercase;'
                     'letter-spacing:.5px;color:#444;margin-top:28px;">'
                     'Segnalazioni (dirittobancario)</h2>'
                     '<div style="font-size:12px;color:#888;margin-bottom:8px;">'
                     'Solo segnalazione, non sintetizzate.</div><ul style="padding-left:18px;">')
        for it in signals:
            title = html.escape(it.get("title", ""))
            link = html.escape(it.get("link", ""))
            parts.append(f'<li style="margin-bottom:6px;font-size:14px;">'
                         f'<a href="{link}" style="color:#1a56db;text-decoration:none;">{title}</a></li>')
        parts.append("</ul>")

    parts.append('<div style="color:#aaa;font-size:11px;margin-top:30px;">'
                 'Generato automaticamente. Le sintesi sono uno strumento di triage: '
                 'fa fede il testo della fonte.</div></div>')
    return "\n".join(parts)


def render_text(digest: dict) -> str:
    primary = digest.get("primary", [])
    signals = digest.get("signals", [])
    lines = [f"DIGEST NORMATIVO - {digest.get('generated_at', _today())}",
             f"{len(primary)} atti, {len(signals)} segnalazioni", ""]
    if not primary and not signals:
        lines.append("Nessuna novità normativa per oggi. "
                     "Controllo automatico eseguito regolarmente: il sistema funziona.")
    for it in primary:
        lines.append(f"[{it.get('source_name','')}] {_clean_title(it.get('title',''))}")
        date_it = _format_date_it(it.get("published"))
        if date_it:
            lines.append(date_it)
        lines.append(_strip_md(it.get("summary", "")))
        lines.append(f"Fonte: {it.get('link','')}")
        lines.append("")
    if signals:
        lines.append("--- SEGNALAZIONI (dirittobancario) ---")
        for it in signals:
            lines.append(f"- {it.get('title','')}: {it.get('link','')}")
    return "\n".join(lines)


# --- Invio -------------------------------------------------------------------
def send_gmail(user: str, app_password: str, recipients: list[str], subject: str,
               text_body: str, html_body: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as server:
        server.login(user, app_password)
        server.sendmail(user, recipients, msg.as_string())


def run() -> int:
    path = _latest_summarized()
    if not path:
        print("ERRORE: nessun digest_summarized_*.json. Esegui prima summarize.py.")
        return 1

    digest = json.loads(path.read_text(encoding="utf-8"))
    primary = digest.get("primary", [])
    signals = digest.get("signals", [])

    html_body = render_html(digest)
    text_body = render_text(digest)

    # anteprima sempre salvata (finisce nell'artifact)
    preview = DATA_DIR / f"email_{_today()}.html"
    preview.write_text(html_body, encoding="utf-8")
    print(f"Anteprima salvata: {preview.relative_to(ROOT)}")

    if not primary and not signals:
        print("Nessuna novita' oggi: invio email di conferma (nessuna novita').")

    user = os.environ.get("GMAIL_USER", "").strip()
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    to_raw = os.environ.get("DIGEST_TO", "").strip() or user
    recipients = [a.strip() for a in to_raw.split(",") if a.strip()]
    if not user or not app_password:
        print("ATTENZIONE: GMAIL_USER / GMAIL_APP_PASSWORD non impostati: "
              "email NON inviata (anteprima comunque salvata).")
        return 0

    if primary or signals:
        subject = f"Digest normativo - {_today()} ({len(primary)} atti)"
    else:
        subject = f"Digest normativo - {_today()} - nessuna novità"
    try:
        send_gmail(user, app_password, recipients, subject, text_body, html_body)
        print(f"Email inviata a {', '.join(recipients)}.")
    except Exception as ex:
        print(f"ERRORE invio email: {ex}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run())
