import os
import json
import sqlite3
import time
from datetime import datetime, date, timedelta
from PIL import Image
import pandas as pd
import streamlit as st
from google import genai

st.set_page_config(page_title="Handy Diet Weekly Planner", page_icon="🥗", layout="centered")

# --- Inizializzazione Database SQLite Permanente ---
conn = sqlite3.connect("meals.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
    CREATE TABLE IF NOT EXISTS handy_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_date TEXT,
        entry_time TEXT,
        week_number TEXT,
        meal_type TEXT,
        category_consumed TEXT,
        score INTEGER,
        plate_summary TEXT,
        calories INTEGER,
        carbs_g REAL,
        protein_g REAL,
        fat_g REAL,
        verdict TEXT,
        image_path TEXT
    )
""")

existing_cols = [col[1] for col in cursor.execute("PRAGMA table_info(handy_logs)").fetchall()]
for col_name in ["week_number", "category_consumed", "entry_time", "image_path"]:
    if col_name not in existing_cols:
        cursor.execute(f"ALTER TABLE handy_logs ADD COLUMN {col_name} TEXT")

cursor.execute("""
    CREATE TABLE IF NOT EXISTS weight_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_date TEXT UNIQUE,
        weight_kg REAL
    )
""")

# Inserimento storico pesate (Giugno - Ottobre 2026)
historical_weights = [
    ("2026-06-12", 66.05),
    ("2026-06-22", 66.55),
    ("2026-07-09", 67.80),
    ("2026-07-13", 67.10),
    ("2026-07-16", 67.90),
    ("2026-07-22", 67.15),
    ("2026-07-23", 67.55),
    ("2026-07-31", 67.25),
    ("2026-08-01", 67.15),
    ("2026-09-03", 69.45),
    ("2026-09-04", 69.90),
    ("2026-09-19", 69.40),
    ("2026-10-03", 70.40),
    ("2026-10-10", 70.50),
]
for entry_d, weight_val in historical_weights:
    cursor.execute("""
        INSERT OR IGNORE INTO weight_logs (entry_date, weight_kg)
        VALUES (?, ?)
    """, (entry_d, weight_val))

cursor.execute("""
    CREATE TABLE IF NOT EXISTS hydration_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_date TEXT UNIQUE,
        water_cl REAL DEFAULT 0,
        tea_cl REAL DEFAULT 0,
        coffee_espresso INTEGER DEFAULT 0,
        coffee_lungo INTEGER DEFAULT 0,
        coffee_americano INTEGER DEFAULT 0
    )
""")

existing_hyd_cols = [col[1] for col in cursor.execute("PRAGMA table_info(hydration_logs)").fetchall()]
if "water_cl" not in existing_hyd_cols:
    cursor.execute("ALTER TABLE hydration_logs ADD COLUMN water_cl REAL DEFAULT 0")
if "tea_cl" not in existing_hyd_cols:
    cursor.execute("ALTER TABLE hydration_logs ADD COLUMN tea_cl REAL DEFAULT 0")
for col_name in ["coffee_espresso", "coffee_lungo", "coffee_americano"]:
    if col_name not in existing_hyd_cols:
        cursor.execute(f"ALTER TABLE hydration_logs ADD COLUMN {col_name} INTEGER DEFAULT 0")

conn.commit()
os.makedirs("meal_images", exist_ok=True)

# --- Configurazione Chiave API Gemini ---
api_key = None
try:
    if "GEMINI_API_KEY" in st.secrets:
        api_key = st.secrets["GEMINI_API_KEY"]
except Exception:
    pass

if not api_key:
    api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    api_key = st.sidebar.text_input("Inserisci la tua Gemini API Key", type="password")

if not api_key:
    st.info("👉 Inserisci la tua API Key di Gemini nella barra laterale per attivare l'assistente.")
    st.stop()

client = genai.Client(api_key=api_key)
ACTIVE_MODEL = "gemini-2.5-flash"

# --- Orario e Pasto Attuale ---
now = datetime.now()
hour_float = now.hour + now.minute / 60.0

if 6.0 <= hour_float < 9.0:
    current_slot = "Colazione"
elif 11.0 <= hour_float < 15.0:
    current_slot = "Pranzo"
elif 15.5 <= hour_float < 18.0:
    current_slot = "Spuntino"
elif 18.5 <= hour_float < 22.0:
    current_slot = "Cena"
elif 22.0 <= hour_float <= 24.0 or hour_float < 2.0:
    current_slot = "Spuntino Serale"
else:
    current_slot = "Pranzo"

current_week_str = now.strftime("%Y-W%W")
today_str = date.today().isoformat()

cursor.execute("""
    INSERT OR IGNORE INTO hydration_logs (entry_date, water_cl, tea_cl, coffee_espresso, coffee_lungo, coffee_americano) 
    VALUES (?, 0, 0, 0, 0, 0)
