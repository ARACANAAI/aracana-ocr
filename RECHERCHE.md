# Fondation scientifique du framework

Revue de littérature, état de l'art, analyse, et plan. Écrit **avant**
l'implémentation, pour que l'architecture découle de la thèse et non l'inverse.

> Statut : étapes 1 à 4 terminées. Ce document est la référence dont dépend
> tout le code du paquet `aracana`. Chaque abstraction du framework y renvoie.

---

# Étape 1 — Revue de littérature

## 1.1 Prédiction sélective : le cadre formel

Le cadre est posé par **Geifman & El-Yaniv (NeurIPS 2017)** puis étendu par
**SelectiveNet (ICML 2019)**. Un prédicteur sélectif est un couple `(f, g)` :
`f` prédit, `g` décide de répondre ou de s'abstenir. Deux grandeurs le
caractérisent :

- **couverture** `c` — la fraction d'entrées sur lesquelles le système s'engage ;
- **risque sélectif** `r` — la perte moyenne sur ces seules entrées.

Faire varier le seuil de `g` trace la **courbe risque–couverture** ; son aire,
l'**AURC**, résume le système d'un chiffre.

**Traub et al. (NeurIPS 2024)** montrent que l'évaluation de la classification
sélective est truffée de pièges méthodologiques — notamment la comparaison de
systèmes dont les prédicteurs `f` diffèrent, qui confond l'apport de `g` avec
celui de `f`. **Cattelan & Silva** montrent que les garanties se dégradent
sous décalage de distribution.

*Ce que nous en retenons :* le vocabulaire (couverture, risque, AURC) et
l'avertissement méthodologique — pour isoler l'apport des vérificateurs, il
faut **fixer l'analyseur et ne faire varier que la politique d'abstention**.

## 1.2 Prédiction conforme appliquée aux documents

L'article le plus proche de nous : **« Beyond Accuracy: Understanding Model
Confidence in Key Information Extraction with Conformal Prediction »**, *IJDAR*
2026. Il applique la prédiction conforme fractionnée à l'extraction de champs
sur des reçus : après ajustement d'un transformeur multimodal, un jeu de
calibration produit des scores de non-conformité et des ensembles de
prédiction par entité, à taux d'erreur choisi. Résultats : **couverture
marginale de 98,3 % à α = 0,02, 70 % de prédictions singleton**, les champs
très structurés (dates, prix) donnant de petits ensembles et les champs rares
de grands.

Complémentaire : **quantification d'incertitude pour la reconnaissance
d'entités nommées par prédiction conforme de séquence** (arXiv 2601.16999).

*Ce que nous en retenons :* c'est l'état de l'art de la fiabilité en
extraction documentaire, et il présente trois traits que notre approche n'a
pas :
1. le score de non-conformité **vient du modèle** (logits, softmax) ;
2. il exige un **jeu de calibration étiqueté**, donc du travail humain avant
   tout déploiement ;
3. la sortie est un **ensemble de prédictions**, difficile à consommer par un
   système comptable qui attend un montant, pas trois candidats.

## 1.3 Auto-vérification par le modèle, et ses limites

Un courant entier cherche le signal d'abstention à l'intérieur du modèle :
**auto-vérification par le même modèle** (arXiv 2605.02915), **délibération
prouveur–vérificateur** (arXiv 2605.25133), désaccord inter-modèles
(arXiv 2604.17112).

Deux résultats limitent explicitement ce courant :

- **« Entropy Alone is Insufficient for Safe Selective Prediction in LLMs »**
  (arXiv 2603.21172) : les signaux d'incertitude internes ne suffisent pas.
- Les scores indirects utilisés en boucle **se découplent de la qualité
  réelle** par détournement de récompense.

Sur notre domaine précisément, **Guan et al., « Teaching VLMs to Admit
Uncertainty in OCR », ICLR 2026** entraîne un modèle vision-langage à baliser
lui-même les passages qu'il juge incertains, par GRPO avec récompense
multi-objectif. Score obtenu sur les balises d'incertitude : **F1 = 0,685**.

*Ce que nous en retenons :* même **entraîné explicitement** à dire son
incertitude, sur son propre domaine, un modèle plafonne autour de 0,69 de F1.
Un contrôle de clé de Luhn, lui, a un taux de détection de 90 % sur une erreur
d'un chiffre, par construction et sans entraînement. L'écart n'est pas une
question d'ingénierie : les deux signaux ne sont pas de même nature.

## 1.4 Boucles de vérification et réparation

- **VeriHarness** (arXiv 2607.14167) : dans une boucle agent, un retour qui
  contient **la localisation de l'échec, la valeur observée et les
  alternatives admissibles** améliore le succès terminal de **+44 points** sur
  Qwen2.5-Coder-14B et **+42** sur Llama-3.1-8B, sous plafond de quatre
  appels. Le retour structuré vaut bien plus qu'un simple « échec ».
