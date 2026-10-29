"""
Suivi de chiffre d'affaires - Garage / Auto-entrepreneur
=========================================================
"""

import sqlite3
from datetime import datetime, date
from pathlib import Path
import os
from io import BytesIO

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, g, send_file, abort
)
from fpdf import FPDF

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("DATA_DIR", BASE_DIR)) / "garage.db"
LOGO_PATH = Path(os.environ.get("DATA_DIR", BASE_DIR)) / "logo.png"

app = Flask(__name__)
app.secret_key = "change-moi-en-production"

# ---------------------------------------------------------------------------
# Paramètres par défaut
# ---------------------------------------------------------------------------
DEFAULT_SETTINGS = {
    "marge_defaut_pct": 30.0,
    "tva_rate_pct": 20.0,
    "cotisation_vente_pct": 12.3,
    "cotisation_service_pct": 21.2,
    "seuil_tva_vente_base": 91900.0,
    "seuil_tva_vente_majore": 101000.0,
    "seuil_tva_service_base": 36800.0,
    "seuil_tva_service_majore": 39100.0,
    "plafond_global": 188700.0,
    "plafond_service": 77700.0,
    "mo_taux_1": 45.0,
    "mo_taux_2": 55.0,
    "mo_taux_3": 65.0,
    # Infos entreprise (stockées en texte via settings texte)
    "entreprise_nom": "Mon Garage",
    "entreprise_siret": "",
    "entreprise_siren": "",
    "entreprise_adresse": "",
    "entreprise_cp_ville": "",
    "entreprise_tel": "",
    "entreprise_email": "",
    "ca_reset_date": "1970-01-01",
}

