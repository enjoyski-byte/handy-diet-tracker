import os
import json
import sqlite3
from datetime import date
from PIL import Image
import pandas as pd
import streamlit as st
from google import genai

st.set_page_config(page_title="Handy Diet & Weight Tracker", page_icon="⚖️", layout="centered")

# --- Inizializzazione Database SQLite ---
conn = sqlite3.connect("meals.db", check_same_thread=False)
cursor = conn.cursor()

# Tabella pasti
cursor.execute("""
    CREATE TABLE IF NOT EXISTS handy_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_date TEXT,
        meal_type TEXT,
        score INTEGER,
        plate_summary TEXT,
        calories INTEGER,
        carbs_g REAL,
        protein_g REAL,
        fat_g REAL,
        verdict TEXT
    )
""")

# Tabella peso corporeo
cursor.execute("""
    CREATE TABLE IF NOT EXISTS weight_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_date TEXT UNIQUE,
        weight_kg REAL
    )
""")
conn.commit()

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
    api_key = st.sidebar.text_input("Inserisci qui la tua Gemini API Key", type="password")

if not api_key:
    st.info("👉 Inserisci la tua Gemini API Key a sinistra per avviare l'assistente.")
    st.stop()

client = genai.Client(api_key=api_key)

# --- Sidebar: Parametri e Obiettivo Peso ---
st.sidebar.header("🎯 Target & Impostazioni")
target_weight = st.sidebar.number_input("Obiettivo Peso Corporeo (kg)", min_value=50.0, max_value=150.0, value=75.0, step=0.5)

# --- Profilo Dieta & Prompt Regolatore ---
DIET_GUIDELINES = """
REGOLE DIETA UTENTE (Metodo "Handy Diet" Low Carb):
1. PRANZO:
   - Proteine: 1 mano (carne bianca/rossa ai ferri, pesce o 2 uova o salmone 4 fette).
   - Carboidrati: Solo 2 gallette tipo norvegesi/cracker proteici (oppure max 2 volte a settimana pasta a "pugno schiacciato").
   - Verdura: 1 pugno e mezzo cruda o 1 pugno cotta (condita con max 1 cucchiaio olio EVO).
2. CENA:
   - Proteine: 1 mano di pesce semplice o carne bianca/uova.
   - Carboidrati: 2 gallette/cracker proteici (NO pasta, NO pane comune).
   - Verdura: Minimo 1 pugno e mezzo cruda o cotta.
3. COLAZIONE / SPUNTINI:
   - Yogurt intero/ricotta + noci (5) o mandorle (15) + 2 gallette/cracker. Frutta: 1 pugno (1 volta al giorno).
4. AMBIENTE HOTEL / BUFFET:
   - Massima attenzione a: fritti, salse grasse, intingoli con burro, cibi impanati, pane bianco e dolci da buffet.
"""

def evaluate_meal_against_diet(image: Image.Image, meal_type: str):
    prompt = f"""
    Sei il nutrizionista e coach personale dell'utente. L'utente sta seguendo questa dieta:
    {DIET_GUIDELINES}

    L'utente è in albergo/ristorante e ti invia la foto di ciò che sta per mangiare per il pasto: **{meal_type}**.
    Valuta quanto questo piatto è distante dalla sua dieta prescritta.

    Istruzioni di analisi:
    1. Identifica cosa c'è nel piatto e stima porzioni in termini di volume "mano/pugno" e grammi approssimativi.
    2. Calcola calorie e macro (carboidrati, proteine, grassi).
    3. Assegna un punteggio di conformità alla dieta (score) da 1 a 10 (10 = perfetto rispetto alla Handy Diet, 1 = sgarro totale).
    4. Distanza dalla dieta: elenca cosa c'è di troppo (es. troppo olio, pane non previsto, carboidrati in eccesso) e cosa manca (es. mancano le verdure o proteine scarse).
    5. Consiglio operativo immediato per l'albergo (es. "Lascia nel piatto il cestino del pane e aggiungi un piatto di finocchi/pomodori dal buffet").

    Rispondi RIGOROSAMENTE con questo formato JSON:
    {{
      "plate_summary": "Breve descrizione del piatto analizzato",
      "compliance_score": 8,
      "distance_verdict": "Sintesi: Sei molto vicino / Sei troppo alto di carboidrati / Troppi grassi nascosti",
      "excesses": ["es. Porzione di pasta doppia rispetto al pugno", "presenza di condimento abbondante"],
      "missing": ["es. Manca la quota di verdure di accompagnamento"],
      "actionable_hotel_tip": "Consiglio pratico per gestire il buffet o cosa lasciare nel piatto.",
      "total_calories": 450,
      "carbs_g": 20.0,
      "protein_g": 35.0,
      "fat_g": 18.0
    }}
    """
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[image, prompt],
        config={"response_mime_type": "application/json"}
    )
    return json.loads(response.text)

