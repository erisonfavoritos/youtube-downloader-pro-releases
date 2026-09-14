from __future__ import annotations

import json
import unicodedata
import urllib.parse
import urllib.request

from updater import MANIFEST_URL


DEFAULT_SUPPORT = {
    "email": "marcodacasco@yahoo.com.br",
    "pix_key": "873f69b8-d13f-46a9-85e4-50ad47de60a1",
    "pix_name": "ERESON SILVA LIMA",
    "pix_city": "CUIABA",
    "price": "29.90",
    "license_days": 365,
}


def _field(identifier: str, value: str) -> str:
    return f"{identifier}{len(value):02d}{value}"


def _clean(value: str, limit: int) -> str:
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().upper()
    return " ".join(text.split())[:limit]


def _crc16(text: str) -> str:
    crc = 0xFFFF
    for byte in text.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return f"{crc:04X}"


def pix_payload(settings: dict, txid: str) -> str:
    key = str(settings["pix_key"]).strip()
    merchant = _field("00", "BR.GOV.BCB.PIX") + _field("01", key)
    amount = f"{float(settings.get('price', 29.90)):.2f}"
    txid = "".join(ch for ch in txid.upper() if ch.isalnum())[:25] or "***"
    payload = (
        _field("00", "01") + _field("26", merchant) + _field("52", "0000") +
        _field("53", "986") + _field("54", amount) + _field("58", "BR") +
        _field("59", _clean(str(settings["pix_name"]), 25)) +
        _field("60", _clean(str(settings["pix_city"]), 15)) +
        _field("62", _field("05", txid)) + "6304"
    )
    return payload + _crc16(payload)


def fetch_support() -> dict:
    settings = dict(DEFAULT_SUPPORT)
    try:
        request = urllib.request.Request(
            f"{MANIFEST_URL}?support=1", headers={"User-Agent": "YouTubeDownloaderPRO-Support", "Cache-Control": "no-cache"}
        )
        with urllib.request.urlopen(request, timeout=8) as response:
            remote = json.load(response).get("support", {})
        settings.update({key: value for key, value in remote.items() if value not in (None, "")})
    except Exception:
        pass
    return settings


def mailto_url(settings: dict, machine_id: str, version: str) -> str:
    subject = f"Solicitação de licença - {machine_id}"
    body = (
        "Olá, gostaria de solicitar a ativação do YouTube Downloader PRO.\n\n"
        f"ID do computador: {machine_id}\nVersão: {version}\n"
        f"Valor do Pix: R$ {float(settings.get('price', 29.90)):.2f}\n\n"
        "Anexarei o comprovante do Pix a este e-mail."
    )
    return "mailto:" + urllib.parse.quote(str(settings["email"])) + "?" + urllib.parse.urlencode({"subject": subject, "body": body})
