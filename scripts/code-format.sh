#!/bin/bash

poetry run ruff format

# Remove trailing whitespace in all .py files
find . -name "*.py" -exec sed -i 's/[[:space:]]*$//' {} \;

