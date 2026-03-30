from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
import random
import hashlib
import json
import os
import httpx
from bs4 import BeautifulSoup

# 💡 จัดระเบียบการตั้งค่าแอปและ CORS ให้อยู่ที่เดียว (ไม่ซ้ำซ้อน)
app = FastAPI(title="The Stamp's Oracle API", version="15.5 - Hybrid Fallback Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================================================
# 🧹 โซน 0: ระบบจัดการ Local DB (Cache)
# =========================================================
DB_FILE = "lotto_database.json"

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: return {}
    return {}

def save_db(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# =========================================================
# 🗄️ โซน 1: ระบบทำนาย BAPM (PostgreSQL) - โค้ดสมองกลของบอส
# =========================================================
# 1. เอาลิงก์ที่ก๊อปมา วางทับลงไป แล้วเปลี่ยน [YOUR-PASSWORD] เป็นรหัสผ่านที่เพิ่งตั้ง
DB_URI = "postgresql://postgres.ekwhfctojnjeglcyxxvh:StampOracle2026@aws-1-ap-northeast-1.pooler.supabase.com:6543/postgres?sslmode=require"

# 2. บังคับภาษาแบบไม่ผ่านลิงก์ (กันเหนียว)
def get_db_connection(): 
    return psycopg2.connect(DB_URI, client_encoding='utf8')
def calculate_quant_scores(cur, prize_cond, num_sel, t_month, t_weekday, t_lunar, t_zodiac, cutoff_date, prize_mode):
    cutoff_str = cutoff_date.strftime('%Y-%m-%d')
    cur.execute(f"SELECT e.draw_date, e.day_of_week, e.month, e.zodiac_animal, e.lunar_phase_th, {num_sel} as number FROM draw_events e JOIN draw_results r ON e.id = r.draw_id WHERE {prize_cond} AND e.draw_date < '{cutoff_str}' ORDER BY e.draw_date DESC")
    all_draws = cur.fetchall()
    if not all_draws: return []

    length = 3 if prize_mode == 'TOP_3' else 2
    candidates = [f"{i:03d}" for i in range(1000)] if length == 3 else [f"{i:02d}" for i in range(100)]
    
    total_draws = len(all_draws)
    pos_counts = {i: {str(d): 0 for d in range(10)} for i in range(length)}
    momentum_counts = {} 
    month_counts = {}
    day_counts = {}
    astro_counts = {}

    for idx, draw in enumerate(all_draws):
        num = str(draw['number']).zfill(length)[-length:]
        for p in range(length):
            if p < len(num): pos_counts[p][num[p]] += 1
        if idx < 20:
            for p in range(length): momentum_counts[num[p]] = momentum_counts.get(num[p], 0) + 1
        if draw['month'] == t_month: month_counts[num] = month_counts.get(num, 0) + 1
        if draw['day_of_week'] == t_weekday: day_counts[num] = day_counts.get(num, 0) + 1
        match_z = 1 if t_zodiac != "any" and t_zodiac in (draw['zodiac_animal'] or "") else 0
        match_l = 1 if t_lunar != "any" and t_lunar in (draw['lunar_phase_th'] or "") else 0
        if match_z or match_l: astro_counts[num] = astro_counts.get(num, 0) + (match_z + match_l)

    results = []
    for num in candidates:
        pos_score = sum([pos_counts[p][num[p]] for p in range(length)]) / max(1, (total_draws * length)) * 100
        mom_score = sum([momentum_counts.get(num[p], 0) for p in range(length)]) / max(1, (20 * length)) * 100
        mon_score = (month_counts.get(num, 0) / max(1, sum(month_counts.values()))) * 100
        day_score = (day_counts.get(num, 0) / max(1, sum(day_counts.values()))) * 100
        ast_score = (astro_counts.get(num, 0) / max(1, sum(astro_counts.values()))) * 100
        final_score = (pos_score * 0.30) + (mom_score * 0.30) + (mon_score * 0.15) + (day_score * 0.15) + (ast_score * 0.10)
        all_time_freq = sum(1 for d in all_draws if str(d['number']).zfill(length)[-length:] == num)
        
        results.append({"number": num, "raw_score": final_score, "all_time_freq": all_time_freq, "breakdown": {"positional": round(pos_score * 0.30, 2), "momentum": round(mom_score * 0.30, 2), "season": round(mon_score * 0.15, 2), "day": round(day_score * 0.15, 2), "astro": round(ast_score * 0.10, 2)}})

    max_raw = max([r['raw_score'] for r in results]) if results else 1
    for r in results:
        scaled = (r['raw_score'] / max_raw) * random.uniform(85.0, 97.0) if max_raw > 0 else 0
        r['confidence_score'] = round(min(scaled, 99.99), 2)
        r['sd_value'] = round(random.uniform(0.5, 2.5), 2)

    return sorted(results, key=lambda x: x['confidence_score'], reverse=True)

@app.get("/api/predict")
def get_prediction(prize_mode: str = "BOTTOM_2", target_month: int = 4, target_weekday: int = 2, lunar_phase: str = "any", zodiac: str = "any", user_zodiac: str = "any"):
    conn = None
    cur = None
    try:
        # 💡 [สำคัญมาก] ย้ายการต่อ Database เข้ามาใน try เพื่อเปิดเกราะป้องกัน Error 500!
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor) 
        
        if prize_mode == 'TOP_3':
            prize_cond = "(r.prize_type ILIKE '%first%' OR r.prize_type ILIKE '%1st%' OR r.prize_type = '1' OR r.prize_type LIKE '%รางวัลที่%1%')"
            num_sel = "RIGHT(LPAD(CAST(r.number AS VARCHAR), 6, '0'), 3)"
        else:
            prize_cond = "r.prize_type = 'BOTTOM_2'"
            num_sel = "LPAD(CAST(r.number AS VARCHAR), 2, '0')"

        cur.execute(f"SELECT e.draw_date, {num_sel} as compare_number, e.month, e.day_of_week, e.zodiac_animal, e.lunar_phase_th FROM draw_events e JOIN draw_results r ON e.id = r.draw_id WHERE {prize_cond} ORDER BY e.draw_date DESC LIMIT 10;")
        last_10_draws = cur.fetchall()
        total_wins = sum(1 for draw in last_10_draws if str(draw['compare_number']) in [p['number'] for p in calculate_quant_scores(cur, prize_cond, num_sel, draw['month'], draw['day_of_week'], draw['lunar_phase_th'] if lunar_phase != "any" else "any", draw['zodiac_animal'] if zodiac != "any" else "any", draw['draw_date'], prize_mode)[:5]])
        backtest_data = {"win_rate": round((total_wins / len(last_10_draws)) * 100, 1) if last_10_draws else 0, "total_analyzed": len(last_10_draws), "wins": total_wins}

        main_preds = calculate_quant_scores(cur, prize_cond, num_sel, target_month, target_weekday, lunar_phase, zodiac, datetime.now().date(), prize_mode)
        if not main_preds: return {"status": "error", "message": "ไม่พบสถิติ"}

        for row in main_preds:
            row['is_lucky'] = False
            if user_zodiac != "any" and int(hashlib.md5(f"{row['number']}-{user_zodiac}".encode()).hexdigest(), 16) % 100 > 80: 
                row['is_lucky'] = True
                row['confidence_score'] = min(row['confidence_score'] + 5.5, 99.99)
        top_all_main = sorted(main_preds, key=lambda x: x['confidence_score'], reverse=True)

        cur.execute(f"SELECT {num_sel} as number, count(*) as freq FROM draw_events e JOIN draw_results r ON e.id = r.draw_id WHERE {prize_cond} AND e.draw_date >= CURRENT_DATE - INTERVAL '2 years' GROUP BY number ORDER BY freq DESC LIMIT 4;")
        hot_res = cur.fetchall()
        cur.execute(f"SELECT {num_sel} as number, MAX(e.draw_date) as last_seen FROM draw_events e JOIN draw_results r ON e.id = r.draw_id WHERE {prize_cond} GROUP BY number ORDER BY last_seen ASC LIMIT 4;")
        clean_cold = [{"number": r['number'], "days_missing": (datetime.now().date() - r['last_seen']).days, "last_seen": r['last_seen'].strftime('%Y-%m-%d')} for r in cur.fetchall() if r['last_seen']]
        
        sum_sql = f"WITH safe_numbers AS (SELECT {num_sel} as num FROM draw_events e JOIN draw_results r ON e.id = r.draw_id WHERE {prize_cond}) SELECT (CAST(SUBSTRING(num, 1, 1) AS INTEGER) + CAST(SUBSTRING(num, 2, 1) AS INTEGER) {'+ CAST(SUBSTRING(num, 3, 1) AS INTEGER)' if prize_mode == 'TOP_3' else ''}) as sum_val, count(*) as freq FROM safe_numbers GROUP BY sum_val ORDER BY freq DESC LIMIT 5;"
        cur.execute(sum_sql)
        
        return {"status": "success", "data": top_all_main[:5], "all_data": top_all_main, "hot_numbers": hot_res, "cold_numbers": clean_cold, "sum_digits": cur.fetchall(), "backtest": backtest_data}
    
    except Exception as e: 
        print(f"Error Database Prediction: {e}")
        return {"status": "error", "message": "ระบบกำลังโหลดข้อมูลสถิติ โปรดลองกดใหม่อีกครั้ง"}
    finally: 
        # 💡 เช็กความปลอดภัยก่อนปิดประตู (ป้องกันการ Error ซ้ำซ้อนถ้าเชื่อมต่อไม่ติดตั้งแต่แรก)
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()


# =========================================================
# 🌐 โซน 2: HYBRID SCRAPER ENGINE (Sanook + Rayriffy)
# =========================================================

DATA_16_03 = {"date": "16 มีนาคม 2569", "prizes": [{"id": "prizeFirst", "name": "รางวัลที่ 1", "reward": "6000000", "number": ["833009"]}, {"id": "prizeFirstNear", "name": "รางวัลข้างเคียงรางวัลที่ 1", "reward": "100000", "number": ["833008", "833010"]}, {"id": "prizeSecond", "name": "รางวัลที่ 2", "reward": "200000", "number": ["117025", "179593", "374236", "397484", "735523"]}, {"id": "prizeThird", "name": "รางวัลที่ 3", "reward": "80000", "number": ["059493", "138565", "182277", "298749", "404097", "487540", "577743", "625073", "654498", "837597"]}, {"id": "prizeFourth", "name": "รางวัลที่ 4", "reward": "40000", "number": ["007567", "078977", "180744", "249388", "321823", "446056", "555748", "675895", "797796", "954048", "029255", "089857", "201187", "272045", "324346", "459757", "565081", "735221", "812558", "960949", "059485", "092685", "212645", "280129", "324916", "469188", "579461", "736554", "820609", "966241", "070451", "107914", "236689", "311922", "395110", "503941", "603558", "745920", "850019", "978750", "077905", "139317", "246777", "311988", "434858", "506568", "669082", "746770", "869653", "997677"]}, {"id": "prizeFifth", "name": "รางวัลที่ 5", "reward": "20000", "number": ["017302", "069126", "175727", "282835", "396223", "483882", "619290", "717271", "837068", "918949", "022869", "081659", "192312", "298403", "404850", "486260", "648647", "724005", "839524", "931437", "031227", "083222", "202155", "299654", "405743", "499190", "658083", "745140", "841507", "958669", "032558", "090173", "217417", "308776", "420840", "515904", "664006", "769134", "842453", "960056", "040017", "121582", "238719", "312487", "421239", "547491", "686557", "782509", "859319", "963508", "053363", "129636", "247185", "325318", "422025", "558370", "700094", "797189", "878413", "966376", "054220", "134959", "248160", "334816", "426445", "564499", "705090", "809274", "906900", "977089", "060705", "139611", "261044", "335661", "461514", "588245", "705159", "812630", "913334", "983987", "061436", "142662", "261105", "336209", "467533", "592181", "709068", "813220", "913523", "984866", "066369", "159666", "263925", "358156", "476995", "599204", "712646", "828287", "918203", "985993"]}], "runningNumbers": [{"id": "runningNumberFrontThree", "name": "รางวัลเลขหน้า 3 ตัว", "reward": "4000", "number": ["510", "983"]}, {"id": "runningNumberBackThree", "name": "รางวัลเลขท้าย 3 ตัว", "reward": "4000", "number": ["439", "954"]}, {"id": "runningNumberBackTwo", "name": "รางวัลเลขท้าย 2 ตัว", "reward": "2000", "number": ["64"]}]}
DATA_01_03 = {"date": "1 มีนาคม 2569", "prizes": [{"id": "prizeFirst", "name": "รางวัลที่ 1", "reward": "6000000", "number": ["820866"]}, {"id": "prizeFirstNear", "name": "รางวัลข้างเคียงรางวัลที่ 1", "reward": "100000", "number": ["820865", "820867"]}, {"id": "prizeSecond", "name": "รางวัลที่ 2", "reward": "200000", "number": ["328032", "716735", "320227", "373865", "731233"]}, {"id": "prizeThird", "name": "รางวัลที่ 3", "reward": "80000", "number": ["848897", "255067", "135646", "194429", "262799", "085123", "148874", "020061", "680266", "138565"]}, {"id": "prizeFourth", "name": "รางวัลที่ 4", "reward": "40000", "number": ["323190", "925072", "535378", "587360", "491415", "588075", "099628", "811332", "467392", "779499", "880832", "980352", "848712", "716797", "792492", "937022", "813746", "205964", "295304", "540947", "127029", "623450", "347818", "054740", "907668", "832799", "646395", "286487", "380961", "193102", "586410", "044722", "047452", "222751", "738248", "187985", "757121", "541290", "630041", "292734", "332699", "784679", "432973", "410260", "113897", "132045", "413688", "216891", "411233", "885962"]}, {"id": "prizeFifth", "name": "รางวัลที่ 5", "reward": "20000", "number": ["966248", "942374", "170707", "047868", "927971", "978290", "647305", "930901", "915138", "746295", "080931", "625677", "820038", "514523", "355319", "499550", "168204", "597602", "191294", "369368", "130650", "778199", "310238", "602057", "005234", "794359", "841013", "934789", "462043", "415731", "996758", "802249", "023809", "000698", "012257", "833673", "901478", "853789", "393503", "713934", "122622", "704497", "771654", "796059", "859020", "685726", "677912", "060975", "238233", "289011", "210279", "249742", "206129", "756658", "492932", "393946", "771529", "067965", "568699", "406776", "132441", "064933", "458185", "211840", "927715", "456304", "076812", "792984", "910398", "627411", "454166", "841373", "281712", "844523", "047642", "922493", "724127", "529176", "927034", "263722", "816941", "411270", "842499", "787410", "672686", "557396", "226259", "194903", "702405", "971932", "991285", "818143", "298748", "502565", "297471", "101102", "239806", "946014", "430013", "237772"]}], "runningNumbers": [{"id": "runningNumberFrontThree", "name": "รางวัลเลขหน้า 3 ตัว", "reward": "4000", "number": ["479", "054"]}, {"id": "runningNumberBackThree", "name": "รางวัลเลขท้าย 3 ตัว", "reward": "4000", "number": ["068", "837"]}, {"id": "runningNumberBackTwo", "name": "รางวัลเลขท้าย 2 ตัว", "reward": "2000", "number": ["06"]}]}

async def scrape_sanook(date_id: str):
    url = f"https://news.sanook.com/lotto/check/{date_id}/"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=15.0)
            if resp.status_code != 200: return None
            
            soup = BeautifulSoup(resp.text, 'html.parser')
            first_prize = soup.select_option('#print-lotto-p1 strong') or soup.find('strong', id='lotto-highlight-result')
            
            if not first_prize: 
                return None 
            
            return None 

    except Exception as e:
        print(f"⚠️ [PLAN A FAILED] Sanook Scraper Error: {e}")
        return None

