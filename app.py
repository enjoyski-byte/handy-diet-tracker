import os
import json
import sqlite3
import time
from datetime import datetime, date, timedelta
from PIL import Image
import pandas as pd
import streamlit as st
from google import genai

st.set_page_config(
    page_title="Handy Diet",
    page_icon="🥗",
    layout="centered",
    initial_sidebar_state="collapsed"
)

# --- CSS MOBILE-FIRST ISPIRATO AL DESIGN ALLEGATO ---
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Plus Jakarta Sans', -apple-system, sans-serif;
    }
    
    /* Contenitore principale compatto per mobile */
    .block-container {
        padding-top: 1.2rem !important;
        padding-bottom: 5rem !important;
        padding-left: 0.9rem !important;
        padding-right: 0.9rem !important;
        max-width: 480px !important;
        margin: 0 auto !important;
    }

    /* Card moderna verde / bianca */
    .metric-card-green {
        background: linear-gradient(135deg, #dcfce7 0%, #bbf7d0 100%);
        border-radius: 24px;
        padding: 18px;
        color: #14532d;
        box-shadow: 0 4px 15px rgba(34, 197, 94, 0.08);
        margin-bottom: 12px;
    }
    
    .card-white {
        background: #ffffff;
        border: 1px solid #f1f5f9;
        border-radius: 20px;
        padding: 16px;
        box-shadow: 0 4px 12px rgba(15, 23, 42, 0.04);
        margin-bottom: 12px;
    }
    
    /* Pulsanti touch-friendly */
    .stButton>button {
        border-radius: 16px !important;
        min-height: 48px !important;
        font-weight: 700 !important;
        border: none !important;
        transition: all 0.15s ease !important;
    }
    
    .stButton>button:active {
        transform: scale(0.98) !important;
    }
    
    /* Input arrotondati */
    input, select, textarea {
        border-radius: 14px !important;
    }
    
    /* Badge pasto */
    .meal-pill {
        display: inline-block;
        padding: 4px 10px;
        border-radius: 20px;
        font-size: 11px;
        font-weight: 700;
        text-transform: uppercase;
        background-color: #f0fdf4;
        color: #16a34a;
        margin-bottom: 6px;
    }
</style>
""", unsafe_allow_html=True)

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
for col_name in ["week_number", "category_consumed", "entry_time", "image_path", "carbs_g", "protein_g", "fat_g"]:
    if col_name not in existing_cols:
        cursor.execute(f"ALTER TABLE handy_logs ADD COLUMN {col_name} TEXT")

cursor.execute("""
    CREATE TABLE IF NOT EXISTS weight_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_date TEXT UNIQUE,
        weight_kg REAL
    )
""")

# Storico pesate
historical_weights = [
    ("2026-06-12", 66.05), ("2026-06-22", 66.55), ("2026-07-09", 67.80),
    ("2026-07-13", 67.10), ("2026-07-16", 67.90), ("2026-07-22", 67.15),
    ("2026-07-23", 67.55), ("2026-07-31", 67.25), ("2026-08-01", 67.15),
    ("2026-09-03", 69.45), ("2026-09-04", 69.90), ("2026-09-19", 69.40),
    ("2026-10-03", 70.40), ("2026-10-10", 70.50),
]
for entry_d, weight_val in historical_weights:
    cursor.execute("INSERT OR IGNORE INTO weight_logs (entry_date, weight_kg) VALUES (?, ?)", (entry_d, weight_val))

cursor.execute("""
    CREATE TABLE IF NOT EXISTS hydration_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_date TEXT UNIQUE,
        water_cl REAL DEFAULT 0,
        tea_cl REAL DEFAULT 0,
        coffee_espresso INTEGER DEFAULT 0,
        coffee_lungo INTEGER DEFAULT 0,
        coffee_americano INTEGER DEFAULT 0,
        coffee_americano_large INTEGER DEFAULT 0
    )
