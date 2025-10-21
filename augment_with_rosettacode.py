#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse, time, re, difflib
from typing import Dict, List, Optional
from pathlib import Path
import pandas as pd
import requests

PRIMARY_API = "https://rosettacode.org/w/api.php"
FALLBACK_API = "https://rosettacode.org/mw/api.php"
API_BASES = [PRIMARY_API, FALLBACK_API]
HEADERS = {"User-Agent": "augment-with-rosettacode/2.1 (+https://example.invalid)"}

def normalize_token(s: str) -> str:
    s = (s or "").strip()
    s = s.replace("–","-").replace("—","-").replace("’","'").replace("“",'"').replace("”",'"')
    s = re.sub(r"\s+"," ",s)
    return s

def normalize_key(s: str) -> str:
    s = normalize_token(s).lower()
    s = re.sub(r"[^a-z0-9+#.\- ]+","",s)
    return s.strip()

def alias_map() -> Dict[str,str]:
    return {
        "c sharp":"c#", "c-sharp":"c#", "csharp":"c#",
        "f sharp":"f#","f-sharp":"f#","fsharp":"f#",
        "c plus plus":"c++","cplusplus":"c++","cpp":"c++",
        "objective c":"objective-c","obj-c":"objective-c",
        "objective c++":"objective-c++","obj-c++":"objective-c++",
        "golang":"go",
        "js":"javascript","ts":"typescript",
        "vb.net":"visual basic .net","vb":"visual basic .net","visual basic":"visual basic .net",
        "ocaml":"ocaml","objective caml":"ocaml",
        "vim script":"vim script","vimscript":"vim script",
        "wolfram language":"mathematica","wolfram":"mathematica",
        "rstats":"r",
        "yml":"yaml",
        "jsonc":"json","json5":"json",
        "pl/sql":"plsql","pl-sql":"plsql",
        "pl/pgsql":"plpgsql",
        "powershell":"powershell",
    }

def _tokenize_pieces(s: str):
    yield s
    for sep in [';', ',', '|', '/', '\\']:
        if sep in s:
            for part in s.split(sep):
                part = part.strip()
                if part:
                    yield part
    if any(ch in s for ch in ['_', '-']):
        for part in re.split(r'[_-]+', s):
            part = part.strip()
            if part:
                yield part

def _variants(key: str):
    yield key
    yield key.replace(' ', '-')
    yield key.replace('-', ' ')
    yield key.replace(' ', '')
    yield key.replace('-', '')

def api_get(params: Dict[str, str]) -> Dict:
    last_exc = None
    for base in API_BASES:
        for attempt in range(3):
            try:
                p = dict(params); p["format"] = "json"
                r = requests.get(base, params=p, headers=HEADERS, timeout=30)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last_exc = e
                time.sleep(0.3*(attempt+1))
    raise RuntimeError(f"RosettaCode API failed. Last error: {last_exc}")

def list_language_subcategories(root_category: str = "Programming Languages") -> List[str]:
    titles = []
    cmcontinue = None
    while True:
        params = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": f"Category:{root_category}",
            "cmnamespace": "14",
            "cmtype": "subcat",
            "cmlimit": "500",
        }
        if cmcontinue: params["cmcontinue"] = cmcontinue
        data = api_get(params)
        for m in data.get("query",{}).get("categorymembers",[]):
            t = m.get("title")
            if t: titles.append(t)
        cmcontinue = data.get("continue",{}).get("cmcontinue")
        if not cmcontinue: break
        time.sleep(0.15)
    return titles

def fetch_extracts(main_titles: List[str]) -> Dict[str,str]:
    out = {}
    BATCH=50
    for i in range(0,len(main_titles),BATCH):
        chunk = main_titles[i:i+BATCH]
        params = {
            "action":"query", "prop":"extracts", "exintro":"1", "explaintext":"1",
            "titles":"|".join(chunk),
        }
        data = api_get(params)
        for pg in data.get("query",{}).get("pages",{}).values():
            title = pg.get("title"); extract = pg.get("extract") or ""
            if title: out[title]=extract.strip()
        time.sleep(0.15)
    return out

def fetch_categoryinfo_counts(category_titles: List[str]) -> Dict[str,int]:
    out = {}
    BATCH=50
    for i in range(0,len(category_titles),BATCH):
        chunk = category_titles[i:i+BATCH]
        params = {
            "action":"query", "prop":"categoryinfo", "titles":"|".join(chunk),
        }
        data = api_get(params)
        for pg in data.get("query",{}).get("pages",{}).values():
            t = pg.get("title"); ci = pg.get("categoryinfo") or {}
            pages = ci.get("pages")
            if t is not None and pages is not None: out[t]=int(pages)
        time.sleep(0.15)
    return out

