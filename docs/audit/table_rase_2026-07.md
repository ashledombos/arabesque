# Table rase 2026-07 — premier run (2026-07-18)

Skill `/table-rase` (actée opérateur 07-18). Contre-expertise à froid par
sous-agent SANS accès aux conclusions (DECISIONS/HANDOFF/EXPERIMENT_LOG/audit/
STRATEGY/CLAUDE.md interdits) : données brutes + code + journaux seulement.

## Phase 1 — Ce que la contre-expertise a conclu seule (résumé)

1. Cabriole et Extension MORTES statistiquement en live (IC99 entièrement
   négatifs : [−0,90;−0,49] n=43 et [−0,45;−0,16] n=128) ; pertes identiques
   sur les 2 brokers → stratégie, pas exécution. Glissade INDÉTERMINÉE
   (XAUUSD n=8, Exp −0,03R = bruit). Fouetté jamais tradée.
2. Les coupes de config CORRESPONDENT aux données (chaque kill a son signal
   négatif dans le journal) — cohérence config↔réalité confirmée.
3. Live global : −72,2R cumulés, aucune stratégie n'a démontré d'edge live.
   « Défendable comme collecte de données, pas comme exploitation » — ce qui
   est exactement le régime de validation déclaré (0,45 %×0,25).
4. Adage : Exp +0,072R/session NON significatif à 95 % ; edge concentré sur
   3 trimestres ; 2026Q2 NÉGATIF ; série AU point de DD max historique
   (pic 05-07, depuis : 49 sessions, −15,8R, WR 28,6 %). Verdict à froid :
   PAS de live maintenant, attendre sortie de DD + 30-50 sessions ombre.
5. À vérifier en priorité selon elle : processus de validation (3 WF PASS →
   live −72R), coûts réels dans les rejeux, fiabilité canal broker avant
   stratégie nocturne, corr result_r/pnl_cash 0,47.

## Phase 2 — Confrontation (a=dossier tient, b=elle a vu juste, c=indécidable)

