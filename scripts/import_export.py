"""Charge dans Supabase les données exportées de la page Claude (une seule fois).
Usage : SUPABASE_URL=... SUPABASE_SERVICE_KEY=... python scripts/import_export.py export.json
Le fichier d'export contient des données personnelles : ne le mettez jamais dans le dépôt.
"""
import json, os, sys, urllib.request

URL = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_KEY"]
data = json.load(open(sys.argv[1], encoding="utf-8"))
rows = [{"collection": c, "id": i, "data": d} for c, docs in data.items() for i, d in docs.items()]
for k in range(0, len(rows), 50):
    req = urllib.request.Request(URL + "/rest/v1/docs?on_conflict=collection,id", method="POST",
                                 data=json.dumps(rows[k:k + 50]).encode())
    for h, v in {"apikey": KEY, "Authorization": "Bearer " + KEY, "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates"}.items():
        req.add_header(h, v)
    urllib.request.urlopen(req, timeout=120).read()
print(len(rows), "documents importés :", {c: len(d) for c, d in data.items()})
