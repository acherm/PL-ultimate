#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
augment_with_pygments.py (hardened)
-----------------------------------
Augment data/derived/languages_master_augmented.csv using Pygments' lexer mapping.

Adds columns:
  - in_pygments (bool)
  - pygments_name           (display name key in LEXERS, e.g., "Mojo")
  - pygments_module         (e.g., "pygments.lexers.mojo")
  - pygments_class          (e.g., "MojoLexer")
  - pygments_aliases        (semicolon-joined)
  - pygments_filenames      (semicolon-joined)
  - pygments_mimetypes      (semicolon-joined)

Also writes Pygments-only report:
  data/derived/pygments_missing_from_master.csv

Heuristics:
  1) Name/aliases: match normalized hyperpolyglot_name (or fallback column) to
     Pygments display-name or aliases (safe normalization; empty aliases dropped).
  2) Filename/extension (SAFE): only scan user-declared extension columns
     (auto-detects cols whose names include: ext, file, pattern, filename).
     No whole-row substring scanning. Requires tokens like ".vim", "vimrc".
  3) Token filters: ignore tokens shorter than 3 chars unless token starts with
     a dot and has length >= 3 (e.g., ".vim" ok, ".c" ignored).

Usage:
  python augment_with_pygments.py \
    --in data/derived/languages_master_augmented.csv \
    --out data/derived/languages_master_augmented_pygments.csv \
    --missing data/derived/pygments_missing_from_master.csv \
    [--langcol name] \
    [--extcols extensions,filenames]