def build_master_index_all_strings(df: pd.DataFrame) -> Dict[str,int]:
    idx = {}
    am = alias_map()
    for ridx, row in df.iterrows():
        for col in df.columns:
            val = row[col]
            if pd.isna(val): continue
            s = str(val)
            if not s.strip(): continue
            for piece in _tokenize_pieces(s):
                key = normalize_key(piece)
                if not key: continue
                for v in _variants(key):
                    idx.setdefault(v, ridx)
                ali = am.get(key)
                if ali:
                    for v in _variants(ali):
                        idx.setdefault(v, ridx)
    return idx

def map_rosetta_to_master(name: str, index: Dict[str,int]) -> Optional[int]:
    key = normalize_key(name)
    if key in index: return index[key]
    ali = alias_map().get(key, key)
    for v in _variants(ali):
        if v in index: return index[v]
    candidates = list(index.keys())
    best = difflib.get_close_matches(ali, candidates, n=1, cutoff=0.92)
    if best: return index[best[0]]
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_csv", default="data/derived/languages_master_augmented_pygments.csv")
    ap.add_argument("--out", dest="out_csv", default="data/derived/languages_master_augmented_rosettacode.csv")
    ap.add_argument("--missing", dest="missing_csv", default="data/derived/rosettacode_missing_from_master.csv")
    ap.add_argument("--dump", dest="dump_csv", default="data/derived/rosettacode_languages.csv")
    args = ap.parse_args()

    cat_titles = list_language_subcategories("Programming Languages")
    main_titles = [t.split("Category:",1)[1] if t.startswith("Category:") else t for t in cat_titles]
    extracts = fetch_extracts(main_titles)
    cat_counts = fetch_categoryinfo_counts(cat_titles)

    rows = [{"rosettacode_name": mt,
             "rosettacode_url": f"https://rosettacode.org/wiki/{mt.replace(' ','_')}",
             "rosettacode_summary": extracts.get(mt,""),
             "rosettacode_tasks_count": int(cat_counts.get(ct,0))}
            for ct, mt in zip(cat_titles, main_titles)]
    rc_df = pd.DataFrame(rows).sort_values("rosettacode_name").reset_index(drop=True)
    Path(args.dump_csv).parent.mkdir(parents=True, exist_ok=True)
    rc_df.to_csv(args.dump_csv, index=False)

    master = pd.read_csv(args.in_csv)
    index = build_master_index_all_strings(master)

    if "in_rosettacode" not in master.columns:
        master["in_rosettacode"] = False
    master["in_rosettacode"] = master["in_rosettacode"].astype("boolean")
    for col in ["rosettacode_name","rosettacode_url","rosettacode_summary"]:
        if col not in master.columns: master[col]=pd.NA
    if "rosettacode_tasks_count" not in master.columns: master["rosettacode_tasks_count"]=pd.NA

    for _, r in rc_df.iterrows():
        ridx = map_rosetta_to_master(r["rosettacode_name"], index)
        if ridx is None: continue
        master.at[ridx,"in_rosettacode"]=True
        master.at[ridx,"rosettacode_name"]=r["rosettacode_name"]
        master.at[ridx,"rosettacode_url"]=r["rosettacode_url"]
        master.at[ridx,"rosettacode_summary"]=r["rosettacode_summary"]
        master.at[ridx,"rosettacode_tasks_count"]=r["rosettacode_tasks_count"]

    Path(args.out_csv).parent.mkdir(parents=True, exist_ok=True)
    master.to_csv(args.out_csv, index=False)

    unmatched = rc_df[~rc_df["rosettacode_name"].map(lambda t: map_rosetta_to_master(t, index) is not None)]
    unmatched[["rosettacode_name","rosettacode_url"]].to_csv(args.missing_csv, index=False)

    print(f"[done] Rosetta languages (subcategories): {len(rc_df)}")
    print(f"[done] Matched rows in master: {int(master['in_rosettacode'].sum())}")
    print(f"[done] Augmented: {args.out_csv}")
    print(f"[done] Rosetta-only report: {args.missing_csv} (count={len(unmatched)})")

if __name__ == "__main__":
    main()