""")

existing_hyd_cols = [col[1] for col in cursor.execute("PRAGMA table_info(hydration_logs)").fetchall()]
for col_name in ["water_cl", "tea_cl", "coffee_espresso", "coffee_lungo", "coffee_americano", "coffee_americano_large"]:
    if col_name not in existing_hyd_cols:
        cursor.execute(f"ALTER TABLE hydration_logs ADD COLUMN {col_name} REAL DEFAULT 0")

conn.commit()
os.makedirs("meal_images", exist_ok=True)

# --- Configurazione API Gemini ---
api_key = None
try:
    if "GEMINI_API_KEY" in st.secrets:
        api_key = st.secrets["GEMINI_API_KEY"]
except Exception:
    pass

if not api_key:
    api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    api_key = st.sidebar.text_input("Gemini API Key", type="password")

if not api_key:
    st.info("👉 Inserisci la tua API Key di Gemini nella barra laterale per attivare il calcolo calorie e i consigli.")
    st.stop()

client = genai.Client(api_key=api_key)
ACTIVE_MODEL = "gemini-1.5-flash"

# --- Orario e Pasto Attuale ---
now = datetime.now()
hour_float = now.hour + now.minute / 60.0

if 6.0 <= hour_float < 10.0:
    current_slot = "Colazione"
elif 11.0 <= hour_float < 15.0:
    current_slot = "Pranzo"
elif 15.0 <= hour_float < 18.5:
    current_slot = "Spuntino"
elif 18.5 <= hour_float < 22.0:
    current_slot = "Cena"
else:
    current_slot = "Spuntino Serale"

current_week_str = now.strftime("%Y-W%W")
today_str = date.today().isoformat()

cursor.execute("""
    INSERT OR IGNORE INTO hydration_logs (entry_date, water_cl, tea_cl, coffee_espresso, coffee_lungo, coffee_americano, coffee_americano_large) 
    VALUES (?, 0, 0, 0, 0, 0, 0)