Requires: requests, pandas
"""
from __future__ import annotations

import argparse, ast, re, sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Iterable

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
        "vim script": "viml", "vimscript": "viml",
    }

# --------------------------- Fetch & Parse Pygments ---------------------------

def fetch_text(url: str) -> str:
    r = requests.get(url, headers={"Accept": "text/plain"}, timeout=45)
    r.raise_for_status()
    return r.text

def extract_lexers_mapping(src_text: str) -> Dict[str, Tuple[str, str, List[str], List[str], List[str]]]:
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

# --------------------------- Indexes ---------------------------

def build_pygments_indexes(lexers):
    """
    Returns:
      - name2meta: display-name -> meta
      - alias_index: normalized token -> display-name (display-name & aliases)
      - fname_index: filename/ext token -> set(display-name)
    """
    name2meta = {}
    alias_index: Dict[str, str] = {}
    fname_index: Dict[str, set] = {}

    def add_fname_token(tok: str, disp: str):
        tok = tok.strip().lower()
        if not tok:
            return
        # ignore very short tokens like ".c"
        if tok.startswith("."):
            if len(tok) < 3:  # ".c" length 2 -> ignore
                return
        else:
            if len(tok) < 3:
                return
        fname_index.setdefault(tok, set()).add(disp)

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
        nk = normalize_key(disp)
        if nk:
            alias_index[nk] = disp
        for a in aliases:
            ak = normalize_key(a)
            if ak:  # drop empty alias like "🔥" -> ""
                alias_index[ak] = disp

        # Filename tokens
        for pat in filenames:
            p = pat.strip()
            if not p:
                continue
            m = re.match(r'^\*\.(?P<ext>[A-Za-z0-9_+\-\.]+)$', p)
            if m:
                add_fname_token("." + m.group("ext").lower().lstrip("."), disp)
            else:
                bare = p.lstrip("./")
                add_fname_token(bare, disp)
                add_fname_token(bare.lstrip("_."), disp)

    return name2meta, alias_index, fname_index

# --------------------------- Matching ---------------------------

def pick_master_name(row, candidates: List[str]) -> str:
    if "hyperpolyglot_name" in row and pd.notna(row["hyperpolyglot_name"]) and str(row["hyperpolyglot_name"]).strip():
        return str(row["hyperpolyglot_name"])
    for c in candidates:
        if c in row and pd.notna(row[c]) and str(row[c]).strip():
            return str(row[c])
    return ""

def split_ext_tokens(val: str) -> Iterable[str]:
    if not val or not isinstance(val, str):
        return []
    # tokens like ".vim", "vimrc", "*.vim" -> ".vim"
    # Split on whitespace or commas/semicolons
    rough = re.split(r"[,\s;|]+", val.strip())
    for t in rough:
        t = t.strip()
        if not t:
            continue
        if t.startswith("*."):
            t = "." + t[2:]
        yield t

def gather_row_ext_tokens(row, extcols: List[str]) -> List[str]:
    tokens = []
    for col in extcols:
        if col in row and pd.notna(row[col]):
            for t in split_ext_tokens(str(row[col])):
                tokens.append(t.lower())
    return tokens

def autodetect_extcols(columns: List[str]) -> List[str]:
    keys = ("ext", "file", "pattern", "filename")
    chosen = [c for c in columns if any(k in c.lower() for k in keys)]
    # De-dup while preserving order
    seen = set(); out = []
    for c in chosen:
        if c not in seen:
            out.append(c); seen.add(c)
    return out

def match_to_pygments(name: str, row_ext_tokens: List[str], alias_index: Dict[str,str], fname_index: Dict[str,set], builtin_aliases: Dict[str,str]) -> Optional[str]:
    key = normalize_key(name)
    if key in alias_index:
        return alias_index[key]
    if key in builtin_aliases:
        alt = normalize_key(builtin_aliases[key])
        if alt in alias_index:
            return alias_index[alt]

    # filename/ext heuristic (strict)
    candidates = []
    for tok in row_ext_tokens:
        if tok in fname_index:
            for disp in fname_index[tok]:
                candidates.append((len(tok), tok, disp))
    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][2]
    return None

# --------------------------- Main ---------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_csv", default="data/derived/languages_master_augmented.csv", help="Input CSV path (augmented)")
    ap.add_argument("--out", dest="out_csv", default="data/derived/languages_master_augmented_pygments.csv", help="Output augmented CSV path")
    ap.add_argument("--missing", dest="missing_csv", default="data/derived/pygments_missing_from_master.csv", help="Output Pygments-only report CSV path")
    ap.add_argument("--langcol", dest="langcol", default=None, help="Language name column in input CSV (optional)")
    ap.add_argument("--extcols", dest="extcols", default=None, help="Comma-separated list of extension/filename columns to use")
    args = ap.parse_args()

    df = pd.read_csv(args.in_csv)

    # Language column candidates
    candidates = ["hyperpolyglot_name", args.langcol] if args.langcol else ["hyperpolyglot_name", "language", "lang", "name", "language_name", "programming_language"]
    candidates = [c for c in candidates if c is not None]

    # Extension columns
    extcols = [c.strip() for c in args.extcols.split(",")] if args.extcols else autodetect_extcols(list(df.columns))

    # Fetch + build indexes
    mapping_src = fetch_text(PYGMENTS_MAPPING_URL)
    lexers = extract_lexers_mapping(mapping_src)
    name2meta, alias_index, fname_index = build_pygments_indexes(lexers)
    builtin_aliases = builtin_alias_table()

    # Match & enrich
    pyg_matches: List[Optional[str]] = []
    for _, row in df.iterrows():
        master_name = pick_master_name(row, candidates)
        ext_tokens = gather_row_ext_tokens(row, extcols)
        pyg_name = match_to_pygments(master_name, ext_tokens, alias_index, fname_index, builtin_aliases)
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

    Path(args.out_csv).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out_csv, index=False)

    matched = set([m for m in pyg_matches if m])
    pyg_all = set(name2meta.keys())
    pyg_only = sorted(pyg_all - matched)
    pd.DataFrame({"pygments_only": pyg_only}).to_csv(args.missing_csv, index=False)

    print(f"[done] In Pygments matches: {df['in_pygments'].sum()} / {len(df)}")
    print(f"[done] Augmented: {args.out_csv}")
    print(f"[done] Pygments-only report: {args.missing_csv} (count={len(pyg_only)})")

if __name__ == "__main__":
    main()
