#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
augment_languages.py (robust fetch)
-----------------------------------
Reads data/derived/languages_master.csv, fetches Hyperpolyglot sources,
matches with a built-in alias map, enriches (hp_type/group/color), and writes:
  - data/derived/languages_master_augmented.csv
  - data/derived/hyperpolyglot_missing_from_master.csv

Primary source: GitHub raw (master). Fallback: docs.rs ?plain=1.

Usage:
  python augment_languages.py \
    --in data/derived/languages_master.csv \
    --out data/derived/languages_master_augmented.csv \
    --missing data/derived/hyperpolyglot_missing_from_master.csv \
    [--langcol name]

Requires: requests, pandas
"""
import argparse, re, sys
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import requests

# GitHub raw (primary)
GH_LANGUAGES_RS_URL = "https://raw.githubusercontent.com/monkslc/hyperpolyglot/master/src/codegen/languages.rs"
GH_LANGUAGE_INFO_MAP_URL = "https://raw.githubusercontent.com/monkslc/hyperpolyglot/master/src/codegen/language-info-map.rs"

# docs.rs (fallback)
DRS_LANGUAGES_RS_URL = "https://docs.rs/crate/hyperpolyglot/latest/source/src/codegen/languages.rs?plain=1"
DRS_LANGUAGE_INFO_MAP_URL = "https://docs.rs/crate/hyperpolyglot/latest/source/src/codegen/language-info-map.rs?plain=1"

UA = {"User-Agent": "augment-languages/1.0 (+https://example.invalid)", "Accept": "text/plain,*/*;q=0.1"}

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
        "c sharp": "C#", "c-sharp": "C#", "csharp": "C#", "cs": "C#", "c#": "C#",
        "f sharp": "F#", "f-sharp": "F#", "fsharp": "F#", "f#": "F#",
        "objective c": "Objective-C", "objective-c": "Objective-C", "obj-c": "Objective-C",
        "objective c++": "Objective-C++", "objective-c++": "Objective-C++", "obj-c++": "Objective-C++",
        "c plus plus": "C++", "cplusplus": "C++", "cpp": "C++", "c++": "C++",
        "c language": "C", "golang": "Go",
        "tsql": "TSQL", "t-sql": "TSQL", "microsoft tsql": "TSQL",
        "pl/sql": "PLSQL", "pl-sql": "PLSQL", "plsql": "PLSQL",
        "pl/pgsql": "PLpgSQL", "plpgsql": "PLpgSQL",
        "cmd": "Batchfile", "dos batch": "Batchfile", "batch": "Batchfile",
        "powershell core": "PowerShell", "windows powershell": "PowerShell", "ps": "PowerShell",
        "z shell": "Shell", "zsh": "Shell", "bash": "Shell", "fish shell": "fish",
        "shell script": "Shell", "unix shell": "Shell", "posix shell": "Shell",
        "html5": "HTML", "html+php": "HTML+PHP", "html+erb": "HTML+ERB", "html+ecr": "HTML+ECR", "html+django": "HTML+Django",
        "scss": "SCSS", "sass": "Sass", "less": "Less", "stylus": "Stylus",
        "js": "JavaScript", "javascript": "JavaScript", "ts": "TypeScript", "tsx": "TSX", "jsx": "JSX",
        "pug": "Pug", "jade": "Pug", "handlebars": "Handlebars", "hbs": "Handlebars", "mustache": "Handlebars",
        "xml plist": "XML Property List", "plist": "XML Property List",
        "yaml": "YAML", "yml": "YAML", "toml": "TOML", "json5": "JSON5", "jsonc": "JSON with Comments",
        "cson": "CSON", "ini": "INI", "editorconfig": "EditorConfig",
        "llvm ir": "LLVM", "llvm": "LLVM",
        "nimlang": "Nim", "ocaml": "OCaml", "objective caml": "OCaml",
        "rkt": "Racket", "clj": "Clojure", "cljc": "Clojure", "cljs": "Clojure",
        "elisp": "Emacs Lisp", "emacs-lisp": "Emacs Lisp",
        "matlab": "MATLAB", "wolfram": "Mathematica", "wolfram language": "Mathematica",
        "rstats": "R", "stata": "Stata", "apl": "APL", "j language": "J",
        "vhdl": "VHDL", "verilog": "Verilog", "systemverilog": "SystemVerilog",
        "hlsl": "HLSL", "glsl": "GLSL",
        "vb": "Visual Basic .NET", "vb.net": "Visual Basic .NET", "vba": "VBA",
        "k8s manifest": "YAML", "cuda": "Cuda",
        "plain text": "Text", "markdown": "Markdown", "md": "Markdown",
        "fstar": "F*",
    }

def http_get_plain(urls: List[str]) -> str:
    last_err = None
    for url in urls:
        try:
            r = requests.get(url, headers=UA, timeout=30)
            r.raise_for_status()
            txt = r.text
            if "<html" in txt.lower():
                # Not plain; skip
                last_err = RuntimeError(f"Received HTML from {url}")
                continue
            return txt
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"Failed to fetch plain source from all candidates. Last error: {last_err}")

def slice_languages_block(text: str) -> str:
    m = re.search(r"static\s+LANGUAGES\s*:[\s\S]*?=\s*&\s*\[(?P<body>[\s\S]*?)\]\s*;", text)
    if not m:
        raise RuntimeError("Could not locate LANGUAGES array in languages.rs")
    return m.group("body")

def parse_languages_rs(text: str) -> List[str]:
    body = slice_languages_block(text)
    items = re.findall(r'"((?:\\.|[^"\\])*)"', body)
    return [i.encode("utf-8").decode("unicode_escape") for i in items]

def parse_language_info_map(text: str) -> Dict[str, Dict[str, Optional[str]]]:
    out = {}
    pattern = re.compile(
        r'\("(?P<key>[^"]+)",\s*Language\s*\{\s*name:\s*"(?P<name>[^"]+)",\s*'
        r'language_type:\s*LanguageType::(?P<ltype>\w+),\s*'
        r'color:\s*(?P<color>Some\("?#?[0-9A-Fa-f]+"?\)|None),\s*'
        r'group:\s*(?P<group>Some\(".*?"\)|None)\s*\}\s*\)',
        re.S)
    for m in pattern.finditer(text):
        name = m.group("name")
        ltype = m.group("ltype")
        color_raw = m.group("color")
        group_raw = m.group("group")
        color = None
        if color_raw.startswith("Some"):
            color = re.search(r'"(#?[0-9A-Fa-f]+)"', color_raw).group(1)
        group = None
        if group_raw.startswith("Some"):
            group = re.search(r'"(.*?)"', group_raw).group(1)
        out[name] = {"hp_type": ltype, "hp_color": color, "hp_group": group}
    return out

def build_index(hp_list: List[str]) -> Dict[str, str]:
    def variants(name: str):
        base = normalize_key(name)
        yield base
        yield base.replace(" ", "-")
        yield base.replace("-", " ")
        yield base.replace(" ", "")
    idx = {}
    for hp in hp_list:
        for v in variants(hp):
            idx[v] = hp
    return idx

def to_hp_canonical(name: str, idx: Dict[str,str], alias_map: Dict[str,str]) -> Optional[str]:
    key = normalize_key(name)
    if key in alias_map:
        return alias_map[key]
    noise = {"language", "programming", "file", "script"}
    key2 = " ".join([w for w in key.split() if w not in noise])
    return idx.get(key) or idx.get(key2)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_csv", default="data/derived/languages_master.csv", help="Input CSV path")
    ap.add_argument("--out", dest="out_csv", default="data/derived/languages_master_augmented.csv", help="Output augmented CSV path")
    ap.add_argument("--missing", dest="missing_csv", default="data/derived/hyperpolyglot_missing_from_master.csv", help="Output 'missing from master' CSV path")
    ap.add_argument("--langcol", dest="langcol", default=None, help="Language name column in input CSV (optional)")
    args = ap.parse_args()

    df = pd.read_csv(args.in_csv)
    lang_col = args.langcol
    if not lang_col:
        candidates = [c for c in df.columns if normalize_key(c) in {"language", "lang", "name", "language_name", "programming_language"}]
        lang_col = candidates[0] if candidates else df.columns[0]

    # Fetch languages.rs and language-info-map.rs (GitHub raw, then docs.rs fallback)
    languages_rs = http_get_plain([GH_LANGUAGES_RS_URL, DRS_LANGUAGES_RS_URL])
    language_info_rs = http_get_plain([GH_LANGUAGE_INFO_MAP_URL, DRS_LANGUAGE_INFO_MAP_URL])

    hp_list = parse_languages_rs(languages_rs)
    info_map = parse_language_info_map(language_info_rs)

    idx = build_index(hp_list)
    alias_map = builtin_alias_table()

    df["hyperpolyglot_name"] = df[lang_col].map(lambda x: to_hp_canonical(str(x), idx, alias_map))
    df["in_hyperpolyglot"] = df["hyperpolyglot_name"].notna()
    df["hp_type"]  = df["hyperpolyglot_name"].map(lambda n: info_map.get(n, {}).get("hp_type") if n else None)
    df["hp_group"] = df["hyperpolyglot_name"].map(lambda n: info_map.get(n, {}).get("hp_group") if n else None)
    df["hp_color"] = df["hyperpolyglot_name"].map(lambda n: info_map.get(n, {}).get("hp_color") if n else None)

    Path(args.out_csv).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out_csv, index=False)

    hp_set = set(hp_list)
    master_canon = set([n for n in df["hyperpolyglot_name"].dropna().unique()])
    missing = sorted(hp_set - master_canon)
    pd.DataFrame({"hyperpolyglot_only": missing}).to_csv(args.missing_csv, index=False)

    print(f"[done] Augmented rows matched: {df['in_hyperpolyglot'].sum()} / {len(df)}")
    print(f"[done] Wrote: {args.out_csv}")
    print(f"[done] Missing report: {args.missing_csv} (count={len(missing)})")

if __name__ == "__main__":
    main()
