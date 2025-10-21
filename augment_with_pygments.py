#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
augment_with_pygments.py
------------------------
Augment data/derived/languages_master_augmented.csv using Pygments' lexer mapping:
  https://github.com/pygments/pygments/blob/master/pygments/lexers/_mapping.py

Adds columns (per matched language):
  - in_pygments (bool)
  - pygments_name (canonical Pygments display name)
  - pygments_module, pygments_class
  - pygments_aliases (semicolon-separated)
  - pygments_filenames (semicolon-separated)
  - pygments_mimetypes (semicolon-separated)

Also writes a report of languages that exist in Pygments but were not matched:
  data/derived/pygments_missing_from_master.csv

Extra heuristics:
  - Built-in alias table expanded (e.g., "vim script" -> VimL).
  - If name/alias matching fails, scan the row text for filename/extension tokens
    derived from Pygments filename patterns (*.ext, exact filenames). Choose the
    candidate with the longest matching token.

Usage (defaults):
  python augment_with_pygments.py
Or explicit:
  python augment_with_pygments.py \
    --in data/derived/languages_master_augmented.csv \
    --out data/derived/languages_master_augmented_pygments.csv \
    --missing data/derived/pygments_missing_from_master.csv \
    --langcol name  # if auto-detect fails

Requires: requests, pandas
"""
from __future__ import annotations

import argparse, ast, re, sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests

PYGMENTS_MAPPING_URL = "https://raw.githubusercontent.com/pygments/pygments/master/pygments/lexers/_mapping.py"

# --------------------------- Normalization ---------------------------

def normalize_token(s: str) -> str:
    s = (s or "").strip()
    s = s.replace("–", "-").replace("—", "-").replace("’", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"\s+", " ", s)
    return s

def normalize_key(s: str) -> str:
    s = normalize_token(s).lower()
    s = re.sub(r"[^a-z0-9+#.\- ]+", "", s)
    s = s.strip()
    return s

def builtin_alias_table() -> Dict[str,str]:
    # Pragmatic normalizations that map to Pygments alias keys or display names
    return {
        "c sharp": "csharp", "c-sharp": "csharp", "c#": "csharp",
        "f sharp": "fsharp", "f-sharp": "fsharp", "f#": "fsharp",
        "c plus plus": "cpp", "cplusplus": "cpp", "c++": "cpp", "cpp": "cpp",
        "objective c": "objective-c", "objective-c": "objective-c", "obj-c": "objective-c",
        "objective c++": "objective-c++", "objective-c++": "objective-c++", "obj-c++": "objective-c++",
        "golang": "go",
        "js": "javascript", "ts": "typescript",
        "vb.net": "vbnet", "vb": "vbnet", "visual basic": "vbnet",
        "ocaml": "ocaml", "objective caml": "ocaml",
        "shell": "bash", "shell script": "bash", "unix shell": "bash",
        "wolfram language": "mathematica", "wolfram": "mathematica",
        "rstats": "r",
        "yaml": "yaml", "yml": "yaml",
        "jsonc": "json", "json5": "json",
        "pl/sql": "plsql", "pl-sql": "plsql", "plpgsql": "postgresql",
        "powershell": "powershell",
        # Vim special-casing
        "vim script": "viml", "vimscript": "viml",
    }

# --------------------------- Fetch & Parse Pygments ---------------------------

def fetch_text(url: str) -> str:
    r = requests.get(url, headers={"Accept": "text/plain"}, timeout=30)
    r.raise_for_status()
    return r.text

def extract_lexers_mapping(src_text: str) -> Dict[str, Tuple[str, str, List[str], List[str], List[str]]]:
    """
    Parse the Python source to get the LEXERS dict safely via AST.
    LEXERS is a dict mapping display-name -> (module, class, aliases, filenames, mimetypes)
    """
    tree = ast.parse(src_text, filename="_mapping.py", mode="exec")
    lexers_node = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "LEXERS":
                    lexers_node = node.value
                    break
    if lexers_node is None:
        raise RuntimeError("Could not find LEXERS assignment in _mapping.py")
    lexers = ast.literal_eval(lexers_node)
    out = {}
    for disp, tup in lexers.items():
        mod, cls, aliases, filenames, mimetypes = tup
        out[str(disp)] = (str(mod), str(cls),
                          [str(a) for a in aliases],
                          [str(f) for f in filenames],
                          [str(m) for m in mimetypes])
    return out

# --------------------------- Build Indexes ---------------------------

def build_pygments_index(lexers: Dict[str, Tuple[str, str, List[str], List[str], List[str]]]):
    """
    Returns:
      - name2meta: canonical display-name -> meta dict
      - alias_index: normalized token -> canonical display-name (covers name + aliases)
      - filename_index: token ('.ext' or bare filename) -> set of canonical display-names
    """
    name2meta = {}
    alias_index: Dict[str, str] = {}
    filename_index: Dict[str, set] = {}

    def add_filename_token(tok: str, disp: str):
        tok = tok.lower()
        filename_index.setdefault(tok, set()).add(disp)

    for disp, (mod, cls, aliases, filenames, mimetypes) in lexers.items():
        meta = {
            "pygments_name": disp,
            "pygments_module": mod,
            "pygments_class": cls,
            "pygments_aliases": aliases,
            "pygments_filenames": filenames,
            "pygments_mimetypes": mimetypes,
        }
        name2meta[disp] = meta
        # Index display-name and aliases
        alias_index[normalize_key(disp)] = disp
        for a in aliases:
            alias_index[normalize_key(a)] = disp

        # Build filename/extension tokens
        for pat in filenames:
            p = pat.strip()
            if not p:
                continue
            # Extract extension like '*.vim' -> '.vim'
            m = re.match(r'^\*\.(?P<ext>[A-Za-z0-9_+\-\.]+)$', p)
            if m:
                add_filename_token("." + m.group("ext").lower().lstrip("."), disp)
                continue
            # Bare filenames like 'vimrc', '.vimrc', '_vimrc', 'Makefile'
            # Normalize leading underscores/dots by keeping both raw and stripped variants
            bare = p.lstrip("./")
            add_filename_token(bare, disp)
            add_filename_token(bare.lstrip("_."), disp)

    return name2meta, alias_index, filename_index

# --------------------------- Matching ---------------------------

def pick_master_name(row, langcol_candidates: List[str]) -> str:
    if "hyperpolyglot_name" in row and pd.notna(row["hyperpolyglot_name"]) and str(row["hyperpolyglot_name"]).strip():
        return str(row["hyperpolyglot_name"])
    for c in langcol_candidates:
        if c in row and pd.notna(row[c]) and str(row[c]).strip():
            return str(row[c])
    return ""

def row_text_blob(row) -> str:
    # Concatenate all stringable fields to allow filename/extension scanning
    try:
        return " | ".join([str(v) for v in row.values]).lower()
    except Exception:
        return ""

def match_to_pygments(name: str, row_blob: str, alias_index: Dict[str,str], filename_index: Dict[str,set], builtin_aliases: Dict[str,str]) -> Optional[str]:
    # Name/alias path
    key = normalize_key(name)
    if key in alias_index:
        return alias_index[key]
    if key in builtin_aliases:
        alias_key = normalize_key(builtin_aliases[key])
        if alias_key in alias_index:
            return alias_index[alias_key]

    # Filename/extension heuristic
    # Gather all candidate display-names from tokens found in the row blob
    candidates = []
    for tok, names in filename_index.items():
        if tok and tok in row_blob:
            for disp in names:
                candidates.append((len(tok), tok, disp))
    if candidates:
        # prefer the longest matching token
        candidates.sort(reverse=True)  # by length, then lexicographic
        return candidates[0][2]

    # Simple hyphen/space collapsing
    for k2 in {key.replace(" ", "-"), key.replace("-", " "), key.replace(" ", "")}:
        if k2 in alias_index:
            return alias_index[k2]

    return None

# --------------------------- Main ---------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_csv", default="data/derived/languages_master_augmented.csv", help="Input CSV path (augmented)")
    ap.add_argument("--out", dest="out_csv", default="data/derived/languages_master_augmented_pygments.csv", help="Output augmented CSV path")
    ap.add_argument("--missing", dest="missing_csv", default="data/derived/pygments_missing_from_master.csv", help="Output Pygments-only report CSV path")
    ap.add_argument("--langcol", dest="langcol", default=None, help="Language name column in input CSV (optional)")
    args = ap.parse_args()

    # Load
    df = pd.read_csv(args.in_csv)

    # Candidate columns
    candidates = ["hyperpolyglot_name", args.langcol] if args.langcol else ["hyperpolyglot_name", "language", "lang", "name", "language_name", "programming_language"]
    candidates = [c for c in candidates if c is not None]

    # Fetch and parse Pygments
    mapping_src = fetch_text(PYGMENTS_MAPPING_URL)
    lexers = extract_lexers_mapping(mapping_src)
    name2meta, alias_index, filename_index = build_pygments_index(lexers)
    builtin_aliases = builtin_alias_table()

    # Match & enrich
    pyg_matches: List[Optional[str]] = []
    for _, row in df.iterrows():
        master_name = pick_master_name(row, candidates)
        blob = row_text_blob(row)
        pyg_name = match_to_pygments(master_name, blob, alias_index, filename_index, builtin_aliases)
        pyg_matches.append(pyg_name)

    df["pygments_name"] = pyg_matches
    df["in_pygments"] = df["pygments_name"].notna()

    def join_list(v):
        return ";".join(v) if isinstance(v, list) else None

    df["pygments_module"]    = df["pygments_name"].map(lambda n: name2meta.get(n, {}).get("pygments_module") if n else None)
    df["pygments_class"]     = df["pygments_name"].map(lambda n: name2meta.get(n, {}).get("pygments_class") if n else None)
    df["pygments_aliases"]   = df["pygments_name"].map(lambda n: join_list(name2meta.get(n, {}).get("pygments_aliases")) if n else None)
    df["pygments_filenames"] = df["pygments_name"].map(lambda n: join_list(name2meta.get(n, {}).get("pygments_filenames")) if n else None)
    df["pygments_mimetypes"] = df["pygments_name"].map(lambda n: join_list(name2meta.get(n, {}).get("pygments_mimetypes")) if n else None)

    # Save augmented
    Path(args.out_csv).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out_csv, index=False)

    # Report: what's in Pygments but missing from master
    matched = set([m for m in pyg_matches if m])
    pyg_all = set(name2meta.keys())
    pyg_only = sorted(pyg_all - matched)
    pd.DataFrame({"pygments_only": pyg_only}).to_csv(args.missing_csv, index=False)

    print(f"[done] In Pygments matches: {df['in_pygments'].sum()} / {len(df)}")
    print(f"[done] Augmented: {args.out_csv}")
    print(f"[done] Pygments-only report: {args.missing_csv} (count={len(pyg_only)})")

if __name__ == "__main__":
    main()
