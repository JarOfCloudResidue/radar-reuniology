"""Scan quotidien du Radar : lit la presse récente (Google News) pour chaque compte
et ajoute les nouveaux signaux dans la base Supabase. Aucune donnée LinkedIn n'est lue.

Variables d'environnement : SUPABASE_URL, SUPABASE_SERVICE_KEY, et en option DAYS (défaut 3).
"""
import hashlib, html, json, os, re, sys, time, unicodedata
import urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

URL = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_KEY"]
DAYS = int(os.environ.get("DAYS", "3"))
UA = "Mozilla/5.0 (RadarReuniology; veille presse)"

# (motif, force, catégorie). Le premier motif qui correspond l'emporte.
RULES = [
    (r"\b(nomm[ée]e?s?|nomination|prend la (tete|direction|presidence)|devient (directeur|directrice|president|pdg|dg)|succede)\b.*\b(directeur general|directrice generale|dg|pdg|ceo|drh|ressources humaines|comex|comite executif|directoire|president)", "fort", "dirigeant"),
    (r"\b(directeur general|directrice generale|dg|pdg|ceo|drh)\b.*\b(nomm[ée]e?|nomination|arrive|rejoint)\b", "fort", "dirigeant"),
    (r"\b(fusion|rapprochement|rachat|rachete|acquisition|acquiert|reprise|reprend|opa)\b", "fort", "fusion"),
    (r"\b(reorganisation|restructuration|pse|plan social|plan de transformation|transformation)\b", "moyen", "reorg"),
    (r"\b(plan strategique|feuille de route|plan 20[23]\d)\b", "moyen", "plan"),
    (r"\b(nouveau siege|demenage|flex office|teletravail|travail hybride|accord d'entreprise|qvct|qualite de vie au travail)\b", "moyen", "travail"),
    (r"\b(comex|comite executif|comite de direction|codir)\b", "moyen", "dirigeant"),
]
TEXT = {
    "dirigeant": ("Changement à la tête de l'entreprise : un nouveau dirigeant revoit souvent ses instances.", "Féliciter la personne nommée et proposer un check-up du CODIR à 100 jours."),
    "fusion": ("Fusion ou rachat : deux organisations doivent apprendre à se réunir ensemble.", "Proposer un format de réunions d'intégration et des règles du jeu communes."),
    "reorg": ("Réorganisation en cours : les réunions managériales sont sous tension.", "Proposer un accompagnement des réunions d'équipe pendant la transformation."),
    "plan": ("Nouveau plan stratégique : de nouveaux rituels de pilotage à installer.", "Proposer un accompagnement des rituels de pilotage du plan."),
    "travail": ("Changement des modes de travail (siège, hybride, accord) : les usages de réunion bougent.", "Proposer un accompagnement des usages de réunion dans la nouvelle organisation."),
}


def norm(s):
    s = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower()


def api(method, path, body=None, prefer=None):
    req = urllib.request.Request(URL + "/rest/v1/" + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("apikey", KEY)
    req.add_header("Authorization", "Bearer " + KEY)
    req.add_header("Content-Type", "application/json")
    if prefer:
        req.add_header("Prefer", prefer)
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def docs(collection):
    out, start = [], 0
    while True:
        rows = api("GET", f"docs?select=id,data&collection=eq.{collection}&limit=1000&offset={start}")
        out += rows
        if len(rows) < 1000:
            return out
        start += 1000


def news(name):
    q = urllib.parse.quote(f'"{name}" when:{DAYS}d')
    url = f"https://news.google.com/rss/search?q={q}&hl=fr&gl=FR&ceid=FR:fr"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        root = ET.fromstring(r.read())
    for it in root.iter("item"):
        title = html.unescape(it.findtext("title") or "")
        src = it.find("source")
        source = src.text if src is not None else ""
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3]
        try:
            d = parsedate_to_datetime(it.findtext("pubDate"))
        except Exception:
            continue
        yield {"title": title.strip(), "url": it.findtext("link") or "", "date": d.astimezone(timezone.utc),
               "source": source or "", "source_url": src.get("url") if src is not None else ""}


def main():
    since = datetime.now(timezone.utc) - timedelta(days=DAYS)
    accounts = [a for a in docs("accounts") if (a["data"].get("status") or "") != "ecarte"]
    seen = {norm(s["data"].get("title", ""))[:90] for s in docs("signals")}
    new, errors = [], 0
    for a in accounts:
        name = a["data"].get("name", "")
        if not name:
            continue
        try:
            items = list(news(name))
        except Exception as e:
            errors += 1
            print("presse indisponible pour", name, "-", e, file=sys.stderr)
            continue
        first = norm(name).split()[0]
        for it in items:
            t = norm(it["title"])
            if it["date"] < since or first not in t or t[:90] in seen:
                continue
            rule = next((r for r in RULES if re.search(r[0], t)), None)
            if not rule:
                continue
            seen.add(t[:90])
            why, action = TEXT[rule[2]]
            sid = "a" + hashlib.sha1(it["title"].encode()).hexdigest()[:12]
            new.append({"collection": "signals", "id": sid, "data": {
                "company": name, "title": it["title"], "url": it["url"], "source": it["source"],
                "source_url": it["source_url"], "date": it["date"].strftime("%Y-%m-%d"),
                "strength": rule[1], "why": why, "action": action, "status": "new", "owner": "", "auto": True}})
        time.sleep(1)
    if new:
        api("POST", "docs?on_conflict=collection,id", new, prefer="resolution=ignore-duplicates")
    print(f"{len(accounts)} comptes lus, {len(new)} nouveaux signaux, {errors} erreurs de presse.")
    if errors > len(accounts) / 2:
        sys.exit("Trop d'erreurs : Google News a peut-être bloqué les requêtes.")


if __name__ == "__main__":
    main()
