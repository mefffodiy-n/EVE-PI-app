import eel
import pandas as pd
import json
import tkinter as tk
from tkinter import filedialog

eel.init('web')

# Матрица персонажей на базе твоего файла
MOCK_CHARACTERS = [
    {"name": "Meda Metha", "ccu": 5, "ic": 5},
    {"name": "Shardani Ponad", "ccu": 5, "ic": 5},
    {"name": "Morfiy Mefodiy", "ccu": 4, "ic": 4},
    {"name": "Olga Santclair", "ccu": 4, "ic": 4}
]

@eel.expose
def get_initial_data():
    df = pd.read_csv('planet industry.csv', sep=';', skiprows=1, encoding='utf-8')
    df = df.dropna(subset=['Constellation'])
    df = df[df['Constellation'] != 'max P2']
    bases = sorted(list(df['Constellation'].unique()))
    
    with open('recipes.json', 'r', encoding='utf-8') as f:
        recipes = json.load(f)
    products = [k for k, v in recipes.items() if v.get('type') in ['P2', 'P3', 'P4']]
    
    return {"bases": bases, "products": sorted(products)}

@eel.expose
def get_systems(constellation):
    df = pd.read_csv('planet industry.csv', sep=';', skiprows=1, encoding='utf-8')
    systems = sorted(list(df[df['Constellation'] == constellation]['System'].dropna().unique()))
    return systems

@eel.expose
def calculate_plan(constellation, factory_sys, target_product):
    try:
        with open('recipes.json', 'r', encoding='utf-8') as f:
            recipes = json.load(f)
            
        # Определяем входящее сырье для завода
        recipe_data = recipes.get(target_product)
        factory_inputs = ", ".join(recipe_data.get('inputs', {}).keys()) if recipe_data else "Сырье"
        
        # Разворачиваем рецепт до P0 (базовых ресурсов)
        raw_materials = set()
        def get_raws(prod):
            d = recipes.get(prod)
            if not d: return
            if d.get('type') == 'P1': raw_materials.add(d['source'])
            elif 'inputs' in d:
                for c in d['inputs']: get_raws(c)
        get_raws(target_product)
        
        df = pd.read_csv('planet industry.csv', sep=';', skiprows=1, encoding='utf-8')
        sys_df = df[df['System'] == factory_sys]
        
        factories = [c for c in MOCK_CHARACTERS if c['ccu'] == 5]
        miners = [c for c in MOCK_CHARACTERS if c['ccu'] < 5]
        
        plan = []
        # Назначаем заводы на выбранную систему[cite: 1]
        for f_char in factories:
            plan.append({
                "character": f_char['name'],
                "system": factory_sys,
                "role": "🏭 Переработка",
                "planet": "Любая Barren / Temperate",
                "res_in": factory_inputs,
                "res_out": target_product
            })
            
        # Назначаем добычу по нужным ресурсам (ищем планеты)
        miner_idx = 0
        for res in raw_materials:
            if res in sys_df.columns:
                sys_df[res] = pd.to_numeric(sys_df[res], errors='coerce')
                avail = sys_df.dropna(subset=[res])
                if not avail.empty:
                    bp = avail.loc[avail[res].idxmax()]
                    miner_name = miners[miner_idx % len(miners)]['name'] if miners else "Нет свободного альта"
                    plan.append({
                        "character": miner_name,
                        "system": factory_sys,
                        "role": "⛏ Добыча",
                        "planet": f"Планета {bp['Planet']} ({bp['Type']})",
                        "res_in": "-",
                        "res_out": res
                    })
                    miner_idx += 1
                    
        return {"status": "success", "data": plan}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@eel.expose
def export_plan_excel(plan_data):
    try:
        # Прячем главное окно Tkinter и вызываем диалог сохранения
        root = tk.Tk()
        root.attributes('-topmost', True)
        root.withdraw()
        
        filepath = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel files", "*.xlsx")],
            title="Сохранить логистический план"
        )
        root.destroy()
        
        if not filepath:
            return {"status": "cancelled"}
            
        # Форматируем под твое ТЗ: Ник, Система, Вход, Выход
        formatted_data = []
        for row in plan_data:
            formatted_data.append({
                "Ник персонажа": row['character'],
                "Система": row['system'],
                "Входящий ресурс": row['res_in'],
                "Исходящий ресурс": row['res_out'],
                "Локация": row['planet']
            })
            
        df = pd.DataFrame(formatted_data)
        df.to_excel(filepath, index=False)
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

if __name__ == '__main__':
    eel.start('index.html', size=(1100, 800))