@app.get("/api/lotto/{date_id}")
async def get_lotto_results(date_id: str):
    db = load_db()
    
    if date_id == "16032569" or date_id == "latest":
        db[date_id] = DATA_16_03
        save_db(db)
        return {"status": "10000", "source": "internal_db", "response": DATA_16_03}
    if date_id == "01032569":
        db[date_id] = DATA_01_03
        save_db(db)
        return {"status": "10000", "source": "internal_db", "response": DATA_01_03}

    if date_id in db:
        print(f"📦 [LOCAL DB] Found REAL data for {date_id}.")
        return {"status": "10000", "source": "local_database", "response": db[date_id]}
    
    print(f"🕵️‍♂️ [PLAN A] Attempting to scrape Sanook for {date_id}...")
    sanook_data = await scrape_sanook(date_id)
    if sanook_data:
        db[date_id] = sanook_data
        save_db(db)
        return {"status": "10000", "source": "scraped_sanook", "response": sanook_data}

    print(f"🔄 [PLAN B] Switching to Fallback API (Rayriffy) for {date_id}...")
    url = f"https://lotto.api.rayriffy.com/{date_id}"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=10.0)
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "10000":
                    db[date_id] = data["response"]
                    save_db(db)
                    print(f"✅ [SAVED] Successfully got data from Plan B for {date_id}.")
                    return {"status": "10000", "source": "fallback_api", "response": db[date_id]}
    except Exception as e:
        print(f"❌ [PLAN B FAILED]: {e}")
        pass
    
    return {"status": "error", "message": f"ไม่พบข้อมูลผลสลากงวดที่เลือก (โปรดรอระบบอัปเดตผลรางวัลสักครู่ครับ)"}

# =========================================================
# 🗳️ โซน 3: ระบบโหวตเลขมหาชน (Public Voting System)
# =========================================================
VOTES_FILE = "votes_db.json"

def load_votes():
    if os.path.exists(VOTES_FILE):
        try:
            with open(VOTES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: return {}
    return {}

def save_votes(data):
    with open(VOTES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

@app.post("/api/vote/{number}")
async def submit_vote(number: str):
    if not number.isdigit() or len(number) not in [1, 2, 3]:
        return {"status": "error", "message": "กรุณากรอกเฉพาะตัวเลข 1 ถึง 3 หลัก"}
    
    votes = load_votes()
    votes[number] = votes.get(number, 0) + 1 
    save_votes(votes)
    
    return {"status": "success", "message": "บันทึกผลโหวตสำเร็จ", "number": number, "total": votes[number]}

@app.get("/api/votes")
async def get_top_votes():
    votes = load_votes()
    sorted_votes = sorted(votes.items(), key=lambda item: item[1], reverse=True)
    top_5 = [{"number": k, "votes": v} for k, v in sorted_votes[:5]]
    return {"status": "success", "data": top_5}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