""", (today_str,))
conn.commit()

# --- Quote Settimanali Esatte ---
WEEKLY_QUOTAS = {
    "Colazione [5x] - Yogurt Intero + Noci (5) + Fiocchi di Avena (5 cucchiai)": {
        "max": 5, "slot": "Colazione", "default_kcal": 420,
        "desc": "Yogurt intero (250 ml) + 5 noci secche + 5 cucchiai fiocchi di avena"
    },
    "Colazione [2x] - Pane di Segale (1 mano) + Ricotta (125g) + Marmellata Light": {
        "max": 2, "slot": "Colazione", "default_kcal": 360,
        "desc": "Pane di segale (1 mano) + 1/2 vasetto ricotta (125g) + 3 cucchiaini marmellata Hero Light"
    },
    "Pranzo [2x] - Pasta corta al sugo leggero (1 pugno) + Verdura cotta": {
        "max": 2, "slot": "Pranzo", "default_kcal": 460,
        "desc": "Pasta corta con sugo leggero (1 pugno schiacciato) + verdura cotta (1 pugno)"
    },
    "Pranzo [2x] - Secondo di Carne (1 mano) + Verdura cotta + 2 Gallette": {
        "max": 2, "slot": "Pranzo", "default_kcal": 430,
        "desc": "Carne semplice (1 mano) + verdura cotta (1 pugno) + 2 gallette norvegesi"
    },
    "Pranzo [2x] - Insalatone con 2 Uova sode + Ortaggi + 2 Gallette": {
        "max": 2, "slot": "Pranzo", "default_kcal": 390,
        "desc": "Insalatone crudo + 2 uova sode + 2 gallette norvegesi"
    },
    "Pranzo [1x] - Zuppa di legumi (1 fondina 250ml) + 2 Gallette": {
        "max": 1, "slot": "Pranzo", "default_kcal": 380,
        "desc": "Zuppa di legumi (250 ml) + 2 gallette norvegesi"
    },
    "Spuntino [7x] - Frutta fresca di stagione (1 pugno)": {
        "max": 7, "slot": "Spuntino", "default_kcal": 90,
        "desc": "1 pugno di frutta fresca di stagione"
    },
    "Cena [2x] - Pesce semplice (1 mano) + Verdura cruda + 6 cucchiai Piselli": {
        "max": 2, "slot": "Cena", "default_kcal": 420,
        "desc": "Pesce semplice (1 mano) + verdura cruda + 6 cucchiai piselli"
    },
    "Cena [2x] - Minestrone (500ml) + Formaggio stagionato (2 dita)": {
        "max": 2, "slot": "Cena", "default_kcal": 440,
        "desc": "Minestrone (500 ml) + formaggio stagionato con crosta (2 dita)"
    },
    "Cena [1x] - Uova (2 unità) + Verdura cruda + 2 Gallette": {
        "max": 1, "slot": "Cena", "default_kcal": 380,
        "desc": "2 uova + verdura cruda + 2 gallette norvegesi"
    },
    "Cena [1x] - Petto di Pollo/Tacchino ai ferri + Verdura + 2 Gallette": {
        "max": 1, "slot": "Cena", "default_kcal": 390,
        "desc": "Petto di pollo o tacchino (1 mano) + verdura cruda + 2 gallette norvegesi"
    },
    "Cena [1x] - Pasto Libero con moderazione (senza pizza)": {
        "max": 1, "slot": "Cena", "default_kcal": 750,
        "desc": "1 pasto libero settimanale con moderazione (evitare la pizza)"
    },
    "Spuntino Serale [4x] - Cioccolato Fondente 85% Perugina Nero (2 quadretti)": {
        "max": 4, "slot": "Spuntino Serale", "default_kcal": 110,
        "desc": "2 quadretti di cioccolato fondente 85% Perugina Nero"
    }
}

# Conteggio quote settimana corrente
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

# --- Sidebar Impostazioni ---
st.sidebar.header("🎯 Target")
target_weight = st.sidebar.number_input("Target Peso (kg)", min_value=50.0, max_value=100.0, value=66.5, step=0.1)
target_daily_kcal = st.sidebar.number_input("Target Calorie (kcal)", min_value=1200, max_value=3500, value=1750, step=50)
target_water_cl = st.sidebar.number_input("Target Liquidi (cl)", min_value=100.0, max_value=500.0, value=250.0, step=10.0)

# Calcolo calorie di oggi
today_meals_db = cursor.execute("SELECT calories FROM handy_logs WHERE entry_date LIKE ?", (f"{today_str}%",)).fetchall()
tot_today_kcal = sum([m[0] for m in today_meals_db if m[0] is not None])

# --- FUNZIONE GEMINI CALCOLO CALORIE DA TESTO ---
def stima_calorie_con_gemini(descrizione_pasto: str):
    prompt = f"""
    Sei un nutrizionista esperto. L'utente ha consumato: "{descrizione_pasto}".
    Analizza gli alimenti e calcola:
    1. Calorie stimate (kcal totali)
    2. Carboidrati (g)
    3. Proteine (g)
    4. Grassi (g)
    5. Breve breakdown degli ingredienti (es. "Mela 150g ~75 kcal")

    Rispondi solo ed esclusivamente in formato JSON valido:
    {{
      "calories": 75,
      "carbs_g": 19.0,
      "protein_g": 0.5,
      "fat_g": 0.3,
      "breakdown": "Mela 150g (~75 kcal)"
    }}
    """
    try:
        resp = client.models.generate_content(
            model=ACTIVE_MODEL,
            contents=prompt,
            config={
                "response_mime_type": "application/json"
            }
        )
        # Pulisce eventuale markdown residuo
        cleaned_text = resp.text.strip().replace("```json", "").replace("```", "")
        return json.loads(cleaned_text)
    except Exception as e:
        st.error(f"Dettaglio errore Gemini: {e}")
        return None
Salva su GitHub: appena Streamlit si riavvia, riprova a cliccare ✨ Stima Calorie con Gemini. Se c'è ancora un problema di chiave, ti mostrerà il messaggio preciso invece di andare in blocco generico.
# --- HEADER STATO MOBILE (ISPIRATO AL DESIGN) ---
st.markdown(f"""
<div class="metric-card-green">
    <div style="display:flex; justify-content:space-between; align-items:center;">
        <div>
            <span style="font-size:11px; font-weight:800; text-transform:uppercase; letter-spacing:0.5px; opacity:0.8;">Handy Diet Planner</span>
            <h2 style="margin:2px 0 0 0; font-size:26px; font-weight:800; color:#14532d;">{tot_today_kcal} <span style="font-size:14px; font-weight:600;">kcal oggi</span></h2>
        </div>
        <div style="text-align:right;">
            <span style="font-size:12px; font-weight:700; color:#15803d;">Target: {target_daily_kcal}</span><br>
            <span style="font-size:11px; color:#166534;">{max(0, target_daily_kcal - tot_today_kcal)} kcal rimaste</span>
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