# Les clés texte (entreprise_*) sont stockées comme REAL dans la table settings
# pour rester compatible. On les gère via une table séparée plus propre.
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
    "mo_taux_1": ("Taux horaire prédéfini n°1", "€/h"),
    "mo_taux_2": ("Taux horaire prédéfini n°2", "€/h"),
    "mo_taux_3": ("Taux horaire prédéfini n°3", "€/h"),
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
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT NOT NULL,
            adresse TEXT,
            code_postal TEXT,
            ville TEXT,
            telephone TEXT,
            email TEXT,
            notes TEXT,
            date_creation TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS chantiers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT NOT NULL,
            client_id INTEGER,
            client TEXT,
            date_creation TEXT NOT NULL,
            date_encaissement TEXT,
            statut TEXT NOT NULL DEFAULT 'brouillon',
            FOREIGN KEY (client_id) REFERENCES clients (id) ON DELETE SET NULL
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
    db.execute("""
        CREATE TABLE IF NOT EXISTS settings_text (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    # Settings numériques
    for key, value in DEFAULT_SETTINGS.items():
        if key.startswith("entreprise_") or key == "ca_reset_date":
            continue
        db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))

    # Settings texte (entreprise)
    text_defaults = {
        "entreprise_nom": "Mon Garage",
        "entreprise_siret": "",
        "entreprise_siren": "",
        "entreprise_adresse": "",
        "entreprise_cp_ville": "",
        "entreprise_tel": "",
        "entreprise_email": "",
    }
    for key, value in text_defaults.items():
        db.execute(
            "INSERT OR IGNORE INTO settings_text (key, value) VALUES (?, ?)",
            (key, value),
        )

    db.commit()
    db.close()


def get_settings():
    db = get_db()
    rows = db.execute("SELECT key, value FROM settings").fetchall()
    return {row["key"]: row["value"] for row in rows}


def get_settings_text():
    db = get_db()
    rows = db.execute("SELECT key, value FROM settings_text").fetchall()
    return {row["key"]: row["value"] for row in rows}


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
    total_heures = sum(m["heures"] for m in mo)

    total_ht = total_pieces_facture + total_mo
    total_ttc = total_ht * (1 + tva_rate / 100.0)
    marge_brute = total_pieces_facture - total_pieces_achat

    return {
        "pieces": pieces,
        "mo": mo,
        "total_pieces_achat": total_pieces_achat,
        "total_pieces_facture": total_pieces_facture,
        "total_mo": total_mo,
        "total_heures": total_heures,
        "total_ht": total_ht,
        "total_ttc": total_ttc,
        "tva_montant": total_ttc - total_ht,
        "tva_rate": tva_rate,
        "marge_brute": marge_brute,
    }


def compute_ca_tracker(year=None):
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
        if ref_date.year != year or ref_date < reset_date:
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
# PDF Facture
# ---------------------------------------------------------------------------
class FacturePDF(FPDF):
    def header(self):
        pass

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")


def generer_pdf_facture(chantier, client, totals, entreprise):
    pdf = FacturePDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    # Logo
    if LOGO_PATH.exists():
        try:
            pdf.image(str(LOGO_PATH), x=10, y=8, w=35)
        except Exception:
            pass

    # Infos entreprise (droite)
    pdf.set_xy(120, 10)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 6, entreprise.get("entreprise_nom", "Mon Garage"), ln=1)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_x(120)
    if entreprise.get("entreprise_adresse"):
        pdf.cell(0, 5, entreprise["entreprise_adresse"], ln=1)
        pdf.set_x(120)
    if entreprise.get("entreprise_cp_ville"):
        pdf.cell(0, 5, entreprise["entreprise_cp_ville"], ln=1)
        pdf.set_x(120)
    if entreprise.get("entreprise_siret"):
        pdf.cell(0, 5, f"SIRET : {entreprise['entreprise_siret']}", ln=1)
        pdf.set_x(120)
    if entreprise.get("entreprise_siren"):
        pdf.cell(0, 5, f"SIREN : {entreprise['entreprise_siren']}", ln=1)
        pdf.set_x(120)
    if entreprise.get("entreprise_tel"):
        pdf.cell(0, 5, f"Tél : {entreprise['entreprise_tel']}", ln=1)
        pdf.set_x(120)
    if entreprise.get("entreprise_email"):
        pdf.cell(0, 5, entreprise["entreprise_email"], ln=1)

    # Titre
    pdf.set_y(50)
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "FACTURE", align="C", ln=1)

    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"N° {chantier['id']:04d}  —  Date : {chantier['date_creation']}", align="C", ln=1)
    pdf.ln(5)

    # Client
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, "Client", ln=1)
    pdf.set_font("Helvetica", "", 10)
    if client:
        pdf.cell(0, 5, client["nom"], ln=1)
        if client["adresse"]:
            pdf.cell(0, 5, client["adresse"], ln=1)
        if client["code_postal"] or client["ville"]:
            pdf.cell(0, 5, f"{client['code_postal'] or ''} {client['ville'] or ''}".strip(), ln=1)
        if client["telephone"]:
            pdf.cell(0, 5, f"Tél : {client['telephone']}", ln=1)
        if client["email"]:
            pdf.cell(0, 5, client["email"], ln=1)
    else:
        pdf.cell(0, 5, chantier["client"] or "—", ln=1)
    pdf.ln(4)

    # Chantier
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, f"Chantier : {chantier['nom']}", ln=1)
    pdf.ln(3)

    # Tableau pièces
    if totals["pieces"]:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(90, 7, "Pièces", border=1)
        pdf.cell(30, 7, "Prix HT", border=1, align="R")
        pdf.cell(30, 7, "TVA", border=1, align="R")
        pdf.cell(30, 7, "Prix TTC", border=1, align="R", ln=1)

        pdf.set_font("Helvetica", "", 9)
        tva_rate = totals["tva_rate"]
        for p in totals["pieces"]:
            ht = p["prix_facture"]
            ttc = ht * (1 + tva_rate / 100.0)
            tva = ttc - ht
            pdf.cell(90, 6, (p["nom"] or "")[:45], border=1)
            pdf.cell(30, 6, f"{ht:.2f} €", border=1, align="R")
            pdf.cell(30, 6, f"{tva:.2f} €", border=1, align="R")
            pdf.cell(30, 6, f"{ttc:.2f} €", border=1, align="R", ln=1)
        pdf.ln(3)

    # Tableau main d'œuvre
    if totals["mo"]:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(70, 7, "Main d'œuvre", border=1)
        pdf.cell(25, 7, "Heures", border=1, align="R")
        pdf.cell(25, 7, "Taux", border=1, align="R")
        pdf.cell(30, 7, "Montant HT", border=1, align="R")
        pdf.cell(30, 7, "Montant TTC", border=1, align="R", ln=1)

        pdf.set_font("Helvetica", "", 9)
        tva_rate = totals["tva_rate"]
        for m in totals["mo"]:
            ht = m["montant"]
            ttc = ht * (1 + tva_rate / 100.0)
            pdf.cell(70, 6, (m["description"] or "Main d'œuvre")[:35], border=1)
            pdf.cell(25, 6, f"{m['heures']:.2f}", border=1, align="R")
            pdf.cell(25, 6, f"{m['taux_horaire']:.2f} €", border=1, align="R")
            pdf.cell(30, 6, f"{ht:.2f} €", border=1, align="R")
            pdf.cell(30, 6, f"{ttc:.2f} €", border=1, align="R", ln=1)
        pdf.ln(3)

    # Totaux
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(120, 7, "Total heures main d'œuvre", border=0)
    pdf.cell(60, 7, f"{totals['total_heures']:.2f} h", border=0, align="R", ln=1)

    pdf.cell(120, 7, "Total HT", border=0)
    pdf.cell(60, 7, f"{totals['total_ht']:.2f} €", border=0, align="R", ln=1)

    pdf.cell(120, 7, f"TVA ({totals['tva_rate']} %)", border=0)
    pdf.cell(60, 7, f"{totals['tva_montant']:.2f} €", border=0, align="R", ln=1)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(120, 9, "TOTAL TTC", border=1)
    pdf.cell(60, 9, f"{totals['total_ttc']:.2f} €", border=1, align="R", ln=1)

    pdf.ln(10)
    pdf.set_font("Helvetica", "I", 8)
    pdf.multi_cell(0, 4, "Facture établie en euros. TVA non applicable, art. 293 B du CGI "
                        "(si micro-entreprise en franchise de TVA) — à adapter selon votre situation.")

    buffer = BytesIO()
    pdf.output(buffer)
    buffer.seek(0)
    return buffer


# ---------------------------------------------------------------------------
# Routes - Tableau de bord
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    db = get_db()
    chantiers = db.execute("""
        SELECT c.*, cl.nom AS client_nom
        FROM chantiers c
        LEFT JOIN clients cl ON c.client_id = cl.id
        ORDER BY c.date_creation DESC, c.id DESC
    """).fetchall()

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


# ---------------------------------------------------------------------------
# Routes - Clients
# ---------------------------------------------------------------------------
@app.route("/clients")
def clients_list():
    db = get_db()
    clients = db.execute(
        "SELECT * FROM clients ORDER BY nom COLLATE NOCASE"
    ).fetchall()
    return render_template("clients.html", clients=clients)


@app.route("/client/nouveau", methods=["GET", "POST"])
def client_nouveau():
    if request.method == "POST":
        nom = request.form.get("nom", "").strip()
        if not nom:
            flash("Le nom du client est obligatoire.", "error")
            return redirect(url_for("client_nouveau"))
        db = get_db()
        db.execute(
            """INSERT INTO clients (nom, adresse, code_postal, ville, telephone, email, notes, date_creation)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                nom,
                request.form.get("adresse", "").strip(),
                request.form.get("code_postal", "").strip(),
                request.form.get("ville", "").strip(),
                request.form.get("telephone", "").strip(),
                request.form.get("email", "").strip(),
                request.form.get("notes", "").strip(),
                date.today().isoformat(),
            ),
        )
        db.commit()
        new_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        flash("Client créé.", "success")
        return redirect(url_for("client_detail", client_id=new_id))
    return render_template("client_form.html", client=None, titre="Nouveau client")


