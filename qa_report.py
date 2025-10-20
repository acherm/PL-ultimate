#!/usr/bin/env python3
import argparse, pathlib, pandas as pd

DER = pathlib.Path("data/derived")
RAW = pathlib.Path("data/raw")


def load_csv(name):
    p = DER / name
    if not p.exists():
        print(f"[err] Missing {p}")
        return None
    try:
        return pd.read_csv(p)
    except Exception as e:
        print(f"[err] Failed to read {p}: {e}")
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--stats-only", action="store_true", help="Only print counts/coverage"
    )
    args = ap.parse_args()

    lm = load_csv("languages_master.csv")
    al = load_csv("aliases.csv")
    if lm is None or al is None:
        return

    print(f"[ok] languages_master rows: {len(lm)}")
    print(f"[ok] aliases rows: {len(al)}")

    # Coverage by source
    src_counts = {}
    for s in lm["source_flags"].fillna(""):
        for flag in s.split(";"):
            flag = flag.strip()
            if not flag:
                continue
            src_counts[flag] = src_counts.get(flag, 0) + 1
    print("[info] Source coverage:")
    for k in sorted(src_counts):
        print(f"  - {k:9s}: {src_counts[k]}")

    if args.stats_only:
        return

    # Booleans summary
    for col in [
        "in_pldb",
        "in_linguist",
        "in_wikipedia",
        "in_esolang",
        "has_extensions",
        "has_paradigm",
        "has_typing",
        "has_hello_world",
    ]:
        if col in lm.columns:
            print(f"[info] {col}: {lm[col].sum()}")

    # Extensions health
    with_ext = lm[lm["extensions"].fillna("") != ""]
    print(f"[info] Rows with extensions: {len(with_ext)} / {len(lm)}")
    pldb_ext = lm[(lm["in_pldb"]) & (lm["extensions"].fillna("") != "")]
    print(f"[info] PLDB rows with extensions: {len(pldb_ext)}")

    # Raw inputs sizes (sanity)
    for raw in [
        "linguist_languages.yml",
        "wikipedia_lang_titles.json",
        "esolang_language_titles.json",
    ]:
        p = RAW / raw
        size = p.stat().st_size if p.exists() else 0
        print(f"[raw] {raw:30s} size={size} bytes{'  (EMPTY!)' if size == 0 else ''}")

    # Peeks
    for flag in ["pldb", "linguist", "wikipedia", "esolang"]:
        subset = lm[lm["source_flags"].fillna("").str.contains(flag)]
        print(f"[peek] {flag} ({len(subset)} rows):")
        if len(subset) == 0:
            print("  (none)")
        else:
            for _, r in subset.head(5).iterrows():
                print(
                    f"  - {r['canonical_name']} | id={r['lang_id']} | flags={r['source_flags']} | ext={r.get('extensions', '')}"
                )

    # No-extension examples (to inspect)
    unext = lm[
        (lm["extensions"].fillna("") == "")
        & lm["source_flags"].str.contains("pldb|wikipedia", na=False)
    ]
    print("[peek] No-extension examples (pldb|wikipedia):")
    for _, r in unext.head(10).iterrows():
        print(f"  - {r['canonical_name']} | flags={r['source_flags']}")

    # High-signal PLs for refined views (examples)
    if all(
        c in lm.columns
        for c in ["has_extensions", "has_paradigm", "in_linguist", "in_wikipedia"]
    ):
        refined = lm[
            (lm["has_extensions"] | lm["has_paradigm"])
            & (lm["in_linguist"] | lm["in_wikipedia"])
        ]
        print(f"[info] Refined-candidate rows (signal-rich): {len(refined)}")


if __name__ == "__main__":
    main()