- **« Verify, Repair, Repeat, or Stop? »** (arXiv 2607.17641) : quand le
  vérificateur **et** le réparateur sont bruités, réparer peut abîmer ce qui
  était juste ; d'où une règle d'arrêt fondée sur le gain marginal réel.
- **« Diagnosis Before Recovery »** (arXiv 2608.11772) : rendre la correction
  **sélective** — diagnostiquer d'abord le type de panne, décider ensuite de
  l'effort.

*Ce que nous en retenons :* trois principes directement transposables — retour
structuré et localisé, budget avec règle d'arrêt, diagnostic avant réparation.
Et une différence favorable : **notre vérificateur n'est pas bruité.** Une
clé de Luhn ne se trompe jamais. Le risque d'« abîmer ce qui était juste »,
central dans arXiv 2607.17641, disparaît si l'on impose la monotonie.

## 1.5 Erreurs silencieuses en extraction documentaire

C'est le problème que le marché nomme sans le résoudre.

- Les hallucinations des modèles vision-langage produisent un texte
  **plausible et faux**, sans signal, contrairement aux erreurs d'OCR
  classique qui sont statistiquement reconnaissables.
- Taux rapporté d'hallucination sur des **montants** : **1 à 3 % des
  extractions** — tolérable pour un résumé, disqualifiant pour une validation
  financière.
- Le cas d'école cité : un modèle transpose un chiffre d'une ligne **et ajuste
  le total pour qu'il tombe juste**. Tout est cohérent. Tout est faux.
- **« Perceptual Hallucination in Vision–Language Models »**, *ACL Findings*
  2026, et les travaux sur l'extraction financière multi-étapes
  (arXiv 2604.26462 sur des flux KYC, arXiv 2510.23066 : meilleur résultat
  **87,27 %** avec PaddleOCR + MiniCPM-o-2.6) confirment le plafond.

*Ce que nous en retenons :* le cas de l'hallucination **interne­ment
cohérente** est celui qui compte, et il est fatal à toute méthode fondée sur
la confiance du modèle **ou sur la seule arithmétique**. Nous y revenons en
§3.2 — c'est la nuance qui distingue un argument solide d'un slogan.

---

# Étape 2 — État de l'art des frameworks OCR

## 2.1 Les analyseurs

| Système | Origine | Sortie | Force revendiquée |
|---|---|---|---|
| **Docling** + Granite-Docling-258M | IBM Research | `DoclingDocument`, MD, JSON | polyvalence des formats, RAG d'entreprise |
| **Marker v2** | Datalab | MD, JSON | **96,7 %** sur les tableaux, **94,2 %** en mise en page mixte |
| **MinerU 2.5** (+ Popo, arXiv 2605.24973) | OpenDataLab | MD, JSON | vitesse, littérature scientifique, CJK |
| **PP-OCRv6** (arXiv 2606.13108) | Baidu | texte | 34,5 M paramètres dépassant des VLM milliardaires |
| **Infinity Parser** (arXiv 2510.15349) | — | MD | apprentissage par renforcement sensible à la mise en page |
| **olmOCR**, **Mistral OCR**, **DeepSeek-OCR** | AI2, Mistral, DeepSeek | texte + structure | modèles auto-hébergeables |
| **pdf-craft**, **PyMuPDF4LLM**, **Liteparse** | divers | MD | livres, extraction native, faibles ressources |

Fournisseurs cloud — **Azure AI Document Intelligence**, **Google Document
AI**, **AWS Textract** — ajoutent des schémas métier et un score de confiance
par champ.

## 2.2 Le banc d'évaluation, et sa saturation

**OmniDocBench** (Ouyang et al., CVPR 2025) est le banc de référence :
1 355 pages, 9 types de documents, métriques **NED** (distance d'édition
normalisée) pour le texte, les formules et l'ordre de lecture, **TEDS** pour
les tableaux.

Il est **saturé**. GLM-OCR et PaddleOCR-VL-1.5 dépassent **94 %** ; les modèles
de frontière tournent autour de 90 %. La communauté le dit sans détour :
progresser encore relève de la « correction de cas limites » plutôt que d'un
gain réel. Et la métrique elle-même est contestée : elle **pénalise des
différences sémantiquement nulles** — ponctuation, espacement, HTML au lieu de
LaTeX — au point qu'une sortie correcte obtient 0,63 de TEDS.

La conclusion tirée par les praticiens : il faudrait mesurer la
**justesse sémantique alignée sur l'usage aval**.

## 2.3 Le seul concurrent agentique sérieux

**LandingAI ADE**, sur modèle propriétaire **DPT-2** : boucle agentique qui
planifie, décide et **s'auto-vérifie**, orchestrant analyseurs, modèles
spécialisés et un LLM séquenceur. Plus d'un milliard de documents traités ;
clients Barclays, Morgan Stanley, AstraZeneca, Deloitte.

C'est le système le plus proche de ce que nous voulons construire. Trois
différences structurantes :