| Divergence | Verdict |
|---|---|
| « Aucun edge live démontré » vs S1 (+0,24R net) | **(c)** — n live trop petit des deux côtés. Critère qui tranche : `portefeuille_ramp` n≥30 (déjà en place). |
| WR Adage 47,4 % vs dérogation fondée sur 58,6 % | **(a)** — convention pré-déclarée au protocole gelé (« WR(> −0,25R) ≥ 55 % ») : 58,5 % recalculé, 47,4 % = strict >0. Mêmes données. ⚠️ Nuance consignée : pour une stratégie SANS BE, la convention boussole compte les petites pertes [−0,25;0] comme wins — plus généreuse que pour les stratégies à BE. Dérogation valide (pré-écrite), nuance à garder pour le jalon 5. |
| « Latence de kill Extension a coûté ~la moitié du DD » (déjà négative fin avril, coupée 07-03) | **(b)** — vrai. MAIS l'outillage actuel (edge_audit drift_structurel, baselines rolling) a été construit PENDANT cet épisode et a tiré 4× selon le state log ; le coût était la latence de DÉCISION, pas de détection. Leçon déjà encaissée (I5) ; pas d'action nouvelle. |
| « Le processus de validation surestime l'edge, rejouer aux coûts réels » | **(a)** — c'est littéralement le banc 07-01/03 (concentration) : la contre-expertise a réinventé notre correctif indépendamment. Convergence rassurante sur la méthode. |
| « 3 états contradictoires pour Fouetté dans settings.yaml » (assignments + rodage + exclusions) | **(b)** — vrai, config zombie inerte mais illisible. Proposition de nettoyage ci-dessous (validation opérateur, fichier live). |
| « 13 % reconciled = chaîne instable » | **(a)** — root-causé (restarts watchdog 06-24, incident 05-07 corrigé) ; invariants per-broker verts depuis. Sa remarque « auditer le canal avant une stratégie tenue 9 h la nuit » est couverte par le design jalon 5 (mur monitor, retry, jamais d'exit inventé). |
| Adage « pas de live maintenant » | **(a)** — même conclusion que le pipeline (jalon 5 seulement après revue n≥30, tripwire armé). Sa lecture renforce S5 : la fenêtre courante est LE test. |

## Phase 3 — Falsification ciblée (instruments gelés relus à date)

- **S2 (Extension morte)** : edge audit J-30 → 9 derniers trades live pré-coupe
  −0,605R ; BT même fenêtre +0,001R (baseline −0,082R). **Tampon 07-18.**
- **S1 (Glissade seul edge)** : replay live vs théorie depuis 05-16 → drift
  −0,429R porté à 100 % par BTCUSD (coupé 07-03) ; branche XAUUSD sans drift
  (n=1, Δ=−0,20R = plancher BE). Edge non re-jugeable (n live insuffisant).
  **Tampon 07-18 « rien ne le contredit », re-mesure n≥30.**
- **S5 (creux Adage = creux de distribution)** : série à **0,36R du tripwire**
  (−15,84R vs −16,2R) ; 90 derniers jours −9,1R. RESTE EN TEST — le seuil
  pré-enregistré décidera, ne pas anticiper dans un sens ou l'autre.
- **Invariants exécution** : verts per-broker (run du jour).

## Phase 4 — Méta-audit (correctifs APPLIQUÉS ce run)

1. **CLAUDE.md contaminait chaque session** : l'arbre stratégies affichait des
   « WF PASS » morts depuis des semaines (Fouetté 4/4, Cabriole 6/6, Révérence)
   et la section Extension se lisait comme courante. → **Corrigé** (statuts
   réels + renvoi HYPOTHESES.md). C'est le constat le plus important du run :
   le fichier réinjecté à CHAQUE session était le plus périmé.
2. **Lecture state log cassable** : le timer écrit des lignes `reminder_sent`
   dans `maintenance_state.jsonl` ; « lire la dernière ligne » (skill /suivi)
   → KeyError réel le 07-17. → **Corrigé** (filtrer les lignes à champ `event`).
3. **Fenêtre bilan semaine trop étroite** (lundi <12h ; le passage réel du
   14/07 à 16:15 a dû forcer le bilan). → **Élargie** (lundi entier).
4. **Triggers zombies** : `cabriole_gft_unblock` / `extension_gft_drift`
   inatteignables (stratégies coupées). → **Marqués 💤 DORMANT** (réveil aux
   réhabilitations d'octobre).
5. Volumétrie rappels : 208 `reminder_sent` pour 59 passages /suivi (3,5×) —
   l'essentiel date de l'ère cooldown 3h (passé à 12h le 06-29). Surveiller au
   prochain run ; rien à changer aujourd'hui.
6. **Cette skill** : la dérivation à froid a produit du NEUF (quantification
   latence de kill, découpe trimestrielle Adage, config zombie, corr
   result_r/pnl_cash) ET a reconvergé sur les décisions clés — signal que le
   dossier tient et que la phase vaut son coût. Reconduite telle quelle.

## Re-tests proposés (protocole gelé au moment du go opérateur)

1. **C1-C3 (calibration BE 0,3/0,20 + risk 0,40 % + tick-TSL)** : preuve datée
   de l'ère Extension (morte) — re-valider sur le parc survivant.
   Instrument : backtest CLI Glissade XAUUSD 20 mois avec/sans BE, fenêtre
   récente 12 m. Kill pré-écrit : le BE n'améliore plus Exp ET WR.
2. **Nettoyage settings.yaml** (Fouetté retiré d'assignments+rodage, intention
   risk 0,45×0,25 commentée en clair) : inerte fonctionnellement, fichier
   live → go opérateur, 10 min.

## Prochain run : fenêtre du 14 au 17 août 2026 (`/suivi` le signalera).
