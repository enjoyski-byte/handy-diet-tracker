import os
import json
import sqlite3
import random
from datetime import datetime, date
from PIL import Image
import pandas as pd
import streamlit as st
from google import genai

st.set_page_config(page_title="Handy Diet Weekly Planner", page_icon="🥗", layout="centered")

# --- Inizializzazione Database SQLite con migrazione sicura ---
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
        verdict TEXT
    )
""")

existing_cols = [col[1] for col in cursor.execute("PRAGMA table_info(handy_logs)").fetchall()]
if "week_number" not in existing_cols:
    cursor.execute("ALTER TABLE handy_logs ADD COLUMN week_number TEXT")
if "category_consumed" not in existing_cols:
    cursor.execute("ALTER TABLE handy_logs ADD COLUMN category_consumed TEXT")
if "entry_time" not in existing_cols:
    cursor.execute("ALTER TABLE handy_logs ADD COLUMN entry_time TEXT")

cursor.execute("""
    CREATE TABLE IF NOT EXISTS weight_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_date TEXT UNIQUE,
        weight_kg REAL
    )
""")
conn.commit()

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

# Modelli con fallback automatico anti-overload
PRIMARY_MODEL = "gemini-2.5-flash"
FALLBACK_MODEL = "gemini-2.0-flash"

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

# --- Quote Settimanali Prescritte (The Handy Diet) ---
WEEKLY_QUOTAS = {
    # PRANZO
    "Pranzo - Pasta / Riso (pugno schiacciato)": {"max": 2, "slot": "Pranzo", "desc": "Primo pasta/riso + verdura cotta (1 pugno)"},
    "Pranzo - Carne / Bistecca / Arrosto (1 mano)": {"max": 2, "slot": "Pranzo", "desc": "Carne ai ferri/arrosto + verdura cruda (1 pugno e mezzo)"},
    "Pranzo - Salmone / Tonno / Pesce veloce": {"max": 1, "slot": "Pranzo", "desc": "Salmone affumicato 4 fette o tonno in vetro + verdura"},
    "Pranzo - Uova sode (2 unità)": {"max": 1, "slot": "Pranzo", "desc": "2 uova sode in insalatona mista"},
    "Pranzo - Zuppa di legumi (1 fondina)": {"max": 1, "slot": "Pranzo", "desc": "Zuppa di legumi (250 ml) + verdura cotta"},
    
    # CENA
    "Cena - Pesce semplice al vapore/ferri (1 mano)": {"max": 2, "slot": "Cena", "desc": "Pesce bianco/orata/merluzzo + verdura mista"},
    "Cena - Bastoncini / Croccole / Merluzzo erbe": {"max": 1, "slot": "Cena", "desc": "6 bastoncini Findus al forno o croccola ai semi + verdura"},
    "Cena - Uova strapazzate / tegamino / frittata (2 uova)": {"max": 1, "slot": "Cena", "desc": "2 uova in padella + verdura cruda (1 pugno e mezzo)"},
    "Cena - Petto di pollo / tacchino ai ferri (1 mano)": {"max": 1, "slot": "Cena", "desc": "Petto di pollo ai ferri (1 mano) + verdura"},
    "Cena - Minestrone verdure con legumi e patate (500 ml)": {"max": 2, "slot": "Cena", "desc": "1 scodella grande minestrone + 2 dita formaggio stagionato"},
    "Cena - Pasto Libero settimanale": {"max": 1, "slot": "Cena", "desc": "Pasto libero con moderazione (senza pizza)"},
    
    # SPUNTINI & COLAZIONE
    "Spuntino Pomeridiano - Frutta fresca (1 pugno)": {"max": 7, "slot": "Spuntino", "desc": "1 frutto intero (mela, pera, kiwi)"},
    "Spuntino Serale - Gallette + Noci/Mandorle o Cioccolato 85%": {"max": 4, "slot": "Spuntino Serale", "desc": "2 gallette norvegesi + 15 mandorle o 2 quadretti fondente 85%"},
    "Colazione - Gallette/Crackers proteici + Yogurt/Ricotta": {"max": 7, "slot": "Colazione", "desc": "Yogurt o ricotta + gallette/fette e noci"}
}

# Conteggio utilizzi
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

st.sidebar.divider()
st.sidebar.markdown(f"**Settimana attiva:** `{current_week_str}`")
st.sidebar.caption("Il monte alimenti si resetta automaticamente alla fine della domenica sera.")
if st.sidebar.button("🔄 Reset Manuale Settimana"):
    cursor.execute("DELETE FROM handy_logs WHERE week_number = ?", (current_week_str,))
    conn.commit()
    st.toast("Settimana azzerata!")
    st.rerun()

# --- Proposta di Riserva Locale in caso di server occupato ---
def generate_offline_suggestions(available_items: list):
    proposals = []
    sample_items = available_items[:3]
    for it in sample_items:
        cat_name = it["categoria"]
        clean_name = cat_name.split(" - ")[-1]
        proposals.append({
            "title": f"Piatto consigliato: {clean_name}",
            "matched_category": cat_name,
            "hotel_advice": f"Al buffet componi: {it['descrizione']}. Condisci con solo un filo di olio EVO a crudo ed evita intingoli di cottura.",
            "why": f"Hai ancora {it['rimaste']} porzione/i rimanente/i da completare questa settimana."
        })
    return {"proposals": proposals}

# --- Chiamata API con Fallback ---
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
    for m in [PRIMARY_MODEL, FALLBACK_MODEL]:
        try:
            resp = client.models.generate_content(
                model=m,
                contents=prompt,
                config={"response_mime_type": "application/json"}
            )
            return json.loads(resp.text)
        except Exception:
            continue
    # Se entrambi i modelli sono occupati, genera istantaneamente da logica interna
    return generate_offline_suggestions(available_items)

def evaluate_meal_photo(image: Image.Image, meal_slot: str, chosen_category: str):
    prompt = f"""
    Sei il nutrizionista personale dell'utente (metodo Handy Diet Low Carb).
    Pasto in corso: {meal_slot}.
    Categoria selezionata: {chosen_category}.
    
    Analizza la foto del pasto scattata in hotel:
    1. Verifica proporzioni handy diet (1 mano di proteine, verdura a pugno, assenza di pane comune/fritti).
    2. Calcola calorie e macro stimati.
    3. Assegna un voto di conformità da 1 a 10.
    4. Fornisci un consiglio immediato per l'albergo.

    Rispondi RIGOROSAMENTE con questo formato JSON:
    {{
      "plate_summary": "Descrizione del cibo nel piatto",
      "compliance_score": 8,
      "distance_verdict": "Sintesi scostamento rispetto al piano",
      "actionable_hotel_tip": "Consiglio pratico per l'albergo",
      "excesses": ["cosa c'è di troppo"],
      "missing": ["cosa manca"],
      "total_calories": 420,
      "carbs_g": 15.0,
      "protein_g": 38.0,
      "fat_g": 16.0
    }}
    """
    for m in [PRIMARY_MODEL, FALLBACK_MODEL]:
        try:
            resp = client.models.generate_content(
                model=m,
                contents=[image, prompt],
                config={"response_mime_type": "application/json"}
            )
            return json.loads(resp.text)
        except Exception:
            continue
    raise RuntimeError("I server Gemini sono temporaneamente occupati. Riprova tra 10 secondi.")

# --- Interfaccia ---
st.title("🥗 Handy Diet Tracker")
st.caption(f"Orario rilevato: **{now.strftime('%H:%M')}** ➔ Pasto suggerito: **{current_slot}**")

tab_suggest, tab_camera, tab_quotas, tab_weight = st.tabs([
    "💡 Proposta Pasto",
    "📸 Fotografa & Scala",
    "📊 Monte Settimanale",
    "⚖️ Controllo Peso"
])

with tab_suggest:
    st.subheader(f"Cosa mangiare adesso ({current_slot})")
    
    slot_available = [
        {"categoria": k, "rimaste": v["remaining"], "massimo": v["max"], "descrizione": v["desc"]}
        for k, v in remaining_options.items()
        if v["slot"] == current_slot and v["remaining"] > 0
    ]

    if not slot_available:
        st.warning(f"⚠️ Hai già completato tutti i piatti previsti per **{current_slot}**! Puoi scegliere proteine magre con verdura a foglia.")
    else:
        st.markdown(f"Hai ancora **{len(slot_available)} opzioni disponibili** per questo pasto.")
        
        if st.button("✨ Fatti suggerire 3 piatti da Gemini", type="primary"):
            with st.spinner("Calcolo proposte in base alla tua dieta..."):
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

with tab_camera:
    st.subheader("Verifica piatto e registra consumo")
    
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

    mode = st.radio("Foto da:", ["Fotocamera", "Carica da galleria"], horizontal=True)
    pic = st.camera_input("Scatta la foto del piatto") if mode == "Fotocamera" else st.file_uploader("Carica file", type=["jpg", "jpeg", "png"])

    if pic:
        pil_pic = Image.open(pic)
        st.image(pil_pic, caption="Piatto pronto per l'analisi", use_container_width=True)

        if st.button("⚖️ Analizza e Valuta Piatto", type="primary"):
            with st.spinner("Valutazione piatto..."):
                try:
                    res = evaluate_meal_photo(pil_pic, sel_slot, sel_category)
                    st.session_state["current_eval"] = res
                    st.session_state["saved_slot"] = sel_slot
                    st.session_state["saved_category"] = sel_category
                except Exception as e:
                    st.error(f"{e}")

        if "current_eval" in st.session_state:
            data = st.session_state["current_eval"]
            score = data.get("compliance_score", 5)

            st.divider()
            if score >= 8:
                st.success(f"🎯 **Aderenza al pasto: {score}/10 (Ottimo)**")
            elif score >= 5:
                st.warning(f"⚠️ **Aderenza: {score}/10 (Accettabile)**")
            else:
                st.error(f"🚨 **Aderenza: {score}/10 (Distante dal piano)**")

            st.write(f"**Verdetto:** {data.get('distance_verdict')}")
            st.info(f"🏨 **Consiglio hotel:** {data.get('actionable_hotel_tip')}")

            c_ex, c_mis = st.columns(2)
            with c_ex:
                st.markdown("**❌ Eccessi:**")
                for e in data.get("excesses", []):
                    st.write(f"- {e}")
            with c_mis:
                st.markdown("**🥗 Carenze:**")
                for m in data.get("missing", []):
                    st.write(f"- {m}")

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Kcal", f"{data.get('total_calories')} kcal")
            m2.metric("Carboidrati", f"{data.get('carbs_g')} g")
            m3.metric("Proteine", f"{data.get('protein_g')} g")
            m4.metric("Grassi", f"{data.get('fat_g')} g")

            if st.button("💾 Salva e SCALA dal monte settimanale", type="primary"):
                cursor.execute("""
                    INSERT INTO handy_logs (
                        entry_date, entry_time, week_number, meal_type, category_consumed,
                        score, plate_summary, calories, carbs_g, protein_g, fat_g, verdict
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    data.get("distance_verdict")
                ))
                conn.commit()
                st.toast(f"Pasto registrato! Scalato da: {st.session_state.get('saved_category')}")
                del st.session_state["current_eval"]
                st.rerun()

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
            st.info("💡 Oscillazione legata a ritenzione e sodio. Tieni alte le verdure e limita i carboidrati stasera.")
        else:
            st.success("✅ **Andamento regolare verso l'obiettivo.**")

        st.line_chart(df_w.set_index("entry_date")["weight_kg"])