@app.route("/client/<int:client_id>")
def client_detail(client_id):
    db = get_db()
    client = db.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    if not client:
        flash("Client introuvable.", "error")
        return redirect(url_for("clients_list"))

    chantiers = db.execute(
        "SELECT * FROM chantiers WHERE client_id = ? ORDER BY date_creation DESC",
        (client_id,),
    ).fetchall()

    chantiers_data = []
    for c in chantiers:
        totals = compute_chantier_totals(c["id"])
        chantiers_data.append({"chantier": c, "totals": totals})

    return render_template(
        "client_detail.html",
        client=client,
        chantiers_data=chantiers_data,
        statut_labels=STATUT_LABELS,
    )


@app.route("/client/<int:client_id>/modifier", methods=["GET", "POST"])
def client_modifier(client_id):
    db = get_db()
    client = db.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    if not client:
        flash("Client introuvable.", "error")
        return redirect(url_for("clients_list"))

    if request.method == "POST":
        nom = request.form.get("nom", "").strip()
        if not nom:
            flash("Le nom du client est obligatoire.", "error")
            return redirect(url_for("client_modifier", client_id=client_id))
        db.execute(
            """UPDATE clients SET nom=?, adresse=?, code_postal=?, ville=?,
               telephone=?, email=?, notes=? WHERE id=?""",
            (
                nom,
                request.form.get("adresse", "").strip(),
                request.form.get("code_postal", "").strip(),
                request.form.get("ville", "").strip(),
                request.form.get("telephone", "").strip(),
                request.form.get("email", "").strip(),
                request.form.get("notes", "").strip(),
                client_id,
            ),
        )
        db.commit()
        flash("Client mis à jour.", "success")
        return redirect(url_for("client_detail", client_id=client_id))

    return render_template("client_form.html", client=client, titre="Modifier le client")


