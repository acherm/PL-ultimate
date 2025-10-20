# -------- Configuration --------
PY := python
VENV := .venv
ACT := $(VENV)/bin/activate
PLDB_DIR ?= ./pldb

# -------- Helpers --------
define runpy
. $(ACT) && $(PY) $(1)
endef

# -------- Targets --------
.PHONY: all venv deps fetch build build-offline qa clean deepclean stats

all: fetch build qa

venv:
	@test -d $(VENV) || python3 -m venv $(VENV)

deps: venv
	. $(ACT) && pip install -U pip
	. $(ACT) && pip install requests pyyaml pandas rapidfuzz beautifulsoup4

# Fetch sources that can change (Linguist, Wikipedia, Esolang)
fetch: deps
	@echo "==> Fetching Linguist/Wikipedia/Esolang"
	$(call runpy, build_master_list_local_pldb.py --pldb-dir $(PLDB_DIR) --fetch-only)

# at the top (config)
INCLUDE_ESOLANG ?= 0

# in build targets, pass --include-esolang when requested
build: deps
	@echo "==> Building languages_master.csv"
	$(call runpy, build_master_list_local_pldb.py --pldb-dir $(PLDB_DIR) $(if $(filter 1,$(INCLUDE_ESOLANG)),--include-esolang,))

build-offline: deps
	@echo "==> Building (offline)"
	$(call runpy, build_master_list_local_pldb.py --pldb-dir $(PLDB_DIR) --offline $(if $(filter 1,$(INCLUDE_ESOLANG)),--include-esolang,))



# Quick QA report (warnings, counts, flags)
qa: deps
	@echo "==> QA report"
	$(call runpy, qa_report.py)

# Quick stats (row counts by source_flag)
stats: deps
	$(call runpy, qa_report.py --stats-only)

clean:
	rm -f data/derived/*.csv

deepclean: clean
	rm -f data/raw/*.json data/raw/*.yml
	rm -rf $(VENV)
