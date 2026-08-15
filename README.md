# ARACANA

**Extraction vérifiée pour documents européens.** Un OCR vous dit ce qu'il a
lu. ARACANA vous dit si vous pouvez le croire — et refuse d'automatiser quand
la réponse est non.

```bash
pip install aracana-ocr
```

Aucune dépendance obligatoire. Aucun GPU. Deux secondes.

---

## Le problème

Les analyseurs de documents — Docling, Marker, MinerU, Azure, Textract —
transforment un PDF en texte structuré. Aucun ne vérifie sa propre sortie.

C'est un problème parce que les erreurs modernes sont **silencieuses**. Un
modèle vision-langage ne produit plus des caractères douteux : il produit un
texte fluide, plausible et faux. Le taux rapporté d'hallucination sur des
**montants** est de 1 à 3 % — tolérable pour un résumé, disqualifiant pour une
écriture comptable.

Et le signal ne vient pas du modèle. Un modèle entraîné explicitement à dire
son incertitude en OCR plafonne à **F1 = 0,685** (Guan et al., ICLR 2026). Une
clé de Luhn détecte 90 % des erreurs d'un chiffre, sans entraînement, en
quelques microsecondes.

## L'idée

Traiter l'extraction comme une **prédiction sélective** dont la fonction
d'abstention est un ensemble de **vérificateurs déterministes exogènes au
modèle**.

```python
from aracana import decider
from aracana.extract import extraire_champs

d = decider(open("facture.pdf", "rb").read(), extraire=extraire_champs)

print(d.issue)            # auto_post | human_review | rejected
print(d.justification())  # une phrase montrable à un auditeur
for c in d.controles:
    print(c["passed"], c["check"], c["detail"])
```

Ces vérificateurs sont de l'arithmétique et des tables : clé de Luhn du SIREN,
clé TVA française, IDE suisse mod-11, IBAN mod-97 (ISO 7064), référence
QR-facture, identité `HT + TVA = TTC`, réconciliation entre le XML d'un
Factur-X et sa page rendue. **Un modèle peut halluciner un total qui tombe
juste ; ce code ne le peut pas.**

## Ce qui n'existe nulle part ailleurs

Un Factur-X contient **deux représentations du même fait** : un XML lisible par
machine et une page lisible par un humain. Rien dans la norme ne garantit
qu'elles concordent. Le XML part en comptabilité ; la page part devant
l'auditeur et le juge.

```
1 écart entre le XML et la page, 1 bloquant.
  total_incl_vat : XML 1500.00 / page 1600.00
```

Aucun système à vue unique ne peut détecter cela — l'information n'est pas
dans sa vue. Ce n'est pas un avantage empirique, c'est un résultat structurel.

À partir du **1ᵉʳ septembre 2026** en France, puis avec **ViDA** à l'échelle
des 27, ces documents deviennent le flux normal.

## Agnostique de l'analyseur

`Parser` est un protocole, pas une classe à hériter. Quinze lignes suffisent.

```python
from aracana import plugins
from aracana.document import Bloc, Boite, Document, Page, TypeBloc

class MonAnalyseur:
    nom = "le mien"
    def analyser(self, source, **kw) -> Document:
        return Document(pages=[Page(1, 1240, 1754, [
            Bloc(1, TypeBloc.TEXTE, "…", Boite(70, 40, 900, 80))])])

plugins.parser(MonAnalyseur())
```

Un paquet publié se déclare dans son `pyproject.toml` et devient disponible
dès l'installation :

```toml
[project.entry-points."aracana.parsers"]
docling = "aracana_docling:Parser"
```

Quatre points d'insertion : `parsers`, `verifiers`, `sinks`, `tools`.

## Mesurer ce qui décide

Le banc de référence du domaine, OmniDocBench, est saturé au-delà de 94 % et
mesure une distance d'édition — qui compte de la même façon un nom mal accentué
et un montant faux.

ARACANA mesure la **courbe risque–couverture** :

```python
from aracana.riskcov import courbe, optimum_economique

c = courbe(cas, rejouer)
print(c)                                  # AURC, et chaque point
print(c.couverture_a_risque(0.001))       # « combien à moins d'un pour mille ? »
print(optimum_economique(c, cout_erreur=200, cout_revue=2))
```

Le risque ne compte que les faux **acceptés** : une erreur détectée et envoyée
en revue n'est pas un échec du système, c'est son fonctionnement.

## En ligne de commande

```bash
aracana trier    facture.pdf              # quelle route, faut-il un modèle ?
aracana verifier facture.pdf              # lecture, contrôles, décision
aracana lot      ./factures --fec sortie/ # FEC + file de revue + audit
aracana extensions                        # ce qui est branché
```

## Traçabilité

```python
from aracana import JournalAudit

j = JournalAudit("audit.jsonl", modele="aracana-ocr-v1")
j.enregistrer(d, document="facture.pdf", octets=octets)
print(j.statistiques())
```

Une ligne JSON par décision : contrôles passés et échoués, champs lus, versions
du code et du modèle, empreintes. Aucun document n'est conservé — un journal
qui recopierait les factures deviendrait lui-même une base de données à
caractère personnel.

C'est exactement l'artefact exigé par l'**article 12 du règlement européen sur
l'IA** : enregistrement des entrées, des sorties et des points de décision.

## Périmètre

**Le framework est universel** — protocoles, vérification, politique, mesure :
rien n'y est européen.

**Les packs de vérificateurs que nous maintenons sont européens** — France,
Suisse, Union. Nous ne prétendrons pas couvrir le GSTIN indien : nous ne
saurions pas le maintenir, et un vérificateur mal maintenu est pire que pas de
vérificateur — il autorise en silence. Les contributions pour d'autres
juridictions sont les bienvenues et n'exigent aucune modification du cœur.

## Ouvert et commercial

Tout le traitement est ouvert, sous **Apache-2.0** : triage, extraction
FR/DE/IT/EN, packs pays, réconciliation, décision, export FEC, lots, mesure,
CLI, extensions.

`aracana-ocr-pro`, sous licence commerciale, ajoute ce qu'exige une organisation et
qu'un développeur seul n'utilise pas : journal scellé par chaînage de hachage,
dossier de conformité AI Act, outillage RGPD, détection de dérive sur vos
documents, connecteurs métier, multi-locataire.

```bash
aracana pro     # ce qui est ouvert, ce qui est payant, ce qui est installé
```

Aucune de ces extensions n'est nécessaire pour lire et vérifier une facture.

## Fondation scientifique

La conception découle d'une thèse écrite avant le code, avec ses affirmations
falsifiables et ce qui manque encore pour les défendre :
[`RECHERCHE.md`](RECHERCHE.md) — 32 sources, taxonomie des classes d'erreur,
protocole de mesure.

## État

`0.1.0`. Le cœur est testé (458 assertions sur la chaîne, 65 sur les garanties
du framework). Les adaptateurs Docling, Marker et MinerU, ainsi que le jeu
d'évaluation public, sont en cours.

## Licence

Apache-2.0. Le modèle ARACANA OCR est dérivé de `baidu/Unlimited-OCR` (MIT).
