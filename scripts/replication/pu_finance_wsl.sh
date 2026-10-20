#!/usr/bin/env bash
# Rebuild the toolchain of the pilot replication package (iit-Demokritos/pu_finance, Zavitsanos et al., ICAIF 2025) and rerun its
# PU-HRF experiment, so that its shipped results can be compared with a fresh run. Developed and run on Ubuntu 24.04 under WSL2.
#
# The package needs Python 3.8, scikit-learn 1.0.2 and compiled Cython trees; none of that is installed system-wide here.
# Everything goes into ~/pu_repro (uv-managed Python 3.8, a virtualenv with the repository's pinned requirements). The package
# directory you give is copied first and never modified. Needs: gcc, tar, pip (for uv), internet access for the wheels.
#
#   bash scripts/replication/pu_finance_wsl.sh /mnt/d/Works/research-suite/sample/pu_finance [output_dir]
#
# Writes <output_dir>/PUHRF_reproduced.csv, shipped_PUHRF.csv, environment.txt and comparison.txt.
set -euo pipefail
SRC="${1:?path of the pu_finance package (as seen from WSL)}"
OUT="${2:-$SRC/reproduced}"
export PATH="$HOME/.local/bin:$PATH"
WORK="$HOME/pu_repro"
mkdir -p "$WORK" "$OUT"

command -v uv >/dev/null || pip3 install --user --break-system-packages uv
uv python install 3.8
[ -d "$WORK/.venv" ] || uv venv --python 3.8 "$WORK/.venv"
. "$WORK/.venv/bin/activate"
# the exact versions pinned by the package's requirements.txt (plus build helpers)
uv pip install "Cython==3.0.12" "numpy==1.24.4" "pandas==2.0.3" "scikit-learn==1.0.2" "scipy==1.10.1" "tqdm==4.67.1" setuptools wheel >/dev/null

DST="$WORK/pu_finance"
rm -rf "$DST"; mkdir -p "$DST"
(cd "$SRC" && tar --exclude=./build --exclude=./reports --exclude=./reproduced --exclude=./data.zip --exclude=./results.zip \
      --exclude=.git --exclude=__pycache__ --exclude='*.so' -cf - .) | (cd "$DST" && tar -xf -)
cd "$DST"
python setup.py build_ext --inplace >build.log 2>&1 || { tail -30 build.log; exit 1; }
cp results/PUHRF_baseline_meta_noCIK.csv "$OUT/shipped_PUHRF.csv"
rm results/PUHRF_baseline_meta_noCIK.csv
( cd scripts && PYTHONPATH="$DST" PYTHONHASHSEED=0 OMP_NUM_THREADS=1 python run_pu_trees.py >"$OUT/run.log" 2>&1 )
cp results/PUHRF_baseline_meta_noCIK.csv "$OUT/PUHRF_reproduced.csv"

{ echo "date: $(date -u +%FT%TZ)"; echo "os: $(lsb_release -ds)"; echo "gcc: $(gcc --version | head -1)"; echo "python: $(python --version)"; uv pip freeze; } >"$OUT/environment.txt"
python - "$OUT" <<'EOF' | tee "$OUT/comparison.txt"
import sys, pandas as pd
out = sys.argv[1]
a = pd.read_csv(f"{out}/shipped_PUHRF.csv").set_index("split"); b = pd.read_csv(f"{out}/PUHRF_reproduced.csv").set_index("split")
cols = [c for c in a.columns if c != "time"]
print("same splits:", list(a.index) == list(b.index))
print("max abs difference per column (time excluded):"); print((a[cols] - b[cols]).abs().max().to_string())
print("mean average_precision shipped / reproduced:", round(a["average_precision"].mean(), 6), round(b["average_precision"].mean(), 6))
print("mean rec-precision     shipped / reproduced:", round(a["rec-precision"].mean(), 6), round(b["rec-precision"].mean(), 6))
EOF
