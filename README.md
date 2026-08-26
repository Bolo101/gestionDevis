# Suivi Garage — Chiffre d'affaires & seuils micro-entreprise

Application web locale (Flask + SQLite) pour un garagiste en micro-entreprise / auto-entrepreneur,
permettant de gérer les chantiers, calculer le prix facturé des pièces avec une marge ajustable,
suivre le total HT/TTC de chaque chantier, et anticiper les seuils fiscaux et sociaux du régime
micro-entreprise.

## Installation

```bash
python -m venv venv
source venv/bin/activate   # Windows : venv\Scripts\activate
pip install -r requirements.txt
```

## Lancement

```bash
python app.py
```

Puis ouvrez votre navigateur à l'adresse : http://127.0.0.1:5000

La base de données `garage.db` (SQLite) est créée automatiquement au premier lancement, dans le
même dossier que `app.py`.

## Fonctionnalités

- **Chantiers** : création, consultation, suppression, statut (brouillon / facturé / encaissé).
- **Pièces** : nom, prix d'achat, marge (%) — utilise la marge par défaut si non renseignée — et
  prix facturé calculé automatiquement (modifiable manuellement si besoin).
- **Main d'œuvre** : description, heures, taux horaire, montant (calculé automatiquement ou
  modifiable manuellement).
- **Totaux** : total HT, montant de TVA, total TTC, marge brute réalisée sur les pièces.
- **Tracker de chiffre d'affaires** : CA encaissé dans l'année (basé sur les chantiers au statut
  "Encaissé"), séparé en part "vente de pièces" et part "main d'œuvre / services", avec :
  - la part de CA prélevée par les cotisations sociales,
  - le revenu net estimatif après cotisations,
  - la position par rapport aux seuils de franchise en base de TVA (vente et services, seuils
    de base et majorés),
  - la position par rapport aux plafonds de chiffre d'affaires du régime micro-entreprise
    (plafond global et plafond "services").
- **Panneau d'administration** :
  - ajustement de la marge par défaut et du taux de TVA,
  - ajustement des taux de cotisations sociales (vente / services),
  - ajustement de tous les seuils (franchise TVA, plafonds de CA),
  - réinitialisation aux valeurs par défaut 2026,
  - remise à zéro du compteur de CA (l'historique des chantiers est conservé, seule la date de
    départ du calcul du CA est réinitialisée),
  - purge complète des chantiers (suppression définitive, avec confirmation textuelle).

## Valeurs préremplies (France, régime micro-entreprise, situation 2026)

Ces valeurs sont **indicatives** et **modifiables** à tout moment dans le panneau
d'administration. Elles évoluent régulièrement — vérifiez-les auprès de votre expert-comptable
ou de l'URSSAF avant de vous en servir pour vos déclarations réelles.

| Paramètre | Valeur par défaut |
|---|---|
| Marge par défaut sur les pièces | 30 % |
| Taux de TVA | 20 % |
| Cotisations sociales — vente de pièces (BIC vente de marchandises) | 12,3 % |
| Cotisations sociales — main d'œuvre (BIC prestations de services artisanales) | 21,2 % |
| Franchise TVA vente — seuil de base | 91 900 € |
| Franchise TVA vente — seuil majoré | 101 000 € |
| Franchise TVA services — seuil de base | 36 800 € |
| Franchise TVA services — seuil majoré | 39 100 € |
| Plafond CA micro-entreprise — global (activité mixte) | 188 700 € |
| Plafond CA micro-entreprise — dont services | 77 700 € |

## Notes de fonctionnement

- Le chiffre d'affaires n'est comptabilisé dans le tracker que pour les chantiers passés au
  statut **"Encaissé"** (le régime micro-entreprise fonctionne en encaissements réels, pas en
  facturation). La date prise en compte est la date d'encaissement (renseignée automatiquement au
  moment du changement de statut).
- Un chantier "mixte" (pièces + main d'œuvre) alimente les deux compteurs (vente et services)
  séparément, ce qui permet de suivre correctement les deux jeux de seuils.
- "Purger les chantiers" supprime définitivement toutes les données ; "Remettre à zéro le CA" ne
  supprime rien mais démarre un nouveau comptage à partir de la date du jour (utile en début
  d'année civile par exemple).
