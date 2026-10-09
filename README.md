# Radar Reuniology

Le Radar de l'équipe Reuniology, hors de Claude : signaux d'opportunité, comptes, suivi des contacts et relations de l'équipe.
Gratuit : site sur GitHub Pages, base de données Supabase, scan de la presse chaque matin par GitHub Actions.
Aucune donnée LinkedIn n'est lue automatiquement : les liens LinkedIn ouvrent des recherches que chacun fait depuis son compte.

## Contenu du dépôt

- `site/` : la page (index.html), la connexion à Supabase (shim.js) et la configuration publique (config.js).
- `supabase/schema.sql` : la base (tables, règles d'accès, mises à jour en direct).
- `scanner/scan.py` : le scan quotidien de la presse pour chaque compte du Radar.
- `.github/workflows/` : publication du site et scan du lundi au vendredi à 7 h.
- `scripts/import_export.py` : import en ligne de commande d'une sauvegarde (facultatif, le site sait aussi le faire).

Aucune donnée n'est stockée dans ce dépôt. Les exports et fichiers de relations (`*.json`, `*.csv`) sont exclus par `.gitignore`.

## Mise en place (une fois)

1. **Supabase** : créer un projet (région Europe), puis SQL Editor › coller `supabase/schema.sql` › Run.
   Ajouter l'équipe avec la requête en bas du fichier (un email et un prénom par personne).
2. **Supabase › Authentication › URL Configuration** : mettre l'adresse du site (`https://<compte>.github.io/radar-reuniology/`) dans « Site URL » et « Redirect URLs ».
3. **Supabase › Project Settings › API** : copier « Project URL » et la clé `anon` dans `site/config.js`.
4. **GitHub › Settings › Pages** : Source = GitHub Actions.
5. **GitHub › Settings › Secrets and variables › Actions** : créer `SUPABASE_URL` et `SUPABASE_SERVICE_KEY` (clé `service_role`, à ne jamais partager ni écrire dans le code).
6. Ouvrir le site, se connecter par email, puis « Importer une sauvegarde » avec le fichier d'export du Radar.

## Au quotidien

- Chaque membre se connecte avec son email : il reçoit un lien, sans mot de passe.
- Le scan ajoute les nouveaux signaux chaque matin. On peut le lancer à la main : Actions › Scan quotidien › Run workflow.
- Ajouter une personne : une ligne dans la table `members` de Supabase.