@app.route("/client/<int:client_id>/supprimer", methods=["POST"])
def client_supprimer(client_id):
    db = get_db()
    db.execute("UPDATE chantiers SET client_id = NULL WHERE client_id = ?", (client_id,))
    db.execute("DELETE FROM clients WHERE id = ?", (client_id,))
    db.commit()
    flash("Client supprimé.", "success")
    return redirect(url_for("clients_list"))


# ---------------------------------------------------------------------------
# Routes - Chantiers
# ---------------------------------------------------------------------------
@app.route("/chantier/nouveau", methods=["GET", "POST"])
def chantier_nouveau():
    db = get_db()
    clients = db.execute("SELECT id, nom FROM clients ORDER BY nom COLLATE NOCASE").fetchall()

    if request.method == "POST":
        nom = request.form.get("nom", "").strip()
        if not nom:
            flash("Le nom du chantier est obligatoire.", "error")
            return redirect(url_for("chantier_nouveau"))

        client_id = request.form.get("client_id")
        nouveau_client_nom = request.form.get("nouveau_client_nom", "").strip()

        # Création à la volée d'un client si un nom est fourni
        if nouveau_client_nom:
            db.execute(
                "INSERT INTO clients (nom, date_creation) VALUES (?, ?)",
                (nouveau_client_nom, date.today().isoformat()),
            )
            db.commit()
            client_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        elif client_id:
            client_id = int(client_id)
        else:
            client_id = None

        # Récupérer le nom du client pour le champ legacy "client"
        client_nom = None
        if client_id:
            row = db.execute("SELECT nom FROM clients WHERE id = ?", (client_id,)).fetchone()
            client_nom = row["nom"] if row else None

        db.execute(
            """INSERT INTO chantiers (nom, client_id, client, date_creation, statut)
               VALUES (?, ?, ?, ?, ?)""",
            (nom, client_id, client_nom, date.today().isoformat(), "brouillon"),
        )
        db.commit()
        new_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        flash("Chantier créé.", "success")
        return redirect(url_for("chantier_detail", chantier_id=new_id))

    return render_template("chantier_nouveau.html", clients=clients)


@app.route("/chantier/<int:chantier_id>")
def chantier_detail(chantier_id):
    db = get_db()
    chantier = db.execute("""
        SELECT c.*, cl.nom AS client_nom, cl.id AS client_id_joined
        FROM chantiers c
        LEFT JOIN clients cl ON c.client_id = cl.id
        WHERE c.id = ?
    """, (chantier_id,)).fetchone()

    if chantier is None:
        flash("Chantier introuvable.", "error")
        return redirect(url_for("index"))

    totals = compute_chantier_totals(chantier_id)
    settings = get_settings()
    clients = db.execute("SELECT id, nom FROM clients ORDER BY nom COLLATE NOCASE").fetchall()

    return render_template(
        "chantier.html",
        chantier=chantier,
        totals=totals,
        settings=settings,
        clients=clients,
        statuts=STATUTS,
        statut_labels=STATUT_LABELS,
    )


