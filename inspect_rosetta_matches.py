#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import re, argparse, difflib
from pathlib import Path
from typing import Dict, List, Optional
import pandas as pd

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

def build_master_index_all_strings(df: pd.DataFrame) -> Dict[str,int]:
    idx = {}
    am = alias_map()
    for ridx, row in df.iterrows():
        for col in df.columns:
            val = row[col]
            if pd.isna(val):
                continue
            s = str(val)
            if not s.strip():
                continue
            for piece in _tokenize_pieces(s):
                key = normalize_key(piece)
                if not key:
                    continue
                for v in _variants(key):
                    idx.setdefault(v, ridx)
                ali = am.get(key)
                if ali:
                    for v in _variants(ali):
                        idx.setdefault(v, ridx)
    return idx

def map_rosetta_to_master(name: str, index: Dict[str,int]) -> Optional[int]:
    key = normalize_key(name)
    if key in index:
        return index[key]
    ali = alias_map().get(key, key)
    for v in _variants(ali):
        if v in index:
            return index[v]
    # fuzzy last resort
    candidates = list(index.keys())
    best = difflib.get_close_matches(ali, candidates, n=1, cutoff=0.92)
    if best:
        return index[best[0]]
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--master", default="data/derived/languages_master_augmented_rosettacode.csv")
    ap.add_argument("--rc", default="data/derived/rosettacode_languages.csv")
    ap.add_argument("--out", default="data/derived/rosettacode_match_details.csv")
    args = ap.parse_args()

    master = pd.read_csv(args.master)
    rc = pd.read_csv(args.rc)

    index = build_master_index_all_strings(master)

    rows = []
    for _, r in rc.iterrows():
        rc_name = r.get("rosettacode_name") or r.get("name") or ""
        rc_url  = r.get("rosettacode_url") or ""
        ridx = map_rosetta_to_master(str(rc_name), index)
        rows.append({
            "rosettacode_name": rc_name,
            "rosettacode_url": rc_url,
            "matched": ridx is not None,
            "master_row_index": int(ridx) if ridx is not None else None,
        })

    outdf = pd.DataFrame(rows)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    outdf.to_csv(args.out, index=False)

    peek = outdf[outdf["rosettacode_name"].str.contains("ALGOL", case=False, na=False)]
    if not peek.empty:
        print(peek.to_string(index=False)[:1000])

if __name__ == "__main__":
    main()
