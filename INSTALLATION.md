# LABAYKANUSUK HAJJ 2027 | installation isolée

## Contenu
274 questions et réponses extraites du PDF fourni, gabarit premium V2, renderer Playwright, historique anti-doublon, workflow indépendant.

## Installer en une fois sur Mac
1. Sauvegarder le dépôt GitHub actuel (ZIP ou branche de sauvegarde).
2. Décompresser ce ZIP **dans le dossier racine du dépôt cloné**, en conservant les chemins. Ne jamais supprimer les fichiers existants.
3. Vérifier les 5 nouveaux fichiers : `hajj2027/posts.json`, `hajj2027/template.html`, `hajj2027/history.json`, `src/hajj2027_daily.py`, `.github/workflows/hajj2027-posts.yml`.
4. Depuis le dossier du dépôt : `git add hajj2027 src/hajj2027_daily.py .github/workflows/hajj2027-posts.yml && git commit -m "Add isolated Hajj 2027 posts library" && git push`.
5. GitHub > Actions > `LABAYKANUSUK Hajj 2027 Posts (isolated)` > Run workflow > **DRY**. Télécharger `hajj2027-preview` et contrôler l'image.

## Activation
Le premier test ne publie rien. Le LIVE manuel exige une nouvelle variable GitHub Actions `HAJJ2027_LIVE_ENABLED=true`, et les secrets Instagram existants. Il publie **réellement** un post. La programmation quotidienne n'est pas activée, car son heure n'a pas encore été décidée. Elle sera configurée après validation, sans toucher aux autres workflows.

## Sécurité
- Ne jamais relancer LIVE après un échec sans vérifier le compte Instagram et `hajj2027/history.json`.
- Un `pending` bloque toute nouvelle publication tant que l'état n'est pas vérifié.
- Le dépôt doit être public pour servir les images depuis raw.githubusercontent.com.
- Les contenus relatifs aux règles 2027 exigent une vérification officielle avant publication.
- Ce module crée des **posts image 4:5**, pas des Reels vidéo, même si le PDF initial appelle ses sujets des Reels.
- Aucun script ni workflow préexistant n'est modifié par ce ZIP.
