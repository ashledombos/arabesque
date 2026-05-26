# Incident GFT position state - 2026-05-26

## Resume

Deux defauts TradeLocker ont fausse le suivi live de positions Extension GFT :

1. `AUDJPY` a ete declaree fermee alors qu'elle restait ouverte broker-side.
2. `XAUUSD` rempli depuis un ordre pending n'a jamais ete enregistre comme
   position suivie, puis a ete auto-ferme comme orphelin.

L'engine a ete arrete pendant la correction, apres cloture manuelle controlee
de l'exposition `AUDJPY`. Aucune position n'etait ouverte lors de l'arret.

## Timeline UTC

| Heure | Evenement |
|---|---|
| 04:30:04 | XAUUSD GFT SHORT fill @ 4532.34, position `504403158265746742`, issue de l'ordre pending `504403158268137324` |
| 15:04:01 | XAUUSD auto-close orpheline @ 4511.17, non journalisee au moment de l'execution |
| 15:00:06 | AUDJPY GFT LONG fill @ 114.205, position `504403158265765438` |
| 18:54:12 | `get_positions()` TradeLocker retourne HTTP 429 |
| 18:54:14 | Faux exit AUDJPY ecrit @ 114.205 ; le monitor retire une position toujours ouverte |
| 21:06 env. | Position AUDJPY confirmee ouverte hors monitor ; cloture operateur autorisee et executee @ 114.167 |
| 21:11:41 | Reconciliation retrouve l'exit ETHUSD manquant @ -1.028R |
| 21:11:43 | Reconciliation corrigee ecrit le vrai exit AUDJPY @ -0.201R |

## Causes techniques

### AUDJPY - etat inconnu traite comme vide

`arabesque/broker/tradelocker.py::get_positions()` attrapait toute exception,
y compris `HTTP 429 Too Many Requests`, et retournait `[]`.
`LivePositionMonitor.reconcile()` interprete legitimement `[]` comme une
liste broker valide sans la position ; il a donc traite l'exposition comme
fermee.

La corroboration etait elle aussi invalide : `get_closed_position_detail()`
prenait `iloc[-1]` des ordres lies. TradeLocker renvoyait les ordres
newest-first ; la ligne selectionnee etait l'ordre d'ouverture `BUY Filled`
@ 114.205, pas une execution de sortie.

### XAUUSD - `order_id != position_id`

Le poll des pending fills cherchait une position dont l'identifiant etait
egal a l'identifiant de l'ordre. Chez TradeLocker, l'ordre
`504403158268137324` a cree la position distincte `504403158265746742`.
Le fill est reste invisible, puis la position a ete detectee comme orpheline
pendant qu'une autre position etait trackee.

### Restart en position GFT - protection non exposee sur `Position`

Les payloads TradeLocker observes montrent `sl=None tp=None` sur une position
qui possede pourtant des ordres STOP/LIMIT protecteurs lies. Une reprise apres
restart ne doit pas abandonner le monitoring uniquement a cause de ces champs.

## Corrections appliquees

- Erreur `get_positions()` TradeLocker : leve desormais `ConnectionError` ;
  elle n'est plus equivalente a une reponse vide.
- Detail exit TradeLocker : exige deux executions remplies opposees et prend
  la plus recente ; une simple entree ne confirme jamais une cloture.
- Pending fills : nouveau hook broker `resolve_position_id_from_order_id()` ;
  GFT enregistre et monitore la position reelle apres fill.
- Confirmation post-placement indisponible : l'ordre reste desormais
  `pending/unconfirmed` et sera repolle, au lieu de disparaitre du suivi.
- Reprise startup : nouveau hook `get_position_protection()` pour recuperer
  les ordres STOP/LIMIT lies quand le payload position masque SL/TP.
- Lifecycle health : `_account_refresh_loop`, `_reconcile_loop` et snapshot
  sont programmes apres `self._running=True`; le health report ne meurt plus
  silencieusement au demarrage. Logs d'exception avec traceback.

## Corrections de journal

- Faux exit AUDJPY renomme `exit_invalidated_by_bug`.
- Vrai exit AUDJPY reconcilie : `114.167`, `-0.201R`.
- Exit ETHUSD manquant reconcilie : `-1.028R`.
- Trade XAUUSD restaure depuis l'historique broker :
  entry `4532.34`, exit `4511.17`, `+1.178R`, `MFE=1.79R`,
  raison `operator_auto_close_orphan_by_bug`.

Le trade XAUUSD represente le resultat reel execute mais sa sortie est un effet
du bug operationnel ; il ne faut pas l'interpreter comme comportement nominal
de la strategie.

## Suivi restant

- Auditer la pression d'appels TradeLocker afin de reduire le risque de 429.
- Consommer/implementer la Task #39 cTrader trading channel (`SCOPE_TASK_39...`).
- Observer le prochain health report apres remise en service.
