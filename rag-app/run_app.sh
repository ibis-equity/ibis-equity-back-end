#!/bin/bash
set -e

# Dependency note:
# - Use requirements.txt for direct dependencies in local development.
# - Use requirements-lock.txt for reproducible CI/release installs.

echo "initializing app.."
python3 app_init.py

echo "starting streamlit app"
streamlit run app.py bedrock_claude
