# Guide de l'opérateur — comprendre Arabesque sans jargon

> **Pour qui ?** Toi. Tous les autres documents du projet sont écrits pour les
> agents et la machine. Celui-ci explique **ce que fait le système, sur quoi il
> parie, et comment on décide** — en langage simple, chaque terme technique
> réexpliqué. Mis à jour : 2026-07-03.

---

## 1. Le but du jeu

Tu ne trades pas ton propre capital : tu passes des **challenges de prop firms**
(FTMO, GFT). Le principe : tu paies un droit d'entrée (80-600 €), tu trades un
compte fictif de 100-150 k$, et si tu gagnes ~8-10 % **sans jamais perdre plus
de ~10 % (ni ~4-5 % en une seule journée)**, la firme te confie un vrai compte
et te reverse ~80 % des gains. C'est un levier : quelques centaines d'euros
d'entrée peuvent donner accès à des payouts de milliers d'euros.

**La conséquence stratégique** : les prop firms ne récompensent pas la
performance brute, mais la **régularité**. Un système qui gagne souvent des
petits montants et perd rarement (et peu) passe. Un système qui fait +30 %
avec des montagnes russes échoue (limite journalière touchée). C'est pour ça
que toutes nos stratégies visent le même profil : **beaucoup de petits gains,
peu de pertes, petites quand elles arrivent**.

**L'objectif chiffré du système** (gravé le 2026-07-03) : produire **au moins
1,5R net par mois**. C'est le rythme qui passe un challenge en 6-12 mois.

---

## 2. Les 8 concepts qui suffisent pour tout comprendre

