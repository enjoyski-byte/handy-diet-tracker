import os
import json
import sqlite3
from datetime import date
from PIL import Image
import pandas as pd
import streamlit as st
from google import genai

st.set_page_config(page_title="Handy Diet Hotel Assistant", page_icon="⚖️", layout="centered")

# --- Inizializzazione Database ---
conn = sqlite3.connect("meals.db", check_same_thread=False)
cursor = conn.cursor()
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
conn.commit()

# --- Configurazione API ---
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
st.title("🥗 Handy Diet Hotel Assistant")
st.caption("Controllo aderenza al piano Low Carb per pasti in hotel e al ristorante.")

tab_check, tab_history = st.tabs(["📸 Verifica Piatto Hotel", "📋 Registro Giornaliero"])

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

            # Barra voto aderenza
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

            # Macronutrienti stimati
            st.markdown("##### Stima Nutrizionale")
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Kcal Stimate", f"{data.get('total_calories')} kcal")
            k2.metric("Carboidrati", f"{data.get('carbs_g')} g")
            k3.metric("Proteine", f"{data.get('protein_g')} g")
            k4.metric("Grassi", f"{data.get('fat_g')} g")

            if st.button("💾 Salva nel diario"):
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

with tab_history:
    today_str = date.today().isoformat()
    df = pd.read_sql_query("SELECT entry_date, meal_type, score, plate_summary, calories, carbs_g, protein_g, fat_g FROM handy_logs WHERE entry_date = ?", conn, params=(today_str,))
    if df.empty:
        st.write("Nessun pasto registrato oggi.")
    else:
        avg_score = df["score"].mean()
        st.metric("Voto Medio Aderenza Oggi", f"{avg_score:.1f} / 10")
        st.dataframe(df, hide_index=True)
        if st.button("Azzera storico oggi"):
            cursor.execute("DELETE FROM handy_logs WHERE entry_date = ?", (today_str,))
            conn.commit()
            st.rerun()