# --- Interfaccia Grafica ---
st.title("🥗 Handy Diet & Weight Tracker")

tab_check, tab_history, tab_weight = st.tabs(["📸 Verifica Piatto Hotel", "📋 Registro Pasti", "⚖️ Controllo Peso"])

# === TAB 1: VERIFICA PIATTO HOTEL ===
with tab_check:
    meal_type = st.selectbox("Quale pasto stai consumando?", ["Pranzo", "Cena", "Colazione", "Spuntino"])

    method = st.radio("Acquisizione foto:", ["Fotocamera", "Carica foto"], horizontal=True)
    img_input = st.camera_input("Inquadra il piatto o il vassoio") if method == "Fotocamera" else st.file_uploader("Carica immagine", type=["jpg", "jpeg", "png"])

    if img_input:
        pil_img = Image.open(img_input)
        st.image(pil_img, caption="Piatto da esaminare", use_container_width=True)

        if st.button("⚖️ Valuta Distanza dalla Dieta", type="primary"):
            with st.spinner("Gemini sta confrontando il piatto con la tua dieta Handy Diet..."):
                try:
                    res = evaluate_meal_against_diet(pil_img, meal_type)
                    st.session_state["handy_eval"] = res
                    st.session_state["eval_meal_type"] = meal_type
                except Exception as e:
                    st.error(f"Errore durante l'analisi: {e}")

        if "handy_eval" in st.session_state:
            data = st.session_state["handy_eval"]
            score = data.get("compliance_score", 5)

            st.divider()
            if score >= 8:
                st.success(f"🎯 **Aderenza alla dieta: {score}/10 (Ottimo)**")
            elif score >= 5:
                st.warning(f"⚠️ **Aderenza alla dieta: {score}/10 (Accettabile con scostamenti)**")
            else:
                st.error(f"🚨 **Aderenza alla dieta: {score}/10 (Molto distante dal piano)**")

            st.write(f"**Verdetto:** {data.get('distance_verdict')}")
            st.info(f"🏨 **Cosa fare adesso in albergo:** {data.get('actionable_hotel_tip')}")

            col1, col2 = st.columns(2)
            with col1:
                st.markdown("**❌ Cosa c'è di troppo / sconsigliato:**")
                for ex in data.get("excesses", []):
                    st.write(f"- {ex}")
            with col2:
                st.markdown("**🥗 Cosa manca rispetto al piano:**")
                for mis in data.get("missing", []):
                    st.write(f"- {mis}")

            st.markdown("##### Stima Nutrizionale")
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Kcal Stimate", f"{data.get('total_calories')} kcal")
            k2.metric("Carboidrati", f"{data.get('carbs_g')} g")
            k3.metric("Proteine", f"{data.get('protein_g')} g")
            k4.metric("Grassi", f"{data.get('fat_g')} g")

            if st.button("💾 Salva pasto nel diario"):
                cursor.execute("""
                    INSERT INTO handy_logs (entry_date, meal_type, score, plate_summary, calories, carbs_g, protein_g, fat_g, verdict)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    date.today().isoformat(),
                    st.session_state.get("eval_meal_type", "Pasto"),
                    score,
                    data.get("plate_summary"),
                    data.get("total_calories"),
                    data.get("carbs_g"),
                    data.get("protein_g"),
                    data.get("fat_g"),
                    data.get("distance_verdict")
                ))
                conn.commit()
                st.toast("Pasto registrato!")
                del st.session_state["handy_eval"]
                st.rerun()

# === TAB 2: REGISTRO PASTI ===
with tab_history:
    today_str = date.today().isoformat()
    df_meals = pd.read_sql_query("SELECT entry_date, meal_type, score, plate_summary, calories, carbs_g, protein_g, fat_g FROM handy_logs WHERE entry_date = ?", conn, params=(today_str,))
    if df_meals.empty:
        st.write("Nessun pasto registrato oggi.")
    else:
        avg_score = df_meals["score"].mean()
        st.metric("Voto Medio Aderenza Oggi", f"{avg_score:.1f} / 10")
        st.dataframe(df_meals, hide_index=True)
        if st.button("Azzera storico pasti di oggi"):
            cursor.execute("DELETE FROM handy_logs WHERE entry_date = ?", (today_str,))
            conn.commit()
            st.rerun()

# === TAB 3: CONTROLLO PESO & PROGRESSI ===
with tab_weight:
    st.subheader("⚖️ Diario del Peso & Analisi Tendenza")
    
    # Inserimento peso
    w_col1, w_col2, w_col3 = st.columns([2, 2, 1.5])
    with w_col1:
        input_date = st.date_input("Data misurazione", value=date.today())
    with w_col2:
        new_weight = st.number_input("Peso rilevato (kg)", min_value=40.0, max_value=180.0, value=80.0, step=0.1, format="%.1f")
    with w_col3:
        st.write("")
        st.write("")
        save_w_btn = st.button("💾 Registra", type="primary")

    if save_w_btn:
        cursor.execute("""
            INSERT INTO weight_logs (entry_date, weight_kg)
            VALUES (?, ?)
            ON CONFLICT(entry_date) DO UPDATE SET weight_kg = excluded.weight_kg
        """, (input_date.isoformat(), new_weight))
        conn.commit()
        st.toast(f"Peso di {new_weight} kg registrato per il {input_date}!")
        st.rerun()

    # Lettura storico pesate
    df_weight = pd.read_sql_query("SELECT entry_date, weight_kg FROM weight_logs ORDER BY entry_date ASC", conn)

    if not df_weight.empty:
        df_weight["entry_date"] = pd.to_datetime(df_weight["entry_date"])
        df_weight = df_weight.sort_values("entry_date").reset_index(drop=True)

        latest_weight = df_weight.iloc[-1]["weight_kg"]
        initial_weight = df_weight.iloc[0]["weight_kg"]
        delta_total = latest_weight - initial_weight
        delta_target = latest_weight - target_weight

        # Calcolo variazione dall'ultima pesata
        if len(df_weight) > 1:
            prev_weight = df_weight.iloc[-2]["weight_kg"]
            delta_last = latest_weight - prev_weight
        else:
            delta_last = 0.0

        # KPI metriche
        st.divider()
        m_col1, m_col2, m_col3 = st.columns(3)
        m_col1.metric("Peso Attuale", f"{latest_weight:.1f} kg", delta=f"{delta_last:+.1f} kg (vs ultima)" if len(df_weight) > 1 else None, delta_color="inverse")
        m_col2.metric("Variazione Totale", f"{delta_total:+.1f} kg", delta_color="inverse")
        m_col3.metric("Mancanti all'Obiettivo", f"{delta_target:+.1f} kg", delta=f"Target: {target_weight:.1f} kg")

        # --- SISTEMA ALERT DI PEGGIORAMENTO ---
        if delta_last > 0.4:
            st.warning(f"⚠️ **ATTENZIONE - AUMENTO RILEVATO (+{delta_last:.1f} kg rispetto all'ultima pesata)**")
            st.info("💡 **Consiglio clinico Handy Diet:** Ricorda che le variazioni giornaliere repentine dipendono spesso da ritenzione idrica o sodio dei pasti fuori casa. Non abbatterti: mantieni rigorosa la quota proteica a '1 mano', aumenta le verdure crude ed evita il pane al buffet stasera.")
        elif delta_total > 1.0 and len(df_weight) >= 3:
            st.error(f"🚨 **ALLERTA TENDENZA NEGATIVA: Sei a +{delta_total:.1f} kg dall'inizio del percorso.**")
            st.write("Verifica se negli ultimi giorni in hotel ci sono stati carboidrati non calcolati a cena, condimenti nascosti o porzioni proteiche oltre la misura.")
        else:
            st.success("✅ **Tendenza sotto controllo:** Stai procedendo verso l'obiettivo. Ricorda che 2 kg al mese rappresentano il ritmo ideale per un calo di peso sano e sostenibile.")

        # --- GRAFICO ANDAMENTO ---
        st.markdown("#### Curva di Andamento del Peso")
        # Prepariamo un dataframe pulito per il grafico
        chart_df = df_weight.set_index("entry_date")[["weight_kg"]]
        chart_df["Target"] = target_weight
        st.line_chart(chart_df)

        # Tabella Storico con opzione cancellazione
        with st.expander("Visualizza o elimina registrazioni passate"):
            st.dataframe(df_weight.sort_values("entry_date", ascending=False), hide_index=True)
            del_date = st.selectbox("Seleziona data da rimuovere", df_weight["entry_date"].dt.strftime("%Y-%m-%d"))
            if st.button("Elimina pesata selezionata"):
                cursor.execute("DELETE FROM weight_logs WHERE entry_date = ?", (del_date,))
                conn.commit()
                st.rerun()
    else:
        st.info("Non hai ancora registrato nessuna pesata. Inserisci il peso di oggi per iniziare il tracciamento.")
