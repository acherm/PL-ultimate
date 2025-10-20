#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build an integrated programming-languages list from:
- Local PLDB clone (treats pldb/concepts/* as languages by default)
- GitHub Linguist (languages.yml)
- Wikipedia List of programming languages (A–Z + category fallback)
- Esolang wiki (optional; OFF by default)

Outputs:
- data/derived/languages_master.csv
- data/derived/aliases.csv

Usage:
  python build_master_list_local_pldb.py --pldb-dir ./pldb
Options:
  --offline            Use existing files in data/raw (no network fetch)
  --fetch-only         Just (re)fetch raw inputs to data/raw and exit
  --include-esolang    Include Esolang (default: OFF)
"""

import os, re, json, time, yaml, argparse, pathlib, hashlib, unicodedata
from typing import Dict, List, Set
import requests, pandas as pd
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

# ----------------------------
# Paths
# ----------------------------
ROOT = pathlib.Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
DER = ROOT / "data" / "derived"
RAW.mkdir(parents=True, exist_ok=True)
DER.mkdir(parents=True, exist_ok=True)


# ----------------------------
# Normalization & IDs
# ----------------------------
def norm(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = s.lower().strip()
    s = s.replace("♯", "#").replace("++", " plus plus ")
    s = re.sub(r"[^\w\s#+]", " ", s)  # keep # and +
    s = re.sub(r"\s+", " ", s)
    s = s.replace("#", " sharp ")
    s = re.sub(r"\s+", "-", s).strip("-")
    return s


def make_id(name: str) -> str:
    nid = norm(name or "")
    if nid:
        return nid
    h = hashlib.sha1((name or "").encode("utf-8", "ignore")).hexdigest()[:8]
    return f"id-{h}"


def save_json(path: pathlib.Path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


# ----------------------------
# CLI & headers
# ----------------------------
def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pldb-dir", required=True, help="Path to local PLDB repo clone")
    ap.add_argument(
        "--offline", action="store_true", help="Do not fetch; use data/raw/* as-is"
    )
    ap.add_argument(
        "--fetch-only", action="store_true", help="Fetch raw sources and exit"
    )
    ap.add_argument(
        "--include-esolang", action="store_true", help="Include Esolang (default: OFF)"
    )
    return ap.parse_args()


HEADERS = {"User-Agent": "PL-ultimate/1.0 (+https://example.org)"}


# ----------------------------
# Fetch sources (Linguist, Wikipedia, Esolang*)
# ----------------------------
def fetch_linguist(off=False) -> pathlib.Path:
    out = RAW / "linguist_languages.yml"
    if off and out.exists():
        return out
    url = "https://raw.githubusercontent.com/github-linguist/linguist/master/lib/linguist/languages.yml"
    r = requests.get(url, headers=HEADERS, timeout=60)
    r.raise_for_status()
    out.write_bytes(r.content)
    return out


WIKI_BAD_PAT = re.compile(
    r"(list of|edits made from this ip address|disambiguation|help:|user:|talk:|wikipedia:)",
    re.IGNORECASE,
)


def fetch_wikipedia_titles(off=False) -> pathlib.Path:
    out = RAW / "wikipedia_lang_titles.json"
    if off and out.exists():
        return out

    titles: Set[str] = set()
    try:
        base = "https://en.wikipedia.org/wiki/List_of_programming_languages"

        def scrape(url: str):
            html = requests.get(url, headers=HEADERS, timeout=60).text
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.select(
                "div.div-col li a[title], ul li a[title], table a[title]"
            ):
                t = (a.get("title") or "").strip()
                if not t:
                    continue
                if WIKI_BAD_PAT.search(t):  # noise guard
                    continue
                titles.add(t)

        scrape(base)
        for L in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            scrape(f"{base}:_{L}")
            time.sleep(0.12)
    except Exception:
        pass

    if not titles:
        # Category fallback (broader)
        S = requests.Session()
        URL = "https://en.wikipedia.org/w/api.php"
        cont = None
        while True:
            params = {
                "action": "query",
                "format": "json",
                "list": "categorymembers",
                "cmtitle": "Category:Programming languages",
                "cmlimit": "500",
                "cmtype": "page",
            }
            if cont:
                params["cmcontinue"] = cont
            data = S.get(URL, params=params, headers=HEADERS, timeout=60).json()
            for m in data["query"]["categorymembers"]:
                t = m["title"].strip()
                if not t or WIKI_BAD_PAT.search(t):
                    continue
                titles.add(t)
            cont = data.get("continue", {}).get("cmcontinue")
            if not cont:
                break
            time.sleep(0.12)

    save_json(out, sorted(titles))
    return out


def fetch_esolang_titles(off=False) -> pathlib.Path:
    out = RAW / "esolang_language_titles.json"
    if off and out.exists():
        return out
    S = requests.Session()
    URL = "https://esolangs.org/w/api.php"
    titles, cont = [], None
    while True:
        params = {
            "action": "query",
            "format": "json",
            "list": "categorymembers",
            "cmtitle": "Category:Languages",
            "cmlimit": "500",
        }
        if cont:
            params["cmcontinue"] = cont
        data = S.get(URL, params=params, headers=HEADERS, timeout=60).json()
        titles += [p["title"] for p in data["query"]["categorymembers"]]
        cont = data.get("continue", {}).get("cmcontinue")
        if not cont:
            break
        time.sleep(0.12)
    save_json(out, sorted(set(titles)))
    return out


# ----------------------------
# PLDB parsing (PLDB = truth)
# ----------------------------

# Block-aware parsing (supports key: and indented lists)
KV_HEAD = re.compile(r"^\s*([A-Za-z0-9_][\w\s/-]*?)\s*:\s*(.*?)\s*$")
LIST_ITEM = re.compile(r"^\s*-\s*(.*?)\s*$")

# Keep some obvious non-language dirs/names out (but DO NOT exclude /concepts/)
BAD_PATH_TOKENS = re.compile(
    r"/(authors|author|build|books?|measures?|metrics?|scripts?|readme|data|csv|tsv|json|assets?)/",
    re.IGNORECASE,
)
BAD_NAME_TOKENS = re.compile(
    r"^(authors?|build|books?|measures?|metrics?|readme|csv|tsv|json)\b", re.IGNORECASE
)

LANG_PROPS_HINTS = {
    "paradigm",
    "paradigms",
    "typing",
    "type system",
    "influenced by",
    "influenced",
    "influenced-by",
    "designed by",
    "designed",
    "filename extension",
    "file extension",
    "file extensions",
    "extensions",
    "hello world",
    "hello-world",
    "hello_world",
    "hello",
    "clocextensions",  # from clocExtensions
}


def parse_blocks(text: str) -> Dict[str, List[str]]:
    props: Dict[str, List[str]] = {}
    current_key = None
    for line in text.splitlines():
        m = KV_HEAD.match(line)
        if m:
            current_key = m.group(1).strip().lower()
            head_val = (m.group(2) or "").strip()
            if head_val:
                props.setdefault(current_key, []).append(head_val)
            else:
                props.setdefault(current_key, [])
            continue
        if current_key:
            lm = LIST_ITEM.match(line)
            if lm:
                val = lm.group(1).strip()
                if val:
                    props[current_key].append(val)
                continue
            if line.startswith((" ", "\t")):
                cont = line.strip()
                if cont:
                    props[current_key].append(cont)
                continue
            current_key = None
    return props


def _norm_ext_token(tok: str) -> str:
    if not tok:
        return ""
    tok = tok.strip()
    tok = tok.lstrip("*")
    if not tok.startswith("."):
        tok = "." + tok
    tok = re.sub(r"[^.\w+-]", "", tok.lower())
    return tok


def _collect_pldb_extensions(props: Dict[str, List[str]]) -> List[str]:
    exts = set()
    # clocExtensions variants
    for key in ("clocextensions", "cloc extensions", "cloc-ext", "cloc_ext"):
        for val in props.get(key, []):
            parts = re.split(r"[\s,;/]+", val) if isinstance(val, str) else [val]
            for p in parts:
                e = _norm_ext_token(str(p))
                if len(e) > 1:
                    exts.add(e)
    # filename/file extensions variants
    for key in (
        "filename extension",
        "file extension",
        "file extensions",
        "extensions",
    ):
        for val in props.get(key, []):
            parts = re.split(r"[\s,;/]+", val) if isinstance(val, str) else [val]
            for p in parts:
                e = _norm_ext_token(str(p))
                if len(e) > 1:
                    exts.add(e)
    return sorted(exts)


def parse_pldb_file(text: str, file_path: pathlib.Path) -> dict:
    pstr = file_path.as_posix()

    # 1) quick path filter for obvious non-language utility dirs
    if BAD_PATH_TOKENS.search("/" + pstr + "/"):
        return {}

    props = parse_blocks(text)

    # 2) PLDB = truth: if it's under concepts/, we assume it's a language
    IS_CONCEPT = ("/concepts/" in pstr) or pstr.startswith("pldb/concepts/")
    is_lang = bool(IS_CONCEPT)

    # 3) additional weak evidence: language-ish properties
    has_lang_prop = any(k in props for k in LANG_PROPS_HINTS)
    is_lang = is_lang or has_lang_prop
    if not is_lang:
        return {}

    # 4) name/title or fallback to filename stem (avoid generic/bad stems)
    name = None
    for key in ("name", "title"):
        if props.get(key):
            name = props[key][0].strip()
            break
    if not name:
        name = file_path.stem.strip()
    if not name or BAD_NAME_TOKENS.search(name):
        return {}

    # metadata fields
    paradigms = "; ".join(props.get("paradigm", []) + props.get("paradigms", []))
    typing = "; ".join(props.get("typing", []) + props.get("type system", []))
    designed_by = "; ".join(props.get("designed by", []) + props.get("designed", []))
    influenced_by = "; ".join(
        props.get("influenced by", [])
        + props.get("influenced", [])
        + props.get("influenced-by", [])
    )
    hello_world = bool(
        props.get("hello world")
        or props.get("hello-world")
        or props.get("hello_world")
        or props.get("hello")
    )

    exts = " ".join(_collect_pldb_extensions(props))

    first_appeared = ""
    for key in ("appeared", "first appeared", "first-appeared"):
        if props.get(key):
            first_appeared = props[key][0].strip()
            break

    homepage = ""
    for key in ("homepage", "home page", "url", "urls"):
        if props.get(key):
            homepage = props[key][0].strip()
            break

    return {
        "name": name,
        "aliases": _collect_aliases(props),
        "first_appeared": first_appeared,
        "homepage": homepage,
        "paradigms": paradigms,
        "typing": typing,
        "designed_by": designed_by,
        "influenced_by": influenced_by,
        "hello_world": hello_world,
        "extensions": exts,
    }


def _collect_aliases(props: Dict[str, List[str]]) -> List[str]:
    aliases = []
    for key in (
        "alias",
        "aliases",
        "aka",
        "also known as",
        "short name",
        "short names",
    ):
        for val in props.get(key, []):
            if any(sep in val for sep in [",", "|", ";"]):
                aliases += [x.strip() for x in re.split(r"[|,;/]", val) if x.strip()]
            else:
                aliases.append(val.strip())
    aliases = sorted(set(a for a in aliases if a and not BAD_NAME_TOKENS.search(a)))
    return aliases


def scan_local_pldb(pldb_dir: pathlib.Path) -> list:
    files = 0
    langs = []
    for p in pldb_dir.rglob("*"):
        if p.suffix.lower() not in (".pldb", ".scroll"):
            continue
        files += 1
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        rec = parse_pldb_file(text, p)
        if rec:
            langs.append(rec)
    print(
        f"[info] PLDB files scanned: {files}, languages detected: {len(langs)} (PLDB=truth)"
    )
    return langs


# ----------------------------
# Enrichment (merge extensions with Linguist)
# ----------------------------
def enrich_extensions_from_linguist(df: pd.DataFrame) -> pd.DataFrame:
    df_l = df[df["source_flags"].str.contains("linguist", na=False)].copy()
    key_to_ext = {
        r["linguist_key"]: r.get("extensions", "") for _, r in df_l.iterrows()
    }
    name_to_key = {r["canonical_name"]: r["linguist_key"] for _, r in df_l.iterrows()}
    linguist_names = set(df_l["canonical_name"].astype(str))

    def _merge_ext(existing: str, extra: str) -> str:
        a = set((existing or "").split())
        b = set((extra or "").split())
        merged = " ".join(sorted((a | b) - {""}))
        return merged

    # exact canonical name
    hit_mask = df["canonical_name"].astype(str).isin(linguist_names)
    df.loc[hit_mask, "linguist_key"] = df.loc[hit_mask, "canonical_name"].map(
        name_to_key
    )
    df.loc[hit_mask, "extensions"] = [
        _merge_ext(e, key_to_ext.get(k, ""))
        for e, k in zip(
            df.loc[hit_mask, "extensions"], df.loc[hit_mask, "linguist_key"]
        )
    ]

    # normalized id
    df_l_ids = set(df_l["lang_id"])
    hit_mask2 = df["lang_id"].isin(df_l_ids)
    id_to_key = {r["lang_id"]: r["linguist_key"] for _, r in df_l.iterrows()}
    df.loc[hit_mask2, "linguist_key"] = df.loc[hit_mask2, "lang_id"].map(id_to_key)
    df.loc[hit_mask2, "extensions"] = [
        _merge_ext(e, key_to_ext.get(k, ""))
        for e, k in zip(
            df.loc[hit_mask2, "extensions"], df.loc[hit_mask2, "linguist_key"]
        )
    ]

    # mark flags for enriched rows
    for idx in df.index[df["linguist_key"].notna() & (df["linguist_key"] != "")]:
        flags = set((df.at[idx, "source_flags"] or "").split(";"))
        flags.add("linguist")
        df.at[idx, "source_flags"] = ";".join(sorted(x for x in flags if x))
    return df


# ----------------------------
# Merge & Dedup
# ----------------------------
def main():
    args = parse_args()
    pldb_dir = pathlib.Path(args.pldb_dir)
    if not pldb_dir.exists():
        raise SystemExit(f"PLDB directory not found: {pldb_dir}")

    print(f"[info] PLDB dir: {pldb_dir}")
    print(
        f"[info] offline={args.offline}, fetch_only={args.fetch_only}, include_esolang={args.include_esolang}"
    )

    ling_p = fetch_linguist(off=args.offline)
    wiki_p = fetch_wikipedia_titles(off=args.offline)
    eso_p = fetch_esolang_titles(off=args.offline) if args.include_esolang else None

    if args.fetch_only:
        print("[ok] Fetched raw sources to data/raw/")
        return

    rows, alias_rows = [], []

    # Linguist
    ling = yaml.safe_load(ling_p.read_text(encoding="utf-8"))
    print(f"[info] Linguist entries: {len(ling)}")
    for name, meta in ling.items():
        nid = make_id(name)
        exts = " ".join(meta.get("extensions", []) or [])
        rows.append(
            {
                "lang_id": nid,
                "canonical_name": name,
                "source_flags": "linguist",
                "types": "",
                "extensions": exts,
                "first_appeared": "",
                "homepage": "",
                "paradigms": "",
                "typing": "",
                "designed_by": "",
                "influenced_by": "",
                "hello_world": False,
                "linguist_key": name,
                "evidence_urls": "https://github.com/github-linguist/linguist/blob/main/lib/linguist/languages.yml",
                "notes": "",
            }
        )
        for a in meta.get("aliases") or []:
            alias_rows.append({"alias": a, "lang_id": nid, "source": "linguist"})

    # Wikipedia
    wiki_titles = json.loads(wiki_p.read_text(encoding="utf-8"))
    print(f"[info] Wikipedia titles: {len(wiki_titles)}")
    for t in wiki_titles:
        rows.append(
            {
                "lang_id": make_id(t),
                "canonical_name": t,
                "source_flags": "wikipedia",
                "types": "",
                "extensions": "",
                "first_appeared": "",
                "homepage": "",
                "paradigms": "",
                "typing": "",
                "designed_by": "",
                "influenced_by": "",
                "hello_world": False,
                "linguist_key": "",
                "evidence_urls": "https://en.wikipedia.org/wiki/List_of_programming_languages",
                "notes": "",
            }
        )

    # Esolang (optional)
    if eso_p is not None:
        eso_titles = json.loads(eso_p.read_text(encoding="utf-8"))
        print(f"[info] Esolang titles: {len(eso_titles)}")
        for t in eso_titles:
            rows.append(
                {
                    "lang_id": make_id(t),
                    "canonical_name": t,
                    "source_flags": "esolang",
                    "types": "esolang",
                    "extensions": "",
                    "first_appeared": "",
                    "homepage": "",
                    "paradigms": "",
                    "typing": "",
                    "designed_by": "",
                    "influenced_by": "",
                    "hello_world": False,
                    "linguist_key": "",
                    "evidence_urls": "https://esolangs.org/wiki/Esolang%3ACopyrights",
                    "notes": "",
                }
            )
    else:
        print("[info] Esolang disabled (use --include-esolang to enable).")

    # PLDB (trust concepts/)
    pldb_recs = scan_local_pldb(pldb_dir)
    for r in pldb_recs:
        nid = make_id(r["name"])
        rows.append(
            {
                "lang_id": nid,
                "canonical_name": r["name"],
                "source_flags": "pldb",
                "types": "",
                "extensions": r.get("extensions", ""),
                "first_appeared": r.get("first_appeared", ""),
                "homepage": r.get("homepage", ""),
                "paradigms": r.get("paradigms", ""),
                "typing": r.get("typing", ""),
                "designed_by": r.get("designed_by", ""),
                "influenced_by": r.get("influenced_by", ""),
                "hello_world": bool(r.get("hello_world", False)),
                "linguist_key": "",
                "evidence_urls": "https://github.com/breck7/pldb",
                "notes": "",
            }
        )
        for a in r.get("aliases", []):
            alias_rows.append({"alias": a, "lang_id": nid, "source": "pldb"})

    # DataFrame + enrichment
    df = pd.DataFrame(rows)
    df = enrich_extensions_from_linguist(df)

    # Derived booleans & counts for post-analysis
    df["in_pldb"] = df["source_flags"].str.contains(r"\bpldb\b", na=False)
    df["in_linguist"] = df["source_flags"].str.contains(r"\blinguist\b", na=False)
    df["in_wikipedia"] = df["source_flags"].str.contains(r"\bwikipedia\b", na=False)
    df["in_esolang"] = df["source_flags"].str.contains(r"\besolang\b", na=False)

    df["has_extensions"] = df["extensions"].fillna("").str.len() > 0
    df["has_paradigm"] = df["paradigms"].fillna("").str.len() > 0
    df["has_typing"] = df["typing"].fillna("").str.len() > 0
    df["has_hello_world"] = df["hello_world"].fillna(False).astype(bool)

    df["source_count"] = (
        df["source_flags"]
        .fillna("")
        .apply(lambda s: len([x for x in s.split(";") if x.strip()]))
    )

    # Merge helpers
    def merge_flags(col):
        return (
            ";".join(sorted(set(";".join(col.dropna().astype(str)).split(";")))) or ""
        )

    def merge_first(col):
        vals = [
            x
            for x in col
            if (isinstance(x, str) and x.strip()) or (isinstance(x, bool))
        ]
        # prefer non-empty strings; allow booleans
        for v in vals:
            if isinstance(v, str) and v.strip():
                return v
        for v in vals:
            if isinstance(v, bool):
                return v
        return ""

    def merge_space(col):
        vals = [x for x in col if isinstance(x, str) and x.strip()]
        return " ".join(sorted(set(" ".join(vals).split()))) if vals else ""

    agg = {
        "canonical_name": merge_first,
        "source_flags": merge_flags,
        "types": merge_first,
        "extensions": merge_space,
        "first_appeared": merge_first,
        "homepage": merge_first,
        "paradigms": merge_first,
        "typing": merge_first,
        "designed_by": merge_first,
        "influenced_by": merge_first,
        "hello_world": merge_first,
        "linguist_key": merge_first,
        "evidence_urls": merge_flags,
        "notes": merge_first,
        "in_pldb": lambda c: any(c),
        "in_linguist": lambda c: any(c),
        "in_wikipedia": lambda c: any(c),
        "in_esolang": lambda c: any(c),
        "has_extensions": lambda c: any(c),
        "has_paradigm": lambda c: any(c),
        "has_typing": lambda c: any(c),
        "has_hello_world": lambda c: any(bool(x) for x in c),
        "source_count": "max",
    }

    # Ensure non-empty IDs before grouping
    df = df[df["lang_id"].astype(str).str.len() > 0].copy()
    dfm = df.groupby("lang_id", as_index=False).agg(agg)

    # alias_count metric
    alias_rows += [
        {"alias": r["canonical_name"], "lang_id": r["lang_id"], "source": "self"}
        for _, r in dfm.iterrows()
        if isinstance(r["lang_id"], str) and r["lang_id"]
    ]
    df_alias = pd.DataFrame(alias_rows).dropna().drop_duplicates()
    alias_counts = df_alias.groupby("lang_id").size().rename("alias_count")
    dfm = (
        dfm.merge(alias_counts, on="lang_id", how="left")
        .fillna({"alias_count": 0})
        .copy()
    )
    dfm["alias_count"] = dfm["alias_count"].astype(int)

    # Conservative fuzzy collapse
    ids = [i for i in dfm["lang_id"].astype(str).tolist() if i]
    id_map = {i: i for i in ids}
    for i in range(len(ids)):
        a = ids[i]
        for j in range(i + 1, len(ids)):
            b = ids[j]
            if not a or not b:
                continue
            if a[0] != b[0]:
                continue
            if fuzz.ratio(a, b) >= 94:
                sa = len(
                    (dfm.loc[dfm.lang_id == a, "source_flags"].values or [""])[0].split(
                        ";"
                    )
                )
                sb = len(
                    (dfm.loc[dfm.lang_id == b, "source_flags"].values or [""])[0].split(
                        ";"
                    )
                )
                keep, drop = (a, b) if sa >= sb else (b, a)
                id_map[drop] = keep

    if set(id_map.values()) != set(ids):
        dfm["lang_id"] = dfm["lang_id"].map(id_map)
        dfm = dfm.groupby("lang_id", as_index=False).agg(agg)
        df_alias["lang_id"] = df_alias["lang_id"].map(lambda x: id_map.get(x, x))
        df_alias = df_alias.drop_duplicates()
        alias_counts = df_alias.groupby("lang_id").size().rename("alias_count")
        dfm = (
            dfm.merge(alias_counts, on="lang_id", how="left")
            .fillna({"alias_count": 0})
            .copy()
        )
        dfm["alias_count"] = dfm["alias_count"].astype(int)

    DER.joinpath("languages_master.csv").write_text(
        dfm.to_csv(index=False), encoding="utf-8"
    )
    DER.joinpath("aliases.csv").write_text(
        df_alias.to_csv(index=False), encoding="utf-8"
    )
    print("Wrote:", DER / "languages_master.csv", "and", DER / "aliases.csv")


if __name__ == "__main__":
    main()
