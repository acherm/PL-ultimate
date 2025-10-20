# Programming languages

Trying to compile all programming languages
see `data/derived/languages_master.csv` (based on Wikipedia, linguist, esolang, and pldb)
and `exploratory.ipynb`

so far almost 12K languages and some meta-information like
```
'lang_id', 'canonical_name', 'source_flags', 'types', 'extensions',
       'first_appeared', 'homepage', 'paradigms', 'typing', 'designed_by',
       'influenced_by', 'hello_world', 'linguist_key', 'evidence_urls',
       'notes', 'in_pldb', 'in_linguist', 'in_wikipedia', 'in_esolang',
       'has_extensions', 'has_paradigm', 'has_typing', 'has_hello_world',
       'source_count', 'alias_count'
```


you can do many things with this data like computing all extensions...
see `data/derived/extensions_inventory.csv`
```
python compute_extensions.py
[ok] Wrote data/derived/extensions_inventory.csv with 1389 unique extensions.
Top 20 extensions by coverage:
extension  count_total  count_pldb  count_linguist  count_wikipedia  count_esolang         sample_lang
     .inc           12           8              12                3              2           Assembely
       .m            7           5               7                3              1               Limbo
     .bas            6           4               6                1              1                 B4X
     .cls            6           4               6                1              0                Apex
    .fcgi            6           4               6                3              2                 Lua
     .mod            5           4               5                2              0                AMPL
     .ncl            5           3               5                0              1        Gerber Image
     .pro            5           4               5                1              0                 IDL
     .sql            5           3               5                1              0             PLpgSQL
      .bf            4           4               4                0              3                Beef
      .fs            4           4               4                0              1                  F#
     .gml            4           4               4                1              0 Game Maker Language
      .gs            4           4               4                1              0               Genie
       .l            4           4               4                2              0         Common Lisp
     .sch            4           3               4                0              0               Eagle
       .t            4           4               4                1              1                Perl
       .x            4           2               4                0              1     DirectX 3D File
    .yaml            4           0               4                0              0            MiniYAML
     .yml            4           0               4                0              0            MiniYAML
     .asc            3           2               3                0              0          AGS Script
[info] Rows with extensions in master: 743 / 11857
```

## Reproducing

`git clone https://github.com/breck7/pldb`

then

```
# Esolang OFF (default)
make build && make qa

# Esolang ON
make build INCLUDE_ESOLANG=1 && make qa
```