1. **API cloud fermée** — le document sort du périmètre du client.
2. **Vérification par un modèle** — un LLM contrôle une sortie de modèle. La
   littérature du §1.3 dit exactement pourquoi ce signal plafonne, et le §1.4
   pourquoi une boucle sur vérificateur bruité peut dégrader.
3. **Aucune mesure publiée de couverture à risque borné.**

## 2.4 Ce que tous ont en commun

**Ils transforment. Ils ne vérifient pas** — ou ils vérifient avec un modèle.

La formulation industrielle la plus juste que nous ayons trouvée :

> *« Document AI gives you the extraction layer; your team still owns the
> validation, exception handling, and operations layer. […] those surrounding
> concerns usually take more engineering time than the first successful API
> call. »*

---

# Étape 3 — Analyse et interprétation

## 3.1 Le vide, formulé précisément

Trois littératures se côtoient sans se rencontrer :

- la **prédiction sélective** a le cadre formel mais cherche son signal
  **dans le modèle** ;
- l'**analyse documentaire** a les modèles mais mesure une **fidélité de
  transcription**, sur un banc désormais saturé ;
- l'**ingénierie métier** connaît les invariants — clés de contrôle,
  identités comptables, référentiels — mais les enfouit dans du code
  applicatif non testé, réécrit chez chaque intégrateur.

Personne ne traite les invariants métier comme **fonction d'abstention d'un
prédicteur sélectif**, ni ne mesure ce que cela donne en couverture à risque
borné.

## 3.2 La taxonomie qui décide de tout

C'est le cœur de l'analyse, et l'honnêteté impose de la poser complètement —
y compris là où notre approche ne suffit pas.

