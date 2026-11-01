import os
import json
import sqlite3
import time
from datetime import datetime, date
from PIL import Image
import pandas as pd
import streamlit as st
from google import genai

st.set_page_config(page_title="Handy Diet Weekly Planner", page_icon="🥗", layout="centered")

# --- Inizializzazione Database SQLite ---
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

# Tabella idratazione e tipologie di caffè
cursor.execute("""
    CREATE TABLE IF NOT EXISTS hydration_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_date TEXT UNIQUE,
        water_ml INTEGER DEFAULT 0,
        coffee_count INTEGER DEFAULT 0,
        coffee_espresso INTEGER DEFAULT 0,
        coffee_lungo INTEGER DEFAULT 0,
        coffee_americano INTEGER DEFAULT 0
    )
""")

existing_hyd_cols = [col[1] for col in cursor.execute("PRAGMA table_info(hydration_logs)").fetchall()]
for col_name in ["coffee_espresso", "coffee_lungo", "coffee_americano"]:
    if col_name not in existing_hyd_cols:
        cursor.execute(f"ALTER TABLE hydration_logs ADD COLUMN {col_name} INTEGER DEFAULT 0")

conn.commit()

# Cartella per archiviare le foto dei pasti
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
ACTIVE_MODEL = "gemini-3.8-flash"

# --- Calcolo Orario e Pasto Attuale ---
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
    INSERT OR IGNORE INTO hydration_logs (entry_date, water_ml, coffee_count, coffee_espresso, coffee_lungo, coffee_americano) 
    VALUES (?, 0, 0, 0, 0, 0)
