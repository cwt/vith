#!/bin/bash
export PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring

OS_NAME=$(uname -s)
case "$OS_NAME" in
  Darwin)  CPU_CORES=$(sysctl -n hw.physicalcpu);;
  Linux)   CPU_CORES=$(lscpu | awk '/^Core\(s\) per socket:/ {c=$4} /^Socket\(s\):/ {s=$2; sum += c * s} END {print sum}');;
  *)       CPU_CORES=$(python3 -c "import multiprocessing; print(multiprocessing.cpu_count())");;
esac

# cleanup
rm -rf */__pycache__ .pytest_cache .coverage

# update existing packages as defined on pyproject.toml
poetry update

# install current project as editable (silence the benign
# BuildBackendWarning about ./vith shadowing the editable finder;
# scoped to this line so pytest warnings stay visible)
PYTHONWARNINGS='ignore:Editable installation' pip install -q -e .[jedi,lzo,dev]

# regenerate poetry.lock based on installed/updated packages
#rm -f poetry.lock && poetry lock --no-cache

# run unit tests
PYTHONPATH=. poetry run pytest tests/ -n $CPU_CORES --cov=vith --cov-report=term-missing --cov-fail-under=85

