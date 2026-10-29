"""
Suivi de chiffre d'affaires - Garage / Auto-entrepreneur
=========================================================

Application locale (Flask + SQLite) permettant à un garagiste de :
- créer des chantiers (interventions),
- saisir les pièces (prix d'achat + marge -> prix facturé),
- saisir la main d'œuvre,
- calculer le total HT / TTC de chaque chantier,
- suivre le chiffre d'affaires encaissé dans l'année,
- anticiper les seuils du régime micro-entreprise / auto-entrepreneur
  (franchise en base de TVA, plafonds de CA) et visualiser la part
  de CA prélevée par les cotisations sociales,
- ajuster tous les paramètres (marge, TVA, seuils, taux de cotisations)
  depuis un panneau d'administration,
- purger les chantiers et remettre à zéro le compteur de CA.

Lancement :
    pip install -r requirements.txt
    python app.py
Puis ouvrir : http://127.0.0.1:5001
"""

import sqlite3
from datetime import datetime, date
from pathlib import Path
import os

from flask import Flask, render_template, request, redirect, url_for, flash, g

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("DATA_DIR", BASE_DIR)) / "garage.db"

app = Flask(__name__)
app.secret_key = "change-moi-en-production"  # utilisé uniquement pour les messages flash

# ---------------------------------------------------------------------------
# Valeurs par défaut des paramètres (France, régime micro-entreprise, 2026)
# Sources vérifiées en ligne (URSSAF / service-public / presse spécialisée
# auto-entrepreneur, situation 2026). Ces montants évoluent régulièrement :
# vérifiez-les auprès de votre expert-comptable ou de l'URSSAF et ajustez-les
# à tout moment depuis le panneau d'administration.
# ---------------------------------------------------------------------------
DEFAULT_SETTINGS = {
    # Marge par défaut appliquée au prix d'achat des pièces (%)
    "marge_defaut_pct": 30.0,
    # Taux de TVA appliqué par défaut sur les chantiers (%)
    "tva_rate_pct": 20.0,

    # Taux de cotisations sociales URSSAF (% du CA encaissé, régime micro)
    "cotisation_vente_pct": 12.3,   # achat/revente de pièces (BIC vente de marchandises)
    "cotisation_service_pct": 21.2,  # prestations de services artisanales (main d'œuvre)

    # Seuils de franchise en base de TVA (activité mixte : vente + services)
    "seuil_tva_vente_base": 91900.0,
    "seuil_tva_vente_majore": 101000.0,
    "seuil_tva_service_base": 36800.0,
    "seuil_tva_service_majore": 39100.0,

    # Plafonds de chiffre d'affaires du régime micro-entreprise (activité mixte)
    "plafond_global": 188700.0,   # CA total (vente + services)
    "plafond_service": 77700.0,   # dont part "services" (main d'œuvre)

    # Date de référence pour la remise à zéro du compteur de CA (ISO)
    "ca_reset_date": "1970-01-01",
}

SETTINGS_LABELS = {
    "marge_defaut_pct": ("Marge par défaut sur les pièces", "%"),
    "tva_rate_pct": ("Taux de TVA appliqué sur les chantiers", "%"),
    "cotisation_vente_pct": ("Taux de cotisations sociales - vente de pièces", "%"),
    "cotisation_service_pct": ("Taux de cotisations sociales - main d'œuvre / services", "%"),
    "seuil_tva_vente_base": ("Seuil franchise TVA (base) - vente de pièces", "€"),
    "seuil_tva_vente_majore": ("Seuil franchise TVA (majoré) - vente de pièces", "€"),
    "seuil_tva_service_base": ("Seuil franchise TVA (base) - services / main d'œuvre", "€"),
    "seuil_tva_service_majore": ("Seuil franchise TVA (majoré) - services / main d'œuvre", "€"),
    "plafond_global": ("Plafond de CA micro-entreprise (global)", "€"),
    "plafond_service": ("Plafond de CA micro-entreprise (dont services)", "€"),
}

STATUTS = ["brouillon", "facture", "encaisse"]
STATUT_LABELS = {"brouillon": "Brouillon", "facture": "Facturé", "encaisse": "Encaissé"}