""", (today_str,))
conn.commit()

# --- Quote Settimanali Prescritte (The Handy Diet) ---
WEEKLY_QUOTAS = {
    # COLAZIONE (Totale: 7)
    "Colazione [5x] - Yogurt Intero + Noci (5) + Fiocchi di Avena (5 cucchiai)": {
        "max": 5, "slot": "Colazione",
        "desc": "Yogurt intero (250 ml) + 5 noci secche + 5 cucchiai fiocchi di avena"
    },
    "Colazione [2x] - Pane di Segale (1 mano) + Ricotta (125g) + Marmellata Light": {
        "max": 2, "slot": "Colazione",
        "desc": "Pane di segale (1 mano) + 1/2 vasetto ricotta (125g) + 3 cucchiaini marmellata Hero Light"
    },

    # PRANZO (Totale: 7)
    "Pranzo [2x] - Pasta corta al sugo leggero (1 pugno schiacciato) + Verdura cotta": {
        "max": 2, "slot": "Pranzo",
        "desc": "Pasta corta con sugo leggero (1 pugno schiacciato) + verdura cotta condita (1 pugno)"
    },
    "Pranzo [2x] - Secondo di Carne (1 mano) + Verdura cotta + 2 Gallette": {
        "max": 2, "slot": "Pranzo",
        "desc": "Carne semplice (1 mano) + verdura cotta (1 pugno) + 2 gallette norvegesi"
    },
    "Pranzo [2x] - Insalatone con 2 Uova sode + Ortaggi misti + 2 Gallette": {
        "max": 2, "slot": "Pranzo",
        "desc": "Insalatone (1.5 pugni cruda + 1 pugno ortaggi) + 2 uova sode + 2 gallette norvegesi"
    },
    "Pranzo [1x] - Zuppa di legumi (1 fondina 250ml) + 2 Gallette": {
        "max": 1, "slot": "Pranzo",
        "desc": "Zuppa di legumi (250 ml) + 2 gallette norvegesi"
    },

    # SPUNTINO POMERIDIANO (Totale: 7)
    "Spuntino [7x] - Frutta fresca di stagione (1 pugno)": {
        "max": 7, "slot": "Spuntino",
        "desc": "1 pugno di frutta fresca di stagione"
    },

    # CENA (Totale: 7)
    "Cena [2x] - Pesce semplice (1 mano) + Verdura cruda + 6 cucchiai Piselli": {
        "max": 2, "slot": "Cena",
        "desc": "Pesce semplice ai ferri/vapore (1 mano) + verdura cruda (1.5 pugni) + 6 cucchiai piselli in padella"
    },
    "Cena [2x] - Minestrone (500ml) + Formaggio stagionato (2 dita)": {
        "max": 2, "slot": "Cena",
        "desc": "Minestrone con legumi e patate (500 ml) + formaggio stagionato con crosta (2 dita)"
    },
    "Cena [1x] - Uova (2 unità tegamino/frittata) + Verdura cruda + 2 Gallette": {
        "max": 1, "slot": "Cena",
        "desc": "2 uova al tegamino/strapazzate/frittata + verdura cruda (1.5 pugni) + 2 gallette norvegesi"
    },
    "Cena [1x] - Petto di Pollo/Tacchino ai ferri (1 mano) + Verdura cruda + 2 Gallette": {
        "max": 1, "slot": "Cena",
        "desc": "Petto di pollo o tacchino ai ferri (1 mano) + verdura cruda (1.5 pugni) + 2 gallette norvegesi"
    },
    "Cena [1x] - Pasto Libero con moderazione (senza pizza)": {
        "max": 1, "slot": "Cena",
        "desc": "1 pasto libero settimanale con moderazione (evitare la pizza)"
    },

    # SPUNTINO SERALE (Totale: 4)
    "Spuntino Serale [4x] - Cioccolato Fondente 85% Perugina Nero (2 quadretti)": {
        "max": 4, "slot": "Spuntino Serale",
        "desc": "2 quadretti di cioccolato fondente 85% Perugina Nero"
    }
}

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
        "desc": meta["desc"]
    }

# --- Sidebar ---
st.sidebar.header("🎯 Impostazioni")
target_weight = st.sidebar.number_input("Target Peso Corporeo (kg)", min_value=50.0, max_value=150.0, value=75.0, step=0.5)
target_water_l = st.sidebar.number_input("Target Acqua/Tisane (Litri)", min_value=1.5, max_value=5.0, value=2.5, step=0.1)

st.sidebar.divider()
st.sidebar.markdown(f"**Settimana attiva:** `{current_week_str}`")
st.sidebar.caption("Il monte alimenti si resetta automaticamente alla fine della domenica sera.")
if st.sidebar.button("🔄 Reset Manuale Settimana"):
    cursor.execute("DELETE FROM handy_logs WHERE week_number = ?", (current_week_str,))
    conn.commit()
    st.toast("Settimana azzerata!")
    st.rerun()

# Proposta locale di emergenza
def generate_offline_suggestions(available_items: list):
    proposals = []
    for it in available_items[:3]:
        cat_name = it["categoria"]
        clean_name = cat_name.split(" - ")[-1]
        proposals.append({
            "title": f"Piatto consigliato: {clean_name}",
            "matched_category": cat_name,
            "hotel_advice": f"Al buffet componi: {it['descrizione']}. Condisci con max 1 cucchiaio olio EVO ed evita intingoli.",
            "why": f"Hai ancora {it['rimaste']} porzione/i rimanente/i da completare questa settimana."
        })
    return {"proposals": proposals}

def get_meal_suggestions(meal_slot: str, available_items: list):
    prompt = f"""
    Sei il nutrizionista dell'utente (metodo Handy Diet Low Carb).
    È il momento del pasto: **{meal_slot}**.
    L'utente è in albergo/ristorante. Opzioni DISPONIBILI per {meal_slot}:
    {json.dumps(available_items, ensure_ascii=False, indent=2)}

    Genera 3 proposte pratiche per chi mangia al ristorante/buffet, usando SOLO le opzioni sopra.
    Rispondi esclusivamente con questo formato JSON:
    {{
      "proposals": [
        {{
          "title": "Titolo proposta",
          "matched_category": "Nome esatto della categoria rimasta",
          "hotel_advice": "Istruzioni per il buffet (es. 1 mano di pesce al vapore + verdura mista a crudo)",
          "why": "Motivazione"
        }}
      ]
    }}
    """
    for _ in range(2):
        try:
            resp = client.models.generate_content(
                model=ACTIVE_MODEL,
                contents=prompt,
                config={"response_mime_type": "application/json"}
            )
            return json.loads(resp.text)
        except Exception:
            time.sleep(1)
    return generate_offline_suggestions(available_items)

def evaluate_meal_photo(image: Image.Image, meal_slot: str, chosen_category: str):
    prompt = f"""
    Sei il nutrizionista personale dell'utente (metodo Handy Diet Low Carb).
    Pasto in corso: {meal_slot}.
    Categoria selezionata: {chosen_category}.
    
    Analizza la foto del pasto scattata in hotel:
    1. Verifica proporzioni handy diet (1 mano di proteine, verdura a pugno, assenza di pane comune/fritti/dolci).
    2. Se l'utente mostra frutta secca (es. noci o mandorle), conta o stima il numero di pezzi rispetto alla prescrizione (5 noci o 15 mandorle).
    3. Calcola calorie e macro stimati.
    4. Assegna un voto di conformità da 1 a 10.
    5. Fornisci un consiglio immediato per l'albergo.

    Rispondi RIGOROSAMENTE con questo formato JSON:
    {{
      "plate_summary": "Descrizione del cibo nel piatto o nella mano",
      "compliance_score": 9,
      "distance_verdict": "Sintesi scostamento rispetto al piano",
      "actionable_hotel_tip": "Consiglio pratico per l'albergo",
      "excesses": ["cosa c'è di troppo"],
      "missing": ["cosa manca"],
      "total_calories": 180,
      "carbs_g": 3.0,
      "protein_g": 4.0,
      "fat_g": 18.0
    }}
    """
    last_err = ""
    for _ in range(2):
        try:
            resp = client.models.generate_content(
                model=ACTIVE_MODEL,
                contents=[image, prompt],
                config={"response_mime_type": "application/json"}
            )
            return json.loads(resp.text)
        except Exception as e:
            last_err = str(e)
            time.sleep(1)
    raise RuntimeError(f"Server Gemini occupati. Dettaglio: {last_err}")

# --- Interfaccia ---
st.title("🥗 Handy Diet Tracker")
st.caption(f"Orario rilevato: **{now.strftime('%H:%M')}** ➔ Pasto suggerito: **{current_slot}**")

tab_suggest, tab_register, tab_quotas, tab_water, tab_weight = st.tabs([
    "💡 Proposta Pasto",
    "📝 Registra Pasto",
    "📊 Monte Settimanale",
    "💧 Acqua & Caffè",
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
        st.warning(f"⚠️ Hai già completato tutti i piatti previsti per **{current_slot}**!")
    else:
        st.markdown(f"Hai ancora **{len(slot_available)} opzioni disponibili** per questo pasto.")
        if st.button("✨ Fatti suggerire 3 piatti da Gemini", type="primary"):
            with st.spinner("Calcolo proposte..."):
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

# === TAB 2: REGISTRAZIONE PASTO ===
with tab_register:
    st.subheader("Registra pasto & scala quota")

    col_s1, col_s2 = st.columns(2)
    with col_s1:
        sel_slot = st.selectbox("Pasto:", ["Colazione", "Pranzo", "Spuntino", "Cena", "Spuntino Serale"], index=["Colazione", "Pranzo", "Spuntino", "Cena", "Spuntino Serale"].index(current_slot))
    with col_s2:
        cat_choices = [k for k, v in remaining_options.items() if v["slot"] == sel_slot]
        if not cat_choices:
            cat_choices = list(WEEKLY_QUOTAS.keys())
        sel_category = st.selectbox(
            "Voce della dieta da scalare:",
            cat_choices,
            format_func=lambda x: f"{x} (Rimaste: {remaining_options.get(x, {}).get('remaining', 0)}/{remaining_options.get(x, {}).get('max', 0)})"
        )

    st.markdown("---")
    input_method = st.radio("Metodo di registrazione:", ["✍️ Inserimento a mano (+ Foto facoltativa per ricordo)", "📸 Analisi automatica foto con IA"], horizontal=True)

    if input_method == "✍️ Inserimento a mano (+ Foto facoltativa per ricordo)":
        meal_desc = st.text_input("Descrivi cosa hai mangiato:", placeholder="Es. 5 noci e acqua oppure 1 trancio di orata con zucchine")
        
        st.caption("📷 Vuoi scattare o caricare la foto del pasto per archiviarla? (Opzionale)")
        photo_sub = st.radio("Sorgente foto:", ["Nessuna foto", "Scatta foto", "Carica da galleria"], horizontal=True)
        img_file = None
        if photo_sub == "Scatta foto":
            img_file = st.camera_input("Scatta per il diario")
        elif photo_sub == "Carica da galleria":
            img_file = st.file_uploader("Scegli immagine", type=["jpg", "jpeg", "png"])

        if img_file:
            st.image(Image.open(img_file), caption="Foto salvata per il diario", use_container_width=True)

        col_m1, col_m2 = st.columns(2)
        with col_m1:
            manual_kcal = st.number_input("Calorie stimate (kcal)", min_value=0, max_value=2500, value=200, step=10)
        with col_m2:
            manual_score = st.slider("Voto aderenza alla dieta (1-10)", min_value=1, max_value=10, value=9)

        if st.button("💾 Salva e Scala Subito", type="primary"):
            if not meal_desc.strip():
                meal_desc = sel_category.split(" - ")[-1]
            
            saved_img_path = None
            if img_file:
                timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
                saved_img_path = f"meal_images/{timestamp_str}.jpg"
                Image.open(img_file).save(saved_img_path)

            cursor.execute("""
                INSERT INTO handy_logs (
                    entry_date, entry_time, week_number, meal_type, category_consumed,
                    score, plate_summary, calories, carbs_g, protein_g, fat_g, verdict, image_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                date.today().isoformat(),
                datetime.now().strftime("%H:%M"),
                current_week_str,
                sel_slot,
                sel_category,
                manual_score,
                meal_desc,
                manual_kcal,
                10.0,
                15.0,
                10.0,
                "Registrato a mano",
                saved_img_path
            ))
            conn.commit()
            st.toast(f"Pasto registrato con successo! Scalata 1 quota da: {sel_category}")
            st.rerun()

    else:
        mode_cam = st.radio("Foto da:", ["Fotocamera", "Carica da galleria"], horizontal=True)
        pic = st.camera_input("Inquadra il piatto") if mode_cam == "Fotocamera" else st.file_uploader("Carica file", type=["jpg", "jpeg", "png"])

        if pic:
            pil_pic = Image.open(pic)
            st.image(pil_pic, caption="Piatto pronto per l'analisi", use_container_width=True)

            if st.button("⚖️ Analizza con IA", type="primary"):
                with st.spinner("Valutazione piatto con Gemini..."):
                    try:
                        res = evaluate_meal_photo(pil_pic, sel_slot, sel_category)
                        st.session_state["current_eval"] = res
                        st.session_state["saved_slot"] = sel_slot
                        st.session_state["saved_category"] = sel_category
                        st.session_state["temp_img"] = pil_pic
                    except Exception as e:
                        st.error(f"Errore IA: {e}")

            if "current_eval" in st.session_state:
                data = st.session_state["current_eval"]
                score = data.get("compliance_score", 5)

                st.divider()
                st.write(f"**Verdetto:** {data.get('distance_verdict')}")
                st.info(f"🏨 **Consiglio hotel:** {data.get('actionable_hotel_tip')}")

                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Kcal", f"{data.get('total_calories')} kcal")
                m2.metric("Carboidrati", f"{data.get('carbs_g')} g")
                m3.metric("Proteine", f"{data.get('protein_g')} g")
                m4.metric("Grassi", f"{data.get('fat_g')} g")

                if st.button("💾 Conferma e SCALA dal monte settimanale"):
                    saved_path = None
                    if "temp_img" in st.session_state:
                        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
                        saved_path = f"meal_images/{timestamp_str}.jpg"
                        st.session_state["temp_img"].save(saved_path)

                    cursor.execute("""
                        INSERT INTO handy_logs (
                            entry_date, entry_time, week_number, meal_type, category_consumed,
                            score, plate_summary, calories, carbs_g, protein_g, fat_g, verdict, image_path
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        date.today().isoformat(),
                        datetime.now().strftime("%H:%M"),
                        current_week_str,
                        st.session_state.get("saved_slot", sel_slot),
                        st.session_state.get("saved_category", sel_category),
                        score,
                        data.get("plate_summary"),
                        data.get("total_calories"),
                        data.get("carbs_g"),
                        data.get("protein_g"),
                        data.get("fat_g"),
                        data.get("distance_verdict"),
                        saved_path
                    ))
                    conn.commit()
                    st.toast(f"Pasto registrato! Scalato da: {st.session_state.get('saved_category')}")
                    del st.session_state["current_eval"]
                    st.rerun()

# === TAB 3: MONTE SETTIMANALE ===
with tab_quotas:
    st.subheader(f"Stato Settimana ({current_week_str})")
    table_data = []
    for cat, val in remaining_options.items():
        table_data.append({
            "Pasto": val["slot"],
            "Alimento / Preparazione": cat.split(" - ")[-1],
            "Fatti": val["used"],
            "Previsti": val["max"],
            "Rimanenti": val["remaining"]
        })
    df_quotas = pd.DataFrame(table_data)
    for s_name in ["Colazione", "Pranzo", "Spuntino", "Cena", "Spuntino Serale"]:
        sub = df_quotas[df_quotas["Pasto"] == s_name]
        if not sub.empty:
            st.markdown(f"#### 🍽️ {s_name}")
            st.dataframe(sub[["Alimento / Preparazione", "Fatti", "Previsti", "Rimanenti"]], hide_index=True)

    st.divider()
    st.subheader("Diario dei Pasti con Foto Salvate")
    past_meals = cursor.execute(
        "SELECT entry_date, entry_time, meal_type, category_consumed, plate_summary, score, calories, image_path FROM handy_logs WHERE week_number = ? ORDER BY id DESC",
        (current_week_str,)
    ).fetchall()

    if not past_meals:
        st.write("Nessun pasto registrato questa settimana.")
    else:
        for row in past_meals:
            p_date, p_time, p_slot, p_cat, p_summary, p_score, p_kcal, p_img = row
            with st.expander(f"🕒 {p_date} {p_time} - {p_slot}: {p_summary} (Voto: {p_score}/10 | {p_kcal} kcal)"):
                st.write(f"**Categoria scalata:** `{p_cat}`")
                if p_img and os.path.exists(p_img):
                    st.image(p_img, caption="Foto registrata", width=300)

# === TAB 4: IDRATAZIONE (ACQUA, TISANE & CAFFÈ SPECIFICI) ===
with tab_water:
    st.subheader("💧 Monitor Idratazione & Caffè di Oggi")
    
    h_data = cursor.execute("""
        SELECT water_ml, coffee_count, coffee_espresso, coffee_lungo, coffee_americano 
        FROM hydration_logs WHERE entry_date = ?
    """, (today_str,)).fetchone()

    current_water_ml = h_data[0] if h_data else 0
    current_coffee = h_data[1] if h_data else 0
    current_espresso = h_data[2] if (h_data and len(h_data) > 2 and h_data[2] is not None) else 0
    current_lungo = h_data[3] if (h_data and len(h_data) > 3 and h_data[3] is not None) else 0
    current_americano = h_data[4] if (h_data and len(h_data) > 4 and h_data[4] is not None) else 0

    current_water_l = current_water_ml / 1000.0
    remaining_water_l = max(0.0, target_water_l - current_water_l)
    progress_water = min(1.0, current_water_l / target_water_l)

    c_w1, c_w2, c_w3 = st.columns(3)
    c_w1.metric("Acqua / Tisane Bevute", f"{current_water_l:.2f} L", delta=f"{current_water_ml} ml")
    c_w2.metric("Mancanti al Target", f"{remaining_water_l:.2f} L", delta=f"Target: {target_water_l:.1f} L")
    c_w3.metric("Totale Caffè Oggi", f"{current_coffee} ☕")

    st.progress(progress_water)
    if current_water_l >= target_water_l:
        st.success(f"🎉 **Obiettivo idratazione raggiunto!** Hai superato i {target_water_l} litri oggi.")
    else:
        st.info(f"💡 Per raggiungere il target ti mancano circa **{int(remaining_water_l * 1000 / 250)} bicchieri** da 250 ml.")

    st.markdown("#### Aggiungi Liquidi")
    btn_col1, btn_col2, btn_col3 = st.columns(3)
    with btn_col1:
        if st.button("🥛 +1 Bicchiere (250 ml)", use_container_width=True):
            cursor.execute("UPDATE hydration_logs SET water_ml = water_ml + 250 WHERE entry_date = ?", (today_str,))
            conn.commit()
            st.rerun()
    with btn_col2:
        if st.button("🫖 +1 Tazza Tisana (250 ml)", use_container_width=True):
            cursor.execute("UPDATE hydration_logs SET water_ml = water_ml + 250 WHERE entry_date = ?", (today_str,))
            conn.commit()
            st.rerun()
    with btn_col3:
        if st.button("🧴 +1 Bottiglia / Borraccia (500 ml)", use_container_width=True):
            cursor.execute("UPDATE hydration_logs SET water_ml = water_ml + 500 WHERE entry_date = ?", (today_str,))
            conn.commit()
            st.rerun()

    st.markdown("---")
    st.markdown("#### Registro Tipologia Caffè")

    # Colonne per registrare Espresso, Lungo, Americano
    k_col1, k_col2, k_col3 = st.columns(3)
    with k_col1:
        st.metric("Espresso", f"{current_espresso}")
        if st.button("☕ +1 Espresso", use_container_width=True, type="primary"):
            cursor.execute("""
                UPDATE hydration_logs 
                SET coffee_espresso = coffee_espresso + 1, coffee_count = coffee_count + 1 
                WHERE entry_date = ?
            """, (today_str,))
            conn.commit()
            st.rerun()
        if current_espresso > 0:
            if st.button("↩️ -1 Espresso", use_container_width=True):
                cursor.execute("""
                    UPDATE hydration_logs 
                    SET coffee_espresso = max(0, coffee_espresso - 1), coffee_count = max(0, coffee_count - 1) 
                    WHERE entry_date = ?
                """, (today_str,))
                conn.commit()
                st.rerun()

    with k_col2:
        st.metric("Lungo", f"{current_lungo}")
        if st.button("☕ +1 Lungo", use_container_width=True, type="primary"):
            cursor.execute("""
                UPDATE hydration_logs 
                SET coffee_lungo = coffee_lungo + 1, coffee_count = coffee_count + 1 
                WHERE entry_date = ?
            """, (today_str,))
            conn.commit()
            st.rerun()
        if current_lungo > 0:
            if st.button("↩️ -1 Lungo", use_container_width=True):
                cursor.execute("""
                    UPDATE hydration_logs 
                    SET coffee_lungo = max(0, coffee_lungo - 1), coffee_count = max(0, coffee_count - 1) 
                    WHERE entry_date = ?
                """, (today_str,))
                conn.commit()
                st.rerun()

    with k_col3:
        st.metric("Americano", f"{current_americano}")
        if st.button("☕ +1 Americano", use_container_width=True, type="primary"):
            # L'americano aggiunge anche circa 150ml di acqua all'idratazione
            cursor.execute("""
                UPDATE hydration_logs 
                SET coffee_americano = coffee_americano + 1, coffee_count = coffee_count + 1, water_ml = water_ml + 150 
                WHERE entry_date = ?
            """, (today_str,))
            conn.commit()
            st.rerun()
        if current_americano > 0:
            if st.button("↩️ -1 Americano", use_container_width=True):
                cursor.execute("""
                    UPDATE hydration_logs 
                    SET coffee_americano = max(0, coffee_americano - 1), coffee_count = max(0, coffee_count - 1), water_ml = max(0, water_ml - 150) 
                    WHERE entry_date = ?
                """, (today_str,))
                conn.commit()
                st.rerun()

    with st.expander("Modifica manuale o azzera idratazione di oggi"):
        man_ml = st.number_input("Imposta ml acqua totali oggi", min_value=0, max_value=8000, value=current_water_ml, step=100)
        man_esp = st.number_input("Espresso", min_value=0, max_value=20, value=current_espresso, step=1)
        man_lng = st.number_input("Lungo", min_value=0, max_value=20, value=current_lungo, step=1)
        man_amr = st.number_input("Americano", min_value=0, max_value=20, value=current_americano, step=1)
        if st.button("Aggiorna valori manuali"):
            man_tot_caf = man_esp + man_lng + man_amr
            cursor.execute("""
                UPDATE hydration_logs 
                SET water_ml = ?, coffee_count = ?, coffee_espresso = ?, coffee_lungo = ?, coffee_americano = ? 
                WHERE entry_date = ?
            """, (man_ml, man_tot_caf, man_esp, man_lng, man_amr, today_str))
            conn.commit()
            st.rerun()

# === TAB 5: CONTROLLO PESO ===
with tab_weight:
    st.subheader("⚖️ Diario Peso Corporeo")
    w1, w2, w3 = st.columns([2, 2, 1.5])
    with w1:
        w_date = st.date_input("Data pesata", value=date.today())
    with w2:
        w_val = st.number_input("Peso (kg)", min_value=40.0, max_value=160.0, value=80.0, step=0.1, format="%.1f")
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
        init_w = df_w.iloc[0]["weight_kg"]
        d_tot = latest_w - init_w
        d_last = (latest_w - df_w.iloc[-2]["weight_kg"]) if len(df_w) > 1 else 0.0

        st.divider()
        k1, k2, k3 = st.columns(3)
        k1.metric("Peso Attuale", f"{latest_w:.1f} kg", delta=f"{d_last:+.1f} kg (vs ultima)" if len(df_w) > 1 else None, delta_color="inverse")
        k2.metric("Variazione Inizio", f"{d_tot:+.1f} kg", delta_color="inverse")
        k3.metric("Mancanti al Target", f"{latest_w - target_weight:+.1f} kg", delta=f"Target: {target_weight:.1f} kg")

        if d_last > 0.4:
            st.warning(f"⚠️ **ATTENZIONE: +{d_last:.1f} kg rispetto all'ultima pesata.**")
            st.info("💡 Oscillazione legata a ritenzione e sodio. Tieni alte le verdure, bevi i tuoi 2.5L di acqua e limita i carboidrati stasera.")
        else:
            st.success("✅ **Andamento regolare verso l'obiettivo.**")

        st.line_chart(df_w.set_index("entry_date")["weight_kg"])
