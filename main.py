from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from openpyxl.styles import PatternFill, Font
from openpyxl.utils import get_column_letter
import pandas as pd
import json
import io
import httpx
import sqlite3
import traceback
import os

app = FastAPI(title="PI Director API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- БАЗА ДАННЫХ SQLITE ---
DB_FILE = "pi_director.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS characters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            character_id INTEGER UNIQUE,
            name TEXT NOT NULL,
            ccu_level INTEGER DEFAULT 0,
            ic_level INTEGER DEFAULT 0,
            access_token TEXT,
            refresh_token TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

# --- КОНФИГУРАЦИЯ ESI SSO ---
ESI_CLIENT_ID = "YOUR_CLIENT_ID_HERE"
ESI_SECRET_KEY = "YOUR_SECRET_KEY_HERE"
ESI_CALLBACK_URL = "http://localhost:8000/api/auth/callback"

# --- ПОЛНЫЙ СЛОВАРЬ ТОВАРОВ P3 и P4 ---
# --- ДИНАМИЧЕСКИЙ СПИСОК ТОВАРОВ P3 и P4 ---
PI_TYPE_NAMES = [
    "Broadcast Node", "Integrity Response Drones", "Nano-Factory", 
    "Organic Mortar Applicators", "Recursive Computing Module", 
    "Self-Harmonizing Power Core", "Sterile Conduits", "Wetware Mainframe",
    "Biotech Research Reports", "Camera Drones", "Condensates", 
    "Cryoprotectant Solution", "Data Chips", "Gel-Matrix Biopaste", 
    "Guidance Systems", "Hazmat Detection Systems", "Hermetic Membranes", 
    "High-Tech Transmitters", "Industrial Explosives", "Neocoms", 
    "Nuclear Reactors", "Planetary Vehicles", "Robotics", 
    "Smartfab Units", "Supercomputers", "Synthetic Synapses", 
    "Transcranial Microcontrollers", "Ukomi Superconductors", "Vaccines"
]

PI_TYPE_IDS = {}

@app.on_event("startup")
async def load_eve_ids():
    """Автоматически подтягивает 100% правильные TypeID из CCP ESI при старте сервера"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://esi.evetech.net/latest/universe/ids/",
                json=PI_TYPE_NAMES,
                headers={'Accept-Language': 'en'}
            )
            data = response.json()
            if 'inventory_types' in data:
                for item in data['inventory_types']:
                    PI_TYPE_IDS[item['name']] = item['id']
                print(f"[OK] Успешно загружены TypeID для {len(PI_TYPE_IDS)} PI-товаров из ESI.")
            else:
                print("[WARN] Ошибка: ESI не вернул данные о предметах.")
    except Exception as e:
        print("[ERROR] Ошибка синхронизации с ESI:", e)

class PlanRequest(BaseModel):
    constellation: str
    factory_sys: str
    target_product: str

class ExportRequest(BaseModel):
    plan_data: list

def load_planet_data():
    df = pd.read_csv('planet industry.csv', sep=';', skiprows=1, encoding='utf-8')
    df = df.dropna(subset=['Constellation'])
    df = df[df['Constellation'] != 'max P2']
    return df

@app.get("/api/auth/login")
def login_redirect():
    sso_url = f"https://login.eveonline.com/v2/oauth/authorize?response_type=code&client_id={ESI_CLIENT_ID}&redirect_uri={ESI_CALLBACK_URL}&scope=esi-skills.read_skills.v1"
    return {"status": "mock", "mock_url": "/api/auth/callback?code=mock_code", "real_url": sso_url}

@app.get("/api/auth/callback")
def auth_callback(code: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    mock_chars_from_esi = [
        (90001, "Meda Metha", 5, 5, "token1", "refresh1"),
        (90002, "Shardani Ponad", 5, 5, "token2", "refresh2"),
        (90003, "Morfiy Mefodiy", 4, 4, "token3", "refresh3"),
        (90004, "Olga Santclair", 4, 4, "token4", "refresh4")
    ]
    
    for char in mock_chars_from_esi:
        cursor.execute('''
            INSERT INTO characters (character_id, name, ccu_level, ic_level, access_token, refresh_token)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(character_id) DO UPDATE SET
                name=excluded.name,
                ccu_level=excluded.ccu_level,
                ic_level=excluded.ic_level,
                access_token=excluded.access_token,
                refresh_token=excluded.refresh_token
        ''', char)
    
    conn.commit()
    cursor.execute("SELECT name, ccu_level as ccu, ic_level as ic FROM characters")
    saved_chars = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return {"status": "success", "message": "Авторизация успешна", "characters": saved_chars}

@app.get("/api/market/best-product")
async def get_best_production():
    type_ids_str = ",".join(str(v) for v in PI_TYPE_IDS.values())
    
    # ИСКОМАЯ ПРАВКА: Меняем станцию на весь регион The Forge (Житу и окрестности)
    the_forge_region = 10000002 
    
    try:
        async with httpx.AsyncClient() as client:
            # Теперь запрашиваем ?region= вместо ?station=
            url = f"https://market.fuzzwork.co.uk/aggregates/?region={the_forge_region}&types={type_ids_str}"
            response = await client.get(url)
            market_data = response.json()
            
        profitability = []
        for prod_name, type_id in PI_TYPE_IDS.items():
            str_id = str(type_id)
            if str_id in market_data:
                # Берем максимальный Buy Order по всему региону
                buy_price = float(market_data[str_id]["buy"]["max"])
                profitability.append({"product": prod_name, "jita_buy": buy_price})
                
        profitability.sort(key=lambda x: x["jita_buy"], reverse=True)
        best_product = profitability[0]["product"] if profitability else "Robotics"
        
        return {"status": "success", "recommended": best_product, "analytics": profitability}
    except Exception as e:
        print("ОШИБКА РЫНКА:", e)
        raise HTTPException(status_code=500, detail=str(e))
    
@app.get("/api/initial-data")
def get_initial_data():
    try:
        df = load_planet_data()
        bases = sorted(list(df['Constellation'].unique()))
        with open('recipes.json', 'r', encoding='utf-8') as f:
            recipes = json.load(f)
        products = [k for k, v in recipes.items() if v.get('type') in ['P2', 'P3', 'P4']]
        return {"status": "success", "bases": bases, "products": sorted(products)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/systems/{constellation}")
def get_systems(constellation: str):
    try:
        df = load_planet_data()
        if constellation == "ALL":
            systems = sorted(list(df['System'].dropna().unique()))
        else:
            systems = sorted(list(df[df['Constellation'] == constellation]['System'].dropna().unique()))
        return {"status": "success", "systems": systems}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/calculate")
def calculate_plan(req: PlanRequest):
    try:
        with open('recipes.json', 'r', encoding='utf-8') as f:
            recipes = json.load(f)
            
        raw_materials = set()
        production_steps = []
        
        def parse_recipe(prod):
            d = recipes.get(prod)
            if not d: return
            ptype = d.get('type')
            
            if ptype == 'P1': 
                raw_materials.add((prod, d.get('source', 'Сырье')))
            elif ptype in ['P2', 'P3', 'P4']:
                inputs = d.get('inputs', {})
                production_steps.append((prod, ptype, inputs))
                for inc in inputs.keys():
                    parse_recipe(inc)
                    
        parse_recipe(req.target_product)
        
        unique_steps = []
        seen = set()
        for prod, ptype, inputs in production_steps:
            if prod not in seen:
                seen.add(prod)
                unique_steps.append((prod, ptype, inputs))
                
        tier_order = {'P2': 1, 'P3': 2, 'P4': 3}
        unique_steps.sort(key=lambda x: tier_order.get(x[1], 0))
        
        df = load_planet_data()
        
        if req.constellation == "ALL":
            search_df = df.copy()
        else:
            search_df = df[df['Constellation'] == req.constellation].copy()
        
        # ЧТЕНИЕ ИЗ БАЗЫ ДАННЫХ ВМЕСТО УДАЛЕННОГО MOCK_SESSION_CHARS
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name, ccu_level as ccu FROM characters")
        all_chars = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        factories = [c for c in all_chars if c['ccu'] == 5]
        miners = [c for c in all_chars if c['ccu'] < 5]
        
        plan = []
        factory_const_series = df[df['System'] == req.factory_sys]['Constellation']
        factory_const = factory_const_series.iloc[0] if not factory_const_series.empty else req.constellation
        
        fact_idx = 0
        for prod, ptype, inputs in unique_steps:
            f_char = factories[fact_idx % len(factories)] if factories else {"name": "Нет альта", "ccu": 5}
            
            prev_tier = "P1" if ptype == "P2" else "P2" if ptype == "P3" else "P3"
            if ptype == "P4": prev_tier = "P3"
                
            inputs_str = ", ".join(inputs.keys()) if inputs else "Компоненты"
            structures_text = "24 Advanced Factories" if ptype in ["P2", "P3"] else "6-8 High-Tech Factories"
            
            plan.append({
                "constellation": factory_const,
                "system": req.factory_sys,
                "planet": "Любая Barren",
                "character": f_char['name'],
                "assignment": f"Переработка {prev_tier} -> {ptype} ({inputs_str})",
                "res_out": prod,
                "structures": structures_text,
                "role": f"🏭 Переработка {ptype}",
                "res_in": inputs_str,
                "pg_load": 92,
                "cpu_load": 95
            })
            fact_idx += 1
            
        miner_idx = 0
        for p1_res, p0_res in raw_materials:
            if p0_res in search_df.columns:
                search_df[p0_res] = pd.to_numeric(search_df[p0_res], errors='coerce')
                avail = search_df.dropna(subset=[p0_res])
                
                if not avail.empty:
                    bp = avail.loc[avail[p0_res].idxmax()]
                    
                    try:
                        radius_str = str(bp.iloc[4]).replace(' ', '').replace(',', '.')
                        planet_radius = float(radius_str)
                    except Exception:
                        planet_radius = 5000
                    
                    miner_char = miners[miner_idx % len(miners)] if miners else {"name": "Нет альта", "ccu": 0}
                    
                    base_pg = 70
                    radius_penalty = (planet_radius / 10000) * 8
                    skill_penalty = 0 if miner_char['ccu'] == 5 else (5 - miner_char['ccu']) * 12
                    total_pg_load = int(base_pg + radius_penalty + skill_penalty)
                    
                    plan.append({
                        "constellation": bp['Constellation'], 
                        "system": bp['System'], 
                        "planet": f"Planet {bp['Planet']} ({bp['Type']})",
                        "character": miner_char['name'],
                        "assignment": f"Добыча P0 -> P1 ({p0_res})",
                        "res_out": p1_res,
                        "structures": "Экстрактор + 10-12 Basic Factories",
                        "role": f"⛏ Добыча P1",
                        "res_in": "-",
                        "pg_load": total_pg_load,
                        "cpu_load": 75
                    })
                    miner_idx += 1
                    
        return {"status": "success", "data": plan}
    except Exception as e:
        print("ОШИБКА РАСЧЕТА:")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/export")
def export_plan(req: ExportRequest):
    try:
        formatted_data = []
        for row in req.plan_data:
            formatted_data.append({
                "Констелляция": row.get('constellation', ''),
                "Система": row.get('system', ''),
                "Планета": row.get('planet', ''),
                "Персонаж": row.get('character', ''),
                "Назначение (Сырье / Переработка)": row.get('assignment', ''),
                "Выходной ресурс": row.get('res_out', ''),
                "Количество заводов": row.get('structures', '')
            })
            
        df = pd.DataFrame(formatted_data)
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='PI Plan')
            worksheet = writer.sheets['PI Plan']
            
            header_fill = PatternFill(start_color="548235", end_color="548235", fill_type="solid")
            header_font = Font(color="FFFFFF", bold=True)
            row_fill_light = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")
            
            for cell in worksheet[1]:
                cell.fill = header_fill
                cell.font = header_font
                
            for row_idx, row in enumerate(worksheet.iter_rows(min_row=2, max_row=worksheet.max_row), start=2):
                if row_idx % 2 == 0:
                    for cell in row:
                        cell.fill = row_fill_light

            for col in worksheet.columns:
                max_length = 0
                column_letter = col[0].column_letter
                for cell in col:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                worksheet.column_dimensions[column_letter].width = max_length + 2

        output.seek(0)
        
        headers = {'Content-Disposition': 'attachment; filename="PI_Logistics_Plan.xlsx"'}
        return StreamingResponse(output, headers=headers, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)