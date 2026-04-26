import os
import json
import requests
from flask import Flask, request, jsonify
import anthropic

app = Flask(__name__)

# ── הגדרות — מלא את אלה ──
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ── תוכנית המסחר של ניסים ──
TRADING_PLAN = """
אתה מנתח צ'ארטים מקצועי עבור טריידר בשם ניסים. הוא סוחר לפי השיטה הבאה בלבד:

סגנון: Swing Trading — עסקאות של כמה ימים עד שבוע. לא סקאלפינג.

טיימפריימים:
Weekly → כיוון ראשי
Daily → אישור כיוון
H4 → אזור כניסה (תיקון)
H1 → טריגר כניסה
חוק ברזל: אין כניסה אם Weekly ו-Daily לא באותו כיוון!

זיהוי כיוון:
HH + HL = שוק שורי → מחפש לונג בלבד
LH + LL = שוק דובי → מחפש שורט בלבד
לא סוחר נגד הכיוון בשום מצב.

Setup (H4):
- אזורי תמיכה/התנגדות ברורים
- Volume Profile
- אזורי נזילות
- Pullback ברור לאזור
- לא נכנס באמצע תנועה

טריגר (H1):
- שינוי מבנה שוק (BOS / CHOCH)
- נר חזק עם אישור
- דחייה ברורה מאזור

סטופ לוס: לפי ATR + מבנה שוק.
ניהול סיכון: 1-2% לעסקה, מקסימום 2 עסקאות פתוחות.
חדשות: לא נכנס לפני CPI / PCE / FOMC / GDP / NFP.
"""

def analyze_with_claude(alert_data: dict) -> dict:
    """שולח את נתוני ה-Alert ל-Claude ומקבל ניתוח"""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    symbol = alert_data.get("symbol", "לא ידוע")
    timeframe = alert_data.get("timeframe", "לא ידוע")
    price = alert_data.get("price", "לא ידוע")
    condition = alert_data.get("condition", "התראה ממסחר")
    extra = alert_data.get("extra", "")

    prompt = f"""{TRADING_PLAN}

קיבלת התראה אוטומטית מ-TradingView עבור ניסים:
- נכס: {symbol}
- טיימפריים: {timeframe}
- מחיר נוכחי: {price}
- תנאי שהתקיים: {condition}
{f"- מידע נוסף: {extra}" if extra else ""}

נתח את ההתראה לפי שיטת המסחר של ניסים ותן תשובה בדיוק בפורמט JSON הבא, ללא שום טקסט נוסף:
{{
  "verdict": "BUY" או "SELL" או "WAIT",
  "score": מספר 1-10,
  "checklist": {{
    "direction": true/false,
    "pullback": true/false,
    "zone": true/false,
    "trigger": true/false,
    "news": true/false
  }},
  "summary": "2-3 משפטים מה בדיוק קורה ומדוע",
  "entry_note": "היכן כדאי להיכנס ומה לצפות",
  "risk_note": "היכן SL הגיוני ומה הסיכון"
}}"""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}]
    )

    text = response.content[0].text.strip()
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)


def format_telegram_message(alert_data: dict, analysis: dict) -> str:
    """מעצב הודעת Telegram יפה"""
    verdict = analysis.get("verdict", "WAIT")
    score = analysis.get("score", 0)
    chk = analysis.get("checklist", {})
    symbol = alert_data.get("symbol", "—")
    timeframe = alert_data.get("timeframe", "—")
    price = alert_data.get("price", "—")

    # אייקון לפי פסיקה
    if verdict == "BUY":
        verdict_line = "🟢 *LONG*"
    elif verdict == "SELL":
        verdict_line = "🔴 *SHORT*"
    else:
        verdict_line = "⏳ *WAIT*"

    # ציון עם צבע
    if score >= 7:
        score_emoji = "🔥"
    elif score >= 5:
        score_emoji = "⚠️"
    else:
        score_emoji = "❌"

    # צ'קליסט
    def chk_icon(val): return "✅" if val else "❌"

    msg = f"""📊 *התראת מסחר — ניסים*
━━━━━━━━━━━━━━━
*{symbol}* | {timeframe} | `{price}`

{verdict_line}
{score_emoji} ציון התאמה: *{score}/10*

*צ'קליסט:*
{chk_icon(chk.get('direction'))} כיוון W+D תואם
{chk_icon(chk.get('pullback'))} תיקון ברור
{chk_icon(chk.get('zone'))} אזור מפתח
{chk_icon(chk.get('trigger'))} טריגר H1
{chk_icon(chk.get('news'))} נקי מחדשות

*ניתוח:*
{analysis.get('summary', '—')}

*כניסה:*
{analysis.get('entry_note', '—')}

*סיכון:*
{analysis.get('risk_note', '—')}
━━━━━━━━━━━━━━━
_ניסים | Chart Analyzer Pro_"""

    return msg


def send_telegram(message: str):
    """שולח הודעה ל-Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    response = requests.post(url, json=payload, timeout=10)
    return response.ok


@app.route("/webhook", methods=["POST"])
def webhook():
    """מקבל Webhook מ-TradingView"""
    try:
        # TradingView שולח JSON או טקסט
        if request.is_json:
            alert_data = request.get_json()
        else:
            # אם טקסט פשוט — נפרסר אותו
            raw = request.data.decode("utf-8")
            try:
                alert_data = json.loads(raw)
            except:
                alert_data = {"condition": raw, "symbol": "לא ידוע", "timeframe": "לא ידוע", "price": "לא ידוע"}

        print(f"[WEBHOOK] קיבלתי: {alert_data}")

        # ניתוח Claude
        analysis = analyze_with_claude(alert_data)
        print(f"[CLAUDE] תשובה: {analysis}")

        # שליחה ל-Telegram
        message = format_telegram_message(alert_data, analysis)
        sent = send_telegram(message)
        print(f"[TELEGRAM] נשלח: {sent}")

        return jsonify({"status": "ok", "verdict": analysis.get("verdict")}), 200

    except Exception as e:
        print(f"[ERROR] {e}")
        send_telegram(f"⚠️ שגיאה בניתוח התראה: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    """בדיקת חיבור"""
    return jsonify({"status": "running", "message": "Nissim Chart Analyzer is live!"}), 200


@app.route("/test", methods=["GET"])
def test():
    """שליחת הודעת טסט ל-Telegram"""
    test_alert = {
        "symbol": "EUR/USD",
        "timeframe": "H4",
        "price": "1.0850",
        "condition": "BOS זוהה על H4, Daily בולישי, Weekly תומך"
    }
    try:
        analysis = analyze_with_claude(test_alert)
        message = format_telegram_message(test_alert, analysis)
        send_telegram(message)
        return jsonify({"status": "test sent!", "analysis": analysis}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
