"""Scan quotidien du Radar : lit la presse récente (Google News) pour chaque compte
et ajoute les nouveaux signaux dans la base Supabase. Aucune donnée LinkedIn n'est lue.

Variables d'environnement : SUPABASE_URL, SUPABASE_SERVICE_KEY, et en option DAYS (défaut 3),
ANTHROPIC_API_KEY (qualification par l'IA), CLAUDE_MODEL (défaut claude-haiku-5-5) et BACKFILL
(nombre d'articles déjà en base à faire qualifier par l'IA, défaut 0).
"""
import hashlib, html, json, os, re, sys, time, unicodedata
import urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

def fail(msg):
    print("::error::" + msg)  # visible dans le résumé du run GitHub
    sys.exit(1)


URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
if not URL or not KEY:
    fail("Secrets manquants : créer SUPABASE_URL et SUPABASE_SERVICE_KEY dans GitHub › Settings › Secrets and variables › Actions.")
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
    if KEY.startswith("eyJ"):  # ancienne clé service_role (JWT) ; les clés sb_secret_ passent par apikey seul
        req.add_header("Authorization", "Bearer " + KEY)
    req.add_header("Content-Type", "application/json")
    if prefer:
        req.add_header("Prefer", prefer)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        fail(f"Supabase a refusé {method} {path.split('?')[0]} : {e.code} {e.read()[:300]!r}")
    return json.loads(raw) if raw else None


def docs(collection):
    out, start = [], 0
    while True:
        rows = api("GET", f"docs?select=id,data&collection=eq.{collection}&limit=1000&offset={start}")
        out += rows
        if len(rows) < 1000:
            return out
        start += 1000


def upsert(rows, merge=False):
    for k in range(0, len(rows), 200):
        api("POST", "docs?on_conflict=collection,id", rows[k:k + 200],
            prefer="resolution=merge-duplicates" if merge else "resolution=ignore-duplicates")


def news(name, days=None, raw=False):
    q = urllib.parse.quote(name if raw else f'"{name}" when:{days or DAYS}d')
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
        desc = re.sub(r"<[^>]+>", " ", html.unescape(it.findtext("description") or ""))
        yield {"title": title.strip(), "url": it.findtext("link") or "", "date": d.astimezone(timezone.utc),
               "summary": re.sub(r"\s+", " ", desc).strip()[:400],
               "source": source or "", "source_url": src.get("url") if src is not None else ""}


CAT = {"dirigeant": "dirigeant", "fusion": "fusion", "reorg": "reorg", "plan": "plan", "travail": "travail"}
NOMINATION = re.compile(r"\b(nomme|nommee|nomination|rejoint|prend la (tete|direction)|devient|succede|arrive)\b")
EVENT_QUERIES = [
    '"ANDRH" (conférence OR rencontre OR atelier) when:14d',
    '"club des DRH" OR "cercle des DRH" when:14d',
    '"appel à intervenants" (management OR RH OR organisation OR agile) when:30d',
    '"rencontres RH" OR "université RH" OR "matinale RH" when:14d',
    '(APM OR CJD OR "MEDEF") rencontre dirigeants management when:14d',
]


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", norm(s)).strip("-")[:50]


def relations_by_account(accounts):
    """Relations de l'équipe rangées par compte (même règle de rapprochement que la page, en plus simple)."""
    names = {a["id"]: norm(a["data"].get("name", "")) for a in accounts}
    out = {}
    for chunk in docs("relations"):
        member = chunk["data"].get("member", "")
        for r in chunk["data"].get("rows", []):
            comp = norm(r[3] if len(r) > 3 else "")
            if not comp:
                continue
            for aid, n in names.items():
                if len(n) >= 3 and (comp == n or re.search(r"(^| )" + re.escape(n) + r"( |$)", comp)):
                    out.setdefault(aid, []).append({"member": member, "name": f"{r[0]} {r[1]}".strip(),
                                                    "position": r[4] if len(r) > 4 else ""})
                    break
    return out


