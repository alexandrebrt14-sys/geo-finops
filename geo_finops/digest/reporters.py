"""Delivery do digest: snapshot em disco + envio via WhatsApp."""

from __future__ import annotations

import json
from pathlib import Path

from ..config import DEFAULT_WHATSAPP_OWNER, get_digests_dir, load_whatsapp_creds


def save_snapshot(d: dict) -> Path:
    """Persiste o digest em ``<config>/digests/<label>.json``."""
    out_dir = get_digests_dir()
    path = out_dir / f"{d['label']}.json"
    path.write_text(
        json.dumps(d, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def send_whatsapp(text: str, to: str = DEFAULT_WHATSAPP_OWNER) -> dict:
    """Envia ``text`` via Meta Graph API. Retorna dict com ``ok`` + detalhes.

    Credenciais resolvidas em ``config.load_whatsapp_creds`` (env vars
    ou .env candidates). Nao levanta excecao em falha: retorna
    ``{"ok": False, "error": str}``.
    """
    import httpx

    token, phone_id = load_whatsapp_creds()
    if not token or not phone_id:
        return {"ok": False, "error": "Credenciais WhatsApp ausentes"}

    url = f"https://graph.facebook.com/v22.0/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    try:
        resp = httpx.post(url, headers=headers, json=body, timeout=15)
        return {
            "ok": resp.status_code == 200,
            "status": resp.status_code,
            "body": resp.text[:300],
        }
    except httpx.RequestError as exc:
        return {"ok": False, "error": str(exc)}