**R (l'unité de risque)** — Tout se mesure en R. 1R = ce qu'on accepte de
perdre sur un trade raté. Si on risque 100 € et qu'on gagne 200 €, le trade a
fait +2R. S'il touche le stop, -1R. Penser en R permet de comparer des trades
de tailles différentes et de régler la taille indépendamment de la stratégie.

**Edge (l'avantage)** — Le gain moyen par trade, en R, sur beaucoup de trades.
Un edge de +0.20R veut dire : en moyenne, chaque trade rapporte 20 % de ce
qu'il risque. Les vrais edges sont **minuscules** (+0.1 à +0.3R). C'est normal :
si c'était gros, tout le monde le prendrait et il disparaîtrait.

**WR (win rate, taux de réussite)** — Le % de trades gagnants. Nos stratégies
visent 70-90 % : beaucoup de petits gains (+0.2 à +2R), rares pertes (-1R max).

**Drawdown (DD)** — La perte cumulée depuis le sommet du compte. C'est LA
contrainte prop firm : -10 % total = éliminé. Nos gardes internes coupent
bien avant (pause à -8 %, taille réduite dès -7 %).

**Coûts en R** — Chaque trade paie une taxe (spread = écart achat/vente,
commission, swap = frais de nuit). Exprimée en R, cette taxe est **fixe par
marché** : ~0.015R sur l'or chez GFT, ~0.03R sur les devises, ~0.05-0.24R sur
les cryptos. **Un edge de +0.05R sur un marché qui coûte 0.11R perd de
l'argent à coup sûr, même parfaitement exécuté.** C'est la leçon la plus chère
du projet (elle a coûté ~-72R) : on juge maintenant tout candidat *net de
coûts mesurés*, jamais en brut.

**Échantillon et variance** — Un trade a un écart-type de ~0.6R. Mesurer un
edge de +0.2R avec 30 trades donne une incertitude de ±0.23R : **la barre
d'erreur est plus grosse que la chose mesurée**. D'où la règle : ne jamais
décider sur moins de 30 trades, considérer tout verdict positif comme fragile,
et tout verdict négatif massif (ex : -5R sur 166 trades) comme solide.

**Walk-forward (WF)** — La méthode anti-illusion. Au lieu de tester une
stratégie sur tout l'historique (elle « connaît » déjà les réponses), on la
calibre sur 6 mois puis on la teste sur les 2 mois *suivants*, en faisant
glisser la fenêtre. Si elle gagne sur des données qu'elle n'a jamais vues,
fenêtre après fenêtre, l'edge a une chance d'être réel.

**Régime** — Le caractère du marché à une époque (tendanciel, agité, calme).
Un edge peut être réel *et* dormant selon le régime. C'est pour ça qu'un
enterrement n'est définitif que « sur la période mesurée » et que les
stratégies coupées restent surveillées gratuitement.

---

## 3. Les stratégies — sur quoi chacune parie

Chaque stratégie porte un nom de mouvement de danse et parie sur un
**comportement récurrent des participants du marché**. Voici le pari de
chacune, et son statut au 2026-07-03.

### 🟢 Glissade — EN LIVE (or uniquement) — le seul edge validé
**Le pari** : dans une tendance de fond, les replis s'essoufflent avant de se
retourner. On détecte l'essoufflement par une **divergence RSI** : le prix
fait un nouveau creux, mais la « force » du mouvement (RSI) ne suit plus —
comme un ballon qui rebondit de moins en moins haut. On entre alors **dans le
sens de la tendance**, avec un stop serré, dès que le repli montre qu'il est à
bout de souffle.
**Pourquoi ça peut marcher sur l'or** : marché très profond, participants très
différents (banques centrales, industriels, particuliers) → sur-réactions
fréquentes qui se corrigent ; et régime haussier 2025-26 où les creux se
rachètent.
**Chiffres** : +0.27R par trade en walk-forward (16 trades, 100 % gagnants —
échantillon mince, d'où la prudence), ~1-2 trades/mois. Coût or : ~0.015R
chez GFT. En live à taille réduite (rodage ×0.25).

### 🟡 Renversé — CANDIDAT (métaux) — en instruction
**Le pari** : les stops des particuliers s'accumulent sous les creux évidents.
Les gros acteurs « balaient » ces niveaux (le prix casse brièvement le creux,
déclenche les stops, aspire la liquidité) **puis le prix se retourne**. On
détecte le balayage + le retournement de structure, et on entre dans le sens
du retournement. C'est de la chasse au piège à stops.
**Chiffres** : argent (XAGUSD) +0.26R sur 21 trades, or +0.13R sur 9 — soit
~+0.17R net estimé, ~3-4 trades/mois. **Vérifié : 0 % de trades en commun avec
Glissade** → c'est un deuxième moteur, pas le même compté deux fois.
**Où il en est (2026-07-04)** : test sur les 3 derniers mois réussi (+0.30R,
robuste aux frais) → **en observation « ombre » depuis le 04/07** : ses signaux
sont calculés en conditions 100 % réelles mais aucun ordre ne part. Après 2-4
semaines (~15 signaux), si l'ombre colle aux tests, décision de passage en
réel (la tienne), en priorité chez GFT (frais 3× plus bas sur les métaux).

### ⚫ Pas de Deux — ÉCARTÉE (2026-07-04, interdite par les prop firms)
**Le pari** : deux instruments statistiquement liés (cointégrés) « dansent »
autour d'un équilibre. Quand l'écart devient anormal, on vend le cher et on
achète le pas-cher — neutre au marché, le diversifiant idéal sur le papier.
**Pourquoi écartée** : ton intuition d'époque était la bonne. FTMO interdit
textuellement les « positions opposées sur instruments fortement corrélés »,
GFT interdit le « hedging entre instruments corrélés » même dans un seul
compte — c'est la définition même du pairs trading. Risque : refus de payout.
Verdict rendu en 30 minutes de lecture des règlements, **avant** d'écrire la
moindre ligne de code (le pipeline « compliance d'abord » a fait son travail).
Reste valable un jour sur un compte personnel, hors prop firm.

### ⚫ Extension — ENTERRÉE (2026-07-03) — l'ancienne stratégie principale
**Le pari** : quand la volatilité se comprime (bandes de Bollinger serrées),
elle finit par exploser dans un sens — on suit l'explosion (trend-following).
**Pourquoi enterrée** : validée jadis sur 20 mois (+0.13R), mais en régime
récent (2025-mi-2026) l'edge a disparu **partout** : crypto +0.05R brut mais
coûts 0.05-0.11R (le spread mange tout), devises/métaux carrément négatifs
avant frais. Trois causes : l'estimation d'origine était optimiste (biais de
sélection), les coûts n'étaient pas modélisés, et le régime s'est dégradé.
Ses signaux restent calculés gratuitement : si le marché re-trend, on le verra.

### ⚫ Cabriole — ENTERRÉE (mai-juillet 2026)
**Le pari** : cassure d'un canal de Donchian (plus haut/plus bas de N barres)
en 4h = début de mouvement. Trend-following, cousin d'Extension.
**Pourquoi enterrée** : backtest récent ≈ zéro, live catastrophique (WR 26 %
vs 74 % attendu), et 73-95 % de ses trades recouvraient Extension — même pari,
compté deux fois.

### ⚫ Révérence — ENTERRÉE (2026-07-03)
**Le pari** : une contraction extrême du range (NR7 : la barre la plus étroite
depuis 7 jours) précède une expansion. **Testée au filtre dur : aucun edge**
(-6.8R sur 69 trades).

### ⏸️ Fouetté — PARQUÉE
**Le pari** : casser le range des premières minutes après l'ouverture d'une
session (Opening Range Breakout M1). **Pourquoi parquée** : ne tire presque
jamais sur devises/métaux, et sur crypto les coûts M1 sont rédhibitoires.
Re-test prévu (basse priorité) sur l'or à l'ouverture de Londres.

---

## 4. La structure découverte — pourquoi « les métaux »

Ce n'est pas que l'or soit magique. C'est un **entonnoir de coûts** :

| Marché | Coût par trade | Un edge de +0.15R y survit ? |
|---|---|---|
| Or/argent chez GFT | ~0.015R | ✅ largement |
| Devises | ~0.02-0.05R | ✅ oui |
| Crypto (BTC) | ~0.05R | ⚠️ à peine |
| Crypto (petites) | jusqu'à 0.24R | ❌ jamais |

Les edges réalistes faisant +0.1 à +0.3R, **seuls les métaux et les devises
laissent vivre un edge fin**. Nos deux moteurs survivants sont sur métaux non
par hasard, mais parce que c'est le seul terrain où on peut *voir* un edge.
(Les devises ont les coûts, mais aucun de nos paris n'y a montré d'edge brut.)

---

## 5. Comment on décide — le « filtre dur » et le pipeline

Toute idée de stratégie, quelle que soit sa séduction, passe par :

```
1. ÉTUDE DE COÛTS SUR DOSSIER (½ journée, zéro code)
   → le marché visé laisse-t-il vivre un edge fin ? Règles prop firm OK ?
2. WALK-FORWARD sur 18 mois récents (quelques heures de calcul)
   → edge BRUT ≥ 3× le coût du marché ? Stable de fenêtre en fenêtre ?
     Au moins ~2 trades/mois ?
3. DRY-RUN 3 mois (rejeu sur données historiques, zéro argent)
4. SHADOW 2-4 semaines (le signal est calculé en réel mais pas tradé)
5. GO LIVE — ta validation, taille réduite d'abord (×0.25), montée par crans
```

Et dans l'autre sens : une stratégie live est surveillée en continu par
l'audit d'edge (le même outil qui a détecté la mort d'Extension). Si son edge
s'éteint, tu reçois une alerte avec recommandation — jamais d'arrêt automatique.

**Règle des enterrements** : définitif sur la période mesurée, pas éternel.
Les enterrées continuent de générer leurs signaux (sans trader) — si l'une
d'elles se remet à gagner sur le papier, on le verra sans rien payer.

---

## 6. Où on en est, et la route (2026-07-03)

**Aujourd'hui** : le live ne trade que Glissade-or, à taille réduite.
Production ≈ 0,36R/mois. Comptes : FTMO -7 % (micro-taille, sert de point de
mesure), GFT -5,4 % (venue principale, frais 3× plus bas sur l'or).

**La cible** : 1,5R net/mois = un portefeuille de **4 à 6 moteurs** de la
qualité actuelle (~8-12 trades/mois cumulés à ~0.2R). Il en existe 1 validé +
1 candidat + 1 à l'étude → **il en manque 2-4**, que la R&D cherche au rythme
d'un candidat instruit par quinzaine.

**Jalons** : ≥0,8R/mois quand Glissade + Renversé sont validés ensemble →
montée en taille par crans → ≥1,5R/mois → achat d'un nouveau challenge
(décision : pas avant ce seuil).

**Ton rôle** : lancer `/suivi` quand le rappel arrive (2-3 min), un `/bilan`
par semaine (auto-déclenché), répondre aux demandes de décision (questions
fermées avec recommandation). Les urgences te trouvent d'elles-mêmes — toute
notification non-urgente finit par « 👉 rien à faire ».

---

## 7. La carte des documents (si tu veux creuser)

| Document | C'est quoi | Quand le lire |
|---|---|---|
| **Ce guide** | La compréhension d'ensemble | Quand tu veux te resituer |
| `docs/STATUS.md` | Photo de l'état live (comptes, config) | « Qu'est-ce qui tourne là ? » |
| `docs/VALIDATION_CONTRACT.md` | Les règles de décision (filtre dur, ramp) | Avant toute décision de risque |
| `HANDOFF.md` | Le carnet de bord des sessions (TODO, état) | Repère du travail en cours |
| `docs/DECISIONS.md` | L'historique des décisions et leurs pourquoi | « Pourquoi a-t-on fait ça ? » |
| `docs/EXPERIMENT_LOG.md` | Tous les tests et leurs verdicts chiffrés | « A-t-on déjà testé X ? » |
| `docs/audit/couts_reels_*.md` | La table des frais mesurés | Avant de juger un candidat |
| `arabesque/strategies/*/STRATEGY.md` | La fiche technique de chaque stratégie | Détail d'une stratégie |
| `logs/journal/AAAA-MM.md` | Les bilans hebdo/mensuels | Le récit mois par mois |