# ---------------------------------------------------------------------------
# Base de données
# ---------------------------------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute("""
        CREATE TABLE IF NOT EXISTS chantiers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT NOT NULL,
            client TEXT,
            date_creation TEXT NOT NULL,
            date_encaissement TEXT,
            statut TEXT NOT NULL DEFAULT 'brouillon'
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS pieces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chantier_id INTEGER NOT NULL,
            nom TEXT NOT NULL,
            prix_achat REAL NOT NULL,
            marge_pct REAL NOT NULL,
            prix_facture REAL NOT NULL,
            FOREIGN KEY (chantier_id) REFERENCES chantiers (id) ON DELETE CASCADE
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS main_oeuvre (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chantier_id INTEGER NOT NULL,
            description TEXT,
            heures REAL NOT NULL,
            taux_horaire REAL NOT NULL,
            montant REAL NOT NULL,
            FOREIGN KEY (chantier_id) REFERENCES chantiers (id) ON DELETE CASCADE
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value REAL NOT NULL
        )
    """)
    for key, value in DEFAULT_SETTINGS.items():
        db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
    db.commit()
    db.close()


def get_settings():
    """Renvoie les paramètres sous forme de dict {key: float}, sauf ca_reset_date (str)."""
    db = get_db()
    rows = db.execute("SELECT key, value FROM settings").fetchall()
    settings = {row["key"]: row["value"] for row in rows}
    # ca_reset_date est stocké comme un nombre de jours depuis epoch pour rester REAL ;
    # on le garde ici en texte ISO stocké séparément pour plus de simplicité de lecture.
    return settings


def get_ca_reset_date():
    db = get_db()
    row = db.execute("SELECT value FROM settings WHERE key = 'ca_reset_date_ts'").fetchone()
    if row is None:
        return date(1970, 1, 1)
    return date.fromordinal(int(row["value"]))


def set_ca_reset_date(d: date):
    db = get_db()
    db.execute(
        "INSERT INTO settings (key, value) VALUES ('ca_reset_date_ts', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (d.toordinal(),),
    )
    db.commit()


# ---------------------------------------------------------------------------
# Calculs métier
# ---------------------------------------------------------------------------
def compute_chantier_totals(chantier_id):
    db = get_db()
    settings = get_settings()
    tva_rate = settings.get("tva_rate_pct", 20.0)

    pieces = db.execute(
        "SELECT * FROM pieces WHERE chantier_id = ? ORDER BY id", (chantier_id,)
    ).fetchall()
    mo = db.execute(
        "SELECT * FROM main_oeuvre WHERE chantier_id = ? ORDER BY id", (chantier_id,)
    ).fetchall()

    total_pieces_achat = sum(p["prix_achat"] for p in pieces)
    total_pieces_facture = sum(p["prix_facture"] for p in pieces)
    total_mo = sum(m["montant"] for m in mo)

    total_ht = total_pieces_facture + total_mo
    total_ttc = total_ht * (1 + tva_rate / 100.0)
    marge_brute = total_pieces_facture - total_pieces_achat

    return {
        "pieces": pieces,
        "mo": mo,
        "total_pieces_achat": total_pieces_achat,
        "total_pieces_facture": total_pieces_facture,
        "total_mo": total_mo,
        "total_ht": total_ht,
        "total_ttc": total_ttc,
        "tva_montant": total_ttc - total_ht,
        "tva_rate": tva_rate,
        "marge_brute": marge_brute,
    }


def compute_ca_tracker(year=None):
    """Calcule le CA encaissé (statut = 'encaisse') sur l'année donnée,
    en distinguant la part 'vente de pièces' et la part 'services / main d'œuvre',
    et compare aux seuils en vigueur."""
    db = get_db()
    settings = get_settings()
    if year is None:
        year = datetime.now().year

    reset_date = get_ca_reset_date()

    chantiers = db.execute(
        "SELECT * FROM chantiers WHERE statut = 'encaisse'"
    ).fetchall()

    ca_pieces = 0.0
    ca_service = 0.0

    for c in chantiers:
        ref_date_str = c["date_encaissement"] or c["date_creation"]
        try:
            ref_date = datetime.fromisoformat(ref_date_str).date()
        except ValueError:
            continue
        if ref_date.year != year:
            continue
        if ref_date < reset_date:
            continue
        totals = compute_chantier_totals(c["id"])
        ca_pieces += totals["total_pieces_facture"]
        ca_service += totals["total_mo"]

    ca_total = ca_pieces + ca_service

    cotis_vente = ca_pieces * settings["cotisation_vente_pct"] / 100.0
    cotis_service = ca_service * settings["cotisation_service_pct"] / 100.0
    cotisations_total = cotis_vente + cotis_service

    part_cotisations_pct = (cotisations_total / ca_total * 100.0) if ca_total > 0 else 0.0
    net_apres_cotisations = ca_total - cotisations_total

    def pct(value, seuil):
        return min(100.0, (value / seuil * 100.0)) if seuil else 0.0

    seuils = {
        "tva_vente_base": {
            "seuil": settings["seuil_tva_vente_base"],
            "valeur": ca_pieces,
            "pct": pct(ca_pieces, settings["seuil_tva_vente_base"]),
            "depasse": ca_pieces >= settings["seuil_tva_vente_base"],
        },
        "tva_vente_majore": {
            "seuil": settings["seuil_tva_vente_majore"],
            "valeur": ca_pieces,
            "pct": pct(ca_pieces, settings["seuil_tva_vente_majore"]),
            "depasse": ca_pieces >= settings["seuil_tva_vente_majore"],
        },
        "tva_service_base": {
            "seuil": settings["seuil_tva_service_base"],
            "valeur": ca_service,
            "pct": pct(ca_service, settings["seuil_tva_service_base"]),
            "depasse": ca_service >= settings["seuil_tva_service_base"],
        },
        "tva_service_majore": {
            "seuil": settings["seuil_tva_service_majore"],
            "valeur": ca_service,
            "pct": pct(ca_service, settings["seuil_tva_service_majore"]),
            "depasse": ca_service >= settings["seuil_tva_service_majore"],
        },
        "plafond_global": {
            "seuil": settings["plafond_global"],
            "valeur": ca_total,
            "pct": pct(ca_total, settings["plafond_global"]),
            "depasse": ca_total >= settings["plafond_global"],
        },
        "plafond_service": {
            "seuil": settings["plafond_service"],
            "valeur": ca_service,
            "pct": pct(ca_service, settings["plafond_service"]),
            "depasse": ca_service >= settings["plafond_service"],
        },
    }

    return {
        "year": year,
        "ca_pieces": ca_pieces,
        "ca_service": ca_service,
        "ca_total": ca_total,
        "cotis_vente": cotis_vente,
        "cotis_service": cotis_service,
        "cotisations_total": cotisations_total,
        "part_cotisations_pct": part_cotisations_pct,
        "net_apres_cotisations": net_apres_cotisations,
        "seuils": seuils,
        "reset_date": reset_date,
    }


# ---------------------------------------------------------------------------
# Routes - Tableau de bord / chantiers
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    db = get_db()
    chantiers = db.execute(
        "SELECT * FROM chantiers ORDER BY date_creation DESC, id DESC"
    ).fetchall()

    chantiers_data = []
    for c in chantiers:
        totals = compute_chantier_totals(c["id"])
        chantiers_data.append({"chantier": c, "totals": totals})

    tracker = compute_ca_tracker()

    return render_template(
        "index.html",
        chantiers_data=chantiers_data,
        tracker=tracker,
        statut_labels=STATUT_LABELS,
    )


@app.route("/chantier/nouveau", methods=["GET", "POST"])
def chantier_nouveau():
    if request.method == "POST":
        nom = request.form.get("nom", "").strip()
        client = request.form.get("client", "").strip()
        if not nom:
            flash("Le nom du chantier est obligatoire.", "error")
            return redirect(url_for("chantier_nouveau"))
        db = get_db()
        db.execute(
            "INSERT INTO chantiers (nom, client, date_creation, statut) VALUES (?, ?, ?, ?)",
            (nom, client, date.today().isoformat(), "brouillon"),
        )
        db.commit()
        new_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        flash("Chantier créé.", "success")
        return redirect(url_for("chantier_detail", chantier_id=new_id))
    return render_template("chantier_nouveau.html")


@app.route("/chantier/<int:chantier_id>")
def chantier_detail(chantier_id):
    db = get_db()
    chantier = db.execute("SELECT * FROM chantiers WHERE id = ?", (chantier_id,)).fetchone()
    if chantier is None:
        flash("Chantier introuvable.", "error")
        return redirect(url_for("index"))
    totals = compute_chantier_totals(chantier_id)
    settings = get_settings()
    return render_template(
        "chantier.html",
        chantier=chantier,
        totals=totals,
        settings=settings,
        statuts=STATUTS,
        statut_labels=STATUT_LABELS,
    )


@app.route("/chantier/<int:chantier_id>/statut", methods=["POST"])
def chantier_statut(chantier_id):
    statut = request.form.get("statut")
    db = get_db()
    if statut not in STATUTS:
        flash("Statut invalide.", "error")
    else:
        date_encaissement = None
        if statut == "encaisse":
            date_encaissement = date.today().isoformat()
        db.execute(
            "UPDATE chantiers SET statut = ?, date_encaissement = COALESCE(?, date_encaissement) WHERE id = ?",
            (statut, date_encaissement, chantier_id),
        )
        db.commit()
        flash("Statut mis à jour.", "success")
    return redirect(url_for("chantier_detail", chantier_id=chantier_id))


@app.route("/chantier/<int:chantier_id>/supprimer", methods=["POST"])
def chantier_supprimer(chantier_id):
    db = get_db()
    db.execute("DELETE FROM pieces WHERE chantier_id = ?", (chantier_id,))
    db.execute("DELETE FROM main_oeuvre WHERE chantier_id = ?", (chantier_id,))
    db.execute("DELETE FROM chantiers WHERE id = ?", (chantier_id,))
    db.commit()
    flash("Chantier supprimé.", "success")
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Routes - Pièces
# ---------------------------------------------------------------------------
@app.route("/chantier/<int:chantier_id>/piece/ajouter", methods=["POST"])
def piece_ajouter(chantier_id):
    settings = get_settings()
    nom = request.form.get("nom", "").strip()
    try:
        prix_achat = float(request.form.get("prix_achat", "0").replace(",", "."))
    except ValueError:
        prix_achat = 0.0

    marge_raw = request.form.get("marge_pct", "").strip()
    if marge_raw == "":
        marge_pct = settings["marge_defaut_pct"]
    else:
        try:
            marge_pct = float(marge_raw.replace(",", "."))
        except ValueError:
            marge_pct = settings["marge_defaut_pct"]

    prix_facture_raw = request.form.get("prix_facture", "").strip()
    if prix_facture_raw:
        try:
            prix_facture = float(prix_facture_raw.replace(",", "."))
        except ValueError:
            prix_facture = prix_achat * (1 + marge_pct / 100.0)
    else:
        prix_facture = prix_achat * (1 + marge_pct / 100.0)

    if not nom:
        flash("Le nom de la pièce est obligatoire.", "error")
        return redirect(url_for("chantier_detail", chantier_id=chantier_id))

    db = get_db()
    db.execute(
        "INSERT INTO pieces (chantier_id, nom, prix_achat, marge_pct, prix_facture) "
        "VALUES (?, ?, ?, ?, ?)",
        (chantier_id, nom, prix_achat, marge_pct, prix_facture),
    )
    db.commit()
    flash("Pièce ajoutée.", "success")
    return redirect(url_for("chantier_detail", chantier_id=chantier_id))


@app.route("/piece/<int:piece_id>/supprimer", methods=["POST"])
def piece_supprimer(piece_id):
    db = get_db()
    row = db.execute("SELECT chantier_id FROM pieces WHERE id = ?", (piece_id,)).fetchone()
    db.execute("DELETE FROM pieces WHERE id = ?", (piece_id,))
    db.commit()
    if row:
        return redirect(url_for("chantier_detail", chantier_id=row["chantier_id"]))
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Routes - Main d'œuvre
# ---------------------------------------------------------------------------
@app.route("/chantier/<int:chantier_id>/mo/ajouter", methods=["POST"])
def mo_ajouter(chantier_id):
    description = request.form.get("description", "").strip()
    try:
        heures = float(request.form.get("heures", "0").replace(",", "."))
    except ValueError:
        heures = 0.0
    try:
        taux_horaire = float(request.form.get("taux_horaire", "0").replace(",", "."))
    except ValueError:
        taux_horaire = 0.0

    montant_raw = request.form.get("montant", "").strip()
    if montant_raw:
        try:
            montant = float(montant_raw.replace(",", "."))
        except ValueError:
            montant = heures * taux_horaire
    else:
        montant = heures * taux_horaire

    db = get_db()
    db.execute(
        "INSERT INTO main_oeuvre (chantier_id, description, heures, taux_horaire, montant) "
        "VALUES (?, ?, ?, ?, ?)",
        (chantier_id, description, heures, taux_horaire, montant),
    )
    db.commit()
    flash("Main d'œuvre ajoutée.", "success")
    return redirect(url_for("chantier_detail", chantier_id=chantier_id))


@app.route("/mo/<int:mo_id>/supprimer", methods=["POST"])
def mo_supprimer(mo_id):
    db = get_db()
    row = db.execute("SELECT chantier_id FROM main_oeuvre WHERE id = ?", (mo_id,)).fetchone()
    db.execute("DELETE FROM main_oeuvre WHERE id = ?", (mo_id,))
    db.commit()
    if row:
        return redirect(url_for("chantier_detail", chantier_id=row["chantier_id"]))
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Routes - Administration
# ---------------------------------------------------------------------------
@app.route("/admin", methods=["GET"])
def admin():
    settings = get_settings()
    reset_date = get_ca_reset_date()
    return render_template(
        "admin.html",
        settings=settings,
        labels=SETTINGS_LABELS,
        reset_date=reset_date,
    )


@app.route("/admin/parametres", methods=["POST"])
def admin_parametres():
    db = get_db()
    for key in SETTINGS_LABELS:
        raw = request.form.get(key)
        if raw is None:
            continue
        try:
            value = float(raw.replace(",", "."))
        except ValueError:
            continue
        db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
    db.commit()
    flash("Paramètres mis à jour.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/purger", methods=["POST"])
def admin_purger():
    confirmation = request.form.get("confirmation", "")
    if confirmation != "PURGER":
        flash("Purge annulée : confirmation incorrecte.", "error")
        return redirect(url_for("admin"))
    db = get_db()
    db.execute("DELETE FROM pieces")
    db.execute("DELETE FROM main_oeuvre")
    db.execute("DELETE FROM chantiers")
    db.commit()
    flash("Tous les chantiers ont été supprimés de la base de données.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/reset_ca", methods=["POST"])
def admin_reset_ca():
    confirmation = request.form.get("confirmation", "")
    if confirmation != "RESET":
        flash("Remise à zéro annulée : confirmation incorrecte.", "error")
        return redirect(url_for("admin"))
    set_ca_reset_date(date.today())
    flash(
        "Le compteur de chiffre d'affaires a été remis à zéro à la date du jour. "
        "L'historique des chantiers est conservé.",
        "success",
    )
    return redirect(url_for("admin"))


@app.route("/admin/reset_defaults", methods=["POST"])
def admin_reset_defaults():
    db = get_db()
    for key, value in DEFAULT_SETTINGS.items():
        if key == "ca_reset_date":
            continue
        db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
    db.commit()
    flash("Paramètres réinitialisés aux valeurs par défaut 2026.", "success")
    return redirect(url_for("admin"))


# ---------------------------------------------------------------------------
# Filtres Jinja
# ---------------------------------------------------------------------------
@app.template_filter("euros")
def euros_filter(value):
    try:
        return f"{value:,.2f} €".replace(",", " ").replace(".", ",")
    except (TypeError, ValueError):
        return value


@app.template_filter("pct")
def pct_filter(value):
    try:
        return f"{value:,.1f} %".replace(".", ",")
    except (TypeError, ValueError):
        return value


if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="127.0.0.1", port=5001)

init_db()