# --- NAVIGAZIONE A TABS ---
tab_register, tab_quotas, tab_water, tab_weight, tab_suggest = st.tabs([
    "📝 Pasti & Diario",
    "📊 Monte Quote",
    "💧 Liquidi",
    "⚖️ Peso",
    "💡 Idee Pasto"
])

# ==========================================
# === TAB 1: REGISTRA PASTO & CALENDARIO ===
# ==========================================
with tab_register:
    st.markdown("### 📝 Registra Nuovo Pasto")
    
    col_t1, col_t2 = st.columns(2)
    with col_t1:
        reg_date = st.date_input("Data:", value=date.today(), key="reg_date_input")
    with col_t2:
        reg_time = st.time_input("Ora del pasto:", value=now.time(), step=300, key="reg_time_input")

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
            "Quota da scalare:",
            cat_choices,
            format_func=lambda x: f"{x.split(' - ')[-1][:28]}... ({remaining_options.get(x, {}).get('remaining', 0)}/{remaining_options.get(x, {}).get('max', 0)})"
        )

    meal_desc = st.text_input(
        "Descrivi cosa hai mangiato:",
        value=st.session_state.get("meal_desc_input", WEEKLY_QUOTAS.get(sel_category, {}).get("desc", "")),
        placeholder="Es. Riso in bianco 90g con 1 filo d'olio e petto di pollo 150g",
        key="meal_desc_field"
    )

    # --- TRIGGER CALCOLO GEMINI ---
    col_gem1, col_gem2 = st.columns([1.5, 1])
    with col_gem1:
        if st.button("✨ Stima Calorie con Gemini", use_container_width=True):
            if meal_desc.strip():
                with st.spinner("Gemini sta analizzando gli ingredienti..."):
                    gemini_res = stima_calorie_con_gemini(meal_desc)
                    if gemini_res:
                        st.session_state["gemini_calc"] = gemini_res
                        st.toast(f"Calcolate: {gemini_res.get('calories')} kcal!")
                    else:
                        st.error("Errore nel calcolo. Verifica la connessione API.")
            else:
                st.warning("Descrivi prima cosa hai mangiato.")

    # Valore calorie calcolato o standard
    gemini_data = st.session_state.get("gemini_calc", None)
    if gemini_data:
        default_cal = int(gemini_data.get("calories", 400))
        st.info(f"💡 **Calcolo Gemini:** {gemini_data.get('breakdown')} (C: {gemini_data.get('carbs_g')}g | P: {gemini_data.get('protein_g')}g | G: {gemini_data.get('fat_g')}g)")
    else:
        default_cal = WEEKLY_QUOTAS.get(sel_category, {}).get("default_kcal", 350)

    with col_gem2:
        final_kcal = st.number_input("Kcal Stimate:", min_value=0, max_value=3000, value=default_cal, step=10)

    manual_score = st.slider("Voto aderenza (1-10):", min_value=1, max_value=10, value=10)

    with st.expander("📷 Allega foto del piatto (opzionale)"):
        img_file = st.file_uploader("Carica immagine", type=["jpg", "jpeg", "png"])
        if img_file:
            st.image(Image.open(img_file), use_container_width=True)

    if st.button("💾 Registra Pasto & Scala Quota", type="primary", use_container_width=True):
        saved_img_path = None
        if img_file:
            timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            saved_img_path = f"meal_images/{timestamp_str}.jpg"
            Image.open(img_file).save(saved_img_path)

        reg_week_str = reg_date.strftime("%Y-W%W")
        time_str = reg_time.strftime("%H:%M")

        carbs_val = gemini_data.get("carbs_g", 0.0) if gemini_data else 0.0
        prot_val = gemini_data.get("protein_g", 0.0) if gemini_data else 0.0
        fat_val = gemini_data.get("fat_g", 0.0) if gemini_data else 0.0

        cursor.execute("""
            INSERT INTO handy_logs (
                entry_date, entry_time, week_number, meal_type, category_consumed,
                score, plate_summary, calories, carbs_g, protein_g, fat_g, verdict, image_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            reg_date.isoformat(),
            time_str,
            reg_week_str,
            sel_slot,
            sel_category,
            manual_score,
            meal_desc if meal_desc.strip() else sel_category.split(" - ")[-1],
            final_kcal,
            carbs_val, prot_val, fat_val,
            "Registrato",
            saved_img_path
        ))
        conn.commit()
        st.session_state.pop("gemini_calc", None)
        st.toast(f"Pasto salvato per le {time_str}!")
        st.rerun()

    # ==============================================================
    # --- CALENDARIO A GRIGLIA CLICCABILE (ISPIRATO AL DESIGN) ---
    # ==============================================================
    st.divider()
    st.markdown("### 📅 Diario & Calendario Pasti")
    
    # Calcolo settimana attiva per il selettore giorni
    if "selected_calendar_date" not in st.session_state:
        st.session_state["selected_calendar_date"] = date.today()

    active_date = st.session_state["selected_calendar_date"]
    start_of_week = active_date - timedelta(days=active_date.weekday())
    
    # Mese e Anno con freccette
    col_nav1, col_nav2, col_nav3 = st.columns([1, 3, 1])
    with col_nav1:
        if st.button("◀", use_container_width=True, help="Settimana precedente"):
            st.session_state["selected_calendar_date"] -= timedelta(days=7)
            st.rerun()
    with col_nav2:
        st.markdown(f"<div style='text-align:center; font-weight:800; font-size:15px; padding-top:6px;'>{active_date.strftime('%B %Y').capitalize()}</div>", unsafe_allow_html=True)
    with col_nav3:
        if st.button("▶", use_container_width=True, help="Settimana successiva"):
            st.session_state["selected_calendar_date"] += timedelta(days=7)
            st.rerun()

    # Riga con i 7 giorni cliccabili a pillola
    day_cols = st.columns(7)
    day_names = ["L", "M", "M", "G", "V", "S", "D"]
    
    for i in range(7):
        current_day_date = start_of_week + timedelta(days=i)
        is_selected = (current_day_date == active_date)
        is_today = (current_day_date == date.today())
        
        btn_label = f"{day_names[i]}\n{current_day_date.day:02d}"
        
        with day_cols[i]:
            btn_type = "primary" if is_selected else "secondary"
            if st.button(btn_label, key=f"cal_btn_{current_day_date.isoformat()}", use_container_width=True, type=btn_type):
                st.session_state["selected_calendar_date"] = current_day_date
                st.rerun()

    # Visualizzazione pasti per il giorno selezionato
    view_date_str = active_date.isoformat()
    day_meals = cursor.execute("""
        SELECT id, entry_time, meal_type, category_consumed, plate_summary, score, calories, image_path 
        FROM handy_logs 
        WHERE entry_date LIKE ? 
        ORDER BY entry_time ASC, id ASC
    """, (f"{view_date_str}%",)).fetchall()

    tot_cal_day = sum([m[6] for m in day_meals if m[6] is not None])
    
    st.markdown(f"""
    <div style="background:#f8fafc; border-radius:16px; padding:12px; margin-top:14px; display:flex; justify-content:space-between; align-items:center;">
        <div>
            <span style="font-size:11px; font-weight:700; color:#64748b; text-transform:uppercase;">Giorno selezionato</span>
            <div style="font-size:15px; font-weight:800; color:#0f172a;">{active_date.strftime('%A %d %B %Y').capitalize()}</div>
        </div>
        <div style="text-align:right;">
            <span style="font-size:11px; font-weight:700; color:#64748b;">Totale</span>
            <div style="font-size:16px; font-weight:800; color:#16a34a;">{tot_cal_day} kcal</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    if not day_meals:
        st.info("Nessun pasto registrato per questa data.")
    else:
        for m in day_meals:
            m_id, m_time, m_slot, m_cat, m_sum, m_score, m_kcal, m_img = m
            
            with st.expander(f"🕒 **{m_time or '--:--'}** | **{m_slot}** — {m_kcal} kcal"):
                st.markdown(f"**Descrizione:** {m_sum}")
                st.caption(f"Categoria Handy: `{m_cat}` | Voto: **{m_score}/10**")
                
                if m_img and os.path.exists(m_img):
                    st.image(m_img, width=200)

                col_b1, col_b2 = st.columns(2)
                with col_b1:
                    with st.popover("✏️ Modifica"):
                        new_sum = st.text_input("Descrizione", value=m_sum, key=f"e_s_{m_id}")
                        new_kc = st.number_input("Kcal", value=int(m_kcal or 0), step=10, key=f"e_k_{m_id}")
                        if st.button("Salva", key=f"save_m_{m_id}"):
                            cursor.execute("UPDATE handy_logs SET plate_summary = ?, calories = ? WHERE id = ?", (new_sum, new_kc, m_id))
                            conn.commit()
                            st.rerun()
                with col_b2:
                    if st.button("🗑️ Elimina", key=f"del_m_{m_id}", type="secondary", use_container_width=True):
                        cursor.execute("DELETE FROM handy_logs WHERE id = ?", (m_id,))
                        conn.commit()
                        st.toast("Pasto eliminato e quota ripristinata!")
                        st.rerun()

# ==========================================
# === TAB 2: MONTE SETTIMANALE MOBILE ======
# ==========================================
with tab_quotas:
    st.markdown(f"### 📊 Monte Settimanale Quote")
    st.caption(f"Settimana: **{current_week_str}** — Scorri in verticale le tue quote")

    for slot_name in ["Colazione", "Pranzo", "Spuntino", "Cena", "Spuntino Serale"]:
        slot_items = {k: v for k, v in remaining_options.items() if v["slot"] == slot_name}
        if slot_items:
            st.markdown(f"#### 🍽️ {slot_name}")
            for cat_title, data in slot_items.items():
                short_title = cat_title.split(" - ")[-1]
                used = data["used"]
                max_val = data["max"]
                rem = data["remaining"]
                
                # Progresso quota
                pct = min(1.0, used / max_val) if max_val > 0 else 0
                status_color = "#16a34a" if rem > 0 else "#dc2626"
                
                st.markdown(f"""
                <div class="card-white">
                    <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:6px;">
                        <span style="font-size:13px; font-weight:700; color:#1e293b; max-width:78%;">{short_title}</span>
                        <span style="font-size:12px; font-weight:800; color:{status_color};">{used}/{max_val}</span>
                    </div>
                </div>
                """, unsafe_allow_html=True)
                st.progress(pct)

    st.divider()
    st.markdown("### ⚙️ Gestione Rapida Quote")
    st.caption("Aggiungi o togli al volo senza compilare moduli:")
    
    sel_cat_manage = st.selectbox("Seleziona categoria:", list(WEEKLY_QUOTAS.keys()), key="sel_cat_mng_quick")
    col_q1, col_q2 = st.columns(2)
    with col_q1:
        if st.button("➖ Rimuovi Ultimo", use_container_width=True):
            last_id = cursor.execute("""
                SELECT id FROM handy_logs 
                WHERE week_number = ? AND category_consumed = ? 
                ORDER BY id DESC LIMIT 1
            """, (current_week_str, sel_cat_manage)).fetchone()
            if last_id:
                cursor.execute("DELETE FROM handy_logs WHERE id = ?", (last_id[0],))
                conn.commit()
                st.toast(f"Quota rimossa!")
                st.rerun()
            else:
                st.warning("Nessun pasto registrato per questa voce.")
    with col_q2:
        if st.button("➕ Scala 1 Rapido", use_container_width=True, type="primary"):
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
                auto_k, 0.0, 0.0, 0.0, "Rapido", None
            ))
            conn.commit()
            st.toast("1 quota scalata!")
            st.rerun()

