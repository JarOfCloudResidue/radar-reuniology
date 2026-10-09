"""Qualification des articles et repérage des événements par l'IA Claude.

Sans ANTHROPIC_API_KEY, tout est désactivé et le scan garde ses règles simples.
"""
import json
import os
import sys

MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-5-5")
_client = None
_warned = False


def enabled():
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic()
    return _client


CONTEXT = """Tu travailles pour Réuniologie (Reuniology à l'international), un cabinet fondé par Louis Vareille qui rend les réunions des équipes de direction (Comex, CODIR, réunions managériales) plus courtes et plus décisives.
Références clients : CACIB, BPCE, CNRS, Panzani, Metro.
Équipe : Louis Vareille (fondateur), Vincent Paulus (marketing, utilise cet outil), Philippe Paulus, Samar El Hachem, Olivier Wolff, Jo Williams.
Client idéal : DRH, DG, directeur transformation ou organisation d'une entreprise de 300 à 3 000 salariés avec plusieurs niveaux de management, qui vit une croissance, une transformation, une réorganisation, une fusion ou une internationalisation, ou qui parle publiquement de réunions, de management, de collaboration ou de travail hybride.
Offre d'appel : un diagnostic gratuit de 30 minutes des réunions de direction (outil Réunioscope)."""

JUDGE_SYSTEM = CONTEXT + """

On te donne un article de presse sur une entreprise suivie. Décide s'il ouvre une vraie occasion commerciale MAINTENANT.
- "go" : un événement qui change la vie de l'équipe de direction ou des managers (nouveau DG, DRH ou membre du Comex, fusion, rachat, réorganisation, plan stratégique, forte croissance, déménagement de siège, accord de travail hybride, plan de transformation) dans une entreprise du bon profil.
- "watch" : actualité utile pour ouvrir une conversation plus tard, sans urgence.
- "no" : bruit. Résultats financiers seuls, lancement de produit, sponsoring, fait divers, homonyme, article qui ne parle pas vraiment de l'entreprise, entreprise hors cible, nomination à un poste sans lien avec la direction.
Le score (0 à 100) mesure la chaleur de l'occasion. Sois exigeant : moins de 20 % des articles méritent "go".
Écris tout en français, phrases courtes et concrètes, vouvoiement dans le message.
target_name : la personne à contacter si elle est nommée dans l'article ou parmi les relations, sinon une chaîne vide. target_role : sa fonction, ou la fonction à viser.
path : par qui passer (une relation de l'équipe si elle existe, sinon approche directe), en une phrase.
plan : exactement 3 étapes concrètes, la première faisable cette semaine.
message : le premier message LinkedIn ou email à envoyer à la cible (60 à 110 mots), qui cite le déclencheur, explique en une phrase ce que Réuniologie apporte dans cette situation, et propose le diagnostic gratuit de 30 minutes. Pas de flatterie creuse, pas d'emoji.
label : 2 à 4 mots qui résument le déclencheur (ex. « Nouveau DG », « Fusion »).
why_now : une phrase qui explique pourquoi c'est le moment."""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["go", "watch", "no"]},
        "score": {"type": "integer"},
        "label": {"type": "string"},
        "why_now": {"type": "string"},
        "target_name": {"type": "string"},
        "target_role": {"type": "string"},
        "path": {"type": "string"},
        "plan": {"type": "array", "items": {"type": "string"}},
        "message": {"type": "string"},
    },
    "required": ["verdict", "score", "label", "why_now", "target_name", "target_role", "path", "plan", "message"],
    "additionalProperties": False,
}

EVENT_SYSTEM = CONTEXT + """

On te donne un article de presse. Dis s'il annonce un événement À VENIR en France (conférence, rencontre, atelier, club, salon, appel à intervenants) où se retrouvent des DRH, des dirigeants ou des managers, et où Réuniologie pourrait animer gratuitement un atelier de 45 minutes ou faciliter des tables rondes, ou au moins venir rencontrer des clients.
Si ce n'est pas le cas (événement passé, compte rendu, public hors cible, pas un événement), réponds is_event = false et laisse les autres champs vides.
date au format AAAA-MM-JJ si elle est connue, sinon vide. kind : reseau (association, club, réseau de 50 à 300 personnes), conference (avec programme d'intervenants), salon (grand salon). Écris en français."""

