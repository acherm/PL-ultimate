#!/usr/bin/env python3
import pathlib, re, pandas as pd

DER = pathlib.Path("data/derived")
INP = DER / "languages.csv"
OUT = DER / "extensions_inventory.csv"

VALID_EXT = re.compile(r"^\.[A-Za-z0-9_+-]+$")


def split_exts(s: str):
    if not isinstance(s, str):
        return []
    toks = [t for t in s.strip().split() if t]
    toks = [t if t.startswith(".") else f".{t}" for t in toks]
    toks = [t.lower() for t in toks if VALID_EXT.match(t)]
    return toks


def main():
    if not INP.exists():
        raise SystemExit(f"Missing {INP}. Run make build first.")

    df = pd.read_csv(INP)

    # explode extensions
    rows = []
    for _, r in df.iterrows():
        exts = split_exts(r.get("extensions", ""))
        if not exts:
            continue
        flags = set((r.get("source_flags", "") or "").split(";"))
        for ext in exts:
            rows.append(
                {
                    "extension": ext,
                    "lang_id": r["lang_id"],
                    "canonical_name": r["canonical_name"],
                    "in_pldb": "pldb" in flags,
                    "in_linguist": "linguist" in flags,
                    "in_wikipedia": "wikipedia" in flags,
                    "in_esolang": "esolang" in flags,
                }
            )

    if not rows:
        print("[warn] No extensions found in languages_master.csv.")
        OUT.write_text(
            "extension,count_total,count_pldb,count_linguist,count_wikipedia,count_esolang,sample_lang\n"
        )
        return

    dx = pd.DataFrame(rows)

    # aggregate counts
    agg = (
        dx.groupby("extension")
        .agg(
            count_total=("extension", "size"),
            count_pldb=("in_pldb", "sum"),
            count_linguist=("in_linguist", "sum"),
            count_wikipedia=("in_wikipedia", "sum"),
            count_esolang=("in_esolang", "sum"),
            sample_lang=("canonical_name", "first"),
        )
        .reset_index()
        .sort_values(["count_total", "extension"], ascending=[False, True])
    )

    # write CSV
    DER.mkdir(parents=True, exist_ok=True)
    agg.to_csv(OUT, index=False, encoding="utf-8")
    print(f"[ok] Wrote {OUT} with {len(agg)} unique extensions.")

    # quick console summary
    print("Top 20 extensions by coverage:")
    print(agg.head(20).to_string(index=False))

    # sanity: how many rows in master have at least one ext?
    with_ext = (df["extensions"].fillna("") != "").sum()
    print(f"[info] Rows with extensions in master: {with_ext} / {len(df)}")


if __name__ == "__main__":
    main()
