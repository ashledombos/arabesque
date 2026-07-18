---
description: Audit mensuel « à zéro » — redécouverte indépendante du système, procès des vérités acquises, méta-audit des méthodes de suivi et des skills (y compris celle-ci). Sortie = tampons de fraîcheur + re-tests proposés en protocole gelé.
argument-hint: "[complet|registre-seul|meta-seul]"
---

# /table-rase — le procès mensuel des vérités acquises

Mode demandé : **$ARGUMENTS** (vide = `complet`).

## Pourquoi cette skill existe

Trois couches sont déjà surveillées en continu : l'exécution (`/suivi`),
la performance (`/bilan`), la R&D (protocoles gelés). **La couche jamais
re-questionnée systématiquement = le graphe de décisions lui-même** — les
« vérités acquises » sur lesquelles tout repose. Une hypothèse vraie en 2024
pourrit en silence (cas fondateurs : edge Extension mort détecté avec des
semaines de retard ; STRATEGY.md de Révérence affichant un PASS déjà invalidé).

**Posture** : procès, pas confirmation. On cherche à FALSIFIER, pas à
rassurer. « Quelle donnée tuerait cette croyance, et l'ai-je maintenant ? »

## Cadence et déclenchement

- **Mensuel calendaire** : fenêtre jour 14-17 du mois (évite la collision
  avec le bilan mensuel des jours 1-2). `/suivi` signale quand c'est dû
  (ligne watchlist `table_rase_due` : aucun `docs/audit/table_rase_YYYY-MM.md`
  du mois courant pendant la fenêtre → proposer, ne PAS auto-lancer — c'est
  une session lourde).
- Lancement manuel possible n'importe quand (`/table-rase`).

## Garde-fous absolus (lire avant chaque run)

1. **Jamais de re-tuning.** L'audit PROPOSE des re-tests en protocole gelé
   pré-enregistré, un tir — il ne recalibre rien, n'optimise rien, ne
   modifie ni config live ni stratégie. Re-tester parce que la preuve est
   périmée ou que les données ont bougé = légitime. Re-litiger parce que la
   re-dérivation « sent » autrement = interdit sans donnée nouvelle.
2. **Les rejets opérateur sont hors périmètre** (martingale/DCA-levier,
   grid…) sauf donnée factuelle nouvelle — auquel cas on la PRÉSENTE, on ne
   ré-ouvre pas soi-même.
3. **Aucun changement live** sans validation opérateur. Les livrables sont
   des documents et des propositions.
4. **La boussole (CLAUDE.md) n'est pas auditable ici** : c'est le cahier des
   charges opérateur (profil prop firm), pas une hypothèse empirique. Ce qui
   EST auditable : tout ce qui prétend la servir.

## Registre des hypothèses : `docs/HYPOTHESES.md`

Une ligne par croyance porteuse. Colonnes : ID, énoncé, date de preuve,
fenêtre de données de la preuve, poids (— combien de choses cassent si c'est
faux), source (commit/doc), **dernier tampon** (« tient au JJ-MM »), verdict
courant. Les entrées les plus ANCIENNES et les plus LOURDES passent en
priorité au procès. Tout audit met à jour les tampons ; une hypothèse sans
re-vérification depuis > 6 mois est marquée 🕰️ périmée d'office (à re-tester
ou à déclasser explicitement).

## Déroulé (mode `complet`)

### Phase 0 — Mise à jour du registre (~10 min)
Balayer les décisions/documents modifiés depuis le dernier run
(`git log --since`, DECISIONS.md, STRATEGY.md, VALIDATION_CONTRACT) →
ajouter les hypothèses nouvelles au registre, retirer les caduques.

### Phase 1 — Dérivation à froid (sous-agent, ~20 min)
Lancer UN sous-agent (general-purpose) avec un prompt qui lui interdit de
lire DECISIONS.md, HANDOFF.md, EXPERIMENT_LOG.md, docs/audit/, HYPOTHESES.md
et tout STRATEGY.md. Il reçoit uniquement : la boussole, l'accès au code,
aux données `barres_au_sol/`, et à `logs/trade_journal.jsonl` +
`logs/adage_ombre_sessions.jsonl`. Sa mission : « tu découvres ce système,
dis ce que TU conclurais des données brutes — quelles stratégies semblent
vivantes/mortes, quels paramètres te semblent porteurs, qu'est-ce qui te
surprend ». Son rapport = contre-expertise vierge de nos biais de dossier.

### Phase 2 — Confrontation (~15 min)
Croiser le rapport à froid avec le registre. Trois issues par divergence :
(a) le dossier tient, la contre-expertise manquait de contexte → noter
pourquoi ; (b) la contre-expertise a vu quelque chose → **proposition de
re-test protocole gelé** ; (c) indécidable → marquer l'hypothèse 🟡 avec le
critère qui trancherait.

### Phase 3 — Falsification ciblée (~20 min)
Pour les 3-5 hypothèses les plus lourdes/anciennes : re-lancer les scripts
de mesure EXISTANTS sur la fenêtre fraîche (edge audit, WF, invariants,
baselines rolling). Pas de nouveau calcul exploratoire — uniquement des
instruments déjà gelés, relus à date. Tamponner ou signaler.

### Phase 4 — Méta-audit des méthodes (~15 min)
Questionner l'outillage lui-même, y compris CETTE skill :
- Les seuils de la watchlist `/suivi` détecteraient-ils les pannes RÉELLES
  des 2 derniers mois ? (rejouer mentalement les incidents contre les triggers)
- Les fenêtres `/bilan` couvrent-elles ce qui a bougé ?
- Angles morts : que mesure-t-on jamais ? Quelles alertes n'ont JAMAIS tiré
  (seuil inatteignable ?) ou tirent pour rien (fatigue d'alerte) ?
- Cette skill : la Phase 1 produit-elle du neuf ou du bruit ? Les tampons
  sont-ils consultés ? Ajuster le déroulé si non (et le committer).

### Phase 5 — Livrables
1. `docs/audit/table_rase_YYYY-MM.md` : verdicts, divergences, re-tests
   proposés (chacun avec hypothèse + critère de kill pré-écrit), méta-constats.
2. `docs/HYPOTHESES.md` : tampons rafraîchis.
3. HANDOFF si un re-test entre dans la file des GO (validation opérateur).
4. Notif Telegram+ntfy structurée en langage simple (résumé/verdict/plan),
   même si RAS (c'est le rapport mensuel de non-complaisance).

## Modes partiels
- `registre-seul` : Phase 0 uniquement (rattrapage après grosse période R&D).
- `meta-seul` : Phase 4 uniquement.

## Contraintes de sortie
- Le dossier mensuel ≤ ~150 lignes : verdicts et divergences, pas de prose.
- Chaque proposition de re-test = 3 lignes max : hypothèse, instrument
  existant à relancer, critère de kill. Le protocole détaillé sera gelé
  AU MOMENT du go opérateur, pas ici.
- Terminer par la date du prochain run attendu (fenêtre 14-17 du mois suivant).