# ==========================================
# === TAB 3: ACQUA, TISANE & CAFFÈ =========
# ==========================================
with tab_water:
    st.markdown("### 💧 Monitor Liquidi di Oggi")
    
    h_data = cursor.execute("""
        SELECT water_cl, tea_cl, coffee_espresso, coffee_lungo, coffee_americano, coffee_americano_large 
        FROM hydration_logs WHERE entry_date = ?
    """, (today_str,)).fetchone()

    cur_water_cl = float(h_data[0] or 0) if h_data else 0.0
    cur_tea_cl = float(h_data[1] or 0) if h_data else 0.0
    cur_espresso = int(h_data[2] or 0) if h_data else 0
    cur_lungo = int(h_data[3] or 0) if h_data else 0
    cur_americano = int(h_data[4] or 0) if h_data else 0
    cur_americano_large = int(h_data[5] or 0) if h_data else 0

    coffee_cl = (cur_espresso * 3.0) + (cur_lungo * 6.0) + (cur_americano * 15.0) + (cur_americano_large * 50.0)
    total_liquids_cl = cur_water_cl + cur_tea_cl

    pct_water = min(1.0, total_liquids_cl / target_water_cl) if target_water_cl > 0 else 1.0

    st.markdown(f"""
    <div class="card-white" style="text-align:center;">
        <span style="font-size:12px; font-weight:700; color:#64748b;">IDRATAZIONE TOTALE</span>
        <h1 style="margin:4px 0; font-size:32px; font-weight:800; color:#0284c7;">{total_liquids_cl:.0f} <span style="font-size:16px;">/ {target_water_cl:.0f} cl</span></h1>
        <span style="font-size:12px; color:#0369a1;">Acqua: {cur_water_cl:.0f} cl | Tisane: {cur_tea_cl:.0f} cl</span>
    </div>
    """, unsafe_allow_html=True)
    st.progress(pct_water)

    st.markdown("#### 🥛 Acqua (cl)")
    w1, w2, w3 = st.columns(3)
    with w1:
        if st.button("+25 cl (Bicchiere)", use_container_width=True):
            cursor.execute("UPDATE hydration_logs SET water_cl = water_cl + 25 WHERE entry_date = ?", (today_str,))
            conn.commit()
            st.rerun()
    with w2:
        if st.button("+50 cl (Borraccia)", use_container_width=True, type="primary"):
            cursor.execute("UPDATE hydration_logs SET water_cl = water_cl + 50 WHERE entry_date = ?", (today_str,))
            conn.commit()
            st.rerun()
    with w3:
        if st.button("+100 cl (1 Litro)", use_container_width=True):
            cursor.execute("UPDATE hydration_logs SET water_cl = water_cl + 100 WHERE entry_date = ?", (today_str,))
            conn.commit()
            st.rerun()

    st.markdown("#### 🫖 Tisane (cl)")
    t1, t2 = st.columns(2)
    with t1:
        if st.button("+25 cl (Tazza classica)", use_container_width=True):
            cursor.execute("UPDATE hydration_logs SET tea_cl = tea_cl + 25 WHERE entry_date = ?", (today_str,))
            conn.commit()
            st.rerun()
    with t2:
        if st.button("+50 cl (Tazzone / Mug)", use_container_width=True, type="primary"):
            cursor.execute("UPDATE hydration_logs SET tea_cl = tea_cl + 50 WHERE entry_date = ?", (today_str,))
            conn.commit()
            st.rerun()

    st.markdown("#### ☕ Caffè")
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.caption(f"Espresso: **{cur_espresso}**")
        if st.button("☕ +1", key="btn_esp", use_container_width=True):
            cursor.execute("UPDATE hydration_logs SET coffee_espresso = coffee_espresso + 1 WHERE entry_date = ?", (today_str,))
            conn.commit()
            st.rerun()
    with k2:
        st.caption(f"Lungo: **{cur_lungo}**")
        if st.button("☕ +1", key="btn_lng", use_container_width=True):
            cursor.execute("UPDATE hydration_logs SET coffee_lungo = coffee_lungo + 1 WHERE entry_date = ?", (today_str,))
            conn.commit()
            st.rerun()
    with k3:
        st.caption(f"Amer. 15cl: **{cur_americano}**")
        if st.button("☕ +1", key="btn_am15", use_container_width=True):
            cursor.execute("UPDATE hydration_logs SET coffee_americano = coffee_americano + 1 WHERE entry_date = ?", (today_str,))
            conn.commit()
            st.rerun()
    with k4:
        st.caption(f"Amer. 50cl: **{cur_americano_large}**")
        if st.button("☕ +1 (Mug)", key="btn_am50", use_container_width=True, type="primary"):
            cursor.execute("UPDATE hydration_logs SET coffee_americano_large = coffee_americano_large + 1 WHERE entry_date = ?", (today_str,))
            conn.commit()
            st.rerun()