def main():
    import qualify
    since = datetime.now(timezone.utc) - timedelta(days=DAYS)
    all_accounts = docs("accounts")
    accounts = [a for a in all_accounts if (a["data"].get("status") or "") != "ecarte"]
    old = docs("signals")
    seen = {norm(s["data"].get("title", ""))[:90] for s in old}
    new, errors = [], 0

    # 1. La presse des entreprises suivies
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
            if not rule and not qualify.enabled():
                continue
            seen.add(t[:90])
            why, action = TEXT[rule[2]] if rule else ("", "")
            sid = "a" + hashlib.sha1(it["title"].encode()).hexdigest()[:12]
            new.append({"collection": "signals", "id": sid, "data": {
                "company": name, "account_id": a["id"], "title": it["title"], "summary": it["summary"],
                "url": it["url"], "source": it["source"], "source_url": it["source_url"],
                "date": it["date"].strftime("%Y-%m-%d"), "strength": rule[1] if rule else "faible",
                "category": rule[2] if rule else "", "why": why, "action": action,
                "status": "new", "owner": "", "auto": True}})
        time.sleep(1)

    # 2. Les anciens contacts clients qui changent de poste
    for p in docs("alumni"):
        person, before = p["data"].get("name", ""), p["data"].get("company", "")
        if not person:
            continue
        try:
            items = list(news(person, days=max(DAYS, 7)))
        except Exception as e:
            print("presse indisponible pour", person, "-", e, file=sys.stderr)
            continue
        for it in items:
            t = norm(it["title"])
            if norm(person).split()[-1] not in t or not NOMINATION.search(t) or t[:90] in seen:
                continue
            seen.add(t[:90])
            sid = "p" + hashlib.sha1(it["title"].encode()).hexdigest()[:12]
            new.append({"collection": "signals", "id": sid, "data": {
                "kind": "alumni", "person": person, "previous_company": before, "company": before or person,
                "title": it["title"], "summary": it["summary"], "url": it["url"], "source": it["source"],
                "source_url": it["source_url"], "date": it["date"].strftime("%Y-%m-%d"), "strength": "fort",
                "category": "dirigeant", "status": "new", "owner": "", "auto": True}})
        time.sleep(1)

    # 3. Qualification par l'IA (nouveaux articles, et en option ceux déjà en base)
    acc_by_id = {a["id"]: a["data"] for a in all_accounts}
    acc_by_name = {norm(a["data"].get("name", "")): a for a in all_accounts}
    rels = relations_by_account(all_accounts) if qualify.enabled() else {}
    feedback = [s["data"] for s in old if s["data"].get("feedback") == "no"][-15:]
    todo = list(new)
    backfill = int(os.environ.get("BACKFILL", "0") or 0)
    if backfill and qualify.enabled():
        recent = (datetime.now(timezone.utc) - timedelta(days=180)).strftime("%Y-%m-%d")
        old_todo = [{"collection": "signals", "id": s["id"], "data": s["data"]} for s in old
                    if not (s["data"].get("ai") or {}).get("verdict") and s["data"].get("status") != "dropped"
                    and str(s["data"].get("date", "")) >= recent]
        old_todo.sort(key=lambda r: str(r["data"].get("date", "")), reverse=True)
        todo += old_todo[:backfill]
    judged, started = 0, time.time()
    budget = float(os.environ.get("AI_MINUTES", "30")) * 60
    for n, row in enumerate(todo):
        if time.time() - started > budget:
            print(f"::warning::Temps d'IA écoulé : {len(todo) - n} articles restent à qualifier au prochain passage.")
            break
        # Sauvegarde au fil de l'eau, pour ne rien perdre si le run s'arrête
        if n and n % 40 == 0:
            done = todo[:n]
            upsert([r for r in done if r in new and (r["data"].get("category") or r["data"].get("ai") or r["data"].get("kind") == "alumni")])
            upsert([r for r in done if r not in new and r["data"].get("ai")], merge=True)
        d = row["data"]
        acc = acc_by_id.get(d.get("account_id")) or (acc_by_name.get(norm(d.get("company", ""))) or {}).get("data") or {}
        aid = d.get("account_id") or (acc_by_name.get(norm(d.get("company", ""))) or {}).get("id")
        verdict = qualify.judge(d, acc, rels.get(aid, []), feedback)
        if verdict:
            d["ai"] = verdict
            judged += 1
    # On ne garde que ce que les règles ont reconnu ou que l'IA a jugé (si elle n'a pas répondu, le bruit est écarté)
    new = [r for r in new if r["data"].get("category") or r["data"].get("ai") or r["data"].get("kind") == "alumni"]
    if new:
        upsert(new)
    old_rows = [r for r in todo if r not in new and r["data"].get("ai")]
    if old_rows:
        upsert(old_rows, merge=True)

    # 4. Événements : la liste de départ, puis ceux repérés dans la presse
    events = docs("events")
    seed_path = os.path.join(os.path.dirname(__file__), "events_seed.json")
    if os.path.exists(seed_path):
        seed = json.load(open(seed_path, encoding="utf-8"))
        upsert([{"collection": "events", "id": e["id"], "data": {k: v for k, v in e.items() if k != "id"} | {"status": "a_contacter", "source_kind": "liste"}}
                for e in seed])
    found = 0
    if qualify.enabled():
        known = {slug(e["data"].get("name", "")) for e in events}
        for q in EVENT_QUERIES:
            try:
                items = list(news(q, raw=True))
            except Exception as e:
                print("presse indisponible pour", q, "-", e, file=sys.stderr)
                continue
            for it in items[:15]:
                ev = qualify.event(it)
                if not ev or slug(ev["name"]) in known:
                    continue
                known.add(slug(ev["name"]))
                upsert([{"collection": "events", "id": slug(ev["name"]) + "-" + (ev.get("date") or "x"),
                         "data": ev | {"status": "a_contacter", "source_kind": "presse", "added": datetime.now(timezone.utc).strftime("%Y-%m-%d")}}])
                found += 1
            time.sleep(1)

    print(f"::notice::{len(accounts)} comptes lus, {len(new)} nouveaux articles, {judged} qualifiés par l'IA, "
          f"{found} événements repérés, {errors} erreurs de presse.")
    if errors > len(accounts) / 2:
        fail("Trop d'erreurs : Google News a peut-être bloqué les requêtes.")


if __name__ == "__main__":
    main()