| Classe d'erreur | Confiance du modèle | Conforme (IJDAR 26) | Identité arithmétique | Clé de contrôle | Réconciliation inter-vues | Référentiel externe |
|---|---|---|---|---|---|---|
| Lecture fautive isolée, incohérente | parfois | parfois | **oui** | **oui** | **oui** | non |
| **Hallucination cohérente** (le modèle ajuste le total) | **non** | **non** | **non** | **oui**¹ | **oui** | **oui** |
| Champ manqué | parfois | parfois | partiel | non | **oui** | non |
| Divergence réelle XML ≠ page (erreur d'émetteur) | non | non | non | non | **oui** | non |
| IBAN substitué (fraude au fournisseur) | non | non | non | **oui**¹ | **oui** | **oui** |
| Fournisseur fictif, forme correcte | non | non | non | non | non | **oui** |
| Doublon inter-canal | non | non | non | non | non | (empreinte métier) |

¹ *Une clé de contrôle laisse passer une erreur avec probabilité ≈ 1/10 (Luhn)
ou ≈ 1/97 (mod-97) si le modèle « invente » un identifiant complet. Elle
n'est pas infaillible ; elle est exacte et bon marché.*

**Trois lectures de ce tableau.**

1. Les deux colonnes de gauche — tout ce que la recherche actuelle explore —
   sont vides sur les lignes qui coûtent de l'argent. Un modèle confiant et
   faux est, par définition, invisible à sa propre confiance.
2. **L'arithmétique seule ne suffit pas.** Le cas d'école du §1.5 — le modèle
   ajuste le total pour qu'il tombe juste — passe l'identité HT + TVA = TTC.
   Prétendre le contraire serait malhonnête. Ce qui l'attrape, ce sont les
   **clés** (le modèle ne peut pas rendre un SIREN faux conforme à Luhn autrement
   que par chance) et surtout la **réconciliation inter-vues** (le XML n'a pas
   été halluciné).
3. La colonne **réconciliation inter-vues** est la seule pleine, et elle est
   **structurellement inaccessible à tout système à vue unique** : l'information
   n'est pas dans sa vue. C'est notre résultat le plus fort, et il est
   démontrable plutôt qu'empirique.

## 3.3 Pourquoi la réforme rend ce résultat exploitable maintenant

Un Factur-X est un PDF/A-3 portant **deux représentations du même fait** : un
XML lisible par machine et une page lisible par un humain. Rien dans la norme
n'oblige qu'elles concordent. Le XML part en comptabilité ; la page part devant
l'auditeur et le juge.

Avant la réforme, ces documents étaient rares. À partir du **1ᵉʳ septembre
2026**, toute entreprise française doit pouvoir en **recevoir**, et à l'horizon
**2030** le règlement **ViDA** étend la logique aux 27 États membres. La
colonne la plus puissante de notre tableau passe donc, en un an, d'un cas de
niche à un flux quotidien de masse.

Corollaire méthodologique inattendu et précieux : **ce corpus fournit une
vérité terrain gratuite.** Le XML embarqué donne les champs, la page donne
l'entrée. Le corpus de conformité Factur-X / ZUGFeRD — 151 PDF réels produits
par de vrais émetteurs, dont les exemples officiels du Forum National de la
Facture Électronique — est un jeu d'évaluation d'extraction **que personne
n'exploite comme tel**.

## 3.4 Ancrage réglementaire

| Échéance | Texte | Obligation | Ce que le framework apporte |
|---|---|---|---|
| **1ᵉʳ sept. 2026** | Réforme française | Réception obligatoire de factures électroniques | La vérification XML ↔ page, que personne ne fait |
| 2026–2027 | Réforme, volet émission | Émission par paliers | Contrôle avant dépôt : une facture qui ne balance pas est rejetée par la plateforme |
| **2030** | **ViDA** | Déclaration numérique et facturation intra-UE | Vérificateurs pays transposables aux 27 |
| en vigueur | **EN 16931** | Sémantique européenne de la facture | Le document canonique s'y aligne |
| phasé | **AI Act, art. 11** | Documentation technique évaluable sans rétro-ingénierie | Générée depuis les traces réelles |
| phasé | **AI Act, art. 12** | **Journalisation automatique** : entrées, sorties, points de décision, traçabilité complète | Le journal rejouable **est** l'artefact demandé |
| phasé | **AI Act, art. 26** | Obligations du déployeur, conservation des journaux | Journal append-only, empreinte vérifiable |
| en cours | **Euro numérique** | Traçabilité des flux de paiement | Clé IBAN mod-97, détection de substitution de compte |

L'article 12 mérite d'être souligné : il exige exactement ce que produit notre
journal — enregistrement des entrées, des sorties, des points de décision, et
identification des personnes intervenues dans la vérification. Ce n'est pas
une fonctionnalité que nous ajoutons pour la conformité : c'est la conséquence
naturelle d'un système bâti sur des vérificateurs qui déclarent leur verdict.

## 3.5 L'économie : pourquoi la courbe risque–couverture *est* la courbe de coût

Une thèse scientifique qui ne se traduit pas en euros n'intéresse personne en
production. Or ici, la traduction est exacte — et c'est ce qui rend
l'affirmation C4 défendable des deux côtés.

Pour un flux de `N` documents, une couverture `c` et un risque `r` :

```
  Coût total  =  c·N·r·C_erreur   +   (1 − c)·N·C_revue   +   N·C_calcul
                 └── erreurs      └── revue humaine       └── inférence
                     acceptées        du reste
```

Les trois termes ne sont pas du même ordre :

| Terme | Ordre de grandeur | Source |
|---|---|---|
| `C_calcul` | ~0,001 € / page | inférence GPU mutualisée |
| `C_vérification` | **≈ 0** | arithmétique : quelques microsecondes |
| `C_revue` | 1 à 3 € | 2 à 4 min d'un comptable |
| `C_erreur` | **50 à 500 €** | détection tardive, extourne, rectification de TVA, pénalité et intérêts de retard ; sans compter un IBAN substitué, où la perte est le montant de la facture |

Trois conséquences, toutes vérifiables :

**1. Le coût est dominé par `C_erreur`, pas par la revue.** Avec `r = 2 %`
(taux d'hallucination sur montants rapporté par la littérature, §1.5) et
`C_erreur / C_revue ≈ 100`, automatiser sans vérifier coûte **deux fois plus
cher** que de tout faire relire à la main. C'est le calcul que fait
implicitement tout cabinet qui refuse l'automatisation — et il a raison tant
que personne ne vérifie.

**2. La vérification est gratuite, l'erreur ne l'est pas.** Un contrôle
déterministe coûte des microsecondes et supprime une classe entière d'erreurs
acceptées. Le rapport bénéfice/coût n'est pas favorable : il est sans commune
mesure. C'est ce qui rend indéfendable de ne pas le faire.

**3. L'optimum se déplace, il ne se choisit pas.** Sans vérificateurs, le
minimum de coût est à **couverture faible** — on n'automatise que le trivial.
Avec eux, `r` s'effondre à couverture égale, et l'optimum se déplace vers la
droite. **La boucle de réparation (C5) déplace encore ce point**, en
convertissant des abstentions en acceptations vérifiées.

> **C'est la formulation industrielle de la thèse :** nous ne vendons pas une
> meilleure lecture, nous vendons un **déplacement de l'optimum
> coût/couverture**. Et il se mesure sur les documents du client, avant
> l'achat.

Corollaire commercial direct : la métrique de vente est `c@r≤ε` — « combien
puis-je automatiser en restant sous un pour mille d'erreurs ? ». Aucun
concurrent ne publie ce chiffre, parce qu'aucun ne le mesure.

## 3.6 Périmètre : ouvert au monde, concentré sur l'Europe

Distinction volontaire, et elle n'est pas cosmétique.

**Le framework est universel.** Le protocole `Parser`, le document canonique,
le moteur de vérification, la politique, la boucle de réparation, la mesure
risque–couverture : rien là-dedans n'est européen. Un développeur brésilien ou
japonais installe `aracana`, écrit ses propres vérificateurs, et obtient le
même bénéfice. C'est ce qui rend le paquet publiable et adoptable, et c'est ce
qui donne au résultat scientifique sa portée générale.

**Les packs de vérificateurs que nous écrivons et maintenons sont européens.**
France, Suisse, Union — clés SIREN et TVA, IDE mod-11, QR-facture, formats de
TVA des 27, taux légaux, EN 16931, Factur-X. Nous ne prétendrons pas couvrir
le GSTIN indien ou le CNPJ brésilien : nous ne saurions pas les maintenir
correctement, et un vérificateur mal maintenu est pire que pas de
vérificateur — il autorise en silence.

Trois raisons de concentrer là :

1. **La réforme crée la demande à date fixe.** Septembre 2026 en France, 2030
   avec ViDA pour les 27. Peu de marchés offrent une échéance réglementaire
   qui force l'adoption.
2. **La conformité est un différenciateur défendable.** AI Act, RGPD,
   souveraineté des données : un service américain qui traite des factures
   françaises hérite d'un problème que nous n'avons pas.
3. **L'expertise ne se copie pas vite.** Connaître la clé de contrôle du QR-
   facture suisse ou le profil BASICWL du FNFE demande du terrain, pas du
   calcul.

L'architecture rend cette frontière propre : `countries/` est un dossier de
paquets indépendants, chacun testé séparément. Une contribution communautaire
pour un pays hors zone est la bienvenue et n'exige aucune modification du
cœur — c'est précisément à cela que sert un protocole de vérificateurs.

## 3.7 Thèse

> **L'extraction documentaire doit être opérée et évaluée comme une prédiction
> sélective dont la fonction d'abstention est un ensemble de vérificateurs
> déterministes exogènes au modèle — et dont les échecs, étant diagnostiques,
> pilotent une boucle de réparation bornée.**

Cinq affirmations falsifiables :

| | Affirmation | Comment la réfuter |
|---|---|---|
| **C1** | Les vérificateurs déterministes dominent la confiance du modèle sur la courbe risque–couverture | Trouver un analyseur dont la confiance donne une AURC meilleure |
| **C2** | La réconciliation inter-vues détecte une classe d'erreurs inaccessible à tout système à vue unique | Exhiber un système mono-vue qui détecte une divergence XML ≠ page |
| **C3** | Le plancher de risque est fixé par les vérificateurs, pas par l'analyseur | Montrer que le risque à couverture fixée varie significativement entre Docling, Marker, MinerU et ARACANA |
| **C4** | La couverture à risque borné est la métrique de déploiement pertinente | Montrer qu'une décision d'achat se prend sur la NED |
| **C5** | La réparation guidée par les vérificateurs augmente la couverture à risque constant | Mesurer une hausse du risque, ou une couverture inchangée |

---

# Étape 4 — Plan du framework

## 4.1 Principe d'organisation

Une seule abstraction dépend d'un modèle. Toutes les autres sont
déterministes, testables hors ligne, et utilisables avec **n'importe quel**
analyseur.

```
   Document brut  (PDF, image, XML, courriel)
        │
        ▼
  ┌──────────────┐  protocole ouvert — quinze lignes par adaptateur
  │   Parser     │  aracana · docling · marker · mineru · paddle · tesseract
  │              │  azure · google · textract · le vôtre
  └──────┬───────┘
         │  Document canonique — pages, blocs typés, boîtes 0–999, ordre
         ▼
  ┌──────────────┐  remplaçable ; par défaut, motifs multilingues FR/DE/IT/EN
  │  Extractor   │
  └──────┬───────┘
         │  Champs, chacun portant `found`
         ▼
  ┌══════════════┐  ★ LE CŒUR ★  aucun modèle, aucun apprentissage
  ║  Verifiers   ║  Luhn · clé TVA · IDE mod-11 · IBAN mod-97 · QR-facture
  ║              ║  identités comptables · réconciliation inter-vues
  ║              ║  empreinte métier · référentiels (VIES, INSEE)
  └══════┬═══════┘
         │  Verdicts : passe, gravité, indice diagnostique, cible
         ▼
  ┌──────────────┐  accepter · revoir · rejeter — et pouvoir le justifier
  │   Policy     │
  └──────┬───────┘
         │            ┌─────────────────────────────────────────────┐
         ├─ échec ───▶│  Agent : réparation guidée                  │
         │            │  monotone · budget borné · arrêt sur         │
         │            │  non-progression                            │
         │            └──────────────┬──────────────────────────────┘
         │◀──────────────────────────┘
         ├──▶  Sinks   FEC, CSV, comptabilité, file de revue
         ├──▶  Audit   journal rejouable (AI Act art. 12)
         └──▶  Measure courbe risque–couverture, AURC, c@r≤ε
```

## 4.2 Les sept modules, et ce que chacun doit garantir

| Module | Rôle | Invariant non négociable |
|---|---|---|
| `document` | modèle canonique + protocole `Parser` | Un adaptateur ne doit rien hériter ni rien importer de nous |
| `parsers/` | adaptateurs | Aucune dépendance obligatoire : chaque analyseur est un extra |
| `extract` | texte → champs | Rien n'est deviné ; un champ absent est `found=False` |
| `verify` | vérificateurs, registre, verdicts | Aucun modèle. Aucune correction silencieuse. Gravité déclarée par l'auteur du contrôle |
| `policy` | verdicts → décision | La décision est reconstructible depuis les verdicts seuls |
| `agent` | réparation guidée | **Monotonie** : ne peut que faire passer d'arrêté à accepté, jamais en assouplissant un contrôle. **Budget** : plafond d'actions et arrêt sur non-progression |
| `riskcov` | mesure | Ne compte comme risque que les faux **acceptés**, jamais les faux détectés |
| `audit` | trace | Append-only, format banal (JSON Lines), aucun document conservé |

## 4.3 Ce qui rend la monotonie indispensable

**« Verify, Repair, Repeat, or Stop? »** montre qu'une boucle à vérificateur
bruité peut dégrader ce qui était juste. Nous supprimons ce risque par
construction, avec une règle unique :

> Une réparation rejoue la **lecture**, jamais le **contrôle**. Un document ne
> peut passer d'arrêté à accepté que parce qu'une nouvelle lecture satisfait
> les mêmes vérificateurs, inchangés.

Sans cette règle, une boucle agentique devient une machine à fabriquer des
acceptations — exactement le détournement de récompense décrit au §1.3.

## 4.4 Protocole de mesure

**Grandeurs.** Pour un jeu `D` et un système `(analyseur A, vérificateurs V,
politique P, budget agent B)` :

- couverture `c` = part acceptée sans humain ;
- risque `r` = part des **acceptations** dont un champ décisif est faux ;
- courbe obtenue en durcissant `P` ; **AURC** pour comparer ;
- **`c@r≤ε`** — la grandeur qu'un directeur comptable demande.

**Ce qui rend la mesure honnête.**
- Risque mesuré sur les **champs décisifs** (montants, numéro, date,
  identifiants bancaires), pas sur la transcription entière — c'est
  précisément la critique adressée à OmniDocBench en §2.2.
- Les faux **détectés** ne comptent pas : ils sont partis en revue, le système
  a fait son travail. C'est ce qui distingue cette métrique d'une précision.
- Intervalles par **bootstrap apparié** sur les mêmes documents. Jamais de
  comparaison entre échantillons différents.
- Conformément à Traub et al. 2024 : pour isoler l'apport de `V`, on **fixe
  l'analyseur** et l'on ne fait varier que la politique.

**Jeux d'évaluation.**
1. **Corpus de conformité Factur-X / ZUGFeRD** — 151 PDF réels, vérité terrain
   gratuite tirée du XML embarqué.
2. **Divergences injectées** — pour C2 : on altère la page ou le XML d'un
   document concordant et l'on mesure le taux de détection. Un système
   mono-vue y obtient 0 par construction.
3. **Documents à vue unique annotés** — pour C1 et C5.

## 4.5 Article envisageable

**Titre de travail** — *Verified Extraction: Selective Prediction for Document
AI with Deterministic Domain Verifiers*

**Contributions revendiquées**
1. Reformulation de l'extraction documentaire en prédiction sélective à
   fonction d'abstention **exogène au modèle**, sans jeu de calibration
   étiqueté — contrairement à l'approche conforme (IJDAR 2026).
2. Résultat structurel : la réconciliation inter-vues détecte une classe
   d'erreurs inaccessible à tout système mono-vue (C2), avec la taxonomie du
   §3.2 qui délimite honnêtement ce que chaque famille de vérificateurs
   attrape et laisse passer.
3. Protocole d'évaluation risque–couverture pour l'extraction, et
   démonstration que le plancher de risque est porté par les vérificateurs et
   non par l'analyseur (C3) — réponse directe à la saturation d'OmniDocBench.
4. Boucle de réparation à vérificateur **non bruité**, monotone et bornée,
   augmentant la couverture à risque constant (C5).
5. Implémentation ouverte Apache-2.0, agnostique de l'analyseur, avec
   adaptateurs pour les principaux systèmes, et un jeu d'évaluation à vérité
   terrain gratuite tiré du corpus de conformité Factur-X.

**Journaux visés** — *IJDAR* (International Journal on Document Analysis and
Recognition), qui a publié l'article conforme de 2026 et est le lieu naturel du
débat ; à défaut, la piste conférence *ICDAR* ou *DAS*.

**Ce qu'il faut avant de soumettre, et qui n'est pas fait**
- mesures sur **≥ 3 analyseurs** et **≥ 500 documents** ;
- courbes avec intervalles par bootstrap apparié ;
- comparaison honnête à un seuil sur la confiance d'un service commercial ;
- réplication de la baseline conforme d'IJDAR 2026 sur notre corpus.

Sans cela, il y a une thèse et une implémentation — pas un article. Le dire
maintenant évite de le découvrir en relecture.

---

# État des mesures

> Section tenue à jour à chaque campagne. Elle enregistre ce qui a été mesuré,
> **y compris les campagnes non concluantes** — les taire ferait du reste une
> sélection.

## Campagne 1 — premier passage du harnais C3

**Dispositif** : `framework/experiences/c3_analyseurs.py`, corpus de
conformité Factur-X (11 fichiers dont 10 exploitables, le douzième sans XML
embarqué donc sans vérité terrain), deux analyseurs — le témoin sans lecture
et la couche texte native du PDF.

**Résultat : non concluant.** Couverture nulle pour les deux analyseurs, à
toutes les politiques. La courbe se réduit au point (0, 0) ; il n'y a rien à
comparer.

**Pourquoi**, d'après le diagnostic du harnais :

| Motif bloquant | Occurrences (couche texte, n=10) |
|---|---|
| Clé de Luhn du SIRET | 7 |
| Écart XML ↔ page sur `total_incl_vat` | 3 |
| Écart XML ↔ page sur `total_excl_vat` | 3 |
| Écart XML ↔ page sur `vat_amount` | 3 |
| `HT + TVA = TTC` | 3 |
| `invoice_number` absent | 4 |

**Lecture.** Le motif dominant est un contrôle d'identité, et il a raison : les
exemples du FNFE portent des SIREN et SIRET **fictifs**. Ce corpus n'est pas
fait pour mesurer une couverture — il est fait pour éprouver la conformité
d'un émetteur. Le système se comporte correctement ; c'est l'instrument qui
n'est pas adapté à cette mesure-là.

Résultat secondaire, non recherché mais notable : **trois documents sur dix
présentent un écart réel entre le XML et la page** sur les montants, détecté
par la réconciliation. C'est C2 qui s'exerce, sur des documents produits par
de vrais outils d'émission. À creuser — il faut établir si ce sont des erreurs
de lecture de la couche texte ou de véritables divergences d'émetteur.

**Ce qui a été corrigé dans le harnais à cette occasion**, et qui aurait
invalidé une publication :

1. *Circularité de la vérité terrain.* La première version tirait les champs
   du XML via `decider()` — qui traite le XML comme normatif — puis les
   comparait au XML. Résultat : 6/6 champs justes partout, y compris pour le
   témoin qui ne lit rien. Un score parfait mesurant l'égalité d'une chose
   avec elle-même. Les champs proviennent désormais de la **page**.
2. *Verdict fabriqué à partir de zéro donnée.* Avec toutes les couvertures à
   zéro, les deux étendues valaient zéro, et le script imprimait « NON
   compatible avec C3 » — une réfutation tirée d'une absence de mesure. Le
   harnais distingue maintenant « réfuté » de « non testé », et refuse de
   conclure.

**Suite.** Deux voies, toutes deux honnêtes, aucune ne consistant à desserrer
un contrôle pour obtenir une courbe :

- corpus à identifiants réels — la voie pour une publication ;
- registre de vérificateurs excluant les contrôles d'identité, **mentionné sur
  la courbe** : on mesure alors la lecture, pas l'identité.

Et, dans les deux cas, au moins un analyseur de plus : Docling ou MinerU dans
un environnement séparé, plus le service ARACANA.

---

# Bibliographie

**Prédiction sélective**
1. Geifman & El-Yaniv, *Selective Classification for Deep Neural Networks*, NeurIPS 2017 — [ResearchGate](https://www.researchgate.net/publication/317100919_Selective_Classification_for_Deep_Neural_Networks)
2. Geifman & El-Yaniv, *SelectiveNet: A Deep Neural Network with an Integrated Reject Option*, ICML 2019 — [PMLR](http://proceedings.mlr.press/v97/geifman19a/geifman19a.pdf)
3. *Overcoming Common Flaws in the Evaluation of Selective Classification Systems*, NeurIPS 2024 — [Proceedings](https://proceedings.neurips.cc/paper_files/paper/2024/file/047c84ec50bd8ea29349b996fc64af4b-Paper-Conference.pdf)
4. Cattelan & Silva, *On Selective Classification under Distribution Shift* — [OpenReview](https://openreview.net/pdf?id=FiqXqKR26c)
5. *AURC* — [TorchUncertainty](https://torch-uncertainty.github.io/generated/torch_uncertainty.metrics.classification.AURC.html)

**Prédiction conforme et extraction**
6. *Beyond Accuracy: Understanding Model Confidence in Key Information Extraction with Conformal Prediction*, IJDAR 2026 — [Springer](https://link.springer.com/article/10.1007/s10032-026-00572-y)
7. *Uncertainty Quantification for NER via Full-Sequence and Subsequence Conformal Prediction* — [arXiv 2601.16999](https://arxiv.org/html/2601.16999)

**Auto-vérification et ses limites**
8. *When Should a Language Model Trust Itself? Same-Model Self-Verification* — [arXiv 2605.02915](https://arxiv.org/pdf/2605.02915)
9. *Trust but Verify: Prover-Verifier Deliberation for Selective LLM Prediction* — [arXiv 2605.25133](https://arxiv.org/pdf/2605.25133)
10. *Entropy Alone is Insufficient for Safe Selective Prediction in LLMs* — [arXiv 2603.21172](https://arxiv.org/pdf/2603.21172)
11. Guan et al., *Teaching VLMs to Admit Uncertainty in OCR*, ICLR 2026 — [OpenReview](https://openreview.net/forum?id=zyCjizqOxB)
12. *Selective "Selective Prediction": Reducing Unnecessary Abstention in Vision-Language Reasoning* — [arXiv 2402.15610](https://arxiv.org/pdf/2402.15610)
13. *Reliable VQA: Abstain Rather Than Answer Incorrectly*, Berkeley 2022 — [EECS](https://www2.eecs.berkeley.edu/Pubs/TechRpts/2022/EECS-2022-137.pdf)

**Boucles de réparation**
14. *Structured Feedback Improves Repair in an LLM Agent Loop* (VeriHarness) — [arXiv 2607.14167](https://arxiv.org/html/2607.14167v1)
15. *Verify, Repair, Repeat, or Stop? Robust Stopping for Noisy Verify-Repair Loops* — [arXiv 2607.17641](https://arxiv.org/html/2607.17641)
16. *Diagnosis Before Recovery: Turning Agent Failures into Selective Self-Correction* — [arXiv 2608.11772](https://arxiv.org/abs/2608.11772)

**Analyse documentaire et bancs d'évaluation**
17. Ouyang et al., *OmniDocBench*, CVPR 2025 — [CVF](https://openaccess.thecvf.com/content/CVPR2025/papers/Ouyang_OmniDocBench_Benchmarking_Diverse_PDF_Document_Parsing_with_Comprehensive_Annotations_CVPR_2025_paper.pdf)
18. *OmniDocBench is Saturated, What's Next for OCR Benchmarks?* — [LlamaIndex](https://www.llamaindex.ai/blog/omnidocbench-is-saturated-what-s-next-for-ocr-benchmarks)
19. *MinerU-Popo: Universal Post-Processing Model for Structured Document Parsing* — [arXiv 2605.24973](https://arxiv.org/pdf/2605.24973)
20. *Infinity Parser: Layout Aware Reinforcement Learning for Scanned Document Parsing* — [arXiv 2510.15349](https://arxiv.org/pdf/2510.15349)
21. *PP-OCRv6* — [arXiv 2606.13108](https://arxiv.org/html/2606.13108)
22. *Docling vs Marker vs MinerU — benchmark 2026* — [Medium](https://adityamangal98.medium.com/docling-vs-marker-vs-mineru-the-ultimate-open-source-pdf-parser-benchmark-2026-which-is-best-a36ecbb6c6b1)

**Erreurs silencieuses et documents financiers**
23. *Perceptual Hallucination in Vision–Language Models*, ACL Findings 2026 — [ACL](https://aclanthology.org/2026.findings-acl.1237.pdf)
24. *A Multistage Extraction Pipeline for Long Scanned Financial Documents (KYC)* — [arXiv 2604.26462](https://arxiv.org/pdf/2604.26462)
25. *Multi-Stage Field Extraction of Financial Documents with OCR and Compact VLMs* — [arXiv 2510.23066](https://arxiv.org/pdf/2510.23066)
26. *Why OCR Invoice Processing Fails: The Automation Gap* — [LayerNext](https://www.layernext.ai/post/ocr-invoice-processing-errors)
27. *Generative AI vs Extraction: Document Validation* — [CheckFile](https://www.checkfile.ai/en-US/blog/generative-ai-vs-extraction-document-validation)

**Systèmes agentiques commerciaux**
28. *Agentic Document Extraction (DPT-2)* — [LandingAI](https://landing.ai/blog/ocr-to-agentic-document-extraction-a-look-into-the-evolution-of-document-intelligence)
29. *Invoice Parsing at Scale with Agentic Document Extraction* — [LandingAI](https://landing.ai/blog/invoice-parsing-at-scale-with-agentic-document-extraction)

**Réglementation**
30. *EU AI Act, art. 11 — Technical Documentation* — [artificialintelligenceact.eu](https://artificialintelligenceact.eu/article/11/)
31. *EU AI Act, art. 12 — Record-Keeping* — [artificialintelligenceact.eu](https://artificialintelligenceact.eu/article/12/)
32. *EU AI Act, art. 26 — Obligations of Deployers* — [artificialintelligenceact.eu](https://artificialintelligenceact.eu/article/26/)
