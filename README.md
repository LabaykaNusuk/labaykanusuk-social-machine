# LABAYKANUSUK Social Machine

Socle v0.1 — moteur de publication social sans Zapier/Make.

## Objectif
7 sorties quotidiennes:
1. Story religion
2. Story outil
3. Story religion
4. Story religion
5. Story outil
6. Story religion
7. Feed permanent (religion ou outil)

## Sécurité éditoriale
- Liste blanche de sources: `config/sources.json`
- Politique stricte: `config/content_policy.json`
- Rien n'est publiable sans `approved: true`
- Phase 1 hadith: Sahih al-Bukhari / Sahih Muslim uniquement
- Les textes éditoriaux ne sont jamais présentés comme des versets ou hadiths
- Les règles actuelles (visa, permis, accès, etc.) nécessitent une source officielle datée

## Couleurs
Le vert LABAYKANUSUK ajouté au système est `#2E6B55`.
L'or reste la couleur d'emphase spirituelle/premium.
Le vert est réservé aux actions, outils et CTA.

## Test local
```bash
pip install -r requirements.txt
playwright install chromium
python src/validate.py
python src/render.py --template story-religion.html --data config/demo-story-religion.json --output demo.png
```

## État actuel
- [x] Charte de base codée HTML/CSS
- [x] 4 templates: story religion, story outil, feed religion, feed outil
- [x] Whitelist de sources
- [x] Validateur de contenu
- [x] Premier contenu Coran sourcé
- [x] Premier hadith référencé mais volontairement non approuvé côté traduction FR
- [x] Premiers outils LABAYKANUSUK
- [x] GitHub Action de rendu manuel
- [ ] Banque photo définitive
- [ ] Horaires des 7 créneaux
- [ ] Moteur anti-répétition
- [ ] Queue quotidienne
- [ ] Connexion Instagram
- [ ] Connexion TikTok
- [ ] Publication automatique


## Nouvelles briques v0.2
- [x] Bibliothèque outils enrichie
- [x] Bibliothèque de rappels éditoriaux
- [x] Moteur de sélection quotidien
- [x] Historique anti-répétition 90 jours
- [x] Génération automatique de la file des 7 publications
- [x] Rendu en lot de toute la journée
- [x] Workflow GitHub de dry-run quotidien

## Fichiers clés ajoutés
- `src/selector.py` → fabrique la file quotidienne
- `src/render_queue.py` → rend toute la file en images
- `logs/publication_history.json` → mémoire anti-répétition
- `output/queue_YYYY-MM-DD.json` → file du jour

## v0.3 — Design social adaptatif
- [x] 20 fonds utilisateur intégrés dans `assets/photos/approved/`
- [x] Manifest photo avec tags thématiques
- [x] Sélection automatique du fond selon le contenu
- [x] Safe zones conservatrices Story/Reel et Feed
- [x] Texte centré verticalement dans la surface utile
- [x] Typographie adaptative : short / medium / long / xlong
- [x] Overlay cinématique violet/or ou vert pour les outils
- [x] CTA protégé des zones d’interface sociales
- [x] Mode debug avec contour de la safe zone pour contrôle visuel

Les marges utilisées sont documentées dans `config/safe_zones.json`.


## V0.4 — logos officiels + Reel + publication Instagram
- [x] 3 sigles officiels stockés tels quels (aucune déformation/recolorisation)
- [x] Sélection automatique du logo selon le fond
- [x] Logo ancré plus loin des bords, dans la safe zone
- [x] Typographie agrandie selon le design validé
- [x] Sortie JPEG pour compatibilité publication
- [x] Générateur Reel MP4 H.264 1080×1920 (zoom cinématique léger, silencieux)
- [x] Module Instagram IMAGE / STORY / REEL en dry-run
- [x] Workflow GitHub pour exposer les médias dans un dépôt public d’assets
- [ ] Créer le dépôt GitHub privé du moteur
- [ ] Créer le dépôt GitHub public d’assets médias
- [ ] Ajouter les secrets Meta/GitHub
- [ ] Premier test Instagram réel

### Secrets jamais committés
`IG_USER_ID`, `IG_ACCESS_TOKEN`, `IG_API_VERSION`, `PUBLIC_MEDIA_REPO`, `PUBLIC_MEDIA_REPO_TOKEN`


## V0.4.1 — publication publique simplifiée
- [x] GitHub Pages (`docs/media`) comme hébergement public gratuit des JPG/MP4
- [x] Workflow `Expose media on Pages`
- [x] Workflow manuel `Instagram first publish` avec sécurité `live=NO/YES`
- [x] `.env.example` sans secret
- [x] Guide `SETUP_FIRST_PUBLICATION.md`


## v0.4.2 Web Upload
Fonds optimises en JPG pour permettre un upload simple via GitHub Web. Les masters et reels sont regeneres par les workflows et ne sont pas stockes dans le depot.