@app.route("/chantier/<int:chantier_id>/assigner_client", methods=["POST"])
def chantier_assigner_client(chantier_id):
    db = get_db()
    client_id = request.form.get("client_id")
    if client_id:
        client_id = int(client_id)
        row = db.execute("SELECT nom FROM clients WHERE id = ?", (client_id,)).fetchone()
        client_nom = row["nom"] if row else None
    else:
        client_id = None
        client_nom = None

    db.execute(
        "UPDATE chantiers SET client_id = ?, client = ? WHERE id = ?",
        (client_id, client_nom, chantier_id),
    )
    db.commit()
    flash("Client assigné.", "success")
    return redirect(url_for("chantier_detail", chantier_id=chantier_id))


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


@app.route("/chantier/<int:chantier_id>/pdf")
def chantier_pdf(chantier_id):
    db = get_db()
    chantier = db.execute("SELECT * FROM chantiers WHERE id = ?", (chantier_id,)).fetchone()
    if not chantier:
        abort(404)

    client = None
    if chantier["client_id"]:
        client = db.execute("SELECT * FROM clients WHERE id = ?", (chantier["client_id"],)).fetchone()

    totals = compute_chantier_totals(chantier_id)
    entreprise = get_settings_text()

    buffer = generer_pdf_facture(chantier, client, totals, entreprise)
    filename = f"facture_{chantier_id:04d}_{chantier['nom'][:30].replace(' ', '_')}.pdf"
    return send_file(buffer, as_attachment=True, download_name=filename, mimetype="application/pdf")


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
    settings = get_settings()
    description = request.form.get("description", "").strip()

    try:
        heures = float(request.form.get("heures", "0").replace(",", "."))
    except ValueError:
        heures = 0.0

    mode = request.form.get("mode", "manuel")
    if mode.startswith("taux"):
        key = f"mo_{mode}".replace("taux", "taux_")
        taux_horaire = settings.get(key, 0.0)
    else:
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
    settings_text = get_settings_text()
    reset_date = get_ca_reset_date()
    return render_template(
        "admin.html",
        settings=settings,
        settings_text=settings_text,
        labels=SETTINGS_LABELS,
        reset_date=reset_date,
        logo_exists=LOGO_PATH.exists(),
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
    flash("Paramètres numériques mis à jour.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/entreprise", methods=["POST"])
def admin_entreprise():
    db = get_db()
    text_keys = [
        "entreprise_nom", "entreprise_siret", "entreprise_siren",
        "entreprise_adresse", "entreprise_cp_ville",
        "entreprise_tel", "entreprise_email",
    ]
    for key in text_keys:
        value = request.form.get(key, "").strip()
        db.execute(
            "INSERT INTO settings_text (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
    db.commit()

    # Upload logo
    if "logo" in request.files:
        f = request.files["logo"]
        if f and f.filename:
            f.save(LOGO_PATH)

    flash("Informations entreprise mises à jour.", "success")
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
    flash("Tous les chantiers ont été supprimés.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/reset_ca", methods=["POST"])
def admin_reset_ca():
    confirmation = request.form.get("confirmation", "")
    if confirmation != "RESET":
        flash("Remise à zéro annulée : confirmation incorrecte.", "error")
        return redirect(url_for("admin"))
    set_ca_reset_date(date.today())
    flash("Compteur de CA remis à zéro.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/reset_defaults", methods=["POST"])
def admin_reset_defaults():
    db = get_db()
    for key, value in DEFAULT_SETTINGS.items():
        if key.startswith("entreprise_") or key == "ca_reset_date":
            continue
        db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
    db.commit()
    flash("Paramètres numériques réinitialisés.", "success")
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