# ==========================================
# === TAB 4: CONTROLLO PESO ================
# ==========================================
with tab_weight:
    st.markdown("### ⚖️ Controllo Peso Corporeo")

    col_pw1, col_pw2 = st.columns([1.5, 1])
    with col_pw1:
        w_val = st.number_input("Nuova Pesata (kg):", min_value=40.0, max_value=150.0, value=70.0, step=0.05, format="%.2f")
    with col_pw2:
        w_d = st.date_input("Data pesata:", value=date.today())

    if st.button("💾 Registra Pesata", type="primary", use_container_width=True):
        cursor.execute("""
            INSERT INTO weight_logs (entry_date, weight_kg)
            VALUES (?, ?)
            ON CONFLICT(entry_date) DO UPDATE SET weight_kg = excluded.weight_kg
        """, (w_d.isoformat(), w_val))
        conn.commit()
        st.toast("Peso salvato!")
        st.rerun()

    df_w = pd.read_sql_query("SELECT entry_date, weight_kg FROM weight_logs ORDER BY entry_date ASC", conn)
    if not df_w.empty:
        df_w["entry_date"] = pd.to_datetime(df_w["entry_date"])
        latest_w = df_w.iloc[-1]["weight_kg"]
        min_historical = df_w["weight_kg"].min()
        
        diff_target = latest_w - target_weight

        st.markdown(f"""
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-top:14px;">
            <div class="card-white" style="text-align:center;">
                <span style="font-size:11px; font-weight:700; color:#64748b;">ULTIMO PESO</span>
                <h3 style="margin:4px 0; color:#0f172a;">{latest_w:.2f} kg</h3>
            </div>
            <div class="card-white" style="text-align:center;">
                <span style="font-size:11px; font-weight:700; color:#64748b;">TARGET</span>
                <h3 style="margin:4px 0; color:{'#16a34a' if diff_target <= 0 else '#eab308'};">{diff_target:+.2f} kg</h3>
            </div>
        </div>
        """, unsafe_allow_html=True)

        chart_df = df_w.set_index("entry_date")[["weight_kg"]]
        chart_df["Target"] = target_weight
        st.line_chart(chart_df)