EVENT_SCHEMA = {
    "type": "object",
    "properties": {
        "is_event": {"type": "boolean"},
        "name": {"type": "string"},
        "organizer": {"type": "string"},
        "kind": {"type": "string", "enum": ["reseau", "conference", "salon", ""]},
        "date": {"type": "string"},
        "date_text": {"type": "string"},
        "city": {"type": "string"},
        "audience": {"type": "string"},
        "angle": {"type": "string"},
        "contact_hint": {"type": "string"},
    },
    "required": ["is_event", "name", "organizer", "kind", "date", "date_text", "city", "audience", "angle", "contact_hint"],
    "additionalProperties": False,
}


def _ask(system, prompt, schema):
    import anthropic
    try:
        r = client().messages.create(
            model=MODEL, max_tokens=4000, system=system,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIStatusError as e:
        global _warned
        if not _warned:
            _warned = True
            if e.status_code in (401, 403):
                print("::error::Clé ANTHROPIC_API_KEY refusée : vérifier le secret GitHub.")
            else:
                print(f"::warning::L'IA a répondu {e.status_code} ({MODEL}) : {str(e)[:200]}")
        return None
    except anthropic.APIConnectionError as e:
        print("IA injoignable :", e, file=sys.stderr)
        return None
    if r.stop_reason != "end_turn":
        return None
    text = next((b.text for b in r.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def judge(sig, account, relations, feedback):
    if not enabled():
        return None
    rel = "\n".join(f"- {r['member']} connaît {r['name']} ({r['position']})" for r in relations[:8]) or "aucune"
    fb = "\n".join(f"- « {f.get('title', '')} » ({f.get('company', '')}) : {f.get('feedback_reason') or 'sans raison donnée'}" for f in feedback) or "aucun"
    if sig.get("kind") == "alumni":
        intro = (f"Cet article parle de {sig.get('person')}, un ancien contact client de Réuniologie "
                 f"(mission chez {sig.get('previous_company') or 'un client'}). S'il ou elle prend un nouveau poste de direction, c'est une occasion très chaude.")
    else:
        intro = (f"Entreprise suivie : {account.get('name') or sig.get('company')} ; type : {account.get('kind', 'cible')} ; "
                 f"secteur : {account.get('sector', '?')} ; effectif : {account.get('size', '?')} ; ville : {account.get('city', '?')} ; "
                 f"ressemble à : {account.get('lookalike', '')} ; notes : {account.get('why', '')}")
    prompt = f"""{intro}

Article ({sig.get('date')}, {sig.get('source', '')}) :
Titre : {sig.get('title')}
Extrait : {sig.get('summary', '')}

Relations de l'équipe dans cette entreprise :
{rel}

Articles que Vincent a jugés non pertinents (à ne pas reproduire) :
{fb}"""
    out = _ask(JUDGE_SYSTEM, prompt, JUDGE_SCHEMA)
    if not out:
        return None
    out["score"] = max(0, min(100, int(out.get("score") or 0)))
    out["plan"] = [p for p in out.get("plan", []) if p][:3]
    out["model"] = MODEL
    return out


def event(item):
    if not enabled():
        return None
    out = _ask(EVENT_SYSTEM, f"Titre : {item['title']}\nSource : {item['source']} ({item['date']:%Y-%m-%d})\nExtrait : {item.get('summary', '')}\nLien : {item['url']}", EVENT_SCHEMA)
    if not out or not out.get("is_event") or not out.get("name"):
        return None
    if out.get("date") and out["date"] < f"{item['date']:%Y-%m-%d}":
        return None
    out.pop("is_event", None)
    out["url"] = item["url"]
    out["source"] = item["source"]
    return out
