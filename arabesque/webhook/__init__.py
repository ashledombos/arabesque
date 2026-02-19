"""
arabesque.webhook — DEPRECATED.

Ce module était utilisé pour recevoir les alertes TradingView via webhook HTTP.
Arabesque utilise désormais cTrader comme source unique de données temps réel.

Conservé temporairement pour :
- Tests manuels (injection de signaux via HTTP)
- Référence historique

Le chemin principal est désormais :
  cTrader ticks → BarAggregator → signal_gen → LiveEngine
"""
# Pas d'import automatique — importer explicitement si nécessaire
