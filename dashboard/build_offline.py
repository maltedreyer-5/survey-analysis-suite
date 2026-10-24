#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Converts the JSON files into JS files for offline use.
Run this script before offering the dashboard as a download.

Usage:
    python build_offline.py

The script creates/updates:
    - js/data.js (from data/dashboard_data.json)
    - js/themes-data.js (from data/themes.json)
    - js/correlations-data.js (from data/correlations.json)
    - js/survey-structure.js (from data/survey_structure.json)
"""

import json
import os
from pathlib import Path

def convert_json_to_js(json_path, js_path, var_name):
    """Converts a JSON file into a JS file with one constant."""
    if not os.path.exists(json_path):
        print(f"  ⚠️  {json_path} not found, skipping...")
        return False
    
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        js_content = f"// Auto-generated from {os.path.basename(json_path)}\n"
        js_content += f"// Generated for offline use\n"
        js_content += f"const {var_name} = {json.dumps(data, ensure_ascii=False)};\n"
        
        with open(js_path, 'w', encoding='utf-8') as f:
            f.write(js_content)
        
        size_kb = os.path.getsize(js_path) / 1024
        print(f"  ✅ {js_path} ({size_kb:.1f} KB)")
        return True
        
    except json.JSONDecodeError as e:
        print(f"  ❌ JSON error in {json_path}: line {e.lineno}, {e.msg}")
        return False
    except Exception as e:
        print(f"  ❌ Error in {json_path}: {e}")
        return False

def main():
    # base directory (where this script lives)
    base_dir = Path(__file__).parent
    data_dir = base_dir / "data"
    js_dir = base_dir / "js"
    
    print("=" * 50)
    print("Offline build of the survey dashboard")
    print("=" * 50)
    print()
    
    # mapping: JSON file -> (JS file, variable name)
    conversions = [
        ("dashboard_data.json", "data.js", "DASHBOARD_DATA"),
        ("themes.json", "themes-data.js", "THEMES_DATA"),
        ("correlations.json", "correlations-data.js", "CORRELATIONS_DATA"),
        ("survey_structure.json", "survey-structure.js", "SURVEY_STRUCTURE"),
        ("segment_clusters.json", "segment-clusters-data.js", "SEGMENT_CLUSTERS_DATA"),
        ("segment_insights.json", "segment-insights-data.js", "SEGMENT_INSIGHTS_DATA"),
    ]
    
    print("Converting JSON → JS for offline use:\n")
    
    success_count = 0
    for json_file, js_file, var_name in conversions:
        json_path = data_dir / json_file
        js_path = js_dir / js_file
        
        if convert_json_to_js(json_path, js_path, var_name):
            success_count += 1
    
    print()
    print(f"Done! {success_count}/{len(conversions)} files converted.")
    print()
    print("The dashboard can now also be opened locally (file://).")

if __name__ == "__main__":
    main()