""", (today_str,))
conn.commit()

# --- Quote Settimanali Esatte con Stima Kcal di Riferimento ---
WEEKLY_QUOTAS = {
    # COLAZIONE (Totale: 7)
    "Colazione [5x] - Yogurt Intero + Noci (5) + Fiocchi di Avena (5 cucchiai)": {
        "max": 5, "slot": "Colazione", "default_kcal": 420,
        "desc": "Yogurt intero (250 ml) + 5 noci secche + 5 cucchiai fiocchi di avena"
    },
    "Colazione [2x] - Pane di Segale (1 mano) + Ricotta (125g) + Marmellata Light": {
        "max": 2, "slot": "Colazione", "default_kcal": 360,
        "desc": "Pane di segale (1 mano) + 1/2 vasetto ricotta (125g) + 3 cucchiaini marmellata Hero Light"
    },

    # PRANZO (Totale: 7)
    "Pranzo [2x] - Pasta corta al sugo leggero (1 pugno schiacciato) + Verdura cotta": {
        "max": 2, "slot": "Pranzo", "default_kcal": 460,
        "desc": "Pasta corta con sugo leggero (1 pugno schiacciato) + verdura cotta condita (1 pugno)"
    },
    "Pranzo [2x] - Secondo di Carne (1 mano) + Verdura cotta + 2 Gallette": {
        "max": 2, "slot": "Pranzo", "default_kcal": 430,
        "desc": "Carne semplice (1 mano) + verdura cotta (1 pugno) + 2 gallette norvegesi"
    },
    "Pranzo [2x] - Insalatone con 2 Uova sode + Ortaggi misti + 2 Gallette": {
        "max": 2, "slot": "Pranzo", "default_kcal": 390,
        "desc": "Insalatone (1.5 pugni cruda + 1 pugno ortaggi) + 2 uova sode + 2 gallette norvegesi"
    },
    "Pranzo [1x] - Zuppa di legumi (1 fondina 250ml) + 2 Gallette": {
        "max": 1, "slot": "Pranzo", "default_kcal": 380,
        "desc": "Zuppa di legumi (250 ml) + 2 gallette norvegesi"
    },

    # SPUNTINO POMERIDIANO (Totale: 7)
    "Spuntino [7x] - Frutta fresca di stagione (1 pugno)": {
        "max": 7, "slot": "Spuntino", "default_kcal": 90,
        "desc": "1 pugno di frutta fresca di stagione"
    },

    # CENA (Totale: 7)
    "Cena [2x] - Pesce semplice (1 mano) + Verdura cruda + 6 cucchiai Piselli": {
        "max": 2, "slot": "Cena", "default_kcal": 420,
        "desc": "Pesce semplice ai ferri/vapore (1 mano) + verdura cruda (1.5 pugni) + 6 cucchiai piselli in padella"
    },
    "Cena [2x] - Minestrone (500ml) + Formaggio stagionato (2 dita)": {
        "max": 2, "slot": "Cena", "default_kcal": 440,
        "desc": "Minestrone con legumi e patate (500 ml) + formaggio stagionato con crosta (2 dita)"
    },
    "Cena [1x] - Uova (2 unità tegamino/frittata) + Verdura cruda + 2 Gallette": {
        "max": 1, "slot": "Cena", "default_kcal": 380,
        "desc": "2 uova al tegamino/strapazzate/frittata + verdura cruda (1.5 pugni) + 2 gallette norvegesi"
    },
    "Cena [1x] - Petto di Pollo/Tacchino ai ferri (1 mano) + Verdura cruda + 2 Gallette": {
        "max": 1, "slot": "Cena", "default_kcal": 390,
        "desc": "Petto di pollo o tacchino ai ferri (1 mano) + verdura cruda (1.5 pugni) + 2 gallette norvegesi"
    },
    "Cena [1x] - Pasto Libero con moderazione (senza pizza)": {
        "max": 1, "slot": "Cena", "default_kcal": 750,
        "desc": "1 pasto libero settimanale con moderazione (evitare la pizza)"
    },

    # SPUNTINO SERALE (Totale: 4)
    "Spuntino Serale [4x] - Cioccolato Fondente 85% Perugina Nero (2 quadretti)": {
        "max": 4, "slot": "Spuntino Serale", "default_kcal": 110,
        "desc": "2 quadretti di cioccolato fondente 85% Perugina Nero"
    }
}

# Conteggio per la settimana in corso
consumed_counts = {k: 0 for k in WEEKLY_QUOTAS.keys()}
rows = cursor.execute("SELECT category_consumed FROM handy_logs WHERE week_number = ?", (current_week_str,)).fetchall()
for r in rows:
    cat = r[0]
    if cat in consumed_counts:
        consumed_counts[cat] += 1

remaining_options = {}
for cat, meta in WEEKLY_QUOTAS.items():
    rem = meta["max"] - consumed_counts.get(cat, 0)
    remaining_options[cat] = {
        "max": meta["max"],
        "used": consumed_counts.get(cat, 0),
        "remaining": max(0, rem),
        "slot": meta["slot"],
        "desc": meta["desc"],
        "default_kcal": meta["default_kcal"]
    }

# --- Sidebar ---
st.sidebar.header("🎯 Impostazioni")
target_weight = st.sidebar.number_input("Target Peso Corporeo (kg)", min_value=50.0, max_value=100.0, value=66.5, step=0.1)
target_daily_kcal = st.sidebar.number_input("Target Calorie Giornaliere (kcal)", min_value=1200, max_value=3500, value=1750, step=50)
target_water_cl = st.sidebar.number_input("Target Acqua / Tisane (cl)", min_value=100.0, max_value=500.0, value=250.0, step=10.0)

st.sidebar.divider()
st.sidebar.markdown(f"**Settimana attiva:** `{current_week_str}`")

# Calcolo Voto Settimanale Automatico
week_meals = cursor.execute(
    "SELECT score, calories, category_consumed FROM handy_logs WHERE week_number = ?", 
    (current_week_str,)
).fetchall()

if week_meals:
    scores = [m[0] for m in week_meals if m[0] is not None]
    avg_score = sum(scores) / len(scores) if scores else 8.0
    
    # Penalità se si superano le quote massime prescritte
    penalties = 0
    for cat_name, cnt in consumed_counts.items():
        quota_max = WEEKLY_QUOTAS[cat_name]["max"]
        if cnt > quota_max:
            penalties += (cnt - quota_max) * 0.5
            
    final_weekly_score = max(1.0, min(10.0, round(avg_score - penalties, 1)))
else:
    final_weekly_score = 10.0

st.sidebar.metric(
    "🏆 Voto Settimana", 
    f"{final_weekly_score}/10", 
    help="Valutato su aderenza pasti registrati e rispetto dei tetti massimi delle categorie settimanali."
)

def generate_offline_suggestions(available_items: list):
    proposals = []
    for it in available_items[:3]:
        cat_name = it["categoria"]
        clean_name = cat_name.split(" - ")[-1]
        proposals.append({
            "title": f"Consigliato: {clean_name}",
            "matched_category": cat_name,
            "hotel_advice": f"Al buffet prepara: {it['descrizione']}.",
            "why": f"Hai ancora {it['rimaste']} porzione/i rimanente/i su {it['massimo']} previste."
        })
    return {"proposals": proposals}

def get_meal_suggestions(meal_slot: str, available_items: list):
    prompt = f"""
    Sei il nutrizionista dell'utente (metodo Handy Diet del Dr. Michele Sculati).
    È l'orario di: **{meal_slot}**.
    L'utente è in albergo/ristorante.
    Ecco le quote e le opzioni DISPONIBILI per {meal_slot} questa settimana:
    {json.dumps(available_items, ensure_ascii=False, indent=2)}

    Genera 3 proposte pratiche per comporre il piatto al buffet o ristorante usando ESCLUSIVAMENTE le categorie rimaste sopra.
    Per ogni proposta specifica:
    - Titolo chiaro
    - Categoria esatta associata
    - Istruzioni pratiche per l'albergo (misure a mano, pugno, dita)
    - Motivazione

    Rispondi solo ed esclusivamente in formato JSON:
    {{
      "proposals": [
        {{
          "title": "Titolo proposta",
          "matched_category": "Nome esatto della categoria rimasta",
          "hotel_advice": "Istruzioni per il buffet",
          "why": "Motivazione"
        }}
      ]
    }}
    """
    try:
        resp = client.models.generate_content(
            model=ACTIVE_MODEL,
            contents=prompt,
            config={"response_mime_type": "application/json"}
        )
        return json.loads(resp.text)
    except Exception:
        return generate_offline_suggestions(available_items)

# --- Interfaccia Principale ---
st.title("🥗 Handy Diet Tracker")
st.caption(f"Orario rilevato: **{now.strftime('%H:%M')}** ➔ Pasto suggerito: **{current_slot}**")

tab_suggest, tab_register, tab_quotas, tab_water, tab_weight = st.tabs([
    "💡 Proposta Pasto",
    "📝 Registra Pasto & Diario",
    "📊 Monte Settimanale",
    "💧 Acqua, Tisane & Caffè",
    "⚖️ Controllo Peso"
])

# === TAB 1: PROPOSTE ===
with tab_suggest:
    st.subheader(f"Cosa mangiare adesso ({current_slot})")
    slot_available = [
        {"categoria": k, "rimaste": v["remaining"], "massimo": v["max"], "descrizione": v["desc"]}
        for k, v in remaining_options.items()
        if v["slot"] == current_slot and v["remaining"] > 0
    ]

    if not slot_available:
        st.warning(f"⚠️ Hai già completato tutti i pasti previsti per **{current_slot}** questa settimana!")
    else:
        st.markdown(f"Hai ancora **{len(slot_available)} opzioni disponibili** per questo pasto.")
        if st.button("✨ Fatti suggerire 3 piatti da Gemini", type="primary"):
            with st.spinner("Calcolo proposte in base alle quote rimanenti..."):
                sugg = get_meal_suggestions(current_slot, slot_available)
                st.session_state["suggestions"] = sugg.get("proposals", [])

        if "suggestions" in st.session_state and st.session_state["suggestions"]:
            st.divider()
            for idx, p in enumerate(st.session_state["suggestions"], 1):
                st.markdown(f"### Proposta {idx}: {p.get('title')}")
                st.markdown(f"**Categoria da scalare:** `{p.get('matched_category')}`")
                st.info(f"🍽️ **Come comporlo al buffet/hotel:** {p.get('hotel_advice')}")
                st.caption(f"Motivazione: {p.get('why')}")
                st.divider()

# === TAB 2: REGISTRAZIONE PASTO & CALENDARIO STORICO ===
with tab_register:
    st.subheader("Registra pasto & scala quota")

    col_s1, col_s2 = st.columns(2)
    with col_s1:
        sel_slot = st.selectbox(
            "Pasto:", 
            ["Colazione", "Pranzo", "Spuntino", "Cena", "Spuntino Serale"], 
            index=["Colazione", "Pranzo", "Spuntino", "Cena", "Spuntino Serale"].index(current_slot)
        )
    with col_s2:
        cat_choices = [k for k, v in remaining_options.items() if v["slot"] == sel_slot]
        if not cat_choices:
            cat_choices = [k for k in WEEKLY_QUOTAS.keys() if WEEKLY_QUOTAS[k]["slot"] == sel_slot]
        sel_category = st.selectbox(
            "Voce della dieta da scalare:",
            cat_choices,
            format_func=lambda x: f"{x} (Rimaste: {remaining_options.get(x, {}).get('remaining', 0)}/{remaining_options.get(x, {}).get('max', 0)})"
        )

    auto_kcal = WEEKLY_QUOTAS.get(sel_category, {}).get("default_kcal", 350)

    st.markdown("---")
    meal_desc = st.text_input(
        "Descrivi cosa hai mangiato:", 
        value=WEEKLY_QUOTAS.get(sel_category, {}).get("desc", ""),
        placeholder="Es. Yogurt intero con 5 noci e 5 cucchiai avena"
    )

    st.caption("📷 Vuoi allegare una foto per verifica e ricordo? (Opzionale)")
    photo_sub = st.radio("Sorgente foto:", ["Nessuna foto", "Scatta foto", "Carica da galleria"], horizontal=True)
    img_file = None
    if photo_sub == "Scatta foto":
        img_file = st.camera_input("Scatta per il diario")
    elif photo_sub == "Carica da galleria":
        img_file = st.file_uploader("Scegli immagine", type=["jpg", "jpeg", "png"])

    if img_file:
        st.image(Image.open(img_file), caption="Foto piatto", use_container_width=True)

    col_m1, col_m2 = st.columns(2)
    with col_m1:
        manual_kcal = st.number_input(
            "Calorie stimate (automatiche dal piano)", 
            min_value=0, max_value=2500, value=auto_kcal, step=10,
            help="Calcolate in automatico in base alla porzione Handy Diet. Puoi modificarle liberamente se hai mangiato di più o di meno."
        )
    with col_m2:
        manual_score = st.slider("Voto aderenza a questa porzione (1-10)", min_value=1, max_value=10, value=10)

    reg_date = st.date_input("Data del pasto da registrare:", value=date.today(), key="reg_meal_date")

    if st.button("💾 Salva e Scala Subito", type="primary"):
        if not meal_desc.strip():
            meal_desc = sel_category.split(" - ")[-1]
        
        saved_img_path = None
        if img_file:
            timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            saved_img_path = f"meal_images/{timestamp_str}.jpg"
            Image.open(img_file).save(saved_img_path)

        reg_week_str = reg_date.strftime("%Y-W%W")

        cursor.execute("""
            INSERT INTO handy_logs (
                entry_date, entry_time, week_number, meal_type, category_consumed,
                score, plate_summary, calories, carbs_g, protein_g, fat_g, verdict, image_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            reg_date.isoformat(),
            datetime.now().strftime("%H:%M"),
            reg_week_str,
            sel_slot,
            sel_category,
            manual_score,
            meal_desc,
            manual_kcal,
            0.0,
            0.0,
            0.0,
            "Registrato",
            saved_img_path
        ))
        conn.commit()
        st.toast(f"Pasto registrato per il {reg_date}! Scalata 1 quota.")
        st.rerun()

    # --- CALENDARIO & DIARIO GIORNALIERO INTEGRATO ---
    st.divider()
    st.subheader("📅 Calendario & Diario Pasti")
    st.caption("Seleziona una data per vedere tutti i pasti consumati, controllare le calorie giornaliere o modificare/eliminare voci.")

    view_date = st.date_input("Seleziona data da consultare:", value=date.today(), key="calendar_view_date")
    view_date_str = view_date.isoformat()

    day_meals = cursor.execute("""
        SELECT id, entry_time, meal_type, category_consumed, plate_summary, score, calories, image_path 
        FROM handy_logs 
        WHERE entry_date = ? 
        ORDER BY id ASC
    """, (view_date_str,)).fetchall()

    # Riassunto Giornaliero Kcal
    tot_day_kcal = sum([m[6] for m in day_meals if m[6] is not None])
    delta_kcal = tot_day_kcal - target_daily_kcal

    col_k1, col_k2, col_k3 = st.columns(3)
    col_k1.metric("Kcal Consumate", f"{tot_day_kcal} kcal")
    col_k2.metric("Target Giornaliero", f"{target_daily_kcal} kcal")
    col_k3.metric("Scostamento Target", f"{delta_kcal:+d} kcal", delta_color="inverse" if delta_kcal > 0 else "normal")

    if not day_meals:
        st.info(f"Nessun pasto registrato per il giorno **{view_date.strftime('%d/%m/%Y')}**.")
    else:
        st.markdown(f"#### Pasti registrati il {view_date.strftime('%d/%m/%Y')} ({len(day_meals)} pasti):")
        for m in day_meals:
            m_id, m_time, m_slot, m_cat, m_sum, m_score, m_kcal, m_img = m
            
            with st.expander(f"🕒 {m_time} | **{m_slot}**: {m_sum} ({m_kcal} kcal - Voto: {m_score}/10)"):
                st.markdown(f"**Categoria associata:** `{m_cat}`")
                if m_img and os.path.exists(m_img):
                    st.image(m_img, caption="Foto piatto", width=250)

                # Form per Modificare
                with st.popover("✏️ Modifica questo pasto"):
                    new_summary = st.text_input("Descrizione:", value=m_sum, key=f"edit_sum_{m_id}")
                    new_kcal = st.number_input("Calorie (kcal):", value=int(m_kcal or 0), step=10, key=f"edit_kcal_{m_id}")
                    new_score = st.slider("Voto (1-10):", min_value=1, max_value=10, value=int(m_score or 10), key=f"edit_sc_{m_id}")
                    if st.button("Salva modifiche", key=f"btn_save_{m_id}"):
                        cursor.execute("""
                            UPDATE handy_logs 
                            SET plate_summary = ?, calories = ?, score = ? 
                            WHERE id = ?
                        """, (new_summary, new_kcal, new_score, m_id))
                        conn.commit()
                        st.toast("Pasto aggiornato!")
                        st.rerun()

                # Tasto Elimina con ripristino quota
                if st.button(f"🗑️ Elimina Pasto", key=f"del_{m_id}", type="secondary"):
                    cursor.execute("DELETE FROM handy_logs WHERE id = ?", (m_id,))
                    conn.commit()
                    st.toast("Pasto eliminato e quota ripristinata!")
                    st.rerun()

