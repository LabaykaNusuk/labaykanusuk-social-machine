# Première publication Instagram — ordre exact

Le moteur est prêt en dry-run. Pour publier réellement, il faut uniquement effectuer les actions de compte que ChatGPT ne peut pas faire à votre place.

## Étape A — GitHub
1. Créer un repository GitHub nommé `labaykanusuk-social-machine`.
2. Pour la configuration la plus simple et gratuite, le mettre **Public**. Aucun token Instagram n'est stocké dans les fichiers : les secrets restent dans GitHub Actions Secrets.
3. Décompresser le ZIP du moteur et envoyer le contenu du dossier `labaykanusuk-social-machine/` à la racine du repository.
4. Dans Settings → Pages, choisir **Deploy from a branch**, branche `main`, dossier `/docs`.
5. GitHub Pages donnera une URL publique utilisée par Instagram pour récupérer JPG/MP4.

## Étape B — Meta / Instagram
1. Le compte Instagram doit être un compte professionnel. Pour les Stories via l'API avec Facebook Login, un compte Business est requis.
2. Créer/ouvrir une application Meta Developers et activer Instagram API.
3. Autoriser la publication de contenu et obtenir l'identifiant Instagram professionnel + un access token adapté.
4. Dans GitHub → Settings → Secrets and variables → Actions, créer :
   - `IG_USER_ID`
   - `IG_ACCESS_TOKEN`
   - `IG_API_VERSION`
5. Ne jamais écrire ces valeurs dans un fichier du dépôt.

## Étape C — Test
1. Lancer l'Action `Expose media on Pages` sur un JPG généré.
2. Vérifier l'URL publique du média.
3. Lancer `Instagram first publish` avec `live=NO` pour le dry-run.
4. Si tout est correct, relancer avec `live=YES`.

Après le premier succès, la planification des 7 créneaux quotidiens peut être activée.