# ==========================================
# === TAB 5: IDEE PASTO ====================
# ==========================================
with tab_suggest:
    st.markdown(f"### 💡 Proposte per {current_slot}")
    slot_available = [
        {"categoria": k, "rimaste": v["remaining"], "massimo": v["max"], "descrizione": v["desc"]}
        for k, v in remaining_options.items()
        if v["slot"] == current_slot and v["remaining"] > 0
    ]

    if not slot_available:
        st.warning(f"Hai esaurito le opzioni per {current_slot} questa settimana.")
    else:
        if st.button("✨ Suggerisci 3 Piatti con Gemini", type="primary", use_container_width=True):
            with st.spinner("Calcolo proposte in corso..."):
                prompt = f"""
                Sei il nutrizionista dell'utente (metodo Handy Diet del Dr. Michele Sculati).
                È l'orario di: {current_slot}.
                Quote rimanenti disponibili:
                {json.dumps(slot_available, ensure_ascii=False)}

                Genera 3 proposte pratiche per comporre il piatto usando SOLO le categorie rimaste.
                Rispondi in JSON:
                {{
                  "proposals": [
                    {{"title": "Titolo", "matched_category": "Categoria esatta", "hotel_advice": "Istruzioni pratiche", "why": "Motivo"}}
                  ]
                }}
                """
                try:
                    resp = client.models.generate_content(
                        model=ACTIVE_MODEL, contents=prompt, config={"response_mime_type": "application/json"}
                    )
                    st.session_state["suggestions"] = json.loads(resp.text).get("proposals", [])
                except Exception:
                    st.error("Errore nel recupero delle proposte.")

        if "suggestions" in st.session_state:
            for p in st.session_state["suggestions"]:
                st.markdown(f"""
                <div class="card-white">
                    <span class="meal-pill">{p.get('matched_category').split(' - ')[-1][:30]}</span>
                    <h4 style="margin:4px 0; font-weight:800;">{p.get('title')}</h4>
                    <p style="font-size:12px; color:#475569; margin-bottom:4px;">🍽️ {p.get('hotel_advice')}</p>
                    <small style="color:#94a3b8;">{p.get('why')}</small>
                </div>
                """, unsafe_allow_html=True)