# === TAB 3: MONTE SETTIMANALE ===
with tab_quotas:
    st.subheader(f"Monte Settimanale Quote ({current_week_str})")
    st.caption("Controlla le porzioni consumate e quelle rimaste. Puoi annullare o scalare manualmente una porzione con un click.")
    
    table_data = []
    for cat, val in remaining_options.items():
        table_data.append({
            "Pasto": val["slot"],
            "Opzione Prescritta": cat.replace(f"{val['slot']} ", ""),
            "Fatti": val["used"],
            "Previsti": val["max"],
            "Rimanenti": val["remaining"]
        })
    df_quotas = pd.DataFrame(table_data)
    
    for s_name in ["Colazione", "Pranzo", "Spuntino", "Cena", "Spuntino Serale"]:
        sub = df_quotas[df_quotas["Pasto"] == s_name]
        if not sub.empty:
            st.markdown(f"#### 🍽️ {s_name}")
            st.dataframe(sub[["Opzione Prescritta", "Fatti", "Previsti", "Rimanenti"]], hide_index=True, use_container_width=True)

    st.divider()
    st.subheader("⚙️ Gestione Rapida Quote Settimanali")
    st.caption("Vuoi togliere o aggiungere un pasto consumato senza passare dal form?")
    
    sel_cat_manage = st.selectbox("Seleziona categoria:", list(WEEKLY_QUOTAS.keys()), key="sel_cat_manage")
    c_m1, c_m2 = st.columns(2)
    with c_m1:
        if st.button("➖ Rimuovi ultimo pasto registrato di questa voce", use_container_width=True):
            last_id = cursor.execute("""
                SELECT id FROM handy_logs 
                WHERE week_number = ? AND category_consumed = ? 
                ORDER BY id DESC LIMIT 1
            """, (current_week_str, sel_cat_manage)).fetchone()
            if last_id:
                cursor.execute("DELETE FROM handy_logs WHERE id = ?", (last_id[0],))
                conn.commit()
                st.toast(f"Rimosso un pasto da {sel_cat_manage}!")
                st.rerun()
            else:
                st.warning("Nessun pasto trovato per questa voce nella settimana corrente.")
    with c_m2:
        if st.button("➕ Registra rapido 1 quota (senza foto)", use_container_width=True):
            auto_k = WEEKLY_QUOTAS[sel_cat_manage]["default_kcal"]
            cursor.execute("""
                INSERT INTO handy_logs (
                    entry_date, entry_time, week_number, meal_type, category_consumed,
                    score, plate_summary, calories, carbs_g, protein_g, fat_g, verdict, image_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                today_str,
                datetime.now().strftime("%H:%M"),
                current_week_str,
                WEEKLY_QUOTAS[sel_cat_manage]["slot"],
                sel_cat_manage,
                10,
                WEEKLY_QUOTAS[sel_cat_manage]["desc"],
                auto_k,
                0.0, 0.0, 0.0,
                "Registrato rapido",
                None
            ))
            conn.commit()
            st.toast(f"Aggiunta 1 quota a {sel_cat_manage}!")
            st.rerun()

# === TAB 4: ACQUA, TISANE & CAFFÈ (IN CL) ===
with tab_water:
    st.subheader("💧 Monitor Liquidi di Oggi (in cl)")
    
    h_data = cursor.execute("""
        SELECT water_cl, tea_cl, coffee_espresso, coffee_lungo, coffee_americano 
        FROM hydration_logs WHERE entry_date = ?
    """, (today_str,)).fetchone()

    cur_water_cl = float(h_data[0] or 0) if h_data else 0.0
    cur_tea_cl = float(h_data[1] or 0) if h_data else 0.0
    cur_espresso = int(h_data[2] or 0) if h_data else 0
    cur_lungo = int(h_data[3] or 0) if h_data else 0
    cur_americano = int(h_data[4] or 0) if h_data else 0

    # Conversione caffè in cl (1 espresso = 3 cl, 1 lungo = 6 cl, 1 americano = 15 cl)
    coffee_cl = (cur_espresso * 3.0) + (cur_lungo * 6.0) + (cur_americano * 15.0)
    total_liquids_cl = cur_water_cl + cur_tea_cl

    remaining_cl = max(0.0, target_water_cl - total_liquids_cl)
    progress_val = min(1.0, total_liquids_cl / target_water_cl) if target_water_cl > 0 else 1.0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Acqua", f"{cur_water_cl:.0f} cl")
    c2.metric("Tisane", f"{cur_tea_cl:.0f} cl")
    c3.metric("Caffè (totale cl)", f"{coffee_cl:.0f} cl", delta=f"{cur_espresso + cur_lungo + cur_americano} tazzine")
    c4.metric("Mancanti al Target", f"{remaining_cl:.0f} cl", delta=f"Target: {target_water_cl:.0f} cl")

    st.progress(progress_val)
    if total_liquids_cl >= target_water_cl:
        st.success(f"🎉 **Target raggiunto!** Hai assunto {total_liquids_cl:.0f} cl tra acqua e tisane.")

    st.divider()
    st.markdown("#### 🥛 Registra Acqua (in cl)")
    w_b1, w_b2, w_b3, w_b4 = st.columns(4)
    with w_b1:
        if st.button("+25 cl (Bicchiere)", use_container_width=True):
            cursor.execute("UPDATE hydration_logs SET water_cl = water_cl + 25 WHERE entry_date = ?", (today_str,))
            conn.commit()
            st.rerun()
    with w_b2:
        if st.button("+50 cl (Borraccia)", use_container_width=True):
            cursor.execute("UPDATE hydration_logs SET water_cl = water_cl + 50 WHERE entry_date = ?", (today_str,))
            conn.commit()
            st.rerun()
    with w_b3:
        if st.button("+100 cl (1 Litro)", use_container_width=True):
            cursor.execute("UPDATE hydration_logs SET water_cl = water_cl + 100 WHERE entry_date = ?", (today_str,))
            conn.commit()
            st.rerun()
    with w_b4:
        if cur_water_cl >= 25:
            if st.button("-25 cl (Correggi)", use_container_width=True):
                cursor.execute("UPDATE hydration_logs SET water_cl = max(0, water_cl - 25) WHERE entry_date = ?", (today_str,))
                conn.commit()
                st.rerun()

    st.markdown("#### 🫖 Registra Tisane (in cl)")
    t_b1, t_b2, t_b3 = st.columns(3)
    with t_b1:
        if st.button("+25 cl (Tazza tisana)", use_container_width=True):
            cursor.execute("UPDATE hydration_logs SET tea_cl = tea_cl + 25 WHERE entry_date = ?", (today_str,))
            conn.commit()
            st.rerun()
    with t_b2:
        if st.button("+50 cl (Teiera)", use_container_width=True):
            cursor.execute("UPDATE hydration_logs SET tea_cl = tea_cl + 50 WHERE entry_date = ?", (today_str,))
            conn.commit()
            st.rerun()
    with t_b3:
        if cur_tea_cl >= 25:
            if st.button("-25 cl Tisana", use_container_width=True):
                cursor.execute("UPDATE hydration_logs SET tea_cl = max(0, tea_cl - 25) WHERE entry_date = ?", (today_str,))
                conn.commit()
                st.rerun()

    st.divider()
    st.markdown("#### ☕ Registro Caffè")

    k_col1, k_col2, k_col3 = st.columns(3)
    with k_col1:
        st.metric("Espresso (3 cl)", f"{cur_espresso}")
        if st.button("☕ +1 Espresso", use_container_width=True, type="primary"):
            cursor.execute("UPDATE hydration_logs SET coffee_espresso = coffee_espresso + 1 WHERE entry_date = ?", (today_str,))
            conn.commit()
            st.rerun()
        if cur_espresso > 0:
            if st.button("↩️ -1 Espresso", use_container_width=True):
                cursor.execute("UPDATE hydration_logs SET coffee_espresso = max(0, coffee_espresso - 1) WHERE entry_date = ?", (today_str,))
                conn.commit()
                st.rerun()

    with k_col2:
        st.metric("Lungo (6 cl)", f"{cur_lungo}")
        if st.button("☕ +1 Lungo", use_container_width=True, type="primary"):
            cursor.execute("UPDATE hydration_logs SET coffee_lungo = coffee_lungo + 1 WHERE entry_date = ?", (today_str,))
            conn.commit()
            st.rerun()
        if cur_lungo > 0:
            if st.button("↩️ -1 Lungo", use_container_width=True):
                cursor.execute("UPDATE hydration_logs SET coffee_lungo = max(0, coffee_lungo - 1) WHERE entry_date = ?", (today_str,))
                conn.commit()
                st.rerun()

    with k_col3:
        st.metric("Americano (15 cl)", f"{cur_americano}")
        if st.button("☕ +1 Americano", use_container_width=True, type="primary"):
            cursor.execute("UPDATE hydration_logs SET coffee_americano = coffee_americano + 1 WHERE entry_date = ?", (today_str,))
            conn.commit()
            st.rerun()
        if cur_americano > 0:
            if st.button("↩️ -1 Americano", use_container_width=True):
                cursor.execute("UPDATE hydration_logs SET coffee_americano = max(0, coffee_americano - 1) WHERE entry_date = ?", (today_str,))
                conn.commit()
                st.rerun()

# === TAB 5: CONTROLLO PESO ===
with tab_weight:
    st.subheader("⚖️ Diario Peso Corporeo & Andamento Storico")
    
    w1, w2, w3 = st.columns([2, 2, 1.5])
    with w1:
        w_date = st.date_input("Data pesata", value=date.today())
    with w2:
        w_val = st.number_input("Peso (kg)", min_value=40.0, max_value=160.0, value=70.0, step=0.05, format="%.2f")
    with w3:
        st.write("")
        st.write("")
        if st.button("💾 Registra Peso", type="primary"):
            cursor.execute("""
                INSERT INTO weight_logs (entry_date, weight_kg)
                VALUES (?, ?)
                ON CONFLICT(entry_date) DO UPDATE SET weight_kg = excluded.weight_kg
            """, (w_date.isoformat(), w_val))
            conn.commit()
            st.toast(f"Peso {w_val} kg salvato!")
            st.rerun()

    df_w = pd.read_sql_query("SELECT entry_date, weight_kg FROM weight_logs ORDER BY entry_date ASC", conn)
    if not df_w.empty:
        df_w["entry_date"] = pd.to_datetime(df_w["entry_date"])
        latest_w = df_w.iloc[-1]["weight_kg"]
        min_historical = df_w["weight_kg"].min()
        latest_date_str = df_w.iloc[-1]["entry_date"].strftime("%d/%m/%Y")
        
        if len(df_w) > 1:
            prev_w = df_w.iloc[-2]["weight_kg"]
            d_last = latest_w - prev_w
        else:
            d_last = 0.0

        st.divider()
        k1, k2, k3 = st.columns(3)
        k1.metric("Ultimo Peso Rilevato", f"{latest_w:.2f} kg", delta=f"{d_last:+.2f} kg (vs prec)" if len(df_w) > 1 else None, delta_color="inverse")
        k2.metric("Minimo Storico Raggiunto", f"{min_historical:.2f} kg")
        k3.metric("Mancanti al Target", f"{latest_w - target_weight:+.2f} kg", delta=f"Target: {target_weight:.1f} kg")

        if latest_w > 70.0:
            st.warning(f"⚠️ **Attenzione al trend:** Il peso si trova a {latest_w:.2f} kg rispetto al minimo estivo di {min_historical:.2f} kg (+{latest_w - min_historical:.2f} kg). Seguire scrupolosamente le quote della Handy Diet riporterà il trend verso il target sostenibile.")

        st.markdown("#### Grafico dell'Andamento del Peso")
        chart_df = df_w.set_index("entry_date")[["weight_kg"]]
        chart_df["Target"] = target_weight
        st.line_chart(chart_df)

        with st.expander("Visualizza storico completo di tutte le pesate"):
            st.dataframe(df_w.sort_values("entry_date", ascending=False), hide_index=True)
